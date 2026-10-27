"""
fx_signals.py - The gold / forex day-trading strategy. Decides LONG / SHORT / WAIT on 15m candles.

Trend : follow the 1h trend only (config.FX_TREND_TF).
        1h price and EMA50 above EMA200 = LONG only. Both below = SHORT only.
        1h sideways = the 15m trend is used instead (config.FX_TREND_FALLBACK_TF), else WAIT.
Entry : the last closed 15m candle
        a) closes beyond the high (LONG) / low (SHORT) of the 12 candles before it (breakout), or
        b) touched EMA20 and closed back on the trend side of it, in the trend direction (pullback).
Filter: RSI 45-75 for LONG, 25-55 for SHORT. No signal when price is more than 2 ATR from EMA20.
Levels: stop 1.5 x the 15m ATR, take profit 1.5R (R = stop distance): reachable within a few hours.

After this, fx.py only sends the signal when news and Claude's review agree too (signals.grade).
The daily bias uses trend() on daily candles. All numbers are in config.py (section 12).
For LEARNING, not a promise of profit.
"""

from . import config as C
from . import indicators, signals


def add_indicators(df):
    """Return a copy of df with ema20, ema50, ema200, rsi, atr and adx columns."""
    df = df.copy()
    df["ema20"] = indicators.ema(df["close"], C.FX_EMA_ENTRY)
    df["ema50"] = indicators.ema(df["close"], C.FX_EMA_TREND_FAST)
    df["ema200"] = indicators.ema(df["close"], C.FX_EMA_TREND_SLOW)
    df["rsi"] = indicators.rsi(df["close"], C.RSI_PERIOD)
    df["atr"] = indicators.atr(df, C.ATR_PERIOD)
    df["adx"] = indicators.adx(df, C.ADX_PERIOD)
    return df


def trend(df):
    """UP / DOWN / SIDEWAYS from price and EMA50 vs EMA200 (SIDEWAYS when there are too few candles)."""
    if len(df) < C.FX_EMA_TREND_SLOW:
        return "SIDEWAYS"
    last = df.iloc[-1]
    if last["close"] > last["ema200"] and last["ema50"] > last["ema200"]:
        return "UP"
    if last["close"] < last["ema200"] and last["ema50"] < last["ema200"]:
        return "DOWN"
    return "SIDEWAYS"


def levels(side, price, atr):
    """Stop 1.5 ATR away, take profit 1.5R."""
    sign = 1 if side == "LONG" else -1
    risk = C.FX_STOP_ATR * atr
    return {"stop": price - sign * risk, "target": price + sign * risk * C.FX_REWARD_RISK}


def evaluate(df, trend_side, trend_tf=None):
    """df = 15m candles with indicators, trend_side = UP / DOWN / SIDEWAYS of the trend_tf candles (1h)."""
    trend_tf = trend_tf or C.FX_TREND_TF
    last = df.iloc[-1]
    window = df.iloc[-1 - C.FX_BREAKOUT_CANDLES:-1]
    close, ema20 = float(last["close"]), float(last["ema20"])
    rsi, atr = float(last["rsi"]), float(last["atr"])
    adx = float(last["adx"]) if "adx" in df and last["adx"] == last["adx"] else None
    swing_low, swing_high = signals.swing_points(df)
    out = {"side": "WAIT", "reason": "", "price": close, "rsi": round(rsi, 1), "atr": atr,
           "adx": round(adx, 1) if adx is not None else None,
           # the 1h trend is the signal's own trend here, so trend and htf are the same
           "trend": trend_side, "htf": trend_side, "trend_tf": trend_tf,
           "candle_time": last["time"].isoformat(), "swing_low": swing_low, "swing_high": swing_high,
           "stop": None, "target": None}

    side = {"UP": "LONG", "DOWN": "SHORT"}.get(trend_side)
    if side is None:
        out["reason"] = f"{trend_tf} trend unclear (price and EMA50 not on one side of EMA200)"
        return out
    long = side == "LONG"
    if long:
        breakout = close > float(window["high"].max())
        pullback = float(last["low"]) <= ema20 < close and close > float(last["open"])
    else:
        breakout = close < float(window["low"].min())
        pullback = float(last["high"]) >= ema20 > close and close < float(last["open"])
    low, high = C.FX_RSI_LONG if long else C.FX_RSI_SHORT

    if not (breakout or pullback):
        out["reason"] = (f"{trend_tf} trend {trend_side}: waiting for a {C.FX_BREAKOUT_CANDLES}-candle "
                         f"breakout or a pullback to EMA{C.FX_EMA_ENTRY}")
    elif not low <= rsi <= high:
        out["reason"] = f"{side} setup, but RSI {rsi:.0f} is outside {low}-{high}"
    elif abs(close - ema20) > C.FX_MAX_ATR_FROM_EMA * atr:
        out["reason"] = (f"{side} setup, but price is more than {C.FX_MAX_ATR_FROM_EMA:g} ATR "
                         f"from EMA{C.FX_EMA_ENTRY} (late)")
    else:
        word = "high" if long else "low"
        out["side"] = side
        out["reason"] = (f"Breakout beyond the {C.FX_BREAKOUT_CANDLES}-candle {word}" if breakout
                         else f"Pullback to EMA{C.FX_EMA_ENTRY}, closed back {'up' if long else 'down'}")
        out.update(levels(side, close, atr))
    return out


def hold(sig, event):
    """Turn a LONG / SHORT into WAIT because a big news event is close."""
    if sig["side"] == "WAIT":
        return sig
    return {**sig, "side": "WAIT", "reason": f"{sig['side']} held: {event['currency']} {event['title']} soon",
            "stop": None, "target": None}


def key_levels(daily):
    """Yesterday's high / low / close, classic pivot with R1 / S1, and the 20-day high / low."""
    last = daily.iloc[-1]
    high, low, close = float(last["high"]), float(last["low"]), float(last["close"])
    pivot = (high + low + close) / 3
    recent = daily.tail(20)
    return {"high": high, "low": low, "close": close, "pivot": pivot,
            "r1": 2 * pivot - low, "s1": 2 * pivot - high,
            "high20": float(recent["high"].max()), "low20": float(recent["low"].min())}
