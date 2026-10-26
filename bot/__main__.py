"""
__main__.py - The bot's main program.

Commands (run from the project folder):
    python -m bot run            check prices + send alerts, crypto AND gold/forex (use this on your laptop)
    python -m bot loop           laptop mode: `run` every 5 minutes, forever (stop with Ctrl+C);
                                 a failed run is logged and the loop keeps going
    python -m bot fx             gold / forex check only (XAU/USD, EUR/USD, GBP/USD, USD/JPY)
    python -m bot detect         step 1 only: check prices + scan news for events (no Claude, no Telegram)
                                 (also picks world-news candidates for step 2)
    python -m bot notify         step 2 only: news + Claude + Telegram for found signals
    python -m bot watch          laptop mode: check every 10 minutes AND answer /stats at once
    python -m bot test-telegram  send a test message to your channel
    python -m bot test-claude    check that Claude Code answers with your Pro login

Add  --dry-run  to print messages instead of sending them to Telegram.
Secrets are read from the environment, or from a local .env file (never committed).

Why two steps on GitHub Actions?
    "detect" runs every 10 minutes and is very light. Only when it finds a new
    signal does the workflow install Claude Code and run "notify".
    That keeps each run fast and your Pro usage small.
"""

import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone

from . import config as C
from . import (events, fx, indicators, leverage, macro, market_data, news, sentiment, signals, state,
               telegram, tracker)


def load_env_file(path=".env"):
    """Put NAME=value lines of a local .env file into the environment (values already set win).

    ANTHROPIC_API_KEY is never loaded: it would bill the paid API (Claude uses your Pro login)."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = (part.strip() for part in line.split("=", 1))
            name = name.removeprefix("export ").strip()
            if name == "ANTHROPIC_API_KEY":
                print("[env] ignoring ANTHROPIC_API_KEY in .env (it bills the paid API)")
                continue
            os.environ.setdefault(name, value.strip('"').strip("'"))


def _write_github_output(**values):
    """Tell GitHub Actions what happened (used to decide the next steps)."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        for key, value in values.items():
            f.write(f"{key}={str(value).lower()}\n")


def detect():
    """Step 1: check every coin. Saves new signals to pending_signals.json."""
    st = state.load(C.STATE_FILE)
    commands, tg_offset = telegram.get_commands(st["tg_offset"])
    asked = sorted({telegram.parse_lookup(c) for c in commands} - {None})  # coin buttons that were tapped
    lookups = [x for x in (look(name, tf, lev) for name, tf, lev in asked) if x]
    coin_stats = sorted({telegram.parse_coin_stats(c) for c in commands} - {None})
    new_signals, latest, failures = [], [], 0
    candles = {}  # symbol -> candle table, used to judge open signals
    market = {}  # coin name -> signal info (price, ATR, swing points), used for event trade ideas

    for coin in C.COINS:
        try:
            df = market_data.get_candles(coin["symbol"], C.TIMEFRAME, C.CANDLES)
            df = indicators.add_indicators(df, C.EMA_FAST, C.EMA_SLOW, C.RSI_PERIOD, C.ATR_PERIOD)
            sig = signals.evaluate(df)
            if C.HTF_FILTER and sig["side"] != "WAIT":
                htf = _bigger_trend(coin["symbol"])
                sig = {**signals.apply_htf(sig, htf), "htf": htf}
            candles[coin["symbol"]] = df
        except Exception as exc:
            failures += 1
            print(f"[detect] {coin['name']}: {exc}")
            continue

        print(f"[detect] {coin['name']}: {sig['side']} | price {sig['price']:.2f} | "
              f"RSI {sig['rsi']} | trend {sig['trend']} | candle {sig['candle_time']}")
        market[coin["name"]] = sig
        latest.append({"name": coin["name"], "side": sig["side"],
                       "price": sig["price"], "rsi": sig["rsi"]})

        if state.is_new_signal(st, coin["symbol"], sig["side"], sig["candle_time"]):
            new_signals.append({"coin": coin, "signal": sig})

    new_events = []
    if C.EVENT_ALERTS:
        try:
            found = events.group(events.find_events(news.fetch_all_headlines(C.NEWS_FEEDS), st["seen_events"]))
            if C.EVENTS_WATCHED_ONLY:
                found = [e for e in found if e["coin"]]
            new_events = found[:C.MAX_EVENTS_PER_RUN]
            for ev in new_events:
                if ev["coin"] in market:
                    m = market[ev["coin"]]
                    ev["trade"] = events.trade_idea(
                        ev["bias"], m["price"], m["atr"], m["swing_low"], m["swing_high"])
        except Exception as exc:
            print(f"[detect] event scan failed: {exc}")

    macro_items = []
    if C.MACRO_ALERTS and C.USE_CLAUDE:
        try:
            headlines = news.fetch_all_headlines(C.MACRO_FEEDS, C.MACRO_MAX_AGE_HOURS)
            macro_items = macro.candidates(headlines, st["seen_macro"])
        except Exception as exc:
            print(f"[detect] macro scan failed: {exc}")

    results_due = False
    if not C.TRACK_RESULTS:
        commands = []
    if C.TRACK_RESULTS:
        history = tracker.load(C.HISTORY_FILE)
        for rec in history["signals"]:
            if rec["status"] == "OPEN" and rec.get("market") != "fx":  # gold / forex: judged in fx.py
                df = _candles_for(rec, candles)
                if df is not None:
                    tracker.resolve(rec, df)
        if history["signals"]:
            tracker.save(C.HISTORY_FILE, history)
        results_due = bool(tracker.unannounced(history))
        commands = [c for c in commands if c in telegram.STATS_COMMANDS]  # /state = common typo

    summary = state.summary_due(st, C.DAILY_SUMMARY_HOUR_UTC)
    with open(C.PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump({"signals": new_signals, "events": new_events, "macro": macro_items,
                   "market": market, "summary": summary, "latest": latest,
                   "commands": commands, "tg_offset": tg_offset,
                   "lookups": lookups, "coin_stats": coin_stats}, f, indent=2)

    print(f"[detect] new signals: {len(new_signals)} | new events: {len(new_events)} "
          f"| macro candidates: {len(macro_items)} | daily summary due: {summary} "
          f"| results to send: {results_due} | commands: {len(commands)}")
    _write_github_output(
        has_work=(bool(new_signals) or bool(new_events) or bool(macro_items) or summary
                  or results_due or bool(commands) or bool(lookups) or bool(coin_stats)),
        needs_claude=(bool(new_signals) or bool(macro_items) or (bool(new_events) and C.EVENT_CLAUDE_CONFIRM)
                      or any(not x["lev"] for x in lookups)) and C.USE_CLAUDE,
    )

    if failures == len(C.COINS):
        print("[detect] ERROR: could not get prices for any coin")
        sys.exit(1)
    return market


def _bigger_trend(symbol, tf=None):
    """Trend of the bigger candle (config.HTF_FOR). SIDEWAYS if it cannot be read (blocks only with HTF_STRICT)."""
    try:
        df = market_data.get_candles(symbol, C.HTF_FOR[tf or C.TIMEFRAME], C.CANDLES)
        df = indicators.add_indicators(df, C.EMA_FAST, C.EMA_SLOW, C.RSI_PERIOD, C.ATR_PERIOD)
        return signals.trend_of(df)
    except Exception as exc:
        print(f"[detect] {symbol}: bigger-candle trend failed, not filtering: {exc}")
        return "SIDEWAYS"


def look(name, tf, lev=0):
    """Fresh check of one coin on one candle size (a coin button was tapped). None if it fails.

    lev > 0 means a leverage button was tapped: the answer is the plan for that leverage."""
    coin = next(c for c in C.ALL_COINS if c["name"] == name)
    try:
        df = market_data.get_candles(coin["symbol"], tf, C.CANDLES)
        df = indicators.add_indicators(df, C.EMA_FAST, C.EMA_SLOW, C.RSI_PERIOD, C.ATR_PERIOD)
        sig = signals.evaluate(df)
        if C.HTF_FILTER and sig["side"] != "WAIT":
            htf = _bigger_trend(coin["symbol"], tf)
            sig = {**signals.apply_htf(sig, htf), "htf": htf}
        return {"name": name, "tf": tf, "lev": lev, "signal": sig}
    except Exception as exc:
        print(f"[look] {name} {tf}: {exc}")
        return None


def send_lookup(item, dry_run=False):
    sig = item["signal"]
    if item.get("lev"):
        text = telegram.format_leverage(item["name"], item["tf"], item["lev"], sig)
    else:
        text = telegram.format_lookup(item["name"], item["tf"], sig, item.get("news"))
    telegram.send(text, dry_run, telegram.coin_keyboard(item["name"]))


def answer_lookups(lookups, dry_run=False):
    """Answer coin buttons. A candle button also gets a news verdict (one Claude call per coin)."""
    verdicts, headlines = {}, None
    for item in lookups:
        if not item.get("lev"):
            if headlines is None:
                headlines = news.fetch_all_headlines(C.NEWS_FEEDS)
            if item["name"] not in verdicts:
                coin = next(c for c in C.ALL_COINS if c["name"] == item["name"])
                lean = telegram.lean_sides(item["signal"])
                verdicts[item["name"]] = sentiment.analyze(
                    item["name"], lean[0] if len(lean) == 1 else "WAIT",
                    news.filter_for_coin(headlines, coin["keywords"], C.MAX_HEADLINES), item["signal"])
            item["news"] = verdicts[item["name"]]
        send_lookup(item, dry_run)


def _candles_for(rec, candles):
    """Candles for judging an open signal. Signals made on another candle size need their own download."""
    if tracker.rec_tf(rec) == C.TIMEFRAME:
        return candles.get(rec["symbol"])
    try:
        return market_data.get_candles(rec["symbol"], tracker.rec_tf(rec), C.CANDLES)
    except Exception as exc:
        print(f"[detect] {rec['coin']}: could not judge {tracker.rec_tf(rec)} signal: {exc}")
        return None


def notify(dry_run=False):
    """Step 2: for each new signal -> news -> Claude (or backup) -> Telegram."""
    if not os.path.exists(C.PENDING_FILE):
        print("[notify] nothing to do")
        return
    with open(C.PENDING_FILE, encoding="utf-8") as f:
        pending = json.load(f)

    st = state.load(C.STATE_FILE)
    history = tracker.load(C.HISTORY_FILE)

    answer_lookups(pending.get("lookups", []), dry_run)
    for name in pending.get("coin_stats", []):
        send_coin_stats(history, name, {n: m["price"] for n, m in pending.get("market", {}).items()}, dry_run)

    all_headlines = []
    if pending["signals"]:
        all_headlines = news.fetch_all_headlines(C.NEWS_FEEDS)
        print(f"[notify] downloaded {len(all_headlines)} recent headlines")

    notify_signals(pending["signals"], all_headlines, st, history, dry_run)
    notify_macro(pending, st, dry_run)
    notify_events(pending.get("events", []), st, dry_run)

    if pending.get("summary"):
        rows = [(v["name"], v["side"], v["price"], v["rsi"]) for v in pending.get("latest", [])]
        if telegram.send(telegram.format_summary(rows), dry_run) or dry_run:
            st["last_summary_date"] = datetime.now(timezone.utc).date().isoformat()

    if C.TRACK_RESULTS:
        notify_results(pending, history, dry_run)
        st["tg_offset"] = pending.get("tg_offset", st["tg_offset"])
        tracker.save(C.HISTORY_FILE, history)

    state.save(C.STATE_FILE, st)
    os.remove(C.PENDING_FILE)


def notify_signals(items, all_headlines, st, history, dry_run):
    """Send only the best crypto signal(s): trend, news and Claude must all agree (signals.grade).

    The strongest coin (highest ADX) per direction is reviewed; the others with the same direction are
    listed in its alert as "also signaling". At most MAX_SIGNALS_PER_RUN per run and MAX_SIGNALS_PER_DAY
    per day. Everything not sent is marked alerted (no repeats) and logged quietly for /stats."""
    def skip(item, reason, call=None):
        coin, sig = item["coin"], item["signal"]
        print(f"[notify] {coin['name']} {sig['side']}: not sent ({tracker.SKIP_REASONS[reason]})")
        state.mark_alerted(st, coin["symbol"], sig["side"], sig["candle_time"])
        if C.TRACK_RESULTS and not dry_run:
            tracker.record(history, coin, sig, call, extra=tracker.skip_fields(reason))

    ready = []
    for item in items:
        if signals.trend_agrees(item["signal"]):
            ready.append(item)
        else:
            skip(item, "trend")
    room = max(0, min(C.MAX_SIGNALS_PER_RUN, C.MAX_SIGNALS_PER_DAY - tracker.sent_today(history)))
    for n, (leader, others) in enumerate(signals.group_correlated(ready)):
        if n >= room:
            limit = "daily limit" if n < C.MAX_SIGNALS_PER_RUN else "run limit"
            for item in [leader, *others]:
                skip(item, limit)
            continue
        coin, sig = leader["coin"], leader["signal"]
        headlines = news.filter_for_coin(all_headlines, coin["keywords"], C.MAX_HEADLINES)
        print(f"[notify] {coin['name']}: {len(headlines)} headlines -> analyzing")
        verdict = sentiment.analyze(coin["name"], sig["side"], headlines, sig)  # news + chart second opinion
        print(f"[notify] {coin['name']}: news {verdict['verdict']} via {verdict['source']}, "
              f"Claude {verdict.get('call', '-')}")
        confidence, why_not = signals.grade(sig, verdict)
        if confidence and confidence not in C.SEND_CONFIDENCE:
            why_not = "confidence"
        for item in others:
            skip(item, "also signaling", verdict.get("call"))
        if why_not:
            skip(leader, why_not, verdict.get("call"))
            continue

        swing = sig.get("swing_low") if sig["side"] == "LONG" else sig.get("swing_high")
        plans = leverage.plans(sig["side"], sig["price"], sig["atr"], swing, sig["trend"])
        also = [it["coin"]["name"] for it in others]
        sent = telegram.send(telegram.format_signal(coin["name"], sig, verdict, plans, also, confidence),
                             dry_run, telegram.coin_keyboard(coin["name"]))
        if sent or dry_run:
            state.mark_alerted(st, coin["symbol"], sig["side"], sig["candle_time"],
                               msg_id=sent if sent is not True else None, sent=True)
        if sent and C.TRACK_RESULTS:  # only real alerts count, not dry runs
            tracker.record(history, coin, sig, verdict.get("call"), extra={"confidence": confidence})


def notify_events(evs, st, dry_run):
    """Event alerts. Claude first confirms which are real (one call); rejected ones are not sent again."""
    if evs and C.EVENT_CLAUDE_CONFIRM and C.USE_CLAUDE:
        confirmed, rejected = events.confirm(evs)
        if confirmed is None:
            print("[notify] events: Claude unavailable -> will try again next run")
            return
        print(f"[notify] events: {len(confirmed)} confirmed, {len(rejected)} rejected by Claude")
        state.mark_events_seen(st, [i for ev in rejected for i in ev.get("ids", [ev["id"]])])
        evs = confirmed
    sent_ids = []
    for ev in evs:
        markup = telegram.news_keyboard([ev["coin"]]) if ev["coin"] else None
        if telegram.send(telegram.format_event(ev), dry_run, markup) or dry_run:
            sent_ids += ev.get("ids", [ev["id"]])
    state.mark_events_seen(st, sent_ids)


def notify_results(pending, history, dry_run):
    """Send finished-signal results, and answer /stats."""
    for rec in tracker.unannounced(history):
        if telegram.send(telegram.format_result(rec), dry_run) or dry_run:
            rec["announced"] = True
    if pending.get("commands"):
        prices = {name: m["price"] for name, m in pending.get("market", {}).items()}
        send_stats(history, prices, dry_run)


def send_coin_stats(history, name, prices, dry_run=False):
    """Reply to a coin's stats button: results of that coin only, open signals with the live price."""
    mine = {"signals": [r for r in history["signals"] if r["coin"] == name]}
    prices = dict(prices)
    for rec in mine["signals"]:
        if rec["status"] == "OPEN":
            try:
                prices[name] = market_data.get_price(rec["symbol"])
            except Exception as exc:
                print(f"[stats] live price for {name} failed, using last candle: {exc}")
            break
    telegram.send(telegram.format_coin_stats(name, tracker.stats(mine), prices), dry_run,
                  telegram.coin_keyboard(name))


def send_stats(history, prices, dry_run=False):
    """Reply to /stats. Open signals get the live price (the candle close is up to an hour old)."""
    prices = dict(prices)
    for rec in history["signals"]:
        if rec["status"] == "OPEN":
            try:
                prices[rec["coin"]] = (fx.live_price(rec["symbol"]) if rec.get("market") == "fx"
                                       else market_data.get_price(rec["symbol"]))
            except Exception as exc:
                print(f"[stats] live price for {rec['coin']} failed, using last candle: {exc}")
    telegram.send(telegram.format_stats(tracker.stats(history), prices), dry_run)


def _log_failure(where, every_min, exc):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{where}] {stamp} check failed, will retry in {every_min} min: {exc!r}")
    print(traceback.format_exc().rstrip())


def watch(dry_run=False, every_min=None, with_fx=False):
    """Laptop mode: full check every `every_min` minutes, /stats answered within seconds.

    `watch` = crypto every CHECK_EVERY_MIN; `loop` = crypto + gold/forex every LOOP_EVERY_MIN."""
    every_min = every_min or C.CHECK_EVERY_MIN
    where = "loop" if with_fx else "watch"
    prices, next_check = {}, 0.0
    while True:
        if time.time() >= next_check:
            try:
                market = detect()
                notify(dry_run)
                prices = {name: m["price"] for name, m in market.items()}
            except (Exception, SystemExit) as exc:  # a failed check must not stop the watcher
                _log_failure(where, every_min, exc)
            if with_fx:
                try:
                    prices.update(fx.check(dry_run))
                except Exception as exc:  # gold / forex failing must not stop crypto either
                    _log_failure(where, every_min, exc)
            next_check = time.time() + every_min * 60
        st = state.load(C.STATE_FILE)
        commands, offset = telegram.get_commands(st["tg_offset"], wait=25)
        if offset != st["tg_offset"]:
            st["tg_offset"] = offset
            state.save(C.STATE_FILE, st)
        for name in sorted({telegram.parse_coin_stats(c) for c in commands} - {None}):
            send_coin_stats(tracker.load(C.HISTORY_FILE), name, prices, dry_run)
        asked = sorted({telegram.parse_lookup(c) for c in commands} - {None})
        answer_lookups([x for x in (look(name, tf, lev) for name, tf, lev in asked) if x], dry_run)  # coin buttons: at once
        if any(c in telegram.STATS_COMMANDS for c in commands):
            send_stats(tracker.load(C.HISTORY_FILE), prices, dry_run)
        if not (os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")):
            time.sleep(30)  # no Telegram settings: get_commands returns at once, don't spin




def notify_macro(pending, st, dry_run):
    """Ask Claude which world-news headlines are market moving, and alert on those."""
    items = pending.get("macro", [])
    if not items:
        return
    alerts, judged = macro.judge(items)
    if alerts is None:
        print("[notify] macro: Claude unavailable -> will try again next run")
        return
    print(f"[notify] macro: {len(items)} headlines judged, {len(alerts)} market moving")
    alert_ids = {a["id"] for a in alerts}
    done = [i for i in judged if i not in alert_ids]  # judged as not important: don't ask again
    for al in alerts[:C.MACRO_MAX_ALERTS]:
        picked = al.get("coins") or C.MACRO_IDEA_COINS  # Claude names the coins; BTC/ETH/SOL if it did not
        al = {**al, "coins": picked}
        if macro_conflicts(al, st, dry_run):
            done.append(al["id"])
            continue
        ideas = {name: events.trade_idea(al["bias"], m["price"], m["atr"],
                                         m.get("swing_low"), m.get("swing_high"))
                 for name, m in pending.get("market", {}).items()
                 if name in picked}
        if telegram.send(telegram.format_macro(al, ideas), dry_run, telegram.news_keyboard(picked)) or dry_run:
            done.append(al["id"])
    state.mark_macro_seen(st, done)


def macro_conflicts(alert, st, dry_run):
    """True if the alert goes against a signal sent for one of its coins in the last MACRO_CONFLICT_HOURS.

    Then no macro alert is sent; instead each such signal gets a note as a reply, so the conflict
    is read where the trade is."""
    against = "SHORT" if alert["bias"] == "BULLISH" else "LONG"
    hits = []
    for coin in C.ALL_COINS:
        if coin["name"] in alert["coins"]:
            last = state.recent_signal(st, coin["symbol"], C.MACRO_CONFLICT_HOURS)
            if last and last["side"] == against:
                hits.append((coin["name"], last))
    for name, last in hits:
        print(f"[notify] macro: against the recent {name} {last['side']} -> note on that signal instead")
        telegram.send(telegram.format_macro_conflict(alert, name, last["side"]), dry_run,
                      reply_to=last.get("msg_id"))
    return bool(hits)


def test_telegram():
    ok = telegram.send("✅ Test message from your crypto signal bot. Telegram works!")
    print("Telegram test:", "OK, check your channel" if ok else "FAILED (see message above)")


def test_claude():
    answer = sentiment.ask_claude(sentiment.build_question(
        "BTC", "LONG", ["Bitcoin ETF records largest weekly inflow this year"]))
    print("Claude test:", f"OK -> {answer}" if answer else "FAILED (see message above)")


def main():
    load_env_file()
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    command = next((a for a in args if not a.startswith("--")), "run")

    if command == "detect":
        detect()
    elif command == "notify":
        notify(dry_run)
    elif command in ("watch", "loop"):
        try:
            if command == "loop":
                watch(dry_run, C.LOOP_EVERY_MIN, with_fx=True)
            else:
                watch(dry_run)
        except KeyboardInterrupt:
            print(f"[{command}] stopped (Ctrl+C)")
    elif command == "run":
        try:
            detect()
            notify(dry_run)
        finally:
            fx.check(dry_run)  # gold / forex run even when crypto failed
    elif command == "fx":
        fx.check(dry_run)
    elif command == "test-telegram":
        test_telegram()
    elif command == "test-claude":
        test_claude()
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
