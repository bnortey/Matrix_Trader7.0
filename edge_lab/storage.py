from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "edge_lab.db"
SCHEMA_VERSION = "edge_lab_schema_v2"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init_storage(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS candle_labels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            exchange TEXT NOT NULL DEFAULT 'MEXC',
            timeframe TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            features_json TEXT NOT NULL,
            paths_json TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            UNIQUE(symbol, exchange, timeframe, timestamp)
        )
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_candle_labels_symbol_time
        ON candle_labels(symbol, exchange, timeframe, timestamp)
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_candle_labels_timeframe
        ON candle_labels(timeframe)
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_candle_labels_timestamp
        ON candle_labels(timestamp)
    """)
    _ensure_column(con, "candle_labels", "feature_version", "TEXT")
    _ensure_column(con, "candle_labels", "label_version", "TEXT")
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_candle_labels_versions_id
        ON candle_labels(label_version, feature_version, id)
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS candle_feature_snapshots (
            symbol TEXT NOT NULL,
            exchange TEXT NOT NULL DEFAULT 'MEXC',
            timeframe TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            features_json TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            PRIMARY KEY(symbol, exchange, timeframe, timestamp)
        )
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_cfs_symbol_timeframe_ts
        ON candle_feature_snapshots(symbol, exchange, timeframe, timestamp)
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_cfs_timestamp
        ON candle_feature_snapshots(timestamp)
    """)
    _ensure_column(con, "candle_feature_snapshots", "feature_version", "TEXT")
    con.execute("""
        CREATE TABLE IF NOT EXISTS edge_lab_symbol_status (
            symbol TEXT NOT NULL,
            exchange TEXT NOT NULL DEFAULT 'MEXC',
            timeframe TEXT NOT NULL DEFAULT 'Min15',
            status TEXT NOT NULL,
            failure_reason TEXT,
            failure_count INTEGER DEFAULT 0,
            candles_fetched INTEGER DEFAULT 0,
            rows_labeled INTEGER DEFAULT 0,
            last_labeled_timestamp INTEGER,
            last_started_at TEXT,
            last_completed_at TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(symbol, exchange, timeframe)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS edge_lab_build_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            generated_at TEXT NOT NULL,
            mode TEXT NOT NULL,
            feature_version TEXT,
            label_version TEXT,
            schema_version TEXT NOT NULL,
            summary_json TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS edge_lab_schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    con.execute("""
        INSERT INTO edge_lab_schema_meta(key, value, updated_at)
        VALUES ('schema_version', ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value=excluded.value,
            updated_at=excluded.updated_at
    """, (SCHEMA_VERSION, utc_now()))
    con.commit()


def _ensure_column(
    con: sqlite3.Connection,
    table: str,
    column: str,
    declaration: str,
) -> None:
    columns = {row[1] for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def upsert_pending(con: sqlite3.Connection, symbol: str, timeframe: str) -> None:
    now = utc_now()
    con.execute("""
        INSERT INTO edge_lab_symbol_status(symbol, exchange, timeframe, status, updated_at)
        VALUES (?, 'MEXC', ?, 'pending', ?)
        ON CONFLICT(symbol, exchange, timeframe) DO NOTHING
    """, (symbol, timeframe, now))


def mark_running(con: sqlite3.Connection, symbol: str, timeframe: str) -> None:
    now = utc_now()
    con.execute("""
        INSERT INTO edge_lab_symbol_status(symbol, exchange, timeframe, status, last_started_at, updated_at)
        VALUES (?, 'MEXC', ?, 'running', ?, ?)
        ON CONFLICT(symbol, exchange, timeframe) DO UPDATE SET
            status='running',
            failure_reason=NULL,
            last_started_at=excluded.last_started_at,
            updated_at=excluded.updated_at
    """, (symbol, timeframe, now, now))


def mark_complete(
    con: sqlite3.Connection,
    symbol: str,
    timeframe: str,
    candles_fetched: int,
    rows_labeled: int,
    last_labeled_timestamp: int | None,
) -> None:
    now = utc_now()
    con.execute("""
        INSERT INTO edge_lab_symbol_status(
            symbol, exchange, timeframe, status, failure_reason, candles_fetched,
            rows_labeled, last_labeled_timestamp, last_completed_at, updated_at
        )
        VALUES (?, 'MEXC', ?, 'complete', NULL, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, exchange, timeframe) DO UPDATE SET
            status='complete',
            failure_reason=NULL,
            candles_fetched=excluded.candles_fetched,
            rows_labeled=excluded.rows_labeled,
            last_labeled_timestamp=excluded.last_labeled_timestamp,
            last_completed_at=excluded.last_completed_at,
            updated_at=excluded.updated_at
    """, (symbol, timeframe, candles_fetched, rows_labeled, last_labeled_timestamp, now, now))


def mark_skipped(con: sqlite3.Connection, symbol: str, timeframe: str, reason: str, candles_fetched: int = 0) -> None:
    now = utc_now()
    con.execute("""
        INSERT INTO edge_lab_symbol_status(symbol, exchange, timeframe, status, failure_reason, candles_fetched, updated_at)
        VALUES (?, 'MEXC', ?, 'skipped', ?, ?, ?)
        ON CONFLICT(symbol, exchange, timeframe) DO UPDATE SET
            status='skipped',
            failure_reason=excluded.failure_reason,
            candles_fetched=excluded.candles_fetched,
            updated_at=excluded.updated_at
    """, (symbol, timeframe, reason, candles_fetched, now))


def mark_failed(con: sqlite3.Connection, symbol: str, timeframe: str, reason: str) -> None:
    now = utc_now()
    con.execute("""
        INSERT INTO edge_lab_symbol_status(symbol, exchange, timeframe, status, failure_reason, failure_count, updated_at)
        VALUES (?, 'MEXC', ?, 'failed', ?, 1, ?)
        ON CONFLICT(symbol, exchange, timeframe) DO UPDATE SET
            status='failed',
            failure_reason=excluded.failure_reason,
            failure_count=COALESCE(edge_lab_symbol_status.failure_count, 0) + 1,
            updated_at=excluded.updated_at
    """, (symbol, timeframe, reason[:500], now))


def get_last_labeled_timestamp(con: sqlite3.Connection, symbol: str, timeframe: str) -> int | None:
    row = con.execute("""
        SELECT COALESCE(
            (SELECT last_labeled_timestamp FROM edge_lab_symbol_status
             WHERE symbol=? AND exchange='MEXC' AND timeframe=?),
            (SELECT MAX(timestamp) FROM candle_labels
             WHERE symbol=? AND exchange='MEXC' AND timeframe=?)
        )
    """, (symbol, timeframe, symbol, timeframe)).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def get_last_feature_timestamp(con: sqlite3.Connection, symbol: str, timeframe: str) -> int | None:
    row = con.execute("""
        SELECT MAX(timestamp)
        FROM candle_feature_snapshots
        WHERE symbol=? AND exchange='MEXC' AND timeframe=?
    """, (symbol, timeframe)).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def get_status_counts(con: sqlite3.Connection, timeframe: str) -> dict:
    rows = con.execute("""
        SELECT status, COUNT(*) FROM edge_lab_symbol_status
        WHERE exchange='MEXC' AND timeframe=?
        GROUP BY status
    """, (timeframe,)).fetchall()
    return {status: count for status, count in rows}


def insert_labels(con: sqlite3.Connection, rows: list[dict]) -> int:
    if not rows:
        return 0
    payload = [
        (
            r["symbol"],
            "MEXC",
            r["timeframe"],
            int(r["timestamp"]),
            json.dumps(r["features"], separators=(",", ":"), sort_keys=True),
            json.dumps(r["paths"], separators=(",", ":"), sort_keys=True),
            str(r["features"].get("feature_version") or ""),
            str(next(iter(r["paths"].values()), {}).get("label_version") or ""),
            utc_now(),
        )
        for r in rows
    ]
    con.executemany("""
        INSERT INTO candle_labels(
            symbol, exchange, timeframe, timestamp, features_json, paths_json,
            feature_version, label_version, generated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, exchange, timeframe, timestamp) DO UPDATE SET
            features_json=excluded.features_json,
            paths_json=excluded.paths_json,
            feature_version=excluded.feature_version,
            label_version=excluded.label_version,
            generated_at=excluded.generated_at
    """, payload)
    return len(payload)


def upsert_feature_snapshots(con: sqlite3.Connection, rows: list[dict]) -> int:
    """Persist features known at candle close, independent of future path labels."""
    if not rows:
        return 0
    generated_at = utc_now()
    payload = [
        (
            r["symbol"],
            "MEXC",
            r["timeframe"],
            int(r["timestamp"]),
            json.dumps(r["features"], separators=(",", ":"), sort_keys=True),
            str(r["features"].get("feature_version") or ""),
            generated_at,
        )
        for r in rows
    ]
    con.executemany("""
        INSERT INTO candle_feature_snapshots(
            symbol, exchange, timeframe, timestamp, features_json,
            feature_version, generated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, exchange, timeframe, timestamp) DO UPDATE SET
            features_json=excluded.features_json,
            feature_version=excluded.feature_version,
            generated_at=excluded.generated_at
    """, payload)
    return len(payload)


def record_build_run(con: sqlite3.Connection, summary: dict) -> None:
    con.execute("""
        INSERT INTO edge_lab_build_runs(
            generated_at, mode, feature_version, label_version,
            schema_version, summary_json
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        str(summary.get("generated_at") or utc_now()),
        str(summary.get("mode") or "unknown"),
        str(summary.get("feature_version") or ""),
        str(summary.get("label_version") or ""),
        SCHEMA_VERSION,
        json.dumps(summary, separators=(",", ":"), sort_keys=True, default=str),
    ))
