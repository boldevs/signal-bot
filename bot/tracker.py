"""
tracker.py - Remembers every signal and checks later whether it won (see GLOSSARY.md).

A signal is a WIN if the price reaches the take profit before the stop loss, a LOSS if the
stop comes first (or both in the same candle), and EXPIRED if neither happens within
expire_hours() (config.RESULT_EXPIRE_HOURS, shorter for 15m / 30m candles). Free: it only reads candles, no Claude.

Crypto signals the filters held back are recorded too, quietly (rec['skipped'] = why, see SKIP_REASONS):
judged the same way, never announced, and kept out of the normal results so /stats can compare them.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import pandas as pd

from . import config as C
from . import market_data

MAX_RECORDS = 500  # keep history.json small


def load(path):
    if not os.path.exists(path):
        return {"signals": []}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("signals", [])
    return data


def save(path, data):
    data["signals"] = data["signals"][-MAX_RECORDS:]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def record(history, coin, sig, call=None, tf=None, extra=None):
    """Add a sent signal to the history (once per coin, candle size and candle).

    tf = candle size (default config.TIMEFRAME). extra = more fields, e.g. gold / forex records have
    market "fx", digits and expire_hours."""
    sid = f"{coin['symbol']}-{tf}-{sig['candle_time']}" if tf else f"{coin['symbol']}-{sig['candle_time']}"
    if any(r["id"] == sid for r in history["signals"]):
        return
    history["signals"].append({
        "id": sid, "coin": coin["name"], "symbol": coin["symbol"], "side": sig["side"],
        "entry": sig["price"], "stop": sig["stop"], "target": sig["target"],
        "candle_time": sig["candle_time"], "tf": tf or C.TIMEFRAME, "status": "OPEN", "closed_at": None,
        "result_r": None, "announced": False, "move_1h": None, "move_2h": None,
        "call": call,  # Claude's CONFIRM / CAUTION / AVOID, so /stats can show whether it helps
        **(extra or {}),
    })


SKIP_REASONS = {  # rec['skipped'] -> text shown in /stats
    "trend": "trend did not agree",
    "no Claude": "no Claude review",
    "Claude": "Claude CAUTION / AVOID",
    "news": "news against it",
    "confidence": "confidence too low",
    "also signaling": "weaker coin, same direction",
    "run limit": "max per run",
    "daily limit": "max per day",
}


def skip_fields(reason):
    """`extra` for record() when the filters held a signal back: logged quietly, never announced."""
    return {"skipped": reason, "announced": True}


def sent_today(history, now=None):
    """Crypto signals really sent today (local day, config.LOCAL_UTC_OFFSET_HOURS)."""
    local = timezone(timedelta(hours=C.LOCAL_UTC_OFFSET_HOURS))
    today = (now or datetime.now(timezone.utc)).astimezone(local).date()
    return sum(1 for r in history["signals"]
               if r.get("sent_at") and not r.get("skipped") and r.get("market") != "fx"
               and datetime.fromisoformat(r["sent_at"]).astimezone(local).date() == today)


def rec_tf(rec):
    return rec.get("tf", "1h")  # records from before the buttons existed were all 1h


def _interval(rec):
    return pd.Timedelta(minutes=market_data.INTERVAL_MINUTES[rec_tf(rec)])


def expire_hours(rec):
    """Hours until an open signal EXPIRES: its own expire_hours (gold / forex), else by candle size."""
    return rec.get("expire_hours") or C.EXPIRE_HOURS_BY_TF.get(rec_tf(rec), C.RESULT_EXPIRE_HOURS)


def hours_taken(rec):
    """Hours from the signal to its WIN / LOSS (None if not finished that way)."""
    if rec["status"] not in ("WIN", "LOSS") or not rec["closed_at"]:
        return None
    start = pd.Timestamp(rec["candle_time"]) + _interval(rec)
    return max((pd.Timestamp(rec["closed_at"]) - start).total_seconds() / 3600, 0.0)


def resolve(rec, df, now=None):
    """Update one open record from fresh candles. Returns True if it just finished."""
    if rec["status"] != "OPEN":
        return False
    now = now or datetime.now(timezone.utc)
    start = pd.Timestamp(rec["candle_time"]) + _interval(rec)
    after = df[df["time"] >= start]
    long = rec["side"] == "LONG"
    entry, stop, target = rec["entry"], rec["stop"], rec["target"]

    def move(close):  # % move in the signal's favour
        return round((close - entry) / entry * 100 * (1 if long else -1), 2)

    if len(after) >= 1:
        rec["move_1h"] = move(float(after.iloc[0]["close"]))
    if len(after) >= 2:
        rec["move_2h"] = move(float(after.iloc[1]["close"]))

    for _, c in after.iterrows():
        hit_stop = c["low"] <= stop if long else c["high"] >= stop
        hit_target = c["high"] >= target if long else c["low"] <= target
        if hit_stop or hit_target:  # both in one candle -> count as a loss
            rec["status"] = "LOSS" if hit_stop else "WIN"
            rec["result_r"] = -1.0 if hit_stop else round(abs(target - entry) / abs(entry - stop), 2)
            rec["closed_at"] = (c["time"] + _interval(rec)).isoformat()
            return True

    if now >= start + timedelta(hours=expire_hours(rec)):
        rec["status"] = "EXPIRED"
        rec["closed_at"] = now.isoformat()
        return True
    return False


def unannounced(history):
    """Finished signals whose result message has not been sent yet."""
    return [r for r in history["signals"] if r["status"] != "OPEN" and not r["announced"]]


def _block(rows):
    wins = sum(r["status"] == "WIN" for r in rows)
    losses = sum(r["status"] == "LOSS" for r in rows)
    done = wins + losses
    hours = [h for h in map(hours_taken, rows) if h is not None]
    return {
        "avg_hours": sum(hours) / len(hours) if hours else None,
        "wins": wins, "losses": losses,
        "expired": sum(r["status"] == "EXPIRED" for r in rows),
        "win_rate": wins / done * 100 if done else None,
        "avg_r": sum(r["result_r"] for r in rows if r["status"] in ("WIN", "LOSS")) / done if done else None,
    }


def stats(history, now=None):
    now = now or datetime.now(timezone.utc)
    everything = history["signals"]
    rows = [r for r in everything if not r.get("skipped")]  # the signals really sent
    skipped = [r for r in everything if r.get("skipped")]
    day_ago = (now - timedelta(hours=24)).isoformat()
    by_coin = {}
    for r in rows:
        by_coin.setdefault(r["coin"], []).append(r)
    return {
        "all": _block(rows),
        "last_24h": _block([r for r in rows if r["closed_at"] and r["closed_at"] >= day_ago]),
        "by_coin": {name: _block(rs) for name, rs in by_coin.items()},
        "by_call": {call: _block([r for r in everything if r.get("call") == call])  # sent + skipped
                    for call in ("CONFIRM", "CAUTION", "AVOID") if any(r.get("call") == call for r in everything)},
        "skipped": _block(skipped),
        "by_skip": {why: _block([r for r in skipped if r["skipped"] == why])
                    for why in SKIP_REASONS if any(r["skipped"] == why for r in skipped)},
        "by_market": ({"crypto": _block([r for r in rows if r.get("market") != "fx"]),
                       "fx": _block([r for r in rows if r.get("market") == "fx"])}
                      if any(r.get("market") == "fx" for r in rows) else {}),  # only once gold / forex exist
        "open": [r for r in rows if r["status"] == "OPEN"],
    }
