"""
Bybit V5 public API client for Matrix Trader.
Linear perpetuals (USDT/USDC-settled) only.
No API key required for market data.
"""
from __future__ import annotations

import sys
import time

import requests

BYBIT_BASE = "https://api.bybit.com"
_TIMEOUT   = 10

# Bybit V5 interval keys
BYBIT_INTERVAL = {
    "1m": "1",
    "3m": "3",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "1h": "60",
    "4h": "240",
    "8h": "480",
    "1d": "D",
}


def _fetch_bybit(path: str, params: dict | None = None) -> dict | None:
    """
    GET a Bybit V5 endpoint and return result dict.
    Returns None on any failure or non-zero retCode.
    """
    last_err = ""
    for attempt in range(3):
        try:
            resp = requests.get(f"{BYBIT_BASE}{path}", params=params, timeout=_TIMEOUT)
            if resp.status_code >= 500 and attempt < 2:
                last_err = f"HTTP {resp.status_code}"
                time.sleep(attempt + 1)
                continue
            resp.raise_for_status()
            body = resp.json()
            if body.get("retCode") != 0:
                return None
            return body.get("result")
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_err = str(e)
            if attempt < 2:
                time.sleep(attempt + 1)
                continue
        except Exception as e:
            print(f"[bybit_client] fetch error [{path}]: {e}", file=sys.stderr)
            return None
    print(f"[bybit_client] fetch error [{path}]: {last_err} (gave up)", file=sys.stderr)
    return None


def normalize_bybit_symbol(raw_symbol: str) -> str:
    """Convert Bybit symbol format to canonical underscore format.
    "BTCUSDT" → "BTC_USDT", "BTCUSDC" → "BTC_USDC"
    """
    if raw_symbol.endswith("USDT"):
        return raw_symbol[:-4] + "_USDT"
    if raw_symbol.endswith("USDC"):
        return raw_symbol[:-4] + "_USDC"
    return raw_symbol


def bybit_symbol_to_raw(canonical: str) -> str:
    """Reverse of normalize_bybit_symbol for API calls.
    "BTC_USDT" → "BTCUSDT"
    """
    return canonical.replace("_USDT", "USDT").replace("_USDC", "USDC")


def fetch_bybit_tickers() -> list[dict]:
    """Fetch all linear perpetual tickers. Returns [] on failure."""
    result = _fetch_bybit("/v5/market/tickers", params={"category": "linear"})
    if not result:
        return []
    return result.get("list", [])


def fetch_bybit_klines(raw_symbol: str, interval: str, limit: int) -> dict | None:
    """
    Fetch OHLCV klines for a Bybit symbol (raw format, e.g. "BTCUSDT").
    interval: "1h" | "4h" | "1d"
    Returns canonical {"open":[], "high":[], "low":[], "close":[], "volume":[]}
    or None on failure.
    Bybit returns newest-first; output is reversed to oldest-first.
    """
    iv = BYBIT_INTERVAL.get(interval, "60")
    result = _fetch_bybit(
        "/v5/market/kline",
        params={"category": "linear", "symbol": raw_symbol, "interval": iv, "limit": limit},
    )
    if not result or not result.get("list"):
        return None
    rows = list(reversed(result["list"]))  # oldest → newest
    # Row format: [timestamp, open, high, low, close, volume, turnover]
    return {
        "open":   [float(r[1]) for r in rows],
        "high":   [float(r[2]) for r in rows],
        "low":    [float(r[3]) for r in rows],
        "close":  [float(r[4]) for r in rows],
        "volume": [float(r[5]) for r in rows],
    }


def fetch_bybit_candles(raw_symbol: str, interval: str, limit: int) -> list[dict]:
    """
    Fetch timestamped OHLCV candles for charting.
    Returns rows ordered oldest-first with seconds timestamps.
    """
    iv = BYBIT_INTERVAL.get(interval, "60")
    result = _fetch_bybit(
        "/v5/market/kline",
        params={"category": "linear", "symbol": raw_symbol, "interval": iv, "limit": limit},
    )
    if not result or not result.get("list"):
        return []
    rows = list(reversed(result["list"]))
    return [
        {
            "time":   int(int(r[0]) / 1000),
            "open":   float(r[1]),
            "high":   float(r[2]),
            "low":    float(r[3]),
            "close":  float(r[4]),
            "volume": float(r[5]),
        }
        for r in rows
    ]


def fetch_bybit_depth(raw_symbol: str, limit: int = 20) -> dict | None:
    """
    Fetch order book snapshot.
    Returns {"bids": [[price, qty], ...], "asks": [[price, qty], ...]} or None.
    """
    result = _fetch_bybit(
        "/v5/market/orderbook",
        params={"category": "linear", "symbol": raw_symbol, "limit": limit},
    )
    if not result:
        return None
    return {
        "bids": [[float(b[0]), float(b[1])] for b in result.get("b", [])],
        "asks": [[float(a[0]), float(a[1])] for a in result.get("a", [])],
    }


def fetch_bybit_next_funding_minutes(raw_symbol: str) -> int | None:
    """Returns minutes until next funding settlement, or None."""
    result = _fetch_bybit("/v5/market/tickers", params={"category": "linear", "symbol": raw_symbol})
    if not result:
        return None
    rows = result.get("list", [])
    if not rows:
        return None
    next_ft = rows[0].get("nextFundingTime")
    if not next_ft:
        return None
    try:
        minutes_left = (int(next_ft) / 1000 - time.time()) / 60
        return max(0, int(round(minutes_left)))
    except Exception:
        return None
