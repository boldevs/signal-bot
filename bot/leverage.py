"""
leverage.py - A separate plan for each leverage tier (10x, 20x, 40x ... 100x).

The higher the leverage, the closer the liquidation price, so the stop loss
must be tighter and the entry rules stricter:

    10x-20x   Swing stop             stop beyond the recent swing point (1-2 ATR), RR 2:1
    20x-40x   Trend-confirmed swing  same, but 1-1.5 ATR and only WITH the trend
    40x-60x   Tight ATR stop         stop 1 ATR, RR 1.5:1, only WITH the trend
    60x-100x  Micro scalp            stop 0.5 ATR, RR 1.5:1, only WITH the trend

Each plan covers a leverage RANGE (config.LEVERAGE_RANGES). The method is picked by
the low end of the range. The stop is kept inside STOP_SAFETY of the distance to
liquidation at the HIGH end, the nearest one, so it is safe anywhere in the range.
Liquidation is approximate: 1 / leverage minus MAINTENANCE_MARGIN, fees ignored.
These are plans for you to judge. The bot never trades.
"""

from . import config as C

# (max leverage of the range's LOW end, method name, uses swing point, min ATR, max ATR, reward/risk, needs trend)
TIERS = [
    (10, "Swing stop", True, 1.0, 2.0, 2.0, False),
    (20, "Trend-confirmed swing", True, 1.0, 1.5, 2.0, True),
    (40, "Tight ATR stop", False, 1.0, 1.0, 1.5, True),
    (10_000, "Micro scalp", False, 0.5, 0.5, 1.5, True),
]


def _tier(leverage):
    return next(t for t in TIERS if leverage <= t[0])


def plan(side, price, atr, swing, trend, low, high):
    """One plan dict for the leverage range low..high. plan['skip'] holds the reason if it should not be sent."""
    _, method, use_swing, min_atr, max_atr, rr, need_trend = _tier(low)
    sign = 1 if side == "LONG" else -1
    liq_low = 1 / low - C.MAINTENANCE_MARGIN    # farthest liquidation (lowest leverage)
    liq_high = 1 / high - C.MAINTENANCE_MARGIN  # nearest liquidation (highest leverage)
    out = {"low": low, "high": high, "label": f"{low}x-{high}x", "method": method,
           "side": side, "skip": None, "warnings": []}

    if liq_high <= 0:
        out["skip"] = "leverage too high: liquidation is closer than the maintenance margin"
        return out
    if need_trend and trend != ("UP" if side == "LONG" else "DOWN"):
        out["skip"] = f"{method} needs the trend to agree with the {side}, but trend is {trend}"
        return out

    if use_swing and swing is not None:
        dist = abs(price - swing) + C.SWING_BUFFER_ATR * atr
        dist = min(max(dist, min_atr * atr), max_atr * atr)
    else:
        dist = min_atr * atr if not use_swing else C.STOP_ATR * atr

    cap = C.STOP_SAFETY * liq_high * price
    if dist > cap:
        dist = cap
        out["warnings"].append("Stop shortened to stay safely inside liquidation")
    if dist <= 0.5 * atr:
        out["warnings"].append("Stop is smaller than normal price noise (half a candle): "
                               "it will probably be hit")

    zone = C.ENTRY_ZONE_ATR * atr
    out.update({
        "price": price,
        "entry_low": price - zone if side == "LONG" else price,
        "entry_high": price if side == "LONG" else price + zone,
        "stop": price - sign * dist,
        "target": price + sign * dist * rr,
        "liquidation_low": price * (1 - sign * liq_low),    # at the low end of the range
        "liquidation_high": price * (1 - sign * liq_high),  # at the high end of the range
        "loss_low": dist / price * low * 100, "loss_high": dist / price * high * 100,
        "gain_low": dist * rr / price * low * 100, "gain_high": dist * rr / price * high * 100,
        "stop_pct": dist / price * 100,
        "target_pct": dist * rr / price * 100,
    })
    return out


def plans(side, price, atr, swing, trend, ranges=None):
    """A plan for every range in config.LEVERAGE_RANGES (skipped ones have plan['skip'] set)."""
    return [plan(side, price, atr, swing, trend, lo, hi) for lo, hi in (ranges or C.LEVERAGE_RANGES)]
