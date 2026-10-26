"""
market_data.py - Downloads candle (price) data. Free, no API key needed.

Main source : Binance public market data (data-api.binance.vision)
Backup      : Kraken public API (used if Binance fails)

We only keep CLOSED candles. The newest candle is still moving, and using it
would make signals appear and disappear ("repainting").
"""

import time

import pandas as pd
import requests

BINANCE_URL = "https://data-api.binance.vision/api/v3/klines"
KRAKEN_URL = "https://api.kraken.com/0/public/OHLC"

# Kraken uses different names and minutes for the interval
KRAKEN_PAIRS = {
    "BTCUSDT": "XBTUSD", "ETHUSDT": "ETHUSD", "SOLUSDT": "SOLUSD", "XRPUSDT": "XRPUSD",
    "BNBUSDT": "BNBUSD", "DOGEUSDT": "DOGEUSD", "ADAUSDT": "ADAUSD", "AVAXUSDT": "AVAXUSD",
    "LINKUSDT": "LINKUSD",
}
INTERVAL_MINUTES = {"15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}

HEADERS = {"User-Agent": "crypto-signal-bot/1.0"}


def _from_binance(symbol, timeframe, limit):
    params = {"symbol": symbol, "interval": timeframe, "limit": limit}
    resp = requests.get(BINANCE_URL, params=params, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    rows = resp.json()

    now_ms = int(time.time() * 1000)
    rows = [r for r in rows if int(r[6]) < now_ms]  # r[6] = close time -> keep closed only

    df = pd.DataFrame(rows).iloc[:, :6]
    df.columns = ["time", "open", "high", "low", "close", "volume"]
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    return df


def _from_kraken(symbol, timeframe, limit):
    pair = KRAKEN_PAIRS.get(symbol) or (symbol[:-4] + "USD" if symbol.endswith("USDT") else None)
    if not pair:
        raise RuntimeError(f"no Kraken pair known for {symbol} (add it to KRAKEN_PAIRS)")
    params = {"pair": pair, "interval": INTERVAL_MINUTES[timeframe]}
    resp = requests.get(KRAKEN_URL, params=params, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if data.get("error"):
        raise RuntimeError(f"Kraken error: {data['error']}")

    key = next(k for k in data["result"] if k != "last")
    rows = data["result"][key][:-1]  # last row is the candle still forming

    df = pd.DataFrame(rows).iloc[:, [0, 1, 2, 3, 4, 6]]
    df.columns = ["time", "open", "high", "low", "close", "volume"]
    df["time"] = pd.to_datetime(df["time"].astype(int), unit="s", utc=True)
    return df.tail(limit)


def get_price(symbol):
    """Live price right now (not the last closed candle). Binance first, Kraken as backup."""
    try:
        resp = requests.get(BINANCE_URL.replace("klines", "ticker/price"), params={"symbol": symbol},
                            headers=HEADERS, timeout=20)
        resp.raise_for_status()
        return float(resp.json()["price"])
    except Exception:
        pair = KRAKEN_PAIRS.get(symbol) or symbol[:-4] + "USD"
        resp = requests.get(KRAKEN_URL.replace("OHLC", "Ticker"), params={"pair": pair},
                            headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            raise RuntimeError(f"Kraken error: {data['error']}")
        return float(next(iter(data["result"].values()))["c"][0])


def get_candles(symbol, timeframe, limit):
    """Return a table of closed candles: time, open, high, low, close, volume."""
    errors = []
    for source in (_from_binance, _from_kraken):
        try:
            df = source(symbol, timeframe, limit)
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = df[col].astype(float)
            if len(df) < 60:
                raise RuntimeError(f"only {len(df)} candles returned")
            return df.reset_index(drop=True)
        except Exception as exc:  # try the next source
            errors.append(f"{source.__name__}: {exc}")
    raise RuntimeError(f"Could not download {symbol} prices -> " + " | ".join(errors))
