"""Strategy-conditioned, net paper validation for Edge Lab.

This layer answers whether an Edge Lab condition adds value *inside* an MT7
strategy's admitted trades. It reads signals.db, never writes to it, and emits
review-only suggestion drafts with explicit risk impact.
"""
from __future__ import annotations

import json
import hashlib
import math
import sqlite3
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from edge_lab.factor_engine import SIDE_COL, TEMPLATE_COL, TEMPLATES

EVALUATOR_VERSION = "edge_strategy_validation_v1"


def evaluate_strategy_economics(
    signals_db: Path,
    *,
    edge_db: Path | None = None,
    min_research_n: int = 10,
    min_candidate_n: int = 30,
) -> dict:
    signals_db = Path(signals_db)
    if not signals_db.exists():
        return {
            "available": False,
            "version": EVALUATOR_VERSION,
            "error": f"signals database not found: {signals_db}",
            "suggestion_drafts": [],
        }
    con = sqlite3.connect(f"file:{signals_db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    if not _table_exists(con, "paper_trades"):
        con.close()
        return {
            "available": False,
            "version": EVALUATOR_VERSION,
            "error": "paper_trades table is missing",
            "suggestion_drafts": [],
        }
    rows = [dict(row) for row in con.execute("""
        SELECT p.id, p.symbol, p.strategy_key, p.direction, p.result,
               p.pnl_pct, p.size_usd, p.leverage, p.conviction,
               p.flow_confirmed, p.flow_score, p.atr_pct, p.trend_score,
               p.fee_cost_pct, p.slippage_cost_pct, p.opened_at, p.closed_at,
               s.funding_rate, s.volatility, s.signal_json
        FROM paper_trades AS p
        LEFT JOIN signals AS s ON s.id=p.signal_id
        WHERE p.status='closed' AND p.pnl_pct IS NOT NULL
        ORDER BY COALESCE(p.closed_at,p.opened_at), p.id
    """).fetchall()]
    blocked = _blocked_opportunity_summary(con)
    con.close()
    if edge_db is not None:
        blocked["counterfactual_paths"] = _counterfactual_paths(
            signals_db, Path(edge_db)
        )
        blocked["outcomes_available"] = bool(
            blocked["counterfactual_paths"].get("matched_candidates")
        )
        blocked["note"] = (
            "Counterfactual outcomes use the latest fully closed pre-candidate "
            "v2 candle state. They estimate gate efficacy; they are not trades."
        )

    prepared = [_prepare(row) for row in rows]
    strategy_totals: dict[str, list[dict]] = defaultdict(list)
    condition_rows: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in prepared:
        strategy_totals[row["strategy_key"]].append(row)
        for condition in row["conditions"]:
            condition_rows[
                (row["strategy_key"], row["direction"], condition)
            ].append(row)

    validations = []
    for (strategy, direction, condition), selected in condition_rows.items():
        if len(selected) < min_research_n:
            continue
        control = [
            row for row in strategy_totals[strategy]
            if row["direction"] == direction and condition not in row["conditions"]
        ]
        selected_stats = _stats(selected)
        control_stats = _stats(control)
        uplift = None
        if (
            selected_stats["avg_net_pnl_pct"] is not None
            and control_stats["avg_net_pnl_pct"] is not None
        ):
            uplift = round(
                selected_stats["avg_net_pnl_pct"]
                - control_stats["avg_net_pnl_pct"],
                4,
            )
        tier = "research_only"
        if (
            len(selected) >= min_candidate_n
            and len(control) >= min_candidate_n
            and uplift is not None and uplift > 0
            and float(selected_stats.get("profit_factor") or 0.0) >= 1.25
            and float(selected_stats.get("avg_net_pnl_pct") or 0.0) > 0
        ):
            tier = "paper_confirmation_candidate"
        validations.append({
            "strategy_key": strategy,
            "direction": direction,
            "condition": condition,
            "condition_stats": selected_stats,
            "matched_control_stats": control_stats,
            "net_ev_uplift_pct": uplift,
            "evidence_tier": tier,
            "selection_warning": (
                "Observed association inside admitted paper trades; "
                "not a causal estimate and not evidence for blocked candidates."
            ),
        })
    validations.sort(key=lambda row: (
        row["evidence_tier"] == "paper_confirmation_candidate",
        row.get("net_ev_uplift_pct")
        if row.get("net_ev_uplift_pct") is not None else -999,
        row["condition_stats"]["count"],
    ), reverse=True)
    drafts = [
        _suggestion_draft(row)
        for row in validations
        if row["evidence_tier"] == "paper_confirmation_candidate"
    ]
    return {
        "available": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": EVALUATOR_VERSION,
        "authority_mode": "shadow_read_only",
        "authority_eligible": False,
        "closed_trade_count": len(prepared),
        "strategies": {
            key: _stats(value) for key, value in sorted(strategy_totals.items())
        },
        "validations": validations[:100],
        "blocked_opportunities": blocked,
        "suggestion_drafts": drafts[:20],
        "limitations": [
            "Paper trades are selected by existing MT7 admission logic.",
            "Conditions are evaluated within strategy and direction but remain observational.",
            "Funding history is available only where captured with the original signal.",
            "No draft can change scoring, sizing, leverage, paper, or live execution.",
        ],
    }


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    return bool(con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone())


def _blocked_opportunity_summary(con: sqlite3.Connection) -> dict:
    if not _table_exists(con, "filtered_candidates"):
        return {"count": 0, "by_strategy": {}, "by_gate": {}}
    rows = con.execute("""
        SELECT strategy_key, gate_key, COUNT(*)
        FROM filtered_candidates
        GROUP BY strategy_key, gate_key
    """).fetchall()
    by_strategy: dict[str, int] = defaultdict(int)
    by_gate: dict[str, int] = defaultdict(int)
    for strategy, gate, count in rows:
        by_strategy[str(strategy or "unknown")] += int(count or 0)
        by_gate[str(gate or "unknown")] += int(count or 0)
    return {
        "count": sum(by_strategy.values()),
        "by_strategy": dict(sorted(by_strategy.items())),
        "by_gate": dict(sorted(by_gate.items())),
        "outcomes_available": False,
        "note": "Blocked candidates are counted but require counterfactual path labels before efficacy can be estimated.",
    }


def _counterfactual_paths(
    signals_db: Path,
    edge_db: Path,
    *,
    max_age_seconds: int = 1800,
    max_candidates: int = 100_000,
) -> dict:
    """Match rejected candidates to pre-existing v2 paths without lookahead."""
    if not edge_db.exists():
        return {
            "available": False,
            "matched_candidates": 0,
            "reason": "edge database missing",
            "results": [],
        }
    signals_con = sqlite3.connect(f"file:{signals_db}?mode=ro", uri=True)
    signals_con.row_factory = sqlite3.Row
    try:
        if not _table_exists(signals_con, "filtered_candidates"):
            return {
                "available": False,
                "matched_candidates": 0,
                "reason": "filtered_candidates table missing",
                "results": [],
            }
        candidates = [dict(row) for row in signals_con.execute(
            """
            SELECT id, logged_at, symbol, direction, strategy_key, gate_key
            FROM filtered_candidates
            WHERE logged_at >= datetime('now','-90 days')
            ORDER BY logged_at DESC
            LIMIT ?
            """,
            (max_candidates,),
        ).fetchall()]
    finally:
        signals_con.close()
    parsed = []
    for row in candidates:
        epoch = _iso_epoch(row.get("logged_at"))
        direction = str(row.get("direction") or "").lower()
        if epoch is None or direction not in SIDE_COL:
            continue
        row["epoch"] = epoch
        row["direction"] = direction
        parsed.append(row)
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for row in parsed:
        by_symbol[str(row.get("symbol") or "")].append(row)

    edge_con = sqlite3.connect(f"file:{edge_db}?mode=ro", uri=True)
    edge_con.row_factory = sqlite3.Row
    aggregates: dict[tuple[str, str, str, str], dict] = defaultdict(
        lambda: {
            "matched": 0, "resolved": 0, "ambiguous": 0,
            "gross_sum": 0.0, "positive": 0,
        }
    )
    matched_ids = set()
    try:
        columns = {
            row[1] for row in edge_con.execute(
                "PRAGMA table_info(candle_features)"
            ).fetchall()
        }
        for symbol, symbol_candidates in by_symbol.items():
            lower = min(row["epoch"] for row in symbol_candidates) - max_age_seconds
            upper = max(row["epoch"] for row in symbol_candidates)
            feature_rows = [dict(row) for row in edge_con.execute(
                """
                SELECT *
                FROM candle_features
                WHERE symbol=? AND timeframe='Min15'
                  AND timestamp BETWEEN ? AND ?
                  AND label_version='edge_path_v2'
                ORDER BY timestamp
                """,
                (symbol, lower, upper),
            ).fetchall()]
            if not feature_rows:
                continue
            timestamps = [int(row["timestamp"]) for row in feature_rows]
            for candidate in symbol_candidates:
                index = bisect_right(timestamps, candidate["epoch"]) - 1
                if index < 0:
                    continue
                state = feature_rows[index]
                if candidate["epoch"] - int(state["timestamp"]) > max_age_seconds:
                    continue
                matched_ids.add(int(candidate["id"]))
                for template in TEMPLATES:
                    stem = (
                        f"{TEMPLATE_COL[template]}_"
                        f"{SIDE_COL[candidate['direction']]}"
                    )
                    if f"{stem}_gross" not in columns:
                        continue
                    key = (
                        str(candidate.get("strategy_key") or "unknown"),
                        str(candidate.get("gate_key") or "unknown"),
                        template,
                        candidate["direction"].upper(),
                    )
                    agg = aggregates[key]
                    agg["matched"] += 1
                    if int(state.get(f"{stem}_ambig") or 0):
                        agg["ambiguous"] += 1
                        continue
                    gross = _number(state.get(f"{stem}_gross"))
                    if gross is None:
                        continue
                    agg["resolved"] += 1
                    agg["gross_sum"] += gross
                    agg["positive"] += int(gross > 0)
    finally:
        edge_con.close()

    cost_pct = _edge_cost_pct()
    results = []
    for (strategy, gate, template, direction), agg in aggregates.items():
        resolved = int(agg["resolved"])
        gross_ev = agg["gross_sum"] / resolved if resolved else None
        results.append({
            "strategy_key": strategy,
            "gate_key": gate,
            "template": template,
            "direction": direction,
            "matched_n": int(agg["matched"]),
            "resolved_n": resolved,
            "ambiguous_n": int(agg["ambiguous"]),
            "positive_rate": (
                round(agg["positive"] / resolved, 4) if resolved else None
            ),
            "gross_expectancy_pct": (
                round(gross_ev, 4) if gross_ev is not None else None
            ),
            "round_trip_cost_pct": cost_pct,
            "net_expectancy_pct": (
                round(gross_ev - cost_pct, 4)
                if gross_ev is not None else None
            ),
            "interpretation": (
                "Positive values mean the rejected candidates later had "
                "favorable path economics; that can indicate an over-strict gate."
            ),
        })
    results.sort(key=lambda row: (
        int(row["resolved_n"]),
        float(row["net_expectancy_pct"] or -999),
    ), reverse=True)
    return {
        "available": True,
        "candidate_count": len(parsed),
        "matched_candidates": len(matched_ids),
        "coverage_pct": round(
            len(matched_ids) / len(parsed) * 100.0, 2
        ) if parsed else 0.0,
        "max_match_age_minutes": max_age_seconds // 60,
        "label_version_required": "edge_path_v2",
        "authority_mode": "shadow_read_only",
        "results": results[:100],
    }


def _iso_epoch(value) -> int | None:
    try:
        text = str(value or "").strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except (TypeError, ValueError):
        return None


def _edge_cost_pct() -> float:
    import os
    try:
        return max(
            0.0,
            float(os.getenv("EDGE_LAB_ROUND_TRIP_COST_BPS") or "7") / 100.0,
        )
    except ValueError:
        return 0.07


def _prepare(row: dict) -> dict:
    try:
        payload = json.loads(row.get("signal_json") or "{}")
    except Exception:
        payload = {}
    funding = _number(row.get("funding_rate"))
    conditions = {
        f"volatility:{str(row.get('volatility') or payload.get('volatility_regime') or 'unknown').lower()}",
        f"flow_confirmed:{bool(row.get('flow_confirmed'))}",
    }
    agent_regime = str(payload.get("agent_regime") or "").strip().lower()
    if agent_regime:
        conditions.add(f"agent_regime:{agent_regime}")
    if funding is not None:
        if funding <= -0.0005:
            bucket = "extreme_negative"
        elif funding < 0:
            bucket = "negative"
        elif funding >= 0.0005:
            bucket = "extreme_positive"
        else:
            bucket = "positive"
        conditions.add(f"funding:{bucket}")
    liquidity_payload = payload.get("liquidity")
    liquidity = str(
        payload.get("liquidity_tier")
        or (
            liquidity_payload.get("tier")
            if isinstance(liquidity_payload, dict) else ""
        )
    ).strip().lower()
    if liquidity:
        conditions.add(f"liquidity:{liquidity}")
    pnl_pct = float(row.get("pnl_pct") or 0.0)
    size_usd = float(row.get("size_usd") or 0.0)
    return {
        **row,
        "strategy_key": str(row.get("strategy_key") or "unknown"),
        "direction": str(row.get("direction") or "unknown").upper(),
        "pnl_pct": pnl_pct,
        "pnl_usd": size_usd * pnl_pct / 100.0,
        "conditions": sorted(conditions),
    }


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _stats(rows: list[dict]) -> dict:
    pnls = [float(row.get("pnl_pct") or 0.0) for row in rows]
    dollars = [float(row.get("pnl_usd") or 0.0) for row in rows]
    gains = sum(value for value in dollars if value > 0)
    losses = abs(sum(value for value in dollars if value < 0))
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for value in dollars:
        equity += value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return {
        "count": len(rows),
        "avg_net_pnl_pct": round(sum(pnls) / len(pnls), 4) if pnls else None,
        "total_net_pnl_usd": round(sum(dollars), 2),
        "positive_rate": round(
            sum(1 for value in dollars if value > 0) / len(dollars), 4
        ) if dollars else None,
        "profit_factor": round(gains / losses, 4) if losses else (
            999.0 if gains else None
        ),
        "max_drawdown_usd": round(abs(drawdown), 2),
    }


def _suggestion_draft(validation: dict) -> dict:
    stats = validation["condition_stats"]
    identity = "|".join([
        validation["strategy_key"],
        validation["direction"],
        validation["condition"],
        EVALUATOR_VERSION,
    ])
    return {
        "id": "edge_lab_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16],
        "candidate_type": "edge_lab_shadow_filter",
        "status": "draft_review_only",
        "strategy_key": validation["strategy_key"],
        "direction": validation["direction"],
        "condition": validation["condition"],
        "expected_net_ev_uplift_pct": validation.get("net_ev_uplift_pct"),
        "paper_sample_n": stats.get("count"),
        "paper_profit_factor": stats.get("profit_factor"),
        "paper_max_drawdown_usd": stats.get("max_drawdown_usd"),
        "risk_impact": {
            "trade_frequency": "may_decrease",
            "position_size_change": "none",
            "leverage_change": "none",
            "account_exposure_change": "none",
            "automatic_risk_increase": False,
            "plain_language": (
                "This draft only proposes testing an entry filter. It does not "
                "raise leverage, position size, or account exposure."
            ),
        },
        "required_next_step": (
            "Register an untouched shadow cohort, then require explicit user "
            "approval before any Paper configuration change."
        ),
        "authority_mode": "shadow_read_only",
        "authority_eligible": False,
    }
