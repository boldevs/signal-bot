"""
signals.py - The strategy. Decides LONG / SHORT / WAIT from the indicators.

Rules (checked on the LAST CLOSED candle, config.TIMEFRAME):

LONG  when RSI is below 70 (not overbought) AND one of these happens:
      a) EMA20 crosses ABOVE EMA50 (trend just turned up), or
      b) the trend is already up (EMA20 > EMA50 and price > EMA50)
         and RSI crosses UP through 40 (a dip is ending), or
      c) the trend is already up and RSI crosses UP through 50 (momentum
         is back), or price closes back above EMA20 (pullback is over)

SHORT when RSI is above 30 (not oversold) AND one of these happens:
      a) EMA20 crosses BELOW EMA50 (trend just turned down), or
      b) the trend is already down (EMA20 < EMA50 and price < EMA50)
         and RSI crosses DOWN through 60 (a bounce is ending), or
      c) the trend is already down and RSI crosses DOWN through 50, or
         price closes back below EMA20 (bounce is over)

Otherwise -> WAIT

Filters: no signal while ADX < config.ADX_MIN (sideways chop), and (in the main program)
none against the trend of the bigger candle (config.HTF_FOR).

This is a simple, well-known starting strategy for LEARNING.
It is not a promise of profit. Backtest and paper trade before trusting it.
"""

from . import config as C


def swing_points(df):
    """Lowest low and highest high of the last SWING_LOOKBACK closed candles (or None, None)."""
    if "low" not in df or "high" not in df:
        return None, None
    recent = df.tail(C.SWING_LOOKBACK)
    return float(recent["low"].min()), float(recent["high"].max())


def trade_levels(side, price, atr, swing=None):
    """Stop loss, take profit and liquidation price for a LONG or SHORT.

    swing = recent swing low (for LONG) or swing high (for SHORT). The stop goes just
    beyond it, kept between MIN_STOP_ATR and MAX_STOP_ATR. Without it, STOP_ATR is used.
    """
    sign = 1 if side == "LONG" else -1
    if swing is None:
        dist = C.STOP_ATR * atr
    else:
        dist = abs(price - swing) + C.SWING_BUFFER_ATR * atr
        dist = min(max(dist, C.MIN_STOP_ATR * atr), C.MAX_STOP_ATR * atr)
    zone = C.ENTRY_ZONE_ATR * atr
    return {
        # entry = the current price; the zone is a slightly better price to wait for
        "entry_low": price - zone if side == "LONG" else price,
        "entry_high": price if side == "LONG" else price + zone,
        "stop": price - sign * dist,
        "target": price + sign * dist * C.REWARD_RISK,
        "liquidation": price * (1 - sign / C.LEVERAGE),
    }


def evaluate(df):
    """df must already have indicator columns. Returns a dict describing the signal."""
    last = df.iloc[-1]
    prev = df.iloc[-2]

    price = float(last["close"])
    rsi_now, rsi_prev = float(last["rsi"]), float(prev["rsi"])
    atr_now = float(last["atr"])

    trend_up = last["ema_fast"] > last["ema_slow"] and price > last["ema_slow"]
    trend_down = last["ema_fast"] < last["ema_slow"] and price < last["ema_slow"]
    cross_up = prev["ema_fast"] <= prev["ema_slow"] and last["ema_fast"] > last["ema_slow"]
    cross_down = prev["ema_fast"] >= prev["ema_slow"] and last["ema_fast"] < last["ema_slow"]
    rsi_up_through = rsi_prev < C.RSI_PULLBACK_LONG <= rsi_now
    rsi_down_through = rsi_prev > C.RSI_PULLBACK_SHORT >= rsi_now
    # extra, more frequent setups inside an existing trend (see config.EXTRA_SETUPS)
    mid = C.RSI_MOMENTUM
    prev_close = float(prev["close"])
    momentum_up = C.EXTRA_SETUPS and (
        rsi_prev < mid <= rsi_now or (prev_close <= prev["ema_fast"] and price > last["ema_fast"]))
    momentum_down = C.EXTRA_SETUPS and (
        rsi_prev > mid >= rsi_now or (prev_close >= prev["ema_fast"] and price < last["ema_fast"]))

    side = "WAIT"
    reason = "No clear setup"
    adx_now = float(last["adx"]) if "adx" in df and last["adx"] == last["adx"] else None
    choppy = adx_now is not None and adx_now < C.ADX_MIN  # no real trend: every setup is noise

    if choppy:
        reason = f"Sideways market (ADX {adx_now:.0f} < {C.ADX_MIN})"
    elif rsi_now < C.RSI_OVERBOUGHT and (cross_up or (trend_up and (rsi_up_through or momentum_up))):
        side = "LONG"
        reason = (
            f"EMA{C.EMA_FAST} crossed above EMA{C.EMA_SLOW}"
            if cross_up
            else f"Up-trend + RSI recovered above {C.RSI_PULLBACK_LONG}" if rsi_up_through
            else f"Up-trend + momentum back (RSI {rsi_now:.0f} / price above EMA{C.EMA_FAST})"
        )
    elif rsi_now > C.RSI_OVERSOLD and (cross_down or (trend_down and (rsi_down_through or momentum_down))):
        side = "SHORT"
        reason = (
            f"EMA{C.EMA_FAST} crossed below EMA{C.EMA_SLOW}"
            if cross_down
            else f"Down-trend + RSI fell below {C.RSI_PULLBACK_SHORT}" if rsi_down_through
            else f"Down-trend + momentum gone (RSI {rsi_now:.0f} / price below EMA{C.EMA_FAST})"
        )

    if trend_up:
        trend = "UP"
    elif trend_down:
        trend = "DOWN"
    else:
        trend = "SIDEWAYS"

    result = {
        "side": side,
        "reason": reason,
        "price": price,
        "rsi": round(rsi_now, 1),
        "atr": atr_now,
        "trend": trend,
        "adx": round(adx_now, 1) if adx_now is not None else None,
        "ema_gap_pct": round((float(last["ema_fast"]) - float(last["ema_slow"])) / float(last["ema_slow"]) * 100, 2),
        "from_ema20_pct": round((price - float(last["ema_fast"])) / float(last["ema_fast"]) * 100, 2),
        "change_6_pct": round((price / float(df["close"].iloc[-7]) - 1) * 100, 2) if len(df) > 7 else None,
        "atr_pct": round(atr_now / price * 100, 2),
        "candle_time": last["time"].isoformat(),
        "stop": None,
        "target": None,
        "liquidation": None,
    }

    swing_low, swing_high = swing_points(df)
    result["swing_low"], result["swing_high"] = swing_low, swing_high

    if side in ("LONG", "SHORT"):
        result.update(trade_levels(side, price, atr_now, swing_low if side == "LONG" else swing_high))

    return result


def trend_of(df):
    """UP / DOWN / SIDEWAYS from the last closed candle of a table with indicators."""
    last = df.iloc[-1]
    if last["ema_fast"] > last["ema_slow"] and last["close"] > last["ema_slow"]:
        return "UP"
    if last["ema_fast"] < last["ema_slow"] and last["close"] < last["ema_slow"]:
        return "DOWN"
    return "SIDEWAYS"


def apply_htf(sig, htf_trend):
    """Turn a signal into WAIT when it goes against the trend of the bigger candle.

    With config.HTF_STRICT the bigger trend must point the signal's way (SIDEWAYS blocks too)."""
    if C.HTF_STRICT and sig["side"] in TREND_FOR:
        against = htf_trend != TREND_FOR[sig["side"]]
    else:
        against = (sig["side"] == "LONG" and htf_trend == "DOWN") or (sig["side"] == "SHORT" and htf_trend == "UP")
    if not against:
        return sig
    wait = dict(sig)
    wait.update(side="WAIT", reason=f"{sig['side']} blocked: bigger candle trend is {htf_trend}",
                stop=None, target=None, liquidation=None)
    return wait



TREND_FOR = {"LONG": "UP", "SHORT": "DOWN"}
NEWS_FOR = {"LONG": "BULLISH", "SHORT": "BEARISH"}


def trend_agrees(sig):
    """True when the coin's trend AND the bigger candle's trend (if known) point the signal's way.

    Checked before Claude, it is free."""
    want = TREND_FOR[sig["side"]]
    return sig["trend"] == want and sig.get("htf", want) == want


def grade(sig, news):
    """Confidence of a signal after the news and Claude's review: (HIGH / MEDIUM, None) or (None, skip reason).

    Sent only when trend, news and Claude all agree: HIGH = the news agrees too, MEDIUM = news neutral.
    Claude CAUTION / AVOID, news against the side, or no Claude review at all = no signal."""
    side = sig["side"]
    if not trend_agrees(sig):
        return None, "trend"
    if not news.get("call"):
        return None, "no Claude"
    if news["call"] != "CONFIRM":
        return None, "Claude"
    if news["verdict"] == NEWS_FOR["SHORT" if side == "LONG" else "LONG"]:
        return None, "news"
    return ("HIGH" if news["verdict"] == NEWS_FOR[side] else "MEDIUM"), None


def group_correlated(items):
    """Coins signalling the same direction move together, so they become one alert.

    items: [{"coin", "signal"}]. Returns [(leader item, [other items])], one per direction, the
    strongest group first. The leader is the coin with the strongest trend (highest ADX)."""
    def strength(it):
        return -(it["signal"].get("adx") or 0)

    by_side = {}
    for it in sorted(items, key=strength):
        by_side.setdefault(it["signal"]["side"], []).append(it)
    return [(members[0], members[1:]) for members in by_side.values()]  # dicts keep insertion order
