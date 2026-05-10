"""
Edge Lab Materializer CLI.

Reads candle_labels (JSON blobs) and writes flat columns to candle_features
so factor engine queries run in <5s instead of 270-360s per group.

Usage:
  python3 edge_lab_materialize.py
  python3 edge_lab_materialize.py --db data/edge_lab.db
  python3 edge_lab_materialize.py --batch-size 5000

Safe to re-run — uses INSERT OR IGNORE (idempotent).
Never writes to signals.db.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from edge_lab.materializer import materialize


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Materialize candle_labels JSON into flat candle_features columns."
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/edge_lab.db"),
        help="Path to edge_lab.db (default: data/edge_lab.db)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10_000,
        help="Rows per INSERT batch (default: 10000)",
    )
    args = parser.parse_args()

    try:
        summary = materialize(db_path=args.db, batch_size=args.batch_size)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print()
    print("=" * 60)
    print("MATERIALIZATION COMPLETE")
    print("=" * 60)
    print(f"  DB:           {summary['db_path']}")
    print(f"  Rows read:    {summary['rows_read']:,}")
    print(f"  Rows inserted:{summary['rows_inserted']:,}")
    print(f"  Rows skipped: {summary['rows_skipped']}")
    print(f"  Runtime:      {summary['runtime_seconds']}s")
    print("=" * 60)
    print()
    print("candle_features is ready. Run factor analysis with:")
    print("  python3 edge_lab_factors.py")


if __name__ == "__main__":
    main()
