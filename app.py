import os
import sys
import json
import socket
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from functools import partial

import requests
import pandas as pd
from flask import Flask, jsonify, render_template, request
from dotenv import load_dotenv

from lib.indicators import (
    rsi as calc_rsi,
    ema as calc_ema,
    atr as calc_atr,
    atr_pct as calc_atr_pct,
    volatility_regime,
)
from lib.laddering import generate_ladders

load_dotenv()

app = Flask(__name__)

MEXC_BASE    = "https://contract.mexc.com/api/v1"
BINANCE_FAPI = "https://fapi.binance.com"
BYBIT_BASE   = "https://api.bybit.com"

# Major pairs that exist on Binance/Bybit futures — only these get sentiment calls.
# MEXC has 800+ pairs; calling Binance/Bybit for obscure altcoins wastes time and hits 400s.
SENTIMENT_PAIRS = {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT"}

PORT = int(os.getenv("MATRIX_PORT", "8080"))
CONVICTION_THRESHOLD = 55   # signals below this are filtered from results
KLINE_INTERVAL = "Min60"    # 1h candles — 100 candles default = ~4 days, plenty for 14-period indicators
ENRICH_TOP_N = 30           # enrich only the top N base signals to limit API calls
ENRICH_WORKERS = 10         # concurrent threads for stage-2 enrichment
DB_PATH = "data/signals.db"

# ---------------------------------------------------------------------------
# Signal history — SQLite
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Create the signals history table if it doesn't exist."""
    os.makedirs("data", exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            logged_at    TEXT NOT NULL,
            symbol       TEXT NOT NULL,
            exchange     TEXT NOT NULL,
            direction    TEXT NOT NULL,
            strategy     TEXT NOT NULL,
            conviction   INTEGER NOT NULL,
            price        REAL NOT NULL,
            entry1       REAL,
            entry2       REAL,
            entry3       REAL,
            tp1          REAL,
            tp2          REAL,
            tp3          REAL,
            stop_loss    REAL,
            atr_pct      REAL,
            volatility   TEXT,
            funding_rate REAL,
            rsi_1h       REAL,
            trend_score  INTEGER,
            tags         TEXT,
            signal_why   TEXT,
            result       TEXT DEFAULT NULL,
            result_note  TEXT DEFAULT NULL,
            result_at    TEXT DEFAULT NULL
        )
    """)
    con.commit()
    con.close()


def log_signals(signals: list[dict]) -> None:
    """
    Append enriched signals to the history DB after every scan.
    Failures are swallowed — logging must never crash the scan.
    Duplicate guard: skip any signal already logged in the last 30 minutes
    for the same symbol + strategy + direction combination.
    """
    try:
        con = sqlite3.connect(DB_PATH)
        cutoff = (datetime.utcnow() - timedelta(minutes=30)).isoformat()
        for sig in signals:
            exists = con.execute("""
                SELECT 1 FROM signals
                WHERE symbol=? AND strategy=? AND direction=? AND logged_at > ?
            """, (sig["symbol"], sig.get("strategy", ""), sig["direction"], cutoff)).fetchone()
            if exists:
                continue
            entries = sig.get("entries") or [None, None, None]
            exits   = sig.get("exits")   or [None, None, None]
            con.execute("""
                INSERT INTO signals
                (logged_at, symbol, exchange, direction, strategy, conviction,
                 price, entry1, entry2, entry3, tp1, tp2, tp3, stop_loss,
                 atr_pct, volatility, funding_rate, rsi_1h, trend_score,
                 tags, signal_why)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                datetime.utcnow().isoformat(),
                sig["symbol"],
                sig.get("exchange", "MEXC"),
                sig["direction"],
                sig.get("strategy", ""),
                sig.get("conviction", 0),
                sig.get("price", 0),
                entries[0], entries[1], entries[2],
                exits[0],   exits[1],   exits[2],
                sig.get("stop_loss"),
                sig.get("atr_pct"),
                sig.get("volatility"),
                sig.get("funding_rate"),
                sig.get("rsi_1h"),
                sig.get("trend_score"),
                ",".join(sig.get("tags", [])),
                sig.get("signal_why", ""),
            ))
        con.commit()
        con.close()
    except Exception as e:
        print(f"log_signals error: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------
# Each strategy is a self-contained scoring profile. Weights scale the raw
# points awarded in score_ticker(); filters gate which tickers even enter
# stage 2; leverage_cap and min_conviction are applied at run_scan() time.
#
# Weight proportions are preserved from the Balanced defaults:
#   momentum strong : weak  = 1 : 0.5
#   funding  strong : weak  = 1 : 0.4
#   basis           = full weight or nothing (no weak tier)

STRATEGIES: dict = {
    "balanced": {
        "name": "Balanced",
        "description": "All-market scanner, default settings",
        "risk_level": "medium",
        "weights": {
            "momentum":    30,
            "funding":     25,
            "basis":       15,
            "volume_mult": 1.1,
        },
        "leverage_cap":    20,
        "min_conviction":  55,
        "filters":         {},
    },
    "funding_arb": {
        "name": "Funding Arb",
        "description": "Funding rate extremes in ranging markets",
        "risk_level": "low",
        "weights": {
            "momentum":    10,
            "funding":     50,
            "basis":       20,
            "volume_mult": 1.0,
        },
        "leverage_cap":    10,
        "min_conviction":  60,
        "filters": {
            # Only fire if absolute funding rate exceeds 0.03%
            "min_funding_abs": 0.0003,
        },
    },
    "momentum_breakout": {
        "name": "Momentum Breakout",
        "description": "Trending markets with volume expansion",
        "risk_level": "high",
        "weights": {
            "momentum":    50,
            "funding":     10,
            "basis":        5,
            "volume_mult": 1.2,
        },
        "leverage_cap":    25,
        "min_conviction":  55,
        "filters": {
            # Only fire if 24h change exceeds ±3% (stored as percent, e.g. 3.0 = 3%)
            "min_24h_change_pct": 3.0,
        },
    },
    "mean_reversion": {
        "name": "Mean Reversion",
        "description": "Overbought/oversold after extended moves",
        "risk_level": "medium",
        "weights": {
            "momentum":     5,
            "funding":     30,
            "basis":       30,
            "volume_mult": 1.0,
        },
        "leverage_cap":    15,
        "min_conviction":  65,
        "filters": {
            # RSI extremes required — applied in stage 2 after klines are available
            "rsi_long_max":   35,   # LONG only if RSI < 35
            "rsi_short_min":  65,   # SHORT only if RSI > 65
        },
    },
}


# ---------------------------------------------------------------------------
# MEXC API helper
# ---------------------------------------------------------------------------

def fetch_mexc(path: str, params: dict | None = None) -> dict | list | None:
    """
    GET a MEXC contract API endpoint and unwrap the response envelope.

    All MEXC responses wrap their payload in {"success": true, "data": ...}.
    Returns the value of "data" directly, or None on any failure.

    Retry policy: up to 3 total attempts (2 retries) with 1s then 2s delays.
    Retries on: ConnectionError, Timeout, HTTP 5xx.
    Does NOT retry on 4xx — those are real client errors that won't change.
    """
    last_err: str = ""
    for attempt in range(3):
        try:
            resp = requests.get(f"{MEXC_BASE}{path}", params=params, timeout=10)

            # 5xx = server-side problem; retry after a delay. 4xx = our fault; bail.
            if resp.status_code >= 500 and attempt < 2:
                last_err = f"HTTP {resp.status_code}"
                time.sleep(attempt + 1)
                continue

            resp.raise_for_status()
            body = resp.json()
            if not body.get("success"):
                return None
            return body.get("data")

        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_err = str(e)
            if attempt < 2:
                time.sleep(attempt + 1)
                continue
        except Exception as e:
            print(f"MEXC fetch error [{path}]: {e}", file=sys.stderr)
            return None

    print(f"MEXC fetch error [{path}]: {last_err} (gave up after 3 attempts)", file=sys.stderr)
    return None


def fetch_market_sentiment(mexc_symbol: str, price: float) -> dict:
    """
    Fetch L/S ratio and open interest from Binance, Bybit, and OKX public APIs.
    No API key required for any exchange.

    Args:
        mexc_symbol: MEXC contract symbol, e.g. "BTC_USDT".
        price:       Current price in USD — used to convert base-asset OI to USD.

    Returns:
        Dict with keys:
          binance_ls_long_pct — % of accounts long on Binance futures (0–100 float). None on failure.
          binance_oi          — Binance futures OI in USD. None on failure.
          bybit_oi            — Bybit linear futures OI in USD. None on failure.
          okx_ls_long_pct     — % of accounts long on OKX futures (0–100 float). None on failure.
          okx_oi              — OKX futures OI in USD. None on failure.

    Only pairs in SENTIMENT_PAIRS are fetched — MEXC-specific altcoins return
    all-None immediately (no API calls). Each of the five network calls is
    independently wrapped in try/except.

    OI unit note: all three exchanges return OI in base-asset units (e.g., BTC
    quantity). Multiplying by `price` converts to USD.

    Binance L/S: longAccount is a 0–1 decimal → × 100 = percentage.
    OKX L/S: longShortRatio is longs:shorts ratio → ratio/(1+ratio) × 100 = percentage.
    """
    base = mexc_symbol.split("_")[0]        # "BTC_USDT" → "BTC"
    result = {
        "binance_ls_long_pct": None,
        "binance_oi":          None,
        "bybit_oi":            None,
        "okx_ls_long_pct":     None,
        "okx_oi":              None,
    }

    if base not in SENTIMENT_PAIRS:
        return result

    bn_sym  = mexc_symbol.replace("_", "")         # "BTC_USDT" → "BTCUSDT"
    quote   = mexc_symbol.split("_")[-1]            # "BTC_USDT" → "USDT"
    okx_sym = f"{base}-{quote}-SWAP"                # "BTC_USDT" → "BTC-USDT-SWAP"

    # --- Binance: L/S ratio ---
    try:
        resp = requests.get(
            f"{BINANCE_FAPI}/futures/data/globalLongShortAccountRatio",
            params={"symbol": bn_sym, "period": "1h", "limit": 1},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            long_account = float(data[0]["longAccount"])   # 0–1 decimal
            result["binance_ls_long_pct"] = round(long_account * 100, 1)
    except Exception as e:
        print(f"Binance L/S error [{bn_sym}]: {e}", file=sys.stderr)

    # --- Binance: open interest ---
    try:
        resp = requests.get(
            f"{BINANCE_FAPI}/fapi/v1/openInterest",
            params={"symbol": bn_sym},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        oi_base = float(data.get("openInterest", 0) or 0)
        if oi_base > 0 and price > 0:
            result["binance_oi"] = round(oi_base * price)
    except Exception as e:
        print(f"Binance OI error [{bn_sym}]: {e}", file=sys.stderr)

    # --- Bybit: open interest ---
    try:
        resp = requests.get(
            f"{BYBIT_BASE}/v5/market/open-interest",
            params={"category": "linear", "symbol": bn_sym, "intervalTime": "1h", "limit": 1},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        items = (data.get("result") or {}).get("list") or []
        if items and price > 0:
            oi_base = float(items[0].get("openInterest", 0) or 0)
            if oi_base > 0:
                result["bybit_oi"] = round(oi_base * price)
    except Exception as e:
        print(f"Bybit OI error [{bn_sym}]: {e}", file=sys.stderr)

    # --- OKX: L/S ratio ---
    # Endpoint uses ccy (base currency), not instId.
    # Response data is a list of [timestamp_ms, ratio_string] pairs, newest first.
    # ratio is longs:shorts (e.g. "1.04" = 1.04 longs per 1 short).
    # long_pct = ratio / (1 + ratio) × 100
    try:
        resp = requests.get(
            "https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio",
            params={"ccy": base, "period": "1H"},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json().get("data") or []
        if data:
            ratio = float(data[0][1])   # data[0] = [timestamp, ratio_string]
            result["okx_ls_long_pct"] = round(ratio / (1 + ratio) * 100, 1)
    except Exception as e:
        print(f"OKX L/S error [{base}]: {e}", file=sys.stderr)

    # --- OKX: open interest ---
    # oiUsd is already in USD — no multiplication needed.
    try:
        resp = requests.get(
            "https://www.okx.com/api/v5/public/open-interest",
            params={"instType": "SWAP", "instId": okx_sym},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json().get("data") or []
        if data:
            oi_usd = float(data[0].get("oiUsd", 0) or 0)
            if oi_usd > 0:
                result["okx_oi"] = round(oi_usd)
    except Exception as e:
        print(f"OKX OI error [{okx_sym}]: {e}", file=sys.stderr)

    return result


# ---------------------------------------------------------------------------
# Stage 1: lightweight ticker scoring
# ---------------------------------------------------------------------------

def score_ticker(ticker: dict, strategy: dict | None = None) -> dict | None:
    """
    Score a single ticker using only the fields available in the /contract/ticker
    response. No additional API calls. Returns a base signal dict or None if
    the ticker is unusable (zero price, missing symbol, parse error), or if
    it fails the strategy's stage-1 filters.

    Scoring inputs (weights scale with active strategy):
      - riseFallRate: 24h price change
      - fundingRate:  negative = shorts paying longs, bullish
      - price vs fairPrice: basis spread
      - volume24:     multiplier when volume > $10M

    Direction is whichever side accumulates more points. Ties go to LONG.
    """
    strat = strategy or STRATEGIES["balanced"]
    w = strat["weights"]
    filters = strat.get("filters", {})

    try:
        symbol = ticker.get("symbol", "")
        price = float(ticker.get("lastPrice") or 0)
        fair_price = float(ticker.get("fairPrice") or price)
        change_pct = float(ticker.get("riseFallRate") or 0) * 100  # decimal → percent
        funding = float(ticker.get("fundingRate") or 0)
        volume = float(
            ticker.get("volume24") or ticker.get("vol24h") or ticker.get("amount24") or 0
        )
        open_interest = float(ticker.get("holdVol") or 0)

        if price <= 0 or not symbol:
            return None

        # Stage-1 filters: applied before any scoring to avoid wasting enrichment quota
        if "min_funding_abs" in filters and abs(funding) < filters["min_funding_abs"]:
            return None
        if "min_24h_change_pct" in filters and abs(change_pct) < filters["min_24h_change_pct"]:
            return None

        long_score = 0
        short_score = 0
        tags: list[str] = []

        # 24h momentum
        # strong tier = full weight, weak tier = half weight (preserves original proportions)
        mom_strong = w["momentum"]
        mom_weak   = w["momentum"] // 2
        if change_pct > 5:
            long_score += mom_strong
            tags.append("strong_momentum")
        elif change_pct > 2:
            long_score += mom_weak
            tags.append("momentum")
        elif change_pct < -5:
            short_score += mom_strong
            tags.append("strong_dump")
        elif change_pct < -2:
            short_score += mom_weak
            tags.append("dump")

        # Funding rate
        # Negative funding = shorts paying longs = short-squeeze setup
        # strong tier = full weight, weak tier = 40% (original 10/25 ratio)
        fund_strong = w["funding"]
        fund_weak   = int(w["funding"] * 0.4)
        if funding < -0.001:
            long_score += fund_strong
            tags.append("short_squeeze")
        elif funding < 0:
            long_score += fund_weak
        elif funding > 0.001:
            short_score += fund_strong
            tags.append("long_squeeze")
        elif funding > 0:
            short_score += fund_weak

        # Basis spread — full weight or nothing (no weak tier)
        # Premium (price > fair): longs paying more, bearish lean
        # Discount (price < fair): shorts paying more, bullish lean
        if fair_price > 0:
            basis_pct = (price - fair_price) / fair_price * 100
            if basis_pct > 0.1:
                short_score += w["basis"]
                tags.append("premium")
            elif basis_pct < -0.1:
                long_score += w["basis"]
                tags.append("discount")

        # Volume multiplier: strategy-defined, only applied when volume > $10M
        vol_mult = w["volume_mult"] if volume > 10_000_000 else 1.0
        if volume > 10_000_000:
            tags.append("high_volume")

        if long_score >= short_score:
            direction = "LONG"
            conviction_base = min(int(long_score * vol_mult), 100)
        else:
            direction = "SHORT"
            conviction_base = min(int(short_score * vol_mult), 100)

        return {
            "symbol": symbol,
            "exchange": "MEXC",
            "direction": direction,
            "conviction_base": conviction_base,
            "price": price,
            "change_24h_pct": round(change_pct, 4),
            "funding_rate": funding,
            "volume_24h": volume,
            "open_interest": open_interest,
            "tags": tags,
        }
    except Exception as e:
        print(f"score_ticker error [{ticker.get('symbol', '?')}]: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Stage 2: enrich with klines, depth, indicators, and ladders
# ---------------------------------------------------------------------------

def _parse_depth_volume(levels: list) -> float:
    """Sum quantity from depth levels regardless of whether each level is a
    [price, qty] list or a {"price": ..., "quantity": ...} dict."""
    total = 0.0
    for level in levels:
        try:
            if isinstance(level, (list, tuple)) and len(level) >= 2:
                total += float(level[1])
            elif isinstance(level, dict):
                total += float(level.get("quantity") or level.get("qty") or 0)
        except (ValueError, TypeError):
            continue
    return total


def why_signal(sig: dict) -> str:
    """
    Generate a plain-English one-liner explaining the primary reason a signal
    fired, using the tags list assembled during scoring plus key numeric fields.

    Priority waterfall: the first matching tag sets the primary clause.
    A secondary clause is appended when extreme_vol is present, as a size-down
    caution — this is the only modifier that overrides normal secondary text
    because a 5%+ ATR candle can wipe a position regardless of setup quality.

    Kept as a pure function (no side-effects, no I/O) so it can be unit-tested
    or reused in P2c's template report without changes.
    """
    tags      = sig.get("tags", [])
    direction = sig.get("direction", "LONG")
    rsi       = sig.get("rsi_1h", 50.0)
    funding   = sig.get("funding_rate", 0.0)
    chg24     = sig.get("change_24h_pct", 0.0)
    conviction = sig.get("conviction", 0)
    is_extreme = "extreme_vol" in tags

    # Format helpers local to this function so we don't import formatting deps
    def _fmt_pct(v: float) -> str:
        sign = "+" if v >= 0 else ""
        return f"{sign}{v:.1f}%"

    def _fmt_fund(v: float) -> str:
        sign = "+" if v >= 0 else ""
        return f"{sign}{v * 100:.3f}%"

    # --- Primary clause: tag-priority waterfall ---
    if "short_squeeze" in tags:
        primary = f"Funding at {_fmt_fund(funding)} — shorts paying longs, squeeze setup"
    elif "long_squeeze" in tags:
        primary = f"Funding at {_fmt_fund(funding)} — longs overextended, squeeze building"
    elif "strong_momentum" in tags:
        primary = f"Surging {_fmt_pct(chg24)} in 24h"
        if "high_volume" in tags:
            primary += " with high volume"
    elif "strong_dump" in tags:
        primary = f"Dumping {_fmt_pct(chg24)} in 24h — bearish momentum in play"
    elif "momentum" in tags:
        primary = f"Bullish momentum, up {_fmt_pct(chg24)} in 24h"
    elif "dump" in tags:
        primary = f"Bearish pressure, down {_fmt_pct(chg24)} in 24h"
    elif "oversold" in tags:
        suffix = " with negative funding" if funding < -0.0001 else ""
        primary = f"RSI at {rsi:.0f} — oversold{suffix}"
    elif "overbought" in tags:
        suffix = " with positive funding" if funding > 0.0001 else ""
        primary = f"RSI at {rsi:.0f} — overbought{suffix}"
    elif "overbought_risk" in tags:
        primary = f"Momentum signal but RSI elevated at {rsi:.0f} — reduced conviction"
    elif "oversold_risk" in tags:
        primary = f"Short signal but RSI stretched down at {rsi:.0f} — reduced conviction"
    elif "discount" in tags:
        primary = "Basis discount — price trading below fair value"
    elif "premium" in tags:
        primary = "Basis premium — price trading above fair value"
    elif "bid_heavy" in tags and direction == "LONG":
        primary = "Orderbook bid-heavy — buy pressure dominates"
    elif "ask_heavy" in tags and direction == "SHORT":
        primary = "Orderbook ask-heavy — sell pressure dominates"
    else:
        primary = f"Score {conviction} — multiple factors aligned"

    # --- Extreme volatility caution always appended ---
    if is_extreme:
        return primary + " · extreme volatility — size down"
    return primary


def generate_report(sig: dict) -> str:
    """
    Build a 4-section plain-English trade brief from a fully-populated signal dict.

    Returns a JSON string: list of {label, text} dicts, one per section.
    Pure function — no I/O, no side effects.

    Section order:
      §1  Setup      — what is driving the signal (strategy-aware)
      §2  Structure  — entry zone, TP levels, stop loss
      §3  Invalidation — ATR multiple (must match invalidationHTML formula exactly)
      §4  Risk       — volatility, leverage cap, funding context

    §1 special rule: when strategy is "Mean Reversion", lead with RSI regardless
    of whether funding tags (short_squeeze, long_squeeze) are present. RSI extreme
    is the thesis for that strategy; funding is secondary context.

    ATR formula (must match invalidationHTML in index.html exactly):
        atr_value = (atr_pct / 100) * price
        distance  = abs(entries[0] - stop_loss)
        atr_mult  = round(distance / atr_value, 1)
    """
    strategy_name  = sig.get("strategy", "Balanced")
    direction      = sig.get("direction", "LONG")
    rsi            = sig.get("rsi_1h", 50.0)
    funding        = sig.get("funding_rate", 0.0)
    chg_24h        = sig.get("change_24h_pct", 0.0)
    chg_4h         = sig.get("change_4h_pct", 0.0)
    chg_1h         = sig.get("change_1h_pct", 0.0)
    atr_pct        = sig.get("atr_pct", 0.0)
    price          = sig.get("price", 0.0)
    vol_regime     = sig.get("volatility", "medium")
    conviction     = sig.get("conviction", 0)
    leverage_cap   = sig.get("leverage_cap", 20)
    tags           = sig.get("tags", [])
    entries        = sig.get("entries", [0.0, 0.0, 0.0])
    exits          = sig.get("exits", [0.0, 0.0, 0.0])
    stop_loss      = sig.get("stop_loss", 0.0)
    nfm            = sig.get("next_funding_minutes")
    trend_score    = sig.get("trend_score", 0)
    symbol         = sig.get("symbol", "")

    is_long = direction == "LONG"

    def _pct(v: float) -> str:
        sign = "+" if v >= 0 else ""
        return f"{sign}{v:.1f}%"

    def _fund(v: float) -> str:
        sign = "+" if v >= 0 else ""
        return f"{sign}{v * 100:.3f}%"

    # --- §1 Setup ---
    if strategy_name == "Mean Reversion":
        rsi_label = "oversold" if is_long else "overbought"
        s1 = (
            f"RSI at {rsi:.0f} — {rsi_label} reading signals the thesis for this trade. "
            f"{symbol} has extended and the setup is a {direction.lower()} reversion to mean. "
        )
        if abs(funding) > 0.0001:
            paying = "longs" if funding > 0 else "shorts"
            s1 += f"Funding at {_fund(funding)} supports the setup — {paying} are paying the carry."
        elif trend_score != 0:
            trend_word = "bullish" if trend_score > 0 else "bearish"
            s1 += f"Trend score at {trend_score:+d} ({trend_word} EMA structure) is secondary context."
    elif "short_squeeze" in tags:
        s1 = (
            f"Funding at {_fund(funding)} — shorts are paying longs. A squeeze setup is forming. "
            f"24h change: {_pct(chg_24h)}. Negative funding combined with upward momentum "
            f"creates a forced-unwind dynamic that can move quickly."
        )
    elif "long_squeeze" in tags:
        s1 = (
            f"Funding at {_fund(funding)} — longs are overextended and paying carry. "
            f"24h change: {_pct(chg_24h)}. Elevated positive funding signals overcrowded longs "
            f"and increases the risk of a liquidation cascade on any dip."
        )
    elif "strong_momentum" in tags or "momentum" in tags:
        volume_note = " with above-average volume confirming the move" if "high_volume" in tags else ""
        s1 = (
            f"{symbol} is up {_pct(chg_24h)} in 24h{volume_note}. "
            f"4h change: {_pct(chg_4h)}, 1h change: {_pct(chg_1h)}. "
            f"Trend score {trend_score:+d} — EMA structure is {'aligned' if trend_score > 20 else 'weak'}. "
            f"Momentum setup — price is breaking higher and buyers remain in control."
        )
    elif "strong_dump" in tags or "dump" in tags:
        s1 = (
            f"{symbol} is down {_pct(chg_24h)} in 24h. "
            f"4h: {_pct(chg_4h)}, 1h: {_pct(chg_1h)}. "
            f"Sellers are in control. Trend score {trend_score:+d}. "
            f"Short momentum setup — continued downside is the higher-probability outcome."
        )
    elif "oversold" in tags:
        s1 = (
            f"RSI at {rsi:.0f} — oversold. Price may be reaching a local exhaustion point. "
            f"Funding at {_fund(funding)}. "
            f"Setup is a counter-trend long off stretched RSI — conviction at {conviction}."
        )
    elif "overbought" in tags:
        s1 = (
            f"RSI at {rsi:.0f} — overbought. "
            f"Funding at {_fund(funding)}. "
            f"Short setup off stretched RSI — conviction at {conviction}."
        )
    else:
        s1 = (
            f"Score {conviction} — multiple factors aligned for a {direction.lower()} setup. "
            f"24h: {_pct(chg_24h)}, RSI: {rsi:.0f}, funding: {_fund(funding)}."
        )

    # --- §2 Structure ---
    e1, e2, e3 = entries[0], entries[1], entries[2]
    tp1, tp2, tp3 = exits[0], exits[1], exits[2]
    direction_verb = "below" if is_long else "above"
    s2 = (
        f"Scale in across three entries: {e1:.8g} → {e2:.8g} → {e3:.8g}. "
        f"Take partial profits at {tp1:.8g}, {tp2:.8g}, and {tp3:.8g}. "
        f"Full stop at {stop_loss:.8g} ({direction_verb} all entries). "
        f"Risk/reward on Entry 1 to TP 3: ~{abs(tp3 - e1) / max(abs(e1 - stop_loss), 1e-12):.1f}:1."
    )

    # --- §3 Invalidation (must use same formula as invalidationHTML) ---
    atr_value = (atr_pct / 100) * price
    if atr_value > 0:
        distance = abs(entries[0] - stop_loss)
        atr_mult = round(distance / atr_value, 1)
        close_word = "below" if is_long else "above"
        s3 = (
            f"Trade invalidates on a close {close_word} {stop_loss:.8g}. "
            f"That level is {atr_mult}× ATR from Entry 1 — "
            f"the ATR ({atr_pct:.1f}% of price) defines the expected daily range. "
            f"If price reaches the stop, the thesis is wrong and the full position must close."
        )
    else:
        close_word = "below" if is_long else "above"
        s3 = f"Trade invalidates on a close {close_word} {stop_loss:.8g}. Close the full position if reached."

    # --- §4 Risk ---
    regime_note = {
        "low":     "Low volatility — tighter fills, lower slippage risk.",
        "medium":  "Medium volatility — standard position sizing applies.",
        "high":    "High volatility — consider reducing size by 25–50%.",
        "extreme": "Extreme volatility (5%+ ATR) — reduce position size significantly, expect wider spreads.",
    }.get(vol_regime, "")
    fund_note = ""
    if abs(funding) > 0.0001:
        paying_side = "long" if funding > 0 else "short"
        receiving_side = "short" if funding > 0 else "long"
        fund_note = (
            f" Funding at {_fund(funding)} per 8h — {paying_side}s pay, {receiving_side}s collect. "
        )
        if nfm is not None:
            fund_note += f"Next settlement in {nfm}m."
    s4 = (
        f"Max leverage: {leverage_cap}×. ATR: {atr_pct:.1f}% of price. {regime_note}"
        f"{fund_note}"
    )

    sections = [
        {"label": "Setup",         "text": s1.strip()},
        {"label": "Structure",     "text": s2.strip()},
        {"label": "Invalidation",  "text": s3.strip()},
        {"label": "Risk",          "text": s4.strip()},
    ]
    return json.dumps(sections)


def enrich_signal(base: dict, strategy: dict | None = None) -> dict | None:
    """
    Stage 2: fetch klines and depth for one symbol, run RSI/ATR indicators,
    compute orderbook imbalance, adjust conviction, and generate entry/TP/SL
    ladders. Returns the full signal dict or None if data is insufficient.

    Conviction adjustments (on top of conviction_base from stage 1):
      +10 if RSI aligns with direction (oversold for LONG, overbought for SHORT)
      -10 if RSI contradicts direction
      +10 if orderbook imbalance aligns with direction
      ×0.85 in extreme volatility regime (5%+ ATR) — risky, reduce confidence

    Strategy-specific stage-2 filters (applied after RSI is computed):
      mean_reversion: discard if LONG and RSI >= rsi_long_max, or
                              if SHORT and RSI <= rsi_short_min

    Falls back gracefully at every step:
      - If klines are missing or too short: return None (skip the signal)
      - If depth is unavailable: use 0.5 (neutral) imbalance
      - If ATR is zero: fall back to 1% of price so generate_ladders never errors
    """
    strat = strategy or STRATEGIES["balanced"]
    symbol = base["symbol"]
    price = base["price"]
    direction = base["direction"]
    tags = list(base["tags"])

    try:
        # --- Klines ---
        # No start/end: MEXC returns the latest ~100 candles by default.
        # 100 × Hour1 = ~4 days, enough for 14-period ATR and RSI with headroom.
        kline_data = fetch_mexc(f"/contract/kline/{symbol}", params={"interval": KLINE_INTERVAL})

        if not kline_data or not isinstance(kline_data, dict):
            return None

        df = pd.DataFrame({
            "open":   kline_data.get("open", []),
            "high":   kline_data.get("high", []),
            "low":    kline_data.get("low", []),
            "close":  kline_data.get("close", []),
            "volume": kline_data.get("vol", []),
        }).astype(float)

        # Need at least period+2 rows for reliable ATR/RSI (14 + 2 = 16)
        if len(df) < 16:
            return None

        # --- Momentum: 1h and 4h price change ---
        # change_1h_pct: open→close on the most recent completed candle.
        # Using open rather than close[-2] because MEXC sometimes delivers the
        # current live candle as the last row — open/close of that row reflects
        # the in-progress move, which is still useful directional signal.
        try:
            last_open = float(df["open"].iloc[-1])
            last_close = float(df["close"].iloc[-1])
            change_1h_pct = round((last_close - last_open) / last_open * 100, 4) if last_open > 0 else 0.0
        except Exception:
            change_1h_pct = 0.0

        # change_4h_pct: close now vs close 4 candles ago (= 4 hours at Min60).
        # iloc[-5] is the close 4 periods back: [-1]=now, [-2]=1h, [-3]=2h, [-4]=3h, [-5]=4h.
        # We already require len(df) >= 16 so iloc[-5] is always safe.
        try:
            close_4h_ago = float(df["close"].iloc[-5])
            change_4h_pct = round((last_close - close_4h_ago) / close_4h_ago * 100, 4) if close_4h_ago > 0 else 0.0
        except Exception:
            change_4h_pct = 0.0

        # --- Indicators ---
        rsi_series = calc_rsi(df, period=14)
        rsi_clean = rsi_series.dropna()
        last_rsi = float(rsi_clean.iloc[-1]) if not rsi_clean.empty else 50.0

        atr_series = calc_atr(df, period=14)
        atr_clean = atr_series.dropna()
        atr_val = float(atr_clean.iloc[-1]) if not atr_clean.empty else 0.0
        if atr_val <= 0:
            atr_val = price * 0.01  # 1% of price as safe fallback

        atr_pct_val = calc_atr_pct(df, price, period=14)
        vol_regime = volatility_regime(atr_pct_val)

        # --- Trend score: EMA20/EMA50 alignment ---
        # EWM never produces NaN with adjust=False, but dropna() guards against
        # a column of NaNs from a degenerate input series.
        try:
            ema20_last = float(calc_ema(df, period=20).dropna().iloc[-1])
            ema50_last = float(calc_ema(df, period=50).dropna().iloc[-1])
            # Amplify the EMA gap 10× so a 1% gap → 10 pts, 10% gap → 100 pts (full range).
            raw = (ema20_last - ema50_last) / ema50_last * 100 * 10
            raw = max(-100.0, min(100.0, raw))
            # Price position bonus: +20 if price above both EMAs (bullish structure),
            # -20 if below both (bearish). No bonus if price is between the two.
            if price > ema20_last and price > ema50_last:
                raw += 20
            elif price < ema20_last and price < ema50_last:
                raw -= 20
            trend_score = int(max(-100, min(100, raw)))
        except Exception:
            trend_score = 0

        # --- Next funding settlement ---
        # One extra API call per enriched symbol (top 30 only). The endpoint
        # returns nextSettleTime as a Unix millisecond timestamp.
        next_funding_minutes = None
        try:
            fr_data = fetch_mexc(f"/contract/funding_rate/{symbol}")
            if fr_data and isinstance(fr_data, dict):
                next_settle_ms = fr_data.get("nextSettleTime")
                if next_settle_ms:
                    minutes_left = (int(next_settle_ms) / 1000 - time.time()) / 60
                    next_funding_minutes = max(0, int(round(minutes_left)))
        except Exception:
            next_funding_minutes = None

        # --- Orderbook imbalance ---
        imbalance = 0.5  # neutral default if depth fetch fails
        depth_data = fetch_mexc(f"/contract/depth/{symbol}")
        if depth_data and isinstance(depth_data, dict):
            asks = depth_data.get("asks", [])[:10]
            bids = depth_data.get("bids", [])[:10]
            ask_vol = _parse_depth_volume(asks)
            bid_vol = _parse_depth_volume(bids)
            total = bid_vol + ask_vol
            if total > 0:
                imbalance = bid_vol / total

        # --- Strategy stage-2 filter: RSI extremes (mean_reversion only) ---
        # Applied after RSI is computed but before any conviction adjustment.
        # Mean reversion only makes sense when price is already stretched.
        strat_filters = strat.get("filters", {})
        if "rsi_long_max" in strat_filters and direction == "LONG":
            if last_rsi >= strat_filters["rsi_long_max"]:
                return None
        if "rsi_short_min" in strat_filters and direction == "SHORT":
            if last_rsi <= strat_filters["rsi_short_min"]:
                return None

        # --- Conviction adjustments ---
        conviction = base["conviction_base"]

        if direction == "LONG":
            if last_rsi < 40:
                conviction += 10
                tags.append("oversold")
            elif last_rsi > 70:
                conviction -= 10
                tags.append("overbought_risk")
            if imbalance > 0.6:
                conviction += 10
                tags.append("bid_heavy")
        else:  # SHORT
            if last_rsi > 60:
                conviction += 10
                tags.append("overbought")
            elif last_rsi < 30:
                conviction -= 10
                tags.append("oversold_risk")
            if imbalance < 0.4:
                conviction += 10
                tags.append("ask_heavy")

        # Extreme volatility: high ATR% means the signal is real but the trade
        # is more dangerous — discount conviction so it ranks below calmer setups.
        if vol_regime == "extreme":
            conviction = int(conviction * 0.85)
            tags.append("extreme_vol")

        conviction = max(0, min(100, conviction))

        # --- Ladders ---
        entries, exits, stop_loss = generate_ladders(
            current_price=price,
            atr_value=atr_val,
            tiers=3,
            direction=direction,
        )

        # Build the final signal dict before calling why_signal so we can
        # pass it in with all fields populated (rsi, conviction, tags, etc.)
        final_tags = list(dict.fromkeys(tags))  # deduplicate, preserve order
        sig = {
            "symbol": symbol,
            "exchange": "MEXC",
            "direction": direction,
            "conviction": conviction,
            "price": price,
            "entries": [round(e, 8) for e in entries],
            "exits": [round(e, 8) for e in exits],
            "stop_loss": round(stop_loss, 8),
            # Momentum
            "change_24h_pct": base["change_24h_pct"],
            "change_4h_pct": change_4h_pct,
            "change_1h_pct": change_1h_pct,
            # Funding / open interest
            "funding_rate": base["funding_rate"],
            "open_interest": base["open_interest"],
            "next_funding_minutes": next_funding_minutes,
            # Volume
            "volume_24h": base["volume_24h"],
            # Technical
            "atr_pct": round(atr_pct_val, 4),
            "volatility": vol_regime,
            "rsi_1h": round(last_rsi, 2),
            "trend_score": trend_score,
            "tags": final_tags,
            "basis_pct": None,      # reserved for P1.5
            "ai_report": None,      # populated below after sig is fully built
            # Strategy context — surfaced in the UI
            "strategy": strat["name"],
            "leverage_cap": strat["leverage_cap"],
            # Market sentiment — populated below from Binance/Bybit/OKX public APIs
            "binance_ls_long_pct": None,
            "binance_oi":          None,
            "bybit_oi":            None,
            "okx_ls_long_pct":     None,
            "okx_oi":              None,
            # True when symbol is in SENTIMENT_PAIRS; False for MEXC-only altcoins.
            # Used by the frontend to distinguish "geo-blocked" from "not tracked".
            "sentiment_tracked": symbol.split("_")[0] in SENTIMENT_PAIRS,
        }
        sig["signal_why"] = why_signal(sig)
        sig["ai_report"]  = generate_report(sig)

        # Sentiment enrichment — no API key needed; per-field failures are
        # swallowed inside fetch_market_sentiment() so this never crashes a scan.
        sig.update(fetch_market_sentiment(symbol, price))

        return sig
    except Exception as e:
        print(f"enrich_signal error [{symbol}]: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_scan(
    threshold: int = CONVICTION_THRESHOLD,
    strategy_key: str = "balanced",
) -> tuple[list[dict], int]:
    """
    Two-stage scan across all MEXC perpetual tickers.

    Stage 1: Fetch all 800+ tickers, score each with score_ticker() using the
             active strategy's weights and stage-1 filters. Discard anything
             with conviction_base < 20, take the top ENRICH_TOP_N.
    Stage 2: Enrich the top N concurrently — fetches klines + depth per symbol,
             runs indicators, applies stage-2 strategy filters, builds ladders.

    The effective conviction floor is max(threshold, strategy.min_conviction)
    so each strategy's minimum bar is always respected.

    Returns (signals, total_pairs) where total_pairs is the raw ticker count.
    Signals have conviction >= effective_threshold, sorted descending.
    """
    strat = STRATEGIES.get(strategy_key, STRATEGIES["balanced"])
    effective_threshold = max(threshold, strat["min_conviction"])

    tickers = fetch_mexc("/contract/ticker")
    if not tickers or not isinstance(tickers, list):
        return [], 0

    total_pairs = len(tickers)

    # Stage 1 — strategy weights + stage-1 filters applied inside score_ticker
    base_signals: list[dict] = []
    for t in tickers:
        scored = score_ticker(t, strategy=strat)
        if scored and scored["conviction_base"] >= 20:
            base_signals.append(scored)

    base_signals.sort(key=lambda s: s["conviction_base"], reverse=True)
    top = base_signals[:ENRICH_TOP_N]

    # Stage 2 — concurrent enrichment, strategy-aware
    enrich = partial(enrich_signal, strategy=strat)
    signals: list[dict] = []
    with ThreadPoolExecutor(max_workers=ENRICH_WORKERS) as executor:
        for sig in executor.map(enrich, top):
            if sig and sig["conviction"] >= effective_threshold:
                signals.append(sig)

    signals.sort(key=lambda s: s["conviction"], reverse=True)
    return signals, total_pairs


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/scan")
def api_scan():
    try:
        threshold    = request.args.get("threshold", CONVICTION_THRESHOLD, type=int)
        strategy_key = request.args.get("strategy", "balanced")
        if strategy_key not in STRATEGIES:
            strategy_key = "balanced"
        signals, total_pairs = run_scan(threshold=threshold, strategy_key=strategy_key)
        strat = STRATEGIES[strategy_key]
        log_signals(signals)
        return jsonify({
            "success":       True,
            "signals":       signals,
            "count":         len(signals),
            "total_pairs":   total_pairs,
            "strategy":      strategy_key,
            "strategy_name": strat["name"],
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/market")
def api_market():
    try:
        tickers = fetch_mexc("/contract/ticker")
        if not tickers or not isinstance(tickers, list):
            return jsonify({"success": False, "error": "MEXC unavailable"}), 502

        pairs = []
        for t in tickers:
            scored = score_ticker(t)
            if scored:
                pairs.append(scored)

        pairs.sort(key=lambda p: p["conviction_base"], reverse=True)
        return jsonify({"success": True, "pairs": pairs, "count": len(pairs)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/signal/<symbol>")
def api_signal(symbol: str):
    """
    Return a fully-enriched signal for a single symbol. Used by the signal
    detail modal to refresh or display a specific ticker on demand.
    Fetches all tickers and finds the matching one — MEXC's public ticker
    endpoint doesn't support single-symbol filtering reliably.
    """
    try:
        tickers = fetch_mexc("/contract/ticker")
        if not tickers:
            return jsonify({"success": False, "error": "MEXC unavailable"}), 502

        ticker = next(
            (t for t in tickers if t.get("symbol") == symbol.upper()), None
        )
        if not ticker:
            return jsonify({"success": False, "error": f"Symbol {symbol!r} not found"}), 404

        base = score_ticker(ticker)
        if not base:
            return jsonify({"success": False, "error": "Unable to score ticker"}), 422

        signal = enrich_signal(base)
        if not signal:
            return jsonify({"success": False, "error": "Insufficient kline data"}), 422

        return jsonify({"success": True, "signal": signal})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Signal history routes
# ---------------------------------------------------------------------------

@app.route("/api/signal/result", methods=["PATCH"])
def api_signal_result():
    """Mark a logged signal with a trade outcome."""
    try:
        body   = request.get_json(force=True)
        sig_id = int(body["id"])
        result = body.get("result", "").upper()
        note   = body.get("result_note", "")
        valid  = {"WIN", "LOSS", "PARTIAL", "EXPIRED", "SKIPPED"}
        if result not in valid:
            return jsonify({"success": False, "error": f"result must be one of {valid}"}), 400
        con = sqlite3.connect(DB_PATH)
        con.execute("""
            UPDATE signals
            SET result=?, result_note=?, result_at=?
            WHERE id=?
        """, (result, note, datetime.utcnow().isoformat(), sig_id))
        con.commit()
        row = con.execute("SELECT * FROM signals WHERE id=?", (sig_id,)).fetchone()
        con.close()
        return jsonify({"success": True, "signal": row})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/signals/history")
def api_signals_history():
    """Return logged signal history with optional filters."""
    try:
        limit    = request.args.get("limit",    100,  type=int)
        strategy = request.args.get("strategy", None)
        result   = request.args.get("result",   None)
        symbol   = request.args.get("symbol",   None)

        query  = "SELECT * FROM signals WHERE 1=1"
        params: list = []
        if strategy:
            query += " AND strategy=?"; params.append(strategy)
        if result:
            query += " AND result=?";   params.append(result.upper())
        if symbol:
            query += " AND symbol=?";   params.append(symbol.upper())
        query += " ORDER BY logged_at DESC LIMIT ?"
        params.append(limit)

        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        rows = con.execute(query, params).fetchall()
        con.close()
        return jsonify({
            "success": True,
            "signals": [dict(r) for r in rows],
            "count":   len(rows),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def find_free_port(preferred: int, max_tries: int = 5) -> int:
    """
    Return the first available port starting at preferred, trying up to
    max_tries consecutive ports. Binds to 0.0.0.0 to match Flask's binding
    behavior — a port that's free on loopback only could still fail when Flask
    binds to all interfaces. Returns preferred if none are free (Flask will
    then surface its own error).
    """
    for port in range(preferred, preferred + max_tries):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("0.0.0.0", port))
            return port
        except OSError:
            continue
    return preferred


if __name__ == "__main__":
    try:
        # SOCK_DGRAM connect trick: doesn't send packets, just resolves the
        # outbound interface so we get the real LAN IP rather than 127.0.0.1.
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]
        s.close()
    except Exception:
        lan_ip = "127.0.0.1"

    actual_port = find_free_port(PORT)
    if actual_port != PORT:
        print(f"Note: port {PORT} was busy, using {actual_port} instead.")

    print("=" * 50)
    print("  Matrix Trader 7.0")
    print(f"  Local  → http://localhost:{actual_port}")
    print(f"  iPhone → http://{lan_ip}:{actual_port}")
    print("=" * 50)

    init_db()
    app.run(host="0.0.0.0", port=actual_port, debug=False)
