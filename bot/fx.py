"""
fx.py - Gold and forex (XAU/USD, EUR/USD, GBP/USD, USD/JPY): day-trading signals and the morning bias.

check() runs from `python -m bot run`, `fx` and `loop` (GitHub Actions runs `loop`, and so can your laptop):
    1. skipped while the forex market is closed (Friday 22:00 to Sunday 21:00 UTC)
    2. 15m candles per pair (fx_data.py); the 1h trend candles are built from them
    3. 15m signals in the 1h trend direction, the 15m trend when 1h is sideways (fx_signals.py), held as WAIT before big news (fx_calendar.py)
    4. new signals -> ONE Claude review for all of them (fx_news.py) -> signals.grade(): sent only when
       trend, news and Claude all agree, at a confidence in FX_SEND_CONFIDENCE, at most
       FX_MAX_SIGNALS_PER_RUN per run (the strongest) and FX_MAX_SIGNALS_PER_DAY per day.
       The others are logged quietly as skipped, so /stats can show whether the filters help.
    5. once a day at FX_DAILY_HOUR_LOCAL: the daily bias for every pair, ONE Claude call
    6. open gold / forex signals are judged (tracker.py, EXPIRED after FX_EXPIRE_HOURS)
"""

from datetime import datetime, timedelta, timezone

from . import config as C
from . import fx_calendar, fx_data, fx_news, fx_signals, leverage, news, signals, state, telegram, tracker


def pair_by_symbol(symbol):
    return next((p for p in C.FX_PAIRS if p["symbol"] == symbol), None)


def live_price(symbol):
    """Latest price of a gold / forex symbol (for /stats)."""
    return fx_data.get_price(pair_by_symbol(symbol))


def _local(now):
    return now.astimezone(timezone(timedelta(hours=C.LOCAL_UTC_OFFSET_HOURS)))


def daily_due(st, now):
    local = _local(now)
    return (C.FX_DAILY_SIGNAL and local.hour >= C.FX_DAILY_HOUR_LOCAL
            and st.get("last_fx_daily_date") != local.date().isoformat())


def sent_today(history, now):
    """Gold / forex signals really sent today (local day)."""
    today = _local(now).date()
    return sum(1 for r in history["signals"]
               if r.get("market") == "fx" and r.get("sent_at") and not r.get("skipped")
               and _local(datetime.fromisoformat(r["sent_at"])).date() == today)


def _note(sig):
    return (f"15m {sig['side']} signal, entry {sig['price']}, stop {sig['stop']}, target {sig['target']}, "
            f"{sig['trend_tf']} trend {sig['trend']}, RSI {sig['rsi']}, ADX {sig['adx']}. Why: {sig['reason']}")


def _headlines():
    try:
        return news.fetch_all_headlines(C.FX_NEWS_FEEDS, 24)
    except Exception as exc:
        print(f"[fx] headlines failed: {exc}")
        return []


def _key(pair):
    return f"{pair['symbol']}:{C.FX_TIMEFRAME}"


def check(dry_run=False, now=None):
    """One gold / forex check. Returns {pair name: latest price} (empty when skipped)."""
    now = now or datetime.now(timezone.utc)
    if not C.FX_ENABLED:
        return {}
    if not fx_data.market_open(now):
        print("[fx] forex market closed (Fri 22:00 - Sun 21:00 UTC) -> skipped")
        return {}

    st = state.load(C.STATE_FILE)
    history = tracker.load(C.HISTORY_FILE)
    events = fx_calendar.refresh(st, now)
    prices, candidates, candles = {}, [], {}

    for pair in C.FX_PAIRS:
        try:
            m15 = fx_data.get_candles(pair, C.FX_TIMEFRAME, now)
            m15i = fx_signals.add_indicators(m15)
            trend, trend_tf = fx_signals.trend(fx_signals.add_indicators(fx_data.resample(m15, C.FX_TREND_TF))), C.FX_TREND_TF
            if trend == "SIDEWAYS" and C.FX_TREND_FALLBACK_TF:  # 1h unclear: follow the 15m trend
                trend, trend_tf = fx_signals.trend(m15i), C.FX_TREND_FALLBACK_TF
        except Exception as exc:
            print(f"[fx] {pair['name']}: {exc}")
            continue
        candles[pair["symbol"]] = m15
        prices[pair["name"]] = float(m15["close"].iloc[-1])
        sig = fx_signals.evaluate(m15i, trend, trend_tf)
        held = fx_calendar.hold_for(pair, events, now)
        if held:
            sig = fx_signals.hold(sig, held)
        print(f"[fx] {pair['name']}: {sig['side']} | price {sig['price']:.{pair['digits']}f} | RSI {sig['rsi']} | "
              f"{trend_tf} trend {trend} | candle {sig['candle_time']} | {sig['reason']}")
        if state.is_new_signal(st, _key(pair), sig["side"], sig["candle_time"], C.FX_TIMEFRAME):
            candidates.append((pair, sig))

    if candidates:
        send_candidates(st, history, candidates, events, now, dry_run)
    if daily_due(st, now):
        send_daily(st, events, now, dry_run)
    if C.TRACK_RESULTS:
        _judge_open(history, candles, now, dry_run)
        tracker.save(C.HISTORY_FILE, history)
    state.save(C.STATE_FILE, st)
    return prices


def _strength(item):
    """Sort key: HIGH before MEDIUM, then the strongest trend (highest ADX)."""
    return item["confidence"] != "HIGH", -(item["sig"].get("adx") or 0)


def send_candidates(st, history, candidates, events, now, dry_run=False):
    """ONE Claude review for every new signal; send only the best that pass every filter."""
    reviews = fx_news.judge([{"pair": p, "note": _note(s)} for p, s in candidates], _headlines(),
                            fx_calendar.upcoming(events, now, hours=4), review=True)
    items = []
    for (pair, sig), review in zip(candidates, reviews):
        confidence, skip = signals.grade(sig, review)
        if confidence and confidence not in C.FX_SEND_CONFIDENCE:
            confidence, skip = None, "confidence"
        items.append({"pair": pair, "sig": sig, "news": review, "confidence": confidence, "skip": skip})

    passed = sorted((it for it in items if it["confidence"]), key=_strength)
    left_today = C.FX_MAX_SIGNALS_PER_DAY - sent_today(history, now)
    for n, it in enumerate(passed):
        if left_today <= 0:
            it["skip"] = "daily limit"
        elif n >= min(C.FX_MAX_SIGNALS_PER_RUN, left_today):
            it["skip"] = "run limit"

    for it in items:
        pair, sig, review = it["pair"], it["sig"], it["news"]
        extra = {"market": "fx", "digits": pair["digits"], "expire_hours": C.FX_EXPIRE_HOURS,
                 "confidence": it["confidence"]}
        if it["skip"]:
            print(f"[fx] {pair['name']} {sig['side']}: not sent ({tracker.SKIP_REASONS[it['skip']]})")
            state.mark_alerted(st, _key(pair), sig["side"], sig["candle_time"])
            if not dry_run and C.TRACK_RESULTS:  # quiet log: judged later, never announced
                tracker.record(history, pair, sig, review.get("call"), C.FX_TIMEFRAME,
                               {**extra, **tracker.skip_fields(it["skip"])})
            continue
        swing = sig["swing_low"] if sig["side"] == "LONG" else sig["swing_high"]
        plans = leverage.plans(sig["side"], sig["price"], sig["atr"], swing, sig["trend"])
        msg_id = telegram.send(telegram.format_fx_signal(pair, sig, review, it["confidence"], plans, now), dry_run)
        if msg_id or dry_run:
            state.mark_alerted(st, _key(pair), sig["side"], sig["candle_time"], msg_id=msg_id, sent=True)
        if msg_id and not dry_run and C.TRACK_RESULTS:  # only real alerts count, not dry runs
            tracker.record(history, pair, sig, review.get("call"), C.FX_TIMEFRAME,
                           {**extra, "sent_at": now.isoformat()})


def _judge_open(history, candles, now, dry_run):
    """Resolve open gold / forex signals (sent and skipped) on fresh 15m candles; post finished results."""
    for rec in history["signals"]:
        if rec.get("market") == "fx" and rec["status"] == "OPEN" and rec["symbol"] in candles:
            tracker.resolve(rec, candles[rec["symbol"]], now)
    for rec in tracker.unannounced(history):
        if rec.get("market") == "fx" and (telegram.send(telegram.format_result(rec), dry_run) or dry_run):
            rec["announced"] = True


def daily_rows(now):
    """Bias of every pair from closed daily candles: the daily trend side, WAIT when sideways."""
    rows = []
    for pair in C.FX_PAIRS:
        try:
            d1 = fx_signals.add_indicators(fx_data.get_candles(pair, "1d", now))
            try:
                price = fx_data.get_price(pair, now)
            except Exception:
                price = float(d1["close"].iloc[-1])
            trend = fx_signals.trend(d1)
            rows.append({"pair": pair, "price": price, "trend": trend, "rsi": round(float(d1["rsi"].iloc[-1]), 1),
                         "side": {"UP": "LONG", "DOWN": "SHORT"}.get(trend, "WAIT"),
                         "levels": fx_signals.key_levels(d1)})
        except Exception as exc:
            print(f"[fx] daily {pair['name']}: {exc}")
            rows.append({"pair": pair, "error": str(exc)})
    return rows


def send_daily(st, events, now, dry_run=False):
    """Morning bias for all pairs, with ONE Claude news call. News against the trend turns the bias to WAIT."""
    rows = daily_rows(now)
    ok = [r for r in rows if not r.get("error")]
    if not ok:
        print("[fx] daily bias: no data for any pair -> will try again next run")
        return
    today = fx_calendar.upcoming(events, now)
    notes = [{"pair": r["pair"], "note": f"daily trend {r['trend']}, price {r['price']}, RSI {r['rsi']}"} for r in ok]
    for r, verdict in zip(ok, fx_news.judge(notes, _headlines(), today)):
        r["news"] = verdict
        against = {"LONG": "-", "SHORT": "+"}.get(r["side"])
        if verdict["sign"] == against:
            r["side"], r["why"] = "WAIT", f"daily trend {r['trend']}, but the news points the other way"
    if telegram.send(telegram.format_fx_daily(rows, today, now), dry_run) or dry_run:
        st["last_fx_daily_date"] = _local(now).date().isoformat()
