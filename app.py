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
from lib.coinglass_client import (
    coinglass_enabled,
    enrich_symbol_with_coinglass,
    get_coin_market_snapshot,
    get_symbol_derivatives_context,
)
from lib.hyperliquid_client import (
    fetch_hl_meta_and_ctxs,
    fetch_hl_klines,
    fetch_hl_orderbook,
    fetch_hl_account,
    normalize_hl_tickers,
)

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
HL_WALLET_ADDRESS = os.getenv("HL_WALLET_ADDRESS", "")
CONVICTION_THRESHOLD = 55   # signals below this are filtered from results
KLINE_INTERVAL = "Min60"    # 1h candles — 100 candles default = ~4 days, plenty for 14-period indicators
ENRICH_TOP_N = 30           # enrich only the top N base signals to limit API calls
ENRICH_WORKERS = 10         # concurrent threads for stage-2 enrichment
DB_PATH = "data/signals.db"
RISK_GATES_PATH = "data/risk_gates.json"

DEFAULT_RISK_GATES = {
    "long_vol_long": {
        "key": "long_vol_long",
        "name": "High-volatility LONG circuit breaker",
        "mode": "block",  # block | shadow | off
        "direction": "LONG",
        "volatility": ["high", "extreme"],
        "description": (
            "Blocks or shadows LONG candidates in high/extreme ATR regimes. "
            "Created from the April 25 audit where these signals caused the "
            "largest drawdown cluster while SHORTs in the same regimes stayed useful."
        ),
    },
    "short_vol_short": {
        "key": "short_vol_short",
        "name": "Extreme-volatility SHORT circuit breaker (Balanced)",
        "mode": "shadow",  # shadow for one week, promote to block if pattern holds
        "direction": "SHORT",
        "volatility": ["extreme"],
        "strategy_scope": "balanced",
        "description": (
            "Shadows Balanced SHORT candidates in extreme ATR regimes. "
            "April 26 audit: Balanced SHORT extreme avg −34.2 pnl_pct over 25 trades, "
            "8% win rate. Momentum SHORT extreme was +53.3 avg (1 outlier win in 14) — "
            "left open pending more data. Default SHADOW for one week; promote to BLOCK "
            "if pattern holds. Only fires for strategies that set block_short_volatility."
        ),
    },
}

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
        CREATE INDEX IF NOT EXISTS idx_signals_open_dedupe
        ON signals (symbol, direction, strategy_key, result)
    """)
    con.commit()
    con.execute("""
        CREATE TABLE IF NOT EXISTS position_events (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id          INTEGER NOT NULL,
            event_type         TEXT NOT NULL,
            event_at           TEXT NOT NULL,
            price              REAL,
            realized_pct       REAL DEFAULT 0,
            remaining_size_pct REAL DEFAULT 100,
            note               TEXT,
            created_at         TEXT NOT NULL,
            UNIQUE(signal_id, event_type)
        )
    """)
    con.commit()
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_position_events_signal
        ON position_events (signal_id, event_at)
    """)
    con.commit()
    con.execute("""
        CREATE TABLE IF NOT EXISTS filtered_candidates (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            logged_at    TEXT NOT NULL,
            gate_key     TEXT DEFAULT 'long_vol_long',
            gate_mode    TEXT DEFAULT 'block',
            symbol       TEXT NOT NULL,
            exchange     TEXT NOT NULL,
            direction    TEXT NOT NULL,
            strategy     TEXT NOT NULL,
            strategy_key TEXT NOT NULL,
            conviction   INTEGER,
            price        REAL,
            atr_pct      REAL,
            volatility   TEXT,
            funding_rate REAL,
            tags         TEXT,
            reason       TEXT NOT NULL,
            signal_json  TEXT
        )
    """)
    con.commit()
    try:
        con.execute("ALTER TABLE filtered_candidates ADD COLUMN gate_key TEXT DEFAULT 'long_vol_long'")
        con.commit()
    except sqlite3.OperationalError:
        pass
    try:
        con.execute("ALTER TABLE filtered_candidates ADD COLUMN gate_mode TEXT DEFAULT 'block'")
        con.commit()
    except sqlite3.OperationalError:
        pass
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_filtered_candidates_lookup
        ON filtered_candidates (strategy_key, direction, volatility, logged_at)
    """)
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
    Duplicate guard: skip if an open (result IS NULL) signal already exists
    for the same symbol + direction + strategy_key. Different strategies may
    each hold the same symbol simultaneously — that is intentional.
    """
    try:
        con = sqlite3.connect(DB_PATH)
        for sig in signals:
            entries = sig.get("entries") or [None, None, None]
            exits   = sig.get("exits")   or [None, None, None]
            skey = sig.get("strategy_key") or strategy_name_to_key(sig.get("strategy", ""))
            exists = con.execute("""
                SELECT 1 FROM signals
                WHERE symbol=? AND direction=? AND strategy_key=? AND result IS NULL
                LIMIT 1
            """, (sig["symbol"], sig["direction"], skey)).fetchone()
            if exists:
                continue
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


def load_risk_gates() -> dict:
    """Load local risk-gate configuration from data/risk_gates.json."""
    gates = copy.deepcopy(DEFAULT_RISK_GATES)
    try:
        if os.path.exists(RISK_GATES_PATH):
            with open(RISK_GATES_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for key, cfg in (raw or {}).items():
                if key in gates and isinstance(cfg, dict):
                    gates[key].update(cfg)
    except Exception as e:
        print(f"load_risk_gates error: {e}", file=sys.stderr)

    for key, cfg in gates.items():
        cfg["key"] = key
        if cfg.get("mode") not in {"block", "shadow", "off"}:
            cfg["mode"] = DEFAULT_RISK_GATES[key]["mode"]
        vols = cfg.get("volatility") or DEFAULT_RISK_GATES[key]["volatility"]
        cfg["volatility"] = [str(v) for v in vols if str(v) in {"low", "medium", "high", "extreme"}]
        if not cfg["volatility"]:
            cfg["volatility"] = list(DEFAULT_RISK_GATES[key]["volatility"])
    return gates


def save_risk_gates(gates: dict) -> None:
    os.makedirs("data", exist_ok=True)
    # Preserve non-gate keys (e.g. disabled_builtins) already in the file.
    try:
        if os.path.exists(RISK_GATES_PATH):
            with open(RISK_GATES_PATH, "r", encoding="utf-8") as f:
                existing = json.load(f)
        else:
            existing = {}
    except Exception:
        existing = {}
    merged = {k: v for k, v in existing.items() if k not in DEFAULT_RISK_GATES}
    merged.update(gates)
    with open(RISK_GATES_PATH, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, sort_keys=True)


def get_risk_gate(gate_key: str) -> dict:
    return load_risk_gates()[gate_key]


def get_long_vol_gate() -> dict:
    return get_risk_gate("long_vol_long")


def log_filtered_candidate(candidate: dict) -> None:
    """Shadow-log a candidate blocked by a risk gate. Never crash scanning."""
    try:
        con = sqlite3.connect(DB_PATH, timeout=1)
        con.execute("""
            INSERT INTO filtered_candidates
            (logged_at, gate_key, gate_mode, symbol, exchange, direction, strategy, strategy_key, conviction,
             price, atr_pct, volatility, funding_rate, tags, reason, signal_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            datetime.utcnow().isoformat(),
            candidate.get("gate_key", "long_vol_long"),
            candidate.get("gate_mode", "block"),
            candidate.get("symbol", ""),
            candidate.get("exchange", "MEXC"),
            candidate.get("direction", ""),
            candidate.get("strategy", ""),
            candidate.get("strategy_key", ""),
            candidate.get("conviction"),
            candidate.get("price"),
            candidate.get("atr_pct"),
            candidate.get("volatility"),
            candidate.get("funding_rate"),
            ",".join(candidate.get("tags", [])),
            candidate.get("reason", "risk_gate"),
            json.dumps(candidate, default=str),
        ))
        con.commit()
        con.close()
    except Exception as e:
        print(f"log_filtered_candidate error: {e}", file=sys.stderr)


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
        "risk_gates": {
            "block_short_volatility": ["extreme"],
        },
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


def _get_disabled_builtins() -> set:
    """Return the set of built-in strategy keys that have been disabled."""
    try:
        with open(RISK_GATES_PATH, "r") as f:
            data = json.load(f)
        return set(data.get("disabled_builtins", []))
    except Exception:
        return set()


def _set_disabled_builtins(disabled: set) -> None:
    """Write the disabled_builtins set into risk_gates.json, preserving all other keys."""
    os.makedirs("data", exist_ok=True)
    try:
        if os.path.exists(RISK_GATES_PATH):
            with open(RISK_GATES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {}
    except Exception:
        data = {}
    data["disabled_builtins"] = sorted(disabled)
    with open(RISK_GATES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def _get_symbol_overrides() -> dict:
    """Return the symbol_overrides dict from risk_gates.json. Returns {} on any error."""
    try:
        with open(RISK_GATES_PATH, "r") as f:
            data = json.load(f)
        return data.get("symbol_overrides", {})
    except Exception:
        return {}


def _load_symbol_performance_cache(min_trades: int = 5) -> dict:
    """
    Loads historical avg pnl_pct per symbol from signals.db.
    Returns {symbol: {avg_pnl, trade_count}} for symbols with >= min_trades closed trades.
    Called once per scan at the start of run_scan(). Returns {} on any error.
    """
    try:
        con = sqlite3.connect(DB_PATH)
        rows = con.execute('''
            SELECT symbol,
                   COUNT(*) as n,
                   ROUND(AVG(pnl_pct), 4) as avg_pnl
            FROM signals
            WHERE result IS NOT NULL
            AND pnl_pct IS NOT NULL
            AND result NOT IN ('EXPIRED', 'SKIPPED')
            GROUP BY symbol
            HAVING COUNT(*) >= ?
        ''', (min_trades,)).fetchall()
        con.close()
        return {r[0]: {'avg_pnl': r[2], 'trade_count': r[1]} for r in rows}
    except Exception as e:
        print(f'[sym_penalty] cache load error: {e}', file=sys.stderr)
        return {}


def builtin_strategy_config(key: str, cfg: dict) -> dict:
    out = clone_strategy_config(cfg)
    out["key"] = key
    out["is_custom"] = False
    out["base_key"] = key
    out["regime"] = _STRATEGY_REGIME.get(key, "any")
    out["description"] = _STRATEGY_DESCRIPTIONS.get(key, out.get("description", ""))
    disabled_builtins = _get_disabled_builtins()
    out["enabled"] = key not in disabled_builtins
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
    registry = {}
    for key, cfg in STRATEGIES.items():
        built = builtin_strategy_config(key, cfg)
        if include_disabled or built["enabled"]:
            registry[key] = built
    for row in load_custom_strategy_rows(include_disabled=include_disabled):
        cfg = custom_row_to_strategy(row)
        if cfg:
            registry[cfg["key"]] = cfg
    # balanced must always be present as fallback even when disabled
    if "balanced" not in registry:
        registry["balanced"] = builtin_strategy_config("balanced", STRATEGIES["balanced"])
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

    direction_lock = payload.get("direction_lock", None)
    if direction_lock not in (None, "LONG", "SHORT"):
        return None, "direction_lock must be 'LONG', 'SHORT', or null"

    allowed_volatility = payload.get("allowed_volatility", None)
    if allowed_volatility is not None:
        if not isinstance(allowed_volatility, list) or not allowed_volatility:
            return None, "allowed_volatility must be a non-empty list or null"
        valid_regimes = {"low", "medium", "high", "extreme"}
        for v in allowed_volatility:
            if v not in valid_regimes:
                return None, f"allowed_volatility contains invalid regime: {v}"
        allowed_volatility = list(allowed_volatility)

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
        "direction_lock": direction_lock,
        "allowed_volatility": allowed_volatility,
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
        "is_custom":          bool(cfg.get("is_custom", False)),
        "enabled":            bool(cfg.get("enabled", True)),
        "base_key":           cfg.get("base_key", key),
        "direction_lock":     cfg.get("direction_lock", None),
        "allowed_volatility": cfg.get("allowed_volatility", None),
        "performance":        performance or {},
    }


def blocked_volatility_regimes(direction: str, strategy: dict | None = None) -> set[str]:
    """Return volatility regimes where candidates should be blocked or shadowed."""
    direction = str(direction or "").upper()
    sentinel = object()
    if direction == "SHORT":
        gate_key = "short_vol_short"
        strategy_gate_key = "block_short_volatility"
        default_raw = sentinel
    else:
        gate_key = "long_vol_long"
        strategy_gate_key = "block_long_volatility"
        default_raw = sentinel

    gate = get_risk_gate(gate_key)
    if gate.get("mode") == "off":
        return set()
    strat_gates = (strategy or {}).get("risk_gates") or {}
    raw = strat_gates.get(strategy_gate_key, default_raw)
    if direction == "LONG" and raw is sentinel:
        raw = gate.get("volatility", [])
    elif direction == "SHORT" and raw is sentinel:
        raw = None
    if raw in (None, False):
        return set()
    if raw is True:
        return set(gate.get("volatility", []))
    if isinstance(raw, str):
        return {raw}
    try:
        return {str(v) for v in raw}
    except TypeError:
        return set(gate.get("volatility", []))


def blocked_long_volatility_regimes(strategy: dict | None = None) -> set[str]:
    """Backward-compatible wrapper for older call sites."""
    return blocked_volatility_regimes("LONG", strategy)


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

    quote   = mexc_symbol.split("_")[-1]            # "BTC_USDT" → "USDT"
    quote   = "USDT" if quote == "USDC" else quote  # HL perps display USDC; use USDT venues for sentiment context
    bn_sym  = f"{base}{quote}"                      # "BTC_USDC" → "BTCUSDT" for external sentiment
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

def score_ticker(
    ticker: dict,
    strategy: dict | None = None,
    coinglass_snapshot: dict[str, dict] | None = None,
    **kwargs,
) -> dict | None:
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

        result = {
            "symbol": symbol,
            "exchange": ticker.get("exchange", "MEXC"),
            "direction": direction,
            "conviction_base": conviction_base,
            "price": price,
            "change_24h_pct": round(change_pct, 4),
            "funding_rate": funding,
            "volume_24h": volume,
            "open_interest": open_interest,
            "tags": tags,
        }
        result.update(enrich_symbol_with_coinglass(symbol, coinglass_snapshot))
        cg_oi = result.get("coinglass_open_interest_usd")
        result["derivatives_open_interest"] = cg_oi if cg_oi is not None else open_interest

        # Symbol performance penalty — applied after base conviction is set
        _conviction_before_penalty = result['conviction_base']
        _sym_perf = kwargs.get('sym_perf_cache', {})
        _sym_data = _sym_perf.get(result['symbol'])
        if _sym_data:
            _avg = _sym_data['avg_pnl']
            _n   = _sym_data['trade_count']
            if _avg < -30 and _n >= 5:
                result['conviction_base'] = max(0, result['conviction_base'] - 20)
                result['tags'].append('sym_penalty_severe')
            elif _avg < -15 and _n >= 5:
                result['conviction_base'] = max(0, result['conviction_base'] - 10)
                result['tags'].append('sym_penalty_moderate')
            elif _avg < -5 and _n >= 8:
                result['conviction_base'] = max(0, result['conviction_base'] - 5)
                result['tags'].append('sym_penalty_mild')

        # Symbol override — applied after penalty, can restore or force a tier
        _overrides = kwargs.get('sym_overrides', {})
        _override = _overrides.get(result['symbol'])
        if _override:
            _action = _override.get('action', '')
            result['tags'] = [t for t in result['tags'] if not t.startswith('sym_penalty_')]
            if _action == 'exempt':
                result['conviction_base'] = _conviction_before_penalty
                result['tags'].append('sym_exempt')
            elif _action == 'force_severe':
                result['conviction_base'] = max(0, _conviction_before_penalty - 20)
                result['tags'].append('sym_penalty_severe')
            elif _action == 'force_moderate':
                result['conviction_base'] = max(0, _conviction_before_penalty - 10)
                result['tags'].append('sym_penalty_moderate')
            elif _action == 'force_mild':
                result['conviction_base'] = max(0, _conviction_before_penalty - 5)
                result['tags'].append('sym_penalty_mild')

        return result
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


def enrich_signal(
    base: dict,
    strategy: dict | None = None,
    filter_stats: dict | None = None,
) -> dict | None:
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
        exchange = base.get("exchange", "MEXC")
        if exchange == "HYPERLIQUID":
            coin = symbol.rsplit("_", 1)[0]
            hl_1h = fetch_hl_klines(coin, "1h", 120)
            if not hl_1h:
                return None
            kline_data = {
                "open":  [float(k["o"]) for k in hl_1h],
                "high":  [float(k["h"]) for k in hl_1h],
                "low":   [float(k["l"]) for k in hl_1h],
                "close": [float(k["c"]) for k in hl_1h],
                "vol":   [float(k["v"]) for k in hl_1h],
            }
        else:
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
        if exchange == "HYPERLIQUID":
            hl_4h = fetch_hl_klines(coin, "4h", 480)
            n4h = len(hl_4h)
        else:
            kline4h_data = fetch_mexc(f"/contract/kline/{symbol}", params={"interval": "Hour4", "limit": 50})
            n4h = 0
            if kline4h_data and isinstance(kline4h_data, dict):
                n4h = len(kline4h_data.get("close", []))

        if n1h < 50 or n4h < 20:
            print(f"[kline gate] {symbol} skipped — 1h:{n1h} 4h:{n4h}", file=sys.stderr)
            return None

        # --- Direction lock filter ---
        direction_lock = strat.get("direction_lock")
        if direction_lock and direction != direction_lock:
            print(
                f"[direction lock] {symbol} skipped — strategy={strat.get('key')} "
                f"locked={direction_lock} signal={direction}",
                file=sys.stderr,
            )
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

        gate_key = "short_vol_short" if direction == "SHORT" else "long_vol_long"
        vol_gate = get_risk_gate(gate_key)
        gate_mode = vol_gate.get("mode", "block")
        blocked_vols = blocked_volatility_regimes(direction, strat)
        if vol_regime in blocked_vols:
            should_block = gate_mode == "block"
            reason_prefix = "short_vol" if direction == "SHORT" else "long_vol"
            reason = f"{reason_prefix}_{'refuse' if should_block else 'shadow'}"
            if filter_stats is not None:
                lock = filter_stats.get("lock")
                if lock:
                    with lock:
                        filter_stats[reason] = filter_stats.get(reason, 0) + 1
                else:
                    filter_stats[reason] = filter_stats.get(reason, 0) + 1
            final_tags = list(dict.fromkeys(tags + [reason]))
            skey = strat.get("key") or strategy_name_to_key(strat["name"])
            candidate = {
                "gate_key": gate_key,
                "gate_mode": gate_mode,
                "symbol": symbol,
                "exchange": exchange,
                "direction": direction,
                "strategy": strat["name"],
                "strategy_key": skey,
                "conviction": base.get("conviction_base"),
                "price": price,
                "atr_pct": round(atr_pct_val, 4),
                "volatility": vol_regime,
                "funding_rate": base.get("funding_rate"),
                "tags": final_tags,
                "reason": reason,
                "source": "enrich_signal",
            }
            print(
                f"[{reason}] {symbol} {'skipped' if should_block else 'shadowed'} — strategy={skey} vol={vol_regime} dir={direction}",
                file=sys.stderr,
            )
            log_filtered_candidate(candidate)
            if should_block:
                return None
            tags.append(reason)

        # --- Volatility allowlist filter (custom strategies only) ---
        allowed_volatility = strat.get("allowed_volatility")
        if allowed_volatility and vol_regime not in allowed_volatility:
            print(
                f"[vol allowlist] {symbol} skipped — strategy={strat.get('key')} "
                f"allowed={allowed_volatility} actual={vol_regime}",
                file=sys.stderr,
            )
            return None

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
        next_funding_minutes = None
        if exchange != "HYPERLIQUID":
            # MEXC endpoint returns nextSettleTime as Unix millisecond timestamp.
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
        if exchange == "HYPERLIQUID":
            depth_data = fetch_hl_orderbook(coin)
        else:
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
        if entries and price > 0:
            ladder_spread_pct = abs(entries[0] - stop_loss) / price
            if ladder_spread_pct < 0.001:
                print(
                    f"[ladder gate] {symbol} skipped — entry1={entries[0]} "
                    f"stop={stop_loss} spread_pct={ladder_spread_pct:.4f}",
                    file=sys.stderr,
                )
                return None

        # --- Daily trend direction ---
        # Separate API call — failure must never abort enrichment.
        daily_trend = None
        daily_trend_aligned = None
        try:
            if exchange == "HYPERLIQUID":
                hl_daily = fetch_hl_klines(coin, "1d", 720)
                # Convert to list-of-entry format that daily_trend_direction accepts:
                # [timestamp, open, close, high, low, vol] (index 3=high, 4=low)
                daily_klines = [[k["t"], k["o"], k["c"], k["h"], k["l"], k["v"]] for k in hl_daily] if hl_daily else None
            else:
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
            "exchange": exchange,
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
            "derivatives_open_interest": base.get("derivatives_open_interest"),
            "coinglass_available": base.get("coinglass_available", False),
            "coinglass_symbol": base.get("coinglass_symbol"),
            "coinglass_current_price": base.get("coinglass_current_price"),
            "coinglass_open_interest_usd": base.get("coinglass_open_interest_usd"),
            "coinglass_market_cap_usd": base.get("coinglass_market_cap_usd"),
            "coinglass_oi_market_cap_ratio": base.get("coinglass_oi_market_cap_ratio"),
            "coinglass_funding_oi_weighted": base.get("coinglass_funding_oi_weighted"),
            "coinglass_funding_vol_weighted": base.get("coinglass_funding_vol_weighted"),
            "coinglass_volume_usd": base.get("coinglass_volume_usd"),
            "coinglass_mexc_open_interest_usd": None,
            "coinglass_oi_change_1h_pct": None,
            "coinglass_oi_change_24h_pct": None,
            "coinglass_funding_interval_hours": None,
            "coinglass_liq_long_24h_usd": None,
            "coinglass_liq_short_24h_usd": None,
            "coinglass_mexc_liq_long_24h_usd": None,
            "coinglass_mexc_liq_short_24h_usd": None,
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
        cg_context = get_symbol_derivatives_context(symbol)
        if cg_context.get("coinglass_available"):
            sig.update({k: v for k, v in cg_context.items() if v is not None})
            sig["derivatives_open_interest"] = (
                sig.get("coinglass_open_interest_usd")
                or sig.get("derivatives_open_interest")
                or sig.get("open_interest")
            )
        # --- P7a: CoinGlass conviction adjustments (stage-2 only) ---
        # All blocks operate on sig["conviction"] and sig["tags"] directly after
        # the per-symbol cg_context merge above. CoinGlass fields are None when
        # the key is absent or the plan doesn't cover per-symbol data; every block
        # must skip cleanly when its required field is None so CoinGlass absence
        # is invisible to signal quality.

        # Change 1: Cross-exchange funding confirmation — Funding Arb strategy only.
        if sig.get("strategy_key") == "funding_arb":
            cg_fund = sig.get("coinglass_funding_oi_weighted")
            mexc_fund = sig.get("funding_rate")
            if (
                cg_fund is not None
                and mexc_fund is not None
                and abs(mexc_fund) > 0.0003
            ):
                if (cg_fund > 0) == (mexc_fund > 0) and abs(cg_fund) > 0.0003:
                    sig["conviction"] = min(100, sig["conviction"] + 8)
                    sig["tags"] = list(dict.fromkeys(sig["tags"] + ["cg_funding_confirmed"]))
                elif (cg_fund > 0) != (mexc_fund > 0):
                    sig["conviction"] = max(0, sig["conviction"] - 5)
                    sig["tags"] = list(dict.fromkeys(sig["tags"] + ["cg_funding_divergence"]))

        # Change 2: Liquidation asymmetry — all strategies.
        liq_long_cg = sig.get("coinglass_liq_long_24h_usd")
        liq_short_cg = sig.get("coinglass_liq_short_24h_usd")
        if (
            liq_long_cg is not None
            and liq_short_cg is not None
            and (liq_long_cg + liq_short_cg) > 0
        ):
            smaller_liq = min(liq_long_cg, liq_short_cg)
            liq_ratio = (max(liq_long_cg, liq_short_cg) / smaller_liq) if smaller_liq > 0 else 10.0
            if liq_ratio >= 3.0:
                long_liq_dominant = liq_long_cg > liq_short_cg
                sig_dir = sig.get("direction", "LONG")
                liq_aligned = (sig_dir == "SHORT" and long_liq_dominant) or (
                    sig_dir == "LONG" and not long_liq_dominant
                )
                if liq_aligned:
                    sig["conviction"] = min(100, sig["conviction"] + 5)
                    sig["tags"] = list(dict.fromkeys(sig["tags"] + ["liq_aligned"]))
                else:
                    sig["conviction"] = max(0, sig["conviction"] - 5)
                    sig["tags"] = list(dict.fromkeys(sig["tags"] + ["liq_contrary"]))

        # Thresholds (0.20 / 0.40) are initial guesses with no backtested basis — shadow only.
        # Do not escalate to a hard gate without reviewing fragility_high tag performance
        # across at least 2 weeks of closed signals.
        # Change 3: OI/market-cap fragility — all strategies.
        oi_mc_ratio = sig.get("coinglass_oi_market_cap_ratio")
        if oi_mc_ratio is not None:
            if oi_mc_ratio > 0.40:
                sig["tags"] = list(dict.fromkeys(sig["tags"] + ["fragility_extreme"]))
                sig["conviction"] = int(sig["conviction"] * 0.80)
            elif oi_mc_ratio > 0.20:
                sig["tags"] = list(dict.fromkeys(sig["tags"] + ["fragility_high"]))
                sig["conviction"] = int(sig["conviction"] * 0.90)
        sig["conviction"] = max(0, min(100, sig["conviction"]))

        # --- Agent Intelligence Layer (Phase 1 shadow mode) ---
        # Agent deltas are recorded for forward testing, but are NOT applied to
        # conviction in Phase 1. All blocks are shadow-tagged only — no conviction
        # changes until Phase 2 validation is complete.
        _agent_output = None
        try:
            from lib.agents import run_agent_pipeline

            _exchange = (sig.get("exchange") or base.get("exchange") or "MEXC").upper()
            _enriched = {
                "rsi_1h": sig.get("rsi_1h"),
                "trend_score": sig.get("trend_score"),
                "atr_pct": sig.get("atr_pct"),
                "volatility": sig.get("volatility"),
                "direction": sig.get("direction"),
                "change_4h_pct": sig.get("change_4h_pct"),
                "change_1h_pct": sig.get("change_1h_pct"),
                "basis_pct": sig.get("basis_pct") or base.get("basis_pct") or 0,
                "kline_depth_1h": sig.get("kline_depth_1h"),
                "kline_depth_4h": sig.get("kline_depth_4h"),
                "data_quality": sig.get("data_quality"),
                "leverage_cap": sig.get("leverage_cap"),
            }
            _agent_ticker = {
                **base,
                **sig,
                "symbol": sig.get("symbol"),
                "lastPrice": sig.get("price"),
                "fairPrice": sig.get("price"),
                "indexPrice": sig.get("price"),
                "fundingRate": sig.get("funding_rate"),
                "funding": sig.get("funding_rate"),
                "holdVol": sig.get("open_interest"),
                "openInterest": (
                    (sig.get("open_interest") or 0) / sig.get("price")
                    if _exchange == "HYPERLIQUID" and sig.get("price") else sig.get("open_interest")
                ),
                "volume24": sig.get("volume_24h"),
                "dayNtlVlm": sig.get("volume_24h"),
                "riseFallRate": (sig.get("change_24h_pct") or 0) / 100,
                "markPx": sig.get("price"),
                "oraclePx": sig.get("price"),
                "midPx": sig.get("price"),
                "name": sig.get("symbol", "").replace("_USDC", "").replace("_USDT", ""),
                "maxLeverage": sig.get("leverage_cap"),
            }
            _agent_output = run_agent_pipeline(
                exchange=_exchange,
                ticker_data=_agent_ticker,
                klines=df,
                depth_data={
                    "imbalance": imbalance,
                    "bid_depth_usd": 0,
                    "ask_depth_usd": 0,
                },
                enriched_fields=_enriched,
                timeout=8.0,
            )
        except Exception as _ae:
            print(f"[agents] {symbol}: {_ae}", file=sys.stderr)

        if _agent_output:
            # Phase 1 shadow mode: record blocks/tags but never touch conviction
            if _agent_output.hard_blocked:
                sig["tags"] = list(dict.fromkeys(
                    sig["tags"] + ["agent_blocked"] + _agent_output.block_reasons
                ))
            else:
                shadow_tags = [
                    f"agent_shadow_{t}"
                    for t in _agent_output.tags
                    if not t.startswith("agent_shadow_")
                ]
                sig["tags"] = list(dict.fromkeys(sig["tags"] + shadow_tags))

        sig.update({
            "agent_exchange": _agent_output.exchange if _agent_output else None,
            "agent_regime": _agent_output.detected_regime if _agent_output else None,
            "agent_narrative_bull": (
                _agent_output.narrative_bull_strength if _agent_output else None
            ),
            "agent_structural_bull": (
                _agent_output.structural_bull_strength if _agent_output else None
            ),
            "agent_blocked": _agent_output.hard_blocked if _agent_output else None,
            "agent_version": _agent_output.agent_version if _agent_output else None,
            "agent_shadow_delta": _agent_output.shadow_delta if _agent_output else None,
            "agent_shadow_narrative_delta": (
                _agent_output.shadow_narrative_delta if _agent_output else None
            ),
            "agent_shadow_structural_delta": (
                _agent_output.shadow_structural_delta if _agent_output else None
            ),
            "agent_shadow_disagreement": (
                _agent_output.shadow_disagreement_score if _agent_output else None
            ),
        })

        sig["signal_why"] = why_signal(sig)
        if _agent_output and _agent_output.composite_reasoning:
            sig["signal_why"] = (
                f"{sig['signal_why']} | [Agent] {_agent_output.composite_reasoning}"
            )
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
    tickers: list | None = None,
) -> tuple[list[dict], int]:
    """
    Two-stage scan across all MEXC perpetual tickers.

    Stage 1: Fetch all 800+ tickers (or use pre-fetched `tickers`), score each
             with score_ticker() using the active strategy's weights and
             stage-1 filters. Discard anything with conviction_base < 20, take
             the top ENRICH_TOP_N.
    Stage 2: Enrich the top N concurrently — fetches klines + depth per symbol,
             runs indicators, applies stage-2 strategy filters, builds ladders.

    The effective conviction floor is max(threshold, strategy.min_conviction)
    so each strategy's minimum bar is always respected.

    When `tickers` is provided (e.g. by api_scan_all), the ticker fetch and
    expire_stale_signals() are skipped — the caller handles both.

    Returns (signals, total_pairs) where total_pairs is the raw ticker count.
    Signals have conviction >= effective_threshold, sorted descending.
    """
    if tickers is None:
        expire_stale_signals()
        tickers = fetch_mexc("/contract/ticker")
        if not tickers or not isinstance(tickers, list):
            return [], 0

    registry = get_strategy_registry()
    strat = registry.get(strategy_key, registry["balanced"])
    strategy_key = strat["key"]
    effective_threshold = max(threshold, strat["min_conviction"])
    coinglass_snapshot = get_coin_market_snapshot()

    total_pairs = len(tickers)

    sym_perf_cache = _load_symbol_performance_cache()
    sym_overrides = _get_symbol_overrides()
    print(f'[sym_penalty] loaded {len(sym_perf_cache)} records, {len(sym_overrides)} overrides', file=sys.stderr)

    # Stage 1 — strategy weights + stage-1 filters applied inside score_ticker
    base_signals: list[dict] = []
    for t in tickers:
        scored = score_ticker(t, strategy=strat, coinglass_snapshot=coinglass_snapshot,
                              sym_perf_cache=sym_perf_cache, sym_overrides=sym_overrides)
        if scored and scored["conviction_base"] >= 20:
            base_signals.append(scored)

    base_signals.sort(key=lambda s: s["conviction_base"], reverse=True)
    top = base_signals[:ENRICH_TOP_N]

    # Stage 2 — concurrent enrichment, strategy-aware
    filter_stats = {
        "lock": threading.Lock(),
        "long_vol_refuse": 0,
        "long_vol_shadow": 0,
        "short_vol_refuse": 0,
        "short_vol_shadow": 0,
    }
    enrich = partial(enrich_signal, strategy=strat, filter_stats=filter_stats)
    signals: list[dict] = []
    with ThreadPoolExecutor(max_workers=ENRICH_WORKERS) as executor:
        for sig in executor.map(enrich, top):
            if sig and sig["conviction"] >= effective_threshold:
                signals.append(sig)

    refused = int(filter_stats.get("long_vol_refuse") or 0)
    shadowed = int(filter_stats.get("long_vol_shadow") or 0)
    short_refused = int(filter_stats.get("short_vol_refuse") or 0)
    short_shadowed = int(filter_stats.get("short_vol_shadow") or 0)
    if refused or shadowed or short_refused or short_shadowed:
        print(
            f"[scan risk gates] strategy={strategy_key} "
            f"long_vol_refuse={refused} long_vol_shadow={shadowed} "
            f"short_vol_refuse={short_refused} short_vol_shadow={short_shadowed}",
            file=sys.stderr,
        )

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
        registry = get_strategy_registry(include_disabled=True)
        strat = registry.get(strategy_key)
        if strat and not strat.get("enabled", True) and strategy_key != "balanced":
            return jsonify({"success": False, "error": f"Strategy '{strategy_key}' is disabled"}), 400
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


@app.route("/api/scan/all", methods=["POST"])
def api_scan_all():
    """Fetch tickers once, score and enrich for every enabled strategy in
    parallel, log results, and return all signals grouped by strategy key.
    The frontend caches the results so strategy switching is instant."""
    try:
        t0 = time.time()
        expire_stale_signals()

        tickers = fetch_mexc("/contract/ticker")
        if not tickers or not isinstance(tickers, list):
            return jsonify({"success": False, "error": "MEXC ticker feed unavailable"}), 502

        total_pairs = len(tickers)
        registry = get_strategy_registry()
        results: dict = {}

        for key in registry:
            signals, _ = run_scan(strategy_key=key, tickers=tickers)
            log_signals(signals)
            results[key] = {
                "signals":     signals,
                "total_pairs": total_pairs,
                "strategy":    key,
            }

        scan_time = round(time.time() - t0, 2)
        return jsonify({
            "success":     True,
            "results":     results,
            "total_pairs": total_pairs,
            "scan_time":   scan_time,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/hl/scan")
def api_hl_scan():
    """Scan Hyperliquid perps with the existing MT7 strategy engine."""
    try:
        threshold    = request.args.get("threshold", CONVICTION_THRESHOLD, type=int)
        strategy_key = request.args.get("strategy", "balanced")
        registry = get_strategy_registry(include_disabled=True)
        strat = registry.get(strategy_key)
        if strat and not strat.get("enabled", True) and strategy_key != "balanced":
            return jsonify({"success": False, "error": f"Strategy '{strategy_key}' is disabled"}), 400
        if strategy_key not in registry:
            strategy_key = "balanced"

        expire_stale_signals()
        universe, asset_ctxs = fetch_hl_meta_and_ctxs()
        tickers = normalize_hl_tickers(universe, asset_ctxs)
        if not tickers:
            return jsonify({"success": False, "error": "Hyperliquid ticker feed unavailable"}), 502

        signals, total_pairs = run_scan(
            threshold=threshold,
            strategy_key=strategy_key,
            tickers=tickers,
        )
        strat = registry.get(strategy_key, registry["balanced"])
        log_signals(signals)
        return jsonify({
            "success":       True,
            "signals":       signals,
            "count":         len(signals),
            "total_pairs":   total_pairs,
            "strategy":      strategy_key,
            "strategy_name": strat["name"],
            "exchange":      "HYPERLIQUID",
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/market")
def api_market():
    try:
        exchange = request.args.get("exchange", "mexc").strip().lower()
        if exchange == "hyperliquid":
            universe, asset_ctxs = fetch_hl_meta_and_ctxs()
            tickers = normalize_hl_tickers(universe, asset_ctxs)
            exchange_label = "HYPERLIQUID"
            unavailable = "Hyperliquid unavailable"
        else:
            tickers = fetch_mexc("/contract/ticker")
            exchange_label = "MEXC"
            unavailable = "MEXC unavailable"

        if not tickers or not isinstance(tickers, list):
            return jsonify({"success": False, "error": unavailable}), 502

        coinglass_snapshot = get_coin_market_snapshot()
        pairs = []
        for t in tickers:
            scored = score_ticker(t, coinglass_snapshot=coinglass_snapshot)
            if scored:
                pairs.append(scored)

        pairs.sort(key=lambda p: p["conviction_base"], reverse=True)
        return jsonify({
            "success": True,
            "pairs": pairs,
            "count": len(pairs),
            "exchange": exchange_label,
            "coinglass_enabled": coinglass_enabled(),
            "coinglass_pairs": len(coinglass_snapshot),
        })
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
        exchange = request.args.get("exchange", "mexc").strip().lower()
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

        if exchange == "hyperliquid":
            universe, asset_ctxs = fetch_hl_meta_and_ctxs()
            tickers = normalize_hl_tickers(universe, asset_ctxs)
            unavailable = "Hyperliquid unavailable"
        else:
            tickers = fetch_mexc("/contract/ticker")
            unavailable = "MEXC unavailable"
        if not tickers:
            return jsonify({"success": False, "error": unavailable}), 502

        requested_symbol = symbol.upper()
        ticker = next(
            (t for t in tickers if str(t.get("symbol", "")).upper() == requested_symbol), None
        )
        if not ticker:
            return jsonify({"success": False, "error": f"Symbol {symbol!r} not found"}), 404

        coinglass_snapshot = get_coin_market_snapshot()
        base = score_ticker(ticker, strategy=strat, coinglass_snapshot=coinglass_snapshot)
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
        if row and result not in {"EXPIRED", "SKIPPED"}:
            event_type = {
                "WIN": "MANUAL_WIN",
                "LOSS": "MANUAL_LOSS",
                "PARTIAL": "MANUAL_PARTIAL",
            }.get(result)
            if event_type:
                log_position_event(
                    dict(row),
                    event_type,
                    datetime.utcnow().isoformat(),
                    exit_price,
                    pnl_pct or 0.0,
                    0.0,
                    note or f"Manual {result}",
                )
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
        signals = [dict(r) for r in rows]
        ids = [s["id"] for s in signals]
        events_by_signal: dict[int, list[dict]] = defaultdict(list)
        if ids:
            placeholders = ",".join("?" for _ in ids)
            event_rows = con.execute(f"""
                SELECT * FROM position_events
                WHERE signal_id IN ({placeholders})
                ORDER BY event_at ASC, id ASC
            """, ids).fetchall()
            for ev in event_rows:
                d = dict(ev)
                events_by_signal[int(d["signal_id"])].append(d)
        con.close()
        for sig in signals:
            sig["position_events"] = events_by_signal.get(int(sig["id"]), [])
        return jsonify({
            "success": True,
            "signals": signals,
            "count":   len(signals),
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
        event_rows = con.execute("""
            SELECT * FROM position_events
            WHERE signal_id=?
            ORDER BY event_at ASC, id ASC
        """, (signal_id,)).fetchall()
        con.close()

        if not row:
            return jsonify({"success": False, "error": "not found"}), 404

        sig = dict(row)
        sig["position_events"] = [dict(ev) for ev in event_rows]

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
        journey = compute_trade_journey(sig, pnl_pct)
        journey_prompt = format_journey_for_prompt(journey)

        # AI trade coach review — gracefully skipped if no provider key or call fails
        user_msg = (
            f"Trade review:\n"
            f"Symbol: {sig.get('symbol')} | Direction: {direction} | Strategy: {sig.get('strategy')}\n"
            f"Entry: {entry1} | Exit: {exit_price or 'unknown'} | Result: {sig.get('result')}\n"
            f"Stop: {sig.get('stop_loss')} | TP1: {sig.get('tp1')} | TP2: {sig.get('tp2')} | TP3: {sig.get('tp3')}\n"
            f"Duration: {duration_minutes or 'unknown'} minutes\n"
            f"Trade journey stats:\n{journey_prompt}\n"
            f"Signal reason: {sig.get('signal_why')}\n"
            f"Tags: {tags_raw}\n"
            f"Result note: {sig.get('result_note')}\n\n"
            f"Write a concise but useful coach review in 2 short paragraphs. "
            f"First describe the price journey from signal/entry to close using the journey stats. "
            f"Then explain what the signal got right or wrong and one specific thing to watch next time. "
            f"Do not claim the strategy should change based on a single trade; frame learning as evidence to aggregate."
        )
        ai_analysis = call_ai(
            system="You are a trading coach reviewing a completed trade. "
                   "Be direct, specific, and grounded only in the supplied trade data. "
                   "Explain MAE/MFE/capture in plain trader language. No fluff.",
            user=user_msg,
            max_tokens=900,
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
            "journey":          journey,
            "ai_analysis":      ai_analysis,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def _parse_utc_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", ""))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _signed_raw_pct(direction: str, entry: float, price: float) -> float | None:
    if not entry:
        return None
    if direction == "LONG":
        return (price - entry) / entry * 100
    return (entry - price) / entry * 100


def _journey_label(result: str | None, mae_pct: float, mfe_pct: float, capture_ratio: float | None, stop_pressure_pct: float | None, entry_delay_min: int | None) -> str:
    near_stop = stop_pressure_pct is not None and stop_pressure_pct >= 80
    delayed = entry_delay_min is not None and entry_delay_min >= 60
    if result == "LOSS":
        return "failed_fast" if mfe_pct < 1 else "reversed_after_progress"
    if result == "WIN":
        if near_stop:
            return "near_stop_win"
        if capture_ratio is not None and capture_ratio >= 70 and mae_pct < max(1.0, mfe_pct * 0.25):
            return "clean_follow_through"
        if delayed:
            return "delayed_confirmation"
        return "profitable_but_choppy"
    if result == "PARTIAL":
        return "partial_then_pressure" if near_stop else "partial_follow_through"
    if result == "EXPIRED":
        return "stalled_setup"
    return "path_unclassified"


def compute_trade_journey(sig: dict, pnl_pct: float | None = None) -> dict:
    """
    Analyze the candle path between signal log and close.

    Uses Min15 candles to compute MAE/MFE, target timing, stop pressure, and
    capture ratio. If the exchange no longer has the kline window, returns an
    unavailable journey instead of failing the detail route.
    """
    symbol = sig.get("symbol")
    direction = sig.get("direction", "")
    entry = sig.get("entry1")
    if not symbol or direction not in {"LONG", "SHORT"} or not entry:
        return {"available": False, "reason": "missing entry, symbol, or direction"}

    logged_dt = _parse_utc_iso(sig.get("logged_at"))
    result_dt = _parse_utc_iso(sig.get("result_at")) or datetime.now(timezone.utc)
    if not logged_dt:
        return {"available": False, "reason": "missing logged_at"}

    start_ts = int(logged_dt.timestamp())
    end_ts = int(result_dt.timestamp())
    limit = min(300, max(20, int((end_ts - start_ts) / 900) + 6))

    klines = _fetch_klines_for_signal(
        sig,
        interval="Min15",
        start_ts=start_ts,
        limit=limit,
    )
    if klines.empty:
        return {"available": False, "reason": "kline history unavailable"}

    raw_times = klines["timestamp"].tolist()
    raw_highs = klines["high"].tolist()
    raw_lows = klines["low"].tolist()
    raw_closes = klines["close"].tolist()
    candles: list[dict] = []
    for i, t in enumerate(raw_times):
        try:
            ts = int(t)
            if start_ts <= ts <= end_ts + 900:
                candles.append({
                    "time": ts,
                    "high": float(raw_highs[i]),
                    "low": float(raw_lows[i]),
                    "close": float(raw_closes[i]),
                })
        except (ValueError, IndexError, TypeError):
            pass

    if not candles:
        return {"available": False, "reason": "no candles in trade window"}

    entry_hit_ts: int | None = None
    best_favorable = 0.0
    worst_adverse = 0.0
    best_price = None
    worst_price = None
    target_hits: dict[str, str] = {}
    target_prices = {"tp1": sig.get("tp1"), "tp2": sig.get("tp2"), "tp3": sig.get("tp3")}
    stop_loss = sig.get("stop_loss")

    for c in candles:
        ts, high, low = c["time"], c["high"], c["low"]
        if entry_hit_ts is None:
            touched = low <= entry if direction == "LONG" else high >= entry
            if not touched:
                continue
            entry_hit_ts = ts

        if direction == "LONG":
            favorable = _signed_raw_pct(direction, entry, high) or 0.0
            adverse = abs(min(_signed_raw_pct(direction, entry, low) or 0.0, 0.0))
            favorable_price = high
            adverse_price = low
            for label, price in target_prices.items():
                if price and label not in target_hits and high >= price:
                    target_hits[label] = datetime.utcfromtimestamp(ts).isoformat()
        else:
            favorable = _signed_raw_pct(direction, entry, low) or 0.0
            adverse = abs(min(_signed_raw_pct(direction, entry, high) or 0.0, 0.0))
            favorable_price = low
            adverse_price = high
            for label, price in target_prices.items():
                if price and label not in target_hits and low <= price:
                    target_hits[label] = datetime.utcfromtimestamp(ts).isoformat()

        if favorable > best_favorable:
            best_favorable = favorable
            best_price = favorable_price
        if adverse > worst_adverse:
            worst_adverse = adverse
            worst_price = adverse_price

    exit_price = sig.get("exit_price")
    final_raw_pct = _signed_raw_pct(direction, entry, exit_price) if exit_price else None
    capture_ratio = None
    if final_raw_pct is not None and best_favorable > 0 and final_raw_pct > 0:
        capture_ratio = max(0.0, min(100.0, final_raw_pct / best_favorable * 100))

    planned_stop_pct = None
    stop_pressure_pct = None
    if stop_loss:
        planned_stop_pct = abs(_signed_raw_pct(direction, entry, stop_loss) or 0.0)
        if planned_stop_pct > 0:
            stop_pressure_pct = min(100.0, worst_adverse / planned_stop_pct * 100)

    entry_delay_min = None
    if entry_hit_ts:
        entry_delay_min = int((entry_hit_ts - start_ts) / 60)

    entry_to_close_min = None
    if entry_hit_ts and end_ts:
        entry_to_close_min = int((end_ts - entry_hit_ts) / 60)

    leverage = sig.get("leverage")
    if leverage is None:
        try:
            leverage = json.loads(sig.get("signal_json") or "{}").get("leverage_cap")
        except Exception:
            leverage = None
    leverage = float(leverage) if leverage else None

    label = _journey_label(
        sig.get("result"),
        worst_adverse,
        best_favorable,
        capture_ratio,
        stop_pressure_pct,
        entry_delay_min,
    )

    return {
        "available": True,
        "timeframe": "Min15",
        "candles": len(candles),
        "entry_hit": entry_hit_ts is not None,
        "entry_delay_minutes": entry_delay_min,
        "entry_to_close_minutes": entry_to_close_min,
        "mae_pct": round(worst_adverse, 2),
        "mfe_pct": round(best_favorable, 2),
        "mae_leveraged_pct": round(worst_adverse * leverage, 2) if leverage else None,
        "mfe_leveraged_pct": round(best_favorable * leverage, 2) if leverage else None,
        "best_price": round(best_price, 10) if best_price is not None else None,
        "worst_price": round(worst_price, 10) if worst_price is not None else None,
        "final_raw_pct": round(final_raw_pct, 2) if final_raw_pct is not None else None,
        "final_leveraged_pct": pnl_pct,
        "capture_ratio_pct": round(capture_ratio, 1) if capture_ratio is not None else None,
        "planned_stop_pct": round(planned_stop_pct, 2) if planned_stop_pct is not None else None,
        "stop_pressure_pct": round(stop_pressure_pct, 1) if stop_pressure_pct is not None else None,
        "target_hits": target_hits,
        "path_label": label,
    }


def format_journey_for_prompt(journey: dict) -> str:
    if not journey or not journey.get("available"):
        return f"- Journey unavailable: {journey.get('reason', 'unknown') if journey else 'unknown'}"
    target_hits = journey.get("target_hits") or {}
    hit_text = ", ".join(f"{k.upper()} at {v}" for k, v in target_hits.items()) or "No target timestamps detected"
    return (
        f"- Path label: {journey.get('path_label')}\n"
        f"- Entry hit: {journey.get('entry_hit')} after {journey.get('entry_delay_minutes')} minutes\n"
        f"- Entry-to-close time: {journey.get('entry_to_close_minutes')} minutes over {journey.get('candles')} Min15 candles\n"
        f"- MAE: {journey.get('mae_pct')}% raw ({journey.get('mae_leveraged_pct')}% leveraged if available)\n"
        f"- MFE: {journey.get('mfe_pct')}% raw ({journey.get('mfe_leveraged_pct')}% leveraged if available)\n"
        f"- Capture ratio: {journey.get('capture_ratio_pct')}% of favorable excursion\n"
        f"- Stop pressure: {journey.get('stop_pressure_pct')}% of planned stop distance used\n"
        f"- Target hits: {hit_text}"
    )


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


def _leveraged_level_pnl(sig: dict, price: float | None, size_fraction: float = 1.0) -> float:
    """Incremental leveraged P&L contribution for a partial size at a price."""
    if price is None:
        return 0.0
    full = _compute_leveraged_pnl(sig, price)
    if full is None:
        return 0.0
    return round(full * size_fraction, 2)


def log_position_event(
    sig: dict,
    event_type: str,
    event_at: str | None,
    price: float | None,
    realized_pct: float = 0.0,
    remaining_size_pct: float = 100.0,
    note: str = "",
) -> None:
    """Persist TP/SL/entry lifecycle events idempotently for paper positions."""
    signal_id = sig.get("id")
    if not signal_id or not event_type or not event_at:
        return
    try:
        con = sqlite3.connect(DB_PATH, timeout=1)
        con.execute("""
            INSERT OR IGNORE INTO position_events
            (signal_id, event_type, event_at, price, realized_pct,
             remaining_size_pct, note, created_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            signal_id,
            event_type,
            event_at,
            price,
            round(float(realized_pct or 0), 2),
            round(float(remaining_size_pct), 2),
            note,
            datetime.utcnow().isoformat(),
        ))
        con.commit()
        con.close()
    except Exception as e:
        print(f"log_position_event error [{signal_id}:{event_type}]: {e}", file=sys.stderr)


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


def _fetch_klines_for_signal(
    sig: dict,
    interval: str = "Min15",
    limit: int = 300,
    start_ts: int | None = None,
) -> "pd.DataFrame":
    """
    Fetch klines for a signal using the correct exchange client.
    Routes to Hyperliquid or MEXC based on sig.get('exchange').
    Returns a DataFrame with columns: [timestamp, open, high, low, close, volume].
    Returns empty DataFrame on any error — never raises.
    Callers must check df.empty before using.
    """
    exchange = (sig.get("exchange") or "MEXC").upper()
    symbol   = sig.get("symbol", "")

    if exchange == "HYPERLIQUID":
        coin = symbol.replace("_USDT", "").replace("_USDC", "")
        interval_map = {
            "Min1":  "1m",  "Min5":  "5m",  "Min15": "15m",
            "Min30": "30m", "Min60": "1h",  "Hour4": "4h",
            "Hour8": "8h",  "Day1":  "1d",
        }
        hl_interval = interval_map.get(interval, "15m")
        hours_per_candle = {
            "1m": 1/60, "5m": 5/60, "15m": 0.25, "30m": 0.5,
            "1h": 1, "4h": 4, "8h": 8, "1d": 24,
        }
        lookback = int(limit * hours_per_candle.get(hl_interval, 0.25) * 1.2)
        try:
            raw = fetch_hl_klines(coin, hl_interval, lookback_hours=max(lookback, 24))
            if not raw:
                return pd.DataFrame()
            df = pd.DataFrame(raw)
            df = df.rename(columns={
                "t": "timestamp", "o": "open", "h": "high",
                "l": "low",       "c": "close", "v": "volume",
            })
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.sort_values("timestamp").tail(limit).reset_index(drop=True)
            print(f"[hl_klines] {symbol} fetched {len(df)} {hl_interval} candles", file=sys.stderr)
            return df
        except Exception as e:
            print(f"[hl_klines] {symbol} error: {e}", file=sys.stderr)
            return pd.DataFrame()

    else:
        # MEXC path — mirrors existing fetch_mexc kline call
        try:
            params: dict = {"interval": interval, "limit": limit}
            if start_ts is not None:
                params["start"] = start_ts
            raw = fetch_mexc(f"/contract/kline/{symbol}", params=params)
            if not raw or not isinstance(raw, dict):
                return pd.DataFrame()
            df = pd.DataFrame({
                "timestamp": raw.get("time",  []),
                "open":      raw.get("open",  []),
                "high":      raw.get("high",  []),
                "low":       raw.get("low",   []),
                "close":     raw.get("close", []),
                "volume":    raw.get("vol",   []),
            })
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.sort_values("timestamp").reset_index(drop=True)
            print(f"[mexc_klines] {symbol} fetched {len(df)} {interval} candles", file=sys.stderr)
            return df
        except Exception as e:
            print(f"[mexc_klines] {symbol} error: {e}", file=sys.stderr)
            return pd.DataFrame()


def evaluate_outcome(sig: dict) -> tuple[str, str, float | None, str | None, str | None] | None:
    """
    Fetch Min15 klines since the signal was logged and determine if stop or TP was hit.

    Returns (result, note, exit_price, result_at, entry_at) or None if no level
    was hit yet / insufficient data.

    Evaluation rules (applied per candle in chronological order):
      - First wait for entry1 to be touched. Signals are logged at scan time, but
        the ladder entry may be away from current price.
      - Stop hit (LONG: low <= stop | SHORT: high >= stop) → LOSS (or PARTIAL if TP1 hit first)
      - TP1/TP2 hit → log position_events and keep remainder open
      - TP3 fully hit → WIN
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

    klines = _fetch_klines_for_signal(
        sig,
        interval="Min15",
        start_ts=start_ts,
        limit=300,   # 300 × 15min = ~75 hours of coverage
    )
    if klines.empty:
        return None

    raw_times  = klines["timestamp"].tolist()
    raw_highs  = klines["high"].tolist()
    raw_lows   = klines["low"].tolist()
    raw_closes = klines["close"].tolist()
    if not raw_times or not raw_highs or not raw_lows or not raw_closes:
        return None

    # Lifecycle invariant: TP/SL events must follow ENTRY_FILLED. See P5c.
    # Bootstrap from persisted entry_at so re-evaluations skip pre-entry candles.
    existing_entry_at = sig.get("entry_at")
    entry_hit  = bool(existing_entry_at)
    entry_at: str | None = existing_entry_at or None

    # Candle scan starts at entry_at when confirmed; otherwise from logged_at.
    # Candles before entry are scenery, not events.
    scan_start_ts = start_ts
    if existing_entry_at:
        try:
            edt = datetime.fromisoformat(existing_entry_at)
            if edt.tzinfo is None:
                edt = edt.replace(tzinfo=timezone.utc)
            scan_start_ts = int(edt.timestamp())
        except Exception:
            pass

    # Build candle list — only candles that opened at or after scan_start_ts
    candles: list[dict] = []
    for i, t in enumerate(raw_times):
        try:
            candle_ts = int(t)
            if candle_ts >= scan_start_ts:
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
            log_position_event(
                sig, "ENTRY_FILLED", entry_at, entry1,
                0.0, 100.0, f"Entry 1 touched at {entry1}"
            )

        if direction == "LONG":
            if l <= stop_loss:
                stop_hit   = True
                stop_size = 1.0 if best_tp == 0 else (2 / 3 if best_tp == 1 else 1 / 3)
                remaining = 0.0
                exit_price = stop_loss if best_tp == 0 else (
                    tp3 if best_tp == 3 else tp2 if best_tp == 2 else tp1
                )
                result_at = _iso(ts)
                log_position_event(
                    sig, "STOP_HIT", result_at, stop_loss,
                    _leveraged_level_pnl(sig, stop_loss, stop_size),
                    remaining, f"Stop touched after TP{best_tp}" if best_tp else "Stop touched"
                )
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
                for tier in range(prev_tp + 1, best_tp + 1):
                    tier_price = tp3 if tier == 3 else tp2 if tier == 2 else tp1
                    log_position_event(
                        sig, f"TP{tier}_HIT", result_at, tier_price,
                        _leveraged_level_pnl(sig, tier_price, 1 / 3),
                        max(0.0, (3 - tier) / 3 * 100),
                        f"TP{tier} touched at {tier_price}",
                    )
                if best_tp == 3:
                    break
        else:  # SHORT
            if h >= stop_loss:
                stop_hit   = True
                stop_size = 1.0 if best_tp == 0 else (2 / 3 if best_tp == 1 else 1 / 3)
                remaining = 0.0
                exit_price = stop_loss if best_tp == 0 else (
                    tp3 if best_tp == 3 else tp2 if best_tp == 2 else tp1
                )
                result_at = _iso(ts)
                log_position_event(
                    sig, "STOP_HIT", result_at, stop_loss,
                    _leveraged_level_pnl(sig, stop_loss, stop_size),
                    remaining, f"Stop touched after TP{best_tp}" if best_tp else "Stop touched"
                )
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
                for tier in range(prev_tp + 1, best_tp + 1):
                    tier_price = tp3 if tier == 3 else tp2 if tier == 2 else tp1
                    log_position_event(
                        sig, f"TP{tier}_HIT", result_at, tier_price,
                        _leveraged_level_pnl(sig, tier_price, 1 / 3),
                        max(0.0, (3 - tier) / 3 * 100),
                        f"TP{tier} touched at {tier_price}",
                    )
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
    if best_tp in {1, 2}:
        return None   # partial TP events are logged; remainder stays open until TP3 or stop
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
        rows = con.execute("""
            SELECT id, symbol, exchange, direction,
                   entry1, entry2, entry3, tp1, tp2, tp3, stop_loss,
                   logged_at, entry_at, leverage, strategy_key, signal_json
            FROM signals
            WHERE result IS NULL
            ORDER BY logged_at ASC
        """).fetchall()
        con.close()

        open_sigs = [dict(r) for r in rows]
        tagged: list[dict] = []
        skipped = 0

        for sig in open_sigs:
            outcome = evaluate_outcome(sig)
            if outcome is None:
                # Persist entry_at if ENTRY_FILLED was logged this run but signals.entry_at is still NULL
                if not sig.get("entry_at"):
                    _con = sqlite3.connect(DB_PATH)
                    _con.row_factory = sqlite3.Row
                    _ev = _con.execute(
                        "SELECT event_at FROM position_events "
                        "WHERE signal_id=? AND event_type='ENTRY_FILLED'",
                        (sig["id"],),
                    ).fetchone()
                    if _ev:
                        _con.execute(
                            "UPDATE signals SET entry_at=? WHERE id=? AND entry_at IS NULL",
                            (_ev["event_at"], sig["id"]),
                        )
                        _con.commit()
                    _con.close()
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
                "SUM(CASE WHEN result='WIN'     THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN result='LOSS'    THEN 1 ELSE 0 END), "
                "AVG(conviction), "
                "SUM(CASE WHEN result='PARTIAL' THEN 1 ELSE 0 END) "
                "FROM signals WHERE strategy_key=?",
                (key,),
            ).fetchone()
            total    = int(row[0] or 0)
            wins     = int(row[1] or 0)
            losses   = int(row[2] or 0)
            avg_conv = row[3]
            partials = int(row[4] or 0)
            wr       = wins / (wins + losses + partials) if (wins + losses + partials) > 0 else None
            top_row  = con.execute(
                "SELECT symbol FROM signals WHERE strategy_key=? "
                "GROUP BY symbol ORDER BY COUNT(*) DESC LIMIT 1",
                (key,),
            ).fetchone()
            win_pnl_row = con.execute(
                "SELECT AVG(pnl_pct) FROM signals WHERE strategy_key=? AND pnl_pct > 0",
                (key,),
            ).fetchone()
            loss_pnl_row = con.execute(
                "SELECT AVG(pnl_pct) FROM signals WHERE strategy_key=? AND pnl_pct <= 0",
                (key,),
            ).fetchone()
            perf[key] = {
                "total_signals":  total,
                "wins":           wins,
                "losses":         losses,
                "win_rate":       round(wr, 4) if wr is not None else None,
                "avg_conviction": round(avg_conv, 1) if avg_conv is not None else None,
                "top_symbol":     top_row[0] if top_row else None,
                "avg_win_pnl":    round(win_pnl_row[0], 1) if win_pnl_row[0] is not None else None,
                "avg_loss_pnl":   round(loss_pnl_row[0], 1) if loss_pnl_row[0] is not None else None,
            }
        con.close()
    except Exception as e:
        print(f"api_strategies perf query error: {e}", file=sys.stderr)
        for key in registry:
            if key not in perf:
                perf[key] = {
                    "total_signals": 0, "wins": 0, "losses": 0,
                    "win_rate": None, "avg_conviction": None, "top_symbol": None,
                    "avg_win_pnl": None, "avg_loss_pnl": None,
                }

    result = [
        strategy_to_api(key, cfg, performance=perf.get(key, {}))
        for key, cfg in registry.items()
        if cfg.get("enabled", True) or include_disabled
    ]
    return jsonify({"success": True, "strategies": result})


@app.route("/api/strategies/analytics")
def api_strategies_analytics():
    """Return chart-ready strategy performance analytics from logged signals."""
    try:
        registry = get_strategy_registry(include_disabled=True)
        analytics: dict[str, dict] = {}
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row

        for key, cfg in registry.items():
            rows = [
                dict(r) for r in con.execute(
                    "SELECT * FROM signals WHERE strategy_key=? ORDER BY logged_at ASC",
                    (key,),
                ).fetchall()
            ]

            open_rows = [r for r in rows if r.get("result") is None]
            closed = [r for r in rows if r.get("result") is not None]
            trade_rows = [
                r for r in closed
                if r.get("result") not in {"SKIPPED", "EXPIRED"}
            ]
            pnl_rows = [r for r in trade_rows if r.get("pnl_pct") is not None]
            wins = [r for r in trade_rows if r.get("result") == "WIN"]
            losses = [r for r in trade_rows if r.get("result") == "LOSS"]
            partials = [r for r in trade_rows if r.get("result") == "PARTIAL"]
            total_pnl = sum(float(r.get("pnl_pct") or 0) for r in pnl_rows)
            avg_pnl = total_pnl / len(pnl_rows) if pnl_rows else None
            win_rate = len(wins) / (len(wins) + len(losses) + len(partials)) if (len(wins) + len(losses) + len(partials)) else None

            equity = []
            cumulative = 0.0
            for r in pnl_rows:
                cumulative += float(r.get("pnl_pct") or 0)
                equity.append({
                    "logged_at": r.get("logged_at"),
                    "symbol": r.get("symbol"),
                    "result": r.get("result"),
                    "pnl_pct": round(float(r.get("pnl_pct") or 0), 2),
                    "cumulative_pnl": round(cumulative, 2),
                })

            outcome_counts = {
                "WIN": 0, "LOSS": 0, "PARTIAL": 0, "EXPIRED": 0, "SKIPPED": 0,
            }
            for r in closed:
                res = r.get("result")
                if res in outcome_counts:
                    outcome_counts[res] += 1

            buckets = [
                ("<= -20%", lambda v: v <= -20),
                ("-20 to -10", lambda v: -20 < v <= -10),
                ("-10 to 0", lambda v: -10 < v < 0),
                ("0 to 10", lambda v: 0 <= v < 10),
                ("10 to 20", lambda v: 10 <= v < 20),
                (">= 20%", lambda v: v >= 20),
            ]
            distribution = []
            pnl_values = [float(r.get("pnl_pct") or 0) for r in pnl_rows]
            for label, fn in buckets:
                distribution.append({
                    "label": label,
                    "count": sum(1 for v in pnl_values if fn(v)),
                })

            by_symbol: dict[str, dict] = {}
            for r in pnl_rows:
                symbol = r.get("symbol") or "UNKNOWN"
                d = by_symbol.setdefault(symbol, {"symbol": symbol, "count": 0, "total_pnl": 0.0})
                d["count"] += 1
                d["total_pnl"] += float(r.get("pnl_pct") or 0)
            symbol_rows = []
            for d in by_symbol.values():
                symbol_rows.append({
                    "symbol": d["symbol"],
                    "count": d["count"],
                    "total_pnl": round(d["total_pnl"], 2),
                    "avg_pnl": round(d["total_pnl"] / d["count"], 2) if d["count"] else None,
                })
            best_symbols = sorted(symbol_rows, key=lambda x: x["total_pnl"], reverse=True)[:5]
            worst_symbols = sorted(symbol_rows, key=lambda x: x["total_pnl"])[:5]

            regime_map: dict[str, dict] = {}
            for r in pnl_rows:
                regime = r.get("volatility") or "unknown"
                d = regime_map.setdefault(regime, {"regime": regime, "count": 0, "total_pnl": 0.0})
                d["count"] += 1
                d["total_pnl"] += float(r.get("pnl_pct") or 0)
            regimes = []
            for regime in ["low", "medium", "high", "extreme", "unknown"]:
                d = regime_map.get(regime, {"regime": regime, "count": 0, "total_pnl": 0.0})
                regimes.append({
                    "regime": regime,
                    "count": d["count"],
                    "total_pnl": round(d["total_pnl"], 2),
                    "avg_pnl": round(d["total_pnl"] / d["count"], 2) if d["count"] else None,
                })

            last = rows[-1] if rows else None
            best = max(pnl_rows, key=lambda r: float(r.get("pnl_pct") or 0), default=None)
            worst = min(pnl_rows, key=lambda r: float(r.get("pnl_pct") or 0), default=None)

            analytics[key] = {
                "strategy": strategy_to_api(key, cfg),
                "summary": {
                    "total": len(rows),
                    "open": len(open_rows),
                    "closed": len(closed),
                    "trades": len(trade_rows),
                    "wins": len(wins),
                    "losses": len(losses),
                    "partials": len(partials),
                    "win_rate": round(win_rate, 4) if win_rate is not None else None,
                    "avg_pnl": round(avg_pnl, 2) if avg_pnl is not None else None,
                    "total_pnl": round(total_pnl, 2),
                    "best_symbol": best.get("symbol") if best else None,
                    "best_pnl": round(float(best.get("pnl_pct") or 0), 2) if best else None,
                    "worst_symbol": worst.get("symbol") if worst else None,
                    "worst_pnl": round(float(worst.get("pnl_pct") or 0), 2) if worst else None,
                    "last_signal_at": last.get("logged_at") if last else None,
                    "last_symbol": last.get("symbol") if last else None,
                    "last_direction": last.get("direction") if last else None,
                },
                "equity": equity,
                "outcomes": outcome_counts,
                "distribution": distribution,
                "symbols": {
                    "best": best_symbols,
                    "worst": worst_symbols,
                },
                "regimes": regimes,
            }

        con.close()
        return jsonify({"success": True, "analytics": analytics})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/strategies/portfolio")
def api_strategy_portfolio():
    """
    Replay closed strategy outcomes as a portfolio simulator.

    This is decision support only: selected strategies are compounded against
    historical pnl_pct rows, with optional application of the current long-vol
    circuit breaker so users can compare the old vs gated strategy mix.
    """
    try:
        registry = get_strategy_registry(include_disabled=True)
        requested = request.args.get("strategies", "").strip()
        if requested:
            selected_keys = [
                k.strip() for k in requested.split(",")
                if k.strip() in registry
            ]
        else:
            selected_keys = list(registry.keys())
        if not selected_keys:
            selected_keys = ["balanced"]

        try:
            account_start = float(request.args.get("account", "200"))
        except (TypeError, ValueError):
            account_start = 200.0
        account_start = max(1.0, min(account_start, 10_000_000.0))

        try:
            risk_pct = float(request.args.get("risk_pct", "1"))
        except (TypeError, ValueError):
            risk_pct = 1.0
        risk_pct = max(0.01, min(risk_pct, 100.0))

        gate_mode = request.args.get("long_vol_gate", "on").strip().lower()
        apply_long_vol_gate = gate_mode not in {"0", "false", "off", "no"}
        blocked_vols = set(get_long_vol_gate().get("volatility", []))

        placeholders = ",".join("?" for _ in selected_keys)
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        rows = [
            dict(r) for r in con.execute(f"""
                SELECT id, logged_at, symbol, direction, strategy, strategy_key,
                       result, pnl_pct, volatility
                FROM signals
                WHERE strategy_key IN ({placeholders})
                  AND result IS NOT NULL
                  AND result NOT IN ('SKIPPED', 'EXPIRED')
                  AND pnl_pct IS NOT NULL
                ORDER BY logged_at ASC, id ASC
            """, selected_keys).fetchall()
        ]

        filtered_logged = 0
        try:
            filtered_logged = con.execute(f"""
                SELECT COUNT(*) FROM filtered_candidates
                WHERE strategy_key IN ({placeholders})
            """, selected_keys).fetchone()[0]
        except sqlite3.OperationalError:
            filtered_logged = 0
        con.close()

        balance = account_start
        peak = account_start
        max_drawdown_pct = 0.0
        equity = []
        skipped_by_gate = []
        by_strategy: dict[str, dict] = {}
        wins = losses = partials = 0

        for key in selected_keys:
            by_strategy[key] = {
                "key": key,
                "name": registry[key]["name"],
                "trades": 0,
                "skipped_by_gate": 0,
                "total_pnl": 0.0,
                "account_delta": 0.0,
            }

        for r in rows:
            key = r.get("strategy_key") or "balanced"
            is_blocked = (
                apply_long_vol_gate
                and r.get("direction") == "LONG"
                and (r.get("volatility") or "") in blocked_vols
            )
            if is_blocked:
                skipped_by_gate.append(r)
                if key in by_strategy:
                    by_strategy[key]["skipped_by_gate"] += 1
                continue

            pnl_pct = float(r.get("pnl_pct") or 0.0)
            before = balance
            account_delta = balance * (risk_pct / 100.0) * (pnl_pct / 100.0)
            balance = max(0.0, balance + account_delta)
            peak = max(peak, balance)
            if peak > 0:
                drawdown = (peak - balance) / peak * 100.0
                max_drawdown_pct = max(max_drawdown_pct, drawdown)

            if r.get("result") == "WIN":
                wins += 1
            elif r.get("result") == "LOSS":
                losses += 1
            elif r.get("result") == "PARTIAL":
                partials += 1

            if key in by_strategy:
                by_strategy[key]["trades"] += 1
                by_strategy[key]["total_pnl"] += pnl_pct
                by_strategy[key]["account_delta"] += account_delta

            equity.append({
                "id": r.get("id"),
                "logged_at": r.get("logged_at"),
                "symbol": r.get("symbol"),
                "strategy_key": key,
                "strategy": r.get("strategy"),
                "direction": r.get("direction"),
                "volatility": r.get("volatility"),
                "result": r.get("result"),
                "pnl_pct": round(pnl_pct, 2),
                "account_delta": round(account_delta, 2),
                "balance": round(balance, 2),
                "balance_before": round(before, 2),
            })

        trades = len(equity)
        closed_considered = len(rows)
        win_rate = wins / trades if trades else None
        summary = {
            "account_start": round(account_start, 2),
            "account_final": round(balance, 2),
            "return_pct": round((balance / account_start - 1) * 100.0, 2),
            "risk_pct": round(risk_pct, 4),
            "trades": trades,
            "closed_considered": closed_considered,
            "skipped_by_gate": len(skipped_by_gate),
            "shadow_filtered_logged": filtered_logged,
            "wins": wins,
            "losses": losses,
            "partials": partials,
            "win_rate": round(win_rate, 4) if win_rate is not None else None,
            "max_drawdown_pct": round(max_drawdown_pct, 2),
            "long_vol_gate": apply_long_vol_gate,
        }

        by_strategy_rows = []
        for d in by_strategy.values():
            trades_n = d["trades"]
            by_strategy_rows.append({
                "key": d["key"],
                "name": d["name"],
                "trades": trades_n,
                "skipped_by_gate": d["skipped_by_gate"],
                "total_pnl": round(d["total_pnl"], 2),
                "avg_pnl": round(d["total_pnl"] / trades_n, 2) if trades_n else None,
                "account_delta": round(d["account_delta"], 2),
            })

        return jsonify({
            "success": True,
            "selected_strategies": selected_keys,
            "summary": summary,
            "by_strategy": by_strategy_rows,
            "equity": equity,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/risk-gates")
def api_risk_gates():
    """Return live risk-gate config plus lightweight audit stats."""
    try:
        gates = load_risk_gates()
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row

        summaries: dict[str, dict] = {}
        for key, cfg in gates.items():
            rows = [
                dict(r) for r in con.execute("""
                    SELECT gate_mode, COUNT(*) AS n, MAX(logged_at) AS last_seen
                    FROM filtered_candidates
                    WHERE gate_key=?
                    GROUP BY gate_mode
                """, (key,)).fetchall()
            ]
            total_logged = sum(int(r.get("n") or 0) for r in rows)
            mode_counts = {r.get("gate_mode") or "unknown": int(r.get("n") or 0) for r in rows}
            last_seen = max((r.get("last_seen") for r in rows if r.get("last_seen")), default=None)
            summaries[key] = {
                "total_logged": total_logged,
                "mode_counts": mode_counts,
                "last_seen": last_seen,
            }

        for key, cfg in gates.items():
            direction = str(cfg.get("direction") or "").upper()
            vols = [str(v) for v in (cfg.get("volatility") or []) if str(v)]
            strategy_scope = cfg.get("strategy_scope")
            historical_rows = []
            if direction and vols:
                placeholders = ",".join("?" for _ in vols)
                params: list = [direction, *vols]
                scope_sql = ""
                if strategy_scope:
                    scope_sql = " AND strategy_key=?"
                    params.append(strategy_scope)
                historical_rows = [
                    dict(r) for r in con.execute(f"""
                        SELECT result, pnl_pct FROM signals
                        WHERE direction=?
                          AND volatility IN ({placeholders})
                          {scope_sql}
                          AND result IS NOT NULL
                          AND result NOT IN ('SKIPPED', 'EXPIRED')
                          AND pnl_pct IS NOT NULL
                    """, params).fetchall()
                ]

            pnl_vals = [float(r.get("pnl_pct") or 0) for r in historical_rows]
            total_pnl = sum(pnl_vals)
            wins = sum(1 for r in historical_rows if r.get("result") == "WIN")
            trades = len(historical_rows)
            win_rate = wins / trades if trades else None
            recommendation = "collect_data"
            if trades >= 20 and total_pnl < 0:
                recommendation = "keep_blocking"
            elif trades >= 20 and total_pnl >= 0:
                recommendation = "review_loosen"

            summaries[key]["historical_impact"] = {
                "matched_closed_trades": trades,
                "total_pnl": round(total_pnl, 2),
                "avg_pnl": round(total_pnl / trades, 2) if trades else None,
                "win_rate": round(win_rate, 4) if win_rate is not None else None,
                "recommendation": recommendation,
                "volatility": sorted(vols),
                "direction": direction,
                "strategy_scope": strategy_scope,
            }
        con.close()

        sym_perf = _load_symbol_performance_cache(min_trades=5)
        penalty_symbols = []
        for sym, data in sorted(sym_perf.items(), key=lambda x: x[1]['avg_pnl']):
            avg = data['avg_pnl']
            n = data['trade_count']
            if avg < -5:
                tier = 'severe' if avg < -30 else 'moderate' if avg < -15 else 'mild'
                penalty_symbols.append({
                    'symbol': sym,
                    'avg_pnl': avg,
                    'trade_count': n,
                    'tier': tier,
                    'penalty': -20 if avg < -30 else -10 if avg < -15 else -5
                })

        sym_overrides = _get_symbol_overrides()
        response = {
            "success": True,
            "gates": gates,
            "summaries": summaries,
            "modes": ["block", "shadow", "off"],
            "symbol_performance": {
                "penalty_count": len(penalty_symbols),
                "symbols": penalty_symbols[:20],
                "overrides": sym_overrides,
            },
        }
        return jsonify(response)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/risk-gates/<gate_key>", methods=["PATCH"])
def api_update_risk_gate(gate_key: str):
    """Update a risk gate's live mode. Config is local and gitignored under data/."""
    try:
        gates = load_risk_gates()
        if gate_key not in gates:
            return jsonify({"success": False, "error": "risk gate not found"}), 404
        body = request.get_json(silent=True) or {}
        mode = str(body.get("mode", gates[gate_key].get("mode", "block"))).strip().lower()
        if mode not in {"block", "shadow", "off"}:
            return jsonify({"success": False, "error": "mode must be block, shadow, or off"}), 400
        gates[gate_key]["mode"] = mode
        save_risk_gates(gates)
        return jsonify({"success": True, "gate": gates[gate_key], "gates": gates})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/strategies/custom", methods=["POST"])
def api_create_custom_strategy():
    """Create a custom strategy by cloning a built-in and applying validated overrides."""
    try:
        body = request.get_json(force=True) or {}
        cfg, err = validate_custom_strategy_payload(body)
        if err:
            return jsonify({"success": False, "error": err}), 400

        now = datetime.utcnow().isoformat()
        row_json_dict = {
            "description": cfg["description"],
            "risk_level": cfg["risk_level"],
            "weights": cfg["weights"],
            "leverage_cap": cfg["leverage_cap"],
            "min_conviction": cfg["min_conviction"],
            "filters": cfg["filters"],
            "regime": cfg["regime"],
        }
        if cfg.get("direction_lock") is not None:
            row_json_dict["direction_lock"] = cfg["direction_lock"]
        if cfg.get("allowed_volatility") is not None:
            row_json_dict["allowed_volatility"] = cfg["allowed_volatility"]
        row_json = json.dumps(row_json_dict, sort_keys=True)

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
            "direction_lock": existing.get("direction_lock", None),
            "allowed_volatility": existing.get("allowed_volatility", None),
        }
        for field in ("name", "enabled", "description", "risk_level", "weights", "filters", "min_conviction", "leverage_cap", "max_leverage", "regime", "direction_lock", "allowed_volatility"):
            if field in body:
                merged[field] = body[field]

        cfg, err = validate_custom_strategy_payload(merged, existing_key=key)
        if err:
            return jsonify({"success": False, "error": err}), 400

        row_json_dict = {
            "description": cfg["description"],
            "risk_level": cfg["risk_level"],
            "weights": cfg["weights"],
            "leverage_cap": cfg["leverage_cap"],
            "min_conviction": cfg["min_conviction"],
            "filters": cfg["filters"],
            "regime": cfg["regime"],
        }
        if cfg.get("direction_lock") is not None:
            row_json_dict["direction_lock"] = cfg["direction_lock"]
        if cfg.get("allowed_volatility") is not None:
            row_json_dict["allowed_volatility"] = cfg["allowed_volatility"]
        row_json = json.dumps(row_json_dict, sort_keys=True)

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


@app.route("/api/strategies/builtin/<strategy_key>", methods=["PATCH"])
def api_toggle_builtin_strategy(strategy_key: str):
    """Enable or disable a built-in strategy. State persists in risk_gates.json."""
    try:
        key = strategy_key.strip().lower()
        if key not in STRATEGIES:
            return jsonify({"success": False, "error": "not a built-in strategy key"}), 404
        body = request.get_json(silent=True) or {}
        if "enabled" not in body:
            return jsonify({"success": False, "error": "'enabled' field required"}), 400
        enable = bool(body["enabled"])
        disabled = _get_disabled_builtins()
        if enable:
            disabled.discard(key)
        else:
            disabled.add(key)
        _set_disabled_builtins(disabled)
        cfg = builtin_strategy_config(key, STRATEGIES[key])
        return jsonify({"success": True, "strategy": strategy_to_api(key, cfg)})
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
# Maintenance routes
# ---------------------------------------------------------------------------

@app.route("/api/backfill/pnl", methods=["POST"])
def api_backfill_pnl():
    """
    MAINTENANCE — Backfill pnl_pct (and corrected exit_price) for historical
    signals that were evaluated before the pnl_pct column and blended PARTIAL
    fix existed.

    Queries signals WHERE result IS NOT NULL AND pnl_pct IS NULL
    AND exit_price IS NOT NULL AND entry1 IS NOT NULL, re-runs evaluate_outcome()
    against live MEXC kline data, and writes corrected exit_price, pnl_pct,
    entry_at, and evaluation_version='backfill_v1'.

    Safe to call multiple times — the pnl_pct IS NULL filter skips already-
    backfilled rows. POST to prevent accidental browser trigger.
    """
    updated = 0
    skipped = 0
    errors  = 0

    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        rows = con.execute("""
            SELECT * FROM signals
            WHERE result IS NOT NULL
              AND pnl_pct IS NULL
              AND result NOT IN ('EXPIRED', 'SKIPPED')
              AND entry1 IS NOT NULL
            ORDER BY logged_at ASC
        """).fetchall()
        con.close()

        sigs = [dict(r) for r in rows]
        print(f"[backfill/pnl] {len(sigs)} signals to backfill", file=sys.stderr)

        for sig in sigs:
            try:
                outcome = evaluate_outcome(sig)
                if outcome is None:
                    skipped += 1
                    continue

                result, note, exit_price, result_at, entry_at = outcome
                pnl_pct = _compute_leveraged_pnl(sig, exit_price)

                con = sqlite3.connect(DB_PATH)
                con.execute("""
                    UPDATE signals
                    SET exit_price=?, pnl_pct=?, entry_at=?,
                        evaluation_version='backfill_v1'
                    WHERE id=?
                """, (exit_price, pnl_pct, entry_at, sig["id"]))
                con.commit()
                con.close()
                updated += 1

            except Exception as e:
                print(f"[backfill/pnl] error on id={sig.get('id')}: {e}", file=sys.stderr)
                errors += 1

            time.sleep(0.1)  # rate-limit MEXC API calls

        print(f"[backfill/pnl] done — updated:{updated} skipped:{skipped} errors:{errors}",
              file=sys.stderr)
        return jsonify({"success": True, "updated": updated, "skipped": skipped, "errors": errors})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/cleanup/phantom-events", methods=["POST"])
def api_cleanup_phantom_events():
    """
    MAINTENANCE — Delete TP/SL events in position_events that were logged for
    signals whose entry was never confirmed (entry_at IS NULL in the signals table).

    These phantom events arise when evaluate_outcome() logs ENTRY_FILLED + TP events
    in a single candle scan but the signals table entry_at column was never written
    (because the function returned None for partial TP hits).

    Does NOT delete ENTRY_FILLED rows — those would indicate a different class of bug.
    Idempotent. Safe to call repeatedly. POST only to prevent accidental browser trigger.
    """
    try:
        con = sqlite3.connect(DB_PATH)
        affected_ids = con.execute("""
            SELECT DISTINCT signal_id FROM position_events
             WHERE signal_id IN (SELECT id FROM signals WHERE entry_at IS NULL)
               AND event_type IN ('TP1_HIT','TP2_HIT','TP3_HIT','STOP_HIT')
        """).fetchall()
        affected_signals = len(affected_ids)

        cur = con.execute("""
            DELETE FROM position_events
             WHERE signal_id IN (SELECT id FROM signals WHERE entry_at IS NULL)
               AND event_type IN ('TP1_HIT','TP2_HIT','TP3_HIT','STOP_HIT')
        """)
        deleted = cur.rowcount
        con.commit()
        con.close()

        print(f"[cleanup] phantom-events deleted={deleted} affected_signals={affected_signals}",
              file=sys.stderr)
        return jsonify({"success": True, "deleted": deleted, "affected_signals": affected_signals})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# P8 — Account integration + Bot Readiness
# ---------------------------------------------------------------------------

@app.route("/api/risk-gates/symbol-override", methods=["POST"])
def api_symbol_override_add():
    """
    Body: {symbol, action, reason}
    action: "exempt" | "force_mild" | "force_moderate" | "force_severe"
    """
    try:
        body = request.get_json(force=True) or {}
        symbol = (body.get('symbol') or '').strip().upper()
        action = body.get('action', '')
        reason = body.get('reason', '')
        valid_actions = {'exempt', 'force_mild', 'force_moderate', 'force_severe'}
        if not symbol or action not in valid_actions:
            return jsonify({'success': False, 'error': 'symbol and valid action required'}), 400
        gates = load_risk_gates()
        if 'symbol_overrides' not in gates:
            gates['symbol_overrides'] = {}
        gates['symbol_overrides'][symbol] = {'action': action, 'reason': reason}
        save_risk_gates(gates)
        return jsonify({'success': True, 'symbol': symbol, 'action': action})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route("/api/risk-gates/symbol-override/<symbol>", methods=["DELETE"])
def api_symbol_override_remove(symbol):
    """Removes a symbol from symbol_overrides in risk_gates.json."""
    try:
        symbol = symbol.strip().upper()
        gates = load_risk_gates()
        overrides = _get_symbol_overrides()
        if symbol not in overrides:
            return jsonify({'success': False, 'error': f'{symbol} not in overrides'}), 404
        del overrides[symbol]
        gates['symbol_overrides'] = overrides
        save_risk_gates(gates)
        return jsonify({'success': True, 'removed': symbol})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route("/api/account/readiness")
def api_account_readiness():
    """Bot readiness metrics computed from signals.db. No MEXC auth needed."""
    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute("""
            SELECT strategy_key,
                   COUNT(*) AS closed_trades,
                   SUM(CASE WHEN pnl_pct IS NOT NULL THEN 1 ELSE 0 END) AS trades_with_pnl,
                   AVG(pnl_pct) AS avg_pnl,
                   SUM(CASE WHEN pnl_pct > 0 THEN pnl_pct ELSE 0 END) AS gross_wins,
                   SUM(CASE WHEN pnl_pct < 0 THEN ABS(pnl_pct) ELSE 0 END) AS gross_losses
            FROM signals
            WHERE result IS NOT NULL AND result NOT IN ('EXPIRED', 'SKIPPED')
            GROUP BY strategy_key
        """)
        rows = cur.fetchall()

        out = {}
        for row in rows:
            key = row["strategy_key"] or "balanced"
            trades_with_pnl = row["trades_with_pnl"] or 0
            avg_pnl = row["avg_pnl"]
            gross_wins = row["gross_wins"] or 0.0
            gross_losses = row["gross_losses"] or 0.0
            profit_factor = round(gross_wins / gross_losses, 3) if gross_losses > 0 else None
            win_rate = None
            if trades_with_pnl > 0:
                cur.execute(
                    "SELECT COUNT(*) FROM signals WHERE strategy_key=? AND pnl_pct > 0",
                    (key,)
                )
                wins = cur.fetchone()[0]
                win_rate = round(wins / trades_with_pnl, 4)

            trades_score = min(trades_with_pnl / 300, 1.0) * 40
            pf_score = min((profit_factor or 0) / 1.3, 1.0) * 40
            pnl_score = 20 if (avg_pnl is not None and avg_pnl > 0) else 0
            readiness_pct = int(trades_score + pf_score + pnl_score)

            out[key] = {
                "strategy_key": key,
                "closed_trades": row["closed_trades"],
                "trades_with_pnl": trades_with_pnl,
                "avg_pnl": round(avg_pnl, 2) if avg_pnl is not None else None,
                "profit_factor": profit_factor,
                "win_rate": win_rate,
                "gross_wins": round(gross_wins, 2),
                "gross_losses": round(gross_losses, 2),
                "readiness_pct": readiness_pct,
            }
        con.close()
        return jsonify({"success": True, "readiness": out})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/account/status")
def api_account_status():
    api_key = os.getenv("MEXC_API_KEY", "").strip()
    api_secret = os.getenv("MEXC_API_SECRET", "").strip()
    if not api_key or not api_secret:
        return jsonify({"success": True, "connected": False,
                        "reason": "MEXC_API_KEY or MEXC_API_SECRET not configured"})
    try:
        from lib.mexc_private import get_account_summary
        summary = get_account_summary(api_key, api_secret)
        if not summary:
            return jsonify({"success": True, "connected": False,
                            "reason": "MEXC API returned empty response"})
        return jsonify({"success": True, "connected": True, "data": summary})
    except Exception as e:
        return jsonify({"success": True, "connected": False, "reason": str(e)})


def _hl_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


@app.route("/api/hl/account")
def api_hl_account():
    wallet = HL_WALLET_ADDRESS.strip()
    if not wallet:
        return jsonify({"success": True, "connected": False,
                        "reason": "HL_WALLET_ADDRESS not configured"})
    try:
        state = fetch_hl_account(wallet)
        if not state:
            return jsonify({"success": True, "connected": False,
                            "reason": "Hyperliquid API returned empty response"})

        margin = state.get("marginSummary") or state.get("crossMarginSummary") or {}
        positions = []
        unrealized_pnl = 0.0
        for item in state.get("assetPositions", []) or []:
            pos = item.get("position", item) if isinstance(item, dict) else {}
            size = _hl_float(pos.get("szi"))
            if not size:
                continue
            pnl = _hl_float(pos.get("unrealizedPnl"))
            unrealized_pnl += pnl
            positions.append({
                "symbol": f"{pos.get('coin', '')}_USDC" if pos.get("coin") else "",
                "coin": pos.get("coin"),
                "size": size,
                "entry_price": _hl_float(pos.get("entryPx"), None),
                "unrealized_pnl": pnl,
                "leverage": pos.get("leverage"),
            })

        summary = {
            "equity": _hl_float(margin.get("accountValue")),
            "available_margin": _hl_float(state.get("withdrawable") or margin.get("totalRawUsd")),
            "unrealized_pnl": unrealized_pnl,
            "open_positions": positions,
            "position_count": len(positions),
        }
        return jsonify({
            "success": True,
            "connected": True,
            "wallet": wallet,
            "data": summary,
        })
    except Exception as e:
        return jsonify({"success": True, "connected": False, "reason": str(e)})


@app.route("/api/account/positions")
def api_account_positions():
    api_key = os.getenv("MEXC_API_KEY", "").strip()
    api_secret = os.getenv("MEXC_API_SECRET", "").strip()
    if not api_key or not api_secret:
        return jsonify({"success": True, "connected": False, "positions": [],
                        "reason": "MEXC_API_KEY or MEXC_API_SECRET not configured"})
    try:
        from lib.mexc_private import get_open_positions
        positions = get_open_positions(api_key, api_secret)
        return jsonify({"success": True, "connected": True, "positions": positions})
    except Exception as e:
        return jsonify({"success": True, "connected": False, "positions": [], "reason": str(e)})


@app.route("/api/account/balance")
def api_account_balance():
    api_key = os.getenv("MEXC_API_KEY", "").strip()
    api_secret = os.getenv("MEXC_API_SECRET", "").strip()
    if not api_key or not api_secret:
        return jsonify({"success": True, "connected": False, "balance": None,
                        "reason": "MEXC_API_KEY or MEXC_API_SECRET not configured"})
    try:
        from lib.mexc_private import get_account_assets
        assets = get_account_assets(api_key, api_secret)
        if not assets:
            return jsonify({"success": True, "connected": False, "balance": None,
                            "reason": "MEXC API returned empty response"})
        return jsonify({"success": True, "connected": True, "balance": assets})
    except Exception as e:
        return jsonify({"success": True, "connected": False, "balance": None, "reason": str(e)})


# ---------------------------------------------------------------------------
# Intelligence tab routes
# ---------------------------------------------------------------------------

@app.route('/api/intelligence/status')
def api_intelligence_status():
    """Shadow validation status and agent findings from signals.db."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()

        # Count closed signals with agent shadow data
        cur.execute("""
            SELECT COUNT(*) FROM signals
            WHERE result IS NOT NULL
            AND json_extract(signal_json, '$.agent_regime') IS NOT NULL
        """)
        shadow_signal_count = cur.fetchone()[0] or 0

        shadow_target = 50
        phase = 'shadow'
        if shadow_signal_count >= shadow_target:
            phase = 'validating'

        # Criteria — null = insufficient data
        enough_signals = shadow_signal_count >= shadow_target

        positive_delta_better = None
        negative_delta_worse = None
        disagreement_penalty = None

        if shadow_signal_count >= 10:
            try:
                cur.execute("""
                    SELECT
                        AVG(CASE WHEN json_extract(signal_json, '$.agent_shadow_delta') > 5
                            AND result = 'WIN' THEN 1.0
                            WHEN json_extract(signal_json, '$.agent_shadow_delta') > 5
                            AND result IN ('LOSS','EXPIRED') THEN 0.0
                            END) as pos_wr,
                        AVG(CASE WHEN json_extract(signal_json, '$.agent_shadow_delta') <= 5
                            AND result = 'WIN' THEN 1.0
                            WHEN json_extract(signal_json, '$.agent_shadow_delta') <= 5
                            AND result IN ('LOSS','EXPIRED') THEN 0.0
                            END) as base_wr
                    FROM signals
                    WHERE result IS NOT NULL
                    AND json_extract(signal_json, '$.agent_shadow_delta') IS NOT NULL
                """)
                row = cur.fetchone()
                if row and row[0] is not None and row[1] is not None:
                    positive_delta_better = row[0] > row[1]
            except Exception:
                pass

            try:
                cur.execute("""
                    SELECT
                        AVG(CASE WHEN json_extract(signal_json, '$.agent_shadow_delta') < -5
                            AND result = 'WIN' THEN 1.0
                            WHEN json_extract(signal_json, '$.agent_shadow_delta') < -5
                            AND result IN ('LOSS','EXPIRED') THEN 0.0
                            END) as neg_wr,
                        AVG(CASE WHEN json_extract(signal_json, '$.agent_shadow_delta') >= -5
                            AND result = 'WIN' THEN 1.0
                            WHEN json_extract(signal_json, '$.agent_shadow_delta') >= -5
                            AND result IN ('LOSS','EXPIRED') THEN 0.0
                            END) as base_wr
                    FROM signals
                    WHERE result IS NOT NULL
                    AND json_extract(signal_json, '$.agent_shadow_delta') IS NOT NULL
                """)
                row = cur.fetchone()
                if row and row[0] is not None and row[1] is not None:
                    negative_delta_worse = row[0] < row[1]
            except Exception:
                pass

            try:
                cur.execute("""
                    SELECT
                        AVG(CASE WHEN json_extract(signal_json, '$.agent_shadow_disagreement') > 0.4
                            AND result = 'WIN' THEN 1.0
                            WHEN json_extract(signal_json, '$.agent_shadow_disagreement') > 0.4
                            AND result IN ('LOSS','EXPIRED') THEN 0.0
                            END) as hi_dis_wr,
                        AVG(CASE WHEN (json_extract(signal_json, '$.agent_shadow_disagreement') <= 0.4
                            OR json_extract(signal_json, '$.agent_shadow_disagreement') IS NULL)
                            AND result = 'WIN' THEN 1.0
                            WHEN (json_extract(signal_json, '$.agent_shadow_disagreement') <= 0.4
                            OR json_extract(signal_json, '$.agent_shadow_disagreement') IS NULL)
                            AND result IN ('LOSS','EXPIRED') THEN 0.0
                            END) as lo_dis_wr
                    FROM signals
                    WHERE result IS NOT NULL
                    AND json_extract(signal_json, '$.agent_shadow_disagreement') IS NOT NULL
                """)
                row = cur.fetchone()
                if row and row[0] is not None and row[1] is not None:
                    disagreement_penalty = row[0] < row[1]
            except Exception:
                pass

        # scan_time_ok: placeholder — true if we have no evidence of timeout
        scan_time_ok = True

        if enough_signals and positive_delta_better and negative_delta_worse and disagreement_penalty and scan_time_ok:
            phase = 'phase2_ready'

        # Regime distribution
        regime_distribution = {r: 0 for r in ['volatile_squeeze', 'news_catalyst', 'trending', 'choppy', 'institutional', 'unknown']}
        try:
            cur.execute("""
                SELECT json_extract(signal_json, '$.agent_regime') as regime, COUNT(*) as cnt
                FROM signals
                WHERE json_extract(signal_json, '$.agent_regime') IS NOT NULL
                GROUP BY regime
            """)
            for row in cur.fetchall():
                regime = row[0] if row[0] in regime_distribution else 'unknown'
                regime_distribution[regime] = regime_distribution.get(regime, 0) + row[1]
        except Exception:
            pass

        # Narrative / structural averages
        narrative_avg_bull = None
        structural_avg_bull = None
        avg_disagreement = None
        try:
            cur.execute("""
                SELECT
                    AVG(CAST(json_extract(signal_json, '$.agent_narrative_bull') AS REAL)),
                    AVG(CAST(json_extract(signal_json, '$.agent_structural_bull') AS REAL)),
                    AVG(CAST(json_extract(signal_json, '$.agent_shadow_disagreement') AS REAL))
                FROM signals
                WHERE json_extract(signal_json, '$.agent_regime') IS NOT NULL
            """)
            row = cur.fetchone()
            if row:
                narrative_avg_bull = round(row[0], 3) if row[0] is not None else None
                structural_avg_bull = round(row[1], 3) if row[1] is not None else None
                avg_disagreement = round(row[2], 3) if row[2] is not None else None
        except Exception:
            pass

        # Delta distribution
        delta_distribution = {'strong_boost': 0, 'mild_boost': 0, 'neutral': 0, 'mild_penalty': 0, 'strong_penalty': 0}
        try:
            cur.execute("""
                SELECT json_extract(signal_json, '$.agent_shadow_delta') as d
                FROM signals
                WHERE json_extract(signal_json, '$.agent_shadow_delta') IS NOT NULL
            """)
            for row in cur.fetchall():
                d = row[0]
                if d is None:
                    continue
                d = float(d)
                if d > 10:
                    delta_distribution['strong_boost'] += 1
                elif d > 1:
                    delta_distribution['mild_boost'] += 1
                elif d >= -1:
                    delta_distribution['neutral'] += 1
                elif d >= -10:
                    delta_distribution['mild_penalty'] += 1
                else:
                    delta_distribution['strong_penalty'] += 1
        except Exception:
            pass

        # Outcome correlation
        outcome_correlation = {
            'win': {'avg_delta': None, 'count': 0},
            'loss': {'avg_delta': None, 'count': 0},
            'partial': {'avg_delta': None, 'count': 0},
        }
        try:
            cur.execute("""
                SELECT result,
                    AVG(CAST(json_extract(signal_json, '$.agent_shadow_delta') AS REAL)) as avg_d,
                    COUNT(*) as cnt
                FROM signals
                WHERE result IS NOT NULL
                AND json_extract(signal_json, '$.agent_shadow_delta') IS NOT NULL
                AND result IN ('WIN','LOSS','PARTIAL')
                GROUP BY result
            """)
            for row in cur.fetchall():
                key = row[0].lower()
                if key in outcome_correlation:
                    outcome_correlation[key]['avg_delta'] = round(row[1], 3) if row[1] is not None else None
                    outcome_correlation[key]['count'] = row[2]
        except Exception:
            pass

        conn.close()
        return jsonify({
            'success': True,
            'phase': phase,
            'shadow_signal_count': shadow_signal_count,
            'shadow_target': shadow_target,
            'criteria': {
                'enough_signals': enough_signals,
                'positive_delta_better': positive_delta_better,
                'negative_delta_worse': negative_delta_worse,
                'disagreement_penalty': disagreement_penalty,
                'scan_time_ok': scan_time_ok,
            },
            'regime_distribution': regime_distribution,
            'narrative_avg_bull': narrative_avg_bull,
            'structural_avg_bull': structural_avg_bull,
            'avg_disagreement': avg_disagreement,
            'delta_distribution': delta_distribution,
            'outcome_correlation': outcome_correlation,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/intelligence/suggestions')
def api_intelligence_suggestions():
    """Read pending.json from the external learner service."""
    pending_path = '/opt/mt-learner/suggestions/pending.json'
    heartbeat_path = '/opt/mt-learner/logs/last_heartbeat.txt'
    learner_running = False

    if os.path.exists(heartbeat_path):
        try:
            mtime = os.path.getmtime(heartbeat_path)
            learner_running = (datetime.utcnow().timestamp() - mtime) < 600
        except Exception:
            pass

    if not os.path.exists(pending_path):
        return jsonify({'success': True, 'suggestions': [], 'learner_running': learner_running})

    try:
        with open(pending_path, 'r') as f:
            data = json.load(f)
        suggestions = data.get('suggestions', [])
        return jsonify({'success': True, 'suggestions': suggestions, 'learner_running': learner_running})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'suggestions': [], 'learner_running': learner_running})


@app.route('/api/intelligence/suggestions/<suggestion_id>', methods=['PATCH'])
def api_intelligence_suggestion_action(suggestion_id):
    """Apply or dismiss a learner suggestion."""
    pending_path = '/opt/mt-learner/suggestions/pending.json'
    data = request.get_json() or {}
    action = data.get('action')
    if action not in ('apply', 'dismiss'):
        return jsonify({'success': False, 'error': 'action must be apply or dismiss'}), 400

    if not os.path.exists(pending_path):
        return jsonify({'success': False, 'error': 'pending.json not found'}), 404

    try:
        with open(pending_path, 'r') as f:
            pending = json.load(f)
    except Exception as e:
        return jsonify({'success': False, 'error': f'failed to read pending.json: {e}'}), 500

    suggestions = pending.get('suggestions', [])
    suggestion = next((s for s in suggestions if s.get('id') == suggestion_id), None)
    if not suggestion:
        return jsonify({'success': False, 'error': 'suggestion not found'}), 404

    if action == 'dismiss':
        suggestion['status'] = 'dismissed'
    elif action == 'apply':
        stype = suggestion.get('type')
        payload = suggestion.get('api_payload', {})
        strategy_key = suggestion.get('strategy')
        try:
            if stype == 'new_strategy':
                with app.test_client() as c:
                    r = c.post('/api/strategies/custom',
                               data=json.dumps(payload),
                               content_type='application/json')
                    if r.status_code not in (200, 201):
                        return jsonify({'success': False, 'error': 'failed to create strategy'}), 500
            elif stype in ('threshold', 'regime_suppress') and strategy_key:
                with app.test_client() as c:
                    r = c.patch(f'/api/strategies/custom/{strategy_key}',
                                data=json.dumps(payload),
                                content_type='application/json')
                    if r.status_code not in (200, 201):
                        return jsonify({'success': False, 'error': 'failed to update strategy'}), 500
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500
        suggestion['status'] = 'applied'

    tmp_path = pending_path + '.tmp'
    try:
        with open(tmp_path, 'w') as f:
            json.dump(pending, f, indent=2)
        os.replace(tmp_path, pending_path)
    except Exception as e:
        return jsonify({'success': False, 'error': f'failed to write pending.json: {e}'}), 500

    return jsonify({'success': True, 'applied': action == 'apply', 'suggestion_id': suggestion_id})


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
