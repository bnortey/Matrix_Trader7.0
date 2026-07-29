#!/usr/bin/env python3
"""Bounded, resumable migration of stale Edge Lab rows to current versions."""
from __future__ import annotations

import argparse
import json
import time

from edge_lab.dataset_builder import EdgeLabConfig, build_dataset
from edge_lab.feature_engine import FEATURE_VERSION
from edge_lab.path_labeler import PATH_LABEL_VERSION
from edge_lab.storage import DB_PATH, connect, init_storage


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild a bounded batch of stale Edge Lab symbols."
    )
    parser.add_argument("--max-symbols", type=int, default=5)
    parser.add_argument("--window-days", type=int, default=90)
    parser.add_argument("--max-runtime-minutes", type=int, default=45)
    parser.add_argument("--batch-size", type=int, default=100)
    return parser.parse_args()


def stale_symbols(max_symbols: int, window_days: int) -> list[str]:
    con = connect(DB_PATH)
    try:
        init_storage(con)
        cutoff = int(time.time()) - max(14, min(365, window_days)) * 86400
        rows = con.execute(
            """
            SELECT symbol,
                   SUM(CASE
                       WHEN COALESCE(label_version,'') != ?
                         OR COALESCE(feature_version,'') != ?
                       THEN 1 ELSE 0 END
                   ) AS stale_rows,
                   COUNT(*) AS total_rows
            FROM candle_labels
            WHERE timeframe='Min15' AND timestamp >= ?
            GROUP BY symbol
            HAVING stale_rows > 0
            ORDER BY stale_rows DESC, symbol ASC
            LIMIT ?
            """,
            (PATH_LABEL_VERSION, FEATURE_VERSION, cutoff, max(0, max_symbols)),
        ).fetchall()
        return [str(row[0]) for row in rows]
    finally:
        con.close()


def main() -> int:
    args = parse_args()
    symbols = stale_symbols(args.max_symbols, args.window_days)
    if not symbols:
        print(json.dumps({
            "status": "current",
            "feature_version": FEATURE_VERSION,
            "label_version": PATH_LABEL_VERSION,
            "symbols": [],
        }))
        return 0
    summary = build_dataset(EdgeLabConfig(
        mode="backfill",
        resume=False,
        symbols=symbols,
        max_runtime_minutes=args.max_runtime_minutes,
        batch_size=args.batch_size,
        days=args.window_days,
    ))
    summary["migration_symbols"] = symbols
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if not summary.get("failures") else 1


if __name__ == "__main__":
    raise SystemExit(main())
