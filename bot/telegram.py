"""
telegram.py - Builds the alert text and sends it to your Telegram channel.

Needs two settings (stored as secrets, never inside the code):
    TELEGRAM_BOT_TOKEN  - from @BotFather
    TELEGRAM_CHAT_ID    - your channel, e.g. @my_signal_channel  or  -100123456789
"""

import html
import os
from datetime import datetime, timedelta, timezone

import requests

from . import config as C
from . import leverage, signals, tracker

ICONS = {"LONG": "🟢", "SHORT": "🔴", "WAIT": "⚪"}
NEWS_ICONS = {"BULLISH": "😊", "BEARISH": "😟", "NEUTRAL": "😐"}


def fmt_price(value):
    if value is None:
        return "-"
    if value >= 100:
        return f"${value:,.2f}"
    return f"${value:,.4f}" if value >= 1 else f"${value:,.6f}"  # cheap coins (DOGE, ADA) need more decimals


def fmt_level(value, digits=None):
    """Price of a crypto coin (digits None, see fmt_price) or of gold / forex (fixed decimals, no $)."""
    if digits is None or value is None:
        return fmt_price(value)
    return f"{value:,.{digits}f}"


TF_LABELS = {"1d": "daily"}


def tf_label(tf):
    return TF_LABELS.get(tf, tf)


def news_agreement(side, verdict):
    if verdict == "NEUTRAL":
        return "➖ neutral"
    agrees = (side == "LONG" and verdict == "BULLISH") or (side == "SHORT" and verdict == "BEARISH")
    return "✅ agrees" if agrees else "⚠️ DISAGREES, be careful"


def entry_line(t):
    """Entry price plus the zone to wait for. t needs price, entry_low and entry_high."""
    if t.get("entry_low") is None:
        return f"📍 Entry: {fmt_price(t['price'])}"
    return (f"📍 Entry: {fmt_price(t['price'])} "
            f"(zone {fmt_price(t['entry_low'])} to {fmt_price(t['entry_high'])})")


def pct_from(price, level):
    return abs(level - price) / price * 100


def short_text(text, limit=110):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


CALL_ICONS = {"CONFIRM": "✅", "CAUTION": "⚠️", "AVOID": "⛔"}


def claude_line(news):
    """Claude's second opinion on the whole signal (empty list if Claude did not give one)."""
    if not news.get("call"):
        return []
    return [f"🤖 <b>Claude: {CALL_ICONS[news['call']]} {news['call']}</b>",
            f"<i>{html.escape(short_text(news.get('why', ''), 200))}</i>"]


def analysed_line(now=None):
    """When the bot looked at the market, in local time (C.LOCAL_UTC_OFFSET_HOURS)."""
    offset = C.LOCAL_UTC_OFFSET_HOURS
    local = (now or datetime.now(timezone.utc)).astimezone(timezone(timedelta(hours=offset)))
    return f"🕒 Analysed {local:%d %b %Y %H:%M} (UTC{offset:+g})"


CONFIDENCE_ICONS = {"HIGH": "💎", "MEDIUM": "🔸"}


def why_line(news, fallback):
    """One short reason: Claude's view when it gave one, else the news reason."""
    return f"💬 <i>{html.escape(short_text(news.get('why') or fallback, 120))}</i>"


def verdict_line(news, label, agree):
    """'🤖 Claude: ✅ CONFIRM · 📰 News BULLISH ✅ agrees' in one line."""
    claude = f"🤖 Claude: {CALL_ICONS[news['call']]} {news['call']} · " if news.get("call") else ""
    return f"{claude}📰 News {label}" + ("" if agree.startswith("➖") else f" {agree}")


def format_signal(coin_name, sig, news, plans=None, also=None, confidence=None):
    e = html.escape
    side, price = sig["side"], sig["price"]
    better = sig.get("entry_low") if side == "LONG" else sig.get("entry_high")
    conf = f"{CONFIDENCE_ICONS[confidence]} Confidence <b>{confidence}</b> · " if confidence else ""
    lines = [
        f"{ICONS[side]} <b>{side} SIGNAL: {e(coin_name)}</b> · {C.TIMEFRAME}",
        f"{conf}Trend {sig['trend']} · RSI {sig['rsi']:.0f}",
        "",
        f"📍 Entry   <b>{fmt_price(price)}</b>" + (f" (better {fmt_price(better)})" if better else ""),
        f"🛑 Stop    <b>{fmt_price(sig['stop'])}</b>  -{pct_from(price, sig['stop']):.1f}%",
        f"🎯 Target  <b>{fmt_price(sig['target'])}</b>  +{pct_from(price, sig['target']):.1f}%",
        "",
        verdict_line(news, news["verdict"], news_agreement(side, news["verdict"])),
        why_line(news, news["reason"]),
        *([f"🔗 Also: {e(', '.join(also))}"] if also else []),
    ]
    if plans:
        lines += ["", format_plans(plans)]
    lines += ["", f"⏳ {C.EXPIRE_HOURS_BY_TF.get(C.TIMEFRAME, C.RESULT_EXPIRE_HOURS)}h · {analysed_line()}",
              "<i>Not financial advice.</i>"]
    return "\n".join(lines)


def format_macro_conflict(alert, coin_name, side):
    """Note added (as a reply) to a recent signal when world news points the other way."""
    e = html.escape
    move = "📈 bullish" if alert["bias"] == "BULLISH" else "📉 bearish"
    return "\n".join([
        f"⚠️ <b>Macro news against this {side}: {e(coin_name)}</b>",
        f"📰 {e(alert['headline'])}",
        f"Claude: {move} for crypto. {e(alert['reason'])}",
        "",
        "<i>No separate macro alert was sent. Check the chart before keeping the trade.</i>",
    ])


def lean_sides(sig):
    """Sides to show for a coin: the signal, else the side the trend leans to (both when sideways)."""
    if sig["side"] != "WAIT":
        return [sig["side"]]
    return {"UP": ["LONG"], "DOWN": ["SHORT"]}.get(sig["trend"], ["LONG", "SHORT"])


def format_leverage(coin_name, tf, lev, sig):
    """Reply to a leverage button: the plan for this coin at exactly this leverage."""
    e = html.escape
    lines = [f"⚡ <b>{e(coin_name)} · {lev}x</b> · {tf} candles",
             f"💰 <b>{fmt_price(sig['price'])}</b> · Trend {sig['trend']} · RSI {sig['rsi']:.0f}",
             analysed_line(), ""]
    if sig["side"] == "WAIT":
        lines += [f"⚪ No setup now: {e(short_text(sig['reason']))}", "Plan below is only if you trade it anyway:", ""]
    for side in lean_sides(sig):
        swing = sig.get("swing_low") if side == "LONG" else sig.get("swing_high")
        p = leverage.plan(side, sig["price"], sig["atr"], swing, sig["trend"], lev, lev)
        if p["skip"]:
            lines.append(f"{ICONS[side]} <b>{side}</b>: skipped, {e(p['skip'])}")
            continue
        lines += [f"{ICONS[side]} <b>{side}</b> ({p['method']})",
                  f"📍 Entry  <b>{fmt_price(p['price'])}</b>",
                  f"🛑 Stop    <b>{fmt_price(p['stop'])}</b>  (-{p['stop_pct']:.2f}%)",
                  f"🎯 Target <b>{fmt_price(p['target'])}</b>  (+{p['target_pct']:.2f}%)",
                  f"💥 Liquidation about {fmt_price(p['liquidation_low'])}",
                  f"Lose <b>{p['loss_low']:.0f}%</b> / gain <b>{p['gain_low']:.0f}%</b> of margin"]
        lines += [f"⚠️ {e(w)}" for w in p["warnings"]]
    lines += ["", f"🔥 <i>{lev}x can wipe out your margin in minutes. Fees ignored, liquidation approximate. "
                  "Not financial advice.</i>"]
    return "\n".join(lines)


def format_lookup(coin_name, tf, sig, news=None):
    """Reply to a coin button: a fresh look at one coin on one candle size (not a recorded signal)."""
    adx = f" · ADX {sig['adx']:.0f}" if sig.get("adx") is not None else ""
    lines = [f"🔎 <b>{html.escape(coin_name)} · {tf}</b> (just checked)",
             f"💰 <b>{fmt_price(sig['price'])}</b> · Trend {sig['trend']} · RSI {sig['rsi']:.0f}{adx}",
             analysed_line(), ""]
    if sig["side"] == "WAIT":
        lines.append(f"⚪ <b>WAIT</b>: {html.escape(short_text(sig['reason']))}")
        lines += ["", "📐 <b>Levels if you trade it anyway</b> (no setup, so weaker than a signal):"]
        for side in lean_sides(sig):
            lv = signals.trade_levels(side, sig["price"], sig["atr"],
                                      sig.get("swing_low") if side == "LONG" else sig.get("swing_high"))
            lines.append(f"{ICONS[side]} <b>{side}</b>  📍 {fmt_price(sig['price'])}  "
                         f"🛑 {fmt_price(lv['stop'])}  🎯 {fmt_price(lv['target'])}")
    else:
        price = sig["price"]
        lines += [f"{ICONS[sig['side']]} <b>{sig['side']}</b>: {html.escape(short_text(sig['reason']))}",
                  f"📍 Entry  <b>{fmt_price(price)}</b>",
                  f"🛑 Stop    <b>{fmt_price(sig['stop'])}</b>  (-{pct_from(price, sig['stop']):.1f}%)",
                  f"🎯 Target <b>{fmt_price(sig['target'])}</b>  (+{pct_from(price, sig['target']):.1f}%)"]
    if news:
        lean = lean_sides(sig)
        agree = news_agreement(lean[0], news["verdict"]) if len(lean) == 1 else "➖ no clear side"
        lines += ["", f"{NEWS_ICONS.get(news['verdict'], '')} News {news['verdict']} ({news['source']}) · {agree}",
                  f"<i>{html.escape(short_text(news['reason']))}</i>", *claude_line(news)]
    lines += ["", "ℹ️ <i>A look only: not recorded in /stats. Not financial advice.</i>"]
    return "\n".join(lines)


def format_event(ev):
    e = html.escape
    up = ev["bias"] == "BULLISH"
    coin = ev["coin"] or "market"
    lines = [f"{'🚀' if up else '💣'} <b>EVENT: {e(ev['event'])}</b> · {e(coin)} · {'📈 bullish' if up else '📉 bearish'}"]
    heads = ev.get("headlines") or [ev["headline"]]
    lines += [f"📰 {e(short_text(h, 120))}" for h in heads[:3]]
    if len(heads) > 3:
        lines.append(f"…+{len(heads) - 3} more")
    if ev.get("confirm"):
        lines.append(f"🤖 <i>{e(short_text(ev['confirm'], 120))}</i>")
    t = ev.get("trade")
    if t:
        lines += ["", f"{ICONS[t['side']]} <b>Idea: {t['side']}</b> at {fmt_price(t['price'])}",
                  f"🛑 Stop loss {fmt_price(t['stop'])} · 🎯 Take profit {fmt_price(t['target'])}"]
    else:
        lines.append("ℹ️ No levels: not on the watch list." + (" Tap a button 👇" if ev["coin"] else ""))
    lines += ["", "<i>News may be priced in, check the chart. Not financial advice.</i>"]
    return "\n".join(lines)


def format_macro(alert, ideas):
    """alert: from macro.judge(). ideas: {coin name: trade idea dict} for the watched coins."""
    e = html.escape
    up = alert["bias"] == "BULLISH"
    lines = [f"🌍 <b>MACRO: {'📈 bullish' if up else '📉 bearish'} for crypto</b>",
             f"📰 {e(short_text(alert['headline'], 120))}",
             f"💬 <i>{e(short_text(alert['reason'], 120))}</i>"]
    if ideas:
        lines.append("")
        for name, t in ideas.items():
            lines.append(f"{ICONS[t['side']]} <b>{e(name)} {t['side']}</b> {fmt_price(t['price'])} "
                         f"· 🛑 {fmt_price(t['stop'])} · 🎯 {fmt_price(t['target'])}")
    lines += ["", "<i>Claude's guess, check the chart. Not financial advice.</i>"]
    return "\n".join(lines)


SIGN_TEXT = {"+": "📈 price UP", "-": "📉 price DOWN", "0": "😐 no clear news"}


def fx_news_agreement(side, sign):
    """Does the news direction (+ up / - down) agree with a gold / forex LONG or SHORT?"""
    if sign == "0" or side not in ("LONG", "SHORT"):
        return "➖ neutral"
    return "✅ agrees" if (sign == "+") == (side == "LONG") else "⚠️ DISAGREES"


def format_fx_signal(pair, sig, news, confidence, plans=None, now=None):
    """A gold / forex day trade on 15m candles. news = Claude's review from fx_news.judge(review=True)."""
    e, d = html.escape, pair["digits"]
    side, price = sig["side"], sig["price"]
    lines = [
        f"{ICONS[side]} <b>{side} SIGNAL: {e(pair['name'])}</b> · {C.FX_TIMEFRAME}",
        f"{CONFIDENCE_ICONS[confidence]} Confidence <b>{confidence}</b> · {sig['trend_tf']} trend {sig['trend']} "
        f"· RSI {sig['rsi']:.0f}",
        "",
        f"📍 Entry   <b>{fmt_level(price, d)}</b>",
        f"🛑 Stop    <b>{fmt_level(sig['stop'], d)}</b>  -{pct_from(price, sig['stop']):.2f}%",
        f"🎯 Target  <b>{fmt_level(sig['target'], d)}</b>  +{pct_from(price, sig['target']):.2f}%",
        "",
        verdict_line(news, SIGN_TEXT[news["sign"]], fx_news_agreement(side, news["sign"])),
        why_line(news, sig["reason"]),
    ]
    if plans:
        lines += ["", format_plans(plans, d)]
    lines += ["", f"⏳ expires after {C.FX_EXPIRE_HOURS}h · {analysed_line(now)}", "<i>Not financial advice.</i>"]
    return "\n".join(lines)


def local_time(iso):
    """'Tue 19:30' of a UTC ISO time in your time zone (config.LOCAL_UTC_OFFSET_HOURS)."""
    tz = timezone(timedelta(hours=C.LOCAL_UTC_OFFSET_HOURS))
    return datetime.fromisoformat(iso).astimezone(tz).strftime("%a %H:%M")


def format_fx_daily(rows, events, now=None):
    """Morning bias. rows: from fx.daily_rows() with news added (or an error). events: big news, next 24h."""
    e = html.escape
    lines = ["☀️ <b>Daily gold &amp; forex bias</b>", analysed_line(now)]
    for r in rows:
        pair = r["pair"]
        d = pair["digits"]
        lines.append("")
        if r.get("error"):
            lines.append(f"❔ <b>{e(pair['name'])}</b>: no data")
            continue
        lv, news = r["levels"], r["news"]
        lines.append(f"{ICONS[r['side']]} <b>{e(pair['name'])} {r['side']}</b> {fmt_level(r['price'], d)} "
                     f"· RSI {r['rsi']:.0f} · {SIGN_TEXT[news['sign']]}")
        if r.get("why"):
            lines.append(f"<i>{e(r['why'])}</i>")
        lines.append(f"🔑 Pivot {fmt_level(lv['pivot'], d)} · R1 {fmt_level(lv['r1'], d)} · S1 {fmt_level(lv['s1'], d)}")
    lines += ["", "📅 <b>Big news, 24h</b>"]
    lines += [f"⏰ {local_time(ev['time'])} {e(ev['currency'])} {e(ev['title'])}" for ev in events] or ["None ✅"]
    lines += ["", "<i>Bias only, 15m signals come separately. Not financial advice.</i>"]
    return "\n".join(lines)


def format_plans(plans, digits=None):
    """Leverage plans (from leverage.plans()) as a short block inside the signal message.

    digits = decimals for gold / forex prices (None = crypto price format)."""
    lines = ["⚡ <b>Leverage plans</b> (lose / gain of margin)"]
    for p in plans:
        if p["skip"]:
            lines.append(f"<b>{p['label']}</b> skipped")
            continue
        tiny = " ⚠️" if any("noise" in w for w in p["warnings"]) else ""
        lines.append(f"<b>{p['label']}</b> 🛑 {fmt_level(p['stop'], digits)} 🎯 {fmt_level(p['target'], digits)} "
                     f"· -{p['loss_high']:.0f}%/+{p['gain_high']:.0f}%{tiny}")
    return "\n".join(lines)


def format_summary(rows):
    """rows: list of (coin_name, side, price, rsi)"""
    lines = ["📋 <b>Daily summary</b> · bot running ✅", ""]
    for name, side, price, rsi in rows:
        lines.append(f"{ICONS.get(side, '❔')} <b>{html.escape(name)}</b> {fmt_price(price)} · RSI {rsi}"
                     + ("" if side == "WAIT" else f" · <b>{side}</b>"))
    if rows and all(side == "WAIT" for _, side, _, _ in rows):
        lines += ["", "<i>No setups right now, signals come when one appears.</i>"]
    return "\n".join(lines)


def format_result(rec):
    """Message for a signal that just finished (WIN / LOSS / EXPIRED)."""
    head = {"WIN": "✅ WIN", "LOSS": "❌ LOSS", "EXPIRED": "⌛ EXPIRED"}[rec["status"]]
    why = {"WIN": "hit take profit first", "LOSS": "hit stop loss first",
           "EXPIRED": f"hit neither level in {tracker.expire_hours(rec):g}h"}[rec["status"]]

    def fmt(value):
        return fmt_level(value, rec.get("digits"))

    lines = [f"{head}: <b>{html.escape(rec['coin'])} {rec['side']}</b> · {tf_label(tracker.rec_tf(rec))} {why}",
             f"📍 Entry {fmt(rec['entry'])} | 🛑 {fmt(rec['stop'])} | 🎯 {fmt(rec['target'])}"]
    if rec["result_r"] is not None:
        lines.append(f"Result: <b>{rec['result_r']:+.1f}R</b> after about {tracker.hours_taken(rec):.0f}h")
    moves = [f"{label} {rec[key]:+.2f}%" for label, key in (("candle 1", "move_1h"), ("candle 2", "move_2h"))
             if rec.get(key) is not None]
    if moves:
        lines.append("Price after the signal (+ = your way): " + ", ".join(moves))
    return "\n".join(lines)


def _rate_line(label, b):
    done = b["wins"] + b["losses"]
    if not done:
        return f"{label}: no finished signals yet"
    return (f"{label}: {b['wins']}✅ {b['losses']}❌ = <b>{b['win_rate']:.0f}%</b> win rate | "
            f"avg {b['avg_r']:+.2f}R | ⏱ {b['avg_hours']:.1f}h per trade")


STATS_COMMANDS = ("stats", "state", "stat")  # "/state" is a common typo, so it works too


def format_stats(st, prices):
    """Reply to /stats. st comes from tracker.stats(), prices = {coin name: latest price}."""
    a = st["all"]
    if not (a["wins"] or a["losses"] or a["expired"] or st["open"]):
        return ("📊 <b>Bot results</b>\n\nNo signals yet. Wait until a new signal comes first, "
                "then type /stats again.")
    lines = ["📊 <b>Bot results</b>", "", _rate_line("All time", a), _rate_line("Last 24h", st["last_24h"]),
             f"⌛ Expired: {a['expired']} | ⏳ Open: {len(st['open'])}"]
    markets = st.get("by_market", {})
    if markets:  # only once gold / forex signals exist
        lines += ["", "<b>Crypto vs Gold/FX</b>", _rate_line("🪙 Crypto", markets["crypto"]),
                  _rate_line("🥇 Gold/FX", markets["fx"])]
    finished = [(n, b) for n, b in sorted(st["by_coin"].items()) if b["wins"] + b["losses"]]
    if finished:
        lines += ["", "<b>By coin</b>"]
        lines += [f"{html.escape(n)}: {b['wins']}✅ {b['losses']}❌ ({b['win_rate']:.0f}%)" for n, b in finished]
    calls = [(c, b) for c, b in st.get("by_call", {}).items() if b["wins"] + b["losses"]]
    if calls:
        lines += ["", "<b>Claude's call</b> (does it help?)"]
        lines += [f"{CALL_ICONS[c]} {c}: {b['wins']}✅ {b['losses']}❌ ({b['win_rate']:.0f}%)" for c, b in calls]
    lines += filter_lines(st)
    if st["open"]:
        lines += ["", "<b>Open now</b>"]
        for r in st["open"]:
            now = prices.get(r["coin"])
            pnl = ""
            if now:
                pct = (now - r["entry"]) / r["entry"] * 100 * (1 if r["side"] == "LONG" else -1)
                pnl = f" | now {fmt_level(now, r.get('digits'))} ({pct:+.2f}%)"
            lines.append(f"{ICONS[r['side']]} {html.escape(r['coin'])} {r['side']} · {tf_label(tracker.rec_tf(r))} "
                         f"entry {fmt_level(r['entry'], r.get('digits'))}{pnl}")
    done = a["wins"] + a["losses"]
    if done < C.MIN_SAMPLE:
        lines += ["", f"⚠️ <i>Only {done} finished signals. Wait for about {C.MIN_SAMPLE} before "
                      "trusting the win rate. At 1.5:1 reward/risk you need about 40% wins "
                      "to break even (check avg R).</i>"]
    return "\n".join(lines)


def filter_lines(st):
    """/stats block: how the signals the filters held back would have done (do the filters help?)."""
    sk = st.get("skipped")
    if not sk or not (sk["wins"] + sk["losses"]):
        return []
    lines = ["", "<b>Filters</b> (signals held back, not sent)", _rate_line("Skipped", sk)]
    for why, b in st.get("by_skip", {}).items():
        if b["wins"] + b["losses"]:
            lines.append(f"· {html.escape(tracker.SKIP_REASONS[why])}: {b['wins']}✅ {b['losses']}❌ "
                         f"({b['win_rate']:.0f}%)")
    lines.append("<i>Filters help when skipped signals win less often than the sent ones.</i>")
    return lines


def _answer_button(token, query_id):
    """Stop the spinner on the tapped button."""
    try:
        requests.post(f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                      json={"callback_query_id": query_id, "text": "Got it ✅"}, timeout=10)
    except Exception as exc:
        print(f"[telegram] answerCallbackQuery failed: {exc}")


def get_commands(offset, wait=0):
    """Read new Telegram messages and button taps. Returns (list of command words, next offset).

    Only messages from TELEGRAM_CHAT_ID count, so strangers cannot use the bot.
    Without a token it returns ([], offset) and does nothing.
    wait > 0 holds the connection open up to that many seconds, so a message gets an answer at once.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return [], offset
    try:
        resp = requests.get(f"https://api.telegram.org/bot{token}/getUpdates",
                            params={"offset": offset, "timeout": wait}, timeout=wait + 20)
        if not resp.ok:
            print(f"[telegram] getUpdates failed: {resp.status_code} {resp.text[:200]}")
            return [], offset
        updates = resp.json().get("result", [])
    except Exception as exc:
        print(f"[telegram] getUpdates failed: {exc}")
        return [], offset

    commands = []
    for u in updates:
        offset = max(offset or 0, u["update_id"] + 1)
        query = u.get("callback_query")
        if query:  # a button was tapped
            msg, text = query.get("message") or {}, "/" + str(query.get("data", "")).lower()
            _answer_button(token, query["id"])
        else:
            msg = u.get("message") or u.get("channel_post") or {}
            text = (msg.get("text") or "").strip().lower()
        if str(msg.get("chat", {}).get("id", "")) == chat_id and text.startswith("/"):
            commands.append(text.split()[0].split("@")[0].lstrip("/"))
    return commands, offset


def coin_keyboard(coin_name):
    """Buttons for one coin: a candle size = fresh look at that coin, a leverage = plan at that leverage."""
    key = coin_name.lower()
    rows = [[{"text": f"{coin_name} {tf}", "callback_data": f"{key}:{tf}"} for tf in C.TIMEFRAMES]]
    rows.append([{"text": f"📊 {coin_name} stats", "callback_data": f"{key}:stats"}])
    levs = [{"text": f"{x}x", "callback_data": f"{key}:x{x}"} for x in C.LEVERAGE_BUTTONS]
    rows += [levs[i:i + 5] for i in range(0, len(levs), 5)]
    return {"inline_keyboard": rows}


def news_keyboard(names):
    """Buttons under a news alert: one 🔎 per suggested coin (any coin). One coin also gets the leverage buttons."""
    if len(names) == 1:
        return coin_keyboard(names[0])
    return {"inline_keyboard": [[{"text": f"🔎 {n}", "callback_data": f"{n.lower()}:{C.TIMEFRAME}"} for n in names]]}


def parse_coin_stats(command):
    """'btc:stats' -> 'BTC' for a known coin, else None."""
    name, _, rest = command.partition(":")
    name = name.upper()
    return name if rest == "stats" and any(c["name"] == name for c in C.ALL_COINS) else None


def format_coin_stats(name, st, prices):
    """Reply to a coin's stats button. st comes from tracker.stats() on that coin's signals only."""
    a = st["all"]
    if not (a["wins"] or a["losses"] or a["expired"] or st["open"]):
        return f"📊 <b>{html.escape(name)} results</b>\n\nNo {html.escape(name)} signals yet."
    lines = [f"📊 <b>{html.escape(name)} results</b>", "", _rate_line("All time", a), _rate_line("Last 24h", st["last_24h"]),
             f"⌛ Expired: {a['expired']} | ⏳ Open: {len(st['open'])}"]
    calls = [(c, b) for c, b in st.get("by_call", {}).items() if b["wins"] + b["losses"]]
    if calls:
        lines += ["", "<b>Claude's call</b>"]
        lines += [f"{CALL_ICONS[c]} {c}: {b['wins']}✅ {b['losses']}❌ ({b['win_rate']:.0f}%)" for c, b in calls]
    if st["open"]:
        lines += ["", "<b>Open now</b>"]
        for r in st["open"]:
            now = prices.get(r["coin"])
            pnl = ""
            if now:
                pct = (now - r["entry"]) / r["entry"] * 100 * (1 if r["side"] == "LONG" else -1)
                pnl = f" | now {fmt_price(now)} ({pct:+.2f}%)"
            lines.append(f"{ICONS[r['side']]} {r['side']} entry {fmt_price(r['entry'])}{pnl}")
    done = a["wins"] + a["losses"]
    if done < C.MIN_SAMPLE:
        lines += ["", f"⚠️ <i>Only {done} finished {html.escape(name)} signals, too few to trust the win rate.</i>"]
    return "\n".join(lines)


def parse_lookup(command):
    """'btc:15m' -> ('BTC', '15m', 0); 'btc:x20' -> ('BTC', default candle size, 20); else None."""
    name, _, rest = command.partition(":")
    name = name.upper()
    if not any(c["name"] == name for c in C.ALL_COINS):
        return None
    if rest in C.TIMEFRAMES:
        return name, rest, 0
    if rest.startswith("x") and rest[1:].isdigit() and int(rest[1:]) in C.LEVERAGE_BUTTONS:
        return name, C.TIMEFRAME, int(rest[1:])
    return None


def send(text, dry_run=False, markup=None, reply_to=None):
    """Send a message. With dry_run=True it only prints (useful for testing).

    markup = buttons to show under the message (see coin_keyboard), or None for no buttons.
    reply_to = message id to answer (the message is shown as a reply to it).
    Returns the new message id (truthy) when sent, False otherwise."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if dry_run or not token or not chat_id:
        if not dry_run:
            print("[telegram] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set -> printing instead")
        print("----- TELEGRAM MESSAGE -----\n" + text + "\n----------------------------")
        return False

    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": True,
              **({"reply_markup": markup} if markup else {}),
              **({"reply_parameters": {"message_id": reply_to, "allow_sending_without_reply": True}}
                 if reply_to else {})},
        timeout=20,
    )
    if not resp.ok:
        print(f"[telegram] send failed: {resp.status_code} {resp.text[:300]}")
        return False
    try:
        return resp.json()["result"]["message_id"]
    except Exception:  # sent, but the reply could not be read
        return True
