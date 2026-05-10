#!/usr/bin/env python3
"""
Standalone read-only audit of closed signals in data/signals.db.

Usage:  python3 analyze.py
Output: formatted terminal report + data/audit_report.json

Does NOT import from app.py. Does NOT make API calls.
Does NOT write to data/signals.db (read-only).

Schema notes (verified against live schema):
  - Volatility stored as column "volatility" (not "volatility_regime")
  - Tags stored as comma-separated string (not JSON)
  - tp1/entry1/leverage are direct columns; signal_json used only as
    fallback source for leverage when the column is NULL
"""

import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

DB_PATH     = Path(__file__).parent / "data" / "signals.db"
OUTPUT_PATH = Path(__file__).parent / "data" / "audit_report.json"

REGIMES = ["low", "medium", "high", "extreme", "unknown"]
CONV_BANDS = [
    ("55-64",  55, 65),
    ("65-74",  65, 75),
    ("75-84",  75, 85),
    ("85+",    85, 999),
]

W = 76  # report width

MIN_RECOMMENDATION_N = 10
PROMOTE_WILSON_FLOOR = 35.0
CAUTION_WILSON_FLOOR = 25.0
REVERSE_DELTA_FLOOR = 50.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sep() -> None:
    print("─" * W)


def pct_str(n: int, d: int) -> str:
    return f"{n / d * 100:5.1f}%" if d > 0 else "  n/a "


def fmt_pnl(val) -> str:
    if val is None:
        return "     n/a"
    return f"{val:+8.1f}"


def utc_now_z() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"


def parse_tags(tags_str) -> list:
    if not tags_str:
        return []
    return [t.strip() for t in str(tags_str).split(",") if t.strip()]


def row_leverage(row: dict):
    leverage = row.get("leverage")
    if leverage is None:
        sig_json = row.get("signal_json")
        if sig_json:
            try:
                leverage = json.loads(sig_json).get("leverage_cap")
            except Exception:
                pass
    try:
        return float(leverage) if leverage is not None else None
    except (TypeError, ValueError):
        return None


def sample_quality(n: int) -> str:
    if n >= 100:
        return "high_confidence"
    if n >= 30:
        return "moderate_confidence"
    if n >= 10:
        return "low_confidence"
    return "insufficient_data"


def wilson_interval(wins: int, n: int, z: float = 1.96) -> tuple:
    if n <= 0:
        return None, None

    phat = wins / n
    denom = 1 + (z * z / n)
    centre = phat + (z * z / (2 * n))
    margin = z * math.sqrt((phat * (1 - phat) + (z * z / (4 * n))) / n)
    lower = (centre - margin) / denom * 100
    upper = (centre + margin) / denom * 100
    return round(lower, 1), round(upper, 1)


def agg(rows: list) -> dict:
    n = len(rows)
    quality = sample_quality(n)
    if n == 0:
        return {"n": 0, "win_rate": None, "partial_rate": None,
                "avg_pnl": None, "total_pnl": None,
                "wilson_lower": None, "wilson_upper": None,
                "sample_quality": quality}
    wins     = sum(1 for r in rows if r["result"] == "WIN")
    partials = sum(1 for r in rows if r["result"] == "PARTIAL")
    pnl_vals = [r["pnl_pct"] for r in rows if r["pnl_pct"] is not None]
    wilson_lower, wilson_upper = wilson_interval(wins, n)
    return {
        "n":            n,
        "win_rate":     wins / n * 100,
        "partial_rate": partials / n * 100,
        "avg_pnl":      sum(pnl_vals) / len(pnl_vals) if pnl_vals else None,
        "total_pnl":    sum(pnl_vals) if pnl_vals else None,
        "wilson_lower": wilson_lower,
        "wilson_upper": wilson_upper,
        "sample_quality": quality,
    }


def print_agg_header(col1: str = "label", col1_w: int = 20) -> None:
    print(f"  {col1:<{col1_w}} {'n':>5}  {'win%':>6}  {'par%':>6}  {'avg_pnl':>8}  {'total_pnl':>10}")
    sep()


def print_agg_row(label: str, a: dict, col1_w: int = 20) -> None:
    if a["n"] == 0:
        return
    wr = f"{a['win_rate']:5.1f}%" if a["win_rate"] is not None else "  n/a "
    pr = f"{a['partial_rate']:5.1f}%" if a["partial_rate"] is not None else "  n/a "
    ap = fmt_pnl(a["avg_pnl"])
    tp = fmt_pnl(a["total_pnl"])
    print(f"  {label:<{col1_w}} {a['n']:>5}  {wr}  {pr}  {ap}  {tp}")


# ---------------------------------------------------------------------------
# Data load
# ---------------------------------------------------------------------------

def load_signals() -> tuple:
    """
    Returns (rows_with_pnl, excluded_count).
    Opens DB read-only via URI mode.
    """
    uri = f"file:{DB_PATH}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row

    rows = [dict(r) for r in con.execute(
        "SELECT * FROM signals WHERE result IS NOT NULL AND pnl_pct IS NOT NULL"
    ).fetchall()]

    excluded = con.execute(
        "SELECT COUNT(*) FROM signals WHERE result IS NOT NULL AND pnl_pct IS NULL"
    ).fetchone()[0]

    con.close()

    # Pre-parse tags list once so we never re-split in inner loops
    for r in rows:
        r["_tags"] = parse_tags(r.get("tags"))
        # Normalise strategy_key: fall back to 'balanced' if NULL
        if not r.get("strategy_key"):
            r["strategy_key"] = "balanced"

    return rows, excluded


# ---------------------------------------------------------------------------
# Section 1: Volatility regime breakdown
# ---------------------------------------------------------------------------

def section_regime(rows: list, strategy_key=None) -> dict:
    subset = rows if strategy_key is None else [r for r in rows if r["strategy_key"] == strategy_key]
    result = {}
    for regime in REGIMES:
        if regime == "unknown":
            group = [r for r in subset if not r.get("volatility")]
        else:
            group = [r for r in subset if r.get("volatility") == regime]
        result[regime] = agg(group)
    return result


# ---------------------------------------------------------------------------
# Section 2: Conviction band breakdown
# ---------------------------------------------------------------------------

def section_conviction(rows: list, strategy_key=None) -> dict:
    subset = rows if strategy_key is None else [r for r in rows if r["strategy_key"] == strategy_key]
    result = {}
    for label, lo, hi in CONV_BANDS:
        group = [r for r in subset if lo <= (r.get("conviction") or 0) < hi]
        result[label] = agg(group)
    return result


# ---------------------------------------------------------------------------
# Section 3: Tag breakdown
# ---------------------------------------------------------------------------

def section_tags(rows: list, strategy_key=None) -> list:
    subset = rows if strategy_key is None else [r for r in rows if r["strategy_key"] == strategy_key]

    all_tags = set()
    for r in subset:
        all_tags.update(r["_tags"])

    if not all_tags:
        return []

    # Build per-tag membership once (set of ids that have each tag)
    tag_ids: dict = defaultdict(set)
    for r in subset:
        for t in r["_tags"]:
            tag_ids[t].add(r["id"])

    tag_results = []
    for tag in sorted(all_tags):
        ids_with = tag_ids[tag]
        with_rows    = [r for r in subset if r["id"] in ids_with]
        without_rows = [r for r in subset if r["id"] not in ids_with]
        wa = agg(with_rows)
        wo = agg(without_rows)
        delta = (wa["total_pnl"] or 0.0) - (wo["total_pnl"] or 0.0)
        tag_results.append({
            "tag":        tag,
            "with":       wa,
            "without":    wo,
            "edge_delta": delta,
        })

    tag_results.sort(key=lambda x: x["edge_delta"], reverse=True)
    return tag_results


# ---------------------------------------------------------------------------
# Section 4: Symbol blacklist candidates
# ---------------------------------------------------------------------------

def section_blacklist(rows: list) -> list:
    groups: dict = defaultdict(list)
    for r in rows:
        groups[(r["symbol"], r["strategy_key"])].append(r)

    candidates = []
    for (symbol, strat_key), group in groups.items():
        n = len(group)
        if n < 3:
            continue
        a = agg(group)
        win_rate  = a["win_rate"] or 0.0
        total_pnl = a["total_pnl"] or 0.0
        if win_rate < 25.0 or total_pnl < -100.0:
            candidates.append({
                "symbol":       symbol,
                "strategy_key": strat_key,
                "n_closed":     n,
                "win_rate":     win_rate,
                "total_pnl":    total_pnl,
                "wilson_lower": a["wilson_lower"],
                "wilson_upper": a["wilson_upper"],
                "sample_quality": a["sample_quality"],
            })

    candidates.sort(key=lambda x: x["total_pnl"])
    return candidates


# ---------------------------------------------------------------------------
# Section 5: TP1-only counterfactual (confirmed TP1 hits only)
# ---------------------------------------------------------------------------

def compute_tp1_pnl(row: dict):
    """
    Compute pnl_pct if exit had been 100% at TP1 instead of laddered exits.
    Returns float or None if required fields are missing.
    Uses direct columns: entry1, tp1, direction, leverage.
    Falls back to signal_json.leverage_cap when leverage column is NULL.
    """
    entry1    = row.get("entry1")
    tp1       = row.get("tp1")
    direction = row.get("direction")
    leverage  = row_leverage(row)

    if not entry1 or not tp1 or not direction or not leverage:
        return None
    if entry1 <= 0:
        return None

    if direction == "LONG":
        raw_pct = (tp1 - entry1) / entry1 * 100.0
    else:
        raw_pct = (entry1 - tp1) / entry1 * 100.0

    return raw_pct * float(leverage)


def section_tp1_counterfactual(rows: list, strat_groups: list) -> dict:
    # Gate on confirmed TP1_HIT events only — not all signals with a tp1 column.
    # rows is used only for the total-signal count note; actual computation uses DB.
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    confirmed_rows = con.execute("""
        SELECT s.id, s.strategy_key, s.direction, s.entry1, s.tp1,
               s.pnl_pct, s.leverage, s.signal_json
        FROM signals s
        WHERE EXISTS (
            SELECT 1 FROM position_events pe
            WHERE pe.signal_id = s.id
              AND pe.event_type = 'TP1_HIT'
        )
        AND s.entry1 IS NOT NULL AND s.tp1 IS NOT NULL
        AND s.pnl_pct IS NOT NULL
        AND s.result NOT IN ('EXPIRED', 'SKIPPED')
    """).fetchall()
    con.close()

    confirmed_rows = [dict(r) for r in confirmed_rows]
    n_all_with_tp1 = sum(1 for r in rows if r.get("tp1") is not None)
    n_confirmed    = len(confirmed_rows)

    result = {"_note": f"confirmed TP1_HIT events: {n_confirmed} of {n_all_with_tp1} signals that have a tp1 value"}

    for strat_label in strat_groups:
        subset = confirmed_rows if strat_label == "ALL_STRATEGIES" else [
            r for r in confirmed_rows if r["strategy_key"] == strat_label
        ]
        actual_total = 0.0
        cf_total     = 0.0
        computable   = 0
        skipped      = 0
        for r in subset:
            cf = compute_tp1_pnl(r)
            if cf is not None and r["pnl_pct"] is not None:
                actual_total += r["pnl_pct"]
                cf_total     += cf
                computable   += 1
            else:
                skipped += 1

        result[strat_label] = {
            "n_computable":         computable,
            "n_skipped":            skipped,
            "actual_total":         actual_total    if computable else None,
            "counterfactual_total": cf_total        if computable else None,
            "delta":                (cf_total - actual_total) if computable else None,
        }
    return result


# ---------------------------------------------------------------------------
# Section 6: Direction-flip sanity check
# ---------------------------------------------------------------------------

def section_direction_flip(rows: list, strat_groups: list) -> dict:
    def flip_stats(group: list) -> dict:
        actual = sum(r["pnl_pct"] for r in group if r["pnl_pct"] is not None)
        flipped = -actual
        delta = flipped - actual
        n = len(group)
        improved_materially = delta >= max(REVERSE_DELTA_FLOOR, abs(actual) * 0.5)
        return {
            "n":                    n,
            "actual_total":         actual,
            "flipped_total":        flipped,
            "delta":                delta,
            "actual_avg":           actual / n if n else None,
            "flipped_avg":          flipped / n if n else None,
            "anti_correlated":      flipped > actual,
            "is_reverse_candidate": n >= MIN_RECOMMENDATION_N and actual < 0 and flipped > 0 and improved_materially,
            "sample_quality":       sample_quality(n),
        }

    result = {}
    for strat_label in strat_groups:
        subset = rows if strat_label == "ALL_STRATEGIES" else [
            r for r in rows if r["strategy_key"] == strat_label
        ]
        result[strat_label] = flip_stats(subset)

    strategy_tag = []
    strategy_regime = []
    strategy_keys = sorted(set(r["strategy_key"] for r in rows))
    for strategy_key in strategy_keys:
        strat_rows = [r for r in rows if r["strategy_key"] == strategy_key]

        tags = sorted({t for r in strat_rows for t in r["_tags"]})
        for tag in tags:
            group = [r for r in strat_rows if tag in r["_tags"]]
            if len(group) < MIN_RECOMMENDATION_N:
                continue
            stats = flip_stats(group)
            strategy_tag.append({"strategy": strategy_key, "tag": tag, **stats})

        regimes = sorted({r.get("volatility") or "unknown" for r in strat_rows})
        for regime in regimes:
            group = [
                r for r in strat_rows
                if ((r.get("volatility") or "unknown") == regime)
            ]
            if len(group) < MIN_RECOMMENDATION_N:
                continue
            stats = flip_stats(group)
            strategy_regime.append({"strategy": strategy_key, "regime": regime, **stats})

    strategy_tag.sort(key=lambda x: x["delta"], reverse=True)
    strategy_regime.sort(key=lambda x: x["delta"], reverse=True)
    result["strategy_tag"] = strategy_tag
    result["strategy_regime"] = strategy_regime
    return result


# ---------------------------------------------------------------------------
# Section 7+: Statistical intelligence layers
# ---------------------------------------------------------------------------

def classify_deployment(a: dict, *, regime=None, reverse_stats=None) -> tuple:
    n = a.get("n", 0)
    total_pnl = a.get("total_pnl") or 0.0
    avg_pnl = a.get("avg_pnl") or 0.0
    win_rate = a.get("win_rate") or 0.0
    wilson_lower = a.get("wilson_lower") or 0.0

    if n < MIN_RECOMMENDATION_N:
        return "insufficient_data", "fewer than 10 closed signals"

    if reverse_stats and reverse_stats.get("is_reverse_candidate"):
        return "reverse_candidate", "anti-correlated across sufficient sample size"

    if regime == "extreme" and total_pnl < 0 and avg_pnl < 0:
        return "disable", "persistent negative expectancy in extreme volatility"

    if total_pnl < -100.0 or (win_rate < 25.0 and total_pnl < 0):
        return "disable", "negative expectancy with weak win rate"

    if total_pnl > 0 and avg_pnl > 0 and wilson_lower >= PROMOTE_WILSON_FLOOR:
        return "promote", "positive expectancy with supportive Wilson lower bound"

    if total_pnl > 0 and wilson_lower >= CAUTION_WILSON_FLOOR:
        return "caution", "positive expectancy but confidence remains limited"

    return "caution", "mixed or low-confidence expectancy"


def section_deployment_recommendations(rows: list, all_data: dict) -> list:
    recommendations = []
    reverse_by_strategy = {
        k: v for k, v in all_data["direction_flip"].items()
        if isinstance(v, dict) and k not in ("strategy_tag", "strategy_regime")
    }

    for strategy_key in sorted(set(r["strategy_key"] for r in rows)):
        strat_rows = [r for r in rows if r["strategy_key"] == strategy_key]
        a = agg(strat_rows)
        rec, reason = classify_deployment(
            a,
            reverse_stats=reverse_by_strategy.get(strategy_key),
        )
        recommendations.append({
            "type": "strategy",
            "strategy": strategy_key,
            "recommendation": rec,
            "reason": reason,
            "n": a["n"],
            "win_rate": a["win_rate"],
            "wilson_lower": a["wilson_lower"],
            "total_pnl": a["total_pnl"],
            "sample_quality": a["sample_quality"],
        })

        for tag_data in all_data["tags"].get(strategy_key, []):
            a = tag_data["with"]
            if a["n"] < MIN_RECOMMENDATION_N:
                continue
            reverse_stats = next(
                (
                    item for item in all_data["direction_flip"].get("strategy_tag", [])
                    if item["strategy"] == strategy_key and item["tag"] == tag_data["tag"]
                ),
                None,
            )
            rec, reason = classify_deployment(a, reverse_stats=reverse_stats)
            recommendations.append({
                "type": "strategy_tag",
                "strategy": strategy_key,
                "tag": tag_data["tag"],
                "recommendation": rec,
                "reason": reason,
                "n": a["n"],
                "win_rate": a["win_rate"],
                "wilson_lower": a["wilson_lower"],
                "total_pnl": a["total_pnl"],
                "edge_delta": tag_data["edge_delta"],
                "sample_quality": a["sample_quality"],
            })

        for regime, a in all_data["regimes"].get(strategy_key, {}).items():
            if a["n"] < MIN_RECOMMENDATION_N:
                continue
            reverse_stats = next(
                (
                    item for item in all_data["direction_flip"].get("strategy_regime", [])
                    if item["strategy"] == strategy_key and item["regime"] == regime
                ),
                None,
            )
            rec, reason = classify_deployment(a, regime=regime, reverse_stats=reverse_stats)
            recommendations.append({
                "type": "strategy_regime",
                "strategy": strategy_key,
                "regime": regime,
                "recommendation": rec,
                "reason": reason,
                "n": a["n"],
                "win_rate": a["win_rate"],
                "wilson_lower": a["wilson_lower"],
                "total_pnl": a["total_pnl"],
                "sample_quality": a["sample_quality"],
            })

    order = {
        "disable": 0,
        "reverse_candidate": 1,
        "promote": 2,
        "caution": 3,
        "insufficient_data": 4,
    }
    recommendations.sort(key=lambda x: (order.get(x["recommendation"], 99), x["strategy"], x.get("tag", ""), x.get("regime", "")))
    return recommendations


def section_regime_survival(rows: list) -> list:
    results = []
    for strategy_key in sorted(set(r["strategy_key"] for r in rows)):
        strat_rows = [r for r in rows if r["strategy_key"] == strategy_key]
        medium = agg([r for r in strat_rows if r.get("volatility") == "medium"])
        high = agg([r for r in strat_rows if r.get("volatility") == "high"])
        extreme = agg([r for r in strat_rows if r.get("volatility") == "extreme"])

        medium_pnl = medium["total_pnl"]
        high_pnl = high["total_pnl"]
        medium_avg = medium["avg_pnl"]
        extreme_avg = extreme["avg_pnl"]

        pnl_retention_ratio = None
        if medium_pnl not in (None, 0):
            pnl_retention_ratio = (high_pnl or 0.0) / medium_pnl

        volatility_damage = None
        if medium_avg is not None and extreme_avg is not None:
            volatility_damage = medium_avg - extreme_avg

        score = 50.0
        if pnl_retention_ratio is not None:
            score += max(-30.0, min(30.0, pnl_retention_ratio * 20.0))
        if volatility_damage is not None:
            score -= max(-25.0, min(35.0, volatility_damage / 5.0))
        if (extreme["total_pnl"] or 0.0) < 0:
            score -= 15.0
        if extreme["n"] < MIN_RECOMMENDATION_N:
            score -= 10.0

        regime_stability_score = max(0.0, min(100.0, score))
        flag = (
            extreme["n"] >= MIN_RECOMMENDATION_N
            and (extreme["total_pnl"] or 0.0) < 0
            and (volatility_damage or 0.0) > 0
        )

        results.append({
            "strategy": strategy_key,
            "medium_n": medium["n"],
            "high_n": high["n"],
            "extreme_n": extreme["n"],
            "medium_total_pnl": medium["total_pnl"],
            "high_vol_pnl": high["total_pnl"],
            "extreme_total_pnl": extreme["total_pnl"],
            "pnl_retention_ratio": pnl_retention_ratio,
            "volatility_damage": volatility_damage,
            "regime_stability_score": regime_stability_score,
            "collapses_in_extreme_vol": flag,
            "sample_quality": sample_quality(medium["n"] + high["n"] + extreme["n"]),
        })

    results.sort(key=lambda x: x["regime_stability_score"])
    return results


def section_tag_combinations(rows: list) -> list:
    groups: dict = defaultdict(list)
    baseline = agg(rows)
    baseline_avg = baseline["avg_pnl"] or 0.0

    for r in rows:
        unique_tags = sorted(set(r["_tags"]))
        for pair in combinations(unique_tags, 2):
            groups[pair].append(r)

    results = []
    for pair, group in groups.items():
        if len(group) < MIN_RECOMMENDATION_N:
            continue
        a = agg(group)
        results.append({
            "tags": list(pair),
            "n": a["n"],
            "win_rate": a["win_rate"],
            "avg_pnl": a["avg_pnl"],
            "total_pnl": a["total_pnl"],
            "wilson_lower": a["wilson_lower"],
            "wilson_upper": a["wilson_upper"],
            "sample_quality": a["sample_quality"],
            "edge_delta": (a["avg_pnl"] or 0.0) - baseline_avg,
        })

    results.sort(key=lambda x: (x["edge_delta"], x["total_pnl"] or 0.0), reverse=True)
    return results


def section_extreme_volatility_firebreaks(rows: list) -> list:
    firebreaks = []

    for strategy_key in sorted(set(r["strategy_key"] for r in rows)):
        strat_rows = [r for r in rows if r["strategy_key"] == strategy_key]
        extreme = agg([r for r in strat_rows if r.get("volatility") == "extreme"])
        non_extreme = agg([r for r in strat_rows if r.get("volatility") != "extreme"])
        if extreme["n"] >= MIN_RECOMMENDATION_N and (extreme["avg_pnl"] or 0.0) < 0:
            extreme_rows = [r for r in strat_rows if r.get("volatility") == "extreme"]
            leverage_vals = [row_leverage(r) for r in extreme_rows]
            leverage_vals = [v for v in leverage_vals if v is not None]
            avg_leverage = sum(leverage_vals) / len(leverage_vals) if leverage_vals else None
            max_leverage = max(leverage_vals) if leverage_vals else None
            collapse = (non_extreme["avg_pnl"] or 0.0) - (extreme["avg_pnl"] or 0.0)
            if (extreme["total_pnl"] or 0.0) < 0 or collapse > 10.0:
                affected_tags = []
                tag_counts: dict = defaultdict(int)
                for r in extreme_rows:
                    if (r.get("pnl_pct") or 0.0) < 0:
                        for tag in r["_tags"]:
                            tag_counts[tag] += 1
                affected_tags = [
                    tag for tag, count in sorted(tag_counts.items(), key=lambda x: (-x[1], x[0]))[:5]
                ]
                firebreaks.append({
                    "strategy": strategy_key,
                    "affected_tags": affected_tags,
                    "avg_pnl_collapse": collapse,
                    "extreme_avg_pnl": extreme["avg_pnl"],
                    "extreme_total_pnl": extreme["total_pnl"],
                    "extreme_n": extreme["n"],
                    "avg_leverage": avg_leverage,
                    "max_leverage": max_leverage,
                    "leverage_danger_state": (avg_leverage or 0.0) >= 20.0 or (max_leverage or 0.0) >= 25.0,
                    "recommendation": "disable_above_vol_threshold",
                    "reason": "extreme volatility expectancy collapse",
                    "sample_quality": extreme["sample_quality"],
                })

    tag_groups: dict = defaultdict(list)
    for r in rows:
        if r.get("volatility") == "extreme":
            for tag in r["_tags"]:
                tag_groups[tag].append(r)

    for tag, group in tag_groups.items():
        a = agg(group)
        if a["n"] >= MIN_RECOMMENDATION_N and (a["avg_pnl"] or 0.0) < -10.0:
            leverage_vals = [row_leverage(r) for r in group]
            leverage_vals = [v for v in leverage_vals if v is not None]
            avg_leverage = sum(leverage_vals) / len(leverage_vals) if leverage_vals else None
            max_leverage = max(leverage_vals) if leverage_vals else None
            firebreaks.append({
                "tag": tag,
                "affected_tags": [tag],
                "avg_pnl_collapse": abs(a["avg_pnl"] or 0.0),
                "extreme_avg_pnl": a["avg_pnl"],
                "extreme_total_pnl": a["total_pnl"],
                "extreme_n": a["n"],
                "avg_leverage": avg_leverage,
                "max_leverage": max_leverage,
                "leverage_danger_state": (avg_leverage or 0.0) >= 20.0 or (max_leverage or 0.0) >= 25.0,
                "recommendation": "reduce_or_block_leverage_in_extreme_vol",
                "reason": "tag carries catastrophic downside during extreme volatility",
                "sample_quality": a["sample_quality"],
            })

    firebreaks.sort(key=lambda x: x.get("avg_pnl_collapse") or 0.0, reverse=True)
    return firebreaks


def section_audit_summary(rows: list, all_data: dict) -> dict:
    strategy_aggs = {
        strategy_key: agg([r for r in rows if r["strategy_key"] == strategy_key])
        for strategy_key in sorted(set(r["strategy_key"] for r in rows))
    }
    regime_aggs = {
        regime: agg([
            r for r in rows
            if (r.get("volatility") or "unknown") == regime
        ])
        for regime in REGIMES
    }
    tag_aggs = {}
    for r in rows:
        for tag in r["_tags"]:
            tag_aggs.setdefault(tag, []).append(r)
    tag_aggs = {tag: agg(group) for tag, group in tag_aggs.items()}

    best_strategy = max(strategy_aggs, key=lambda k: strategy_aggs[k]["total_pnl"] or 0.0, default=None)
    worst_strategy = min(strategy_aggs, key=lambda k: strategy_aggs[k]["total_pnl"] or 0.0, default=None)
    safest_regime = max(regime_aggs, key=lambda k: regime_aggs[k]["avg_pnl"] or -999999.0, default=None)
    dangerous_regime = min(regime_aggs, key=lambda k: regime_aggs[k]["avg_pnl"] if regime_aggs[k]["avg_pnl"] is not None else 999999.0, default=None)
    strongest_tag = max(tag_aggs, key=lambda k: tag_aggs[k]["total_pnl"] or 0.0, default=None)
    most_dangerous_tag = min(tag_aggs, key=lambda k: tag_aggs[k]["total_pnl"] or 0.0, default=None)

    reverse_candidates = [
        key for key, data in all_data["direction_flip"].items()
        if isinstance(data, dict) and data.get("is_reverse_candidate")
    ]
    reverse_candidates.extend(
        f"{item['strategy']}+{item['tag']}"
        for item in all_data["direction_flip"].get("strategy_tag", [])
        if item.get("is_reverse_candidate")
    )
    reverse_candidates.extend(
        f"{item['strategy']}+{item['regime']}"
        for item in all_data["direction_flip"].get("strategy_regime", [])
        if item.get("is_reverse_candidate")
    )

    deployment_ready = sorted({
        rec["strategy"] for rec in all_data["deployment_recommendations"]
        if rec["type"] == "strategy" and rec["recommendation"] == "promote"
    })
    disabled = sorted({
        rec["strategy"] for rec in all_data["deployment_recommendations"]
        if rec["type"] == "strategy" and rec["recommendation"] in ("disable", "reverse_candidate")
    })

    return {
        "best_strategy": best_strategy,
        "worst_strategy": worst_strategy,
        "safest_regime": safest_regime,
        "dangerous_regime": dangerous_regime,
        "strongest_tag": strongest_tag,
        "most_dangerous_tag": most_dangerous_tag,
        "reverse_candidates": sorted(set(reverse_candidates)),
        "deployment_ready_strategies": deployment_ready,
        "disabled_strategies": disabled,
    }


# ---------------------------------------------------------------------------
# Report printing
# ---------------------------------------------------------------------------

def print_report(rows: list, excluded: int, all_data: dict) -> None:
    strategy_keys = sorted(set(r["strategy_key"] for r in rows))
    strat_groups  = ["ALL_STRATEGIES"] + strategy_keys

    print(f"\n{'='*W}")
    print("  Matrix Trader 7.0 — Closed Signal Audit Report")
    print(f"  Closed signals analyzed : {len(rows)}")
    if excluded:
        print(f"  Excluded (pnl_pct NULL) : {excluded}")
    print(f"  Strategies present      : {', '.join(strategy_keys) or 'none'}")
    print(f"  Generated               : {utc_now_z()}")
    print(f"{'='*W}")

    for strat_label in strat_groups:
        strat_rows = rows if strat_label == "ALL_STRATEGIES" else [
            r for r in rows if r["strategy_key"] == strat_label
        ]
        print(f"\n{'='*W}")
        print(f"  STRATEGY: {strat_label}  (n={len(strat_rows)})")
        print(f"{'='*W}")

        # --- Section 1 ---
        print(f"\n  SECTION 1 — Volatility Regime Breakdown")
        print_agg_header("regime")
        regime_data = all_data["regimes"][strat_label]
        any_printed = False
        for regime in REGIMES:
            a = regime_data.get(regime, {"n": 0})
            if a["n"] > 0:
                print_agg_row(regime, a)
                any_printed = True
        if not any_printed:
            print("  (no data)")

        # --- Section 2 ---
        print(f"\n  SECTION 2 — Conviction Band Breakdown")
        print_agg_header("band")
        conv_data = all_data["conviction"][strat_label]
        any_printed = False
        for label, _, _ in CONV_BANDS:
            a = conv_data.get(label, {"n": 0})
            if a["n"] > 0:
                print_agg_row(label, a)
                any_printed = True
        if not any_printed:
            print("  (no data)")

        # --- Section 3 ---
        print(f"\n  SECTION 3 — Tag Breakdown (ranked by edge delta: with - without)")
        tag_data = all_data["tags"][strat_label]
        if not tag_data:
            sep()
            print("  (no tagged signals)")
        else:
            C1 = 26
            print(f"  {'tag':<{C1}} {'n_w':>5}  {'win_w':>6}  {'tot_w':>8}  "
                  f"{'n_wo':>5}  {'win_wo':>6}  {'tot_wo':>8}  {'delta':>9}")
            sep()
            for t in tag_data:
                w  = t["with"]
                wo = t["without"]
                wr_w  = pct_str(int(w["win_rate"]  * w["n"]  / 100 + 0.5),  w["n"])  if w["n"]  else "  n/a "
                wr_wo = pct_str(int(wo["win_rate"] * wo["n"] / 100 + 0.5),  wo["n"]) if wo["n"] else "  n/a "
                tp_w  = fmt_pnl(w["total_pnl"])
                tp_wo = fmt_pnl(wo["total_pnl"])
                delta = fmt_pnl(t["edge_delta"])
                print(f"  {t['tag']:<{C1}} {w['n']:>5}  {wr_w}  {tp_w}  "
                      f"{wo['n']:>5}  {wr_wo}  {tp_wo}  {delta}")

    # --- Section 4 (global) ---
    print(f"\n{'='*W}")
    print(f"  SECTION 4 — Symbol Blacklist Candidates  (n>=3 AND win_rate<25% OR total_pnl<-100)")
    sep()
    blacklist = all_data["blacklist"]
    if not blacklist:
        print("  (none — no symbols qualify)")
    else:
        print(f"  {'symbol':<18} {'strategy':<22} {'n':>4}  {'win%':>6}  {'total_pnl':>10}")
        sep()
        for b in blacklist:
            print(f"  {b['symbol']:<18} {b['strategy_key']:<22} {b['n_closed']:>4}  "
                  f"{b['win_rate']:>5.1f}%  {b['total_pnl']:>+10.1f}")

    # --- Section 5 ---
    print(f"\n{'='*W}")
    print(f"  SECTION 5 — TP1-Only Counterfactual  (confirmed TP1 hits only; +delta = TP1 would be better than laddered)")
    tp1 = all_data["tp1_counterfactual"]
    if tp1.get("_note"):
        print(f"  NOTE: {tp1['_note']}")
    sep()
    print(f"  {'strategy':<28} {'n_calc':>6}  {'actual_tot':>11}  {'cf_tp1_tot':>11}  {'delta':>9}")
    sep()
    for sl in ["ALL_STRATEGIES"] + sorted(set(r["strategy_key"] for r in rows)):
        d = tp1.get(sl, {})
        n  = d.get("n_computable", 0)
        at = fmt_pnl(d.get("actual_total"))
        ct = fmt_pnl(d.get("counterfactual_total"))
        dl = fmt_pnl(d.get("delta"))
        sk = d.get("n_skipped", 0)
        note = f"  ({sk} rows skipped — missing entry1/tp1/leverage)" if sk else ""
        print(f"  {sl:<28} {n:>6}  {at}  {ct}  {dl}{note}")

    # --- Section 6 ---
    print(f"\n{'='*W}")
    print(f"  SECTION 6 — Direction-Flip Sanity Check  (pure sign flip, no re-simulation)")
    print(f"  If anti_corr=YES the scorer is anti-correlated with profitable outcomes.")
    sep()
    flip = all_data["direction_flip"]
    print(f"  {'strategy':<28} {'actual_tot':>11}  {'flipped_tot':>11}  {'anti_corr':>10}")
    sep()
    for sl in ["ALL_STRATEGIES"] + sorted(set(r["strategy_key"] for r in rows)):
        d = flip.get(sl, {})
        at = fmt_pnl(d.get("actual_total"))
        ft = fmt_pnl(d.get("flipped_total"))
        ac = "YES ⚠" if d.get("anti_correlated") else "no"
        print(f"  {sl:<28} {at}  {ft}  {ac:>10}")

    # --- Reconciliation ---
    print(f"\n{'='*W}")
    print(f"  RECONCILIATION — Sec1 (regime totals) vs Sec2 (conviction totals) per strategy")
    print(f"  (same trades aggregated two different ways — must match within ±0.5)")
    sep()
    all_ok = True
    strategy_keys = sorted(set(r["strategy_key"] for r in rows))
    for sl in ["ALL_STRATEGIES"] + strategy_keys:
        sl_rows = rows if sl == "ALL_STRATEGIES" else [r for r in rows if r["strategy_key"] == sl]
        sec1 = sum(
            (all_data["regimes"][sl][regime].get("total_pnl") or 0.0)
            for regime in REGIMES
        )
        sec2 = sum(
            (all_data["conviction"][sl][label].get("total_pnl") or 0.0)
            for label, _, _ in CONV_BANDS
        )
        direct = sum(r["pnl_pct"] for r in sl_rows if r["pnl_pct"] is not None)
        diff   = abs(sec1 - sec2)
        status = "OK" if diff <= 0.5 else f"MISMATCH diff={diff:.2f}"
        print(f"  {sl:<28} sec1={sec1:+9.1f}  sec2={sec2:+9.1f}  direct={direct:+9.1f}  [{status}]")
        if diff > 0.5:
            all_ok = False
    print()
    print(f"  Reconciliation: {'PASS' if all_ok else 'FAIL — check data integrity'}")
    print(f"\n{'='*W}\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_audit() -> None:
    if not DB_PATH.exists():
        print(f"[ERROR] Database not found: {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    # Step 1: Schema + sample rows (user sight-check before any aggregations)
    print(f"\n{'='*W}")
    print(f"  [Step 1] Schema inspection — {DB_PATH}")
    print(f"{'='*W}")
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    schema_row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='signals'"
    ).fetchone()
    print(schema_row[0] if schema_row else "(schema not found)")
    print()

    print("  [Sample closed rows — sight-check before aggregations run]")
    con.row_factory = sqlite3.Row
    samples = con.execute(
        "SELECT id, symbol, strategy_key, conviction, volatility, tags, "
        "pnl_pct, leverage, result, direction, entry1, tp1, exit_price "
        "FROM signals WHERE result IS NOT NULL LIMIT 3"
    ).fetchall()
    for r in samples:
        print(" ", dict(r))
    con.close()
    print()

    # Step 2: Load data
    rows, excluded = load_signals()

    if len(rows) == 0:
        count_db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True).execute(
            "SELECT COUNT(*) FROM signals WHERE result IS NOT NULL AND pnl_pct IS NOT NULL"
        ).fetchone()[0]
        print(f"[INFO] No closed signals with pnl_pct found.")
        print(f"       Result-tagged rows with pnl_pct=NULL: {excluded}")
        print(f"       DB confirms count: {count_db}")
        print(f"       Tag outcomes in the app and run /api/backfill/pnl before auditing.")
        OUTPUT_PATH.parent.mkdir(exist_ok=True)
        OUTPUT_PATH.write_text(json.dumps({
            "generated_at":    utc_now_z(),
            "total_analyzed":  0,
            "excluded_no_pnl": excluded,
            "note":            "No closed signals with pnl_pct. Tag outcomes + run backfill first.",
        }, indent=2))
        print(f"\nSaved empty report → {OUTPUT_PATH}")
        return

    strategy_keys = sorted(set(r["strategy_key"] for r in rows))
    strat_groups  = ["ALL_STRATEGIES"] + strategy_keys

    # Steps 3–5: Compute all sections
    all_data: dict = {
        "regimes":                       {},
        "conviction":                    {},
        "tags":                          {},
        "blacklist":                     [],
        "tp1_counterfactual":            {},
        "direction_flip":                {},
        "deployment_recommendations":    [],
        "regime_survival":               [],
        "tag_combinations":              [],
        "extreme_volatility_firebreaks": [],
        "audit_summary":                 {},
    }

    for sl in strat_groups:
        sk = None if sl == "ALL_STRATEGIES" else sl
        all_data["regimes"][sl]    = section_regime(rows, sk)
        all_data["conviction"][sl] = section_conviction(rows, sk)
        all_data["tags"][sl]       = section_tags(rows, sk)

    all_data["blacklist"]          = section_blacklist(rows)
    all_data["tp1_counterfactual"] = section_tp1_counterfactual(rows, strat_groups)
    all_data["direction_flip"]     = section_direction_flip(rows, strat_groups)
    all_data["deployment_recommendations"] = section_deployment_recommendations(rows, all_data)
    all_data["regime_survival"] = section_regime_survival(rows)
    all_data["tag_combinations"] = section_tag_combinations(rows)
    all_data["extreme_volatility_firebreaks"] = section_extreme_volatility_firebreaks(rows)
    all_data["audit_summary"] = section_audit_summary(rows, all_data)

    # Step 6: Print report
    print_report(rows, excluded, all_data)

    # Step 7: Save JSON
    output = {
        "generated_at":    utc_now_z(),
        "total_analyzed":  len(rows),
        "excluded_no_pnl": excluded,
        "sections":        all_data,
    }
    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, indent=2, default=str))
    print(f"Saved → {OUTPUT_PATH}")


if __name__ == "__main__":
    run_audit()
