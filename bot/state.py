"""
state.py - The bot's small memory (state.json).

It remembers which alerts were already sent, so you don't get the same
alert again every 10 minutes. On GitHub Actions this file is saved back
into your repository after each run.
"""

import json
import os
from datetime import datetime, timedelta, timezone

from . import config as C
from . import market_data

COOLDOWN_CANDLES = 2  # don't repeat the same LONG/SHORT for a coin within 2 candles


def load(path):
    if not os.path.exists(path):
        return {"last_alert": {}, "last_summary_date": None, "seen_events": [], "seen_macro": [],
                "tg_offset": None, "fx_calendar": {}, "last_fx_daily_date": None}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("last_alert", {})
    data.pop("latest", None)
    data.setdefault("last_summary_date", None)
    data.setdefault("seen_events", [])
    data.setdefault("seen_macro", [])
    data.setdefault("tg_offset", None)
    data.setdefault("fx_calendar", {})  # ForexFactory big-news cache (fx_calendar.py)
    data.setdefault("last_fx_daily_date", None)
    return data


def save(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def is_new_signal(state, symbol, side, candle_time, tf=None):
    """True if this LONG/SHORT should be alerted (not sent before, not in cooldown).

    tf = candle size of the signal (default config.TIMEFRAME); gold / forex use 'SYMBOL:tf' as symbol."""
    if side == "WAIT":
        return False
    last = state["last_alert"].get(symbol)
    if not last:
        return True
    if last["candle_time"] == candle_time:
        return False  # same candle, already alerted
    candles = COOLDOWN_CANDLES if last["side"] == side else C.FLIP_COOLDOWN_CANDLES  # flip-flopping is noise
    gap = datetime.fromisoformat(candle_time) - datetime.fromisoformat(last["candle_time"])
    return gap >= timedelta(minutes=market_data.INTERVAL_MINUTES[tf or C.TIMEFRAME]) * candles


def mark_alerted(state, symbol, side, candle_time, msg_id=None, sent=False):
    """Remember the alert. sent=True = a real LONG/SHORT message went out (not a WAIT or an
    "also signaling" mention): only those count when a macro alert is checked for conflicts."""
    state["last_alert"][symbol] = {"side": side, "candle_time": candle_time}
    if sent:
        state["last_alert"][symbol].update(sent_at=datetime.now(timezone.utc).isoformat(), msg_id=msg_id)


def recent_signal(state, symbol, hours, now=None):
    """The LONG/SHORT message sent for this coin within `hours`, or None."""
    last = state["last_alert"].get(symbol)
    if not last or not last.get("sent_at"):
        return None
    now = now or datetime.now(timezone.utc)
    return last if now - datetime.fromisoformat(last["sent_at"]) <= timedelta(hours=hours) else None


def summary_due(state, hour_utc, now=None):
    if hour_utc is None:
        return False
    now = now or datetime.now(timezone.utc)
    return now.hour >= hour_utc and state.get("last_summary_date") != now.date().isoformat()


MAX_SEEN_EVENTS = 300  # keep state.json small


def mark_events_seen(state, ids):
    state["seen_events"] = (state["seen_events"] + list(ids))[-MAX_SEEN_EVENTS:]


def mark_macro_seen(state, ids):
    state["seen_macro"] = (state["seen_macro"] + list(ids))[-MAX_SEEN_EVENTS:]
