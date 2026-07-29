"""Cost-aware, dependence-aware factor analysis for Edge Lab."""
from __future__ import annotations

import math
import os
import sqlite3
from typing import Any

ANALYSIS_VERSION = "edge_factor_v2"
TEMPLATES = ["TP0_5_SL0_5", "TP1_0_SL0_5", "TP1_5_SL0_75", "TP2_0_SL1_0"]
SIDES = ["long", "short"]
TAGS = ["compressed", "expanded", "bullish_trend", "bearish_trend", "extreme_vol", "low_vol"]
TEMPLATE_COL = {
    "TP0_5_SL0_5": "t05",
    "TP1_0_SL0_5": "t10",
    "TP1_5_SL0_75": "t15",
    "TP2_0_SL1_0": "t20",
}
TEMPLATE_RISK = {
    "TP0_5_SL0_5": (0.5, 0.5),
    "TP1_0_SL0_5": (1.0, 0.5),
    "TP1_5_SL0_75": (1.5, 0.75),
    "TP2_0_SL1_0": (2.0, 1.0),
}
SIDE_COL = {"long": "l", "short": "s"}
TAG_COL = {
    "compressed": "tag_compressed",
    "expanded": "tag_expanded",
    "bullish_trend": "tag_bullish_trend",
    "bearish_trend": "tag_bearish_trend",
    "extreme_vol": "tag_extreme_vol",
    "low_vol": "tag_low_vol",
}

# Retained only as a historical reference series. Current-run baselines are
# authoritative for edge_delta.
REFERENCE_BASELINES = {
    "TP0_5_SL0_5": {"long": 44.2, "short": 46.2},
    "TP1_0_SL0_5": {"long": 30.4, "short": 32.0},
    "TP1_5_SL0_75": {"long": 30.5, "short": 31.7},
    "TP2_0_SL1_0": {"long": 30.0, "short": 30.7},
}
MIN_N = 30
MIN_EFFECTIVE_N = 30
SLOW_QUERY_THRESHOLD = 5.0


def wilson_interval(n: int, k: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 0.0
    phat = k / n
    denom = 1.0 + z * z / n
    centre = phat + z * z / (2 * n)
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)
    return (
        round(max(0.0, (centre - margin) / denom * 100.0), 2),
        round(min(100.0, (centre + margin) / denom * 100.0), 2),
    )


def sample_quality(effective_n: int) -> str:
    if effective_n >= 250:
        return "high_confidence"
    if effective_n >= 100:
        return "moderate_confidence"
    if effective_n >= MIN_EFFECTIVE_N:
        return "low_confidence"
    return "insufficient_independent_data"


def _columns(con: sqlite3.Connection) -> set[str]:
    return {row[1] for row in con.execute("PRAGMA table_info(candle_features)")}


def _expr(columns: set[str], name: str, fallback: str) -> str:
    return name if name in columns else fallback


def _cost_pct() -> float:
    try:
        bps = max(0.0, float(os.getenv("EDGE_LAB_ROUND_TRIP_COST_BPS") or "7"))
    except ValueError:
        bps = 7.0
    return bps / 100.0


def _analysis_window_days() -> int:
    try:
        return max(14, min(365, int(os.getenv("EDGE_LAB_ANALYSIS_WINDOW_DAYS") or "90")))
    except ValueError:
        return 90


def build_analysis_context(
    con: sqlite3.Connection,
    templates: list[str] | None = None,
) -> dict:
    tmpl_list = templates or TEMPLATES
    cols = _columns(con)
    dataset_min_ts, max_ts = con.execute(
        "SELECT MIN(timestamp), MAX(timestamp) FROM candle_features"
    ).fetchone()
    window_days = _analysis_window_days()
    analysis_start_ts = max(
        int(dataset_min_ts or 0),
        int(max_ts or 0) - window_days * 86400,
    )
    min_ts = con.execute(
        """
        SELECT MIN(timestamp) FROM candle_features
        WHERE timestamp >= ?
          AND label_version='edge_path_v2'
          AND feature_version='edge_features_v2'
        """,
        (analysis_start_ts,),
    ).fetchone()[0]
    total_rows = con.execute(
        """
        SELECT COUNT(*)
        FROM candle_features
        WHERE timestamp >= ?
        """,
        (analysis_start_ts,),
    ).fetchone()[0]
    eligible_v2_rows = con.execute(
        """
        SELECT COUNT(*)
        FROM candle_features
        WHERE label_version='edge_path_v2'
          AND feature_version='edge_features_v2'
          AND timestamp >= ?
        """,
        (analysis_start_ts,),
    ).fetchone()[0]
    paired_v2_coverage_pct = (
        float(eligible_v2_rows or 0) / float(total_rows or 1) * 100.0
    )
    split_ts = (
        int((int(min_ts) + int(max_ts or min_ts)) / 2)
        if min_ts is not None else analysis_start_ts
    )
    select = ["COUNT(*) AS raw_n"]
    for template in tmpl_list:
        prefix = TEMPLATE_COL[template]
        tp_pct, sl_pct = TEMPLATE_RISK[template]
        for side in SIDES:
            sc = SIDE_COL[side]
            stem = f"{prefix}_{sc}"
            tp = _expr(cols, f"{stem}_tp", "0")
            sl = _expr(cols, f"{stem}_sl", "0")
            amb = _expr(cols, f"{stem}_ambig", "0")
            gross_fallback = (
                f"CASE WHEN {tp}=1 THEN {tp_pct} "
                f"WHEN {sl}=1 THEN -{sl_pct} END"
            )
            gross = (
                f"COALESCE({stem}_gross, {gross_fallback})"
                if f"{stem}_gross" in cols else gross_fallback
            )
            key = f"{template}.{side}"
            alias = key.replace(".", "__")
            select.extend([
                f"SUM(COALESCE({tp},0)) AS {alias}__tp",
                f"SUM(COALESCE({amb},0)) AS {alias}__amb",
                f"AVG({gross}) AS {alias}__gross",
                f"AVG(CASE WHEN timestamp < {split_ts} THEN {gross} END) AS {alias}__disc_gross",
                f"AVG(CASE WHEN timestamp >= {split_ts} THEN {gross} END) AS {alias}__conf_gross",
                f"SUM(CASE WHEN timestamp < {split_ts} THEN COALESCE({tp},0) ELSE 0 END) AS {alias}__disc_tp",
                f"SUM(CASE WHEN timestamp < {split_ts} THEN 1-COALESCE({amb},0) ELSE 0 END) AS {alias}__disc_n",
                f"SUM(CASE WHEN timestamp >= {split_ts} THEN COALESCE({tp},0) ELSE 0 END) AS {alias}__conf_tp",
                f"SUM(CASE WHEN timestamp >= {split_ts} THEN 1-COALESCE({amb},0) ELSE 0 END) AS {alias}__conf_n",
            ])
    row = con.execute(
        f"""
        SELECT {', '.join(select)} FROM candle_features
        WHERE timestamp >= ?
          AND label_version='edge_path_v2'
          AND feature_version='edge_features_v2'
        """,
        (analysis_start_ts,),
    ).fetchone()
    names = [item[0] for item in con.execute(
        f"""
        SELECT {', '.join(select)} FROM candle_features
        WHERE timestamp >= ?
          AND label_version='edge_path_v2'
          AND feature_version='edge_features_v2'
        LIMIT 0
        """,
        (analysis_start_ts,),
    ).description]
    data = dict(zip(names, row))
    baselines = {}
    cost = _cost_pct()
    for template in tmpl_list:
        for side in SIDES:
            key = f"{template}.{side}"
            alias = key.replace(".", "__")
            resolved_n = max(0, int(data["raw_n"] or 0) - int(data.get(f"{alias}__amb") or 0))
            tp_rate = (
                float(data.get(f"{alias}__tp") or 0) / resolved_n * 100.0
                if resolved_n else 0.0
            )
            baselines[key] = {
                "tp_rate": round(tp_rate, 4),
                "reference_tp_rate": REFERENCE_BASELINES[template][side],
                "gross_expectancy_pct": _round(data.get(f"{alias}__gross")),
                "net_expectancy_pct": _net(data.get(f"{alias}__gross"), cost),
                "discovery_tp_rate": _rate(
                    data.get(f"{alias}__disc_tp"), data.get(f"{alias}__disc_n")
                ),
                "confirmation_tp_rate": _rate(
                    data.get(f"{alias}__conf_tp"), data.get(f"{alias}__conf_n")
                ),
                "discovery_net_expectancy_pct": _net(
                    data.get(f"{alias}__disc_gross"), cost
                ),
                "confirmation_net_expectancy_pct": _net(
                    data.get(f"{alias}__conf_gross"), cost
                ),
            }
    return {
        "analysis_version": ANALYSIS_VERSION,
        "columns": cols,
        "min_timestamp": min_ts,
        "dataset_min_timestamp": dataset_min_ts,
        "max_timestamp": max_ts,
        "analysis_window_days": window_days,
        "analysis_start_timestamp": analysis_start_ts,
        "split_timestamp": split_ts,
        "round_trip_cost_pct": cost,
        "funding_included": False,
        # Feature and path versions are written as one immutable evidence
        # contract. Reporting the paired coverage avoids two full legacy-table
        # scans while matching the exact population accepted by every factor
        # query.
        "paired_v2_coverage_pct": round(paired_v2_coverage_pct, 4),
        "label_v2_coverage_pct": round(paired_v2_coverage_pct, 4),
        "feature_v2_coverage_pct": round(paired_v2_coverage_pct, 4),
        "eligible_v2_rows": int(eligible_v2_rows or 0),
        "baselines": baselines,
    }


def _round(value, digits: int = 4):
    return round(float(value), digits) if value is not None else None


def _net(gross, cost: float):
    return round(float(gross) - cost, 4) if gross is not None else None


def _rate(numerator, denominator):
    denominator = int(denominator or 0)
    return round(float(numerator or 0) / denominator * 100.0, 4) if denominator else None


def _aggregate_select(
    columns: set[str],
    templates: list[str],
    split_ts: int,
) -> list[str]:
    select = [
        "COUNT(*) AS raw_n",
        "COUNT(DISTINCT symbol) AS symbol_count",
        "COUNT(DISTINCT symbol || ':' || date(timestamp, 'unixepoch')) AS effective_n",
        f"SUM(CASE WHEN timestamp < {split_ts} THEN 1 ELSE 0 END) AS discovery_n",
        f"SUM(CASE WHEN timestamp >= {split_ts} THEN 1 ELSE 0 END) AS confirmation_n",
    ]
    for template in templates:
        prefix = TEMPLATE_COL[template]
        tp_pct, sl_pct = TEMPLATE_RISK[template]
        for side in SIDES:
            sc = SIDE_COL[side]
            stem = f"{prefix}_{sc}"
            tp = _expr(columns, f"{stem}_tp", "0")
            sl = _expr(columns, f"{stem}_sl", "0")
            neither = _expr(columns, f"{stem}_neither", "0")
            amb = _expr(columns, f"{stem}_ambig", "0")
            gross_fallback = (
                f"CASE WHEN {tp}=1 THEN {tp_pct} "
                f"WHEN {sl}=1 THEN -{sl_pct} END"
            )
            gross = (
                f"COALESCE({stem}_gross, {gross_fallback})"
                if f"{stem}_gross" in columns else gross_fallback
            )
            for suffix, expression in [
                ("tp", f"SUM(COALESCE({tp},0))"),
                ("sl", f"SUM(COALESCE({sl},0))"),
                ("neither", f"SUM(COALESCE({neither},0))"),
                ("ambig", f"SUM(COALESCE({amb},0))"),
                ("mfe", f"AVG({_expr(columns, f'{stem}_mfe', 'NULL')})"),
                ("mae", f"AVG({_expr(columns, f'{stem}_mae', 'NULL')})"),
                ("ttp", f"AVG(CASE WHEN {tp}=1 THEN {_expr(columns, f'{stem}_ttp', 'NULL')} END)"),
                ("gross", f"AVG({gross})"),
                ("econ_n", f"COUNT({gross})"),
                ("disc_gross", f"AVG(CASE WHEN timestamp < {split_ts} THEN {gross} END)"),
                ("conf_gross", f"AVG(CASE WHEN timestamp >= {split_ts} THEN {gross} END)"),
                ("disc_tp", f"SUM(CASE WHEN timestamp < {split_ts} THEN COALESCE({tp},0) ELSE 0 END)"),
                ("disc_resolved", f"SUM(CASE WHEN timestamp < {split_ts} THEN 1-COALESCE({amb},0) ELSE 0 END)"),
                ("conf_tp", f"SUM(CASE WHEN timestamp >= {split_ts} THEN COALESCE({tp},0) ELSE 0 END)"),
                ("conf_resolved", f"SUM(CASE WHEN timestamp >= {split_ts} THEN 1-COALESCE({amb},0) ELSE 0 END)"),
            ]:
                select.append(f"{expression} AS {prefix}_{sc}__{suffix}")
    return select


def analyze_factor_group(
    con: sqlite3.Connection,
    group_col: str,
    where_clause: str,
    params: list,
    template: str | None = None,
    side: str | None = None,
    min_n: int = MIN_N,
    *,
    templates: list[str] | None = None,
    context: dict | None = None,
    source_group: str | None = None,
) -> dict | list[dict]:
    """Aggregate every template/side in one SQLite scan for a factor family."""
    tmpl_list = templates or ([template] if template else TEMPLATES)
    ctx = context or build_analysis_context(con, tmpl_list)
    select = _aggregate_select(ctx["columns"], tmpl_list, ctx["split_timestamp"])
    analysis_start = int(ctx.get("analysis_start_timestamp") or 0)
    scoped_clause = (
        f"timestamp >= {analysis_start} "
        "AND label_version='edge_path_v2' "
        "AND feature_version='edge_features_v2'"
    )
    if where_clause:
        scoped_clause += f" AND ({where_clause})"
    where = f"WHERE {scoped_clause}"
    sql = f"""
        SELECT {group_col} AS group_key, {', '.join(select)}
        FROM candle_features
        {where}
        GROUP BY group_key
        HAVING COUNT(*) >= ?
    """
    cur = con.execute(sql, [*params, min_n])
    names = [item[0] for item in cur.description]
    raw_rows = [dict(zip(names, row)) for row in cur.fetchall()]
    output: dict[str, list[dict]] = {}
    for tmpl in tmpl_list:
        for result_side in SIDES:
            key = f"{tmpl}.{result_side}"
            output[key] = [
                _build_row(row, tmpl, result_side, ctx, source_group)
                for row in raw_rows if row.get("group_key") is not None
            ]
            output[key].sort(
                key=lambda item: (
                    item.get("confirmation_net_expectancy_pct")
                    if item.get("confirmation_net_expectancy_pct") is not None else -999,
                    item.get("edge_delta") or -999,
                ),
                reverse=True,
            )
    if template and side:
        return output.get(f"{template}.{side}", [])
    return output


def _build_row(row: dict, template: str, side: str, context: dict, source_group: str | None) -> dict:
    prefix = TEMPLATE_COL[template]
    sc = SIDE_COL[side]
    stem = f"{prefix}_{sc}"
    raw_n = int(row.get("raw_n") or 0)
    effective_n = min(raw_n, int(row.get("effective_n") or 0))
    tp = int(row.get(f"{stem}__tp") or 0)
    sl = int(row.get(f"{stem}__sl") or 0)
    neither = int(row.get(f"{stem}__neither") or 0)
    ambiguous = int(row.get(f"{stem}__ambig") or 0)
    resolved_n = max(0, raw_n - ambiguous)
    tp_rate = tp / resolved_n * 100.0 if resolved_n else 0.0
    effective_tp = int(round(tp_rate / 100.0 * effective_n))
    lo, hi = wilson_interval(effective_n, effective_tp)
    baseline = context["baselines"][f"{template}.{side}"]
    cost = float(context["round_trip_cost_pct"])
    gross = row.get(f"{stem}__gross")
    net = _net(gross, cost)
    disc_net = _net(row.get(f"{stem}__disc_gross"), cost)
    conf_net = _net(row.get(f"{stem}__conf_gross"), cost)
    ambiguity_pct = ambiguous / raw_n * 100.0 if raw_n else 0.0
    tier = "research_only"
    if (
        float(context.get("label_v2_coverage_pct") or 0.0) >= 95.0
        and float(context.get("feature_v2_coverage_pct") or 0.0) >= 95.0
        and
        effective_n >= MIN_EFFECTIVE_N
        and int(row.get(f"{stem}__econ_n") or 0) >= MIN_N
        and ambiguity_pct <= 10.0
    ):
        tier = "confirmation_candidate"
    if (
        tier == "confirmation_candidate"
        and net is not None and net > 0
        and disc_net is not None and disc_net > 0
        and conf_net is not None and conf_net > 0
    ):
        tier = "validated_candidate"
    return {
        "group_key": str(row["group_key"]),
        "source_group": source_group,
        "n": raw_n,
        "raw_n": raw_n,
        "effective_n": effective_n,
        "symbol_count": int(row.get("symbol_count") or 0),
        "discovery_n": int(row.get("discovery_n") or 0),
        "confirmation_n": int(row.get("confirmation_n") or 0),
        "tp_hit": tp,
        "sl_hit": sl,
        "neither_hit": neither,
        "ambiguous_hit": ambiguous,
        "resolved_n": resolved_n,
        "economics_n": int(row.get(f"{stem}__econ_n") or 0),
        "tp_rate": round(tp_rate, 2),
        "sl_rate": round(sl / resolved_n * 100.0, 2) if resolved_n else 0.0,
        "neither_rate": round(neither / resolved_n * 100.0, 2) if resolved_n else 0.0,
        "ambiguous_pct": round(ambiguity_pct, 2),
        "wilson_lower": lo,
        "wilson_upper": hi,
        "sample_quality": sample_quality(effective_n),
        "avg_mfe_pct": _round(row.get(f"{stem}__mfe")),
        "avg_mae_pct": _round(row.get(f"{stem}__mae")),
        "avg_time_to_tp_minutes": _round(row.get(f"{stem}__ttp"), 1),
        "gross_expectancy_pct": _round(gross),
        "round_trip_cost_pct": cost,
        "net_expectancy_pct": net,
        "discovery_net_expectancy_pct": disc_net,
        "confirmation_net_expectancy_pct": conf_net,
        "discovery_tp_rate": _rate(
            row.get(f"{stem}__disc_tp"), row.get(f"{stem}__disc_resolved")
        ),
        "confirmation_tp_rate": _rate(
            row.get(f"{stem}__conf_tp"), row.get(f"{stem}__conf_resolved")
        ),
        "edge_delta": round(tp_rate - float(baseline["tp_rate"]), 2),
        "reference_edge_delta": round(
            tp_rate - REFERENCE_BASELINES[template][side], 2
        ),
        "baseline_rate": round(float(baseline["tp_rate"]), 2),
        "reference_baseline_rate": REFERENCE_BASELINES[template][side],
        "template": template,
        "side": side,
        "evidence_tier": tier,
        "funding_included": False,
    }


def _run(
    con: sqlite3.Connection,
    group: str,
    group_col: str,
    where: str,
    templates: list[str] | None,
    context: dict | None,
) -> dict:
    return analyze_factor_group(
        con, group_col, where, [], templates=templates,
        context=context, source_group=group,
    )


def analyze_volatility_regime(con, templates=None, context=None):
    return _run(con, "volatility_regime", "volatility_regime", "volatility_regime IS NOT NULL", templates, context)


def analyze_trend_state(con, templates=None, context=None):
    return _run(con, "trend_state", "trend_state", "trend_state IS NOT NULL", templates, context)


def analyze_compression_state(con, templates=None, context=None):
    return _run(con, "compression_state", "compression_state", "compression_state IS NOT NULL", templates, context)


def analyze_regime_x_trend(con, templates=None, context=None):
    return _run(
        con, "regime_x_trend", "volatility_regime || '_' || trend_state",
        "volatility_regime IS NOT NULL AND trend_state IS NOT NULL", templates, context,
    )


def analyze_rsi_decile(con, templates=None, context=None):
    return _run(con, "rsi_decile", "rsi_decile", "rsi_decile IS NOT NULL", templates, context)


def analyze_volume_decile(con, templates=None, context=None):
    return _run(con, "volume_decile", "volume_decile", "volume_decile IS NOT NULL", templates, context)


def analyze_atr_decile(con, templates=None, context=None):
    return _run(con, "atr_decile", "atr_decile", "atr_decile IS NOT NULL", templates, context)


def analyze_tag_presence(con, templates=None, context=None):
    result = {}
    for tag in TAGS:
        result[tag] = analyze_factor_group(
            con,
            f"'{tag}'",
            f"{TAG_COL[tag]}=1",
            [],
            templates=templates,
            context=context,
            source_group=f"tag_presence:{tag}",
        )
    return result
