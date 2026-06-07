"""
Exchange-agnostic data layer for Matrix Trader.

All public functions accept an `exchange` key string ("MEXC", "HYPERLIQUID", etc.)
and return canonical formats that the scoring pipeline consumes.

Adding a new exchange:
  1. Add a branch to normalize_ticker_for_scoring()
  2. Add branches to fetch_klines(), fetch_depth(), fetch_next_funding_minutes()
  3. Register the exchange in SUPPORTED_EXCHANGES

Canonical kline format:
  {"open": [float], "high": [float], "low": [float], "close": [float], "volume": [float]}
  Ordered oldest → newest, same as MEXC native format.

Canonical ticker format (output of normalize_ticker_for_scoring):
  {
    "symbol":          str,    # e.g. "BTC_USDT" or "BTC_USDC"
    "exchange":        str,    # "MEXC" | "HYPERLIQUID" | ...
    "price":           float,  # last/mark price
    "fair_price":      float,  # mark/oracle price (for basis calc)
    "change_24h_pct":  float,  # percent, e.g. 5.2 not 0.052
    "funding_rate":    float,  # decimal, e.g. 0.0001
    "volume_24h":      float,  # USD notional
    "open_interest":   float,  # contracts or USD depending on exchange
  }
"""
from __future__ import annotations

import sys
import time
import math

from lib.mexc_client import fetch_mexc
from lib.hyperliquid_client import fetch_hl_klines, fetch_hl_orderbook
from lib.bybit_client import (
    fetch_bybit_tickers,
    fetch_bybit_klines,
    fetch_bybit_depth,
    fetch_bybit_next_funding_minutes,
    normalize_bybit_symbol,
    bybit_symbol_to_raw,
)

# Registry: exchange key → display name + capabilities
SUPPORTED_EXCHANGES: dict[str, dict] = {
    "MEXC": {
        "name":         "MEXC",
        "capabilities": ["scan", "market", "paper", "flow"],
        "quote":        "USDT",
        "geo_free":     True,
        "funding_interval_h": 8,
    },
    # BYBIT temporarily disabled: SUPPORTED_EXCHANGES previously listed it but
    # no lib/adapters/bybit.py exists, so run_agent_pipeline() returned None for
    # every Bybit signal — they carried no agent enhancement, no risk blocks,
    # no shadow delta. The Bybit branches in normalize_ticker_for_scoring(),
    # fetch_klines(), fetch_depth(), and fetch_next_funding_minutes() below
    # remain in place as dead code; re-enable by uncommenting this entry AND
    # building lib/adapters/bybit.py modelled on hyperliquid.py.
    # "BYBIT": {
    #     "name":         "Bybit",
    #     "capabilities": ["scan", "market", "paper", "flow"],
    #     "quote":        "USDT",
    #     "geo_free":     True,
    #     "funding_interval_h": 4,
    # },
    "HYPERLIQUID": {
        "name":         "Hyperliquid",
        "capabilities": ["scan", "market", "paper", "flow", "execute"],
        "quote":        "USDC",
        "geo_free":     True,
        "funding_interval_h": 1,
    },
}

# MEXC interval string → canonical key
_MEXC_INTERVAL = {
    "1m": "Min1",
    "5m": "Min5",
    "15m": "Min15",
    "30m": "Min30",
    "1h": "Min60",
    "4h": "Hour4",
    "1d": "Day1",
}

# HL lookback hours by interval and limit
def _hl_lookback(interval: str, limit: int) -> int:
    if interval == "1m":
        return max(1, math.ceil(limit / 60))
    if interval == "3m":
        return max(1, math.ceil(limit * 3 / 60))
    if interval == "5m":
        return max(1, math.ceil(limit * 5 / 60))
    if interval == "15m":
        return max(1, math.ceil(limit * 15 / 60))
    if interval == "30m":
        return max(1, math.ceil(limit * 30 / 60))
    if interval == "4h":
        return limit * 4
    if interval == "1d":
        return limit * 24
    return limit  # "1h"


# ---------------------------------------------------------------------------
# Ticker fetching
# ---------------------------------------------------------------------------

def fetch_tickers(exchange: str) -> list[dict]:
    """Fetch raw tickers for an exchange. Returns [] on failure."""
    try:
        if exchange == "BYBIT":
            return fetch_bybit_tickers()
        # MEXC and HYPERLIQUID tickers are fetched in app.py (they require
        # app-level helpers). This function handles exchanges with standalone clients.
    except Exception as e:
        print(f"[exchange_data] fetch_tickers({exchange}): {e}", file=sys.stderr)
    return []


# ---------------------------------------------------------------------------
# Ticker normalization
# ---------------------------------------------------------------------------

def normalize_ticker_for_scoring(raw: dict, exchange: str) -> dict | None:
    """
    Map a raw exchange ticker dict to the canonical scoring format.
    Returns None if price <= 0 or symbol is missing.
    """
    try:
        if exchange == "MEXC":
            price = float(raw.get("lastPrice") or 0)
            symbol = raw.get("symbol", "")
            if price <= 0 or not symbol:
                return None
            return {
                "symbol":         symbol,
                "exchange":       "MEXC",
                "price":          price,
                "fair_price":     float(raw.get("fairPrice") or price),
                "change_24h_pct": float(raw.get("riseFallRate") or 0) * 100,
                "funding_rate":   float(raw.get("fundingRate") or 0),
                "volume_24h":     float(raw.get("volume24") or raw.get("vol24h") or raw.get("amount24") or 0),
                "open_interest":  float(raw.get("holdVol") or 0),
            }

        if exchange == "BYBIT":
            raw_sym = raw.get("symbol", "")
            price   = float(raw.get("lastPrice") or 0)
            if price <= 0 or not raw_sym:
                return None
            symbol = normalize_bybit_symbol(raw_sym)
            prev   = float(raw.get("prevPrice24h") or price)
            change_24h = float(raw.get("price24hPcnt") or 0) * 100
            return {
                "symbol":         symbol,
                "exchange":       "BYBIT",
                "price":          price,
                "fair_price":     float(raw.get("markPrice") or price),
                "change_24h_pct": change_24h,
                "funding_rate":   float(raw.get("fundingRate") or 0),
                "volume_24h":     float(raw.get("turnover24h") or 0),
                "open_interest":  float(raw.get("openInterest") or 0),
            }

        if exchange == "HYPERLIQUID":
            price = float(raw.get("markPx") or raw.get("midPx") or raw.get("lastPrice") or 0)
            coin = raw.get("coin") or raw.get("name") or ""
            if not coin:
                coin = (raw.get("symbol") or "").replace("_USDC", "").replace("_USDT", "")
            symbol = f"{coin}_USDC" if coin else raw.get("symbol", "")
            if price <= 0 or not symbol:
                return None
            prev_day = float(raw.get("prevDayPx") or price)
            change_24h = (
                ((price - prev_day) / prev_day * 100)
                if prev_day and prev_day != price
                else float(raw.get("riseFallRate") or 0) * 100
            )
            return {
                "symbol":         symbol,
                "exchange":       "HYPERLIQUID",
                "price":          price,
                "fair_price":     float(raw.get("oraclePx") or price),
                "change_24h_pct": change_24h,
                "funding_rate":   float(raw.get("funding") or raw.get("fundingRate") or 0),
                "volume_24h":     float(raw.get("dayNtlVlm") or raw.get("vol24h") or 0),
                "open_interest":  float(raw.get("openInterest") or raw.get("holdVol") or 0),
            }

    except Exception as e:
        print(f"[exchange_data] normalize_ticker_for_scoring({exchange}): {e}", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# Klines
# ---------------------------------------------------------------------------

def fetch_klines(exchange: str, symbol: str, interval: str, limit: int) -> dict | None:
    """
    Fetch OHLCV klines and return in canonical format.

    interval: "1h" | "4h" | "1d"
    Returns {"open":[], "high":[], "low":[], "close":[], "volume":[]} or None.
    """
    try:
        if exchange == "MEXC":
            mexc_iv = _MEXC_INTERVAL.get(interval, "Hour1")
            data = fetch_mexc(f"/contract/kline/{symbol}", params={"interval": mexc_iv, "limit": limit})
            if not data or not isinstance(data, dict):
                return None
            return {
                "open":   [float(x) for x in data.get("open",  [])],
                "high":   [float(x) for x in data.get("high",  [])],
                "low":    [float(x) for x in data.get("low",   [])],
                "close":  [float(x) for x in data.get("close", [])],
                "volume": [float(x) for x in data.get("vol",   [])],
            }

        if exchange == "BYBIT":
            return fetch_bybit_klines(bybit_symbol_to_raw(symbol), interval, limit)

        if exchange == "HYPERLIQUID":
            coin = symbol.replace("_USDC", "").replace("_USDT", "")
            rows = fetch_hl_klines(coin, interval, lookback_hours=_hl_lookback(interval, limit))
            if not rows:
                return None
            return {
                "open":   [float(k["o"]) for k in rows],
                "high":   [float(k["h"]) for k in rows],
                "low":    [float(k["l"]) for k in rows],
                "close":  [float(k["c"]) for k in rows],
                "volume": [float(k["v"]) for k in rows],
            }

    except Exception as e:
        print(f"[exchange_data] fetch_klines({exchange}, {symbol}, {interval}): {e}", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# Order book depth
# ---------------------------------------------------------------------------

def fetch_depth(exchange: str, symbol: str) -> dict | None:
    """
    Fetch order book snapshot.
    Returns raw exchange dict (bids/asks lists) or None.
    The caller uses _parse_depth_volume() which handles both list and dict formats.
    """
    try:
        if exchange == "MEXC":
            return fetch_mexc(f"/contract/depth/{symbol}")

        if exchange == "BYBIT":
            return fetch_bybit_depth(bybit_symbol_to_raw(symbol))

        if exchange == "HYPERLIQUID":
            coin = symbol.replace("_USDC", "").replace("_USDT", "")
            return fetch_hl_orderbook(coin)

    except Exception as e:
        print(f"[exchange_data] fetch_depth({exchange}, {symbol}): {e}", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# Funding rate info
# ---------------------------------------------------------------------------

def fetch_next_funding_minutes(exchange: str, symbol: str) -> int | None:
    """Returns minutes until next funding settlement, or None."""
    try:
        if exchange == "MEXC":
            data = fetch_mexc(f"/contract/funding_rate/{symbol}")
            if data and isinstance(data, dict):
                next_settle_ms = data.get("nextSettleTime")
                if next_settle_ms:
                    minutes_left = (int(next_settle_ms) / 1000 - time.time()) / 60
                    return max(0, int(round(minutes_left)))

        if exchange == "BYBIT":
            return fetch_bybit_next_funding_minutes(bybit_symbol_to_raw(symbol))

        if exchange == "HYPERLIQUID":
            return 30  # HL settles every hour; ~30 min on average

    except Exception as e:
        print(f"[exchange_data] fetch_next_funding_minutes({exchange}, {symbol}): {e}", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# Daily klines (separate: different format consumed by daily_trend_direction)
# ---------------------------------------------------------------------------

def fetch_daily_klines_raw(exchange: str, symbol: str, limit: int = 30):
    """
    Fetch daily klines in the format daily_trend_direction() expects.

    MEXC:        returns the raw dict {"open":[], ...} that daily_trend_direction reads
    HYPERLIQUID: returns list of [t, o, c, h, l, v] arrays
    Returns None on failure.
    """
    try:
        if exchange == "MEXC":
            return fetch_mexc(
                f"/contract/kline/{symbol}",
                params={"interval": "Day1", "limit": limit},
            )

        if exchange == "BYBIT":
            return fetch_bybit_klines(bybit_symbol_to_raw(symbol), "1d", limit)

        if exchange == "HYPERLIQUID":
            coin = symbol.replace("_USDC", "").replace("_USDT", "")
            rows = fetch_hl_klines(coin, "1d", lookback_hours=limit * 24)
            if not rows:
                return None
            return [[k["t"], k["o"], k["c"], k["h"], k["l"], k["v"]] for k in rows]

    except Exception as e:
        print(f"[exchange_data] fetch_daily_klines_raw({exchange}, {symbol}): {e}", file=sys.stderr)
    return None
