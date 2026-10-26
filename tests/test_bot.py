"""Tests that run offline with fake data. Run with:  python -m pytest -q"""

import json
import os
import stat
import sys

import numpy as np
import pandas as pd
import pytest

from bot import config as C
from bot import indicators, news, sentiment, signals, state, telegram
from bot import __main__ as app


@pytest.fixture(autouse=True)
def no_real_claude(request, monkeypatch):
    """Tests stay offline and never start the real `claude` program (tests that fake it on PATH are exempt)."""
    if request.node.name not in ("test_ask_claude_real_subprocess", "test_claude_failure_uses_backup"):
        monkeypatch.setattr(sentiment.shutil, "which", lambda name: None)


def make_candles(closes):
    closes = np.asarray(closes, dtype=float)
    times = pd.date_range("2026-01-01", periods=len(closes), freq="1h", tz="UTC")
    return pd.DataFrame({
        "time": times, "open": closes, "high": closes * 1.01,
        "low": closes * 0.99, "close": closes, "volume": 1000.0,
    })


def with_ind(df):
    return indicators.add_indicators(df, C.EMA_FAST, C.EMA_SLOW, C.RSI_PERIOD, C.ATR_PERIOD)


# ---------------- indicators ----------------

def test_rsi_extremes():
    up = indicators.rsi(pd.Series(np.arange(1, 60, dtype=float)))
    down = indicators.rsi(pd.Series(np.arange(60, 1, -1, dtype=float)))
    assert up.iloc[-1] == pytest.approx(100)
    assert down.iloc[-1] == pytest.approx(0)


def test_rsi_matches_known_value():
    # Classic Wilder example data; RSI(14) at the 15th point is about 70.5
    data = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84,
            46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
    value = indicators.rsi(pd.Series(data)).iloc[-1]
    assert value == pytest.approx(70.46, abs=0.1)  # textbook value ~70.5


# ---------------- signals ----------------

def ind_rows(prev, last):
    """Build a 2-candle table with hand-picked indicator values (prev, last)."""
    rows = []
    for i, (close, ema_f, ema_s, rsi_v) in enumerate([prev, last]):
        rows.append({"time": pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(hours=4 * i),
                     "close": close, "ema_fast": ema_f, "ema_slow": ema_s, "rsi": rsi_v, "atr": 2.0})
    return pd.DataFrame(rows)


def test_long_on_ema_cross_up():
    sig = signals.evaluate(ind_rows((100, 99, 100, 55), (102, 100.5, 100.2, 58)))
    assert sig["side"] == "LONG" and "crossed above" in sig["reason"]
    assert sig["stop"] == pytest.approx(102 - 1.5 * 2) and sig["target"] == pytest.approx(102 + 3 * C.REWARD_RISK)
    assert sig["liquidation"] == pytest.approx(102 * (1 - 1 / C.LEVERAGE))


def test_long_on_pullback_in_uptrend():
    sig = signals.evaluate(ind_rows((105, 104, 100, 38), (106, 104.5, 100.5, 42)))
    assert sig["side"] == "LONG" and "RSI recovered" in sig["reason"]


def test_overbought_blocks_long():
    assert signals.evaluate(ind_rows((100, 99, 100, 75), (102, 100.5, 100.2, 80)))["side"] == "WAIT"


def test_short_on_ema_cross_down():
    sig = signals.evaluate(ind_rows((100, 101, 100, 45), (98, 99.5, 99.8, 42)))
    assert sig["side"] == "SHORT"
    assert sig["target"] < sig["price"] < sig["stop"] < sig["liquidation"]


def test_short_on_bounce_in_downtrend():
    sig = signals.evaluate(ind_rows((95, 96, 100, 62), (94, 95.5, 99.5, 58)))
    assert sig["side"] == "SHORT" and "RSI fell" in sig["reason"]


def test_real_indicator_pipeline_runs():
    closes = 100 + np.cumsum(np.random.default_rng(1).normal(0, 1, 200))
    sig = signals.evaluate(with_ind(make_candles(closes)))
    assert sig["side"] in ("LONG", "SHORT", "WAIT") and 0 <= sig["rsi"] <= 100


def test_wait_in_flat_market():
    closes = 100 + np.sin(np.linspace(0, 3, 150)) * 0.01
    sig = signals.evaluate(with_ind(make_candles(closes)))
    assert sig["side"] == "WAIT"
    assert sig["stop"] is None


# ---------------- state / no-spam ----------------

def test_no_duplicate_alerts():
    st = {"last_alert": {}, "last_summary_date": None}
    t1 = "2026-01-01T00:00:00+00:00"
    assert state.is_new_signal(st, "BTCUSDT", "LONG", t1)
    state.mark_alerted(st, "BTCUSDT", "LONG", t1)
    assert not state.is_new_signal(st, "BTCUSDT", "LONG", t1)                           # same candle
    assert C.TIMEFRAME == "15m"  # day trading: the times below are 15m candles
    assert not state.is_new_signal(st, "BTCUSDT", "LONG", "2026-01-01T00:15:00+00:00")  # cooldown
    assert state.is_new_signal(st, "BTCUSDT", "SHORT", "2026-01-01T00:45:00+00:00")     # new side
    assert state.is_new_signal(st, "BTCUSDT", "LONG", "2026-01-01T00:30:00+00:00")      # after cooldown
    assert not state.is_new_signal(st, "BTCUSDT", "WAIT", "2026-01-03T00:00:00+00:00")


def test_summary_due():
    from datetime import datetime, timezone
    now = datetime(2026, 1, 1, 2, tzinfo=timezone.utc)
    assert state.summary_due({"last_summary_date": None}, 1, now)
    assert not state.summary_due({"last_summary_date": "2026-01-01"}, 1, now)
    assert not state.summary_due({"last_summary_date": None}, 3, now)
    assert not state.summary_due({"last_summary_date": None}, None, now)


# ---------------- news ----------------

RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Bitcoin ETF sees record inflows</title><pubDate>Sun, 18 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title>Solana network upgrade goes live</title></item>
<item><title>New solution for DeFi wallets</title></item>
<item><title>Ethereum fees drop</title></item>
</channel></rss>"""


def test_parse_and_filter_news():
    titles = [t for t, _ in news.parse_feed(RSS)]
    assert len(titles) == 4
    sol = news.filter_for_coin(titles, ["solana", "sol"], 10)
    assert sol == ["Solana network upgrade goes live"]  # "solution" must NOT match
    assert news.filter_for_coin(titles, ["bitcoin", "btc"], 10) == ["Bitcoin ETF sees record inflows"]


# ---------------- Claude answer parsing + backup ----------------

def test_parse_claude_answer():
    assert sentiment.parse_claude_answer("VERDICT: BULLISH\nREASON: ETF inflows") == ("BULLISH", "ETF inflows")
    assert sentiment.parse_claude_answer("**VERDICT:** bearish\nREASON: hack") == ("BEARISH", "hack")
    assert sentiment.parse_claude_answer("I am not sure") is None
    assert sentiment.parse_claude_answer("") is None


def test_vader_backup():
    verdict, _ = sentiment.vader_sentiment(["Bitcoin soars to record high as huge gains excite investors"])
    assert verdict == "BULLISH"
    verdict, _ = sentiment.vader_sentiment(["Exchange hacked, investors lose millions in terrible crash"])
    assert verdict == "BEARISH"


def test_no_headlines_skips_claude(monkeypatch):
    monkeypatch.setattr(sentiment, "ask_claude", lambda q: pytest.fail("Claude should not be called"))
    assert sentiment.analyze("BTC", "LONG", [])["source"] == "none"


def _fake_claude(tmp_path, body):
    """Create a fake `claude` command so we can test the real subprocess call."""
    script = tmp_path / "claude"
    script.write_text(f"#!{sys.executable}\n{body}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return tmp_path


@pytest.mark.skipif(sys.platform == "win32", reason="fake claude relies on a #! script, which Windows ignores")
def test_ask_claude_real_subprocess(tmp_path, monkeypatch):
    body = (
        "import os, sys, json\n"
        "json.dump({'argv': sys.argv[1:], 'api_key': os.environ.get('ANTHROPIC_API_KEY')},"
        " open(os.path.join(os.path.dirname(sys.argv[0]), 'call.json'), 'w'))\n"
        "print('VERDICT: BEARISH')\nprint('REASON: Regulators sue exchange')"
    )
    folder = _fake_claude(tmp_path, body)
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-be-removed")

    result = sentiment.analyze("ETH", "SHORT", ["SEC sues big exchange"])
    assert result == {"verdict": "BEARISH", "reason": "Regulators sue exchange", "source": "Claude"}

    call = json.loads((folder / "call.json").read_text())
    assert call["api_key"] is None                       # paid API key was removed
    assert call["argv"][0] == "-p"
    assert "--model" in call["argv"] and C.CLAUDE_MODEL in call["argv"]
    assert "--tools" in call["argv"] and "--system-prompt" in call["argv"]


@pytest.mark.skipif(sys.platform == "win32", reason="fake claude relies on a #! script, which Windows ignores")
def test_claude_failure_uses_backup(tmp_path, monkeypatch):
    folder = _fake_claude(tmp_path, "import sys\nprint('usage limit reached', file=sys.stderr)\nsys.exit(1)")
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")
    result = sentiment.analyze("BTC", "LONG", ["Bitcoin rallies strongly on great news"])
    assert result["source"] == "VADER backup"


# ---------------- Telegram formatting ----------------

def test_format_signal_message():
    sig = {"side": "LONG", "reason": "EMA20 crossed above EMA50", "price": 67420.5, "rsi": 45.2,
           "atr": 900, "trend": "UP", "candle_time": "x", "stop": 66070.5, "target": 70120.5,
           "liquidation": 33710.25}
    news_v = {"verdict": "BEARISH", "reason": "Exchange <hack> & outflows", "source": "Claude"}
    text = telegram.format_signal("BTC", sig, news_v)
    assert "LONG SIGNAL: BTC" in text and "$67,420.50" in text
    assert "DISAGREES" in text
    assert "&lt;hack&gt; &amp;" in text  # special characters escaped for Telegram HTML
    assert "🕒 Analysed" in text


def test_analysed_line_uses_local_time():
    from datetime import datetime, timezone
    now = datetime(2026, 10, 26, 7, 5, tzinfo=timezone.utc)
    assert telegram.analysed_line(now) == "🕒 Analysed 26 Oct 2026 14:05 (UTC+7)"


# ---------------- full run (offline) ----------------

def test_full_run_dry(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    candles = make_candles(np.linspace(100, 120, 120))
    fixed = signals.evaluate(ind_rows((100, 99, 100, 55), (102, 100.5, 100.2, 58)))
    monkeypatch.setattr(app.market_data, "get_candles", lambda *a: candles)
    monkeypatch.setattr(app.signals, "evaluate", lambda df: dict(fixed))
    monkeypatch.setattr(app.news, "fetch_all_headlines", lambda feeds, *a: ["Bitcoin ETF inflows hit record"])
    monkeypatch.setattr(sentiment, "run_claude", lambda q, s: (
        "VERDICT: BULLISH\nREASON: Strong ETF demand\nCALL: CONFIRM\nWHY: Trend and news agree"))
    monkeypatch.setattr(C, "DAILY_SUMMARY_HOUR_UTC", 0)

    app.detect()
    app.notify(dry_run=True)
    out = capsys.readouterr().out

    st = json.loads((tmp_path / C.STATE_FILE).read_text())
    assert st["last_summary_date"] is not None
    assert "Daily summary" in out
    assert out.count("LONG SIGNAL") == 1  # coins move together: one alert, the rest listed in it
    assert "LONG SIGNAL: BTC" in out and "Also signaling LONG: ETH, SOL" in out
    assert "Confidence <b>HIGH</b>" in out
    assert "News BULLISH" in out and "Claude: ✅ CONFIRM" in out
    assert set(st["last_alert"]) == {c["symbol"] for c in C.COINS}  # none of them repeats next run
    # second run: same candle -> no repeated alert, summary already sent today
    app.detect()
    pending = json.loads((tmp_path / C.PENDING_FILE).read_text())
    assert pending["signals"] == [] and pending["summary"] is False


# ---------------- event alerts ----------------

def test_classify_events():
    from bot import events
    assert events.classify_headline("Binance will list XYZ token next week") == ("Exchange listing", "BULLISH")
    assert events.classify_headline("DeFi protocol hacked, $50M drained")[1] == "BEARISH"
    assert events.classify_headline("Coinbase to delist ABC") == ("Delisting", "BEARISH")
    assert events.classify_headline("Analyst shares weekly market thoughts") is None


def test_find_events_dedupes_and_tags_coin():
    from bot import events
    heads = ["Solana ETF approved by regulator", "Random coin hacked for $10M", "Quiet day in markets"]
    found = events.find_events(heads)
    assert [e["coin"] for e in found] == ["SOL", None]  # watched coin first
    assert events.find_events(heads, seen_ids=[e["id"] for e in found]) == []


def test_event_message_and_pipeline(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(app.market_data, "get_candles", lambda *a: make_candles(np.linspace(100, 100, 120)))
    monkeypatch.setattr(app.news, "fetch_all_headlines", lambda feeds, *a: ["Binance will list NEWCOIN"])
    monkeypatch.setattr(sentiment, "run_claude", lambda q, s: "1|YES|Binance listing announced today")
    monkeypatch.setattr(C, "DAILY_SUMMARY_HOUR_UTC", None)
    app.detect()
    app.notify(dry_run=True)
    out = capsys.readouterr().out
    assert "EVENT ALERT: Exchange listing" in out and "Claude confirms" in out
    app.detect()
    assert json.loads((tmp_path / C.PENDING_FILE).read_text())["events"] == []


def test_events_ignore_unrelated_headlines():
    from bot import events
    for h in ["Payments jump 45% as Visa, Mastercard and Stripe connect crypto to cash",
              "Tesla Roadster Rocket Is the Cool Flying Car You Can't Have",
              "Suspect in $75M crime network arrested in Bangkok",
              "Q3 hacks cost $1.18 billion, Hacken says"]:
        assert events.classify_headline(h) is None, h
    assert events.classify_headline("Bitcoin price surges to new all-time high")[1] == "BULLISH"


def test_feed_titles_are_unescaped():
    xml = "<rss><channel><item><title>Tesla &amp;#x27;Flying&amp;#x27; car</title></item></channel></rss>"
    assert news.parse_feed(xml)[0][0] == "Tesla 'Flying' car"


def test_event_trade_idea_levels():
    from bot import events
    up = events.trade_idea("BULLISH", 100.0, 2.0)
    assert up["side"] == "LONG" and up["stop"] == pytest.approx(97.0) and up["target"] == pytest.approx(100 + 3 * C.REWARD_RISK)
    down = events.trade_idea("BEARISH", 100.0, 2.0)
    assert down["side"] == "SHORT" and down["stop"] == pytest.approx(103.0) and down["target"] == pytest.approx(100 - 3 * C.REWARD_RISK)
    ev = {"event": "ETF approved", "bias": "BULLISH", "coin": "SOL", "headline": "x", "trade": up}
    text = telegram.format_event(ev)
    assert "Idea: LONG" in text and "Stop loss" in text and "Take profit" in text
    assert "not on the watch list" in telegram.format_event({**ev, "coin": None, "trade": None})


def test_swing_stop_levels():
    # LONG: swing low 98 -> distance 2 + 0.25*2 = 2.5 ATR-clamped between 2 and 4
    lv = signals.trade_levels("LONG", 100.0, 2.0, swing=98.0)
    assert lv["stop"] == pytest.approx(97.5) and lv["target"] == pytest.approx(100 + 2.5 * C.REWARD_RISK)
    # far swing (90) is capped at MAX_STOP_ATR * ATR = 4
    assert signals.trade_levels("LONG", 100.0, 2.0, swing=90.0)["stop"] == pytest.approx(96.0)
    # very close swing (99.9) is widened to MIN_STOP_ATR * ATR = 2
    assert signals.trade_levels("LONG", 100.0, 2.0, swing=99.9)["stop"] == pytest.approx(98.0)
    # SHORT mirrors it: swing high 103 -> stop 103.5, target = 100 - 3.5 x REWARD_RISK
    sh = signals.trade_levels("SHORT", 100.0, 2.0, swing=103.0)
    assert sh["stop"] == pytest.approx(103.5) and sh["target"] == pytest.approx(100 - 3.5 * C.REWARD_RISK)


def test_swing_points_from_candles():
    df = make_candles([100.0] * 30)
    low, high = signals.swing_points(df)
    assert low == pytest.approx(99.0) and high == pytest.approx(101.0)


# ---------------- macro alerts ----------------

def test_macro_candidates_filter_and_dedupe():
    from bot import macro
    heads = ["Fed signals surprise rate hike as inflation jumps", "Local bakery wins award", "Oil surges after OPEC cut"]
    found = macro.candidates(heads)
    assert [f["headline"] for f in found] == [heads[0], heads[2]]
    assert macro.candidates(heads, seen_ids=[f["id"] for f in found]) == []


def test_macro_parse_answer_ignores_bad_lines():
    from bot import macro
    text = "1|BEARISH|stocks, crypto|Rate hike hurts risk assets\ngarbage\n2|none|oil|Minor\n9|BULLISH|x|out of range"
    assert macro.parse_answer(text, 2) == {
        1: ("BEARISH", "stocks, crypto", "Rate hike hurts risk assets"),
        2: ("NONE", "oil", "Minor"),
    }


def test_macro_judge_with_fake_claude(monkeypatch):
    from bot import macro
    items = [{"id": "a", "headline": "Fed hikes rates"}, {"id": "b", "headline": "Oil dips slightly"},
             {"id": "c", "headline": "no answer for this one"}]
    monkeypatch.setattr(sentiment, "run_claude",
                        lambda q, s: "1|BEARISH|stocks,crypto|Hike hurts risk\n2|NONE|oil|Minor")
    alerts, judged = macro.judge(items)
    assert [a["id"] for a in alerts] == ["a"] and alerts[0]["bias"] == "BEARISH"
    assert judged == ["a", "b"]  # "c" was not answered -> asked again next run
    monkeypatch.setattr(sentiment, "run_claude", lambda q, s: None)
    assert macro.judge(items) == (None, [])


def test_macro_full_pipeline_dry(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(app.market_data, "get_candles", lambda *a: make_candles(np.linspace(100, 100, 120)))
    monkeypatch.setattr(app.news, "fetch_all_headlines",
                        lambda feeds, *a: ["Fed announces emergency rate cut"] if feeds == C.MACRO_FEEDS else [])
    monkeypatch.setattr(sentiment, "run_claude", lambda q, s: "1|BULLISH|stocks,crypto,gold|Surprise cut boosts risk assets")
    monkeypatch.setattr(C, "DAILY_SUMMARY_HOUR_UTC", None)
    app.detect()
    app.notify(dry_run=True)
    out = capsys.readouterr().out
    assert "MACRO ALERT" in out and "BTC LONG" in out and "ETH LONG" in out
    app.detect()  # same headline again -> already judged
    assert json.loads((tmp_path / C.PENDING_FILE).read_text())["macro"] == []


def test_parse_claude_plain_answer():
    assert sentiment.parse_claude_answer("Bullish\nLargest ETF inflow is positive.") == ("BULLISH", "Largest ETF inflow is positive.")
    assert sentiment.parse_claude_answer("**Bearish** - regulators sue")[0] == "BEARISH"
    assert sentiment.parse_claude_answer("I cannot tell") is None
    assert "VERDICT:" in sentiment.build_question("BTC", "LONG", ["x"])


def test_entry_zone_in_alerts():
    long_ = signals.trade_levels("LONG", 100.0, 2.0)
    short = signals.trade_levels("SHORT", 100.0, 2.0)
    assert (long_["entry_low"], long_["entry_high"]) == (pytest.approx(99.5), 100.0)
    assert (short["entry_low"], short["entry_high"]) == (100.0, pytest.approx(100.5))
    text = telegram.entry_line({"price": 100.0, **short})
    assert "Entry: $100.00" in text and "zone $100.00 to $100.50" in text
    assert telegram.entry_line({"price": 100.0}) == "📍 Entry: $100.00"  # no zone data


# ---------------- leverage plans ----------------

def test_leverage_ranges_get_tighter():
    from bot import leverage
    ps = {p["low"]: p for p in leverage.plans("LONG", 100.0, 1.0, 99.0, "UP")}
    assert {(p["low"], p["high"]) for p in ps.values()} == {(10, 20), (20, 40), (40, 60), (60, 100)}
    assert ps[40]["label"] == "40x-60x"
    # stops get tighter as leverage grows, and always stay inside liquidation of the HIGH end
    dists = [100.0 - ps[l]["stop"] for l in (10, 20, 40, 60)]
    assert dists == sorted(dists, reverse=True)
    for p in ps.values():
        assert p["liquidation_high"] < p["stop"] < p["price"] < p["target"]  # LONG: stop above the nearest liquidation
    assert ps[10]["method"] == "Swing stop" and ps[60]["method"] == "Micro scalp"
    # 60x-100x: liquidation at 100x is 0.5% away, stop capped at 60% of that = 0.3 (<= half an ATR -> noise warning)
    assert ps[60]["stop"] == pytest.approx(99.7) and ps[60]["warnings"]
    # 40x-60x: a 1 ATR stop (1% of price) is too wide: liquidation at 60x is 1.17% away, so the stop
    # is capped at 60% of that = 0.7 -> loses 28% of margin at 40x and 42% at 60x
    assert ps[40]["stop"] == pytest.approx(99.3)
    assert ps[40]["loss_low"] == pytest.approx(28.0) and ps[40]["loss_high"] == pytest.approx(42.0)
    assert any("shortened" in w for w in ps[40]["warnings"])


def test_leverage_high_ranges_need_trend():
    from bot import leverage
    ps = {p["low"]: p for p in leverage.plans("SHORT", 100.0, 1.0, 101.0, "SIDEWAYS")}
    assert ps[10]["skip"] is None            # swing range works in any trend
    assert all(ps[l]["skip"] for l in (20, 40, 60))
    assert leverage.plan("LONG", 100.0, 1.0, None, "UP", 300, 500)["skip"]   # liquidation inside maintenance margin


def test_leverage_message_and_pipeline(tmp_path, monkeypatch, capsys):
    from bot import leverage
    text = telegram.format_plans(leverage.plans("SHORT", 100.0, 1.0, 101.0, "DOWN"))
    assert "40x-60x" in text and "60x-100x" in text and "gain" in text and "🛑" in text
    monkeypatch.chdir(tmp_path)
    candles = make_candles(np.linspace(100, 120, 120))
    fixed = signals.evaluate(ind_rows((100, 99, 100, 55), (102, 100.5, 100.2, 58)))
    monkeypatch.setattr(app.market_data, "get_candles", lambda *a: candles)
    monkeypatch.setattr(app.signals, "evaluate", lambda df: dict(fixed))
    monkeypatch.setattr(app.news, "fetch_all_headlines", lambda feeds, *a: ["Bitcoin ETF inflows hit record"])
    monkeypatch.setattr(sentiment, "run_claude", lambda q, s: (
        "VERDICT: BULLISH\nREASON: Strong ETF demand\nCALL: CONFIRM\nWHY: Trend and news agree"))
    monkeypatch.setattr(C, "DAILY_SUMMARY_HOUR_UTC", None)
    app.detect()
    app.notify(dry_run=True)
    out = capsys.readouterr().out
    assert "10x-20x" in out and "60x-100x" in out and "PLAN:" not in out  # all plans, inside the signal message
    assert out.count("TELEGRAM MESSAGE") == 1  # one signal per run, nothing else


def test_more_coins_and_price_format():
    from bot import market_data
    assert len({c["symbol"] for c in C.COINS}) == len(C.COINS)  # no duplicates
    assert all(c["symbol"] in market_data.KRAKEN_PAIRS for c in C.COINS)  # every coin has a Kraken backup
    assert telegram.fmt_price(81103.254) == "$81,103.25"
    assert telegram.fmt_price(12.34567) == "$12.3457"
    assert telegram.fmt_price(0.077923) == "$0.077923"


def test_macro_ignores_generic_stock_news(monkeypatch):
    from bot import macro
    generic = ["Top Wall Street analysts are bullish on the growth potential of these 3 stocks",
               "Here are the 3 big things we're watching in the stock market this week"]
    assert macro.candidates(generic) == []
    assert macro.candidates(["Fed announces emergency rate cut"])  # real events still pass
    # Claude says BULLISH but names no affected market -> not an alert
    monkeypatch.setattr(sentiment, "run_claude", lambda q, s: "1|BULLISH|none|Analyst upgrades rarely move markets")
    alerts, judged = macro.judge([{"id": "a", "headline": "Oil analysts upbeat"}])
    assert alerts == [] and judged == ["a"]


def test_long_on_momentum_resume_in_uptrend():
    # uptrend, RSI crosses up through 50 (not through 40): extra setup
    sig = signals.evaluate(ind_rows((105, 104, 100, 48), (106, 104.5, 100.5, 52)))
    assert sig["side"] == "LONG"
    assert "momentum" in sig["reason"]


def test_short_on_momentum_loss_in_downtrend():
    sig = signals.evaluate(ind_rows((95, 96, 100, 52), (94, 95.5, 99.5, 48)))
    assert sig["side"] == "SHORT"


# ---------------- result tracking ----------------

from bot import tracker


def _rec(side="LONG"):
    coin = {"name": "BTC", "symbol": "BTCUSDT"}
    sig = {"side": side, "price": 100.0, "candle_time": "2026-01-01T00:00:00+00:00",
           "stop": 98.0 if side == "LONG" else 102.0, "target": 104.0 if side == "LONG" else 96.0}
    h = {"signals": []}
    tracker.record(h, coin, sig, tf="1h")  # the helpers below use 1h candles
    tracker.record(h, coin, sig, tf="1h")  # same candle twice -> one record
    assert len(h["signals"]) == 1
    return h["signals"][0]


def _bars(*hl):
    """Candles after the signal candle: (high, low) pairs, closing at the midpoint."""
    times = pd.date_range("2026-01-01 01:00", periods=len(hl), freq="1h", tz="UTC")
    return pd.DataFrame({"time": times, "high": [h for h, _ in hl], "low": [lo for _, lo in hl],
                         "close": [(h + lo) / 2 for h, lo in hl]})


NOW = pd.Timestamp("2026-01-01 03:00", tz="UTC").to_pydatetime()


def test_long_win_when_target_hits_first():
    r = _rec()
    assert tracker.resolve(r, _bars((101, 99.5), (104.5, 100)), NOW)
    assert r["status"] == "WIN" and r["result_r"] == 2.0
    assert r["move_1h"] == 0.25


def test_short_loss_when_stop_hits_first():
    r = _rec("SHORT")
    assert tracker.resolve(r, _bars((102.5, 99)), NOW)
    assert r["status"] == "LOSS" and r["result_r"] == -1.0


def test_same_candle_both_levels_is_a_loss():
    r = _rec()
    tracker.resolve(r, _bars((105, 97)), NOW)
    assert r["status"] == "LOSS"


def test_open_then_expires():
    r = _rec()
    assert not tracker.resolve(r, _bars((101, 99.5)), NOW)
    assert r["status"] == "OPEN"
    later = NOW + pd.Timedelta(hours=C.RESULT_EXPIRE_HOURS).to_pytimedelta()
    assert tracker.resolve(r, _bars((101, 99.5)), later)
    assert r["status"] == "EXPIRED" and r["result_r"] is None


def test_stats_and_message():
    h = {"signals": [dict(_rec(), status="WIN", result_r=2.0, closed_at="2026-01-01T03:00:00+00:00"),
                     dict(_rec("SHORT"), status="LOSS", result_r=-1.0, closed_at="2026-01-01T04:00:00+00:00"),
                     dict(_rec(), status="OPEN")]}
    s = tracker.stats(h, NOW)
    assert s["all"]["win_rate"] == 50 and s["all"]["avg_r"] == pytest.approx(0.5)
    text = telegram.format_stats(s, {"BTC": 101.0})
    assert "50%" in text and "(+1.00%)" in text and "Only 2 finished" in text
    assert "WIN" in telegram.format_result(h["signals"][0])
    assert s["all"]["avg_hours"] == pytest.approx(2.5)  # signal at 01:00, WIN at 03:00 / LOSS at 04:00 -> 2h and 3h
    assert "2.5h per trade" in text
    assert "No signals yet" in telegram.format_stats(tracker.stats({"signals": []}), {})


def test_get_commands_only_from_my_chat(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    updates = [{"update_id": 7, "message": {"chat": {"id": 42}, "text": "/stats"}},
               {"update_id": 8, "message": {"chat": {"id": 99}, "text": "/stats"}},
               {"update_id": 9, "message": {"chat": {"id": 42}, "text": "hello"}}]

    class Resp:
        ok = True
        def json(self): return {"result": updates}

    monkeypatch.setattr(telegram.requests, "get", lambda *a, **k: Resp())
    assert telegram.get_commands(None) == (["stats"], 10)
    assert "state" in telegram.STATS_COMMANDS


def test_signal_pipeline_records_and_stats_command(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    candles = make_candles(np.linspace(100, 120, 120))
    fixed = signals.evaluate(ind_rows((100, 99, 100, 55), (102, 100.5, 100.2, 58)))
    monkeypatch.setattr(app.market_data, "get_candles", lambda *a: candles)
    monkeypatch.setattr(app.signals, "evaluate", lambda df: dict(fixed))
    monkeypatch.setattr(app.news, "fetch_all_headlines", lambda feeds, *a: [])
    monkeypatch.setattr(sentiment, "ask_claude", lambda q: None)
    monkeypatch.setattr(app.telegram, "get_commands", lambda offset: (["stats", "other"], 5))
    monkeypatch.setattr(app.telegram, "send", lambda text, dry=False, markup=None: print(text) or True)

    app.detect()
    app.notify()
    out = capsys.readouterr().out
    hist = json.loads((tmp_path / C.HISTORY_FILE).read_text())
    assert len(hist["signals"]) == len(C.COINS)
    assert "Bot results" in out and out.count("Bot results") == 1
    assert json.loads((tmp_path / C.STATE_FILE).read_text())["tg_offset"] == 5


def test_watch_answers_stats_at_once(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(app, "detect", lambda: {"BTC": {"price": 100.0}})
    monkeypatch.setattr(app, "notify", lambda dry_run=False: None)
    monkeypatch.setattr(app.telegram, "get_commands", lambda offset, wait=0: (["state"], 7))
    monkeypatch.setattr(app.telegram, "send", lambda text, dry=False, markup=None: print(text) or True)
    monkeypatch.setattr(app.time, "sleep", lambda s: (_ for _ in ()).throw(KeyboardInterrupt))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    with pytest.raises(KeyboardInterrupt):
        app.watch()
    assert "No signals yet" in capsys.readouterr().out
    assert json.loads((tmp_path / C.STATE_FILE).read_text())["tg_offset"] == 7


def test_stats_uses_live_price_for_open_signals(monkeypatch, capsys):
    monkeypatch.setattr(app.market_data, "get_price", lambda symbol: 102.0)
    monkeypatch.setattr(app.telegram, "send", lambda text, dry=False, markup=None: print(text) or True)
    app.send_stats({"signals": [_rec()]}, {"BTC": 100.0})
    out = capsys.readouterr().out
    assert "(+2.00%)" in out and "By coin" not in out


# ---------------- accuracy filters and candle buttons ----------------

def test_adx_low_in_flat_market_high_in_trend():
    flat = with_ind(make_candles([100 + (i % 2) * 0.1 for i in range(120)]))
    trend = with_ind(make_candles([100 * 1.01 ** i for i in range(120)]))
    assert flat["adx"].iloc[-1] < C.ADX_MIN < trend["adx"].iloc[-1]


def test_no_signal_when_adx_is_low():
    df = ind_rows((100, 99, 100, 55), (102, 100.5, 100.2, 58))
    assert signals.evaluate(df)["side"] == "LONG"
    df["adx"] = C.ADX_MIN - 5
    sig = signals.evaluate(df)
    assert sig["side"] == "WAIT" and "Sideways" in sig["reason"]


def test_bigger_candle_trend_blocks_counter_trend_signal():
    sig = signals.evaluate(ind_rows((100, 99, 100, 55), (102, 100.5, 100.2, 58)))
    assert signals.apply_htf(sig, "UP")["side"] == "LONG"
    assert signals.apply_htf(sig, "SIDEWAYS")["side"] == "WAIT"  # strict: only WITH the 1h trend
    blocked = signals.apply_htf(sig, "DOWN")
    assert blocked["side"] == "WAIT" and blocked["stop"] is None


def test_bigger_candle_sideways_allowed_when_not_strict(monkeypatch):
    monkeypatch.setattr(C, "HTF_STRICT", False)
    sig = signals.evaluate(ind_rows((100, 99, 100, 55), (102, 100.5, 100.2, 58)))
    assert signals.apply_htf(sig, "SIDEWAYS")["side"] == "LONG"
    assert signals.apply_htf(sig, "DOWN")["side"] == "WAIT"


def test_flip_needs_a_longer_pause():
    st = {"last_alert": {"BTCUSDT": {"side": "LONG", "candle_time": "2026-01-01T00:00:00+00:00"}}}
    assert not state.is_new_signal(st, "BTCUSDT", "SHORT", "2026-01-01T00:30:00+00:00")  # 2 x 15m
    assert state.is_new_signal(st, "BTCUSDT", "SHORT", "2026-01-01T00:45:00+00:00")      # 3 x 15m


def test_coin_buttons_and_lookup_parsing(monkeypatch):
    rows = telegram.coin_keyboard("BTC")["inline_keyboard"]
    assert [b["callback_data"] for b in rows[0]] == [f"btc:{tf}" for tf in C.TIMEFRAMES]
    assert [b["text"] for r in rows[2:] for b in r] == [f"{x}x" for x in range(10, 101, 10)]
    assert rows[1][0]["text"] == "📊 BTC stats"
    assert telegram.parse_lookup("btc:15m") == ("BTC", "15m", 0)
    assert telegram.parse_lookup("btc:x40") == ("BTC", C.TIMEFRAME, 40)
    assert telegram.parse_lookup("trx:1h") == ("TRX", "1h", 0)  # any coin in ALL_COINS
    for bad in ("stats", "btc:7m", "xyz:15m", "15m", "btc", "btc:x35", "btc:x"):
        assert telegram.parse_lookup(bad) is None

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    updates = [{"update_id": 1, "callback_query": {"id": "q", "data": "sol:30m", "message": {"chat": {"id": 42}}}},
               {"update_id": 2, "callback_query": {"id": "r", "data": "btc:15m", "message": {"chat": {"id": 99}}}}]

    class Resp:
        ok = True
        def json(self): return {"result": updates}

    monkeypatch.setattr(telegram.requests, "get", lambda *a, **k: Resp())
    monkeypatch.setattr(telegram.requests, "post", lambda *a, **k: Resp())
    assert telegram.get_commands(None) == (["sol:30m"], 3)


def test_lookup_message_and_only_coin_messages_get_buttons(monkeypatch):
    sent = []
    monkeypatch.setattr(telegram.requests, "post", lambda url, json=None, **k: sent.append(json) or type("R", (), {"ok": True})())
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(app.market_data, "get_candles", lambda s, tf, n: make_candles([100 * 1.01 ** i for i in range(120)]))
    item = app.look("ETH", "15m")
    assert item["name"] == "ETH" and item["tf"] == "15m"
    app.send_lookup(item)
    assert "ETH · 15m" in sent[0]["text"] and "reply_markup" in sent[0]
    telegram.send("plain message")
    assert "reply_markup" not in sent[1]


def test_look_failure_returns_none(monkeypatch):
    def boom(*a, **k): raise RuntimeError("down")
    monkeypatch.setattr(app.market_data, "get_candles", boom)
    assert app.look("BTC", "15m") is None


def test_expiry_is_shorter_for_short_candles():
    assert tracker.expire_hours({"tf": "15m"}) < tracker.expire_hours({})


def test_events_for_one_coin_and_direction_become_one_alert():
    from bot import events
    heads = ["Bitcoin drops to $81,400 as stocks tumble", "BlackRock seeks to shield bitcoin investors after 50% crash",
             "Bitcoin lawsuit moves $33 million", "Ethereum listing on Kraken"]
    grouped = events.group(events.find_events(heads))
    btc = [g for g in grouped if g["coin"] == "BTC"]
    assert len(btc) == 1 and len(btc[0]["headlines"]) == 3 and len(btc[0]["ids"]) == 3
    text = telegram.format_event({**btc[0], "trade": None})
    assert text.count("📰") == 3


def test_lookup_shows_levels_even_without_a_setup():
    sig = signals.evaluate(ind_rows((100, 99, 100, 55), (102, 100.5, 100.2, 58)))
    sig.update(side="WAIT", reason="No clear setup", stop=None, target=None)
    text = telegram.format_lookup("BTC", "15m", sig)
    assert "WAIT" in text and "Levels if you trade it anyway" in text and "🛑" in text and "🎯" in text


def test_leverage_reply_for_exact_leverage():
    sig = signals.evaluate(ind_rows((100, 99, 100, 55), (102, 100.5, 100.2, 58)))
    text = telegram.format_leverage("BTC", "1h", 20, sig)
    assert "BTC · 20x" in text and "Stop" in text and "Target" in text and "Liquidation" in text
    assert "of margin" in text
    sig.update(side="WAIT", trend="SIDEWAYS", reason="No clear setup")  # both sides, each skipped or planned
    assert "LONG" in telegram.format_leverage("BTC", "1h", 10, sig) and "SHORT" in telegram.format_leverage("BTC", "1h", 10, sig)


def test_news_alerts_can_name_any_coin():
    from bot import events, macro
    ev = events.find_events(["Tron lists on Coinbase"])[0]
    assert ev["coin"] == "TRX"
    kb = telegram.news_keyboard(["TRX", "SOL"])["inline_keyboard"][0]
    assert [b["callback_data"] for b in kb] == [f"trx:{C.TIMEFRAME}", f"sol:{C.TIMEFRAME}"]
    text = "1|BULLISH|stocks,crypto|Rate cut boosts risk|SOL, trx, FAKE, SOL"
    assert macro.parse_coins(text, 1) == {1: ["SOL", "TRX"]}
    assert macro.parse_answer(text, 1) == {1: ("BULLISH", "stocks,crypto", "Rate cut boosts risk")}


def test_candle_button_gets_news_verdict(monkeypatch):
    monkeypatch.setattr(app.news, "fetch_all_headlines", lambda feeds: ["Solana network upgrade goes live"])
    monkeypatch.setattr(sentiment, "run_claude", lambda q, s: "VERDICT: BULLISH\nREASON: Upgrade is positive")
    sent = []
    monkeypatch.setattr(app.telegram, "send", lambda text, dry=False, markup=None: sent.append(text) or True)
    monkeypatch.setattr(app.market_data, "get_candles", lambda s, tf, n: make_candles([100 * 1.01 ** i for i in range(120)]))
    items = [app.look("SOL", "15m"), app.look("SOL", "1h", 20)]
    app.answer_lookups(items)
    assert "News BULLISH" in sent[0] and "20x" in sent[1] and "News" not in sent[1]


def test_claude_second_opinion_on_signal(monkeypatch):
    sig = {**signals.evaluate(ind_rows((100, 99, 100, 55), (102, 100.5, 100.2, 58))), "htf": "UP"}
    asked = []
    monkeypatch.setattr(sentiment, "run_claude", lambda q, s: asked.append(q) or
                        "VERDICT: NEUTRAL\nREASON: Nothing new\nCALL: CAUTION\nWHY: RSI is stretched near the target")
    out = sentiment.analyze("BTC", "LONG", [], sig)  # no headlines: Claude still judges the chart
    assert out["call"] == "CAUTION" and "RSI" in out["why"] and "bigger candle: UP" in asked[0]
    text = telegram.format_signal("BTC", sig, out)
    assert "Claude: ⚠️ CAUTION" in text
    monkeypatch.setattr(sentiment, "run_claude", lambda q, s: None)  # Claude down -> VADER, no call line
    out = sentiment.analyze("BTC", "LONG", ["Bitcoin rallies"], sig)
    assert out["source"] == "VADER backup" and "call" not in out and "Claude:" not in telegram.format_signal("BTC", sig, out)
    assert sentiment.analyze("BTC", "LONG", []) ["source"] == "none"  # old behaviour without chart data


def test_stats_show_whether_claudes_call_helps():
    rows = [dict(_rec(), status="WIN", result_r=2.0, call="CONFIRM", closed_at="2026-01-01T00:00:00+00:00"),
            dict(_rec(), status="LOSS", result_r=-1.0, call="AVOID", closed_at="2026-01-01T00:00:00+00:00")]
    st = tracker.stats({"signals": rows})
    assert st["by_call"]["CONFIRM"]["wins"] == 1 and st["by_call"]["AVOID"]["losses"] == 1
    assert "Claude's call" in telegram.format_stats(st, {})


def test_coin_stats_button_shows_only_that_coin(monkeypatch):
    assert telegram.parse_coin_stats("btc:stats") == "BTC" and telegram.parse_coin_stats("xyz:stats") is None
    assert telegram.parse_lookup("btc:stats") is None
    rows = telegram.coin_keyboard("ETH")["inline_keyboard"]
    assert any(b["callback_data"] == "eth:stats" for r in rows for b in r)
    base = dict(_rec(), closed_at="2026-01-01T00:00:00+00:00")
    history = {"signals": [dict(base, coin="BTC", status="WIN", result_r=1.5, id="a"),
                           dict(base, coin="ETH", status="LOSS", result_r=-1.0, id="b")]}
    sent = []
    monkeypatch.setattr(app.telegram, "send", lambda text, dry=False, markup=None: sent.append(text) or True)
    app.send_coin_stats(history, "BTC", {})
    assert "BTC results" in sent[0] and "1✅" in sent[0] and "0❌" in sent[0]
    app.send_coin_stats(history, "SOL", {})
    assert "No SOL signals yet" in sent[1]


# ---------------- fewer, better crypto signals (day trading) ----------------

CONFIRM_BULLISH = "VERDICT: BULLISH\nREASON: Strong ETF demand\nCALL: CONFIRM\nWHY: Trend and news agree"


def _crypto_run(tmp_path, monkeypatch, claude_answer, dry=False, history=None):
    """Full detect + notify where every coin gets the same LONG. Returns the sent messages."""
    monkeypatch.chdir(tmp_path)
    if history is not None:
        (tmp_path / C.HISTORY_FILE).write_text(json.dumps(history))
    candles = make_candles(np.linspace(100, 120, 120))
    fixed = signals.evaluate(ind_rows((100, 99, 100, 55), (102, 100.5, 100.2, 58)))
    monkeypatch.setattr(app.market_data, "get_candles", lambda *a: candles)
    monkeypatch.setattr(app.signals, "evaluate", lambda df: dict(fixed))
    monkeypatch.setattr(app.news, "fetch_all_headlines", lambda feeds, *a: ["Bitcoin ETF inflows hit record"])
    monkeypatch.setattr(sentiment, "run_claude", lambda q, s: claude_answer)
    monkeypatch.setattr(C, "DAILY_SUMMARY_HOUR_UTC", None)
    sent = []
    monkeypatch.setattr(app.telegram, "send",
                        lambda text, dry=False, markup=None, reply_to=None: sent.append(text) or 123)
    app.detect()
    app.notify(dry)
    return sent


def test_day_trading_timeframes():
    assert C.TIMEFRAME == "15m" and C.HTF_FOR["15m"] == "1h" and C.HTF_STRICT
    assert tracker.expire_hours({"tf": "15m"}) == 4
    assert C.REWARD_RISK == 1.5
    lv = signals.trade_levels("LONG", 100.0, 0.4)  # ATR of the 15m candle -> small, reachable target
    assert lv["target"] - 100.0 == pytest.approx((100.0 - lv["stop"]) * 1.5)


def test_grade_needs_trend_news_and_claude_to_agree():
    sig = {"side": "LONG", "trend": "UP", "htf": "UP"}
    claude = {"verdict": "BULLISH", "call": "CONFIRM"}
    assert signals.grade(sig, claude) == ("HIGH", None)
    assert signals.grade(sig, {**claude, "verdict": "NEUTRAL"}) == ("MEDIUM", None)
    assert signals.grade(sig, {**claude, "verdict": "BEARISH"}) == (None, "news")
    assert signals.grade(sig, {**claude, "call": "CAUTION"}) == (None, "Claude")
    assert signals.grade(sig, {"verdict": "BULLISH"}) == (None, "no Claude")  # VADER backup only
    assert signals.grade({**sig, "htf": "SIDEWAYS"}, claude) == (None, "trend")
    assert signals.grade({**sig, "trend": "SIDEWAYS"}, claude) == (None, "trend")
    assert signals.grade({"side": "SHORT", "trend": "DOWN", "htf": "DOWN"},
                         {"verdict": "BEARISH", "call": "CONFIRM"}) == ("HIGH", None)


def test_claude_confirm_against_its_own_news_becomes_caution():
    out = sentiment.reconcile({"verdict": "BEARISH", "reason": "x", "call": "CONFIRM", "why": "Looks fine"}, "LONG")
    assert out["call"] == "CAUTION" and "BEARISH" in out["why"]
    same = {"verdict": "BULLISH", "reason": "x", "call": "CONFIRM", "why": "ok"}
    assert sentiment.reconcile(same, "LONG") == same


def test_strongest_adx_leads_and_same_direction_is_grouped():
    def item(name, side, adx):
        return {"coin": {"name": name}, "signal": {"side": side, "adx": adx}}
    groups = signals.group_correlated([item("BTC", "LONG", 26), item("SOL", "LONG", 40),
                                       item("ETH", "SHORT", 30), item("ADA", "LONG", None)])
    assert [(g[0]["coin"]["name"], [o["coin"]["name"] for o in g[1]]) for g in groups] == [
        ("SOL", ["BTC", "ADA"]), ("ETH", [])]


def test_caution_sends_nothing_and_logs_quietly(tmp_path, monkeypatch):
    sent = _crypto_run(tmp_path, monkeypatch,
                       "VERDICT: BULLISH\nREASON: ok\nCALL: CAUTION\nWHY: RSI is stretched")
    assert not any("SIGNAL" in t for t in sent)
    hist = json.loads((tmp_path / C.HISTORY_FILE).read_text())["signals"]
    assert len(hist) == len(C.COINS) and all(r["skipped"] and r["announced"] for r in hist)
    assert {r["skipped"] for r in hist} == {"Claude", "also signaling"}
    st = json.loads((tmp_path / C.STATE_FILE).read_text())
    assert set(st["last_alert"]) == {c["symbol"] for c in C.COINS}  # not reviewed again next run


def test_only_high_confidence_by_default(tmp_path, monkeypatch):
    neutral = "VERDICT: NEUTRAL\nREASON: Nothing new\nCALL: CONFIRM\nWHY: Clean trend"
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert not any("SIGNAL" in t for t in _crypto_run(tmp_path / "a", monkeypatch, neutral))
    monkeypatch.setattr(C, "SEND_CONFIDENCE", ["HIGH", "MEDIUM"])
    sent = _crypto_run(tmp_path / "b", monkeypatch, neutral)
    assert sum("SIGNAL" in t for t in sent) == 1 and "Confidence <b>MEDIUM</b>" in sent[0]


def test_sent_signal_is_recorded_with_message_id(tmp_path, monkeypatch):
    sent = _crypto_run(tmp_path, monkeypatch, CONFIRM_BULLISH)
    assert sum("LONG SIGNAL" in t for t in sent) == 1
    hist = json.loads((tmp_path / C.HISTORY_FILE).read_text())["signals"]
    real = [r for r in hist if not r.get("skipped")]
    assert len(real) == 1 and real[0]["coin"] == "BTC" and real[0]["confidence"] == "HIGH"
    assert real[0]["tf"] == "15m"
    assert json.loads((tmp_path / C.STATE_FILE).read_text())["last_alert"]["BTCUSDT"]["msg_id"] == 123


def test_max_three_signals_per_day(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    done = [dict(_rec(), id=f"old{i}", status="WIN", result_r=1.5, announced=True,
                 closed_at=now, sent_at=now) for i in range(C.MAX_SIGNALS_PER_DAY)]
    assert tracker.sent_today({"signals": done}) == 3
    sent = _crypto_run(tmp_path, monkeypatch, CONFIRM_BULLISH, history={"signals": done})
    assert not any("LONG SIGNAL" in t for t in sent)
    hist = json.loads((tmp_path / C.HISTORY_FILE).read_text())["signals"]
    assert {r["skipped"] for r in hist if r["id"].startswith(("BTC", "ETH"))} == {"daily limit"}


def test_skipped_signals_stay_out_of_results_but_show_in_filters():
    closed = "2026-01-01T03:00:00+00:00"
    rows = [dict(_rec(), id="a", status="WIN", result_r=1.5, closed_at=closed),
            dict(_rec(), id="b", status="LOSS", result_r=-1.0, closed_at=closed, skipped="Claude", call="CAUTION"),
            dict(_rec(), id="c", status="LOSS", result_r=-1.0, closed_at=closed, skipped="trend")]
    st = tracker.stats({"signals": rows}, NOW)
    assert st["all"]["wins"] == 1 and st["all"]["losses"] == 0  # only the signal really sent
    assert st["skipped"]["losses"] == 2 and set(st["by_skip"]) == {"Claude", "trend"}
    text = telegram.format_stats(st, {})
    assert "Filters" in text and "Claude CAUTION / AVOID: 0✅ 1❌" in text and "per trade" in text


def test_crash_words_in_legal_or_political_headlines_are_ignored():
    from bot import events
    for h in ["Judge says Terra case crashed after witness recants",
              "Senate crypto bill collapses in late-night vote",
              "Do Kwon trial plunges into chaos"]:
        assert events.classify_headline(h) is None, h
    assert events.classify_headline("Bitcoin crashes 8% after court ruling")[0] == "Crash / liquidations"
    assert events.classify_headline("Bitcoin price plunges as liquidations hit $1B")[1] == "BEARISH"


def test_claude_confirms_events_before_they_are_sent(monkeypatch):
    from bot import events
    evs = events.find_events(["Solana crashes as network halts", "Binance will list NEWCOIN"])
    monkeypatch.setattr(sentiment, "run_claude", lambda q, s: "1|NO|Old news\n2|YES|Listing confirmed by Binance")
    ok, rejected = events.confirm(evs)
    assert [e["headline"] for e in ok] == ["Binance will list NEWCOIN"] and ok[0]["confirm"]
    assert [e["headline"] for e in rejected] == ["Solana crashes as network halts"]
    monkeypatch.setattr(sentiment, "run_claude", lambda q, s: None)
    assert events.confirm(evs) == (None, [])


def test_events_wait_for_claude(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(app.market_data, "get_candles", lambda *a: make_candles(np.linspace(100, 100, 120)))
    monkeypatch.setattr(app.news, "fetch_all_headlines", lambda feeds, *a: ["Binance will list NEWCOIN"])
    monkeypatch.setattr(C, "DAILY_SUMMARY_HOUR_UTC", None)
    monkeypatch.setattr(sentiment, "run_claude", lambda q, s: None)  # Claude down: nothing sent, retried
    app.detect()
    app.notify(dry_run=True)
    assert "EVENT ALERT" not in capsys.readouterr().out
    app.detect()
    assert json.loads((tmp_path / C.PENDING_FILE).read_text())["events"]
    monkeypatch.setattr(sentiment, "run_claude", lambda q, s: "1|NO|Rumour only")  # rejected: not asked again
    app.notify(dry_run=True)
    assert "EVENT ALERT" not in capsys.readouterr().out
    app.detect()
    assert json.loads((tmp_path / C.PENDING_FILE).read_text())["events"] == []


def test_macro_against_a_recent_signal_becomes_a_note_on_it(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.chdir(tmp_path)
    st = state.load(C.STATE_FILE)
    st["last_alert"]["BTCUSDT"] = {"side": "LONG", "candle_time": "2026-01-01T00:00:00+00:00",
                                   "sent_at": datetime.now(timezone.utc).isoformat(), "msg_id": 77}
    state.save(C.STATE_FILE, st)
    monkeypatch.setattr(app.market_data, "get_candles", lambda *a: make_candles(np.linspace(100, 100, 120)))
    monkeypatch.setattr(app.news, "fetch_all_headlines",
                        lambda feeds, *a: ["Fed hikes rates in surprise move"] if feeds == C.MACRO_FEEDS else [])
    monkeypatch.setattr(sentiment, "run_claude", lambda q, s: "1|BEARISH|stocks,crypto|Hike hurts risk|BTC,ETH")
    monkeypatch.setattr(C, "DAILY_SUMMARY_HOUR_UTC", None)
    sent = []
    monkeypatch.setattr(app.telegram, "send",
                        lambda text, dry=False, markup=None, reply_to=None: sent.append((text, reply_to)) or 5)
    app.detect()
    app.notify()
    assert not any("MACRO ALERT" in t for t, _ in sent)
    notes = [(t, r) for t, r in sent if "Macro news against this LONG: BTC" in t]
    assert len(notes) == 1 and notes[0][1] == 77
    app.detect()  # judged and handled: not asked again
    assert json.loads((tmp_path / C.PENDING_FILE).read_text())["macro"] == []
    # an old signal (or a same-direction one) does not block the alert
    old = {"side": "LONG", "candle_time": "x", "sent_at": "2020-01-01T00:00:00+00:00"}
    assert state.recent_signal({"last_alert": {"BTCUSDT": old}}, "BTCUSDT", C.MACRO_CONFLICT_HOURS) is None


def test_send_returns_message_id_and_can_reply(monkeypatch):
    posted = []

    class Resp:
        ok = True
        def json(self): return {"result": {"message_id": 42}}

    monkeypatch.setattr(telegram.requests, "post", lambda url, json=None, **k: posted.append(json) or Resp())
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    assert telegram.send("hi", reply_to=9) == 42
    assert posted[0]["reply_parameters"]["message_id"] == 9
    telegram.send("plain")
    assert "reply_parameters" not in posted[1]
