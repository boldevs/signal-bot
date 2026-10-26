"""
indicators.py - Calculates EMA, RSI and ATR from candle data.

EMA = Exponential Moving Average -> shows the trend direction
RSI = Relative Strength Index   -> 0..100, above 70 "overbought", below 30 "oversold"
ATR = Average True Range        -> average candle size, used for stop loss / target
"""

import numpy as np
import pandas as pd


def wilder_smooth(series, period):
    """Wilder's smoothing (same as TradingView): start with a simple average
    of the first `period` values, then avg = (prev_avg * (period-1) + value) / period."""
    values = series.to_numpy(dtype=float)
    out = np.full(len(values), np.nan)
    valid = np.where(~np.isnan(values))[0]
    if len(valid) < period:
        return pd.Series(out, index=series.index)
    start = valid[0] + period - 1
    out[start] = values[valid[0]:start + 1].mean()
    for i in range(start + 1, len(values)):
        out[i] = (out[i - 1] * (period - 1) + values[i]) / period
    return pd.Series(out, index=series.index)


def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


def rsi(series, period=14):
    """Wilder's RSI (the standard version used by TradingView)."""
    change = series.diff()
    gain = change.clip(lower=0)
    loss = -change.clip(upper=0)
    avg_gain = wilder_smooth(gain, period)
    avg_loss = wilder_smooth(loss, period)
    rs = avg_gain / avg_loss
    out = 100 - (100 / (1 + rs))
    out = out.where(avg_loss != 0, 100.0)  # no losses at all -> RSI 100
    return out.where(avg_gain.notna())


def atr(df, period=14):
    prev_close = df["close"].shift(1)
    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    true_range.iloc[0] = df["high"].iloc[0] - df["low"].iloc[0]
    return wilder_smooth(true_range, period)


def adx(df, period=14):
    """ADX = how strong the trend is (0..100), whatever its direction. Below ~20 = sideways chop."""
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    prev_close = df["close"].shift(1)
    true_range = pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(),
                            (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    true_range.iloc[0] = df["high"].iloc[0] - df["low"].iloc[0]
    tr = wilder_smooth(true_range, period)
    plus_di = 100 * wilder_smooth(plus_dm.fillna(0.0), period) / tr
    minus_di = 100 * wilder_smooth(minus_dm.fillna(0.0), period) / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return wilder_smooth(dx, period)


def add_indicators(df, ema_fast, ema_slow, rsi_period, atr_period, adx_period=14):
    """Return a copy of df with new columns: ema_fast, ema_slow, rsi, atr, adx."""
    df = df.copy()
    df["ema_fast"] = ema(df["close"], ema_fast)
    df["ema_slow"] = ema(df["close"], ema_slow)
    df["rsi"] = rsi(df["close"], rsi_period)
    df["atr"] = atr(df, atr_period)
    df["adx"] = adx(df, adx_period)
    return df
