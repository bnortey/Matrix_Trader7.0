#!/usr/bin/env python3
"""Audited Edge Lab source-retention maintenance.

The flat v2 projection is the analysis store. Source JSON may be pruned only
after the matching v2 projection is verified. Dry-run is the default.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

from edge_lab.materializer import MATERIALIZER_VERSION
from edge_lab.path_labeler import PATH_LABEL_VERSION


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit or prune materialized Edge Lab source JSON.")
    parser.add_argument("--db", type=Path, default=Path("data/edge_lab.db"))
    parser.add_argument("--retain-days", type=int, default=7)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--backup-confirmed",
        action="store_true",
        help="Required with --apply; confirms a recoverable DB backup exists.",
    )
    parser.add_argument("--batch-size", type=int, default=20_000)
    return parser.parse_args()


def retention_status(db_path: Path, retain_days: int) -> dict:
    con = sqlite3.connect(db_path)
    cutoff = int(time.time()) - max(1, retain_days) * 86400
    try:
        source_rows = con.execute("SELECT COUNT(*) FROM candle_labels").fetchone()[0]
        eligible = con.execute(
            """
            SELECT COUNT(*)
            FROM candle_labels AS l
            JOIN candle_features AS f ON f.candle_id=l.id
            WHERE l.timestamp < ?
              AND l.label_version=?
              AND f.label_version=?
              AND f.materializer_version=?
              AND f.source_generated_at=l.generated_at
            """,
            (
                cutoff, PATH_LABEL_VERSION, PATH_LABEL_VERSION,
                MATERIALIZER_VERSION,
            ),
        ).fetchone()[0]
        page_count = con.execute("PRAGMA page_count").fetchone()[0]
        freelist = con.execute("PRAGMA freelist_count").fetchone()[0]
        page_size = con.execute("PRAGMA page_size").fetchone()[0]
        return {
            "db_path": str(db_path),
            "source_rows": int(source_rows),
            "verified_prune_eligible_rows": int(eligible),
            "retain_days": max(1, retain_days),
            "cutoff_timestamp": cutoff,
            "database_bytes": int(page_count * page_size),
            "reusable_free_bytes": int(freelist * page_size),
            "label_version_required": PATH_LABEL_VERSION,
            "materializer_version_required": MATERIALIZER_VERSION,
        }
    finally:
        con.close()


def prune_verified_sources(
    db_path: Path,
    retain_days: int,
    batch_size: int,
) -> int:
    con = sqlite3.connect(db_path)
    cutoff = int(time.time()) - max(1, retain_days) * 86400
    deleted = 0
    try:
        while True:
            ids = [
                row[0] for row in con.execute(
                    """
                    SELECT l.id
                    FROM candle_labels AS l
                    JOIN candle_features AS f ON f.candle_id=l.id
                    WHERE l.timestamp < ?
                      AND l.label_version=?
                      AND f.label_version=?
                      AND f.materializer_version=?
                      AND f.source_generated_at=l.generated_at
                    LIMIT ?
                    """,
                    (
                        cutoff, PATH_LABEL_VERSION, PATH_LABEL_VERSION,
                        MATERIALIZER_VERSION, max(100, batch_size),
                    ),
                ).fetchall()
            ]
            if not ids:
                break
            con.executemany(
                "DELETE FROM candle_labels WHERE id=?",
                [(row_id,) for row_id in ids],
            )
            con.commit()
            deleted += len(ids)
        return deleted
    finally:
        con.close()


def main() -> int:
    args = parse_args()
    if not args.db.exists():
        raise SystemExit(f"Edge Lab DB not found: {args.db}")
    before = retention_status(args.db, args.retain_days)
    if not args.apply:
        print(json.dumps({**before, "mode": "dry_run"}, indent=2))
        return 0
    if not args.backup_confirmed:
        raise SystemExit("--apply requires --backup-confirmed")
    deleted = prune_verified_sources(
        args.db, args.retain_days, args.batch_size
    )
    after = retention_status(args.db, args.retain_days)
    print(json.dumps({
        "mode": "applied",
        "deleted_rows": deleted,
        "before": before,
        "after": after,
        "note": (
            "SQLite will reuse freed pages. VACUUM is intentionally not "
            "automatic because it requires extra disk space and an exclusive lock."
        ),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
