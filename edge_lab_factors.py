"""
Edge Lab Factor Analysis CLI.

Usage:
  python3 edge_lab_factors.py
  python3 edge_lab_factors.py --db data/edge_lab.db --out data/factor_report.json
  python3 edge_lab_factors.py --template TP1_0_SL0_5 --top-n 5

Connects to the edge_lab.db, runs full factor analysis, saves a JSON report,
and prints a human-readable summary. Never writes to signals.db.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).parent))

from edge_lab.factor_engine import TEMPLATES
from edge_lab.factor_report import run_factor_analysis


def _fmt_row(row: dict) -> str:
    return (
        f"  {row['group_key']:<28} "
        f"n={row['n']:<6} "
        f"tp%={row['tp_rate']:.1f}  "
        f"delta={row['edge_delta']:+.1f}  "
        f"CI=[{row['wilson_lower']:.1f},{row['wilson_upper']:.1f}]  "
        f"{row['template']}.{row['side']}  "
        f"({row['sample_quality']})"
    )


def _print_summary(report: dict, top_n: int) -> None:
    meta = report["meta"]
    print()
    print("=" * 72)
    print("EDGE LAB FACTOR ANALYSIS SUMMARY")
    print("=" * 72)
    print(f"DB:          {meta['db_path']}")
    print(f"Candles:     {meta['total_candles']:,}")
    print(f"Templates:   {', '.join(meta['templates'])}")
    print(f"Runtime:     {meta['total_seconds']:.1f}s total")
    print()

    print(f"TOP {top_n} LONG STATES (by edge delta)")
    print("-" * 72)
    longs = report["top_long_states"]
    if longs:
        for row in longs:
            src = row.get("source_group", "?")
            print(f"  [{src}]")
            print(_fmt_row(row))
    else:
        print("  No qualifying rows (n < min_n for all groups)")
    print()

    print(f"TOP {top_n} SHORT STATES (by edge delta)")
    print("-" * 72)
    shorts = report["top_short_states"]
    if shorts:
        for row in shorts:
            src = row.get("source_group", "?")
            print(f"  [{src}]")
            print(_fmt_row(row))
    else:
        print("  No qualifying rows (n < min_n for all groups)")
    print()

    print("GROUP TIMING")
    print("-" * 72)
    for grp, secs in meta["group_seconds"].items():
        print(f"  {grp:<24} {secs:.2f}s")
    print("=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Edge Lab factor analysis and save a JSON report."
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/edge_lab.db"),
        help="Path to edge_lab.db (default: data/edge_lab.db)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/factor_report.json"),
        help="Output path for the JSON report (default: data/factor_report.json)",
    )
    parser.add_argument(
        "--template",
        action="append",
        dest="templates",
        choices=TEMPLATES,
        metavar="TEMPLATE",
        help=(
            f"Restrict analysis to one template. "
            f"Choices: {', '.join(TEMPLATES)}. "
            f"May be repeated."
        ),
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="Number of top states to include in summary lists (default: 10)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-group progress output",
    )
    args = parser.parse_args()

    try:
        report = run_factor_analysis(
            db_path=args.db,
            top_n=args.top_n,
            templates=args.templates or None,
            verbose=not args.quiet,
        )
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    out_path: Path = args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)

    print(f"\nReport saved → {out_path}")

    _print_summary(report, args.top_n)


if __name__ == "__main__":
    main()
