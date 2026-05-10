"""
Factor engine for Edge Lab candle analysis.

Queries candle_features (flat columns) in edge_lab.db — NOT candle_labels.
candle_features must be built first:
  python3 edge_lab_materialize.py

All grouping is done by SQLite. Never loads all rows into memory.

Do NOT import app.py or backtest.py. Do NOT write to signals.db.
"""
from __future__ import annotations

import math
import sqlite3
from typing import Any

TEMPLATES = ["TP0_5_SL0_5", "TP1_0_SL0_5", "TP1_5_SL0_75", "TP2_0_SL1_0"]
SIDES = ["long", "short"]
TAGS = ["compressed", "expanded", "bullish_trend", "bearish_trend", "extreme_vol", "low_vol"]

# Column prefix for each template
TEMPLATE_COL = {
    "TP0_5_SL0_5":  "t05",
    "TP1_0_SL0_5":  "t10",
    "TP1_5_SL0_75": "t15",
    "TP2_0_SL1_0":  "t20",
}
SIDE_COL = {"long": "l", "short": "s"}

# Tag name → candle_features column
TAG_COL = {
    "compressed":    "tag_compressed",
    "expanded":      "tag_expanded",
    "bullish_trend": "tag_bullish_trend",
    "bearish_trend": "tag_bearish_trend",
    "extreme_vol":   "tag_extreme_vol",
    "low_vol":       "tag_low_vol",
}

BASELINES = {
    "TP0_5_SL0_5":  {"long": 44.2, "short": 46.2},
    "TP1_0_SL0_5":  {"long": 30.4, "short": 32.0},
    "TP1_5_SL0_75": {"long": 30.5, "short": 31.7},
    "TP2_0_SL1_0":  {"long": 30.0, "short": 30.7},
}

MIN_N = 30
SLOW_QUERY_THRESHOLD = 5.0  # seconds — warn if exceeded (queries should be <5s)


def wilson_interval(n: int, k: int, z: float = 1.96) -> tuple[float, float]:
    """
    95% Wilson score interval for binomial proportion.
    n = total observations, k = successes.
    Returns (lower, upper) as percentages 0-100.
    Returns (0.0, 0.0) if n == 0.
    """
    if n == 0:
        return 0.0, 0.0
    phat = k / n
    denom = 1.0 + z * z / n
    centre = phat + z * z / (2 * n)
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)
    lower = (centre - margin) / denom * 100.0
    upper = (centre + margin) / denom * 100.0
    return round(max(0.0, lower), 2), round(min(100.0, upper), 2)


def sample_quality(n: int) -> str:
    if n >= 100:
        return "high_confidence"
    if n >= 30:
        return "moderate_confidence"
    if n >= 10:
        return "low_confidence"
    return "insufficient_data"


def _build_row(
    group_key: Any,
    n: int,
    tp_hit: int,
    avg_mfe: float | None,
    avg_mae: float | None,
    avg_time_to_tp: float | None,
    template: str,
    side: str,
) -> dict:
    baseline = BASELINES[template][side]
    n = int(n or 0)
    tp_hit = int(tp_hit or 0)
    tp_rate = (tp_hit / n * 100) if n > 0 else 0.0
    lo, hi = wilson_interval(n, tp_hit)
    return {
        "group_key":              str(group_key),
        "n":                      n,
        "tp_hit":                 tp_hit,
        "tp_rate":                round(tp_rate, 2),
        "wilson_lower":           lo,
        "wilson_upper":           hi,
        "sample_quality":         sample_quality(n),
        "avg_mfe_pct":            round(float(avg_mfe), 4) if avg_mfe is not None else None,
        "avg_mae_pct":            round(float(avg_mae), 4) if avg_mae is not None else None,
        "avg_time_to_tp_minutes": round(float(avg_time_to_tp), 1) if avg_time_to_tp is not None else None,
        "edge_delta":             round(tp_rate - baseline, 2),
        "template":               template,
        "side":                   side,
        "baseline_rate":          baseline,
    }


def analyze_factor_group(
    con: sqlite3.Connection,
    group_col: str,
    where_clause: str,
    params: list,
    template: str,
    side: str,
    min_n: int = MIN_N,
) -> list[dict]:
    """
    Issue one SQLite aggregate query against candle_features for a single
    (template, side) pair.

    group_col:    SQL expression for the GROUP BY column (constants only — no user input)
    where_clause: SQL fragment for WHERE (may contain ? placeholders)
    params:       bound values for ? placeholders in where_clause

    Returns rows sorted by edge_delta descending. Only rows with n >= min_n.
    """
    tc = TEMPLATE_COL[template]
    sc = SIDE_COL[side]
    tp_col  = f"{tc}_{sc}_tp"
    mfe_col = f"{tc}_{sc}_mfe"
    mae_col = f"{tc}_{sc}_mae"
    ttp_col = f"{tc}_{sc}_ttp"

    where = f"WHERE {where_clause}" if where_clause else ""
    sql = f"""
        SELECT
            {group_col} AS group_key,
            COUNT(*) AS n,
            SUM({tp_col}) AS tp_hit,
            AVG({mfe_col}) AS avg_mfe,
            AVG({mae_col}) AS avg_mae,
            AVG(CASE WHEN {tp_col} = 1 THEN {ttp_col} END) AS avg_ttp
        FROM candle_features
        {where}
        GROUP BY group_key
        HAVING COUNT(*) >= {min_n}
    """
    rows = con.execute(sql, list(params)).fetchall()
    results = [
        _build_row(r[0], r[1], r[2], r[3], r[4], r[5], template, side)
        for r in rows
        if r[0] is not None
    ]
    results.sort(key=lambda x: x["edge_delta"], reverse=True)
    return results


def _run_for_templates(
    con: sqlite3.Connection,
    group_col: str,
    where_clause: str,
    params: list,
    templates: list[str] | None = None,
) -> dict:
    """Run analyze_factor_group for all (template, side) combinations."""
    tmpl_list = templates or TEMPLATES
    results = {}
    for template in tmpl_list:
        for side in SIDES:
            key = f"{template}.{side}"
            results[key] = analyze_factor_group(
                con, group_col, where_clause, params, template, side
            )
    return results


# ---------------------------------------------------------------------------
# Group 1 — Volatility regime
# ---------------------------------------------------------------------------

def analyze_volatility_regime(
    con: sqlite3.Connection, templates: list[str] | None = None
) -> dict:
    return _run_for_templates(
        con,
        group_col="volatility_regime",
        where_clause="volatility_regime IS NOT NULL",
        params=[],
        templates=templates,
    )


# ---------------------------------------------------------------------------
# Group 2 — Trend state
# ---------------------------------------------------------------------------

def analyze_trend_state(
    con: sqlite3.Connection, templates: list[str] | None = None
) -> dict:
    return _run_for_templates(
        con,
        group_col="trend_state",
        where_clause="trend_state IS NOT NULL",
        params=[],
        templates=templates,
    )


# ---------------------------------------------------------------------------
# Group 3 — Compression state
# ---------------------------------------------------------------------------

def analyze_compression_state(
    con: sqlite3.Connection, templates: list[str] | None = None
) -> dict:
    return _run_for_templates(
        con,
        group_col="compression_state",
        where_clause="compression_state IS NOT NULL",
        params=[],
        templates=templates,
    )


# ---------------------------------------------------------------------------
# Group 4 — Volatility regime × trend state (cross)
# ---------------------------------------------------------------------------

def analyze_regime_x_trend(
    con: sqlite3.Connection, templates: list[str] | None = None
) -> dict:
    return _run_for_templates(
        con,
        group_col="volatility_regime || '_' || trend_state",
        where_clause="volatility_regime IS NOT NULL AND trend_state IS NOT NULL",
        params=[],
        templates=templates,
    )


# ---------------------------------------------------------------------------
# Group 5 — RSI decile
# ---------------------------------------------------------------------------

def analyze_rsi_decile(
    con: sqlite3.Connection, templates: list[str] | None = None
) -> dict:
    return _run_for_templates(
        con,
        group_col="rsi_decile",
        where_clause="rsi_decile IS NOT NULL",
        params=[],
        templates=templates,
    )


# ---------------------------------------------------------------------------
# Group 6 — Volume decile
# ---------------------------------------------------------------------------

def analyze_volume_decile(
    con: sqlite3.Connection, templates: list[str] | None = None
) -> dict:
    return _run_for_templates(
        con,
        group_col="volume_decile",
        where_clause="volume_decile IS NOT NULL",
        params=[],
        templates=templates,
    )


# ---------------------------------------------------------------------------
# Group 7 — Tag presence
# ---------------------------------------------------------------------------

def analyze_tag_presence(
    con: sqlite3.Connection, templates: list[str] | None = None
) -> dict:
    """
    For each tag: filter to rows where the tag column = 1.
    No LIKE required — flat integer columns.
    """
    tmpl_list = templates or TEMPLATES
    results = {}
    for tag in TAGS:
        col = TAG_COL[tag]
        tag_results = {}
        for template in tmpl_list:
            for side in SIDES:
                key = f"{template}.{side}"
                tag_results[key] = analyze_factor_group(
                    con,
                    group_col=f"'{tag}'",
                    where_clause=f"{col} = 1",
                    params=[],
                    template=template,
                    side=side,
                )
        results[tag] = tag_results
    return results


# ---------------------------------------------------------------------------
# Group 8 — ATR decile
# ---------------------------------------------------------------------------

def analyze_atr_decile(
    con: sqlite3.Connection, templates: list[str] | None = None
) -> dict:
    return _run_for_templates(
        con,
        group_col="atr_decile",
        where_clause="atr_decile IS NOT NULL",
        params=[],
        templates=templates,
    )
