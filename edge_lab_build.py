#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3

from edge_lab.dataset_builder import EdgeLabConfig, build_dataset
from edge_lab.path_labeler import PATH_TEMPLATES
from edge_lab.storage import DB_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Matrix Trader Edge Lab candle labels.")
    parser.add_argument("--mode", choices=["backfill", "incremental"], default="backfill")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--max-runtime-minutes", type=int, default=45)
    parser.add_argument("--max-symbols", type=int)
    parser.add_argument("--min-volume-24h", type=float, default=0.0)
    parser.add_argument("--symbols", help="Comma-separated symbols, e.g. BTC_USDT,ETH_USDT")
    parser.add_argument("--top-n", type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None
    config = EdgeLabConfig(
        mode=args.mode,
        resume=args.resume,
        batch_size=args.batch_size,
        max_runtime_minutes=args.max_runtime_minutes,
        max_symbols=args.max_symbols,
        min_volume_24h=args.min_volume_24h,
        symbols=symbols,
        top_n=args.top_n,
    )
    summary = build_dataset(config)
    print_summary(summary)
    print_smoke_summary()
    return 0


def print_summary(summary: dict) -> None:
    print("\nEDGE LAB SUMMARY")
    print("----------------")
    labels = [
        ("Mode", "mode"),
        ("Universe mode", "universe_mode"),
        ("Symbols discovered", "symbols_discovered"),
        ("Symbols eligible", "symbols_eligible"),
        ("Symbols processed this run", "symbols_processed_this_run"),
        ("Symbols completed", "symbols_completed"),
        ("Symbols skipped", "symbols_skipped"),
        ("Symbols failed", "symbols_failed"),
        ("Symbols remaining", "symbols_remaining"),
        ("Candles fetched", "candles_fetched"),
        ("Rows labeled", "rows_labeled"),
        ("Rows inserted/updated", "rows_inserted_updated"),
        ("Rows skipped warmup", "rows_skipped_warmup"),
        ("Rows skipped no future", "rows_skipped_no_future"),
        ("Runtime seconds", "runtime_seconds"),
        ("Stopped because", "stopped_because"),
        ("DB path", "db_path"),
        ("Generated at", "generated_at"),
    ]
    for label, key in labels:
        print(f"{label}: {summary.get(key)}")
    if summary.get("partial_history_warnings"):
        print(f"Partial-history warnings: {json.dumps(summary['partial_history_warnings'][:10])}")
    if summary.get("failures"):
        print(f"Failures: {json.dumps(summary['failures'][:10])}")
    print("\nPath templates:")
    for name in PATH_TEMPLATES:
        print(f"- {name}")


def print_smoke_summary() -> None:
    if not DB_PATH.exists():
        return
    con = sqlite3.connect(DB_PATH)
    try:
        print("\nEDGE LAB PATH SMOKE")
        print("-------------------")
        for template in PATH_TEMPLATES:
            row = con.execute("""
                SELECT
                    COUNT(*),
                    AVG(CASE WHEN json_extract(paths_json, '$.' || ? || '.long_tp_hit_first') THEN 1.0 ELSE 0.0 END),
                    AVG(CASE WHEN json_extract(paths_json, '$.' || ? || '.short_tp_hit_first') THEN 1.0 ELSE 0.0 END),
                    AVG(CASE
                        WHEN json_extract(paths_json, '$.' || ? || '.long_ambiguous_hit')
                          OR json_extract(paths_json, '$.' || ? || '.short_ambiguous_hit')
                        THEN 1.0 ELSE 0.0 END)
                FROM candle_labels
            """, (template, template, template, template)).fetchone()
            total, long_rate, short_rate, amb_rate = row
            print(
                f"{template}: rows={total} "
                f"long_tp_first={_pct(long_rate)} "
                f"short_tp_first={_pct(short_rate)} "
                f"ambiguous={_pct(amb_rate)}"
            )
    finally:
        con.close()


def _pct(value) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"


if __name__ == "__main__":
    raise SystemExit(main())

