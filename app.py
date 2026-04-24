import os
import sys
import json
import socket
import sqlite3
import time
import threading
import copy
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import partial

import requests
import pandas as pd
from collections import defaultdict
from flask import Flask, Response, jsonify, render_template, request, stream_with_context
from dotenv import load_dotenv

from lib.indicators import (
    rsi as calc_rsi,
    ema as calc_ema,
    atr as calc_atr,
    atr_pct as calc_atr_pct,
    volatility_regime,
    daily_trend_direction,
)
from lib.laddering import generate_ladders
from lib.ai_client import call_ai

load_dotenv()

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True

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
    try:
        con.execute("ALTER TABLE signals ADD COLUMN exit_price REAL DEFAULT NULL")
        con.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        con.execute("ALTER TABLE signals ADD COLUMN entry_at TEXT DEFAULT NULL")
        con.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        con.execute("ALTER TABLE signals ADD COLUMN signal_json TEXT DEFAULT NULL")
        con.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        con.execute("ALTER TABLE signals ADD COLUMN data_quality TEXT DEFAULT NULL")
        con.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        con.execute("ALTER TABLE signals ADD COLUMN evaluation_version TEXT DEFAULT NULL")
        con.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        con.execute("ALTER TABLE signals ADD COLUMN strategy_key TEXT DEFAULT 'balanced'")
        con.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        con.execute("ALTER TABLE signals ADD COLUMN pnl_pct REAL DEFAULT NULL")
        con.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        con.execute("ALTER TABLE signals ADD COLUMN leverage REAL DEFAULT NULL")
        con.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
    # Backfill leverage from signal_json for existing rows that have it
    con.execute("""
        UPDATE signals
        SET leverage = json_extract(signal_json, '$.leverage_cap')
        WHERE leverage IS NULL AND signal_json IS NOT NULL
    """)
    con.commit()
    # Backfill strategy_key from display name for all existing rows
    for display_name, key in _STRATEGY_NAME_TO_KEY.items():
        con.execute(
            "UPDATE signals SET strategy_key=? WHERE strategy=?",
            (key, display_name),
        )
    con.commit()
    con.execute("""
        CREATE TABLE IF NOT EXISTS custom_strategies (
            key         TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            base_key    TEXT NOT NULL,
            enabled     INTEGER NOT NULL DEFAULT 1,
            config_json TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
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
            skey = sig.get("strategy_key") or strategy_name_to_key(sig.get("strategy", ""))
            leverage_val = sig.get("leverage_cap") or sig.get("max_leverage") or None
            con.execute("""
                INSERT INTO signals
                (logged_at, symbol, exchange, direction, strategy, strategy_key, conviction,
                 price, entry1, entry2, entry3, tp1, tp2, tp3, stop_loss,
                 atr_pct, volatility, funding_rate, rsi_1h, trend_score,
                 tags, signal_why, signal_json, data_quality, leverage)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                datetime.utcnow().isoformat(),
                sig["symbol"],
                sig.get("exchange", "MEXC"),
                sig["direction"],
                sig.get("strategy", ""),
                skey,
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
                json.dumps(sig, default=str),
                sig.get("data_quality") or "current",
                leverage_val,
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

_STRATEGY_NAME_TO_KEY: dict[str, str] = {
    "Balanced":           "balanced",
    "Funding Arb":        "funding_arb",
    "Momentum Breakout":  "momentum_breakout",
    "Mean Reversion":     "mean_reversion",
}


def strategy_name_to_key(name: str) -> str:
    """Convert a strategy display name to its stable key. Falls back to 'balanced'."""
    return _STRATEGY_NAME_TO_KEY.get(name, "balanced")


_STRATEGY_DESCRIPTIONS = {
    "balanced":          "General-purpose scanner for all market conditions using balanced momentum, funding, and basis weights.",
    "funding_arb":       "Targets pairs with extreme funding rates in ranging markets where one side is paying heavily.",
    "momentum_breakout": "Rides strong directional moves with volume expansion in clearly trending markets.",
    "mean_reversion":    "Fades exhausted RSI extremes, expecting price to snap back after extended one-sided moves.",
}

_STRATEGY_REGIME = {
    "balanced":          "any",
    "funding_arb":       "neutral",
    "momentum_breakout": "bull",
    "mean_reversion":    "any",
}

_CUSTOM_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


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


def slugify_strategy_key(name: str) -> str:
    """Build a stable custom strategy key from a display name."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)
    if not slug or not slug[0].isalpha():
        slug = f"strategy_{slug}" if slug else "custom_strategy"
    return f"custom_{slug}"[:64]


def clone_strategy_config(cfg: dict) -> dict:
    """Deep-copy a strategy config so request edits cannot mutate globals."""
    return copy.deepcopy(cfg)


def builtin_strategy_config(key: str, cfg: dict) -> dict:
    out = clone_strategy_config(cfg)
    out["key"] = key
    out["is_custom"] = False
    out["enabled"] = True
    out["base_key"] = key
    out["regime"] = _STRATEGY_REGIME.get(key, "any")
    out["description"] = _STRATEGY_DESCRIPTIONS.get(key, out.get("description", ""))
    return out


def load_custom_strategy_rows(include_disabled: bool = False) -> list[dict]:
    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        query = "SELECT * FROM custom_strategies"
        if not include_disabled:
            query += " WHERE enabled=1"
        query += " ORDER BY created_at ASC"
        rows = [dict(r) for r in con.execute(query).fetchall()]
        con.close()
        return rows
    except Exception as e:
        print(f"load_custom_strategy_rows error: {e}", file=sys.stderr)
        return []


def custom_row_to_strategy(row: dict) -> dict | None:
    try:
        cfg = json.loads(row.get("config_json") or "{}")
        cfg["key"] = row["key"]
        cfg["name"] = row["name"]
        cfg["base_key"] = row.get("base_key") or "balanced"
        cfg["enabled"] = bool(row.get("enabled"))
        cfg["is_custom"] = True
        cfg.setdefault("description", f"Custom clone of {cfg['base_key']}")
        cfg.setdefault("risk_level", "medium")
        cfg.setdefault("regime", "any")
        cfg.setdefault("filters", {})
        return cfg
    except Exception as e:
        print(f"custom_row_to_strategy error [{row.get('key', '?')}]: {e}", file=sys.stderr)
        return None


def get_strategy_registry(include_disabled: bool = False) -> dict[str, dict]:
    registry = {
        key: builtin_strategy_config(key, cfg)
        for key, cfg in STRATEGIES.items()
    }
    for row in load_custom_strategy_rows(include_disabled=include_disabled):
        cfg = custom_row_to_strategy(row)
        if cfg:
            registry[cfg["key"]] = cfg
    return registry


def get_strategy_config(strategy_key: str, include_disabled: bool = False) -> dict:
    registry = get_strategy_registry(include_disabled=include_disabled)
    return registry.get(strategy_key, registry["balanced"])


def validate_custom_strategy_payload(
    payload: dict,
    existing_key: str | None = None,
) -> tuple[dict | None, str | None]:
    registry = get_strategy_registry(include_disabled=True)
    base_key = payload.get("base_key") or payload.get("base") or "balanced"
    if base_key not in STRATEGIES:
        return None, "base_key must be one of the built-in strategies"

    base = clone_strategy_config(STRATEGIES[base_key])
    name = str(payload.get("name") or base["name"] + " Copy").strip()
    if not name:
        return None, "name is required"
    if len(name) > 80:
        return None, "name must be 80 characters or fewer"

    key = str(payload.get("key") or existing_key or slugify_strategy_key(name)).strip().lower()
    if not _CUSTOM_KEY_RE.match(key):
        return None, "key must be 3-64 chars: lowercase letters, numbers, underscores; start with a letter"
    if key in STRATEGIES:
        return None, "custom strategy key cannot collide with a built-in strategy"
    if existing_key is None and key in registry:
        return None, "custom strategy key already exists"
    if existing_key is not None and key != existing_key:
        return None, "strategy key cannot be changed after creation"

    weights_in = payload.get("weights") or {}
    weights = clone_strategy_config(base["weights"])
    for api_key, cfg_key in {
        "momentum": "momentum",
        "funding": "funding",
        "basis": "basis",
        "volume": "volume_mult",
        "volume_mult": "volume_mult",
    }.items():
        if api_key in weights_in:
            try:
                val = float(weights_in[api_key])
            except (TypeError, ValueError):
                return None, f"weights.{api_key} must be numeric"
            if cfg_key == "volume_mult":
                if val < 0.5 or val > 2.0:
                    return None, "volume multiplier must be between 0.5 and 2.0"
                weights[cfg_key] = round(val, 2)
            else:
                if val < 0 or val > 100:
                    return None, f"weights.{api_key} must be between 0 and 100"
                weights[cfg_key] = int(round(val))

    min_conviction = payload.get("min_conviction", base["min_conviction"])
    try:
        min_conviction = int(min_conviction)
    except (TypeError, ValueError):
        return None, "min_conviction must be an integer"
    if min_conviction < 0 or min_conviction > 100:
        return None, "min_conviction must be between 0 and 100"

    leverage_cap = payload.get("leverage_cap", payload.get("max_leverage", base["leverage_cap"]))
    try:
        leverage_cap = int(leverage_cap)
    except (TypeError, ValueError):
        return None, "leverage_cap must be an integer"
    if leverage_cap < 1 or leverage_cap > 50:
        return None, "leverage_cap must be between 1 and 50"

    filters = clone_strategy_config(base.get("filters", {}))
    if "filters" in payload:
        filters = {}
        for fkey, raw_val in (payload.get("filters") or {}).items():
            if raw_val in ("", None):
                continue
            if fkey not in {"min_funding_abs", "min_24h_change_pct", "rsi_long_max", "rsi_short_min"}:
                return None, f"unsupported filter: {fkey}"
            try:
                val = float(raw_val)
            except (TypeError, ValueError):
                return None, f"filters.{fkey} must be numeric"
            if fkey == "min_funding_abs" and (val < 0 or val > 0.01):
                return None, "min_funding_abs must be between 0 and 0.01"
            if fkey == "min_24h_change_pct" and (val < 0 or val > 50):
                return None, "min_24h_change_pct must be between 0 and 50"
            if fkey in {"rsi_long_max", "rsi_short_min"} and (val < 1 or val > 99):
                return None, f"{fkey} must be between 1 and 99"
            filters[fkey] = val

    regime = str(payload.get("regime") or _STRATEGY_REGIME.get(base_key, "any")).strip().lower()
    if regime not in {"bull", "bear", "neutral", "any"}:
        return None, "regime must be one of bull, bear, neutral, any"

    risk_level = str(payload.get("risk_level") or base.get("risk_level", "medium")).strip().lower()
    if risk_level not in {"low", "medium", "high"}:
        return None, "risk_level must be one of low, medium, high"

    description = str(payload.get("description") or f"Custom clone of {base['name']}").strip()
    if len(description) > 240:
        return None, "description must be 240 characters or fewer"

    enabled = payload.get("enabled", True)
    enabled = bool(enabled)

    config = {
        "key": key,
        "name": name,
        "description": description,
        "risk_level": risk_level,
        "weights": weights,
        "leverage_cap": leverage_cap,
        "min_conviction": min_conviction,
        "filters": filters,
        "regime": regime,
        "base_key": base_key,
        "enabled": enabled,
        "is_custom": True,
    }
    return config, None


def strategy_to_api(key: str, cfg: dict, performance: dict | None = None) -> dict:
    w = cfg["weights"]
    return {
        "key":            key,
        "name":           cfg["name"],
        "description":    cfg.get("description", ""),
        "weights": {
            "momentum": w.get("momentum", 0),
            "funding":  w.get("funding", 0),
            "basis":    w.get("basis", 0),
            "volume":   w.get("volume_mult", 1.0),
        },
        "filters":        cfg.get("filters", {}),
        "min_conviction": cfg["min_conviction"],
        "max_leverage":   cfg["leverage_cap"],
        "regime":         cfg.get("regime", "any"),
        "risk_level":     cfg.get("risk_level", "medium"),
        "is_custom":      bool(cfg.get("is_custom", False)),
        "enabled":        bool(cfg.get("enabled", True)),
        "base_key":       cfg.get("base_key", key),
        "performance":    performance or {},
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

        n1h = len(df)

        # --- Kline depth gate ---
        # Fetch 4h candles just to measure history depth; count only, not used for indicators.
        kline4h_data = fetch_mexc(f"/contract/kline/{symbol}", params={"interval": "Hour4", "limit": 50})
        n4h = 0
        if kline4h_data and isinstance(kline4h_data, dict):
            n4h = len(kline4h_data.get("close", []))

        if n1h < 50 or n4h < 20:
            print(f"[kline gate] {symbol} skipped — 1h:{n1h} 4h:{n4h}", file=sys.stderr)
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

        # --- Daily trend direction ---
        # Separate API call — failure must never abort enrichment.
        daily_trend = None
        daily_trend_aligned = None
        try:
            daily_klines = fetch_mexc(
                f"/contract/kline/{symbol}",
                params={"interval": "Day1", "limit": 30},
            )
            if daily_klines:
                dt = daily_trend_direction(daily_klines)
                daily_trend = dt
                if dt != "NEUTRAL":
                    daily_trend_aligned = (direction == dt)
        except Exception:
            pass

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
            "basis_pct": None,          # reserved for P1.5
            "daily_trend": daily_trend,
            "daily_trend_aligned": daily_trend_aligned,
            "ai_report": None,          # populated below after sig is fully built
            # Strategy context — surfaced in the UI
            "strategy": strat["name"],
            "strategy_key": strat.get("key") or strategy_name_to_key(strat["name"]),
            "strategy_is_custom": bool(strat.get("is_custom", False)),
            "strategy_config": {
                "weights": strat.get("weights", {}),
                "filters": strat.get("filters", {}),
                "min_conviction": strat.get("min_conviction"),
                "leverage_cap": strat.get("leverage_cap"),
                "base_key": strat.get("base_key"),
            },
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
            # Kline history depth measured at enrichment time
            "kline_depth_1h": n1h,
            "kline_depth_4h": n4h,
            # "low" when history is thin (50–99 1h or 20–49 4h candles); "current" otherwise
            "data_quality": "low" if (n1h < 100 or n4h < 50) else "current",
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
    expire_stale_signals()

    registry = get_strategy_registry()
    strat = registry.get(strategy_key, registry["balanced"])
    strategy_key = strat["key"]
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
        registry = get_strategy_registry()
        if strategy_key not in registry:
            strategy_key = "balanced"
        signals, total_pairs = run_scan(threshold=threshold, strategy_key=strategy_key)
        strat = registry.get(strategy_key, registry["balanced"])
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

    Strategy resolution order:
      1. ?strategy=<key> query param (explicit override)
      2. Most recent logged signal's strategy_key for this symbol (from DB)
      3. 'balanced' fallback
    """
    try:
        # Resolve which strategy to use for enrichment
        strategy_key = request.args.get("strategy", "").strip().lower()
        registry = get_strategy_registry(include_disabled=True)
        if not strategy_key or strategy_key not in registry:
            try:
                con = sqlite3.connect(DB_PATH)
                row = con.execute(
                    "SELECT strategy_key FROM signals WHERE symbol=? ORDER BY logged_at DESC LIMIT 1",
                    (symbol.upper(),),
                ).fetchone()
                con.close()
                if row and row[0] and row[0] in registry:
                    strategy_key = row[0]
                else:
                    strategy_key = "balanced"
            except Exception:
                strategy_key = "balanced"

        strat = registry.get(strategy_key, registry["balanced"])

        tickers = fetch_mexc("/contract/ticker")
        if not tickers:
            return jsonify({"success": False, "error": "MEXC unavailable"}), 502

        ticker = next(
            (t for t in tickers if t.get("symbol") == symbol.upper()), None
        )
        if not ticker:
            return jsonify({"success": False, "error": f"Symbol {symbol!r} not found"}), 404

        base = score_ticker(ticker, strategy=strat)
        if not base:
            return jsonify({"success": False, "error": "Unable to score ticker"}), 422

        signal = enrich_signal(base, strategy=strat)
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
        body        = request.get_json(force=True)
        sig_id      = int(body["id"])
        result      = body.get("result", "").upper()
        note        = body.get("result_note", "")
        exit_price  = body.get("exit_price")   # optional float
        entry_price = body.get("entry_price")  # optional float — overrides DB entry1
        valid  = {"WIN", "LOSS", "PARTIAL", "EXPIRED", "SKIPPED"}
        if result not in valid:
            return jsonify({"success": False, "error": f"result must be one of {valid}"}), 400

        # Fetch the existing row to get entry1, direction, leverage for pnl_pct
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        existing = con.execute("SELECT * FROM signals WHERE id=?", (sig_id,)).fetchone()
        con.close()
        if not existing:
            return jsonify({"success": False, "error": "Signal not found"}), 404
        existing = dict(existing)

        pnl_pct = None
        if exit_price is not None:
            exit_price = float(exit_price)
            # Use caller-supplied entry_price if provided; fall back to DB entry1
            if entry_price is not None:
                existing["entry1"] = float(entry_price)
            pnl_pct = _compute_leveraged_pnl(existing, exit_price)

        con = sqlite3.connect(DB_PATH)
        con.execute("""
            UPDATE signals
            SET result=?, result_note=?, result_at=?, exit_price=?, pnl_pct=?
            WHERE id=?
        """, (result, note, datetime.utcnow().isoformat(), exit_price, pnl_pct, sig_id))
        con.commit()
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM signals WHERE id=?", (sig_id,)).fetchone()
        con.close()
        return jsonify({"success": True, "signal": dict(row) if row else None})
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


@app.route("/api/signal/detail/<int:signal_id>")
def api_signal_detail(signal_id: int):
    """Return full trade detail + short AI coach review for a closed signal."""
    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
        con.close()

        if not row:
            return jsonify({"success": False, "error": "not found"}), 404

        sig = dict(row)

        # Duration in minutes (both timestamps are UTC ISO without Z)
        duration_minutes = None
        if sig.get("logged_at") and sig.get("result_at"):
            try:
                t0 = datetime.fromisoformat(sig["logged_at"])
                t1 = datetime.fromisoformat(sig["result_at"])
                duration_minutes = int((t1 - t0).total_seconds() / 60)
            except Exception:
                pass

        entry1     = sig.get("entry1")
        exit_price = sig.get("exit_price")
        direction  = sig.get("direction", "")

        # Prefer persisted leveraged pnl_pct; fall back to unleveraged on-the-fly
        # calculation for old rows that predate the pnl_pct column
        pnl_pct = sig.get("pnl_pct")
        if pnl_pct is None and entry1 and exit_price and entry1 != 0:
            if direction == "LONG":
                pnl_pct = round((exit_price - entry1) / entry1 * 100, 2)
            else:
                pnl_pct = round((entry1 - exit_price) / entry1 * 100, 2)

        # Tags stored as comma-separated string → list
        tags_raw  = sig.get("tags") or ""
        tags_list = [t.strip() for t in tags_raw.split(",") if t.strip()]

        # AI trade coach review — gracefully skipped if no provider key or call fails
        user_msg = (
            f"Trade review:\n"
            f"Symbol: {sig.get('symbol')} | Direction: {direction} | Strategy: {sig.get('strategy')}\n"
            f"Entry: {entry1} | Exit: {exit_price or 'unknown'} | Result: {sig.get('result')}\n"
            f"Stop: {sig.get('stop_loss')} | TP1: {sig.get('tp1')} | TP2: {sig.get('tp2')} | TP3: {sig.get('tp3')}\n"
            f"Duration: {duration_minutes or 'unknown'} minutes\n"
            f"Signal reason: {sig.get('signal_why')}\n"
            f"Tags: {tags_raw}\n"
            f"Result note: {sig.get('result_note')}\n\n"
            f"In 3-4 sentences: what likely happened in this trade, what the signal "
            f"got right or wrong, and one specific thing to watch for next time on "
            f"this type of setup."
        )
        ai_analysis = call_ai(
            system="You are a trading coach reviewing a completed trade. "
                   "Be direct and specific. No fluff. 3-4 sentences maximum.",
            user=user_msg,
            max_tokens=512,
        )

        return jsonify({
            "success":          True,
            "id":               sig["id"],
            "symbol":           sig.get("symbol"),
            "direction":        direction,
            "strategy":         sig.get("strategy"),
            "conviction":       sig.get("conviction"),
            "entry_price":      entry1,
            "exit_price":       exit_price,
            "stop_loss":        sig.get("stop_loss"),
            "tp1":              sig.get("tp1"),
            "tp2":              sig.get("tp2"),
            "tp3":              sig.get("tp3"),
            "result":           sig.get("result"),
            "result_note":      sig.get("result_note"),
            "logged_at":        sig.get("logged_at"),
            "result_at":        sig.get("result_at"),
            "duration_minutes": duration_minutes,
            "pnl_pct":          pnl_pct,
            "signal_why":       sig.get("signal_why"),
            "tags":             tags_list,
            "volatility":       sig.get("volatility"),
            "ai_analysis":      ai_analysis,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def _compute_leveraged_pnl(sig: dict, exit_price: float | None) -> float | None:
    """
    Compute leveraged P&L % for a closed trade.

    raw_pct = (exit - entry) / entry * 100  (sign-corrected per direction)
    Returns raw_pct * leverage, rounded to 2 dp.
    Returns None if exit_price or entry1 is missing.
    """
    if exit_price is None:
        return None
    entry1 = sig.get("entry1")
    if not entry1 or entry1 == 0:
        return None
    direction = sig.get("direction", "")
    leverage = sig.get("leverage")
    if leverage is None:
        try:
            sj = json.loads(sig.get("signal_json") or "{}")
            leverage = sj.get("leverage_cap")
        except Exception:
            pass
    leverage = float(leverage) if leverage else 1.0
    if direction == "LONG":
        raw_pct = (exit_price - entry1) / entry1 * 100
    else:
        raw_pct = (entry1 - exit_price) / entry1 * 100
    return round(raw_pct * leverage, 2)


def expire_stale_signals() -> int:
    """
    Tag open signals older than 80 hours as EXPIRED so they don't
    accumulate in the open-positions panel indefinitely.
    Returns the count of signals expired.
    """
    try:
        cutoff = (datetime.utcnow() - timedelta(hours=80)).isoformat()
        con = sqlite3.connect(DB_PATH)
        rows = con.execute(
            "SELECT id FROM signals WHERE result IS NULL AND logged_at < ?",
            (cutoff,),
        ).fetchall()
        if rows:
            now = datetime.utcnow().isoformat()
            for row in rows:
                con.execute(
                    """
                    UPDATE signals
                    SET result='EXPIRED', result_note='Auto-expired after 80h',
                        result_at=?, exit_price=NULL, pnl_pct=NULL
                    WHERE id=?
                    """,
                    (now, row[0]),
                )
            con.commit()
        con.close()
        return len(rows)
    except Exception as e:
        print(f"expire_stale_signals error: {e}", file=sys.stderr)
        return 0


def evaluate_outcome(sig: dict) -> tuple[str, str, float | None, str | None, str | None] | None:
    """
    Fetch Min15 klines since the signal was logged and determine if stop or TP was hit.

    Returns (result, note, exit_price, result_at, entry_at) or None if no level
    was hit yet / insufficient data.

    Evaluation rules (applied per candle in chronological order):
      - First wait for entry1 to be touched. Signals are logged at scan time, but
        the ladder entry may be away from current price.
      - Stop hit (LONG: low <= stop | SHORT: high >= stop) → LOSS (or PARTIAL if TP1 hit first)
      - TP3 fully hit → WIN
      - TP1 or TP2 hit, no stop → PARTIAL
    If TP1 was hit in an earlier candle but stop was hit later, result is PARTIAL —
    the trader would have taken partial profits before being stopped on the remainder.
    """
    symbol    = sig.get("symbol", "")
    direction = sig.get("direction", "")
    logged_at = sig.get("logged_at", "")
    stop_loss = sig.get("stop_loss")
    entry1    = sig.get("entry1")
    tp1       = sig.get("tp1")
    tp2       = sig.get("tp2")
    tp3       = sig.get("tp3")

    if not symbol or not direction or not entry1 or not stop_loss or not tp1:
        return None

    try:
        dt       = datetime.fromisoformat(logged_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        start_ts = int(dt.timestamp())
    except Exception:
        return None

    # Need at least one full 15-minute candle after the signal
    if time.time() - start_ts < 900:
        return None

    klines = fetch_mexc(f"/contract/kline/{symbol}", params={
        "interval": "Min15",
        "start":    start_ts,
        "limit":    300,   # 300 × 15min = ~75 hours of coverage
    })
    if not klines or not isinstance(klines, dict):
        return None

    raw_times  = klines.get("time",  [])
    raw_highs  = klines.get("high",  [])
    raw_lows   = klines.get("low",   [])
    raw_closes = klines.get("close", [])
    if not raw_times or not raw_highs or not raw_lows or not raw_closes:
        return None

    # Build candle list — only candles that opened at or after signal time
    candles: list[dict] = []
    for i, t in enumerate(raw_times):
        try:
            candle_ts = int(t)
            if candle_ts >= start_ts:
                candles.append({
                    "time":  candle_ts,
                    "high":  float(raw_highs[i]),
                    "low":   float(raw_lows[i]),
                    "close": float(raw_closes[i]),
                })
        except (ValueError, IndexError):
            pass

    if not candles:
        return None

    stop_hit   = False
    best_tp    = 0              # highest TP tier reached before stop: 0=none, 1, 2, 3
    entry_hit  = False
    entry_at: str | None = None
    exit_price: float | None = None
    result_at: str | None = None

    def _iso(ts: int) -> str:
        return datetime.utcfromtimestamp(ts).isoformat()

    for c in candles:
        h, l, ts = c["high"], c["low"], c["time"]

        if not entry_hit:
            if direction == "LONG":
                entry_hit = l <= entry1
            else:
                entry_hit = h >= entry1
            if not entry_hit:
                continue
            entry_at = _iso(ts)

        if direction == "LONG":
            if l <= stop_loss:
                stop_hit   = True
                exit_price = stop_loss if best_tp == 0 else (
                    tp3 if best_tp == 3 else tp2 if best_tp == 2 else tp1
                )
                result_at = _iso(ts)
                break
            prev_tp = best_tp
            if tp3 and h >= tp3:
                best_tp = max(best_tp, 3)
            elif tp2 and h >= tp2:
                best_tp = max(best_tp, 2)
            elif tp1 and h >= tp1:
                best_tp = max(best_tp, 1)
            if best_tp > prev_tp:
                exit_price = tp3 if best_tp == 3 else tp2 if best_tp == 2 else tp1
                result_at = _iso(ts)
                if best_tp == 3:
                    break
        else:  # SHORT
            if h >= stop_loss:
                stop_hit   = True
                exit_price = stop_loss if best_tp == 0 else (
                    tp3 if best_tp == 3 else tp2 if best_tp == 2 else tp1
                )
                result_at = _iso(ts)
                break
            prev_tp = best_tp
            if tp3 and l <= tp3:
                best_tp = max(best_tp, 3)
            elif tp2 and l <= tp2:
                best_tp = max(best_tp, 2)
            elif tp1 and l <= tp1:
                best_tp = max(best_tp, 1)
            if best_tp > prev_tp:
                exit_price = tp3 if best_tp == 3 else tp2 if best_tp == 2 else tp1
                result_at = _iso(ts)
                if best_tp == 3:
                    break

    if stop_hit and best_tp == 0:
        return ("LOSS",    f"Stop hit at {stop_loss}",              exit_price, result_at, entry_at)
    if stop_hit and best_tp > 0:
        # Blended exit: ladder exits 1/3 of position at each TP hit before stop takes the rest
        if best_tp == 1:
            blended = round(tp1 * (1 / 3) + stop_loss * (2 / 3), 8)
        else:  # best_tp == 2 (TP3 + stop can't co-occur — TP3 breaks the loop)
            blended = round(tp1 * (1 / 3) + tp2 * (1 / 3) + stop_loss * (1 / 3), 8)
        return ("PARTIAL", f"TP{best_tp} hit then stopped at {stop_loss}", blended, result_at, entry_at)
    if best_tp == 3:
        return ("WIN",     f"TP3 hit at {tp3}",                    exit_price, result_at, entry_at)
    if best_tp == 2:
        return ("PARTIAL", f"TP2 hit at {tp2}",                    exit_price, result_at, entry_at)
    if best_tp == 1:
        return ("PARTIAL", f"TP1 hit at {tp1}",                    exit_price, result_at, entry_at)
    return None   # still open — no level hit yet


@app.route("/api/outcomes/check", methods=["POST"])
def api_outcomes_check():
    """
    Evaluate open positions against Min15 kline history and auto-tag any that
    have hit their stop loss or take profit levels.

    Returns a summary of how many signals were evaluated and tagged.
    Safe to call repeatedly — already-tagged signals are skipped.
    """
    expire_stale_signals()
    try:
        con  = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM signals WHERE result IS NULL ORDER BY logged_at ASC"
        ).fetchall()
        con.close()

        open_sigs = [dict(r) for r in rows]
        tagged: list[dict] = []
        skipped = 0

        for sig in open_sigs:
            outcome = evaluate_outcome(sig)
            if outcome is None:
                skipped += 1
                continue
            result, note, exit_price, result_at, entry_at = outcome
            pnl_pct = _compute_leveraged_pnl(sig, exit_price)
            con = sqlite3.connect(DB_PATH)
            con.execute(
                """
                UPDATE signals
                SET result=?, result_note=?, result_at=?, exit_price=?, entry_at=?,
                    pnl_pct=?,
                    data_quality=COALESCE(data_quality, 'current'),
                    evaluation_version=?
                WHERE id=?
                """,
                (
                    result,
                    note,
                    result_at or datetime.utcnow().isoformat(),
                    exit_price,
                    entry_at,
                    pnl_pct,
                    "entry_fill_v2",
                    sig["id"],
                ),
            )
            con.commit()
            con.close()
            tagged.append({"id": sig["id"], "symbol": sig["symbol"],
                           "direction": sig["direction"], "result": result, "note": note})

        return jsonify({
            "success": True,
            "evaluated": len(open_sigs),
            "tagged":    len(tagged),
            "skipped":   skipped,
            "results":   tagged,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/prices")
def api_prices():
    """Batch price fetch: return current prices for multiple symbols from one MEXC ticker call.
    Used by the open positions panel — avoids triggering full enrichment per symbol."""
    try:
        symbols_param = request.args.get("symbols", "")
        if not symbols_param:
            return jsonify({"success": True, "prices": {}})
        want = {s.strip().upper() for s in symbols_param.split(",") if s.strip()}
        tickers = fetch_mexc("/contract/ticker")
        if not tickers:
            return jsonify({"success": False, "error": "MEXC unavailable"}), 502
        prices = {}
        for t in tickers:
            sym = t.get("symbol", "")
            if sym in want:
                prices[sym] = float(t.get("lastPrice") or t.get("fairPrice") or 0)
        return jsonify({"success": True, "prices": prices})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/stream/prices")
def api_stream_prices():
    """SSE stream: push price updates every 3s for the requested symbols.
    Accepts ?symbols= (comma-separated). Each event: data: {"symbol": ..., "price": ...}"""
    symbols_param = request.args.get("symbols", "")
    want = {s.strip().upper() for s in symbols_param.split(",") if s.strip()}

    def generate():
        try:
            while True:
                if want:
                    tickers = fetch_mexc("/contract/ticker")
                    if tickers:
                        for t in tickers:
                            sym = t.get("symbol", "")
                            if sym in want:
                                price = float(t.get("lastPrice") or t.get("fairPrice") or 0)
                                yield f"data: {json.dumps({'symbol': sym, 'price': price})}\n\n"
                time.sleep(3)
        except GeneratorExit:
            pass

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/strategies")
def api_strategies():
    """Return all strategy configs as a JSON array for dynamic frontend rendering.
    Includes live performance stats queried from the signals DB."""
    include_disabled = request.args.get("include_disabled", "0") in {"1", "true", "yes"}
    registry = get_strategy_registry(include_disabled=include_disabled)

    # Fetch performance stats for all strategies in one DB connection
    perf: dict[str, dict] = {}
    try:
        con = sqlite3.connect(DB_PATH)
        for key in registry:
            row = con.execute(
                "SELECT COUNT(*), "
                "SUM(CASE WHEN result='WIN'  THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END), "
                "AVG(conviction) "
                "FROM signals WHERE strategy_key=?",
                (key,),
            ).fetchone()
            total    = int(row[0] or 0)
            wins     = int(row[1] or 0)
            losses   = int(row[2] or 0)
            avg_conv = row[3]
            wr       = wins / (wins + losses) if (wins + losses) > 0 else None
            top_row  = con.execute(
                "SELECT symbol FROM signals WHERE strategy_key=? "
                "GROUP BY symbol ORDER BY COUNT(*) DESC LIMIT 1",
                (key,),
            ).fetchone()
            perf[key] = {
                "total_signals":  total,
                "wins":           wins,
                "losses":         losses,
                "win_rate":       round(wr, 4) if wr is not None else None,
                "avg_conviction": round(avg_conv, 1) if avg_conv is not None else None,
                "top_symbol":     top_row[0] if top_row else None,
            }
        con.close()
    except Exception as e:
        print(f"api_strategies perf query error: {e}", file=sys.stderr)
        for key in registry:
            if key not in perf:
                perf[key] = {
                    "total_signals": 0, "wins": 0, "losses": 0,
                    "win_rate": None, "avg_conviction": None, "top_symbol": None,
                }

    result = [
        strategy_to_api(key, cfg, performance=perf.get(key, {}))
        for key, cfg in registry.items()
        if cfg.get("enabled", True) or include_disabled
    ]
    return jsonify({"success": True, "strategies": result})


@app.route("/api/strategies/custom", methods=["POST"])
def api_create_custom_strategy():
    """Create a custom strategy by cloning a built-in and applying validated overrides."""
    try:
        body = request.get_json(force=True) or {}
        cfg, err = validate_custom_strategy_payload(body)
        if err:
            return jsonify({"success": False, "error": err}), 400

        now = datetime.utcnow().isoformat()
        row_json = json.dumps({
            "description": cfg["description"],
            "risk_level": cfg["risk_level"],
            "weights": cfg["weights"],
            "leverage_cap": cfg["leverage_cap"],
            "min_conviction": cfg["min_conviction"],
            "filters": cfg["filters"],
            "regime": cfg["regime"],
        }, sort_keys=True)

        con = sqlite3.connect(DB_PATH)
        con.execute("""
            INSERT INTO custom_strategies
            (key, name, base_key, enabled, config_json, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?)
        """, (
            cfg["key"], cfg["name"], cfg["base_key"], 1 if cfg["enabled"] else 0,
            row_json, now, now,
        ))
        con.commit()
        con.close()
        return jsonify({"success": True, "strategy": strategy_to_api(cfg["key"], cfg)})
    except sqlite3.IntegrityError:
        return jsonify({"success": False, "error": "custom strategy key already exists"}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/strategies/custom/<strategy_key>", methods=["PATCH"])
def api_update_custom_strategy(strategy_key: str):
    """Update a custom strategy config. Built-in strategies are immutable."""
    try:
        key = strategy_key.strip().lower()
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM custom_strategies WHERE key=?", (key,)).fetchone()
        con.close()
        if not row:
            return jsonify({"success": False, "error": "custom strategy not found"}), 404

        existing = custom_row_to_strategy(dict(row))
        if not existing:
            return jsonify({"success": False, "error": "stored custom strategy is invalid"}), 500

        body = request.get_json(force=True) or {}
        merged = {
            "key": key,
            "name": existing["name"],
            "base_key": existing["base_key"],
            "enabled": existing["enabled"],
            "description": existing.get("description", ""),
            "risk_level": existing.get("risk_level", "medium"),
            "weights": {
                "momentum": existing["weights"].get("momentum", 0),
                "funding": existing["weights"].get("funding", 0),
                "basis": existing["weights"].get("basis", 0),
                "volume": existing["weights"].get("volume_mult", 1.0),
            },
            "filters": existing.get("filters", {}),
            "min_conviction": existing["min_conviction"],
            "leverage_cap": existing["leverage_cap"],
            "regime": existing.get("regime", "any"),
        }
        for field in ("name", "enabled", "description", "risk_level", "weights", "filters", "min_conviction", "leverage_cap", "max_leverage", "regime"):
            if field in body:
                merged[field] = body[field]

        cfg, err = validate_custom_strategy_payload(merged, existing_key=key)
        if err:
            return jsonify({"success": False, "error": err}), 400

        row_json = json.dumps({
            "description": cfg["description"],
            "risk_level": cfg["risk_level"],
            "weights": cfg["weights"],
            "leverage_cap": cfg["leverage_cap"],
            "min_conviction": cfg["min_conviction"],
            "filters": cfg["filters"],
            "regime": cfg["regime"],
        }, sort_keys=True)

        con = sqlite3.connect(DB_PATH)
        con.execute("""
            UPDATE custom_strategies
            SET name=?, enabled=?, config_json=?, updated_at=?
            WHERE key=?
        """, (
            cfg["name"], 1 if cfg["enabled"] else 0, row_json,
            datetime.utcnow().isoformat(), key,
        ))
        con.commit()
        con.close()
        return jsonify({"success": True, "strategy": strategy_to_api(key, cfg)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/strategies/custom/<strategy_key>", methods=["DELETE"])
def api_delete_custom_strategy(strategy_key: str):
    """Delete a custom strategy definition. Historical signals remain intact."""
    try:
        key = strategy_key.strip().lower()
        if key in STRATEGIES:
            return jsonify({"success": False, "error": "built-in strategies cannot be deleted"}), 400
        con = sqlite3.connect(DB_PATH)
        cur = con.execute("DELETE FROM custom_strategies WHERE key=?", (key,))
        con.commit()
        con.close()
        if cur.rowcount == 0:
            return jsonify({"success": False, "error": "custom strategy not found"}), 404
        return jsonify({"success": True, "deleted": key})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/analysis", methods=["POST"])
def api_analysis():
    """AI strategy review: analyse tagged signal outcomes via available AI provider."""
    print("AI strategy review requested", file=sys.stderr)
    try:
        # Load last 200 tagged signals
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM signals WHERE result IS NOT NULL ORDER BY logged_at DESC LIMIT 200"
        ).fetchall()
        con.close()
        sigs = [dict(r) for r in rows]

        if len(sigs) < 10:
            return jsonify({
                "success": False,
                "error": "Not enough data — need at least 10 tagged outcomes to generate a review",
            }), 400

        # ── helper fns ──────────────────────────────────────────────────────
        def win_rate_str(lst: list) -> str:
            wins = sum(1 for s in lst if s["result"] == "WIN")
            return f"{wins / len(lst) * 100:.0f}%" if lst else "n/a"

        def avg_conv(lst: list) -> str:
            return f"{sum(s['conviction'] for s in lst) / len(lst):.0f}" if lst else "n/a"

        # ── by strategy ─────────────────────────────────────────────────────
        by_strat: dict[str, list] = defaultdict(list)
        for s in sigs:
            by_strat[s["strategy"]].append(s)

        def avg_pnl(lst: list) -> str:
            vals = [s["pnl_pct"] for s in lst if s.get("pnl_pct") is not None]
            return f"{sum(vals) / len(vals):.1f}%" if vals else "n/a"

        strat_lines = []
        for strat_name, ss in by_strat.items():
            winners = [s for s in ss if s["result"] == "WIN"]
            losers  = [s for s in ss if s["result"] == "LOSS"]
            strat_lines.append(
                f"  {strat_name}: {len(ss)} signals, {win_rate_str(ss)} win rate, "
                f"avg conv winners={avg_conv(winners)} losers={avg_conv(losers)}, "
                f"avg pnl winners={avg_pnl(winners)} losers={avg_pnl(losers)}"
            )
        strat_block = "\n".join(strat_lines) or "  (no data)"

        # ── conviction bands ────────────────────────────────────────────────
        band_55 = [s for s in sigs if 55 <= s["conviction"] < 65]
        band_65 = [s for s in sigs if 65 <= s["conviction"] < 75]
        band_75 = [s for s in sigs if s["conviction"] >= 75]

        # ── tag analysis ────────────────────────────────────────────────────
        tag_wins:  dict[str, int] = defaultdict(int)
        tag_total: dict[str, int] = defaultdict(int)
        for s in sigs:
            for tag in [t.strip() for t in (s.get("tags") or "").split(",") if t.strip()]:
                tag_total[tag] += 1
                if s["result"] == "WIN":
                    tag_wins[tag] += 1

        tag_stats = sorted(
            [(t, tag_wins[t], tag_total[t]) for t in tag_total if tag_total[t] >= 5],
            key=lambda x: x[1] / x[2],
            reverse=True,
        )

        def tag_line(t: str, w: int, n: int) -> str:
            return f"  {t}: {w}/{n} ({w / n * 100:.0f}% win rate)"

        best_tags_block  = "\n".join(tag_line(*x) for x in tag_stats[:5])  or "  (fewer than 5 signals per tag)"
        worst_tags_block = "\n".join(tag_line(*x) for x in tag_stats[-5:]) or "  (fewer than 5 signals per tag)"

        # ── symbol analysis ─────────────────────────────────────────────────
        sym_wins:  dict[str, int] = defaultdict(int)
        sym_total: dict[str, int] = defaultdict(int)
        for s in sigs:
            sym_total[s["symbol"]] += 1
            if s["result"] == "WIN":
                sym_wins[s["symbol"]] += 1

        sym_stats = sorted(
            [(sym, sym_wins[sym], sym_total[sym]) for sym in sym_total if sym_total[sym] >= 3],
            key=lambda x: x[1] / x[2],
            reverse=True,
        )

        def sym_line(sym: str, w: int, n: int) -> str:
            return f"  {sym}: {w}/{n} ({w / n * 100:.0f}% win rate)"

        best_syms_block  = "\n".join(sym_line(*x) for x in sym_stats[:5])  or "  (fewer than 3 signals per symbol)"
        worst_syms_block = "\n".join(sym_line(*x) for x in sym_stats[-5:]) or "  (fewer than 3 signals per symbol)"

        # ── full log (max 100 most recent) ───────────────────────────────────
        log_lines = [
            f"  {s['logged_at'][:16]} | {s['symbol']} | {s['direction']} | "
            f"{s['strategy']} | conviction:{s['conviction']} | "
            f"tags:[{s.get('tags', '')}] | result:{s['result']} | "
            f"pnl:{s['pnl_pct']}%" if s.get('pnl_pct') is not None
            else f"  {s['logged_at'][:16]} | {s['symbol']} | {s['direction']} | "
            f"{s['strategy']} | conviction:{s['conviction']} | "
            f"tags:[{s.get('tags', '')}] | result:{s['result']}"
            for s in sigs[:100]
        ]
        log_block = "\n".join(log_lines)

        # ── assemble user message ────────────────────────────────────────────
        user_msg = (
            f"Here is the signal performance data from the last {len(sigs)} tagged trades:\n\n"
            f"SUMMARY BY STRATEGY:\n{strat_block}\n\n"
            f"SUMMARY BY CONVICTION BAND:\n"
            f"  55-64: {len(band_55)} signals, {win_rate_str(band_55)} win rate\n"
            f"  65-74: {len(band_65)} signals, {win_rate_str(band_65)} win rate\n"
            f"  75+:   {len(band_75)} signals, {win_rate_str(band_75)} win rate\n\n"
            f"BEST PERFORMING TAGS (win rate across all signals containing this tag):\n{best_tags_block}\n\n"
            f"WORST PERFORMING TAGS:\n{worst_tags_block}\n\n"
            f"BEST PERFORMING SYMBOLS:\n{best_syms_block}\n\n"
            f"WORST PERFORMING SYMBOLS:\n{worst_syms_block}\n\n"
            f"FULL SIGNAL LOG (most recent first, max 100):\n{log_block}"
        )

        system_prompt = (
            "You are a quantitative trading strategy analyst reviewing signal performance data "
            "from a MEXC perpetual swap scanner called Matrix Trader. The scanner scores signals "
            "using funding rate, momentum, orderbook pressure, RSI, and ATR. Your job is to "
            "identify what is working, what is not, and give specific actionable recommendations "
            "to improve the scoring weights and strategy parameters. Be direct and specific. "
            "Reference actual numbers from the data. Flag any recommendation with fewer than 8 "
            "supporting signals as low-confidence."
        )

        analysis_text = call_ai(system=system_prompt, user=user_msg, max_tokens=2048)
        if analysis_text is None:
            return jsonify({"success": False, "error": "No AI provider available — check API keys"}), 400

        return jsonify({"success": True, "analysis": analysis_text})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _outcome_loop():
    import time as _time
    _time.sleep(60)  # wait 1 minute after startup
    while True:
        try:
            with app.app_context():
                api_outcomes_check()
                print("Outcome checker ran automatically", file=sys.stderr)
        except Exception as e:
            print(f"Outcome checker error: {e}", file=sys.stderr)
        _time.sleep(900)  # 15 minutes

_outcome_thread = threading.Thread(target=_outcome_loop, daemon=True)
_outcome_thread.start()


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

    print("=" * 50)
    print("  Matrix Trader 7.0")
    print(f"  Local  → http://localhost:{PORT}")
    print(f"  iPhone → http://{lan_ip}:{PORT}")
    print("=" * 50)

    init_db()
    app.run(host="0.0.0.0", port=PORT, debug=False)
