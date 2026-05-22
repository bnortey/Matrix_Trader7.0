import os
import sys
import math
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
from lib.risk_controls import compute_daily_pnl, compute_position_size, get_readiness_verdict
from lib.order_flow import flow_confirm as _flow_confirm
from lib.exchange_data import (
    normalize_ticker_for_scoring,
    fetch_klines,
    fetch_depth,
    fetch_next_funding_minutes,
    fetch_daily_klines_raw,
    fetch_tickers as _fetch_exchange_tickers,
    SUPPORTED_EXCHANGES,
)
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

# Stage 1 scoring version. v1 = step-function (legacy; 5.1% rally and 50%
# rally get the same momentum score). v2 = continuous saturating ramp so
# magnitude matters. Default v1 until A/B reconstruction on closed signals
# shows v2 widens the winner/loser conviction divergence beyond the
# current 0.56-point floor. Audit §02 structural fix.
# Set SCORE_VERSION=v2 in .env to activate. Signals are tagged with the
# version that scored them so analyze.py can split apart the two cohorts.
SCORE_VERSION = (os.getenv("SCORE_VERSION") or "v1").strip().lower()
if SCORE_VERSION not in ("v1", "v2"):
    print(f"[score] Unknown SCORE_VERSION={SCORE_VERSION!r}, falling back to v1", file=sys.stderr)
    SCORE_VERSION = "v1"

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True


# ---------------------------------------------------------------------------
# Bearer-token auth middleware (audit §07 fix)
# ---------------------------------------------------------------------------
#
# Behavior:
#   - If MT7_API_TOKEN env var is empty/unset, no auth required (default;
#     preserves local-dev workflow and existing VPS behavior on rollout).
#   - When MT7_API_TOKEN is set, every mutating request (POST/PATCH/PUT/DELETE)
#     must carry  Authorization: Bearer <MT7_API_TOKEN>  or it gets 401.
#   - GET/HEAD/OPTIONS stay open so the dashboard can still load read-only
#     analytics and the iPhone-on-LAN flow keeps working without a config change.
#   - "/" and "/static/*" stay open so the SPA itself can load; the JS wrapper
#     in templates/index.html then attaches the token to every fetch().
#
# Bootstrap: generate a token with
#   python -c "import secrets; print(secrets.token_urlsafe(32))"
# and set MT7_API_TOKEN=... in .env on the VPS. Then visit the dashboard once
# with  ?token=YOUR_TOKEN  in the URL; the JS captures it into sessionStorage
# and re-uses it for the session.
#
import hmac as _hmac

@app.before_request
def _enforce_bearer_token():
    if not MT7_API_TOKEN:
        return None
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    if request.path == "/" or request.path.startswith("/static/"):
        return None
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return jsonify({
            "success": False,
            "error": "auth required: send Authorization: Bearer <MT7_API_TOKEN>",
        }), 401
    presented = auth_header[len("Bearer "):].strip()
    # Constant-time comparison to avoid timing oracle.
    if not _hmac.compare_digest(presented, MT7_API_TOKEN):
        return jsonify({"success": False, "error": "invalid token"}), 401
    return None


MEXC_BASE    = "https://contract.mexc.com/api/v1"
BINANCE_FAPI = "https://fapi.binance.com"
BYBIT_BASE   = "https://api.bybit.com"

# Major pairs that exist on Binance/Bybit futures — only these get sentiment calls.
# MEXC has 800+ pairs; calling Binance/Bybit for obscure altcoins wastes time and hits 400s.
SENTIMENT_PAIRS = {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT"}

PORT = int(os.getenv("MATRIX_PORT", "8080"))
HL_WALLET_ADDRESS    = os.getenv("HL_WALLET_ADDRESS", "")
HL_PRIVATE_KEY       = os.getenv("HL_PRIVATE_KEY", "")
LIVE_TRADING_ENABLED = os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true"
MT7_API_TOKEN        = (os.getenv("MT7_API_TOKEN") or "").strip()
MAX_DAILY_LOSS_USDT  = float(os.getenv("MAX_DAILY_LOSS_USDT") or "0")
# Kill-switch cooldown: in-memory timestamp of last kill_switch invocation.
# Within 60 seconds of a kill switch firing, /api/execution/place refuses
# new orders. Audit §07 kill_switch_cooldown_001.
_KILL_SWITCH_LAST_FIRED_TS: float = 0.0
_KILL_SWITCH_COOLDOWN_S: int = 60
CONVICTION_THRESHOLD = 55   # signals below this are filtered from results
KLINE_INTERVAL = "Min60"    # 1h candles — 100 candles default = ~4 days, plenty for 14-period indicators
ENRICH_TOP_N = 30           # enrich only the top N base signals to limit API calls
AGENT_TOP_N = 10            # run agent pipeline only on the top N base signals
ENRICH_WORKERS = 10         # concurrent threads for stage-2 enrichment
DB_PATH = "data/signals.db"
RISK_GATES_PATH = "data/risk_gates.json"
STRATEGY_OVERRIDES_PATH = "data/strategy_overrides.json"
AI_SETTINGS_PATH   = "data/ai_settings.json"
PAPER_CONFIG_PATH  = "data/paper_config.json"
REPORT_NARRATIVE_MODE = (os.getenv("REPORT_NARRATIVE_MODE") or "free").strip().lower()
REPORT_FREE_AI_PROVIDERS = {"gemini", "groq", "ollama"}

_PAPER_CONFIG_DEFAULT = {
    "enabled":               False,
    "size_usd":              100,
    "disabled_strategies":   [],       # list of strategy keys to skip; empty = run all
    "min_conviction":        55,
    "flow_required":         True,
    "min_flow_score":        50.0,
    "scan_interval_minutes": 5,
    "max_open_positions":    5,
    # Data-driven gates from analyze.py: winners avg atr_pct=3.2% vs losers 5.5%;
    # winners avg trend_score=9.3 vs losers 14.1. These outpredict conviction score
    # (0.56-point separation) by a wide margin.
    "max_atr_pct":           4.0,
    "max_trend_score_abs":   25,
}

# Daily kline cache: symbol → (fetched_at_ts, data)
_daily_kline_cache: dict = {}
_DAILY_KLINE_TTL = 300  # 5 minutes

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
    os.makedirs("data/reports", exist_ok=True)
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
    try:
        con.execute("ALTER TABLE signals ADD COLUMN flow_score REAL DEFAULT NULL")
        con.commit()
    except sqlite3.OperationalError:
        pass
    try:
        con.execute("ALTER TABLE signals ADD COLUMN flow_confirmed INTEGER DEFAULT NULL")
        con.commit()
    except sqlite3.OperationalError:
        pass
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
    con.execute("""
        CREATE TABLE IF NOT EXISTS ticker_snapshots (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            ts             TEXT NOT NULL,
            symbol         TEXT NOT NULL,
            exchange       TEXT NOT NULL DEFAULT 'MEXC',
            price          REAL,
            volume_24h     REAL,
            funding_rate   REAL,
            change_24h_pct REAL,
            open_interest  REAL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_ticker_snap_sym_ts ON ticker_snapshots (symbol, ts)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ticker_snap_ts    ON ticker_snapshots (ts)")
    con.commit()
    con.execute("""
        CREATE TABLE IF NOT EXISTS market_context_snapshots (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            ts             TEXT NOT NULL,
            btc_price      REAL,
            btc_rsi_1h     REAL,
            btc_trend      TEXT,
            btc_change_24h REAL,
            eth_price      REAL,
            eth_rsi_1h     REAL,
            eth_trend      TEXT,
            btc_ls_ratio   REAL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_mktctx_ts ON market_context_snapshots (ts)")
    con.commit()
    con.execute("""
        CREATE TABLE IF NOT EXISTS paper_trades (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            opened_at      TEXT NOT NULL,
            symbol         TEXT NOT NULL,
            strategy_key   TEXT NOT NULL,
            direction      TEXT NOT NULL,
            entry_px       REAL NOT NULL,
            size_usd       REAL NOT NULL DEFAULT 100,
            tp1            REAL,
            tp2            REAL,
            tp3            REAL,
            stop_loss      REAL,
            leverage       REAL DEFAULT 1,
            conviction     INTEGER,
            flow_confirmed INTEGER DEFAULT 0,
            flow_score     REAL DEFAULT 0,
            flow_reasons   TEXT,
            status         TEXT DEFAULT 'open',
            closed_at      TEXT,
            exit_px        REAL,
            result         TEXT,
            pnl_pct        REAL,
            signal_id      INTEGER,
            note           TEXT
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_paper_status ON paper_trades (status, opened_at)")
    con.commit()
    con.close()


def _snapshot_tickers() -> int:
    """Snapshot all MEXC tickers into ticker_snapshots. Returns row count. Prunes rows older than 7 days."""
    try:
        tickers = fetch_mexc("/contract/ticker")
        if not tickers or not isinstance(tickers, list):
            return 0
        ts = datetime.utcnow().isoformat()
        rows = []
        for t in tickers:
            symbol = t.get("symbol", "")
            if not symbol:
                continue
            price  = float(t.get("lastPrice") or t.get("fairPrice") or 0)
            volume = float(t.get("volume24") or t.get("vol24h") or t.get("amount24") or 0)
            funding = float(t.get("fundingRate") or 0)
            change  = float(t.get("riseFallRate") or 0) * 100
            oi      = float(t.get("holdVol") or 0)
            rows.append((ts, symbol, "MEXC", price, volume, funding, change, oi))
        con = sqlite3.connect(DB_PATH)
        con.executemany(
            "INSERT INTO ticker_snapshots (ts,symbol,exchange,price,volume_24h,funding_rate,change_24h_pct,open_interest) VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )
        cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
        con.execute("DELETE FROM ticker_snapshots WHERE ts < ?", (cutoff,))
        con.commit()
        con.close()
        return len(rows)
    except Exception as e:
        print(f"[snapshot_tickers] {e}", file=sys.stderr)
        return 0


def _snapshot_market_context() -> bool:
    """Fetch BTC+ETH 1h RSI/trend and store in market_context_snapshots. Prunes rows older than 30 days."""
    try:
        def _coin_ctx(symbol):
            data = fetch_mexc(f"/contract/kline/{symbol}", params={"interval": "Min60", "limit": 100})
            if not data or not isinstance(data, dict):
                return None, None, None, None
            df = pd.DataFrame({
                "open":   data.get("open", []),
                "high":   data.get("high", []),
                "low":    data.get("low", []),
                "close":  data.get("close", []),
                "volume": data.get("vol", []),
            }).astype(float)
            if len(df) < 16:
                return None, None, None, None
            price = float(df["close"].iloc[-1])
            rsi_s = calc_rsi(df, 14).dropna()
            rsi   = float(rsi_s.iloc[-1]) if not rsi_s.empty else None
            try:
                ema20 = float(calc_ema(df, 20).dropna().iloc[-1])
                ema50 = float(calc_ema(df, 50).dropna().iloc[-1])
                trend = "BULLISH" if ema20 > ema50 else "BEARISH"
            except Exception:
                trend = "NEUTRAL"
            change_24h = None
            if len(df) >= 25:
                old = float(df["close"].iloc[-25])
                if old > 0:
                    change_24h = round((price - old) / old * 100, 4)
            return price, rsi, trend, change_24h

        ts = datetime.utcnow().isoformat()
        btc_price, btc_rsi, btc_trend, btc_change = _coin_ctx("BTC_USDT")
        eth_price, eth_rsi, eth_trend, _           = _coin_ctx("ETH_USDT")

        con = sqlite3.connect(DB_PATH)
        con.execute(
            "INSERT INTO market_context_snapshots (ts,btc_price,btc_rsi_1h,btc_trend,btc_change_24h,eth_price,eth_rsi_1h,eth_trend) VALUES (?,?,?,?,?,?,?,?)",
            (ts, btc_price, btc_rsi, btc_trend, btc_change, eth_price, eth_rsi, eth_trend),
        )
        cutoff = (datetime.utcnow() - timedelta(days=30)).isoformat()
        con.execute("DELETE FROM market_context_snapshots WHERE ts < ?", (cutoff,))
        con.commit()
        con.close()
        return True
    except Exception as e:
        print(f"[snapshot_market_context] {e}", file=sys.stderr)
        return False


def get_latest_market_context() -> dict | None:
    """Return the most recent market_context_snapshots row as a dict, or None."""
    try:
        con = sqlite3.connect(DB_PATH)
        row = con.execute(
            "SELECT ts,btc_price,btc_rsi_1h,btc_trend,btc_change_24h,eth_price,eth_rsi_1h,eth_trend FROM market_context_snapshots ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        con.close()
        if not row:
            return None
        return {
            "ts": row[0], "btc_price": row[1], "btc_rsi_1h": row[2],
            "btc_trend": row[3], "btc_change_24h": row[4],
            "eth_price": row[5], "eth_rsi_1h": row[6], "eth_trend": row[7],
        }
    except Exception:
        return None


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
                 tags, signal_why, signal_json, data_quality, leverage,
                 flow_score, flow_confirmed)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                sig.get("flow_score"),
                1 if sig.get("flow_confirmed") else (0 if sig.get("flow_confirmed") is False else None),
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


def _load_extreme_vol_firebreak() -> bool:
    """
    Read extreme_vol_firebreak from risk_gates.json.
    Returns True (gate ON) if the file is missing, malformed, or the key is absent.
    Never raises — scan must never fail due to a missing settings file.
    """
    try:
        if os.path.exists(RISK_GATES_PATH):
            with open(RISK_GATES_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return bool(raw.get("extreme_vol_firebreak", True))
    except Exception:
        pass
    return True


def _save_extreme_vol_firebreak(val: bool) -> None:
    """Persist extreme_vol_firebreak to risk_gates.json, preserving all existing keys."""
    os.makedirs("data", exist_ok=True)
    try:
        with open(RISK_GATES_PATH, "r", encoding="utf-8") as f:
            existing = json.load(f)
    except Exception:
        existing = {}
    existing["extreme_vol_firebreak"] = bool(val)
    tmp = RISK_GATES_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, sort_keys=True)
    os.replace(tmp, RISK_GATES_PATH)


def _load_strategy_overrides() -> dict:
    """
    Read data/strategy_overrides.json.
    Returns {} if missing or malformed. Never raises.
    Only min_conviction overrides are supported in this phase.
    Structure: {"balanced": {"min_conviction": 65}, ...}
    """
    try:
        if os.path.exists(STRATEGY_OVERRIDES_PATH):
            with open(STRATEGY_OVERRIDES_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_strategy_override(strategy_key: str, field: str, value) -> None:
    """
    Persist a single field override for a built-in strategy.
    Preserves all existing overrides. Atomic write.
    Only permitted fields: ["min_conviction"]
    """
    PERMITTED = {"min_conviction"}
    if field not in PERMITTED:
        return
    os.makedirs("data", exist_ok=True)
    try:
        with open(STRATEGY_OVERRIDES_PATH, "r", encoding="utf-8") as f:
            existing = json.load(f)
    except Exception:
        existing = {}
    if strategy_key not in existing:
        existing[strategy_key] = {}
    existing[strategy_key][field] = value
    tmp = STRATEGY_OVERRIDES_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, sort_keys=True)
    os.replace(tmp, STRATEGY_OVERRIDES_PATH)


def _get_custom_strategy_keys() -> set:
    """Return the set of keys in the custom_strategies table."""
    try:
        con = sqlite3.connect(DB_PATH)
        rows = con.execute("SELECT key FROM custom_strategies WHERE enabled=1").fetchall()
        con.close()
        return {r[0] for r in rows}
    except Exception:
        return set()


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
        "min_conviction":  65,
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
        "min_conviction":  76,
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

def _ramp_score(
    value: float,
    weak_threshold: float,
    strong_threshold: float,
    weak_weight: float,
    strong_weight: float,
    saturation: float = 1.4,
) -> float:
    """
    Continuous saturating-ramp score on |value|. Returns a non-negative
    float (caller decides which side it credits).

    - |value| < weak_threshold              → 0
    - weak_threshold ≤ |value| < strong     → linear from weak_weight to strong_weight
    - |value| ≥ strong_threshold            → log-saturating from strong_weight
                                              toward saturation * strong_weight

    Designed so the legacy step-function tier boundaries (weak/strong) sit
    at the same input values but the OUTPUT is continuous: a 5.1% rally
    scores essentially the same as today, a 50% rally scores ~40% more.
    Audit §02 structural fix.
    """
    a = abs(value)
    if a < weak_threshold:
        return 0.0
    if a < strong_threshold:
        span = strong_threshold - weak_threshold
        if span <= 0:
            return float(weak_weight)
        frac = (a - weak_threshold) / span
        return float(weak_weight + frac * (strong_weight - weak_weight))
    # Saturating tail: log1p compresses extreme outliers without making
    # them indistinguishable from "merely strong".
    if strong_threshold <= 0:
        return float(strong_weight)
    excess_frac = (a - strong_threshold) / strong_threshold
    cap = strong_weight * saturation
    return float(min(cap, strong_weight + math.log1p(excess_frac) * (cap - strong_weight)))


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
    _strat_key = strat.get("key", "balanced")

    try:
        symbol        = ticker.get("symbol", "")
        price         = float(ticker.get("price") or 0)
        fair_price    = float(ticker.get("fair_price") or price)
        change_pct    = float(ticker.get("change_24h_pct") or 0)  # already percent
        funding       = float(ticker.get("funding_rate") or 0)
        volume        = float(ticker.get("volume_24h") or 0)
        open_interest = float(ticker.get("open_interest") or 0)

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
        mom_long_strong = mom_strong
        mom_long_weak   = mom_weak

        if SCORE_VERSION == "v2":
            # Continuous saturating-ramp: magnitude matters. Tag boundaries
            # (2%, 5%) preserved so downstream tag-based filters still work.
            if change_pct > 0:
                long_score += _ramp_score(
                    change_pct, 2.0, 5.0, mom_long_weak, mom_long_strong
                )
                if change_pct > 5:
                    tags.append("strong_momentum")
                elif change_pct > 2:
                    tags.append("momentum")
            elif change_pct < 0:
                short_score += _ramp_score(
                    change_pct, 2.0, 5.0, mom_weak, mom_strong
                )
                if change_pct < -5:
                    tags.append("strong_dump")
                elif change_pct < -2:
                    tags.append("dump")
        else:
            # v1 legacy step function — preserved for A/B comparison.
            if change_pct > 5:
                long_score += mom_long_strong
                tags.append("strong_momentum")
            elif change_pct > 2:
                long_score += mom_long_weak
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
        # Deadband ±0.00025 (±0.025%): exchanges zero out funding within this
        # band — any signal below it is pure noise from the fee mechanism.
        fund_strong = w["funding"]
        fund_weak   = int(w["funding"] * 0.4)
        _FUNDING_DEADBAND = 0.00025

        if abs(funding) >= _FUNDING_DEADBAND:
            if SCORE_VERSION == "v2":
                # Continuous ramp from |funding|=0.0001 (weak) to 0.001 (strong),
                # saturating above. Sign of funding determines side credited.
                fund_score = _ramp_score(
                    funding, 0.0001, 0.001, fund_weak, fund_strong
                )
                if funding < 0:
                    if funding < -0.001:
                        tags.append("short_squeeze")
                        # Balanced only: squeeze without confirming 24h move
                        # is degraded, not awarded full points.
                        if _strat_key == "balanced" and change_pct <= 0:
                            fund_score *= 0.4
                            tags.append("squeeze_unconfirmed")
                    long_score += fund_score
                else:
                    if funding > 0.001:
                        tags.append("long_squeeze")
                    short_score += fund_score
            else:
                # v1 legacy step function.
                if funding < -0.001:
                    tags.append("short_squeeze")
                    if _strat_key == "balanced" and change_pct <= 0:
                        long_score += fund_weak
                        tags.append("squeeze_unconfirmed")
                    else:
                        long_score += fund_strong
                elif funding < 0:
                    long_score += fund_weak
                elif funding > 0.001:
                    short_score += fund_strong
                    tags.append("long_squeeze")
                elif funding > 0:
                    short_score += fund_weak

        # Basis spread
        # Premium (price > fair): longs paying more, bearish lean
        # Discount (price < fair): shorts paying more, bullish lean
        if fair_price > 0:
            basis_pct = (price - fair_price) / fair_price * 100
            if SCORE_VERSION == "v2":
                # Ramp from 0.05% (half weight) to 0.1% (full weight),
                # saturating above. Symmetric on direction.
                basis_score = _ramp_score(
                    basis_pct, 0.05, 0.10,
                    w["basis"] * 0.5, w["basis"],
                )
                if basis_pct > 0:
                    short_score += basis_score
                    if basis_pct > 0.1:
                        tags.append("premium")
                elif basis_pct < 0:
                    long_score += basis_score
                    if basis_pct < -0.1:
                        tags.append("discount")
            else:
                # v1 legacy: all-or-nothing.
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

        # Balanced SHORT bias: audit shows systematic SHORT tag outperformance
        # (+7,894 / +7,388 / +5,424 P&L deltas). Multiplier tips borderline cases
        # toward SHORT; does not override strong LONG signals.
        if _strat_key == "balanced" and short_score > 0:
            short_score = int(short_score * 1.08)
            tags.append("short_bias_applied")

        if long_score >= short_score:
            direction = "LONG"
            conviction_base = min(int(long_score * vol_mult), 100)
        else:
            direction = "SHORT"
            conviction_base = min(int(short_score * vol_mult), 100)

        # Illiquidity penalty: large price move on thin volume = noisy signal
        # Amihud proxy: |return%| / (vol_24h / $1M). High ratio = manipulable.
        if volume > 0:
            _illiq = abs(change_pct) / max(volume / 1_000_000, 0.01)
            if _illiq > 20:
                conviction_base = max(0, conviction_base - 10)
                tags.append("illiq_extreme")
            elif _illiq > 8:
                conviction_base = max(0, conviction_base - 5)
                tags.append("illiq_high")

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
            # New: which Stage 1 scorer ran. Lets analyze.py / the A/B
            # reconstruction script split v1 cohorts from v2 cohorts.
            "score_version": SCORE_VERSION,
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
    agent_min_conviction: int | None = None,
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
    exchange = base.get("exchange", "MEXC")

    try:
        # --- Klines (exchange-agnostic) ---
        kline_data = fetch_klines(exchange, symbol, "1h", 100)
        if not kline_data or not isinstance(kline_data, dict):
            return None

        df = pd.DataFrame({
            "open":   kline_data.get("open",   []),
            "high":   kline_data.get("high",   []),
            "low":    kline_data.get("low",    []),
            "close":  kline_data.get("close",  []),
            "volume": kline_data.get("volume", []),
        }).astype(float)

        # Need at least period+2 rows for reliable ATR/RSI (14 + 2 = 16)
        if len(df) < 16:
            return None

        n1h = len(df)

        # --- Kline depth gate ---
        # Fetch 4h candles just to measure history depth; count only, not used for indicators.
        kline4h_data = fetch_klines(exchange, symbol, "4h", 50)
        n4h = len(kline4h_data.get("close", [])) if kline4h_data else 0

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

        # Vol-of-vol: if ATR itself is volatile, regime is unstable → lower quality
        _vol_unstable = False
        try:
            _recent_atrs = atr_clean.iloc[-10:].values
            if len(_recent_atrs) >= 10 and _recent_atrs.mean() > 0:
                _vol_unstable = (_recent_atrs.std() / _recent_atrs.mean()) > 0.3
        except Exception:
            pass

        # Volume spike ratio: current 1h candle vs avg of prior 23 (rolling window)
        _vol_spike_ratio = None
        try:
            _vols = df["volume"].tolist()
            if len(_vols) >= 24:
                _avg_prior = sum(_vols[-24:-1]) / 23
                if _avg_prior > 0:
                    _vol_spike_ratio = _vols[-1] / _avg_prior
        except Exception:
            pass

        # Extreme vol firebreak — user-controlled gate, defaults ON.
        # Blocks all extreme-vol signals before any strategy-specific gate logic.
        # The ×0.85 conviction multiplier below is a secondary safeguard that
        # still applies when this gate is OFF.
        if vol_regime == "extreme" and _load_extreme_vol_firebreak():
            return None

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
        try:
            next_funding_minutes = fetch_next_funding_minutes(exchange, symbol)
        except Exception:
            next_funding_minutes = None

        # --- Orderbook imbalance ---
        imbalance = 0.5  # neutral default if depth fetch fails
        depth_data = fetch_depth(exchange, symbol)
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

        # --- Regime-aware counter-trend boost ---
        # Factor engine (5.7M labeled candles): counter-trend setups in a
        # bearish EMA structure (LONG) or bullish EMA structure (SHORT)
        # outperform baseline by +3.9 to +5.6 edge_delta at high confidence.
        # trend_score < -20  → price below ema20 below ema50  (bearish structure)
        # trend_score > +20  → price above ema20 above ema50  (bullish structure)
        #
        # AUDIT GATE §02 regime_counter_001: the +3.9–5.6 edge_delta came
        # from ~640 simultaneous cell comparisons with NO Bonferroni or FDR
        # correction; ~32 spurious findings are expected at α=0.05. The
        # tag-stratified A/B (May 2026) confirmed conviction is currently
        # mildly anti-predictive in aggregate — so any unverified boost is
        # likely amplifying the inversion rather than fighting it. Default
        # OFF until a walk-forward run confirms the boost survives FDR.
        # Re-enable with REGIME_COUNTER_ENABLED=true in .env once validated.
        _regime_counter_enabled = (os.getenv("REGIME_COUNTER_ENABLED") or "false").strip().lower() == "true"
        _bearish_structure = trend_score < -20
        _bullish_structure = trend_score > 20
        if direction == "LONG" and _bearish_structure and vol_regime != "extreme":
            if _regime_counter_enabled:
                boost = 8 if vol_regime in ("medium", "high") else 5
                conviction += boost
                tags.append("regime_counter_long")
            else:
                tags.append("regime_counter_long_shadow")
        elif direction == "SHORT" and _bullish_structure and vol_regime != "extreme":
            if _regime_counter_enabled:
                boost = 8 if vol_regime in ("medium", "high") else 5
                conviction += boost
                tags.append("regime_counter_short")
            else:
                tags.append("regime_counter_short_shadow")

        # Extreme volatility: high ATR% means the signal is real but the trade
        # is more dangerous — discount conviction so it ranks below calmer setups.
        if vol_regime == "extreme":
            conviction = int(conviction * 0.85)
            tags.append("extreme_vol")

        # Trend extension penalty: overextended trends = late entry, lower quality
        # Empirical: winners avg |trend_score| 9.3 vs losers 14.1 — higher = worse
        _abs_ts = abs(trend_score)
        if _abs_ts > 60:
            conviction -= 8
            tags.append("trend_extended")
        elif _abs_ts > 35:
            conviction -= 4
            tags.append("trend_extended_mild")

        # Vol-of-vol penalty: unstable ATR regime = degraded signal quality
        if _vol_unstable:
            conviction -= 5
            tags.append("vol_unstable")

        # Volume spike conviction adjustment
        if _vol_spike_ratio is not None:
            if _vol_spike_ratio >= 2.0:
                conviction += 5
                tags.append("vol_spike")
            elif _vol_spike_ratio <= 0.5:
                conviction -= 5
                tags.append("vol_low_participation")

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
            _now = time.time()
            _cache_key = f"{exchange}:{symbol}"
            _cached = _daily_kline_cache.get(_cache_key)
            if _cached and (_now - _cached[0]) < _DAILY_KLINE_TTL:
                daily_klines = _cached[1]
            else:
                daily_klines = fetch_daily_klines_raw(exchange, symbol, limit=30)
                _daily_kline_cache[_cache_key] = (_now, daily_klines)
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
            # BTC/ETH macro context at signal generation time (from latest snapshot)
            "btc_context": get_latest_market_context(),
            # Signal quality factors — stored for analyze.py research
            "vol_spike_ratio": round(_vol_spike_ratio, 3) if _vol_spike_ratio is not None else None,
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

        # --- Agent Intelligence Layer (Phase 2 live mode) ---
        # Phase 2 unlocked: shadow_delta is applied to conviction; real (non-shadow)
        # tags are added. Hard blocks from Risk Manager still use the same logic.
        # Only run agents on the top AGENT_TOP_N signals — the rest get null fields.
        _run_agents = (
            base.get("_scan_rank", 99) < AGENT_TOP_N
            and (agent_min_conviction is None or sig["conviction"] >= agent_min_conviction)
        )
        _agent_output = None
        if _run_agents:
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
            # Phase 2 live mode: apply delta + real tags.
            # Risk Manager hard blocks remain authoritative (deterministic).
            if _agent_output.hard_blocked:
                sig["tags"] = list(dict.fromkeys(
                    sig["tags"] + ["agent_blocked"] + _agent_output.block_reasons
                ))
            else:
                agent_tags = [
                    t if t.startswith("agent_") else f"agent_{t}"
                    for t in _agent_output.tags
                ]
                sig["tags"] = list(dict.fromkeys(sig["tags"] + agent_tags))
                # Only apply the shadow_delta when the LLM pipeline actually
                # produced parseable analyst output. Otherwise the delta is
                # the all-neutral fallback (numerically 0) and tagging it as
                # a real assessment biases the system. Audit §02 fix.
                if _agent_output.shadow_delta and not _agent_output.llm_unavailable:
                    sig["conviction"] = max(0, min(100, sig["conviction"] + _agent_output.shadow_delta))

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
            # New: surface LLM availability so downstream consumers (Intelligence
            # tab, /api/agents/status, factor engine, analyze.py) can filter or
            # caveat agent-derived rows. None when the whole pipeline didn't run.
            "agent_llm_unavailable": (
                _agent_output.llm_unavailable if _agent_output else None
            ),
            "agent_llm_ok_count": (
                _agent_output.llm_ok_count if _agent_output else None
            ),
            "agent_llm_analyst_total": (
                _agent_output.llm_analyst_total if _agent_output else None
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

        # L/S ratio contrarian extremes: extreme crowding on one side = warning
        # for that side (BIS carry paper + practitioner consensus).
        # Only applies to SENTIMENT_PAIRS — altcoins return None here.
        _okx_ls = sig.get("okx_ls_long_pct")
        if _okx_ls is not None:
            if _okx_ls > 65 and sig["direction"] == "LONG":
                sig["conviction"] = max(0, sig["conviction"] - 5)
                sig["tags"] = list(dict.fromkeys(sig["tags"] + ["ls_crowd_long"]))
            elif _okx_ls < 35 and sig["direction"] == "SHORT":
                sig["conviction"] = max(0, sig["conviction"] - 5)
                sig["tags"] = list(dict.fromkeys(sig["tags"] + ["ls_crowd_short"]))
            sig["conviction"] = max(0, min(100, sig["conviction"]))

        # --- Order flow (MEXC only; data collection for researcher — no conviction impact) ---
        flow_score     = None
        flow_confirmed = None
        if exchange != "HYPERLIQUID":
            try:
                _flow = _flow_confirm(symbol, direction, price)
                flow_score     = _flow.get("score")
                flow_confirmed = _flow.get("confirmed")
                if flow_confirmed:
                    sig["tags"] = list(dict.fromkeys(sig["tags"] + ["flow_confirmed"]))
            except Exception:
                pass
        sig["flow_score"]     = flow_score
        sig["flow_confirmed"] = flow_confirmed

        sig.pop("_scan_rank", None)
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
    exchange: str = "MEXC",
) -> tuple[list[dict], int]:
    """
    Two-stage scan across tickers from any supported exchange.

    Stage 1: Fetch all tickers (or use pre-fetched `tickers`), normalize to
             canonical format, score each with score_ticker() using the active
             strategy's weights and stage-1 filters. Discard conviction_base < 20,
             take the top ENRICH_TOP_N.
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
        if exchange == "MEXC":
            tickers = fetch_mexc("/contract/ticker")
        else:
            tickers = _fetch_exchange_tickers(exchange)
        if not tickers or not isinstance(tickers, list):
            return [], 0

    registry = get_strategy_registry()
    strat = registry.get(strategy_key, registry["balanced"])
    strategy_key = strat["key"]

    # Apply runtime overrides for built-in strategies — read fresh, never mutate registry
    overrides = _load_strategy_overrides()
    if strategy_key in overrides:
        strat = dict(strat)
        for field, value in overrides[strategy_key].items():
            if field == "min_conviction":
                strat["min_conviction"] = int(value)

    effective_threshold = max(threshold, strat["min_conviction"])
    coinglass_snapshot = get_coin_market_snapshot()

    total_pairs = len(tickers)

    sym_perf_cache = _load_symbol_performance_cache()
    sym_overrides = _get_symbol_overrides()
    print(f'[sym_penalty] loaded {len(sym_perf_cache)} records, {len(sym_overrides)} overrides', file=sys.stderr)

    # Stage 1 — normalize to canonical format, then score
    base_signals: list[dict] = []
    for t in tickers:
        canonical = normalize_ticker_for_scoring(t, exchange)
        if not canonical:
            continue
        scored = score_ticker(canonical, strategy=strat, coinglass_snapshot=coinglass_snapshot,
                              sym_perf_cache=sym_perf_cache, sym_overrides=sym_overrides)
        if scored and scored["conviction_base"] >= 20:
            base_signals.append(scored)

    base_signals.sort(key=lambda s: s["conviction_base"], reverse=True)
    top = base_signals[:ENRICH_TOP_N]
    for i, s in enumerate(top):
        s["_scan_rank"] = i  # 0 = highest conviction; used by enrich_signal() agent guard

    # Stage 2 — concurrent enrichment, strategy-aware
    filter_stats = {
        "lock": threading.Lock(),
        "long_vol_refuse": 0,
        "long_vol_shadow": 0,
        "short_vol_refuse": 0,
        "short_vol_shadow": 0,
    }
    enrich = partial(
        enrich_signal,
        strategy=strat,
        filter_stats=filter_stats,
        agent_min_conviction=effective_threshold,
    )
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
    """Fetch tickers once, score and enrich for every enabled strategy sequentially,
    log results, and return all signals grouped by strategy key.
    Strategies run one-at-a-time to avoid overwhelming the MEXC public API with
    concurrent kline requests (each strategy enriches 30 signals × 3 API calls).
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
            sigs, _ = run_scan(strategy_key=key, tickers=tickers)
            log_signals(sigs)
            results[key] = {
                "signals":     sigs,
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


@app.route("/api/hl/scan", methods=["POST"])
def api_hl_scan():
    """Fetch Hyperliquid tickers once, score and enrich for every enabled strategy
    sequentially, log results, and return all signals grouped by strategy key.
    Same shape as /api/scan/all — standard pattern for all exchange integrations."""
    try:
        t0 = time.time()
        expire_stale_signals()

        universe, asset_ctxs = fetch_hl_meta_and_ctxs()
        tickers = normalize_hl_tickers(universe, asset_ctxs)
        if not tickers:
            return jsonify({"success": False, "error": "Hyperliquid ticker feed unavailable"}), 502

        total_pairs = len(tickers)
        registry = get_strategy_registry()
        results: dict = {}

        for key in registry:
            sigs, _ = run_scan(strategy_key=key, tickers=tickers, exchange="HYPERLIQUID")
            log_signals(sigs)
            results[key] = {
                "signals":     sigs,
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


@app.route("/api/bybit/scan", methods=["POST"])
def api_bybit_scan():
    """Fetch Bybit tickers once, score and enrich for every enabled strategy.
    Same shape as /api/scan/all. Bybit is geo-restricted; only works from VPS."""
    try:
        t0 = time.time()
        expire_stale_signals()

        raw_tickers = _fetch_exchange_tickers("BYBIT")
        if not raw_tickers:
            return jsonify({"success": False, "error": "Bybit ticker feed unavailable"}), 502

        # normalize_ticker_for_scoring expects raw exchange format; run_scan handles it
        total_pairs = len(raw_tickers)
        registry = get_strategy_registry()
        results: dict = {}

        for key in registry:
            sigs, _ = run_scan(strategy_key=key, tickers=raw_tickers, exchange="BYBIT")
            log_signals(sigs)
            results[key] = {
                "signals":     sigs,
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


# ---------------------------------------------------------------------------
# Exchange configuration
# ---------------------------------------------------------------------------

EXCHANGE_CONFIG_PATH = "data/exchange_config.json"
_DEFAULT_EXCHANGE_CONFIG = {"enabled": ["MEXC", "HYPERLIQUID"]}


def _load_exchange_config() -> dict:
    try:
        with open(EXCHANGE_CONFIG_PATH) as f:
            cfg = json.load(f)
        # Validate enabled list — only keep known exchanges
        known = set(SUPPORTED_EXCHANGES.keys())
        cfg["enabled"] = [e for e in cfg.get("enabled", []) if e in known]
        if not cfg["enabled"]:
            cfg["enabled"] = list(_DEFAULT_EXCHANGE_CONFIG["enabled"])
        return cfg
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(_DEFAULT_EXCHANGE_CONFIG)


def _save_exchange_config(cfg: dict) -> None:
    os.makedirs("data", exist_ok=True)
    with open(EXCHANGE_CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


@app.route("/api/exchanges")
def api_exchanges():
    """Return SUPPORTED_EXCHANGES registry + current enable/disable config."""
    cfg = _load_exchange_config()
    enabled_set = set(cfg["enabled"])
    exchanges = []
    for key, meta in SUPPORTED_EXCHANGES.items():
        exchanges.append({
            "key":       key,
            "name":      meta["name"],
            "enabled":   key in enabled_set,
            "quote":     meta.get("quote", "USDT"),
            "geo_free":  meta.get("geo_free", True),
            "funding_interval_h": meta.get("funding_interval_h", 8),
        })
    return jsonify({"success": True, "exchanges": exchanges, "enabled": cfg["enabled"]})


@app.route("/api/exchanges/config", methods=["PATCH"])
def api_exchanges_config():
    """Toggle an exchange on/off. Body: {"exchange": "BYBIT", "enabled": true}"""
    try:
        body = request.get_json(force=True) or {}
        exch = str(body.get("exchange", "")).upper()
        if exch not in SUPPORTED_EXCHANGES:
            return jsonify({"success": False, "error": f"Unknown exchange: {exch}"}), 400
        enable = bool(body.get("enabled", True))
        cfg = _load_exchange_config()
        enabled = list(cfg.get("enabled", []))
        if enable and exch not in enabled:
            enabled.append(exch)
        elif not enable and exch in enabled:
            enabled.remove(exch)
            if not enabled:
                return jsonify({"success": False, "error": "Cannot disable all exchanges"}), 400
        cfg["enabled"] = enabled
        _save_exchange_config(cfg)
        return jsonify({"success": True, "enabled": enabled})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/scan/multi", methods=["POST"])
def api_scan_multi():
    """Scan all enabled exchanges simultaneously using parallel threads.
    Returns {exchange_key: {results, total_pairs, scan_time}} per exchange."""
    try:
        t0 = time.time()
        expire_stale_signals()

        cfg = _load_exchange_config()
        enabled = cfg.get("enabled", ["MEXC"])

        def _scan_exchange(exch: str) -> tuple[str, dict]:
            try:
                if exch == "MEXC":
                    raw = fetch_mexc("/contract/ticker")
                elif exch == "HYPERLIQUID":
                    universe, asset_ctxs = fetch_hl_meta_and_ctxs()
                    raw = normalize_hl_tickers(universe, asset_ctxs)
                else:
                    raw = _fetch_exchange_tickers(exch)

                if not raw:
                    return exch, {"error": f"{exch} ticker feed unavailable", "results": {}}

                total_pairs = len(raw)
                registry = get_strategy_registry()
                results: dict = {}
                for key in registry:
                    sigs, _ = run_scan(strategy_key=key, tickers=raw, exchange=exch)
                    log_signals(sigs)
                    results[key] = {
                        "signals":     sigs,
                        "total_pairs": total_pairs,
                        "strategy":    key,
                    }
                return exch, {"results": results, "total_pairs": total_pairs}
            except Exception as exc:
                return exch, {"error": str(exc), "results": {}}

        per_exchange: dict = {}
        with ThreadPoolExecutor(max_workers=len(enabled)) as pool:
            for exch, data in pool.map(_scan_exchange, enabled):
                per_exchange[exch] = data

        scan_time = round(time.time() - t0, 2)
        return jsonify({
            "success":      True,
            "per_exchange": per_exchange,
            "enabled":      enabled,
            "scan_time":    scan_time,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/market")
def api_market():
    try:
        exchange = request.args.get("exchange", "mexc").strip().lower()
        if exchange == "hyperliquid":
            universe, asset_ctxs = fetch_hl_meta_and_ctxs()
            raw_tickers = normalize_hl_tickers(universe, asset_ctxs)
            exchange_key = "HYPERLIQUID"
            unavailable = "Hyperliquid unavailable"
        elif exchange == "bybit":
            raw_tickers = _fetch_exchange_tickers("BYBIT")
            exchange_key = "BYBIT"
            unavailable = "Bybit unavailable"
        else:
            raw_tickers = fetch_mexc("/contract/ticker")
            exchange_key = "MEXC"
            unavailable = "MEXC unavailable"

        if not raw_tickers or not isinstance(raw_tickers, list):
            return jsonify({"success": False, "error": unavailable}), 502

        coinglass_snapshot = get_coin_market_snapshot()
        pairs = []
        for t in raw_tickers:
            canonical = normalize_ticker_for_scoring(t, exchange_key)
            if not canonical:
                continue
            scored = score_ticker(canonical, coinglass_snapshot=coinglass_snapshot)
            if scored:
                pairs.append(scored)

        pairs.sort(key=lambda p: p["conviction_base"], reverse=True)
        return jsonify({
            "success": True,
            "pairs": pairs,
            "count": len(pairs),
            "exchange": exchange_key,
            "coinglass_enabled": coinglass_enabled(),
            "coinglass_pairs": len(coinglass_snapshot),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/market/summary")
def api_market_summary():
    """
    Live market summary: top movers, losers, volume, extreme funding, volatility watch,
    volume spikes (requires snapshot history), and latest BTC/ETH market context.
    """
    try:
        exchange_param = request.args.get("exchange", "mexc").strip().lower()

        if exchange_param == "hyperliquid":
            universe, asset_ctxs = fetch_hl_meta_and_ctxs()
            raw_tickers = normalize_hl_tickers(universe, asset_ctxs)
            exchange_label = "HYPERLIQUID"
        elif exchange_param == "bybit":
            raw_tickers = _fetch_exchange_tickers("BYBIT")
            exchange_label = "BYBIT"
        else:
            raw_tickers = fetch_mexc("/contract/ticker")
            exchange_label = "MEXC"

        if not raw_tickers or not isinstance(raw_tickers, list):
            return jsonify({"success": False, "error": "no ticker data"}), 502

        coinglass_snapshot = get_coin_market_snapshot()
        scored = []
        for t in raw_tickers:
            canonical = normalize_ticker_for_scoring(t, exchange_label)
            if canonical:
                s = score_ticker(canonical, coinglass_snapshot=coinglass_snapshot)
                if s:
                    scored.append(s)

        # 7-day avg volume from snapshots for spike detection
        vol_avgs: dict[str, float] = {}
        try:
            cutoff_7d = (datetime.utcnow() - timedelta(days=7)).isoformat()
            con = sqlite3.connect(DB_PATH)
            rows = con.execute("""
                SELECT symbol, AVG(volume_24h), COUNT(*)
                FROM ticker_snapshots
                WHERE ts > ? AND exchange = ?
                GROUP BY symbol HAVING COUNT(*) >= 3
            """, (cutoff_7d, exchange_label)).fetchall()
            con.close()
            vol_avgs = {r[0]: r[1] for r in rows}
        except Exception:
            pass

        def _change(s):  return s.get("change_24h_pct") or 0
        def _vol(s):     return s.get("volume_24h") or 0
        def _funding(s): return s.get("funding_rate") or 0

        # Volume spikes: current vol > 1.5× 7-day avg (only once history exists)
        volume_spikes = []
        for s in scored:
            avg = vol_avgs.get(s["symbol"])
            cur = _vol(s)
            if avg and avg > 0 and cur > avg * 1.5:
                volume_spikes.append({**s, "vol_spike_ratio": round(cur / avg, 2)})
        volume_spikes.sort(key=lambda x: x["vol_spike_ratio"], reverse=True)

        # Volatility watch: combined score from |change| + |funding| magnitude
        def _vwatch(s):
            return abs(_change(s)) * 10 + abs(_funding(s)) * 5000

        return jsonify({
            "success":                  True,
            "exchange":                 exchange_label,
            "top_movers":               sorted(scored, key=_change, reverse=True)[:6],
            "top_losers":               sorted(scored, key=_change)[:6],
            "top_volume":               sorted(scored, key=_vol, reverse=True)[:6],
            "extreme_funding_positive": sorted(scored, key=_funding, reverse=True)[:5],
            "extreme_funding_negative": sorted(scored, key=_funding)[:5],
            "volatility_watch":         sorted(scored, key=_vwatch, reverse=True)[:8],
            "volume_spikes":            volume_spikes[:6],
            "market_context":           get_latest_market_context(),
            "has_history":              len(vol_avgs) > 0,
            "snapshot_symbols":         len(vol_avgs),
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
            raw_tickers = normalize_hl_tickers(*fetch_hl_meta_and_ctxs())
            exchange_key = "HYPERLIQUID"
            unavailable = "Hyperliquid unavailable"
        elif exchange == "bybit":
            raw_tickers = _fetch_exchange_tickers("BYBIT")
            exchange_key = "BYBIT"
            unavailable = "Bybit unavailable"
        else:
            raw_tickers = fetch_mexc("/contract/ticker")
            exchange_key = "MEXC"
            unavailable = "MEXC unavailable"
        if not raw_tickers:
            return jsonify({"success": False, "error": unavailable}), 502

        requested_symbol = symbol.upper()
        # For Bybit, canonical symbols have underscore ("BTC_USDT") but raw tickers
        # use no separator ("BTCUSDT"). Normalize each raw ticker on the fly to match.
        def _sym_matches(raw_t: dict) -> bool:
            raw_sym = str(raw_t.get("symbol", "")).upper()
            if raw_sym == requested_symbol:
                return True
            if exchange_key == "BYBIT":
                from lib.bybit_client import normalize_bybit_symbol
                return normalize_bybit_symbol(raw_sym).upper() == requested_symbol
            return False

        raw_ticker = next((t for t in raw_tickers if _sym_matches(t)), None)
        if not raw_ticker:
            return jsonify({"success": False, "error": f"Symbol {symbol!r} not found"}), 404

        ticker = normalize_ticker_for_scoring(raw_ticker, exchange_key)
        if not ticker:
            return jsonify({"success": False, "error": f"Could not normalize {symbol!r}"}), 404

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


@app.route("/api/signals/stats")
def api_signals_stats():
    """Aggregate stats over ALL closed signals — no limit. Used by History tab summary bar."""
    try:
        strategy = request.args.get("strategy", None)
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        where = "WHERE result IS NOT NULL"
        params: list = []
        if strategy:
            where += " AND strategy=?"
            params.append(strategy)
        cur.execute(f"""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN result='WIN'     THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN result='LOSS'    THEN 1 ELSE 0 END) as losses,
                SUM(CASE WHEN result='PARTIAL' THEN 1 ELSE 0 END) as partials,
                SUM(CASE WHEN result='EXPIRED' THEN 1 ELSE 0 END) as expired,
                AVG(CASE WHEN result IN ('WIN','PARTIAL') AND pnl_pct IS NOT NULL THEN pnl_pct END) as avg_pos_pnl,
                AVG(CASE WHEN result='LOSS' AND pnl_pct IS NOT NULL THEN pnl_pct END) as avg_loss_pnl,
                AVG(CASE WHEN result IN ('WIN','PARTIAL') THEN conviction END) as avg_conv_win,
                AVG(CASE WHEN result='LOSS' THEN conviction END) as avg_conv_loss,
                AVG(CASE WHEN pnl_pct IS NOT NULL AND result NOT IN ('EXPIRED','SKIPPED') THEN pnl_pct END) as avg_pnl
            FROM signals {where}
        """, params)
        row = cur.fetchone()
        con.close()
        total, wins, losses, partials, expired, avg_pos_pnl, avg_loss_pnl, avg_conv_win, avg_conv_loss, avg_pnl = row
        denom = (wins or 0) + (losses or 0) + (partials or 0)
        win_rate_strict   = round((wins or 0) / denom * 100) if denom > 0 else None
        win_rate_positive = round(((wins or 0) + (partials or 0)) / denom * 100) if denom > 0 else None
        return jsonify({
            "success": True,
            "total": total or 0,
            "wins": wins or 0,
            "losses": losses or 0,
            "partials": partials or 0,
            "expired": expired or 0,
            "win_rate_strict":   win_rate_strict,
            "win_rate_positive": win_rate_positive,
            "avg_pos_pnl":  round(avg_pos_pnl,  2) if avg_pos_pnl  is not None else None,
            "avg_loss_pnl": round(avg_loss_pnl,  2) if avg_loss_pnl is not None else None,
            "avg_conv_win":  round(avg_conv_win,  1) if avg_conv_win  is not None else None,
            "avg_conv_loss": round(avg_conv_loss, 1) if avg_conv_loss is not None else None,
            "avg_pnl": round(avg_pnl, 2) if avg_pnl is not None else None,
        })
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


def _generate_coach_review(
    sig: dict,
    signal_id: int,
    sig_json_obj: dict,
    journey: dict,
    journey_prompt: str,
    tags_raw: str,
    entry1,
    exit_price,
    duration_minutes,
) -> str | None:
    """
    Return a coach review string for a closed signal.
    Returns the cached value from signal_json if already generated.
    Otherwise calls the AI, persists the result back to signal_json, and returns it.
    Never raises — returns None on any failure.
    """
    cached = sig_json_obj.get("coach_review")
    if cached:
        return cached

    direction = sig.get("direction", "")

    # --- Agent consensus at scan time ---
    agent_context = ""
    try:
        regime_at_scan = sig_json_obj.get("agent_regime") or sig_json_obj.get("regime") or ""
        disagreement = float(sig_json_obj.get("agent_shadow_disagreement") or
                             sig_json_obj.get("agent_disagreement") or 0)
        delta = float(sig_json_obj.get("agent_shadow_delta") or
                      sig_json_obj.get("agent_delta") or 0)
        bull_thesis = sig_json_obj.get("agent_narrative_bull") or ""
        bear_thesis = sig_json_obj.get("agent_narrative_bear") or ""
        struct_bull = sig_json_obj.get("agent_structural_bull") or ""
        struct_bear = sig_json_obj.get("agent_structural_bear") or ""
        bits = []
        if regime_at_scan:
            bits.append(f"Regime at scan: {regime_at_scan}")
        if delta:
            bits.append(f"Analyst conviction delta: {delta:+.1f} pts")
        if disagreement > 0.3:
            bits.append(f"Desk disagreement score: {disagreement:.2f} (elevated — desk was split)")
        elif disagreement > 0:
            bits.append(f"Desk disagreement score: {disagreement:.2f} (low — desk was aligned)")
        if bull_thesis:
            bits.append(f"Bull thesis at scan: {bull_thesis}")
        if bear_thesis:
            bits.append(f"Bear thesis at scan: {bear_thesis}")
        if struct_bull:
            bits.append(f"Structural bull: {struct_bull}")
        if struct_bear:
            bits.append(f"Structural bear: {struct_bear}")
        if bits:
            agent_context = "\n\nDesk analyst reads at scan time:\n" + "\n".join(f"- {b}" for b in bits)
    except Exception:
        pass

    # --- Funding alignment at signal time ---
    funding_context = ""
    try:
        fr = float(sig.get("funding_rate") or 0)
        if fr != 0:
            fr_ann = abs(fr) * 3 * 365 * 100
            if fr < -0.0003 and direction == "LONG":
                alignment = f"ALIGNED — funding was {fr:.5f} ({fr_ann:.0f}% annualized), shorts paying longs. Squeeze thesis had structural backing."
            elif fr > 0.0003 and direction == "SHORT":
                alignment = f"ALIGNED — funding was {fr:.5f} ({fr_ann:.0f}% annualized), longs paying shorts. Flush thesis had structural backing."
            elif fr < -0.0003 and direction == "SHORT":
                alignment = f"MISALIGNED — funding was {fr:.5f} ({fr_ann:.0f}% annualized) negative while shorting. Carry was fighting the trade."
            elif fr > 0.0003 and direction == "LONG":
                alignment = f"MISALIGNED — funding was {fr:.5f} ({fr_ann:.0f}% annualized) positive while going long. Carry was fighting the trade."
            else:
                alignment = f"NEUTRAL — funding was {fr:.5f} ({fr_ann:.0f}% annualized). No meaningful carry edge in either direction."
            funding_context = f"\n\nFunding alignment: {alignment}"
    except Exception:
        pass

    # --- Daily intelligence report for trade date (fails silently) ---
    intel_context = ""
    try:
        trade_date = (sig.get("logged_at") or "")[:10]
        if trade_date:
            report_path = os.path.join("data", "reports", f"daily_{trade_date}.json")
            if os.path.exists(report_path):
                with open(report_path, "r", encoding="utf-8") as _rf:
                    _rep = json.load(_rf)
                narr = _rep.get("narrative") or {}
                regime_note = narr.get("regime_forecast", "")[:200]
                funding_note = narr.get("funding_autopsy", "")[:200]
                bits = []
                if regime_note:
                    bits.append(f"Nadia's regime read that day: {regime_note}")
                if funding_note:
                    bits.append(f"Kenny's funding note that day: {funding_note}")
                if bits:
                    intel_context = "\n\nCipher desk notes for trade date:\n" + "\n".join(f"- {b}" for b in bits)
    except Exception:
        pass

    # --- Research Firm findings for this strategy ---
    research_context = ""
    try:
        briefs_path = "/opt/mt-learner/research/briefs.json"
        if os.path.exists(briefs_path):
            with open(briefs_path) as _f:
                _rd = json.load(_f)
            skey = sig.get("strategy_key") or strategy_name_to_key(sig.get("strategy", ""))
            relevant = [
                b for b in _rd.get("briefs", [])
                if b.get("strategy_key") == skey and b.get("status") == "active"
            ][:3]
            if relevant:
                research_context = "\n\nResearch Firm findings for this strategy:\n" + "".join(
                    f"- [{b['confidence'].upper()}] {b['title']}: {b['thesis']}"
                    + (f" Suggestion: {b['what_is_novel']}" if b.get("what_is_novel") else "")
                    + "\n"
                    for b in relevant
                )
    except Exception:
        pass

    user_msg = (
        f"Trade data:\n"
        f"Symbol: {sig.get('symbol')} | Direction: {direction} | Strategy: {sig.get('strategy')}\n"
        f"Conviction: {sig.get('conviction')} | Result: {sig.get('result')}\n"
        f"Entry: {entry1} | Exit: {exit_price or 'unknown'} | Stop: {sig.get('stop_loss')}\n"
        f"TP1: {sig.get('tp1')} | TP2: {sig.get('tp2')} | TP3: {sig.get('tp3')}\n"
        f"Duration: {duration_minutes or 'unknown'} minutes\n"
        f"Trade journey:\n{journey_prompt}\n"
        f"Signal reason: {sig.get('signal_why')}\n"
        f"Tags: {tags_raw}\n"
        f"Result note: {sig.get('result_note')}\n"
        f"{agent_context}"
        f"{funding_context}"
        f"{intel_context}"
        f"{research_context}\n\n"
        f"Write a coach review in exactly 2 short paragraphs. "
        f"Paragraph 1: describe the price path using MAE, MFE, capture, stop pressure, and duration in plain language. "
        f"Paragraph 2: what the signal got right or wrong — reference the funding alignment and agent consensus if present — "
        f"and give one specific, concrete forward call (not 'monitor more trades', tell the trader exactly what to look for next time). "
        f"If Cipher desk notes or Research Firm findings are relevant, name them specifically. "
        f"Do not recommend strategy changes from a single trade — frame it as evidence to aggregate.\n\n"
        f"IMPORTANT: Begin your response immediately with the first word of the review. "
        f"Do not write any preamble, introduction, or meta-commentary. "
        f"Do not write sentences like 'Here is the review' or 'Let me analyze this trade' or 'Okay, let's tackle'. "
        f"The first word you output must be the first word of paragraph one."
    )
    review = call_ai(
        system=(
            "You are Thomas Chen, head trader at Cipher Research Group, reviewing a completed trade. "
            "You speak in first person, directly, like a mentor who respects the trader's intelligence. "
            "You are specific about numbers — MAE, MFE, funding rates, annualized carry. "
            "You do not hedge every sentence. You do not use filler phrases. "
            "You do not explain what MAE or MFE stand for — the trader already knows. "
            "Output only the two review paragraphs. Nothing else before or after."
        ),
        user=user_msg,
        max_tokens=500,
    )

    if review:
        # Strip preamble lines that start with reasoning/meta phrases regardless of provider
        _preamble_triggers = (
            "okay", "let's", "let me", "sure", "here is", "here's", "i'll", "i will",
            "alright", "right,", "starting with", "to begin", "first,", "the user",
        )
        lines = review.strip().splitlines()
        while lines and lines[0].strip().lower().startswith(_preamble_triggers):
            lines.pop(0)
        review = "\n".join(lines).strip() or review.strip()
        try:
            sig_json_obj["coach_review"]    = review
            sig_json_obj["coach_review_at"] = datetime.utcnow().isoformat()
            _con = sqlite3.connect(DB_PATH)
            _con.execute("UPDATE signals SET signal_json=? WHERE id=?",
                         (json.dumps(sig_json_obj, default=str), signal_id))
            _con.commit()
            _con.close()
        except Exception as _e:
            print(f"[coach_review persist] {_e}", file=sys.stderr)

    return review


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

        sig_json_obj = {}
        try:
            sig_json_obj = json.loads(sig.get("signal_json") or "{}")
        except Exception:
            pass
        ai_analysis = _generate_coach_review(sig, signal_id, sig_json_obj, journey, journey_prompt,
                                              tags_raw, entry1, exit_price, duration_minutes)

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


@app.route("/api/signal/detail/<int:signal_id>/regenerate-review", methods=["POST"])
def api_signal_regenerate_review(signal_id: int):
    """Clear the cached coach review for a signal so it regenerates on next load."""
    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT signal_json FROM signals WHERE id=?", (signal_id,)).fetchone()
        if not row:
            con.close()
            return jsonify({"success": False, "error": "not found"}), 404
        try:
            sj = json.loads(row["signal_json"] or "{}")
        except Exception:
            sj = {}
        sj.pop("coach_review", None)
        sj.pop("coach_review_at", None)
        con.execute("UPDATE signals SET signal_json=? WHERE id=?",
                    (json.dumps(sj, default=str), signal_id))
        con.commit()
        con.close()
        return jsonify({"success": True, "cleared": True})
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


def _persist_journey_to_signal_json(sig_id: int, sig: dict, pnl_pct: float | None) -> None:
    """
    Compute trade journey metrics and merge them into the stored signal_json
    for a just-closed signal. Called once at close time by api_outcomes_check().

    Persists 11 journey_* fields into signal_json. Never raises.
    """
    try:
        journey = compute_trade_journey(sig, pnl_pct)
        if not journey:
            return

        target_hits = journey.get("target_hits") or {}
        journey_fields = {
            "journey_available":          journey.get("available", False),
            "journey_mae_pct":            journey.get("mae_pct"),
            "journey_mfe_pct":            journey.get("mfe_pct"),
            "journey_capture_ratio":      journey.get("capture_ratio_pct"),
            "journey_stop_pressure":      journey.get("stop_pressure_pct"),
            "journey_path_label":         journey.get("path_label"),
            "journey_entry_delay_min":    journey.get("entry_delay_minutes"),
            "journey_entry_to_close_min": journey.get("entry_to_close_minutes"),
            "journey_tp1_hit":            "tp1" in target_hits,
            "journey_tp2_hit":            "tp2" in target_hits,
            "journey_tp3_hit":            "tp3" in target_hits,
        }

        con = sqlite3.connect(DB_PATH)
        row = con.execute(
            "SELECT signal_json FROM signals WHERE id=?", (sig_id,)
        ).fetchone()
        if row:
            try:
                existing = json.loads(row[0] or "{}")
            except Exception:
                existing = {}
            existing.update(journey_fields)
            con.execute(
                "UPDATE signals SET signal_json=? WHERE id=?",
                (json.dumps(existing, default=str), sig_id),
            )
            con.commit()
        con.close()

    except Exception as e:
        print(f"[journey_persist] signal_id={sig_id} error: {e}", file=sys.stderr)


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
            # HL returns timestamps in milliseconds — normalize to seconds
            df["timestamp"] = (pd.to_numeric(df["timestamp"], errors="coerce") // 1000).astype("Int64")
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


def evaluate_outcome(
    sig: dict,
    interval: str = "Min15",
    kline_limit: int = 336,
) -> tuple[str, str, float | None, str | None, str | None] | None:
    """
    Fetch klines since the signal was logged and determine if stop or TP was hit.

    interval / kline_limit: callers can override. The main signal evaluator uses
    Min15 / 336 (84 hours). The paper bot uses Min1 / 1440 (24 hours) so that
    1-minute wicks trigger stops and TPs just as they would in live trading.

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

    if not symbol or not direction or not entry1 or not stop_loss:
        return None

    try:
        dt       = datetime.fromisoformat(logged_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        start_ts = int(dt.timestamp())
    except Exception:
        return None

    # Need at least one full candle after the signal
    candle_seconds = {"Min1": 60, "Min5": 300, "Min15": 900, "Min30": 1800}.get(interval, 900)
    if time.time() - start_ts < candle_seconds:
        return None

    klines = _fetch_klines_for_signal(
        sig,
        interval=interval,
        start_ts=start_ts,
        limit=kline_limit,
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

    raw_opens = klines["open"].tolist() if "open" in klines.columns else []

    # Build candle list — only candles that opened at or after scan_start_ts.
    # `open` is included so the in-candle TP/stop tie-break heuristic below
    # can use intra-candle direction. Audit §04 fix candle_ordering_001.
    candles: list[dict] = []
    for i, t in enumerate(raw_times):
        try:
            candle_ts = int(t)
            if candle_ts >= scan_start_ts:
                candles.append({
                    "time":  candle_ts,
                    "open":  float(raw_opens[i]) if i < len(raw_opens) else float(raw_closes[i]),
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
        o, cl = c.get("open", c["close"]), c["close"]

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

        # In-candle TP/stop tie-break heuristic (audit §04 candle_ordering_001).
        # When a single 15m candle touches both a TP level and the stop, the
        # prior implementation evaluated stop first and broke immediately,
        # recording LOSS even if the TP was likely hit first. We can't know
        # the intra-candle tick sequence, but candle open→close direction is
        # a reasonable proxy:
        #   LONG  bullish candle (close > open)  → TP probably hit first  → escalate best_tp BEFORE recording stop
        #   LONG  bearish candle (close <= open) → stop probably hit first → LOSS as before
        #   SHORT bearish candle (close < open)  → TP probably hit first
        #   SHORT bullish candle (close >= open) → stop probably hit first → LOSS as before
        # This converts a subset of LOSS rows into PARTIAL (TP1 then stop).
        # Not perfect — only tick data is — but better than "stop always wins".
        if direction == "LONG":
            _both_touched = (l <= stop_loss) and (
                (tp1 and h >= tp1) or (tp2 and h >= tp2) or (tp3 and h >= tp3)
            )
            _bullish_candle = cl > o
            if _both_touched and _bullish_candle:
                # TP likely hit first — escalate best_tp BEFORE we process the stop
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
                            f"TP{tier} touched at {tier_price} (in-candle heuristic)",
                        )
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
            # Mirror heuristic for SHORT: bearish candle (close < open) means
            # the move down dominated, so a TP touch likely happened before
            # the stop touch in the same candle.
            _short_both_touched = (h >= stop_loss) and (
                (tp1 and l <= tp1) or (tp2 and l <= tp2) or (tp3 and l <= tp3)
            )
            _bearish_candle = cl < o
            if _short_both_touched and _bearish_candle:
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
                            f"TP{tier} touched at {tier_price} (in-candle heuristic)",
                        )
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

            # Persist journey metrics into signal_json for Research Firm analysis.
            _journey_sig = {
                **sig,
                "result": result,
                "result_at": result_at or datetime.utcnow().isoformat(),
                "exit_price": exit_price,
                "entry_at": entry_at,
                "pnl_pct": pnl_pct,
            }
            _persist_journey_to_signal_json(sig["id"], _journey_sig, pnl_pct)

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

    overrides = _load_strategy_overrides()
    result = []
    for key, cfg in registry.items():
        if not cfg.get("enabled", True) and not include_disabled:
            continue
        s = strategy_to_api(key, cfg, performance=perf.get(key, {}))
        s["conviction_override"] = overrides.get(key, {}).get("min_conviction")
        result.append(s)
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
            "extreme_vol_firebreak": _load_extreme_vol_firebreak(),
        }
        return jsonify(response)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/risk-gates/extreme_vol_firebreak", methods=["PATCH"])
def api_update_extreme_vol_firebreak():
    """Toggle the extreme vol firebreak gate. Body: {"enabled": true|false}."""
    try:
        body = request.get_json(silent=True) or {}
        if "enabled" not in body:
            return jsonify({"success": False, "error": "enabled field required"}), 400
        val = bool(body["enabled"])
        _save_extreme_vol_firebreak(val)
        return jsonify({"success": True, "extreme_vol_firebreak": val})
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


@app.route("/api/backfill/journey", methods=["POST"])
def api_backfill_journey():
    """
    MAINTENANCE — Backfill journey_* metrics into signal_json for closed signals
    that predate the _persist_journey_to_signal_json() call.

    Processes up to 500 signals per call. Safe to repeat — already-filled rows
    are skipped. Run from VPS shell: curl -X POST http://localhost:8080/api/backfill/journey
    """
    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        rows = con.execute("""
            SELECT id, symbol, exchange, direction,
                   entry1, tp1, tp2, tp3, stop_loss,
                   logged_at, result_at, entry_at,
                   exit_price, pnl_pct, leverage,
                   result, signal_json
            FROM signals
            WHERE result IS NOT NULL
              AND result NOT IN ('EXPIRED', 'SKIPPED')
              AND entry1 IS NOT NULL
              AND (
                signal_json IS NULL
                OR json_extract(signal_json, '$.journey_available') IS NULL
              )
            ORDER BY logged_at DESC
            LIMIT 500
        """).fetchall()
        con.close()

        processed = 0
        filled = 0
        errors = 0

        for row in rows:
            sig = dict(row)
            processed += 1
            try:
                _persist_journey_to_signal_json(sig["id"], sig, sig.get("pnl_pct"))
                filled += 1
            except Exception as e:
                print(f"[backfill/journey] id={sig['id']} error: {e}", file=sys.stderr)
                errors += 1

        return jsonify({
            "success": True,
            "processed": processed,
            "filled": filled,
            "errors": errors,
            "note": "Run again if processed=500 (batch limit) until processed=0",
        })
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


@app.route("/api/strategy-overrides", methods=["GET"])
def api_get_strategy_overrides():
    """Return active strategy overrides with original values from the registry."""
    try:
        overrides = _load_strategy_overrides()
        registry = get_strategy_registry()
        result = {}
        for key, vals in overrides.items():
            original = registry.get(key, {}).get("min_conviction")
            result[key] = {**vals, "original_min_conviction": original}
        return jsonify({"success": True, "overrides": result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/strategy-overrides/<strategy_key>", methods=["DELETE"])
def api_revert_strategy_override(strategy_key: str):
    """Remove the override for strategy_key from strategy_overrides.json. Idempotent."""
    try:
        existing = _load_strategy_overrides()
        if strategy_key in existing:
            del existing[strategy_key]
            tmp = STRATEGY_OVERRIDES_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2, sort_keys=True)
            os.replace(tmp, STRATEGY_OVERRIDES_PATH)
        return jsonify({"success": True, "strategy_key": strategy_key, "reverted": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/account/daily-pnl")
def api_account_daily_pnl():
    """Today's realized P&L summary — used by P9 trade readiness panel."""
    try:
        result = compute_daily_pnl(DB_PATH)
        return jsonify({"success": True, **result})
    except Exception as e:
        return jsonify({"success": False, "total_pnl_pct": 0.0, "wins": 0, "losses": 0, "count": 0})


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
                registry = get_strategy_registry()
                if strategy_key in registry and strategy_key not in _get_custom_strategy_keys():
                    # Built-in strategy — apply via strategy_overrides.json
                    min_conviction = payload.get("min_conviction")
                    if min_conviction is not None:
                        _save_strategy_override(strategy_key, "min_conviction", int(min_conviction))
                    else:
                        return jsonify({'success': False,
                                        'error': 'min_conviction missing from payload'}), 400
                else:
                    # Custom strategy — use existing PATCH route
                    with app.test_client() as c:
                        r = c.patch(f'/api/strategies/custom/{strategy_key}',
                                    data=json.dumps(payload),
                                    content_type='application/json')
                        if r.status_code not in (200, 201):
                            return jsonify({'success': False,
                                            'error': 'failed to update strategy'}), 500
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


@app.route('/api/intelligence/research')
def api_intelligence_research():
    """Read strategy hypothesis briefs from the mt-learner research firm."""
    try:
        briefs_path = '/opt/mt-learner/research/briefs.json'
        heartbeat_path = '/opt/mt-learner/logs/last_heartbeat.txt'

        learner_running = False
        try:
            mtime = os.path.getmtime(heartbeat_path)
            learner_running = (time.time() - mtime) < 600
        except OSError:
            pass

        if not os.path.exists(briefs_path):
            return jsonify({
                'success': True, 'briefs': [],
                'active_count': 0, 'learner_running': learner_running,
            })

        with open(briefs_path, 'r') as f:
            data = json.load(f)

        briefs = data.get('briefs', [])
        active_count = sum(1 for b in briefs if b.get('status') == 'active')

        return jsonify({
            'success': True,
            'briefs': briefs,
            'active_count': active_count,
            'learner_running': learner_running,
            'total_signals_analyzed': data.get('total_signals_analyzed', 0),
            'generated_at': data.get('generated_at'),
        })
    except Exception as e:
        print(f'[api/intelligence/research] error: {e}', file=sys.stderr)
        return jsonify({
            'success': True, 'briefs': [],
            'active_count': 0, 'learner_running': False,
            'error': str(e),
        })


REPORTS_DIR = "data/reports"


def _report_classify_session(utc_iso: str) -> str:
    """Classify a UTC timestamp into the crypto desk session buckets."""
    try:
        dt = datetime.fromisoformat(str(utc_iso).replace("Z", "").split(".")[0])
        h = dt.hour
        if 0 <= h < 8:
            return "ASIA"
        if 8 <= h < 13:
            return "LONDON"
        if 13 <= h < 21:
            return "NY"
        return "ASIA"
    except Exception:
        return "UNKNOWN"


def _report_db_rows(table: str, date_col: str | None = None, date_value: str | None = None,
                    days_back: int | None = None, limit: int = 500) -> list[dict]:
    """Best-effort SQLite reader for report sections; returns [] on schema drift."""
    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        exists = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not exists:
            con.close()
            return []
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
        where = ""
        params: list = []
        if date_col and date_col in cols and date_value:
            if days_back:
                where = f"WHERE date({date_col}) >= date(?, ?)"
                params = [date_value, f"-{days_back} days"]
            else:
                where = f"WHERE date({date_col}) = date(?)"
                params = [date_value]
        order = f"ORDER BY {date_col} DESC" if date_col and date_col in cols else ""
        rows = con.execute(
            f"SELECT * FROM {table} {where} {order} LIMIT ?",
            (*params, limit),
        ).fetchall()
        con.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[report_db_rows] {table}: {e}", file=sys.stderr)
        return []


def _report_signal_json(row: dict) -> dict:
    try:
        raw = row.get("signal_json") or "{}"
        return json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception:
        return {}


def _report_fetch_ticker_snapshot() -> list[dict]:
    """Fetch MEXC tickers for market pulse, funding heatmap, movers, and coiling."""
    try:
        resp = requests.get(f"{MEXC_BASE}/contract/ticker", timeout=10)
        payload = resp.json()
        if payload.get("success") and isinstance(payload.get("data"), list):
            return payload["data"]
    except Exception as e:
        print(f"[report_ticker] {e}", file=sys.stderr)
    return []


def _report_symbol(row: dict) -> str:
    return row.get("symbol") or row.get("contract") or row.get("ticker") or ""


def _report_float(value, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def _report_percent_change(row: dict) -> float:
    if "change_24h_pct" in row:
        return _report_float(row.get("change_24h_pct"))
    return _report_float(row.get("riseFallRate")) * 100


def _report_funding(row: dict) -> float:
    return _report_float(row.get("funding_rate", row.get("fundingRate")))


def _report_volume(row: dict) -> float:
    return _report_float(row.get("volume_24h", row.get("volume24", row.get("amount24"))))


def _report_empty_narrative() -> dict:
    return {
        "trader_open": "",
        "regime_forecast": "",
        "risk_close": "",
        "funding_autopsy": "",
        "microstructure_autopsy": "",
        "cross_venue_autopsy": "",
        "week_ahead": "",
        "spotlight": "",
    }


def _report_fmt_symbol(item: dict | None) -> str:
    if not item:
        return "the watchlist"
    return str(item.get("symbol") or "the watchlist")


def _report_fmt_move(item: dict | None) -> str:
    if not item:
        return "0.00%"
    return f"{_report_float(item.get('change_24h_pct')):.2f}%"


def _report_count_funding_extremes(data: dict) -> tuple[int, int]:
    heatmap = data.get("funding_heatmap") or {}
    neg = len(heatmap.get("extreme_negative") or [])
    pos = len(heatmap.get("extreme_positive") or [])
    return neg, pos


def _build_deterministic_report_narrative(data: dict, weekly: bool = False) -> dict:
    """
    No-cost Cipher desk notes built directly from report data.

    This is the baseline narrative layer. Paid/free hosted LLMs can polish it,
    but reports should never go blank because a provider key is missing or a
    free-tier quota is exhausted.

    All notes are written in first-person analyst voice. No 3rd-person framing.
    Every note ends with a forward call — what to watch or do next.
    """
    narrative = _report_empty_narrative()
    pulse = data.get("market_pulse") or {}
    paper = data.get("paper_desk") or {}
    top_gainers = data.get("top_gainers") or []
    top_losers = data.get("top_losers") or []
    coiling = data.get("whats_coiling") or []
    disagreements = data.get("disagreements") or []
    explosive = data.get("explosive_move") or {}
    explosive_ctx = data.get("explosive_signal_context") or {}
    coiling_rates = data.get("coiling_base_rates") or {}
    strategy_perf = data.get("strategy_regime_perf") or []
    neg_funding, pos_funding = _report_count_funding_extremes(data)

    top_gain = top_gainers[0] if top_gainers else None
    top_loss = top_losers[0] if top_losers else None
    signals = int(pulse.get("signals") or 0)
    blocked = int(pulse.get("blocked") or 0)
    regime = str(pulse.get("dominant_regime") or "unknown").replace("_", " ")

    # --- Thomas: trader_open — signal count, regime, best strategy, funding crowding ---
    best_strat_note = ""
    if strategy_perf:
        regime_key = str(pulse.get("dominant_regime") or "")
        regime_rows = [r for r in strategy_perf if r.get("regime", "") == regime_key]
        if not regime_rows:
            regime_rows = strategy_perf
        def _win_rate(r: dict) -> float:
            w = int(r.get("win", 0)) + int(r.get("partial", 0))
            total = w + int(r.get("loss", 0))
            return w / total if total >= 3 else 0.0
        best = max(regime_rows, key=_win_rate, default=None)
        if best:
            bw = int(best.get("win", 0)) + int(best.get("partial", 0))
            btotal = bw + int(best.get("loss", 0))
            if btotal >= 3:
                best_strat_note = (
                    f" {best.get('strategy', 'unknown').replace('_', ' ').title()} "
                    f"is leading in this regime — {bw}/{btotal} closed trades on the right side."
                )

    funding_crowd = ""
    if neg_funding and pos_funding:
        funding_crowd = f" Funding pressure is split: {neg_funding} names carry negative rate (short squeeze risk), {pos_funding} carry positive (long flush risk)."
    elif neg_funding:
        funding_crowd = f" {neg_funding} names are running extreme negative funding — shorts are paying heavily to stay on."
    elif pos_funding:
        funding_crowd = f" {pos_funding} names are running extreme positive funding — longs are carrying a crowded bet."

    top_gain_str = _report_fmt_symbol(top_gain)
    top_loss_str = _report_fmt_symbol(top_loss)
    narrative["trader_open"] = (
        f"I logged {signals} signals today with {blocked} candidates blocked before they reached the desk. "
        f"The tape is reading {regime}.{best_strat_note}{funding_crowd} "
        f"The move that stood out on strength was {top_gain_str} ({_report_fmt_move(top_gain)}); "
        f"the clearest weakness was {top_loss_str} ({_report_fmt_move(top_loss)}). "
        f"Watch whether the dominant regime holds through the next session — a regime shift mid-tape changes which filters apply."
    )

    # --- Nadia: regime_forecast — coiling setups + historical base rates ---
    if coiling:
        lead = coiling[0]
        lead_sym = lead.get("symbol", "")
        lead_fr = _report_float(lead.get("funding_rate"))
        lead_fr_ann = abs(lead_fr) * 3 * 365 * 100
        watch = lead.get("watch", "SHORT SQUEEZE")
        base_note = ""
        if lead_sym in coiling_rates:
            br = coiling_rates[lead_sym]
            n = br.get("n", 0)
            wr = br.get("win_rate")
            if wr is not None and n >= 5:
                base_note = f" History: {n} prior setups with this funding profile resolved in favor of {br.get('direction', watch)} {wr}% of the time."
            elif n < 5:
                base_note = f" We have fewer than 5 historical comps for this setup — treat it as unvalidated edge."
        narrative["regime_forecast"] = (
            f"I'm reading pressure build, not trend. {lead_sym} has barely moved ({_report_fmt_move(lead)}) "
            f"but funding is at {lead_fr:.5f} — that's {lead_fr_ann:.0f}% annualized carry. "
            f"Something has to give: either price breaks in the direction of the carry, or funding normalizes as positions unwind.{base_note} "
            f"Watch {lead_sym} for a volume surge that resolves the coil. Until then, size down on any signal touching this name."
        )
    else:
        regime_note = f"The {regime} regime is the dominant read today."
        if strategy_perf:
            regime_key = str(pulse.get("dominant_regime") or "")
            regime_rows = [r for r in strategy_perf if r.get("regime", "") == regime_key]
            if regime_rows:
                def _wr(r: dict) -> float:
                    w = int(r.get("win", 0)) + int(r.get("partial", 0))
                    t = w + int(r.get("loss", 0))
                    return w / t if t >= 3 else 0.0
                best = max(regime_rows, key=_wr, default=None)
                if best and _wr(best) > 0:
                    bw = int(best.get("win", 0)) + int(best.get("partial", 0))
                    bt = bw + int(best.get("loss", 0))
                    regime_note = (
                        f"In {regime} regimes, our {best.get('strategy', '').replace('_', ' ').title()} filter "
                        f"has {bw}/{bt} on the right side of closed trades."
                    )
        pressure_summary = ""
        if neg_funding or pos_funding:
            pressure_summary = f" Funding extremes are present ({neg_funding} negative, {pos_funding} positive) but no coiling setup meets the threshold today."
        narrative["regime_forecast"] = (
            f"{regime_note}{pressure_summary} "
            f"No coiling setups on the board means the market isn't telegraphing a squeeze or flush. "
            f"My posture here is selective: require clean structure, elevated volume, and funded direction before taking any position."
        )

    # --- Harper: risk_close — gate names, blocked counts, forward posture ---
    blocked_gates: list[str] = []
    for b in (data.get("blocked") or [])[:20]:
        gk = b.get("gate_key") or ""
        if gk and gk not in blocked_gates:
            blocked_gates.append(gk)

    if blocked or disagreements:
        gate_detail = ""
        if blocked_gates:
            gate_detail = f" The active gates were: {', '.join(blocked_gates[:4])}."
        dis_note = ""
        if disagreements:
            dis_note = f" {len(disagreements)} signals came through with material analyst disagreement — those are lower-confidence entries."
        narrative["risk_close"] = (
            f"I blocked {blocked} candidates before they reached your list.{gate_detail}{dis_note} "
            f"This is not a signal to trade less — it's a signal to size correctly. "
            f"Blocked candidates don't disappear; they reappear when conditions improve. "
            f"Watch the blocked list for symbols that clear gate criteria on the next scan."
        )
    else:
        narrative["risk_close"] = (
            f"I blocked nothing today and saw no major disagreement across analysts. "
            f"That reads as a clean tape — but clean tapes are where discipline matters most. "
            f"When gates are quiet, the instinct is to increase size. Resist it. "
            f"Keep stops mechanical and let the signal quality drive position sizing, not the absence of red flags."
        )

    # --- Kenny: funding_autopsy — alignment, annualized carry, signal cross-reference ---
    if explosive:
        exp_sym = explosive.get("symbol", "")
        exp_change = _report_float(explosive.get("change_24h_pct"))
        exp_fr = _report_float(explosive.get("funding_rate"))
        exp_fr_ann = abs(exp_fr) * 3 * 365 * 100 if exp_fr else 0

        # Classify funding alignment
        if exp_change > 0 and exp_fr < -0.0003:
            alignment = "short squeeze"
            alignment_note = (
                f"Funding was deeply negative ({exp_fr:.5f}, {exp_fr_ann:.0f}% annualized) while price ripped {exp_change:.1f}%. "
                f"That's a classic squeeze — shorts got carried out. Late entries from here carry the wrong side of funding."
            )
        elif exp_change < 0 and exp_fr > 0.0003:
            alignment = "long flush"
            alignment_note = (
                f"Funding was elevated ({exp_fr:.5f}, {exp_fr_ann:.0f}% annualized) while price dropped {abs(exp_change):.1f}%. "
                f"That's a long flush — crowded longs unwinding. Catching this knife requires a funding reset first."
            )
        elif exp_fr and abs(exp_fr) > 0.0003 and exp_change * (1 if exp_fr > 0 else -1) > 0:
            alignment = "aligned carry"
            alignment_note = (
                f"Funding at {exp_fr:.5f} ({exp_fr_ann:.0f}% annualized) moved in the same direction as price. "
                f"That's not a squeeze — it's a carry trade. Momentum chasers are paying to be right. "
                f"Watch for the unwind when funding flips."
            )
        else:
            alignment = "neutral"
            alignment_note = (
                f"Funding was neutral ({exp_fr:.5f}) during the {exp_change:.1f}% move on {exp_sym}. "
                f"This move was driven by something other than carry pressure — likely spot flow or news. "
                f"No funding edge here in either direction."
            )

        # Signal cross-reference
        sig_note = ""
        if explosive_ctx.get("had_signal"):
            sig_dir = explosive_ctx.get("signal_direction", "")
            sig_conv = explosive_ctx.get("signal_conviction", "")
            sig_result = explosive_ctx.get("signal_result") or "open"
            sig_note = (
                f" {exp_sym} was on our list today — {sig_dir} at {sig_conv} conviction, currently {sig_result}. "
                f"The system caught this name."
            )
        elif explosive_ctx.get("was_blocked"):
            gate = explosive_ctx.get("block_gate", "a risk gate")
            sig_note = f" {exp_sym} was blocked by {gate} before reaching the signal list — it was on our radar but gated out."
        else:
            sig_note = f" {exp_sym} did not appear on today's signal list. Worth reviewing why — if structure was absent at scan time, the move may have been un-scannable."

        narrative["funding_autopsy"] = (
            f"The big move today was {exp_sym} at {exp_change:+.1f}%. {alignment_note}{sig_note} "
            f"Forward call: if this is a {alignment}, watch for funding to normalize over the next 8–24 hours. "
            f"That normalization window is when the next tradeable setup typically appears."
        )
    else:
        if neg_funding or pos_funding:
            narrative["funding_autopsy"] = (
                f"No single explosive move to dissect today, but funding pressure is real. "
                f"{neg_funding} names run extreme negative funding (short squeeze risk) and "
                f"{pos_funding} run extreme positive (long flush risk). "
                f"Crowded carry without price resolution is a pending event, not a stable condition. "
                f"Watch for a catalyst that forces the unwind — that's where the next explosive move comes from."
            )
        else:
            narrative["funding_autopsy"] = (
                f"No explosive move and no extreme funding today. "
                f"Funding-neutral environments reduce one edge signal but don't eliminate others. "
                f"Structure and volume still apply. My read: stay selective, let setup quality drive entries."
            )

    # --- Niobe: microstructure_autopsy — volume classification, continuation vs trap ---
    if explosive:
        exp_sym = explosive.get("symbol", "")
        exp_change = _report_float(explosive.get("change_24h_pct"))
        exp_vol = _report_float(explosive.get("volume_24h"))

        if exp_vol >= 500_000_000:
            vol_label = "institutional-grade"
            vol_read = "participation at this scale is hard to fake — the move had real buyers or sellers behind it"
        elif exp_vol >= 100_000_000:
            vol_label = "decent"
            vol_read = "volume was enough to move the market but not enough to call this a consensus event"
        elif exp_vol >= 20_000_000:
            vol_label = "thin"
            vol_read = "thin volume on a large percentage move is a trap signal — sharp candles with no follow-through are common in this profile"
        else:
            vol_label = "very thin"
            vol_read = "this moved on almost no volume. Treat it as noise until real participation shows up"

        continuation_call = "Watch for volume to expand on the next hourly candle — that's continuation. If volume collapses, the move was a wick, not a trend."
        if vol_label in ("institutional-grade", "decent") and abs(exp_change) > 5:
            continuation_call = "High-volume, large-move combination has continuation odds in its favor. Monitor for a retest of the breakout level — that's the entry I'd want, not the breakout itself."

        narrative["microstructure_autopsy"] = (
            f"I care less about the candle size and more about what was behind it. "
            f"{exp_sym} moved {exp_change:+.1f}% on {exp_vol / 1e6:.0f}M USD volume — that's {vol_label}: {vol_read}. "
            f"{continuation_call}"
        )
    else:
        narrative["microstructure_autopsy"] = (
            f"No single move demanded an autopsy today. "
            f"The standing rule: volume proportional to move size is continuation. Volume mismatched to move size is trap. "
            f"If a name appears on the signal list with thin order book depth, drop size by half — the spread will eat the edge."
        )

    # --- Ghost: cross_venue_autopsy — venue leader, arb gap implications ---
    if explosive:
        exp_sym = explosive.get("symbol", "")
        exp_exchange = explosive.get("exchange") or "MEXC"
        exp_change = _report_float(explosive.get("change_24h_pct"))

        if exp_exchange == "MEXC":
            venue_note = (
                f"MEXC led this move on {exp_sym}. MEXC leads when perp traders are driving — "
                f"there's no spot market to anchor it, so funding and liquidations do the work. "
                f"Check Hyperliquid for confirmation: if HL is flat, the move is MEXC-specific and deserves less conviction."
            )
        elif exp_exchange in ("Hyperliquid", "HL"):
            venue_note = (
                f"Hyperliquid led on {exp_sym} — that's a sophisticated-flow signal. "
                f"HL tends to attract directional traders with higher conviction. "
                f"If MEXC is lagging, there's an arb window, but it closes fast. "
                f"I'd rather trade the HL-led direction on MEXC after MEXC catches up."
            )
        else:
            venue_note = (
                f"{exp_exchange} led on {exp_sym}. Cross-venue leadership from smaller venues "
                f"is harder to interpret — check if MEXC and HL are moving in sympathy. "
                f"If they are, the move is real. If they're flat, it's venue-local noise."
            )

        narrative["cross_venue_autopsy"] = (
            f"{exp_sym} moved {exp_change:+.1f}%. {venue_note} "
            f"As Bybit comes back online in this stack, three-venue agreement will become the gold standard. "
            f"Until then: two-venue confirmation is strong enough to trade; single-venue requires tighter stops."
        )
    else:
        narrative["cross_venue_autopsy"] = (
            f"No venue-leader event to call out today. "
            f"Cross-venue arb opportunities are quiet when the tape is range-bound. "
            f"The edge I'm building toward: when MEXC and Hyperliquid diverge by more than 0.5% on the same pair, "
            f"that gap telegraphs the next directional move. Watch for that pattern on any name running extreme funding."
        )

    # --- Weekly additions ---
    if weekly:
        wins = int(paper.get("wins") or 0)
        closed = int(paper.get("closed") or 0)
        avg_pnl = _report_float(paper.get("avg_pnl_pct"))
        total_pnl = _report_float(paper.get("total_pnl_pct"))

        perf_note = ""
        if closed >= 5:
            win_rate = round(wins / closed * 100) if closed else 0
            if win_rate >= 60 and avg_pnl > 0:
                perf_note = f" The paper numbers are encouraging — {win_rate}% win rate and {avg_pnl:.2f}% average P&L. If these hold another two weeks, the filters are earning promotion to live."
            elif win_rate < 40 or avg_pnl < 0:
                perf_note = f" The paper numbers are telling me something is wrong — {win_rate}% win rate and {avg_pnl:.2f}% average P&L. Before going live, I need to understand whether this is a filter problem or a regime problem."
            else:
                perf_note = f" Paper is running at {win_rate}% and {avg_pnl:.2f}% average — acceptable but not yet compelling. Give it more reps before reading too much into it."
        elif closed > 0:
            perf_note = f" Only {closed} paper trades closed this week — too thin for statistical reads. Keep running."
        else:
            perf_note = " No paper trades closed this week. Check that the paper bot is scanning and that minimum conviction thresholds are reachable."

        narrative["week_ahead"] = (
            f"I closed {closed} paper trades this week with {wins} winners, {avg_pnl:.2f}% average, and {total_pnl:.2f}% total P&L.{perf_note} "
            f"Next week, I'm watching whether the dominant regime persists or rotates — regime changes are where backtested win rates stop applying. "
            f"If funding extremes are still building, expect at least one squeeze or flush in the first half of the week."
        )

        spotlight_key = data.get("spotlight_key") or "funding"
        spotlight_name_map = {
            "funding": "Kenny Zhao",
            "microstructure": "Niobe",
            "cross_venue": "Ghost",
            "regime": "Nadia Reyes",
            "technical": "Ryo Tanaka",
            "sentiment": "Zara Cole",
            "tokenomics": "Dr. Asha Mehta",
        }
        spotlight_name = spotlight_name_map.get(spotlight_key, spotlight_key.replace("_", " ").title())
        narrative["spotlight"] = (
            f"This week's spotlight is {spotlight_name}. "
            f"Pull up the signals where {spotlight_name}'s domain was the deciding factor — "
            f"did those calls add edge or cost us? That's the question that earns the analyst a bigger role next week. "
            f"Spotlights rotate to prevent anchoring on a single voice."
        )

    return narrative


def _merge_report_narrative(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in (override or {}).items():
        if key in merged and isinstance(value, str) and value.strip():
            merged[key] = value.strip()
    return merged


def _call_report_ai(data: dict, weekly: bool = False) -> tuple[dict, bool, str]:
    """Generate concise cached report notes. Deterministic notes are the free fallback."""
    narrative = _build_deterministic_report_narrative(data, weekly=weekly)
    mode = REPORT_NARRATIVE_MODE
    if mode in {"deterministic", "off", "none"}:
        return narrative, False, "deterministic"
    allowed_providers = None
    if mode in {"free", "free_only", "free-only"}:
        allowed_providers = REPORT_FREE_AI_PROVIDERS
    elif mode in {"auto", "any"}:
        allowed_providers = None
    elif mode:
        # Future-friendly: REPORT_NARRATIVE_MODE=ollama or =gemini restricts
        # report polishing to that provider while keeping deterministic fallback.
        allowed_providers = {mode}
    try:
        from lib.agents import AGENT_ROSTER, FIRM_META
        compact = {
            "date": data.get("date"),
            "week": data.get("week"),
            "market_pulse": data.get("market_pulse"),
            "top_gainers": data.get("top_gainers", [])[:3],
            "top_losers": data.get("top_losers", [])[:3],
            "explosive_move": data.get("explosive_move"),
            "disagreements": data.get("disagreements", [])[:4],
            "strategy_regime_perf": data.get("strategy_regime_perf", [])[:8],
            "paper_desk": data.get("paper_desk", {}),
        }
        system = (
            f"You are the editorial desk for {FIRM_META['name']}. Return only JSON. "
            "Write concise institutional crypto research notes for retail traders. "
            "Do not imitate copyrighted dialogue; use the broad voice traits provided."
        )
        requested = [
            ("trader_open", AGENT_ROSTER["trader"]),
            ("regime_forecast", AGENT_ROSTER["regime"]),
            ("risk_close", AGENT_ROSTER["risk_manager"]),
            ("funding_autopsy", AGENT_ROSTER["funding"]),
            ("microstructure_autopsy", AGENT_ROSTER["microstructure"]),
            ("cross_venue_autopsy", AGENT_ROSTER["cross_venue"]),
        ]
        if weekly:
            requested.extend([
                ("week_ahead", AGENT_ROSTER["trader"]),
                ("spotlight", AGENT_ROSTER.get(data.get("spotlight_key", "funding"), AGENT_ROSTER["funding"])),
            ])
        user = (
            "Use this data:\n"
            f"{json.dumps(compact, default=str)}\n\n"
            "Return this JSON object with one 35-80 word string per key:\n"
            f"{json.dumps({k: v['voice'] for k, v in requested}, indent=2)}"
        )
        raw = call_ai(
            system=system,
            user=user,
            max_tokens=900,
            allowed_providers=allowed_providers,
        )
        if not raw:
            return narrative, False, "deterministic"
        clean = raw.strip()
        if clean.startswith("```"):
            clean = clean.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        parsed = json.loads(clean)
        if isinstance(parsed, dict):
            return _merge_report_narrative(narrative, parsed), True, "ai"
    except Exception as e:
        print(f"[report_ai] {e}", file=sys.stderr)
    return narrative, False, "deterministic"


def _report_explosive_signal_context(symbol: str, date_str: str) -> dict:
    """Check if the explosive move pair appeared in today's signals or blocked list."""
    ctx: dict = {"had_signal": False, "was_blocked": False}
    if not symbol:
        return ctx
    try:
        con = sqlite3.connect(DB_PATH)
        row = con.execute(
            "SELECT direction, conviction, result, strategy_key FROM signals "
            "WHERE symbol=? AND date(logged_at)=? ORDER BY conviction DESC LIMIT 1",
            (symbol, date_str),
        ).fetchone()
        if row:
            ctx.update({"had_signal": True, "signal_direction": row[0],
                         "signal_conviction": row[1], "signal_result": row[2],
                         "signal_strategy": row[3]})
        blk = con.execute(
            "SELECT gate_key FROM filtered_candidates WHERE symbol=? AND date(logged_at)=? LIMIT 1",
            (symbol, date_str),
        ).fetchone()
        if blk:
            ctx.update({"was_blocked": True, "block_gate": blk[0]})
        con.close()
    except Exception as e:
        print(f"[explosive_ctx] {e}", file=sys.stderr)
    return ctx


def _report_coiling_base_rates(coiling: list, date_str: str) -> dict:
    """Historical win rate for extreme-funding setups in the signals DB."""
    result: dict = {}
    if not coiling:
        return result
    try:
        con = sqlite3.connect(DB_PATH)
        for entry in coiling[:4]:
            fr = _report_float(entry.get("funding_rate"))
            sym = entry.get("symbol", "")
            if fr == 0 or not sym:
                continue
            direction = "LONG" if fr < 0 else "SHORT"
            threshold = max(abs(fr) * 0.5, 0.0003)
            sign = -1 if fr < 0 else 1
            rows = con.execute(
                "SELECT result FROM signals WHERE direction=? "
                "AND funding_rate IS NOT NULL AND ABS(funding_rate)>=? "
                "AND (CASE WHEN funding_rate<0 THEN -1 ELSE 1 END)=? "
                "AND result IN ('WIN','LOSS','PARTIAL') AND date(logged_at)<date(?) LIMIT 100",
                (direction, threshold, sign, date_str),
            ).fetchall()
            n = len(rows)
            wins = sum(1 for r in rows if r[0] in ("WIN", "PARTIAL"))
            result[sym] = {
                "n": n,
                "win_rate": round(wins / n * 100) if n >= 5 else None,
                "direction": direction,
            }
        con.close()
    except Exception as e:
        print(f"[coiling_base_rates] {e}", file=sys.stderr)
    return result


def _build_daily_data(date_str: str, ticker_snapshot: list[dict] | None = None) -> dict:
    """Build template-driven daily report data from Matrix Trader state."""
    signals = _report_db_rows("signals", "logged_at", date_str, limit=1000)
    blocked = _report_db_rows("filtered_candidates", "logged_at", date_str, limit=500)
    paper = _report_db_rows("paper_trades", "opened_at", date_str, days_back=7, limit=500)
    tickers = ticker_snapshot if ticker_snapshot is not None else _report_fetch_ticker_snapshot()

    exchange_breakdown: dict[str, int] = {}
    regime_counts: dict[str, int] = {}
    sessions = {k: {"count": 0, "symbols": []} for k in ("ASIA", "LONDON", "NY", "UNKNOWN")}
    disagreements = []

    for sig in signals:
        exchange = sig.get("exchange") or "MEXC"
        symbol = _report_symbol(sig)
        exchange_breakdown[exchange] = exchange_breakdown.get(exchange, 0) + 1
        session = _report_classify_session(sig.get("logged_at", ""))
        sessions.setdefault(session, {"count": 0, "symbols": []})
        sessions[session]["count"] += 1
        if symbol:
            sessions[session]["symbols"].append(symbol)

        sj = _report_signal_json(sig)
        regime = sj.get("agent_regime") or sj.get("regime") or "unknown"
        regime_counts[regime] = regime_counts.get(regime, 0) + 1
        score = _report_float(sj.get("agent_shadow_disagreement", sj.get("agent_disagreement")), 0)
        if score > 0.4:
            disagreements.append({
                "symbol": symbol,
                "exchange": exchange,
                "direction": sig.get("direction"),
                "conviction": sig.get("conviction"),
                "disagreement_score": round(score, 3),
                "narrative_bull": sj.get("agent_narrative_bull"),
                "structural_bull": sj.get("agent_structural_bull"),
            })

    def ticker_item(t: dict) -> dict:
        return {
            "symbol": _report_symbol(t),
            "exchange": t.get("exchange") or "MEXC",
            "change_24h_pct": round(_report_percent_change(t), 2),
            "volume_24h": round(_report_volume(t), 0),
            "funding_rate": _report_funding(t),
        }

    ticker_items = [ticker_item(t) for t in tickers if _report_symbol(t)]
    ticker_items.sort(key=lambda x: x["change_24h_pct"], reverse=True)
    top_gainers = ticker_items[:5]
    top_losers = sorted(ticker_items, key=lambda x: x["change_24h_pct"])[:5]

    heatmap = sorted(
        [{"symbol": t["symbol"], "exchange": t["exchange"], "funding_rate": t["funding_rate"]}
         for t in ticker_items if t["funding_rate"] is not None],
        key=lambda x: x["funding_rate"],
    )
    funding_heatmap = {
        "extreme_negative": [h for h in heatmap if h["funding_rate"] < -0.001][:10],
        "mild_negative": [h for h in heatmap if -0.001 <= h["funding_rate"] < -0.00025][:10],
        "neutral": [h for h in heatmap if abs(h["funding_rate"]) <= 0.00025][:8],
        "mild_positive": [h for h in heatmap if 0.00025 < h["funding_rate"] <= 0.001][:10],
        "extreme_positive": [h for h in heatmap if h["funding_rate"] > 0.001][:10],
    }
    coiling = [
        {
            "symbol": t["symbol"],
            "exchange": t["exchange"],
            "funding_rate": t["funding_rate"],
            "change_24h_pct": t["change_24h_pct"],
            "watch": "SHORT SQUEEZE" if t["funding_rate"] < 0 else "LONG FLUSH",
        }
        for t in ticker_items
        if abs(t["funding_rate"]) > 0.0008 and abs(t["change_24h_pct"]) < 3
    ][:8]

    explosive_move = None
    if ticker_items:
        explosive_move = max(ticker_items, key=lambda x: abs(x["change_24h_pct"]))

    perf_rows = _report_db_rows("signals", "logged_at", date_str, days_back=7, limit=2000)
    perf: dict[tuple[str, str], dict] = {}
    for row in perf_rows:
        result = str(row.get("result") or "").lower()
        if result not in ("win", "loss", "partial"):
            continue
        regime = _report_signal_json(row).get("agent_regime") or "unknown"
        strategy = row.get("strategy_key") or row.get("strategy") or "unknown"
        bucket = perf.setdefault((strategy, regime), {"win": 0, "loss": 0, "partial": 0})
        bucket[result] += 1

    closed_paper = [p for p in paper if str(p.get("status") or "").lower() == "closed"]
    pnl_values = [_report_float(p.get("pnl_pct")) for p in closed_paper if p.get("pnl_pct") is not None]
    paper_desk = {
        "open": len([p for p in paper if str(p.get("status") or "").lower() == "open"]),
        "closed": len(closed_paper),
        "wins": len([p for p in closed_paper if str(p.get("result") or "").upper() in ("WIN", "TP3")]),
        "avg_pnl_pct": round(sum(pnl_values) / len(pnl_values), 2) if pnl_values else 0,
        "total_pnl_pct": round(sum(pnl_values), 2) if pnl_values else 0,
    }

    dominant_regime = max(regime_counts, key=regime_counts.get) if regime_counts else "unknown"
    explosive_sym = (explosive_move or {}).get("symbol", "")
    explosive_signal_context = _report_explosive_signal_context(explosive_sym, date_str)
    coiling_base_rates = _report_coiling_base_rates(coiling, date_str)
    return {
        "date": date_str,
        "market_pulse": {
            "signals": len(signals),
            "blocked": len(blocked),
            "dominant_regime": dominant_regime,
            "desk_agreement": "high" if not disagreements else "mixed",
            "exchange_breakdown": exchange_breakdown,
        },
        "signals_today": signals[:100],
        "blocked": blocked[:100],
        "sessions": sessions,
        "regime_counts": regime_counts,
        "funding_heatmap": funding_heatmap,
        "top_gainers": top_gainers,
        "top_losers": top_losers,
        "explosive_move": explosive_move,
        "explosive_signal_context": explosive_signal_context,
        "whats_coiling": coiling,
        "coiling_base_rates": coiling_base_rates,
        "liquidation_clusters": [],
        "disagreements": disagreements[:10],
        "strategy_regime_perf": [
            {"strategy": k[0], "regime": k[1], **v}
            for k, v in sorted(perf.items())
        ],
        "desk_wrong": [],
        "paper_desk": paper_desk,
        "learner": _report_read_learner_files(),
    }


def _report_read_learner_files() -> dict:
    data = {"briefs": [], "suggestions": []}
    for key, path in (
        ("briefs", "/opt/mt-learner/research/briefs.json"),
        ("suggestions", "/opt/mt-learner/suggestions/pending.json"),
    ):
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                data[key] = payload.get(key, payload.get("briefs", payload if isinstance(payload, list) else []))
        except Exception:
            pass
    return data


def _build_weekly_data(week_key: str) -> dict:
    try:
        year, week_num = week_key.split("-W", 1)
        start = datetime.fromisocalendar(int(year), int(week_num), 1)
    except Exception:
        start = datetime.utcnow() - timedelta(days=6)
        year, week_num, _ = start.isocalendar()
        week_key = f"{year}-W{week_num:02d}"
    ticker_snapshot = _report_fetch_ticker_snapshot()
    days = []
    for offset in range(7):
        d = (start + timedelta(days=offset)).strftime("%Y-%m-%d")
        daily = _build_daily_data(d, ticker_snapshot=ticker_snapshot)
        days.append({
            "date": d,
            "market_pulse": daily["market_pulse"],
            "top_gainers": daily["top_gainers"][:3],
            "top_losers": daily["top_losers"][:3],
        })
    latest = _build_daily_data(datetime.utcnow().strftime("%Y-%m-%d"), ticker_snapshot=ticker_snapshot)
    spotlight_order = ["funding", "microstructure", "cross_venue", "regime", "technical",
                       "sentiment", "news", "tokenomics", "narrative_debate",
                       "structural_debate", "risk_manager", "trader"]
    spotlight_key = spotlight_order[int(week_num) % len(spotlight_order)]
    latest.update({
        "week": week_key,
        "daily_rollup": days,
        "spotlight_key": spotlight_key,
        "weekly_move_patterns": [],
        "upcoming_events": latest.get("learner", {}).get("briefs", [])[:5],
    })
    return latest


def _report_cache_path(report_type: str, key: str) -> str:
    safe_key = re.sub(r"[^0-9A-Za-z_-]", "-", key)
    return os.path.join(REPORTS_DIR, f"{report_type}_{safe_key}.json")


def _load_or_build_report(report_type: str, key: str, force: bool = False) -> dict:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = _report_cache_path(report_type, key)
    if not force and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if "narrative_source" not in cached:
                data = cached.get("data") or {}
                weekly = cached.get("type") == "weekly"
                cached["narrative"] = _merge_report_narrative(
                    _build_deterministic_report_narrative(data, weekly=weekly),
                    cached.get("narrative") or {},
                )
                cached["narrative_source"] = "ai" if cached.get("ai_available") else "deterministic"
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(cached, f, indent=2, default=str)
                except Exception:
                    pass
            return cached
        except Exception:
            pass
    data = _build_weekly_data(key) if report_type == "weekly" else _build_daily_data(key)
    narrative, ai_available, narrative_source = _call_report_ai(data, weekly=(report_type == "weekly"))
    report = {
        "success": True,
        "type": report_type,
        "key": key,
        "date": data.get("date"),
        "week": data.get("week"),
        "data": data,
        "narrative": narrative,
        "ai_available": ai_available,
        "narrative_source": narrative_source,
        "generated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
    except Exception as e:
        print(f"[report_cache] {e}", file=sys.stderr)
    return report


@app.route('/api/intelligence/roster')
def api_intelligence_roster():
    """Return Cipher Research Group analyst roster and firm metadata."""
    try:
        from lib.agents import AGENT_ROSTER, FIRM_META
        return jsonify({
            'success': True,
            'firm': FIRM_META,
            'agents': AGENT_ROSTER,
        })
    except Exception as e:
        print(f'[api/intelligence/roster] error: {e}', file=sys.stderr)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/intelligence/reports/daily')
def api_intelligence_report_daily():
    """Generate or serve a cached Cipher daily brief."""
    key = request.args.get("date") or datetime.utcnow().strftime("%Y-%m-%d")
    try:
        return jsonify(_load_or_build_report("daily", key))
    except Exception as e:
        print(f"[api/intelligence/reports/daily] error: {e}", file=sys.stderr)
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/intelligence/reports/weekly')
def api_intelligence_report_weekly():
    """Generate or serve a cached Cipher weekly report."""
    iso = datetime.utcnow().isocalendar()
    key = request.args.get("week") or f"{iso.year}-W{iso.week:02d}"
    try:
        return jsonify(_load_or_build_report("weekly", key))
    except Exception as e:
        print(f"[api/intelligence/reports/weekly] error: {e}", file=sys.stderr)
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/intelligence/reports/regenerate', methods=['POST'])
def api_intelligence_report_regenerate():
    """Force a cached Cipher report to regenerate."""
    try:
        body = request.get_json(force=True) or {}
        report_type = body.get("type")
        key = body.get("key")
        if report_type not in ("daily", "weekly") or not key:
            return jsonify({"success": False, "error": "type and key required"}), 400
        return jsonify(_load_or_build_report(report_type, key, force=True))
    except Exception as e:
        print(f"[api/intelligence/reports/regenerate] error: {e}", file=sys.stderr)
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# P11 — Execution routes (Hyperliquid)
# ---------------------------------------------------------------------------

@app.route("/api/execution/status")
def api_execution_status():
    """Live trading status — positions, open orders, and whether live mode is on."""
    try:
        from lib.hl_execution import get_positions, get_open_orders
        wallet = HL_WALLET_ADDRESS.strip()
        keys_ok = bool(wallet and HL_PRIVATE_KEY)
        positions = get_positions(wallet)["positions"] if keys_ok else []
        orders    = get_open_orders(wallet)["orders"]  if keys_ok else []
        return jsonify({
            "success":             True,
            "live_trading":        LIVE_TRADING_ENABLED,
            "keys_configured":     keys_ok,
            "positions":           positions,
            "open_orders":         orders,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/execution/place", methods=["POST"])
def api_execution_place():
    """
    Place a limit order on Hyperliquid.
    Requires LIVE_TRADING_ENABLED=true in .env.
    Body: {coin, is_buy, size, limit_px, signal_id (optional)}
    All safety gates enforced here — signal age, key presence, live mode.
    """
    if not LIVE_TRADING_ENABLED:
        return jsonify({"success": False, "error": "Live trading is disabled. Set LIVE_TRADING_ENABLED=true in .env to enable."}), 403
    if not HL_PRIVATE_KEY or not HL_WALLET_ADDRESS:
        return jsonify({"success": False, "error": "HL_PRIVATE_KEY and HL_WALLET_ADDRESS must be set in .env"}), 400

    # AUDIT FIX §07 kill_switch_cooldown_001: after the kill switch fires,
    # refuse new orders for KILL_SWITCH_COOLDOWN_S seconds. The user (or an
    # external actor) hitting kill_switch then immediately POSTing /place
    # would otherwise undo the safety action they just took.
    _now = time.time()
    if _KILL_SWITCH_LAST_FIRED_TS and (_now - _KILL_SWITCH_LAST_FIRED_TS) < _KILL_SWITCH_COOLDOWN_S:
        remaining = int(_KILL_SWITCH_COOLDOWN_S - (_now - _KILL_SWITCH_LAST_FIRED_TS))
        return jsonify({
            "success": False,
            "error": f"Kill switch fired {int(_now - _KILL_SWITCH_LAST_FIRED_TS)}s ago — "
                     f"new orders refused for another {remaining}s",
        }), 423  # 423 = Locked

    # AUDIT FIX §07 max_daily_loss_hard_gate: MAX_DAILY_LOSS_USDT was
    # previously a warning shown in the readiness verdict, not a hard
    # block on /api/execution/place. The dashboard told the user "you've
    # hit your daily loss limit" but nothing prevented the next order.
    # Now: if MAX_DAILY_LOSS_USDT is set and today's realized loss exceeds
    # it, refuse execution outright.
    if MAX_DAILY_LOSS_USDT > 0:
        try:
            daily = compute_daily_pnl(DB_PATH)
            today_loss_usdt = float(daily.get("total_pnl_usdt") or 0.0)
            if today_loss_usdt < 0 and abs(today_loss_usdt) >= MAX_DAILY_LOSS_USDT:
                return jsonify({
                    "success": False,
                    "error": f"daily loss ${abs(today_loss_usdt):.2f} ≥ "
                             f"MAX_DAILY_LOSS_USDT ${MAX_DAILY_LOSS_USDT:.2f} — refusing execution",
                }), 403
        except Exception as e:
            # Fail closed: if we can't compute today's P&L we can't enforce
            # the gate, so we refuse rather than skip silently.
            return jsonify({
                "success": False,
                "error": f"daily-P&L lookup failed — refusing execution: {e}",
            }), 500

    try:
        from lib.hl_execution import place_limit_order
        body      = request.get_json(force=True) or {}
        coin      = str(body.get("coin", "")).strip().upper()
        is_buy    = bool(body.get("is_buy", True))
        size      = float(body.get("size", 0))
        limit_px  = float(body.get("limit_px", 0))
        signal_id = body.get("signal_id")

        if not coin or size <= 0 or limit_px <= 0:
            return jsonify({"success": False, "error": "coin, size, and limit_px are required"}), 400

        # Signal age gate — never execute on a stale signal.
        # AUDIT FIX §07 sig_age_bypass: the prior `except Exception: pass` here
        # let any timestamp parse error (e.g. a value with a timezone suffix
        # triggering TypeError on naive utcnow() subtraction) silently bypass
        # the 5-minute staleness check. Now we fail closed: every failure
        # path returns a 400 and refuses execution.
        if signal_id:
            con = None
            try:
                con = sqlite3.connect(DB_PATH)
                row = con.execute(
                    "SELECT entry_at, logged_at FROM signals WHERE id=?",
                    (signal_id,),
                ).fetchone()
            except Exception as e:
                return jsonify({
                    "success": False,
                    "error": f"signal lookup failed — refusing execution: {e}",
                }), 400
            finally:
                if con is not None:
                    try:
                        con.close()
                    except Exception:
                        pass

            if not row:
                return jsonify({
                    "success": False,
                    "error": "signal_id not found — refusing execution",
                }), 400
            ts_str = row[0] or row[1]
            if not ts_str:
                return jsonify({
                    "success": False,
                    "error": "signal has no entry_at or logged_at timestamp — refusing execution",
                }), 400
            try:
                # Strip any trailing 'Z' (UTC marker) and ensure naive UTC.
                # CLAUDE.md rule: all timestamps are UTC ISO without Z; we
                # tolerate Z just in case but never use a TZ-aware dt against
                # the naive utcnow() that produces the comparison.
                ts = datetime.fromisoformat(ts_str.replace("Z", ""))
                if ts.tzinfo is not None:
                    ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
            except Exception as e:
                return jsonify({
                    "success": False,
                    "error": f"could not parse signal timestamp {ts_str!r}: {e}",
                }), 400
            age_min = (datetime.utcnow() - ts).total_seconds() / 60
            if age_min > 5:
                return jsonify({
                    "success": False,
                    "error": f"Signal is {age_min:.0f}m old — must be < 5 minutes to execute",
                }), 400

        result = place_limit_order(
            coin=coin, is_buy=is_buy, size=size,
            limit_px=limit_px, private_key=HL_PRIVATE_KEY,
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/execution/kill-switch", methods=["POST"])
def api_execution_kill_switch():
    """
    Emergency kill switch — cancel all open orders and close all positions.
    Available regardless of LIVE_TRADING_ENABLED. Requires keys in .env.

    Audit §07 fix: sets _KILL_SWITCH_LAST_FIRED_TS so /api/execution/place
    refuses new orders for the next KILL_SWITCH_COOLDOWN_S seconds. Without
    this, an attacker (or panicking user) hitting kill-switch then immediately
    /place would undo the safety action.
    """
    if not HL_PRIVATE_KEY or not HL_WALLET_ADDRESS:
        return jsonify({"success": False, "error": "HL_PRIVATE_KEY and HL_WALLET_ADDRESS not configured"}), 400
    try:
        from lib.hl_execution import kill_switch
        # Set the cooldown BEFORE invoking — if the underlying call partially
        # succeeds and then raises, we still want the cooldown active.
        global _KILL_SWITCH_LAST_FIRED_TS
        _KILL_SWITCH_LAST_FIRED_TS = time.time()
        result = kill_switch(HL_WALLET_ADDRESS.strip(), HL_PRIVATE_KEY.strip())
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# AI Model Settings
# ---------------------------------------------------------------------------

@app.route("/api/ai/health")
def api_ai_health():
    """Return AI provider readiness without exposing API keys."""
    try:
        from lib.ai_client import load_ai_settings, provider_status
        settings = load_ai_settings()
        providers = provider_status()
        active = next((p for p in providers if p["provider"] == settings.get("provider")), None)
        any_hosted_or_local = any(p.get("available") for p in providers)
        return jsonify({
            "success": True,
            "current": settings,
            "providers": providers,
            "active_available": bool(active and active.get("available")),
            "any_provider_available": any_hosted_or_local,
            "report_narrative_mode": REPORT_NARRATIVE_MODE,
            "report_free_providers": sorted(REPORT_FREE_AI_PROVIDERS),
            "report_fallback": "deterministic",
            "note": (
                "Cipher reports always render deterministic desk notes for free. "
                "Hosted or local LLMs only polish the narrative when available."
            ),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/settings/ai")
def api_ai_settings_get():
    """Return available models (with key availability) and current selection."""
    try:
        from lib.ai_client import AVAILABLE_MODELS, load_ai_settings
        settings = load_ai_settings()
        models = [
            {**m, "available": bool(os.getenv(m["key_env"], ""))}
            for m in AVAILABLE_MODELS
        ]
        return jsonify({"success": True, "current": settings, "models": models})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/settings/ai", methods=["PATCH"])
def api_ai_settings_patch():
    """Update the active AI model. Body: {provider, model}."""
    try:
        from lib.ai_client import AVAILABLE_MODELS, save_ai_settings
        body = request.get_json(silent=True) or {}
        provider = body.get("provider", "").strip()
        model    = body.get("model", "").strip()
        valid = any(m["provider"] == provider and m["model"] == model for m in AVAILABLE_MODELS)
        if not valid:
            return jsonify({"success": False, "error": "unknown provider/model combination"}), 400
        settings = {"provider": provider, "model": model}
        save_ai_settings(settings)
        return jsonify({"success": True, "current": settings})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Paper bot — config, exit checker, scan loop
# ---------------------------------------------------------------------------

def _load_paper_config() -> dict:
    try:
        if os.path.exists(PAPER_CONFIG_PATH):
            with open(PAPER_CONFIG_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
                return {**_PAPER_CONFIG_DEFAULT, **cfg}
    except Exception:
        pass
    return dict(_PAPER_CONFIG_DEFAULT)


def _save_paper_config(cfg: dict) -> None:
    os.makedirs("data", exist_ok=True)
    with open(PAPER_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def _paper_check_exits() -> int:
    """
    Check all open paper trades against Min15 klines and close those that
    have hit a TP or stop level. Returns number of trades closed.
    Reuses evaluate_outcome() by mapping paper trade columns to the expected dict shape.
    """
    closed = 0
    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM paper_trades WHERE status='open' ORDER BY opened_at ASC"
        ).fetchall()
        con.close()

        for row in rows:
            pt = dict(row)
            # Build a signals-compatible dict for evaluate_outcome
            fake_sig = {
                "symbol":    pt["symbol"],
                "direction": pt["direction"],
                "logged_at": pt["opened_at"],
                "entry_at":  pt["opened_at"],   # already entered
                "entry1":    pt["entry_px"],
                "entry2":    pt["entry_px"],
                "entry3":    pt["entry_px"],
                "tp1":       pt["tp1"],
                "tp2":       pt["tp2"],
                "tp3":       pt["tp3"],
                "stop_loss": pt["stop_loss"],
                "exchange":  "MEXC",
            }
            # Min1 klines: catch 1-minute wicks hitting stop/TP, matching live
            # trading behaviour where stop orders execute on any price touch.
            # 1440 candles = 24 hours; sufficient since the loop runs every 60s
            # and the paper bot doesn't hold positions beyond a day by design.
            outcome = evaluate_outcome(fake_sig, interval="Min1", kline_limit=1440)
            if outcome is None:
                continue  # no level hit yet — position stays open

            result, note, exit_px, result_at, _ = outcome
            # Paper trades only close WIN/LOSS/PARTIAL — never EXPIRED or SKIPPED
            if result not in {"WIN", "LOSS", "PARTIAL"}:
                continue
            leverage = pt.get("leverage") or 1.0
            if exit_px and pt["entry_px"] and pt["entry_px"] != 0:
                raw_pnl = (
                    (exit_px - pt["entry_px"]) / pt["entry_px"] * 100
                    if pt["direction"] == "LONG"
                    else (pt["entry_px"] - exit_px) / pt["entry_px"] * 100
                )
                pnl_pct = round(raw_pnl * leverage, 2)
            else:
                pnl_pct = None

            closed_at = result_at or datetime.utcnow().isoformat()
            con = sqlite3.connect(DB_PATH)
            con.execute(
                """UPDATE paper_trades
                   SET status='closed', closed_at=?, exit_px=?, result=?, pnl_pct=?, note=?
                   WHERE id=?""",
                (closed_at, exit_px, result, pnl_pct, note, pt["id"]),
            )
            if pt.get("signal_id"):
                con.execute(
                    """UPDATE signals
                       SET result=?, exit_price=?, result_at=?, pnl_pct=?
                       WHERE id=? AND result IS NULL""",
                    (result, exit_px, closed_at, pnl_pct, pt["signal_id"]),
                )
            con.commit()
            con.close()
            closed += 1
            print(
                f"[paper_bot] closed {pt['symbol']} {pt['direction']} → {result} "
                f"pnl={pnl_pct}% exit={exit_px}",
                file=sys.stderr,
            )
    except Exception as e:
        print(f"[paper_bot] _paper_check_exits error: {e}", file=sys.stderr)
    return closed


def _paper_bot_scan(cfg: dict) -> dict:
    """
    Run one scan cycle for the paper bot across all enabled strategies.
    Fetches tickers once, runs each enabled strategy, deduplicates by symbol
    (highest conviction wins), then checks flow and enters paper positions.
    Returns a summary dict.
    """
    entered = 0
    rejected = 0
    skipped = 0

    try:
        disabled_strategies  = cfg.get("disabled_strategies") or []
        min_conviction       = int(cfg.get("min_conviction", 55))
        size_usd             = float(cfg.get("size_usd", 100))
        flow_required        = cfg.get("flow_required", True)
        min_flow_score       = float(cfg.get("min_flow_score", 50.0))
        max_open             = int(cfg.get("max_open_positions", 5))
        max_atr_pct          = float(cfg.get("max_atr_pct", 4.0))
        max_trend_score_abs  = int(cfg.get("max_trend_score_abs", 25))

        # Count current open positions
        con = sqlite3.connect(DB_PATH)
        open_count = con.execute(
            "SELECT COUNT(*) FROM paper_trades WHERE status='open'"
        ).fetchone()[0]
        open_symbols = {
            r[0] for r in con.execute(
                "SELECT symbol FROM paper_trades WHERE status='open'"
            ).fetchall()
        }
        con.close()

        if open_count >= max_open:
            print(f"[paper_bot] at max open positions ({max_open}), skipping scan", file=sys.stderr)
            return {"entered": 0, "rejected": 0, "skipped": 0, "reason": "max_positions"}

        tickers = fetch_mexc("/contract/ticker")
        if not tickers:
            return {"entered": 0, "rejected": 0, "skipped": 0, "reason": "no_tickers"}

        # Run all enabled strategies, dedup by symbol keeping highest conviction
        registry = get_strategy_registry()
        best: dict[str, dict] = {}  # symbol → best signal across all strategies
        for key in registry:
            if key in disabled_strategies:
                continue
            sigs, _ = run_scan(threshold=min_conviction, strategy_key=key, tickers=tickers)
            for sig in sigs:
                sym = sig["symbol"]
                conv = sig.get("conviction", sig.get("conviction_base", 0))
                if sym not in best or conv > best[sym].get("conviction", best[sym].get("conviction_base", 0)):
                    sig["_strategy_key"] = key
                    best[sym] = sig

        # Sort by conviction descending
        signals = sorted(best.values(), key=lambda s: s.get("conviction", s.get("conviction_base", 0)), reverse=True)

        now = datetime.utcnow().isoformat()

        for sig in signals:
            if open_count >= max_open:
                break
            if sig.get("conviction", sig.get("conviction_base", 0)) < min_conviction:
                skipped += 1
                continue
            if sig["symbol"] in open_symbols:
                skipped += 1
                continue
            # Data-driven gates: atr_pct and trend_score outpredict conviction
            # (analyze.py: winners 3.2% / 9.3 vs losers 5.5% / 14.1)
            if sig.get("atr_pct", 0) > max_atr_pct:
                print(f"[paper_bot] SKIP {sig['symbol']} atr_pct={sig.get('atr_pct'):.2f} > {max_atr_pct}", file=sys.stderr)
                skipped += 1
                continue
            if abs(sig.get("trend_score", 0)) > max_trend_score_abs:
                print(f"[paper_bot] SKIP {sig['symbol']} trend_score={sig.get('trend_score')} abs>{max_trend_score_abs}", file=sys.stderr)
                skipped += 1
                continue

            flow_result = None
            confirmed   = True
            flow_score  = 0.0
            flow_reasons_str = ""

            if flow_required:
                # Pass min_flow_score through — prior code loaded it from cfg
                # (line ~5938) into a local then never used it, so the value
                # in paper_config.json silently had no effect and every
                # dead-tape altcoin passed the gate at the hardcoded default
                # of 50. Audit §03 finding paper_flow_dead_001.
                flow_result   = _flow_confirm(
                    sig["symbol"],
                    sig["direction"],
                    sig.get("price", 0),
                    min_score=min_flow_score,
                )
                confirmed     = flow_result["confirmed"]
                flow_score    = flow_result["score"]
                flow_reasons_str = json.dumps(flow_result["reasons"])

            entry_px  = sig.get("price", 0)
            leverage  = float(sig.get("leverage_cap") or sig.get("leverage") or 1)
            conviction = sig.get("conviction", sig.get("conviction_base", 0))

            # Ladder levels: signals store as exits[] array, not flat tp1/tp2/tp3 keys
            exits = sig.get("exits") or []
            tp1 = exits[0] if len(exits) > 0 else sig.get("tp1")
            tp2 = exits[1] if len(exits) > 1 else sig.get("tp2")
            tp3 = exits[2] if len(exits) > 2 else sig.get("tp3")

            status = "open" if confirmed else "flow_rejected"

            # Log confirmed entries to signals table so they appear in History tab
            signal_id = sig.get("signal_id")
            if confirmed:
                sig["strategy_key"]   = sig.get("_strategy_key", "balanced")
                sig["flow_score"]     = flow_score
                sig["flow_confirmed"] = True
                log_signals([sig])
                try:
                    _sc  = sqlite3.connect(DB_PATH)
                    _row = _sc.execute(
                        "SELECT id FROM signals WHERE symbol=? AND direction=? AND strategy_key=? AND result IS NULL ORDER BY logged_at DESC LIMIT 1",
                        (sig["symbol"], sig["direction"], sig["strategy_key"]),
                    ).fetchone()
                    _sc.close()
                    if _row:
                        signal_id = _row[0]
                except Exception:
                    pass

            con = sqlite3.connect(DB_PATH)
            con.execute(
                """INSERT INTO paper_trades
                   (opened_at, symbol, strategy_key, direction, entry_px, size_usd,
                    tp1, tp2, tp3, stop_loss, leverage, conviction,
                    flow_confirmed, flow_score, flow_reasons, status, signal_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    now,
                    sig["symbol"],
                    sig.get("_strategy_key", "balanced"),
                    sig["direction"],
                    entry_px,
                    size_usd,
                    tp1,
                    tp2,
                    tp3,
                    sig.get("stop_loss"),
                    leverage,
                    conviction,
                    1 if confirmed else 0,
                    flow_score,
                    flow_reasons_str,
                    status,
                    signal_id,
                ),
            )
            con.commit()
            con.close()

            if confirmed:
                entered += 1
                open_count += 1
                open_symbols.add(sig["symbol"])
                print(
                    f"[paper_bot] ENTER {sig['symbol']} {sig['direction']} "
                    f"@ {entry_px} flow_score={flow_score:.0f}",
                    file=sys.stderr,
                )
            else:
                rejected += 1
                print(
                    f"[paper_bot] REJECTED {sig['symbol']} {sig['direction']} "
                    f"flow_score={flow_score:.0f}",
                    file=sys.stderr,
                )

    except Exception as e:
        print(f"[paper_bot] _paper_bot_scan error: {e}", file=sys.stderr)

    return {"entered": entered, "rejected": rejected, "skipped": skipped}


def _paper_bot_loop():
    """Background daemon: exits checked every 60s; new entries on configured interval."""
    import time as _time
    _time.sleep(120)  # 2-minute startup delay
    last_scan = 0.0
    while True:
        try:
            cfg = _load_paper_config()
            if cfg.get("enabled"):
                # Always check exits — positions must close the moment a level is hit
                closed = _paper_check_exits()
                if closed:
                    print(f"[paper_bot] closed {closed} position(s)", file=sys.stderr)

                # Only scan for new entries on the configured interval
                scan_interval = int(cfg.get("scan_interval_minutes", 5)) * 60
                if _time.time() - last_scan >= scan_interval:
                    scan_result = _paper_bot_scan(cfg)
                    last_scan = _time.time()
                    print(
                        f"[paper_bot] scan done — entered={scan_result['entered']} "
                        f"rejected={scan_result['rejected']}",
                        file=sys.stderr,
                    )
        except Exception as e:
            print(f"[paper_bot] loop error: {e}", file=sys.stderr)
        _time.sleep(60)  # check exits every 60s; new entries gated by scan_interval


# ---------------------------------------------------------------------------
# Paper bot API routes
# ---------------------------------------------------------------------------

@app.route("/api/paper/trades")
def api_paper_trades():
    """Return all paper trades, optionally filtered by status."""
    try:
        status = request.args.get("status", "")
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        if status:
            rows = con.execute(
                "SELECT * FROM paper_trades WHERE status=? ORDER BY opened_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM paper_trades ORDER BY opened_at DESC LIMIT 200"
            ).fetchall()
        con.close()
        return jsonify({"success": True, "trades": [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/paper/filter-stats")
def api_paper_filter_stats():
    """
    Live winner/loser averages for ATR% and trend_score from closed signals.
    Used to annotate the paper trader config panel with real DB-derived hints.
    Requires at least 10 winners and 10 losers to return meaningful data.
    """
    try:
        con = sqlite3.connect(DB_PATH)
        rows = con.execute(
            "SELECT atr_pct, trend_score, result FROM signals "
            "WHERE result IN ('WIN','LOSS','PARTIAL') "
            "AND atr_pct IS NOT NULL AND trend_score IS NOT NULL"
        ).fetchall()
        con.close()

        winners = [(r[0], abs(r[1])) for r in rows if r[2] in ("WIN", "PARTIAL")]
        losers  = [(r[0], abs(r[1])) for r in rows if r[2] == "LOSS"]

        def _avg(vals):
            return round(sum(vals) / len(vals), 2) if vals else None

        w_atr   = _avg([r[0] for r in winners])
        l_atr   = _avg([r[0] for r in losers])
        w_trend = _avg([r[1] for r in winners])
        l_trend = _avg([r[1] for r in losers])

        return jsonify({
            "success": True,
            "sample": {"winners": len(winners), "losers": len(losers)},
            "atr_pct":     {"winners_avg": w_atr,   "losers_avg": l_atr},
            "trend_score": {"winners_avg": w_trend, "losers_avg": l_trend},
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/paper/stats")
def api_paper_stats():
    """Aggregate stats for paper trading: win rate, P&L, flow-confirmed vs rejected comparison."""
    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row

        closed = [dict(r) for r in con.execute(
            "SELECT * FROM paper_trades WHERE status='closed'"
        ).fetchall()]
        open_trades = [dict(r) for r in con.execute(
            "SELECT * FROM paper_trades WHERE status='open'"
        ).fetchall()]
        rejected = con.execute(
            "SELECT COUNT(*) FROM paper_trades WHERE status='flow_rejected'"
        ).fetchone()[0]
        con.close()

        def _stats(trades):
            wins  = [t for t in trades if t.get("result") in ("WIN", "TP3")]
            pnls  = [t["pnl_pct"] for t in trades if t.get("pnl_pct") is not None]
            return {
                "count":      len(trades),
                "win_rate":   round(len(wins) / len(trades) * 100, 1) if trades else 0,
                "avg_pnl":    round(sum(pnls) / len(pnls), 2) if pnls else 0,
                "total_pnl":  round(sum(pnls), 2) if pnls else 0,
            }

        confirmed   = [t for t in closed if t.get("flow_confirmed")]
        unconfirmed = [t for t in closed if not t.get("flow_confirmed")]

        # Equity curve: cumulative P&L over closed trades sorted by closed_at
        sorted_closed = sorted(closed, key=lambda t: t.get("closed_at") or "")
        equity = []
        running = 0.0
        for t in sorted_closed:
            if t.get("pnl_pct") is not None:
                running += t["pnl_pct"]
                equity.append({"closed_at": t["closed_at"], "cumulative_pnl": round(running, 2)})

        return jsonify({
            "success":       True,
            "open_count":    len(open_trades),
            "rejected_count": rejected,
            "all_closed":    _stats(closed),
            "flow_confirmed":   _stats(confirmed),
            "flow_unconfirmed": _stats(unconfirmed),
            "equity_curve":  equity,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def _compute_effective_paper_thresholds(cfg: dict) -> dict:
    """
    Surface the min_conviction override chain so the UI can show the user
    why their setting is or isn't actually applied.

    The paper bot calls run_scan() which does:
        effective_threshold = max(threshold, strat["min_conviction"])
    where strat["min_conviction"] is the built-in default merged with any
    active learner override from strategy_overrides.json.

    If the user sets paper_config.min_conviction = 55 but Balanced has a
    learner override of 65, the paper bot floors to 65 silently. Audit
    §03 paper_override_silent flagged this as the largest user-facing
    confusion in the paper subsystem.

    Returns: {strategy_key: {user_floor, strategy_floor, override_floor,
                             effective_floor, floored_by}}
    """
    user_floor = int(cfg.get("min_conviction", 65))
    try:
        overrides = _load_strategy_overrides() or {}
    except Exception:
        overrides = {}
    try:
        registry = get_strategy_registry() or {}
    except Exception:
        registry = {}

    out: dict[str, dict] = {}
    for key, strat in registry.items():
        base_floor = int(strat.get("min_conviction", 0) or 0)
        override_floor = base_floor
        if key in overrides and "min_conviction" in overrides[key]:
            try:
                override_floor = int(overrides[key]["min_conviction"])
            except (TypeError, ValueError):
                pass
        effective_floor = max(user_floor, override_floor)
        if user_floor >= override_floor:
            floored_by = "user"
        else:
            floored_by = "strategy_override" if override_floor > base_floor else "strategy_default"
        out[key] = {
            "user_floor":      user_floor,
            "strategy_floor":  base_floor,
            "override_floor":  override_floor,
            "effective_floor": effective_floor,
            "floored_by":      floored_by,
        }
    return out


@app.route("/api/paper/config", methods=["GET", "PATCH"])
def api_paper_config():
    """GET current paper bot config. PATCH to update fields."""
    try:
        if request.method == "GET":
            cfg = _load_paper_config()
            return jsonify({
                "success": True,
                "config": cfg,
                # New: surface the silent override chain so the Paper UI can
                # render "you set 55 but Balanced floors to 65 by learner
                # override" instead of pretending the user setting won.
                "effective_thresholds": _compute_effective_paper_thresholds(cfg),
            })

        body = request.get_json(force=True) or {}
        cfg  = _load_paper_config()
        allowed = {
            "enabled", "size_usd", "disabled_strategies", "min_conviction",
            "flow_required", "min_flow_score", "scan_interval_minutes", "max_open_positions",
            "max_atr_pct", "max_trend_score_abs",
        }
        for k, v in body.items():
            if k in allowed:
                cfg[k] = v
        _save_paper_config(cfg)
        return jsonify({
            "success": True,
            "config": cfg,
            "effective_thresholds": _compute_effective_paper_thresholds(cfg),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/flow/<symbol>")
def api_order_flow(symbol: str):
    """
    Live order flow analysis for a single symbol.
    Returns delta, depth imbalance, walls, and flow confirmation result.
    Query params: direction (LONG|SHORT), trade_limit (default 500), depth_levels (default 20)
    """
    try:
        direction    = request.args.get("direction", "LONG").upper()
        trade_limit  = int(request.args.get("trade_limit", 500))
        depth_levels = int(request.args.get("depth_levels", 20))
        result = _flow_confirm(
            symbol.upper(),
            direction,
            price=0,
            trade_limit=trade_limit,
            depth_levels=depth_levels,
        )
        return jsonify({"success": True, **result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/paper/reset", methods=["POST"])
def api_paper_reset():
    """Clear all paper trades (maintenance). Requires ?confirm=yes."""
    if request.args.get("confirm") != "yes":
        return jsonify({"success": False, "error": "Pass ?confirm=yes to reset paper trades"}), 400
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute("DELETE FROM paper_trades")
        con.commit()
        con.close()
        return jsonify({"success": True, "message": "All paper trades cleared"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


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


def _snapshot_loop():
    """Hourly background job: snapshot all MEXC tickers + BTC/ETH market context."""
    import time as _time
    _time.sleep(180)  # wait 3 minutes after startup before first snapshot
    while True:
        try:
            n = _snapshot_tickers()
            ok = _snapshot_market_context()
            print(f"[snapshot_loop] tickers={n} ctx={'ok' if ok else 'err'}", file=sys.stderr)
        except Exception as e:
            print(f"[snapshot_loop] error: {e}", file=sys.stderr)
        _time.sleep(3600)  # every hour

_snapshot_thread = threading.Thread(target=_snapshot_loop, daemon=True)


def _coach_review_loop():
    """
    Background job: generate coach reviews for closed trades that don't have one yet.
    Processes 5 trades per run, every 10 minutes. Uses the shared _generate_coach_review()
    helper so reviews are identical to on-demand ones (Research Firm context included).
    Skips EXPIRED and SKIPPED outcomes — no useful journey data there.
    """
    import time as _time
    _time.sleep(300)  # wait 5 minutes after startup
    while True:
        try:
            con = sqlite3.connect(DB_PATH)
            con.row_factory = sqlite3.Row
            rows = con.execute("""
                SELECT * FROM signals
                WHERE result IS NOT NULL
                  AND result NOT IN ('EXPIRED', 'SKIPPED')
                  AND (
                      signal_json IS NULL
                      OR json_extract(signal_json, '$.coach_review') IS NULL
                  )
                ORDER BY logged_at DESC
                LIMIT 5
            """).fetchall()
            con.close()

            if not rows:
                _time.sleep(600)
                continue

            generated = 0
            for row in rows:
                try:
                    sig = dict(row)
                    signal_id = sig["id"]
                    sig_json_obj = {}
                    try:
                        sig_json_obj = json.loads(sig.get("signal_json") or "{}")
                    except Exception:
                        pass

                    pnl_pct = sig.get("pnl_pct")
                    entry1  = sig.get("entry1")
                    exit_px = sig.get("exit_price")
                    if pnl_pct is None and entry1 and exit_px and entry1 != 0:
                        direction = sig.get("direction", "")
                        pnl_pct = round(
                            ((exit_px - entry1) / entry1 * 100) if direction == "LONG"
                            else ((entry1 - exit_px) / entry1 * 100),
                            2,
                        )

                    duration_minutes = None
                    if sig.get("logged_at") and sig.get("result_at"):
                        try:
                            t0 = datetime.fromisoformat(sig["logged_at"])
                            t1 = datetime.fromisoformat(sig["result_at"])
                            duration_minutes = int((t1 - t0).total_seconds() / 60)
                        except Exception:
                            pass

                    tags_raw     = sig.get("tags") or ""
                    journey      = compute_trade_journey(sig, pnl_pct)
                    journey_prompt = format_journey_for_prompt(journey)

                    review = _generate_coach_review(
                        sig, signal_id, sig_json_obj, journey, journey_prompt,
                        tags_raw, entry1, exit_px, duration_minutes,
                    )
                    if review:
                        generated += 1
                        print(f"[coach_review_loop] generated for signal {signal_id} ({sig.get('symbol')})", file=sys.stderr)

                    _time.sleep(4)  # brief pause between calls — respect Groq rate limits
                except Exception as _e:
                    print(f"[coach_review_loop] signal {sig.get('id')} error: {_e}", file=sys.stderr)

            print(f"[coach_review_loop] batch done — generated={generated}", file=sys.stderr)
        except Exception as e:
            print(f"[coach_review_loop] error: {e}", file=sys.stderr)
        _time.sleep(600)  # 10 minutes between batches

_coach_review_thread = threading.Thread(target=_coach_review_loop, daemon=True)
_paper_bot_thread = threading.Thread(target=_paper_bot_loop, daemon=True)


# All four background threads (_outcome, _snapshot, _coach_review, _paper_bot)
# are constructed at module level so they're attribute-accessible, but their
# .start() calls now live inside the __main__ guard. Previously they fired at
# import time, which meant any offline script (backtest.py, the reconstruct
# harness, ad-hoc REPL exploration) would silently spawn four background
# workers that hit MEXC and the SQLite DB. CLAUDE.md flags this as a hard rule
# ("Do not import from app.py in a way that triggers Flask server startup");
# the audit (§05) flagged backtest.py's `from app import score_ticker` as a
# concrete violation. Moving .start() under __main__ closes that loophole
# without extracting score_ticker into a separate module.
if __name__ == "__main__":
    _outcome_thread.start()
    _snapshot_thread.start()
    _coach_review_thread.start()
    _paper_bot_thread.start()

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
