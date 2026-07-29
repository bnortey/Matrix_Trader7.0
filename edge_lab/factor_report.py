"""
Factor report orchestrator for Edge Lab.

Runs all factor group analyses against edge_lab.db and produces
data/factor_report.json. All DB work is SQLite aggregate queries only —
never loads all rows into memory.

Do NOT import app.py or backtest.py. Do NOT write to signals.db.
"""
from __future__ import annotations

import json
import hashlib
import math
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from edge_lab.factor_engine import (
    TEMPLATES,
    SIDE_COL,
    TAG_COL,
    TEMPLATE_COL,
    SLOW_QUERY_THRESHOLD,
    ANALYSIS_VERSION,
    build_analysis_context,
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
    """Collect the strongest cost-adjusted confirmation states."""
    seen = set()
    all_rows: list[dict] = []
    for _label, result in flat_results.items():
        for key, rows in result.items():
            if not key.endswith(f".{side}"):
                continue
            for row in rows:
                uid = (
                    row.get("source_group"), row["group_key"],
                    row["template"], row["side"],
                )
                if uid not in seen:
                    seen.add(uid)
                    all_rows.append(row)
    tier_rank = {
        "forward_validated": 3,
        "validated_candidate": 2,
        "confirmation_candidate": 1,
        "research_only": 0,
    }
    all_rows.sort(key=lambda r: (
        tier_rank.get(r.get("evidence_tier"), 0),
        r.get("confirmation_net_expectancy_pct")
        if r.get("confirmation_net_expectancy_pct") is not None else -999,
        r.get("net_expectancy_pct")
        if r.get("net_expectancy_pct") is not None else -999,
        r.get("edge_delta") or -999,
    ), reverse=True)
    return all_rows[:n]


def _run_group(
    label: str,
    fn: Callable,
    con: sqlite3.Connection,
    templates: list[str] | None,
    context: dict,
    verbose: bool,
) -> tuple[str, dict, float]:
    if verbose:
        print(f"  [{label}] ...", end="", flush=True)
    t0 = time.time()
    result = fn(con, templates=templates, context=context)
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
    context = build_analysis_context(con, tmpl_list)

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
        _, result, elapsed = _run_group(
            label, fn, con, tmpl_list, context, verbose
        )
        groups[label] = result
        timing[label] = round(elapsed, 2)

    # Compute top_long_states / top_short_states across all flat groups
    # (tag_presence is nested by tag, so flatten it first)
    flat_results: dict[str, dict] = {}
    for label, result in groups.items():
        if label == "tag_presence":
            for tag, tag_result in result.items():
                flat_results[f"tag_presence:{tag}"] = tag_result
        else:
            flat_results[label] = result

    all_factor_rows = []
    for result in flat_results.values():
        for rows in result.values():
            all_factor_rows.extend(rows)
    _attach_multiple_testing_control(all_factor_rows)

    top_long: list[dict] = _top_n_by_edge(flat_results, top_n, "long")
    top_short: list[dict] = _top_n_by_edge(flat_results, top_n, "short")
    concentration_started = time.time()
    _attach_symbol_concentration(
        con, top_long + top_short, context
    )
    timing["top_state_concentration"] = round(
        time.time() - concentration_started, 2
    )
    symbol_count = con.execute(
        """
        SELECT COUNT(DISTINCT symbol)
        FROM candle_features
        WHERE label_version='edge_path_v2'
          AND feature_version='edge_features_v2'
        """
    ).fetchone()[0]
    eligible_v2_rows = int(context.get("eligible_v2_rows") or 0)
    version_rows = [
        (
            "edge_features_v2",
            "edge_path_v2",
            "edge_materializer_v2",
            eligible_v2_rows,
        ),
        (
            "legacy_or_stale",
            "legacy_or_stale",
            "legacy_or_stale",
            max(0, int(total_rows or 0) - eligible_v2_rows),
        ),
    ]
    con.close()
    total_elapsed = round(time.time() - t_start, 2)
    generated_at = datetime.now(timezone.utc).isoformat()
    fingerprint_source = json.dumps({
        "analysis_version": ANALYSIS_VERSION,
        "total_rows": total_rows,
        "symbol_count": symbol_count,
        "min_timestamp": context.get("min_timestamp"),
        "max_timestamp": context.get("max_timestamp"),
        "versions": version_rows,
        "templates": tmpl_list,
    }, sort_keys=True, default=str)

    report = {
        "meta": {
            "db_path":       str(db_path),
            "generated_at": generated_at,
            "analysis_version": ANALYSIS_VERSION,
            "dataset_fingerprint": hashlib.sha256(
                fingerprint_source.encode("utf-8")
            ).hexdigest()[:20],
            "total_candles": total_rows,
            "symbol_count": int(symbol_count or 0),
            "symbol_count_scope": "paired_v2_eligible",
            "min_timestamp": context.get("min_timestamp"),
            "dataset_min_timestamp": context.get("dataset_min_timestamp"),
            "max_timestamp": context.get("max_timestamp"),
            "analysis_window_days": context.get("analysis_window_days"),
            "analysis_start_timestamp": context.get("analysis_start_timestamp"),
            "discovery_confirmation_split_timestamp": context.get("split_timestamp"),
            "round_trip_cost_pct": context.get("round_trip_cost_pct"),
            "funding_included": context.get("funding_included"),
            "net_cost_scope": "round_trip_fee_and_slippage_assumption_only",
            "funding_cost_status": (
                "not included in generic candle factors; exact captured "
                "funding is evaluated in strategy-conditioned Paper evidence"
            ),
            "path_horizon_candles": 96,
            "path_horizon_hours": 24,
            "live_outcome_horizon_hours": 84,
            "horizon_mismatch_note": (
                "Generic factor paths resolve at 24h; MT7 live/manual outcomes "
                "may remain open to 84h. Compare them only as separate evidence."
            ),
            "label_v2_coverage_pct": context.get("label_v2_coverage_pct"),
            "feature_v2_coverage_pct": context.get("feature_v2_coverage_pct"),
            "paired_v2_coverage_pct": context.get("paired_v2_coverage_pct"),
            "analysis_eligible_v2_rows": context.get("eligible_v2_rows"),
            "versions": [
                {
                    "feature_version": row[0],
                    "label_version": row[1],
                    "materializer_version": row[2],
                    "rows": row[3],
                }
                for row in version_rows
            ],
            "baseline_method": "current_run_dynamic",
            "templates":     tmpl_list,
            "top_n":         top_n,
            "total_seconds": total_elapsed,
            "group_seconds": timing,
        },
        "top_long_states":  top_long,
        "top_short_states": top_short,
        "baselines": context.get("baselines"),
        "groups":           groups,
    }

    if verbose:
        print()
        print(f"Done in {total_elapsed:.1f}s")
        print(f"  Top LONG  states: {len(top_long)}")
        print(f"  Top SHORT states: {len(top_short)}")

    return report


def _attach_multiple_testing_control(rows: list[dict]) -> None:
    """Benjamini-Hochberg correction using dependence-adjusted effective n."""
    tested = []
    for index, row in enumerate(rows):
        n = int(row.get("effective_n") or 0)
        observed = float(row.get("tp_rate") or 0.0) / 100.0
        baseline = float(row.get("baseline_rate") or 0.0) / 100.0
        if n <= 0 or baseline <= 0.0 or baseline >= 1.0:
            row["p_value"] = None
            row["q_value"] = None
            continue
        se = math.sqrt(baseline * (1.0 - baseline) / n)
        z = (observed - baseline) / se if se else 0.0
        p_value = 0.5 * math.erfc(z / math.sqrt(2.0))
        row["p_value"] = round(p_value, 8)
        tested.append((p_value, index, row))
    tested.sort(key=lambda item: item[0])
    total = len(tested)
    running = 1.0
    for rank in range(total, 0, -1):
        p_value, _, row = tested[rank - 1]
        running = min(running, p_value * total / rank)
        row["q_value"] = round(running, 8)
        if row.get("evidence_tier") == "validated_candidate" and running > 0.10:
            row["evidence_tier"] = "confirmation_candidate"


def _attach_symbol_concentration(
    con: sqlite3.Connection,
    rows: list[dict],
    context: dict,
) -> None:
    """Measure whether a top state is carried by only a few symbols."""
    group_columns = {
        "volatility_regime": "volatility_regime",
        "trend_state": "trend_state",
        "compression_state": "compression_state",
        "regime_x_trend": "volatility_regime || '_' || trend_state",
        "rsi_decile": "rsi_decile",
        "volume_decile": "volume_decile",
        "atr_decile": "atr_decile",
    }
    cache: dict[tuple, dict] = {}
    for row in rows:
        source = str(row.get("source_group") or "")
        template = str(row.get("template") or "")
        side = str(row.get("side") or "")
        group_key = str(row.get("group_key") or "")
        identity = (source, group_key, template, side)
        if identity in cache:
            row["symbol_concentration"] = cache[identity]
            continue
        stem = f"{TEMPLATE_COL.get(template, '')}_{SIDE_COL.get(side, '')}"
        tp_col = f"{stem}_tp"
        if tp_col not in context.get("columns", set()):
            continue
        params: list = [
            int(context.get("analysis_start_timestamp") or 0),
            "edge_path_v2",
            "edge_features_v2",
        ]
        where = (
            "timestamp >= ? "
            "AND label_version = ? "
            "AND feature_version = ?"
        )
        if source.startswith("tag_presence:"):
            tag = source.split(":", 1)[1]
            tag_col = TAG_COL.get(tag)
            if not tag_col:
                continue
            where += f" AND {tag_col}=1"
        else:
            group_col = group_columns.get(source)
            if not group_col:
                continue
            where += f" AND ({group_col})=?"
            params.append(group_key)
        contributors = con.execute(
            f"""
            SELECT symbol, SUM(COALESCE({tp_col},0)) AS tp_hits
            FROM candle_features
            WHERE {where}
            GROUP BY symbol
            HAVING tp_hits > 0
            ORDER BY tp_hits DESC, symbol
            """,
            params,
        ).fetchall()
        total_hits = sum(int(item[1] or 0) for item in contributors)
        top_five = contributors[:5]
        detail = {
            "symbols_with_tp_hits": len(contributors),
            "top5_tp_share_pct": round(
                sum(int(item[1] or 0) for item in top_five)
                / total_hits * 100.0,
                2,
            ) if total_hits else None,
            "top_symbol_tp_share_pct": round(
                int(top_five[0][1] or 0) / total_hits * 100.0,
                2,
            ) if top_five and total_hits else None,
            "top_symbols": [
                {"symbol": item[0], "tp_hits": int(item[1] or 0)}
                for item in top_five
            ],
        }
        cache[identity] = detail
        row["symbol_concentration"] = detail
        if (
            row.get("evidence_tier") == "validated_candidate"
            and float(detail.get("top5_tp_share_pct") or 0.0) > 50.0
        ):
            row["evidence_tier"] = "confirmation_candidate"
            row["concentration_warning"] = (
                "More than half of TP hits came from five symbols."
            )
