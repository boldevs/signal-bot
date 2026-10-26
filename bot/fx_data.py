"""
fx_data.py - Candles for gold and forex (XAU/USD, EUR/USD, GBP/USD, USD/JPY).

Main source : Twelve Data (free tier, needs TWELVEDATA_API_KEY)
Backup      : Yahoo Finance (no key; gold comes from the COMEX future GC=F, a few dollars off spot)

The free Twelve Data plan allows 8 requests a minute and 800 a day. So the bot downloads only
15m candles (one request per pair, and only after a new candle has closed) and builds the 1h and
4h candles from them. Daily candles cost one more request per pair per day.

Like market_data.py, only CLOSED candles are kept.
"""

import os
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

from . import config as C

TWELVE_URL = "https://api.twelvedata.com/time_series"
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{}"
HEADERS = {"User-Agent": "Mozilla/5.0 (signal-bot)"}  # Yahoo refuses requests without a browser-like agent
MINUTES = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440}
TWELVE_INTERVAL = {"15m": "15min", "1d": "1day"}
YAHOO_RANGE = {"15m": ("15m", "60d"), "1d": ("1d", "2y")}  # Yahoo keeps only 60 days of 15m candles
OHLC = ["open", "high", "low", "close"]

_cache = {}  # (symbol, tf) -> candle table, downloaded again only after a newer candle has closed


def market_open(now=None):
    """Forex is closed from Friday 22:00 UTC to Sunday 21:00 UTC (config FX_CLOSE / FX_OPEN hours)."""
    now = now or datetime.now(timezone.utc)
    day = now.weekday()  # Monday = 0
    if day == 5:
        return False
    if day == 4 and now.hour >= C.FX_CLOSE_HOUR_UTC:
        return False
    if day == 6 and now.hour < C.FX_OPEN_HOUR_UTC:
        return False
    return True


def _from_twelve(pair, tf):
    key = os.environ.get("TWELVEDATA_API_KEY", "").strip()
    if not key:
        raise RuntimeError("TWELVEDATA_API_KEY not set")
    params = {"symbol": pair["symbol"], "interval": TWELVE_INTERVAL[tf], "outputsize": C.FX_CANDLES[tf],
              "timezone": "UTC"}
    # the key goes in a header: errors print the URL, and the key must never show up in a log
    resp = requests.get(TWELVE_URL, params=params, timeout=30,
                        headers={**HEADERS, "Authorization": f"apikey {key}"})
    if resp.status_code != 200:
        raise RuntimeError(f"Twelve Data HTTP {resp.status_code}")
    data = resp.json()
    if data.get("status") != "ok":
        raise RuntimeError(f"Twelve Data error: {str(data.get('message', ''))[:200]}")
    df = pd.DataFrame(data["values"])
    df["time"] = pd.to_datetime(df["datetime"], utc=True)
    return df[["time"] + OHLC]


def _from_yahoo(pair, tf):
    interval, period = YAHOO_RANGE[tf]
    resp = requests.get(YAHOO_URL.format(pair["yahoo"]), params={"interval": interval, "range": period},
                        headers=HEADERS, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"Yahoo HTTP {resp.status_code}")
    result = resp.json()["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    df = pd.DataFrame({"time": pd.to_datetime(result["timestamp"], unit="s", utc=True),
                       **{col: quote[col] for col in OHLC}})
    return df.dropna()


def _closed(df, tf, now):
    return df[df["time"] + pd.Timedelta(minutes=MINUTES[tf]) <= pd.Timestamp(now)]


def get_candles(pair, tf, now=None):
    """Closed candles (time, open, high, low, close). 15m and 1d are downloaded, 1h and 4h built from 15m."""
    now = now or datetime.now(timezone.utc)
    if tf in ("1h", "4h"):
        return resample(get_candles(pair, "15m", now), tf)
    key = (pair["symbol"], tf)
    cached = _cache.get(key)
    step = pd.Timedelta(minutes=MINUTES[tf])
    # the next candle closes 2 steps after the last one opened; 1 extra minute for the provider
    if cached is not None and pd.Timestamp(now) < cached["time"].iloc[-1] + 2 * step + timedelta(minutes=1):
        return cached
    errors = []
    for source in (_from_twelve, _from_yahoo):
        try:
            df = source(pair, tf)
            for col in OHLC:
                df[col] = df[col].astype(float)
            df = _closed(df.sort_values("time").drop_duplicates("time"), tf, now).reset_index(drop=True)
            if len(df) < C.FX_MIN_CANDLES[tf]:
                raise RuntimeError(f"only {len(df)} candles returned")
            _cache[key] = df
            return df
        except Exception as exc:  # try the next source
            errors.append(f"{source.__name__}: {exc}")
    raise RuntimeError(f"Could not download {pair['symbol']} {tf} prices -> " + " | ".join(errors))


def resample(df, tf):
    """Build bigger candles (1h, 4h) from 15m ones. The last one is dropped until it is complete."""
    out = (df.set_index("time").resample(f"{MINUTES[tf]}min", label="left", closed="left")
           .agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna().reset_index())
    last_close = df["time"].iloc[-1] + pd.Timedelta(minutes=15)
    return out[out["time"] + pd.Timedelta(minutes=MINUTES[tf]) <= last_close].reset_index(drop=True)


def get_price(pair, now=None):
    """Latest price = close of the last 15m candle (no extra download while it is fresh)."""
    return float(get_candles(pair, "15m", now)["close"].iloc[-1])
