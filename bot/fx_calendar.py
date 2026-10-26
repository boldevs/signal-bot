"""
fx_calendar.py - Big news events (Fed, CPI, NFP...) from the free ForexFactory calendar.

Used two ways:
    hold_for()  a gold / forex signal becomes WAIT within NEWS_HOLD_MINUTES before a big event
                in one of the pair's currencies (prices jump around news)
    upcoming()  the events listed in the morning daily signal

The calendar (this week only, high impact only) is cached in state.json and downloaded again
after CALENDAR_REFRESH_HOURS (1 hour after a failed download), so the site is not hammered.
"""

from datetime import datetime, timedelta, timezone

import requests

from . import config as C

HEADERS = {"User-Agent": "Mozilla/5.0 (signal-bot)"}
RETRY_AFTER_FAIL_HOURS = 1


def parse(rows):
    """ForexFactory JSON rows -> sorted list of {time (UTC ISO), currency, title} for big events
    in the watched pairs' currencies (others are dropped to keep state.json small)."""
    watched = {c for p in C.FX_PAIRS for c in p["currencies"]}
    out = []
    for r in rows:
        if r.get("impact") not in C.NEWS_HOLD_IMPACT or str(r.get("country", "")).upper() not in watched:
            continue
        try:
            when = datetime.fromisoformat(r["date"]).astimezone(timezone.utc)
        except (KeyError, TypeError, ValueError):
            continue
        out.append({"time": when.isoformat(), "currency": str(r.get("country", "")).upper(),
                    "title": str(r.get("title", ""))[:80]})
    return sorted(out, key=lambda e: e["time"])


def refresh(st, now=None):
    """Big events of this week, from the cache in st (state.json) or a fresh download."""
    now = now or datetime.now(timezone.utc)
    cache = st.get("fx_calendar") or {}
    wait = C.CALENDAR_REFRESH_HOURS if cache.get("ok") else RETRY_AFTER_FAIL_HOURS
    checked = cache.get("checked_at")
    if checked and now < datetime.fromisoformat(checked) + timedelta(hours=wait):
        return cache.get("events", [])
    try:
        resp = requests.get(C.FF_CALENDAR_URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        st["fx_calendar"] = {"checked_at": now.isoformat(), "ok": True, "events": parse(resp.json())}
    except Exception as exc:
        print(f"[calendar] download failed, using the cached events: {exc}")
        st["fx_calendar"] = {**cache, "checked_at": now.isoformat(), "ok": False}
    return st["fx_calendar"].get("events", [])


def hold_for(pair, events, now=None):
    """The big event that holds this pair right now (starts within NEWS_HOLD_MINUTES), or None."""
    now = now or datetime.now(timezone.utc)
    for ev in events:
        if ev["currency"] not in pair["currencies"]:
            continue
        when = datetime.fromisoformat(ev["time"])
        if when - timedelta(minutes=C.NEWS_HOLD_MINUTES) <= now < when:
            return ev
    return None


def upcoming(events, now=None, hours=24, currencies=None):
    """Big events in the next `hours`, for these currencies (default: every watched pair's)."""
    now = now or datetime.now(timezone.utc)
    if currencies is None:
        currencies = {c for p in C.FX_PAIRS for c in p["currencies"]}
    end = now + timedelta(hours=hours)
    return [ev for ev in events
            if ev["currency"] in currencies and now <= datetime.fromisoformat(ev["time"]) < end]
