from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "edge_lab.db"


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
    con.commit()


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
            utc_now(),
        )
        for r in rows
    ]
    con.executemany("""
        INSERT INTO candle_labels(symbol, exchange, timeframe, timestamp, features_json, paths_json, generated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, exchange, timeframe, timestamp) DO UPDATE SET
            features_json=excluded.features_json,
            paths_json=excluded.paths_json,
            generated_at=excluded.generated_at
    """, payload)
    return len(payload)

