"""Resumable, version-aware Edge Lab feature materializer.

The canonical JSON labels remain in candle_labels. This module maintains the
flat candle_features projection used by factor analysis. Existing rows are
updated whenever their source label changes; feature-engine upgrades therefore
cannot silently leave stale flat rows behind.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

from edge_lab.feature_engine import FEATURE_VERSION
from edge_lab.path_labeler import PATH_LABEL_VERSION

MATERIALIZER_VERSION = "edge_materializer_v2"

_TEMPLATE_PREFIX = {
    "TP0_5_SL0_5": "t05",
    "TP1_0_SL0_5": "t10",
    "TP1_5_SL0_75": "t15",
    "TP2_0_SL1_0": "t20",
}
_TEMPLATES = list(_TEMPLATE_PREFIX)
_SIDES = ["long", "short"]
_SIDE_COL = {"long": "l", "short": "s"}

_BASE_COLUMNS = [
    "candle_id", "symbol", "timeframe", "timestamp",
    "volatility_regime", "trend_state", "compression_state",
    "rsi_decile", "volume_decile", "atr_decile", "stddev_decile",
    "tag_compressed", "tag_expanded", "tag_bullish_trend",
    "tag_bearish_trend", "tag_extreme_vol", "tag_low_vol",
    "source_generated_at", "feature_version", "label_version",
    "materializer_version",
]
_OUTCOME_SUFFIXES = [
    "tp", "sl", "neither", "ambig", "mfe", "mae", "ttp", "tsl", "texit",
    "gross", "hret", "amb_low", "amb_high",
]
_OUTCOME_COLUMNS = [
    f"{prefix}_{_SIDE_COL[side]}_{suffix}"
    for prefix in _TEMPLATE_PREFIX.values()
    for side in _SIDES
    for suffix in _OUTCOME_SUFFIXES
]
_ALL_COLUMNS = _BASE_COLUMNS + _OUTCOME_COLUMNS

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS candle_features (
    candle_id INTEGER PRIMARY KEY,
    symbol TEXT,
    timeframe TEXT,
    timestamp INTEGER,
    volatility_regime TEXT,
    trend_state TEXT,
    compression_state TEXT,
    rsi_decile INTEGER,
    volume_decile INTEGER,
    atr_decile INTEGER,
    stddev_decile INTEGER,
    tag_compressed INTEGER,
    tag_expanded INTEGER,
    tag_bullish_trend INTEGER,
    tag_bearish_trend INTEGER,
    tag_extreme_vol INTEGER,
    tag_low_vol INTEGER
)
"""

_INDEXES = [
    ("idx_cf_vol_regime", "CREATE INDEX IF NOT EXISTS idx_cf_vol_regime ON candle_features(volatility_regime)"),
    ("idx_cf_trend", "CREATE INDEX IF NOT EXISTS idx_cf_trend ON candle_features(trend_state)"),
    ("idx_cf_compression", "CREATE INDEX IF NOT EXISTS idx_cf_compression ON candle_features(compression_state)"),
    ("idx_cf_rsi", "CREATE INDEX IF NOT EXISTS idx_cf_rsi ON candle_features(rsi_decile)"),
    ("idx_cf_volume", "CREATE INDEX IF NOT EXISTS idx_cf_volume ON candle_features(volume_decile)"),
    ("idx_cf_atr", "CREATE INDEX IF NOT EXISTS idx_cf_atr ON candle_features(atr_decile)"),
    ("idx_cf_regime_trend", "CREATE INDEX IF NOT EXISTS idx_cf_regime_trend ON candle_features(volatility_regime, trend_state)"),
    ("idx_cf_symbol_ts", "CREATE INDEX IF NOT EXISTS idx_cf_symbol_ts ON candle_features(symbol, timeframe, timestamp)"),
    ("idx_cf_versions_ts", "CREATE INDEX IF NOT EXISTS idx_cf_versions_ts ON candle_features(label_version, feature_version, timestamp)"),
]


def _parse_json(value: str | None) -> dict:
    try:
        return json.loads(value or "{}")
    except Exception:
        return {}


def _bool_to_int(value) -> int | None:
    return 1 if value is True else 0 if value is False else None


def _int_or_none(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _float_or_none(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _ensure_schema(con: sqlite3.Connection) -> None:
    con.execute(_CREATE_TABLE)
    label_columns = {
        row[1] for row in con.execute("PRAGMA table_info(candle_labels)")
    }
    for column in ("feature_version", "label_version"):
        if column not in label_columns:
            con.execute(f"ALTER TABLE candle_labels ADD COLUMN {column} TEXT")
    current = {row[1] for row in con.execute("PRAGMA table_info(candle_features)")}
    declarations = {
        "source_generated_at": "TEXT",
        "feature_version": "TEXT",
        "label_version": "TEXT",
        "materializer_version": "TEXT",
    }
    for column in _OUTCOME_COLUMNS:
        declarations[column] = "INTEGER" if column.endswith(
            ("_tp", "_sl", "_neither", "_ambig")
        ) else "REAL"
    for column, declaration in declarations.items():
        if column not in current:
            con.execute(f"ALTER TABLE candle_features ADD COLUMN {column} {declaration}")
    con.commit()


def _extract_row(row: tuple) -> tuple:
    (
        candle_id, symbol, timeframe, timestamp, features_str, paths_str,
        source_generated_at, source_feature_version, source_label_version,
    ) = row
    features = _parse_json(features_str)
    paths = _parse_json(paths_str)
    tags_raw = features.get("tags") or []
    if isinstance(tags_raw, str):
        tags_raw = _parse_json(tags_raw)
    tags = set(tags_raw) if isinstance(tags_raw, list) else set()
    first_path = next(iter(paths.values()), {})

    values = [
        candle_id, symbol, timeframe, timestamp,
        features.get("volatility_regime"),
        features.get("trend_state"),
        features.get("compression_state"),
        _int_or_none(features.get("rsi_15m_decile")),
        _int_or_none(features.get("volume_decile")),
        _int_or_none(features.get("atr_pct_15m_decile")),
        _int_or_none(features.get("stddev_decile")),
        int("compressed" in tags),
        int("expanded" in tags),
        int("bullish_trend" in tags),
        int("bearish_trend" in tags),
        int("extreme_vol" in tags),
        int("low_vol" in tags),
        source_generated_at,
        source_feature_version or features.get("feature_version"),
        source_label_version or first_path.get("label_version"),
        MATERIALIZER_VERSION,
    ]

    for template in _TEMPLATES:
        payload = paths.get(template) or {}
        for side in _SIDES:
            values.extend([
                _bool_to_int(payload.get(f"{side}_tp_hit_first")),
                _bool_to_int(payload.get(f"{side}_sl_hit_first")),
                _bool_to_int(payload.get(f"{side}_neither_hit")),
                _bool_to_int(payload.get(f"{side}_ambiguous_hit")),
                _float_or_none(payload.get(f"{side}_mfe_pct")),
                _float_or_none(payload.get(f"{side}_mae_pct")),
                _float_or_none(payload.get(f"{side}_time_to_tp_minutes")),
                _float_or_none(payload.get(f"{side}_time_to_sl_minutes")),
                _float_or_none(payload.get(f"{side}_time_to_exit_minutes")),
                _float_or_none(payload.get(f"{side}_gross_pnl_pct")),
                _float_or_none(payload.get(f"{side}_horizon_return_pct")),
                _float_or_none(payload.get(f"{side}_ambiguity_pnl_low_pct")),
                _float_or_none(payload.get(f"{side}_ambiguity_pnl_high_pct")),
            ])
    return tuple(values)


def _upsert_sql() -> str:
    quoted = ", ".join(_ALL_COLUMNS)
    placeholders = ", ".join("?" for _ in _ALL_COLUMNS)
    updates = ", ".join(
        f"{column}=excluded.{column}" for column in _ALL_COLUMNS if column != "candle_id"
    )
    return (
        f"INSERT INTO candle_features ({quoted}) VALUES ({placeholders}) "
        f"ON CONFLICT(candle_id) DO UPDATE SET {updates}"
    )


def materialize(db_path: Path, batch_size: int = 10_000) -> dict:
    """Project current-version JSON labels into ``candle_features``.

    Legacy source rows remain available for audit and gradual rebuilds, but
    rewriting their stale projections during the v2 migration would create
    millions of unusable rows and a large WAL. The factor engine only accepts
    current feature/label versions, so materialization follows the same
    boundary.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"DB not found: {db_path}")

    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA cache_size=-64000")
    _ensure_schema(con)

    total_source = con.execute("SELECT COUNT(*) FROM candle_labels").fetchone()[0]
    eligible_source = con.execute(
        """
        SELECT COUNT(*)
        FROM candle_labels
        WHERE feature_version = ?
          AND label_version = ?
        """,
        (FEATURE_VERSION, PATH_LABEL_VERSION),
    ).fetchone()[0]
    t_start = time.time()
    cursor_id = -1
    rows_read = 0
    rows_upserted = 0
    rows_skipped = 0
    upsert_sql = _upsert_sql()

    print(f"[materializer] DB: {db_path}", file=sys.stderr)
    print(f"[materializer] source rows: {total_source:,}", file=sys.stderr)
    while True:
        batch = con.execute("""
            SELECT l.id, l.symbol, l.timeframe, l.timestamp,
                   l.features_json, l.paths_json, l.generated_at,
                   l.feature_version, l.label_version
            FROM candle_labels AS l
            LEFT JOIN candle_features AS f ON f.candle_id=l.id
            WHERE l.id > ?
              AND l.feature_version = ?
              AND l.label_version = ?
              AND (
                    f.candle_id IS NULL
                 OR COALESCE(f.source_generated_at, '') <> COALESCE(l.generated_at, '')
                 OR COALESCE(f.materializer_version, '') <> ?
              )
            ORDER BY l.id
            LIMIT ?
        """, (
            cursor_id,
            FEATURE_VERSION,
            PATH_LABEL_VERSION,
            MATERIALIZER_VERSION,
            batch_size,
        )).fetchall()
        if not batch:
            break

        payload = []
        for raw_row in batch:
            cursor_id = int(raw_row[0])
            rows_read += 1
            try:
                payload.append(_extract_row(raw_row))
            except Exception as exc:
                rows_skipped += 1
                print(
                    f"[materializer] WARN candle_id={raw_row[0]}: {exc}",
                    file=sys.stderr,
                )
        if payload:
            con.executemany(upsert_sql, payload)
            con.commit()
            rows_upserted += len(payload)
        if rows_read and rows_read % 500_000 < batch_size:
            elapsed = max(0.001, time.time() - t_start)
            print(
                f"[materializer] {rows_read:,} changed rows "
                f"({rows_read / elapsed:,.0f}/s)",
                file=sys.stderr,
            )

    print(f"[materializer] ensuring {len(_INDEXES)} indexes", file=sys.stderr)
    for name, ddl in _INDEXES:
        started = time.time()
        con.execute(ddl)
        con.commit()
        print(f"[materializer] {name}: {time.time() - started:.1f}s", file=sys.stderr)

    con.close()
    runtime = round(time.time() - t_start, 2)
    summary = {
        "db_path": str(db_path),
        "materializer_version": MATERIALIZER_VERSION,
        "source_rows": total_source,
        "eligible_source_rows": eligible_source,
        "rows_read": rows_read,
        "rows_upserted": rows_upserted,
        "rows_inserted": rows_upserted,
        "rows_skipped": rows_skipped,
        "runtime_seconds": runtime,
    }
    print(
        f"[materializer] done: {rows_upserted:,} upserted, "
        f"{rows_skipped:,} skipped, {runtime}s",
        file=sys.stderr,
    )
    return summary
