"""
Factor report orchestrator for Edge Lab.

Runs all factor group analyses against edge_lab.db and produces
data/factor_report.json. All DB work is SQLite aggregate queries only —
never loads all rows into memory.

Do NOT import app.py or backtest.py. Do NOT write to signals.db.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Callable

from edge_lab.factor_engine import (
    TEMPLATES,
    SLOW_QUERY_THRESHOLD,
    analyze_atr_decile,
    analyze_compression_state,
    analyze_regime_x_trend,
    analyze_rsi_decile,
    analyze_tag_presence,
    analyze_trend_state,
    analyze_volatility_regime,
    analyze_volume_decile,
)


def _top_n_by_edge(flat_results: dict, n: int, side: str) -> list[dict]:
    """Collect top-n rows across all groups and templates for a given side, sorted by edge_delta."""
    seen = set()
    all_rows: list[dict] = []
    for _label, result in flat_results.items():
        for key, rows in result.items():
            if not key.endswith(f".{side}"):
                continue
            for row in rows:
                uid = (row["group_key"], row["template"], row["side"])
                if uid not in seen:
                    seen.add(uid)
                    all_rows.append(row)
    all_rows.sort(key=lambda r: r["edge_delta"], reverse=True)
    return all_rows[:n]


def _run_group(
    label: str,
    fn: Callable,
    con: sqlite3.Connection,
    templates: list[str] | None,
    verbose: bool,
) -> tuple[str, dict, float]:
    if verbose:
        print(f"  [{label}] ...", end="", flush=True)
    t0 = time.time()
    result = fn(con, templates=templates)
    elapsed = time.time() - t0
    if verbose:
        print(f" {elapsed:.1f}s")
    if elapsed > SLOW_QUERY_THRESHOLD:
        print(f"  WARNING: [{label}] took {elapsed:.1f}s (>{SLOW_QUERY_THRESHOLD}s threshold)")
    return label, result, elapsed


def run_factor_analysis(
    db_path: Path,
    top_n: int = 10,
    templates: list[str] | None = None,
    verbose: bool = True,
) -> dict:
    """
    Run all 8 factor group analyses against edge_lab.db.

    Returns a dict suitable for JSON serialisation. Does not write any files.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"Edge Lab DB not found: {db_path}")

    con = sqlite3.connect(str(db_path), check_same_thread=False)
    con.row_factory = sqlite3.Row

    # Guard: candle_features must exist and be populated before analysis
    try:
        cf_count = con.execute("SELECT COUNT(*) FROM candle_features").fetchone()[0]
    except sqlite3.OperationalError:
        cf_count = 0
    if cf_count == 0:
        con.close()
        raise RuntimeError(
            "candle_features table is missing or empty. "
            "Run: python3 edge_lab_materialize.py"
        )

    total_rows: int = cf_count
    tmpl_list = templates or TEMPLATES

    if verbose:
        print(f"Edge Lab Factor Analysis")
        print(f"  DB:        {db_path}")
        print(f"  Features:  {total_rows:,}")
        print(f"  Templates: {', '.join(tmpl_list)}")
        print(f"  Top-N:     {top_n}")
        print()

    groups_cfg: list[tuple[str, Callable]] = [
        ("volatility_regime",  analyze_volatility_regime),
        ("trend_state",        analyze_trend_state),
        ("compression_state",  analyze_compression_state),
        ("regime_x_trend",     analyze_regime_x_trend),
        ("rsi_decile",         analyze_rsi_decile),
        ("volume_decile",      analyze_volume_decile),
        ("tag_presence",       analyze_tag_presence),
        ("atr_decile",         analyze_atr_decile),
    ]

    t_start = time.time()
    groups: dict[str, dict] = {}
    timing: dict[str, float] = {}

    for label, fn in groups_cfg:
        _, result, elapsed = _run_group(label, fn, con, tmpl_list, verbose)
        groups[label] = result
        timing[label] = round(elapsed, 2)

    con.close()
    total_elapsed = round(time.time() - t_start, 2)

    # Compute top_long_states / top_short_states across all flat groups
    # (tag_presence is nested by tag, so flatten it first)
    flat_results: dict[str, dict] = {}
    for label, result in groups.items():
        if label == "tag_presence":
            for tag, tag_result in result.items():
                flat_results[f"tag_presence:{tag}"] = tag_result
        else:
            flat_results[label] = result

    top_long: list[dict] = _top_n_by_edge(flat_results, top_n, "long")
    top_short: list[dict] = _top_n_by_edge(flat_results, top_n, "short")

    # Attach source_group to each top row
    for row in top_long + top_short:
        for label, result in flat_results.items():
            key = f"{row['template']}.{row['side']}"
            rows_for_key = result.get(key, [])
            if any(r["group_key"] == row["group_key"] for r in rows_for_key):
                row["source_group"] = label
                break

    report = {
        "meta": {
            "db_path":       str(db_path),
            "total_candles": total_rows,
            "templates":     tmpl_list,
            "top_n":         top_n,
            "total_seconds": total_elapsed,
            "group_seconds": timing,
        },
        "top_long_states":  top_long,
        "top_short_states": top_short,
        "groups":           groups,
    }

    if verbose:
        print()
        print(f"Done in {total_elapsed:.1f}s")
        print(f"  Top LONG  states: {len(top_long)}")
        print(f"  Top SHORT states: {len(top_short)}")

    return report
