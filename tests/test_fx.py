"""Gold / forex tests. Offline: every download is faked. Run with:  python -m pytest -q"""

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from bot import config as C
from bot import fx, fx_calendar, fx_data, fx_news, fx_signals, sentiment, state, telegram, tracker
from bot import __main__ as app

GOLD, EURUSD, GBPUSD, USDJPY = C.FX_PAIRS
MONDAY = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)  # a Monday, market open


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No real Claude, no real downloads, empty candle cache."""
    monkeypatch.setattr(sentiment.shutil, "which", lambda name: None)
    fx_data._cache.clear()

    def no_network(*a, **k):
        raise AssertionError("tests must stay offline")
    monkeypatch.setattr(fx_data.requests, "get", no_network)
    monkeypatch.setattr(fx_calendar.requests, "get", no_network)
    monkeypatch.setattr(fx.news, "fetch_all_headlines", lambda feeds, *a: [])


def m15(n=4000, end=MONDAY, start_price=1.10, step=0.00002):
    """Closed 15m candles ending just before `end`, gently rising."""
    times = pd.date_range(end=pd.Timestamp(end) - pd.Timedelta(minutes=15), periods=n, freq="15min")
    close = start_price + step * np.arange(n)
    return pd.DataFrame({"time": times, "open": close - step / 2, "high": close + 0.0003,
                         "low": close - 0.0003, "close": close})


def frame(rows):
    """Candles with hand-picked indicator values: rows of (open, high, low, close, ema20, rsi, atr)."""
    t0 = pd.Timestamp("2026-10-26", tz="UTC")
    return pd.DataFrame([{"time": t0 + pd.Timedelta(minutes=15 * i), "open": o, "high": h, "low": lo,
                          "close": c, "ema20": e, "rsi": r, "atr": a}
                         for i, (o, h, lo, c, e, r, a) in enumerate(rows)])


def flat(n=20, price=1.1000):
    return [(price, price + 0.0010, price - 0.0010, price, price, 55, 0.0010)] * n


# ---------------- market hours ----------------

def test_market_closed_from_friday_22_to_sunday_21_utc():
    friday = datetime(2026, 10, 30, tzinfo=timezone.utc)
    assert fx_data.market_open(friday.replace(hour=21, minute=59))
    assert not fx_data.market_open(friday.replace(hour=22))
    assert not fx_data.market_open(friday + timedelta(days=1, hours=12))  # Saturday
    assert not fx_data.market_open((friday + timedelta(days=2)).replace(hour=20, minute=59))
    assert fx_data.market_open((friday + timedelta(days=2)).replace(hour=21))
    assert fx_data.market_open(MONDAY)


# ---------------- candles ----------------

def test_resample_builds_complete_bigger_candles_only():
    df = m15(n=18, end=pd.Timestamp("2026-10-26 04:30", tz="UTC"))  # 00:00 .. 04:15 = 4h + 2 candles
    h1 = fx_data.resample(df, "1h")
    assert h1["time"].iloc[-1] == pd.Timestamp("2026-10-26 03:00", tz="UTC")  # 04:00 hour not complete
    first = df[df["time"] < pd.Timestamp("2026-10-26 01:00", tz="UTC")]
    assert h1["high"].iloc[0] == first["high"].max() and h1["close"].iloc[0] == first["close"].iloc[-1]
    assert len(fx_data.resample(df, "4h")) == 1


class Resp:
    def __init__(self, body, code=200):
        self.body, self.status_code = body, code

    def json(self):
        return self.body


def test_twelve_data_key_in_header_and_only_closed_candles(monkeypatch):
    monkeypatch.setenv("TWELVEDATA_API_KEY", "secret-key")
    now = pd.Timestamp("2026-10-26 10:07", tz="UTC")
    times = pd.date_range(end="2026-10-26 10:00", periods=1200, freq="15min", tz="UTC")[::-1]  # newest first
    values = [{"datetime": t.strftime("%Y-%m-%d %H:%M:%S"), "open": "1.1", "high": "1.2", "low": "1.0",
               "close": "1.15"} for t in times]
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, params, headers))
        return Resp({"status": "ok", "values": values})
    monkeypatch.setattr(fx_data.requests, "get", fake_get)

    df = fx_data.get_candles(EURUSD, "15m", now.to_pydatetime())
    assert df["time"].iloc[-1] == pd.Timestamp("2026-10-26 09:45", tz="UTC")  # 10:00 candle still open
    assert df["time"].is_monotonic_increasing
    url, params, headers = calls[0]
    assert "secret-key" not in url + json.dumps(params) and headers["Authorization"] == "apikey secret-key"
    fx_data.get_candles(EURUSD, "15m", (now + pd.Timedelta(minutes=5)).to_pydatetime())
    assert len(calls) == 1  # no new candle closed yet -> served from the cache
    fx_data.get_candles(EURUSD, "1h", (now + pd.Timedelta(minutes=5)).to_pydatetime())
    assert len(calls) == 1  # 1h is built from the cached 15m candles


def test_yahoo_backup_without_twelve_key(monkeypatch):
    monkeypatch.delenv("TWELVEDATA_API_KEY", raising=False)
    times = pd.date_range(end="2026-10-26 09:45", periods=1100, freq="15min", tz="UTC")
    body = {"chart": {"result": [{"timestamp": [int(t.timestamp()) for t in times],
                                  "indicators": {"quote": [{"open": [1.0] * 1100, "high": [1.1] * 1100,
                                                            "low": [0.9] * 1099 + [None],
                                                            "close": [1.05] * 1100}]}}]}}
    asked = []
    monkeypatch.setattr(fx_data.requests, "get", lambda url, **k: asked.append(url) or Resp(body))
    df = fx_data.get_candles(GOLD, "15m", MONDAY)
    assert "GC=F" in asked[0] and len(df) == 1099  # the row with a missing value is dropped


# ---------------- strategy ----------------

def test_trend_needs_price_and_ema50_on_one_side_of_ema200():
    up = fx_signals.add_indicators(m15(n=400))
    assert fx_signals.trend(up) == "UP"
    down = fx_signals.add_indicators(m15(n=400, step=-0.00002))
    assert fx_signals.trend(down) == "DOWN"
    assert fx_signals.trend(up.tail(100)) == "SIDEWAYS"  # too few candles for EMA200


def test_breakout_long_with_levels():
    df = frame(flat() + [(1.1000, 1.1022, 1.0998, 1.1020, 1.1005, 62, 0.0010)])
    sig = fx_signals.evaluate(df, "UP")
    assert sig["side"] == "LONG" and "Breakout" in sig["reason"]
    assert sig["stop"] == pytest.approx(1.1020 - 0.0015)              # 1.5 x the 15m ATR
    assert sig["target"] == pytest.approx(1.1020 + 0.0015 * 1.5)      # 1.5R
    assert sig["trend_tf"] == "1h" and sig["htf"] == "UP"


def test_pullback_short_to_ema20():
    df = frame(flat(price=1.2000) + [(1.1999, 1.2001, 1.1993, 1.1994, 1.1997, 40, 0.0010)])  # low stays above 1.1990
    sig = fx_signals.evaluate(df, "DOWN")
    assert sig["side"] == "SHORT" and "Pullback" in sig["reason"]
    assert sig["stop"] == pytest.approx(1.1994 + 0.0015)


def test_only_with_the_1h_trend():
    df = frame(flat() + [(1.1000, 1.1022, 1.0998, 1.1020, 1.1005, 62, 0.0010)])
    assert fx_signals.evaluate(df, "DOWN")["side"] == "WAIT"  # breaks up, but the 1h trend is down
    assert fx_signals.evaluate(df, "SIDEWAYS")["side"] == "WAIT"


def test_rsi_and_distance_filters():
    rsi_high = frame(flat() + [(1.1000, 1.1022, 1.0998, 1.1020, 1.1005, 80, 0.0010)])
    assert "RSI" in fx_signals.evaluate(rsi_high, "UP")["reason"]
    far = frame(flat() + [(1.1000, 1.1032, 1.0998, 1.1030, 1.1005, 62, 0.0010)])  # 2.5 ATR from EMA20
    sig = fx_signals.evaluate(far, "UP")
    assert sig["side"] == "WAIT" and "ATR" in sig["reason"]


def test_key_levels():
    daily = pd.DataFrame({"high": [10.0] * 19 + [12.0], "low": [8.0] * 19 + [9.0], "close": [9.0] * 19 + [11.0]})
    lv = fx_signals.key_levels(daily)
    assert lv["pivot"] == pytest.approx(32 / 3) and lv["r1"] == pytest.approx(2 * 32 / 3 - 9)
    assert lv["high20"] == 12 and lv["low20"] == 8


def test_leverage_plans_keep_stops_inside_liquidation_for_fx():
    plans = fx.leverage.plans("LONG", 1.1020, 0.0010, 1.1000, "UP")
    assert [p["label"] for p in plans] == ["10x-20x", "20x-40x", "40x-60x", "60x-100x"]
    for p in plans:
        assert not p["skip"] and p["liquidation_high"] < p["stop"] < 1.1020


# ---------------- big news calendar ----------------

FF_ROWS = [
    {"title": "Non-Farm Employment Change", "country": "USD", "date": "2026-10-26T08:30:00-04:00", "impact": "High"},
    {"title": "German ifo", "country": "EUR", "date": "2026-10-26T04:00:00-04:00", "impact": "Medium"},
    {"title": "BOJ Policy Rate", "country": "JPY", "date": "2026-10-27T03:00:00+00:00", "impact": "High"},
]


def test_calendar_keeps_big_events_in_utc():
    events = fx_calendar.parse(FF_ROWS)
    assert [e["title"] for e in events] == ["Non-Farm Employment Change", "BOJ Policy Rate"]
    assert events[0]["time"] == "2026-10-26T12:30:00+00:00"


def test_hold_only_in_the_hour_before_and_for_the_right_currencies():
    events = fx_calendar.parse(FF_ROWS)
    nfp = datetime(2026, 10, 26, 12, 30, tzinfo=timezone.utc)
    assert fx_calendar.hold_for(EURUSD, events, nfp - timedelta(minutes=59))["title"].startswith("Non-Farm")
    assert fx_calendar.hold_for(EURUSD, events, nfp - timedelta(minutes=61)) is None
    assert fx_calendar.hold_for(EURUSD, events, nfp + timedelta(minutes=1)) is None
    boj = datetime(2026, 10, 27, 2, 30, tzinfo=timezone.utc)
    assert fx_calendar.hold_for(USDJPY, events, boj) and fx_calendar.hold_for(GBPUSD, events, boj) is None
    sig = fx_signals.hold({"side": "LONG", "reason": "x", "stop": 1, "target": 2},
                          events[0])
    assert sig["side"] == "WAIT" and "held" in sig["reason"]


def test_calendar_cached_in_state(monkeypatch):
    calls = []
    monkeypatch.setattr(fx_calendar.requests, "get",
                        lambda *a, **k: calls.append(1) or type("R", (), {"json": lambda s: FF_ROWS,
                                                                          "raise_for_status": lambda s: None})())
    st = state.load("missing.json")
    assert len(fx_calendar.refresh(st, MONDAY)) == 2
    fx_calendar.refresh(st, MONDAY + timedelta(hours=5))
    assert len(calls) == 1 and st["fx_calendar"]["ok"]
    fx_calendar.refresh(st, MONDAY + timedelta(hours=7))
    assert len(calls) == 2


def test_calendar_failure_keeps_old_events(monkeypatch):
    st = {"fx_calendar": {"checked_at": (MONDAY - timedelta(hours=7)).isoformat(), "ok": True,
                          "events": fx_calendar.parse(FF_ROWS)}}
    assert len(fx_calendar.refresh(st, MONDAY)) == 2  # download blocked by the fixture -> cache kept
    assert st["fx_calendar"]["ok"] is False


# ---------------- news check ----------------

def test_news_sign_is_the_pair_price_direction(monkeypatch):
    asked = []
    monkeypatch.setattr(sentiment, "run_claude", lambda q, s: asked.append((q, s)) or
                        "1|+|Weak dollar data\n2|−|Stronger yen after BOJ hint")
    items = [{"pair": EURUSD, "note": "daily trend UP"}, {"pair": USDJPY, "note": "daily trend DOWN"}]
    out = fx_news.judge(items, ["Dollar slides on weak data", "Yen jumps after BOJ"])
    assert [o["sign"] for o in out] == ["+", "-"] and [o["verdict"] for o in out] == ["BULLISH", "BEARISH"]
    assert len(asked) == 1 and out[0]["source"] == "Claude"  # one call for every pair
    assert "stronger yen pushes USD/JPY DOWN" in asked[0][1]
    assert "Yen jumps" in asked[0][0]


def test_review_needs_a_call_per_signal(monkeypatch):
    monkeypatch.setattr(sentiment, "run_claude", lambda q, s: "1|+|CONFIRM|Clean breakout\n2|-|Dollar strong")
    out = fx_news.judge([{"pair": EURUSD, "note": "x"}, {"pair": GBPUSD, "note": "y"}], [], review=True)
    assert out[0]["call"] == "CONFIRM" and out[0]["why"] == "Clean breakout"
    assert "call" not in out[1]  # line without a CALL -> no review -> the signal will not be sent


def test_news_falls_back_to_vader(monkeypatch):
    monkeypatch.setattr(sentiment, "run_claude", lambda q, s: None)
    out = fx_news.judge([{"pair": GOLD, "note": "x"}],
                        ["Gold soars to a record high as investors celebrate strong gains"])
    assert out[0]["source"] == "VADER backup" and out[0]["sign"] == "+" and "call" not in out[0]
    assert fx_news.judge([{"pair": GOLD, "note": "x"}], [])[0]["source"] == "none"


# ---------------- full gold / forex run (offline) ----------------

def LONG_SIG(adx=30):
    return {"side": "LONG", "reason": "Breakout beyond the 20-candle high", "price": 1.1020, "rsi": 62.0,
            "atr": 0.0010, "adx": adx, "trend": "UP", "htf": "UP", "trend_tf": "1h", "stop": 1.1005,
            "target": 1.10425, "swing_low": 1.1000, "swing_high": 1.1030}


def _fake_market(monkeypatch, side="LONG", adx=(20, 35, 25, 30)):
    """Every pair signals; ADX per pair in C.FX_PAIRS order (EUR/USD is the strongest by default)."""
    monkeypatch.setattr(fx.fx_data, "get_candles", lambda pair, tf, now=None: m15(end=now or MONDAY))
    order = iter(list(adx) * 50)
    monkeypatch.setattr(fx.fx_signals, "evaluate", lambda df, trend, trend_tf=None: {
        **LONG_SIG(next(order)), "side": side, "candle_time": df["time"].iloc[-1].isoformat()})
    monkeypatch.setattr(fx.fx_calendar, "refresh", lambda st, now=None: [])
    monkeypatch.setattr(C, "FX_DAILY_SIGNAL", False)
    sent = []
    monkeypatch.setattr(fx.telegram, "send", lambda text, dry=False, markup=None: sent.append(text) or 123)
    return sent


def _claude(monkeypatch, line="+|CONFIRM|Trend and news agree"):
    calls = []
    monkeypatch.setattr(sentiment, "run_claude", lambda q, s: calls.append(q) or
                        "\n".join(f"{n}|{line}" for n in range(1, 5)))
    return calls


def _history(tmp_path):
    return json.loads((tmp_path / C.HISTORY_FILE).read_text())["signals"]


def test_only_the_strongest_signal_is_sent_others_logged(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sent = _fake_market(monkeypatch)
    calls = _claude(monkeypatch)

    prices = fx.check(now=MONDAY)
    assert set(prices) == {p["name"] for p in C.FX_PAIRS}
    assert len(calls) == 1 and len(sent) == 1  # ONE Claude review for all, max 1 per run
    text = sent[0]
    assert "LONG SIGNAL: EUR/USD</b> · 15m" in text and "Confidence <b>HIGH</b>" in text
    assert "1h trend UP" in text and "Leverage plans" in text and "60x-100x" in text
    assert "expires after 4h" in text and "Claude: ✅ CONFIRM" in text and "🕒 Analysed" in text
    hist = _history(tmp_path)
    sent_rec = [r for r in hist if not r.get("skipped")]
    assert [r["coin"] for r in sent_rec] == ["EUR/USD"] and sent_rec[0]["expire_hours"] == 4
    assert sorted(r["skipped"] for r in hist if r.get("skipped")) == ["run limit"] * 3
    assert all(r["market"] == "fx" and r["announced"] for r in hist if r.get("skipped"))

    sent.clear()
    fx.check(now=MONDAY + timedelta(minutes=5))  # same candle -> nothing new, no Claude call
    assert sent == [] and len(calls) == 1


def test_max_3_per_day(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sent = _fake_market(monkeypatch)
    _claude(monkeypatch)
    today = [{"id": f"x{i}", "coin": "XAU/USD", "symbol": "XAU/USD", "market": "fx", "status": "WIN",
              "sent_at": (MONDAY - timedelta(hours=i + 1)).isoformat(), "announced": True, "side": "LONG",
              "candle_time": "2026-10-26T00:00:00+00:00", "tf": "15m", "closed_at": None, "result_r": 1.5}
             for i in range(C.FX_MAX_SIGNALS_PER_DAY)]
    tracker.save(C.HISTORY_FILE, {"signals": today})
    fx.check(now=MONDAY)
    assert sent == []
    assert {r["skipped"] for r in _history(tmp_path) if r.get("skipped")} == {"daily limit"}


def test_claude_caution_means_no_signal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sent = _fake_market(monkeypatch)
    _claude(monkeypatch, "+|CAUTION|Move already stretched")
    fx.check(now=MONDAY)
    assert sent == []
    assert {r["skipped"] for r in _history(tmp_path)} == {"Claude"}


def test_news_against_the_side_means_no_signal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sent = _fake_market(monkeypatch)
    _claude(monkeypatch, "-|CONFIRM|Chart fine but dollar news strong")
    fx.check(now=MONDAY)
    assert sent == [] and {r["skipped"] for r in _history(tmp_path)} == {"news"}


def test_medium_only_when_configured(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(C, "FX_SEND_CONFIDENCE", ["HIGH"])
    sent = _fake_market(monkeypatch)
    _claude(monkeypatch, "0|CONFIRM|No news either way")
    fx.check(now=MONDAY)
    assert sent == [] and {r["skipped"] for r in _history(tmp_path)} == {"confidence"}

    monkeypatch.chdir(tmp_path / "..")
    (tmp_path / "b").mkdir()
    monkeypatch.chdir(tmp_path / "b")
    monkeypatch.setattr(C, "FX_SEND_CONFIDENCE", ["HIGH", "MEDIUM"])
    fx.check(now=MONDAY)
    assert len(sent) == 1 and "Confidence <b>MEDIUM</b>" in sent[0]


def test_no_claude_means_no_signal_but_still_logged(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sent = _fake_market(monkeypatch)
    monkeypatch.setattr(sentiment, "run_claude", lambda q, s: None)
    fx.check(now=MONDAY)
    assert sent == [] and {r["skipped"] for r in _history(tmp_path)} == {"no Claude"}


def test_dry_run_logs_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sent = _fake_market(monkeypatch)
    _claude(monkeypatch)
    fx.check(dry_run=True, now=MONDAY)
    assert len(sent) == 1 and _history(tmp_path) == []


def test_fx_skipped_when_market_closed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sent = _fake_market(monkeypatch)
    assert fx.check(now=datetime(2026, 10, 31, 12, tzinfo=timezone.utc)) == {}
    assert sent == []


def test_fx_signal_held_before_big_news(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sent = _fake_market(monkeypatch)
    calls = _claude(monkeypatch)
    nfp = [{"time": (MONDAY + timedelta(minutes=30)).isoformat(), "currency": "USD", "title": "NFP"}]
    monkeypatch.setattr(fx.fx_calendar, "refresh", lambda st, now=None: nfp)
    fx.check(now=MONDAY)
    assert sent == [] and calls == []  # every pair has USD: all WAIT, Claude not even asked


def test_daily_bias_at_6_local_once(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sent = _fake_market(monkeypatch, side="WAIT")
    monkeypatch.setattr(C, "FX_DAILY_SIGNAL", True)
    monkeypatch.setattr(fx.fx_data, "get_candles", lambda pair, tf, now=None: (
        m15(n=300, end=MONDAY).assign(time=lambda d: pd.date_range(end="2026-10-26", periods=300, freq="1D"))
        if tf == "1d" else m15(end=now)))
    events = [{"time": "2026-10-27T12:30:00+00:00", "currency": "USD", "title": "CPI m/m"}]
    monkeypatch.setattr(fx.fx_calendar, "refresh", lambda st, now=None: events)
    claude = []
    monkeypatch.setattr(sentiment, "run_claude", lambda q, s: claude.append(q) or "1|-|Strong dollar\n2|+|Weak dollar")

    fx.check(now=datetime(2026, 10, 26, 22, 59, tzinfo=timezone.utc))  # 05:59 in Phnom Penh
    assert sent == []
    six = datetime(2026, 10, 26, 23, 0, tzinfo=timezone.utc)          # 06:00 Tuesday in Phnom Penh
    fx.check(now=six)
    assert len(sent) == 1 and len(claude) == 1
    text = sent[0]
    assert "Daily gold &amp; forex bias" in text and "Analysed 27 Oct 2026 06:00 (UTC+7)" in text
    assert "XAU/USD WAIT" in text and "news points the other way" in text  # trend up, news down
    assert "EUR/USD LONG" in text and "📈 price UP" in text
    assert "Pivot" in text and "Tue 19:30 USD CPI m/m" in text
    assert _history(tmp_path) == []  # a bias is not a tracked signal
    fx.check(now=six + timedelta(hours=1))
    assert len(sent) == 1  # once a day


def test_open_fx_signal_is_judged_and_announced(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sent = _fake_market(monkeypatch, side="WAIT")
    hist = {"signals": []}
    tracker.record(hist, EURUSD, {**LONG_SIG(), "candle_time": "2026-10-26T08:00:00+00:00",
                                  "stop": 0.5, "target": 1.0}, tf="15m",
                   extra={"market": "fx", "digits": 5, "expire_hours": 4})
    tracker.save(C.HISTORY_FILE, hist)
    fx.check(now=MONDAY)
    rec = _history(tmp_path)[0]
    assert rec["status"] == "WIN" and rec["announced"]
    assert any("WIN: <b>EUR/USD LONG</b> · 15m hit take profit first" in t for t in sent)


def test_fx_signal_expires_after_4_hours():
    rec = {"id": "x", "coin": "EUR/USD", "symbol": "EUR/USD", "side": "LONG", "entry": 1.10, "stop": 1.09,
           "target": 1.11, "candle_time": "2026-10-26T00:00:00+00:00", "tf": "15m", "status": "OPEN",
           "closed_at": None, "result_r": None, "announced": False, "market": "fx", "expire_hours": 4}
    bars = pd.DataFrame({"time": pd.date_range("2026-10-26 00:15", periods=16, freq="15min", tz="UTC"),
                         "high": [1.101] * 16, "low": [1.099] * 16, "close": [1.10] * 16})
    assert not tracker.resolve(rec, bars, datetime(2026, 10, 26, 4, 14, tzinfo=timezone.utc))
    assert tracker.resolve(rec, bars, datetime(2026, 10, 26, 4, 15, tzinfo=timezone.utc))
    assert rec["status"] == "EXPIRED" and "neither level in 4h" in telegram.format_result(rec)


# ---------------- /stats: gold / forex apart from crypto ----------------

def test_stats_show_gold_fx_apart_from_crypto_with_avg_time():
    def rec(market, status, r, hours):
        return {"coin": "EUR/USD" if market else "BTC", "tf": "15m", "status": status, "result_r": r,
                "side": "LONG", "candle_time": "2026-10-26T00:00:00+00:00",
                "closed_at": (datetime(2026, 10, 26, 0, 15, tzinfo=timezone.utc) + timedelta(hours=hours)).isoformat(),
                **({"market": market} if market else {})}
    h = {"signals": [rec("fx", "WIN", 1.5, 2), rec("fx", "LOSS", -1.0, 1), rec(None, "LOSS", -1.0, 3),
                     {**rec("fx", "WIN", 1.5, 1), "skipped": "run limit"}]}
    st = tracker.stats(h, MONDAY)
    assert st["by_market"]["fx"]["win_rate"] == 50 and st["by_market"]["fx"]["avg_hours"] == pytest.approx(1.5)
    assert st["by_market"]["crypto"]["losses"] == 1
    text = telegram.format_stats(st, {})
    assert "Crypto vs Gold/FX" in text and "Gold/FX: 1✅ 1❌ = <b>50%</b>" in text and "1.5h per trade" in text
    assert "Crypto vs Gold/FX" not in telegram.format_stats(tracker.stats({"signals": [h["signals"][2]]}), {})


def test_stats_live_price_for_fx_uses_fx_data(monkeypatch, capsys):
    monkeypatch.setattr(app.fx, "live_price", lambda symbol: 1.1100)
    monkeypatch.setattr(app.market_data, "get_price", lambda s: pytest.fail("not a crypto symbol"))
    monkeypatch.setattr(app.telegram, "send", lambda text, dry=False, markup=None: print(text) or True)
    rec = {"coin": "EUR/USD", "symbol": "EUR/USD", "market": "fx", "digits": 5, "tf": "1h", "side": "LONG",
           "entry": 1.1000, "status": "OPEN", "closed_at": None, "result_r": None,
           "candle_time": "2026-10-26T00:00:00+00:00"}
    app.send_stats({"signals": [rec]}, {})
    assert "now 1.11000 (+0.91%)" in capsys.readouterr().out


# ---------------- laptop: .env and loop ----------------

def test_env_file_loaded_without_overriding_or_paid_api_key(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('# comment\nTWELVEDATA_API_KEY="abc"\nTELEGRAM_CHAT_ID=from_file\n'
                   "ANTHROPIC_API_KEY=sk-never\n", encoding="utf-8")
    monkeypatch.delenv("TWELVEDATA_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "already_set")
    app.load_env_file(str(env))
    import os
    assert os.environ["TWELVEDATA_API_KEY"] == "abc" and os.environ["TELEGRAM_CHAT_ID"] == "already_set"
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_gitignore_keeps_env_out():
    from pathlib import Path
    assert ".env" in Path(__file__).resolve().parent.parent.joinpath(".gitignore").read_text().split()


def test_loop_logs_a_failed_run_and_keeps_going(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    runs = []

    def broken_detect():
        runs.append(1)
        raise RuntimeError("Binance is down")
    monkeypatch.setattr(app, "detect", broken_detect)
    monkeypatch.setattr(app.fx, "check", lambda dry_run=False: runs.append("fx") or {})
    clock = iter(range(0, 10_000, 400))  # every look at the clock is 400 s later -> a new check each time
    monkeypatch.setattr(app.time, "time", lambda: next(clock))
    naps = []
    monkeypatch.setattr(app.time, "sleep",
                        lambda s: naps.append(s) if len(naps) < 1 else (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        app.watch(True, C.LOOP_EVERY_MIN, with_fx=True)
    out = capsys.readouterr().out
    assert runs == [1, "fx", 1, "fx"]  # failed twice, never stopped, gold / forex still ran
    assert "[loop]" in out and "Binance is down" in out and "retry in 5 min" in out


def test_loop_stops_after_for_min(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    runs = []
    monkeypatch.setattr(app, "detect", lambda: runs.append(1) or {})
    monkeypatch.setattr(app, "notify", lambda dry_run=False: None)
    monkeypatch.setattr(app.fx, "check", lambda dry_run=False: {})
    clock = iter(range(0, 100_000, 400))
    monkeypatch.setattr(app.time, "time", lambda: next(clock))
    monkeypatch.setattr(app.time, "sleep", lambda s: None)
    app.watch(True, C.LOOP_EVERY_MIN, with_fx=True, for_min=30)  # returns by itself, no Ctrl+C
    assert runs and "ran 30 min -> stopping" in capsys.readouterr().out


def test_sideways_1h_follows_the_15m_trend():
    df = frame(flat() + [(1.1000, 1.1022, 1.0998, 1.1020, 1.1005, 62, 0.0010)])
    sig = fx_signals.evaluate(df, "UP", "15m")
    assert sig["side"] == "LONG" and sig["trend_tf"] == "15m"


def test_loop_command_stops_cleanly_on_ctrl_c(monkeypatch, capsys):
    monkeypatch.setattr(app, "watch", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt))
    monkeypatch.setattr(app, "load_env_file", lambda path=".env": None)
    monkeypatch.setattr(app.sys, "argv", ["bot", "loop"])
    app.main()
    assert "stopped (Ctrl+C)" in capsys.readouterr().out
