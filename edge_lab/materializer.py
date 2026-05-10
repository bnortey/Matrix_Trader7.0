"""
Materializer for Edge Lab candle_features table.

Reads candle_labels (with JSON blobs) and writes flat columns to
candle_features so factor queries run in <5s instead of 270-360s.

Do NOT import app.py or backtest.py. Do NOT write to signals.db.
Pure Python + sqlite3 only. No pandas.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

# Template → short column prefix
_TEMPLATE_PREFIX = {
    "TP0_5_SL0_5":  "t05",
    "TP1_0_SL0_5":  "t10",
    "TP1_5_SL0_75": "t15",
    "TP2_0_SL1_0":  "t20",
}
_TEMPLATES = list(_TEMPLATE_PREFIX)
_SIDES = ["long", "short"]
_SIDE_COL = {"long": "l", "short": "s"}

_TAG_COLS = {
    "compressed":    "tag_compressed",
    "expanded":      "tag_expanded",
    "bullish_trend": "tag_bullish_trend",
    "bearish_trend": "tag_bearish_trend",
    "extreme_vol":   "tag_extreme_vol",
    "low_vol":       "tag_low_vol",
}

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS candle_features (
    candle_id    INTEGER PRIMARY KEY,
    symbol       TEXT,
    timeframe    TEXT,
    timestamp    INTEGER,

    volatility_regime  TEXT,
    trend_state        TEXT,
    compression_state  TEXT,
    rsi_decile         INTEGER,
    volume_decile      INTEGER,
    atr_decile         INTEGER,
    stddev_decile      INTEGER,

    tag_compressed    INTEGER,
    tag_expanded      INTEGER,
    tag_bullish_trend INTEGER,
    tag_bearish_trend INTEGER,
    tag_extreme_vol   INTEGER,
    tag_low_vol       INTEGER,

    -- TP0_5_SL0_5
    t05_l_tp INTEGER, t05_l_sl INTEGER, t05_l_mfe REAL, t05_l_mae REAL, t05_l_ttp REAL,
    t05_s_tp INTEGER, t05_s_sl INTEGER, t05_s_mfe REAL, t05_s_mae REAL, t05_s_ttp REAL,

    -- TP1_0_SL0_5
    t10_l_tp INTEGER, t10_l_sl INTEGER, t10_l_mfe REAL, t10_l_mae REAL, t10_l_ttp REAL,
    t10_s_tp INTEGER, t10_s_sl INTEGER, t10_s_mfe REAL, t10_s_mae REAL, t10_s_ttp REAL,

    -- TP1_5_SL0_75
    t15_l_tp INTEGER, t15_l_sl INTEGER, t15_l_mfe REAL, t15_l_mae REAL, t15_l_ttp REAL,
    t15_s_tp INTEGER, t15_s_sl INTEGER, t15_s_mfe REAL, t15_s_mae REAL, t15_s_ttp REAL,

    -- TP2_0_SL1_0
    t20_l_tp INTEGER, t20_l_sl INTEGER, t20_l_mfe REAL, t20_l_mae REAL, t20_l_ttp REAL,
    t20_s_tp INTEGER, t20_s_sl INTEGER, t20_s_mfe REAL, t20_s_mae REAL, t20_s_ttp REAL
)
"""

_INSERT_SQL = """
INSERT OR IGNORE INTO candle_features VALUES (
    ?,?,?,?,
    ?,?,?,?,?,?,?,
    ?,?,?,?,?,?,
    ?,?,?,?,?,
    ?,?,?,?,?,
    ?,?,?,?,?,
    ?,?,?,?,?,
    ?,?,?,?,?,
    ?,?,?,?,?,
    ?,?,?,?,?,
    ?,?,?,?,?
)
"""

_INDEXES = [
    ("idx_cf_vol_regime",  "CREATE INDEX IF NOT EXISTS idx_cf_vol_regime ON candle_features(volatility_regime)"),
    ("idx_cf_trend",       "CREATE INDEX IF NOT EXISTS idx_cf_trend ON candle_features(trend_state)"),
    ("idx_cf_compression", "CREATE INDEX IF NOT EXISTS idx_cf_compression ON candle_features(compression_state)"),
    ("idx_cf_rsi",         "CREATE INDEX IF NOT EXISTS idx_cf_rsi ON candle_features(rsi_decile)"),
    ("idx_cf_volume",      "CREATE INDEX IF NOT EXISTS idx_cf_volume ON candle_features(volume_decile)"),
    ("idx_cf_atr",         "CREATE INDEX IF NOT EXISTS idx_cf_atr ON candle_features(atr_decile)"),
    ("idx_cf_regime_trend","CREATE INDEX IF NOT EXISTS idx_cf_regime_trend ON candle_features(volatility_regime, trend_state)"),
    ("idx_cf_tags",        "CREATE INDEX IF NOT EXISTS idx_cf_tags ON candle_features(tag_compressed, tag_expanded, tag_bullish_trend, tag_bearish_trend, tag_extreme_vol, tag_low_vol)"),
]

_PROGRESS_INTERVAL = 500_000


def _parse_json(s: str | None) -> dict:
    if not s:
        return {}
    try:
        return json.loads(s)
    except Exception:
        return {}


def _bool_to_int(v) -> int | None:
    if v is True:
        return 1
    if v is False:
        return 0
    return None


def _int_or_none(v) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _float_or_none(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _extract_row(row: tuple) -> tuple:
    """Convert one candle_labels row into a candle_features INSERT tuple."""
    candle_id, symbol, timeframe, timestamp, features_str, paths_str = row

    features = _parse_json(features_str)
    paths    = _parse_json(paths_str)

    # Tags are stored as a JSON array string inside features_json
    tags_raw = features.get("tags") or []
    if isinstance(tags_raw, str):
        tags_raw = _parse_json(tags_raw)
    tags = set(tags_raw) if isinstance(tags_raw, list) else set()

    values = [
        candle_id,
        symbol,
        timeframe,
        timestamp,
        # Feature columns
        features.get("volatility_regime"),
        features.get("trend_state"),
        features.get("compression_state"),
        _int_or_none(features.get("rsi_15m_decile")),
        _int_or_none(features.get("volume_decile")),
        _int_or_none(features.get("atr_pct_15m_decile")),
        _int_or_none(features.get("stddev_decile")),
        # Tag flag columns
        1 if "compressed"    in tags else 0,
        1 if "expanded"      in tags else 0,
        1 if "bullish_trend" in tags else 0,
        1 if "bearish_trend" in tags else 0,
        1 if "extreme_vol"   in tags else 0,
        1 if "low_vol"       in tags else 0,
    ]

    # Path outcome columns — 4 templates × 2 sides × 5 fields = 40
    for tmpl in _TEMPLATES:
        tmpl_paths = paths.get(tmpl) or {}
        for side in _SIDES:
            values += [
                _bool_to_int(tmpl_paths.get(f"{side}_tp_hit_first")),
                _bool_to_int(tmpl_paths.get(f"{side}_sl_hit_first")),
                _float_or_none(tmpl_paths.get(f"{side}_mfe_pct")),
                _float_or_none(tmpl_paths.get(f"{side}_mae_pct")),
                _float_or_none(tmpl_paths.get(f"{side}_time_to_tp_minutes")),
            ]

    return tuple(values)


def materialize(db_path: Path, batch_size: int = 10_000) -> dict:
    """
    Read candle_labels in batches, write flat columns to candle_features.
    INSERT OR IGNORE — idempotent, safe to resume.
    Returns summary dict.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"DB not found: {db_path}")

    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA cache_size=-64000")  # 64 MB page cache

    # Create table
    con.execute(_CREATE_TABLE)
    con.commit()

    total_source = con.execute("SELECT COUNT(*) FROM candle_labels").fetchone()[0]

    # Resume: start after highest already-materialized candle_id
    resume_row = con.execute("SELECT MAX(candle_id) FROM candle_features").fetchone()
    last_id = resume_row[0] if resume_row[0] is not None else -1
    already_done = con.execute(
        "SELECT COUNT(*) FROM candle_features WHERE candle_id <= ?", (last_id,)
    ).fetchone()[0] if last_id >= 0 else 0

    print(f"[materializer] DB: {db_path}", file=sys.stderr)
    print(f"[materializer] candle_labels rows: {total_source:,}", file=sys.stderr)
    if last_id >= 0:
        print(f"[materializer] Resuming after candle_id={last_id} ({already_done:,} already done)", file=sys.stderr)

    t_start = time.time()
    rows_read = 0
    rows_inserted = 0
    rows_skipped = 0
    last_progress_print = 0

    while True:
        batch = con.execute(
            "SELECT id, symbol, timeframe, timestamp, features_json, paths_json "
            "FROM candle_labels WHERE id > ? ORDER BY id ASC LIMIT ?",
            (last_id, batch_size),
        ).fetchall()

        if not batch:
            break

        insert_batch = []
        for raw_row in batch:
            try:
                insert_batch.append(_extract_row(raw_row))
            except Exception as e:
                print(f"[materializer] WARN: skipping candle_id={raw_row[0]}: {e}", file=sys.stderr)
                rows_skipped += 1

        if insert_batch:
            con.executemany(_INSERT_SQL, insert_batch)
            con.commit()
            rows_inserted += len(insert_batch)

        rows_read += len(batch)
        last_id = batch[-1][0]

        # Progress report every 500k rows
        milestone = (rows_read // _PROGRESS_INTERVAL) * _PROGRESS_INTERVAL
        if milestone > last_progress_print and rows_read >= _PROGRESS_INTERVAL:
            pct = rows_read / total_source * 100 if total_source else 0
            elapsed = time.time() - t_start
            rate = rows_read / elapsed if elapsed > 0 else 0
            print(
                f"[materializer] {rows_read:>9,} / {total_source:,} rows "
                f"({pct:.1f}%)  {rate:,.0f} rows/s",
                file=sys.stderr,
            )
            last_progress_print = milestone

    # Build indexes
    print(f"[materializer] Building {len(_INDEXES)} indexes ...", file=sys.stderr)
    for name, ddl in _INDEXES:
        t0 = time.time()
        con.execute(ddl)
        con.commit()
        print(f"[materializer]   {name}: {time.time()-t0:.1f}s", file=sys.stderr)

    con.close()
    runtime = round(time.time() - t_start, 1)

    summary = {
        "db_path":         str(db_path),
        "rows_read":       rows_read,
        "rows_inserted":   rows_inserted,
        "rows_skipped":    rows_skipped,
        "runtime_seconds": runtime,
    }
    print(
        f"[materializer] Done: {rows_inserted:,} inserted, "
        f"{rows_skipped} skipped, {runtime}s",
        file=sys.stderr,
    )
    return summary
