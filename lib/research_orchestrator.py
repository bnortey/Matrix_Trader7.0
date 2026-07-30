"""Pure helpers for MT7's governed research experiment orchestrator.

The orchestrator deliberately owns no I/O and has no trading authority.  It
normalizes generated ideas into falsifiable contracts, calculates conflict
domains, assigns deterministic Paper arms, and reports scheduler decisions.
Application code remains responsible for persistence and user approvals.
"""

from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime, timezone
from typing import Any


ORCHESTRATOR_VERSION = "mt7_research_orchestrator_v1"

IDEA_TYPES = {
    "new_strategy",
    "entry_gate",
    "risk_gate",
    "regime_filter",
    "stop_rule",
    "exit_rule",
    "sizing_rule",
    "scoring_rule",
    "portfolio_rule",
    "execution_rule",
    "annotation",
    "data_collection",
}

BEHAVIORAL_IDEA_TYPES = IDEA_TYPES - {"annotation", "data_collection"}
VIRTUAL_FIRST_IDEA_TYPES = {
    "stop_rule",
    "exit_rule",
    "sizing_rule",
    "scoring_rule",
    "portfolio_rule",
    "execution_rule",
}

DECISION_SURFACE = {
    "new_strategy": "signal_generation",
    "entry_gate": "entry_eligibility",
    "risk_gate": "entry_eligibility",
    "regime_filter": "entry_eligibility",
    "stop_rule": "loss_management",
    "exit_rule": "profit_management",
    "sizing_rule": "capital_allocation",
    "scoring_rule": "signal_ranking",
    "portfolio_rule": "portfolio_allocation",
    "execution_rule": "order_execution",
    "annotation": "observation",
    "data_collection": "data_collection",
}

TYPE_EVALUATOR = {
    "new_strategy": "isolated_strategy_cohort",
    "entry_gate": "accepted_vs_blocked",
    "risk_gate": "accepted_vs_blocked",
    "regime_filter": "accepted_vs_blocked",
    "stop_rule": "same_entry_path",
    "exit_rule": "same_entry_path",
    "sizing_rule": "virtual_portfolio",
    "scoring_rule": "calibration",
    "portfolio_rule": "chronological_portfolio",
    "execution_rule": "fill_quality",
    "annotation": "observation_only",
    "data_collection": "observation_only",
}

LIFECYCLE_STATES = {
    "discovered",
    "needs_data",
    "ready_for_experiment",
    "queued",
    "awaiting_approval",
    "collecting",
    "review",
    "falsified",
    "inconclusive",
    "promotion_candidate",
    "paper_challenger",
    "paper_champion",
    "live_candidate",
    "confirmed",
    "rolled_back",
    "parked",
}

ALLOWED_TRANSITIONS = {
    "discovered": {"needs_data", "ready_for_experiment", "parked"},
    "needs_data": {"ready_for_experiment", "parked"},
    "ready_for_experiment": {"queued", "awaiting_approval", "parked"},
    "queued": {"awaiting_approval", "parked"},
    "awaiting_approval": {"collecting", "parked"},
    "collecting": {"review", "falsified", "promotion_candidate", "parked"},
    "review": {"collecting", "falsified", "inconclusive", "promotion_candidate", "parked"},
    "promotion_candidate": {"paper_challenger", "parked"},
    "paper_challenger": {"paper_champion", "falsified", "rolled_back", "parked"},
    "paper_champion": {"live_candidate", "review", "rolled_back"},
    "live_candidate": {"confirmed", "rolled_back", "parked"},
    "inconclusive": {"collecting", "parked"},
    "falsified": {"rolled_back", "parked"},
    "rolled_back": set(),
    "confirmed": set(),
    "parked": {"ready_for_experiment"},
}


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _text(value: Any, default: str = "") -> str:
    return str(value if value is not None else default).strip()


def normalize_idea_type(value: Any, *, fallback: str = "annotation") -> str:
    raw = _text(value).lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "strategy": "new_strategy",
        "strategy_change": "new_strategy",
        "filter": "entry_gate",
        "shadow_filter": "entry_gate",
        "entry_filter": "entry_gate",
        "risk_filter": "risk_gate",
        "stop": "stop_rule",
        "stop_management": "stop_rule",
        "exit": "exit_rule",
        "profit_taking": "exit_rule",
        "sizing": "sizing_rule",
        "score": "scoring_rule",
        "conviction": "scoring_rule",
        "portfolio": "portfolio_rule",
        "execution": "execution_rule",
        "context": "annotation",
    }
    normalized = aliases.get(raw, raw)
    return normalized if normalized in IDEA_TYPES else fallback


def normalize_contract(candidate: dict) -> dict:
    """Normalize one generated idea into an explicit experiment contract."""
    candidate = candidate if isinstance(candidate, dict) else {}
    idea_type = normalize_idea_type(
        candidate.get("idea_type")
        or candidate.get("rule_type")
        or candidate.get("candidate_type")
        or candidate.get("type")
    )
    strategy = _text(
        candidate.get("target_strategy")
        or candidate.get("strategy_key")
        or candidate.get("strategy")
        or "portfolio"
    )
    family = _text(
        candidate.get("hypothesis_family")
        or candidate.get("family")
        or candidate.get("research_shadow_tag")
        or candidate.get("shadow_tag")
        or candidate.get("id")
        or idea_type
    )
    family = family.lower().replace(" ", "_")
    surface = DECISION_SURFACE[idea_type]
    fields = candidate.get("required_pre_entry_fields")
    if not isinstance(fields, list):
        readiness = candidate.get("field_readiness") or {}
        fields = list(readiness.get("pre_entry") or readiness.get("partial") or [])
    retrospective = list((candidate.get("field_readiness") or {}).get("retrospective") or [])
    missing = list((candidate.get("field_readiness") or {}).get("missing") or [])
    behavior = _text(
        candidate.get("behavior_changed")
        or candidate.get("thesis")
        or candidate.get("hypothesis")
        or candidate.get("title")
        or family
    )
    authority_ceiling = _text(candidate.get("authority_ceiling") or "paper").lower()
    if authority_ceiling not in {"observation", "shadow", "paper", "live_candidate"}:
        authority_ceiling = "paper"

    conflict_keys = [
        f"strategy:{strategy}",
        f"surface:{surface}",
        f"strategy_surface:{strategy}:{surface}",
    ]
    if strategy == "portfolio" or idea_type == "portfolio_rule":
        conflict_keys.append("portfolio:shared_capital")
    if candidate.get("global_context") or strategy in {"all", "all_strategies", "portfolio"}:
        conflict_keys.append("global:market_context")
    for field in sorted({_text(x) for x in fields if _text(x)}):
        conflict_keys.append(f"field:{field}")

    target_n = int(candidate.get("minimum_closed_trades") or 50)
    target_days = float(candidate.get("minimum_elapsed_days") or 7)
    treatment_pct = int(candidate.get("treatment_pct") or 30)
    treatment_pct = max(10, min(treatment_pct, 90))
    executable = not missing and not (retrospective and not fields)
    if idea_type in {"annotation", "data_collection"}:
        executable = False
    variant_policy = candidate.get("variant_policy")
    if not isinstance(variant_policy, dict):
        variant_policy = {}
    runtime_wired = bool(candidate.get("runtime_wired"))
    strategy_contract = candidate.get("strategy_contract")
    if not isinstance(strategy_contract, dict):
        strategy_contract = {}

    return {
        "contract_version": ORCHESTRATOR_VERSION,
        "idea_type": idea_type,
        "decision_surface": surface,
        "evaluator": TYPE_EVALUATOR[idea_type],
        "hypothesis_family": family,
        "target_strategy": strategy,
        "behavior_changed": behavior,
        "required_pre_entry_fields": sorted({_text(x) for x in fields if _text(x)}),
        "retrospective_fields": retrospective,
        "missing_fields": missing,
        "conflict_keys": sorted(set(conflict_keys)),
        "authority_ceiling": authority_ceiling,
        "runtime_wired": runtime_wired,
        "variant_policy": variant_policy,
        "strategy_contract": strategy_contract,
        "primary_metric": _text(
            candidate.get("primary_metric") or {
                "new_strategy": "net_expectancy_delta",
                "entry_gate": "net_expectancy_delta",
                "risk_gate": "drawdown_adjusted_expectancy_delta",
                "regime_filter": "net_expectancy_delta",
                "stop_rule": "realized_r_delta",
                "exit_rule": "realized_r_delta",
                "sizing_rule": "geometric_return_at_drawdown",
                "scoring_rule": "brier_score_delta",
                "portfolio_rule": "portfolio_return_at_drawdown",
                "execution_rule": "net_fill_quality",
                "annotation": "coverage",
                "data_collection": "coverage",
            }[idea_type]
        ),
        "behavioral": idea_type in BEHAVIORAL_IDEA_TYPES,
        "virtual_first": idea_type in VIRTUAL_FIRST_IDEA_TYPES,
        "executable": executable,
        "treatment_pct": treatment_pct,
        "evidence_rules": {
            "minimum_closed_trades": max(1, target_n),
            "minimum_elapsed_days": max(0.0, target_days),
            "minimum_effective_days": max(
                3,
                int(candidate.get("minimum_effective_days") or min(20, target_n)),
            ),
            "maximum_extensions": max(
                0, min(1, int(candidate.get("maximum_extensions") or 1))
            ),
        },
        "safety": {
            "auto_start_behavior": False,
            "auto_promote": False,
            "auto_live": False,
            "automatic_paper_stop": True,
            "automatic_live_change": False,
        },
    }


def validate_contract(contract: dict) -> dict:
    """Return readiness failures without changing the contract."""
    problems: list[str] = []
    idea_type = normalize_idea_type(contract.get("idea_type"))
    if idea_type != contract.get("idea_type"):
        problems.append("unknown idea type")
    if not _text(contract.get("hypothesis_family")):
        problems.append("hypothesis family is required")
    if not _text(contract.get("target_strategy")):
        problems.append("target strategy is required")
    if not _text(contract.get("behavior_changed")):
        problems.append("behavioral difference is required")
    if contract.get("missing_fields"):
        problems.append("required pre-entry fields are missing")
    if contract.get("retrospective_fields") and not contract.get(
        "required_pre_entry_fields"
    ):
        problems.append("retrospective-only evidence cannot control entries")
    if contract.get("behavioral") and contract.get("authority_ceiling") == "observation":
        problems.append("behavioral idea has observation-only authority")
    if idea_type in {"entry_gate", "risk_gate", "regime_filter"} and not contract.get(
        "runtime_wired"
    ):
        problems.append("entry or risk rule has no Paper runtime adapter")
    if idea_type in VIRTUAL_FIRST_IDEA_TYPES and not contract.get("variant_policy"):
        problems.append("virtual-first idea requires an explicit variant policy")
    if idea_type == "new_strategy" and not contract.get("strategy_contract"):
        problems.append("new strategy requires a validated strategy contract")
    return {
        "valid": not problems,
        "problems": problems,
        "next_state": (
            "ready_for_experiment"
            if not problems
            and (
                contract.get("executable")
                or idea_type in {"annotation", "data_collection"}
            )
            else "needs_data"
            if contract.get("missing_fields")
            or any(
                phrase in problem
                for problem in problems
                for phrase in (
                    "runtime adapter",
                    "variant policy",
                    "strategy contract",
                )
            )
            else "parked"
        ),
    }


def conflict_report(left: dict, right: dict) -> dict:
    """Detect contamination risk between two normalized contracts."""
    left_keys = set(left.get("conflict_keys") or [])
    right_keys = set(right.get("conflict_keys") or [])
    shared = sorted(left_keys & right_keys)
    same_strategy = left.get("target_strategy") == right.get("target_strategy")
    same_surface = left.get("decision_surface") == right.get("decision_surface")
    global_overlap = bool(
        {"portfolio:shared_capital", "global:market_context"} & set(shared)
    )
    # A shared raw feature is not sufficient on its own.  The strongest
    # contamination is the same strategy/surface or a shared portfolio/global
    # behavior change.
    conflicts = bool(
        global_overlap
        or (
            same_strategy
            and same_surface
            and left.get("behavioral")
            and right.get("behavioral")
        )
    )
    return {
        "conflicts": conflicts,
        "shared_keys": shared,
        "same_strategy": same_strategy,
        "same_decision_surface": same_surface,
        "reason": (
            "shared global or portfolio authority"
            if global_overlap
            else "same strategy and decision surface"
            if conflicts
            else "orthogonal or observation-only"
        ),
    }


def deterministic_arm(
    experiment_id: str,
    symbol: str,
    signal_timestamp: str,
    treatment_pct: int = 30,
) -> dict:
    """Assign a stable champion/control or challenger/treatment arm."""
    pct = max(0, min(int(treatment_pct), 100))
    seed = "|".join(
        [_text(experiment_id), _text(symbol).upper(), _text(signal_timestamp)]
    )
    bucket = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16) % 100
    arm = "treatment" if bucket < pct else "control"
    return {
        "arm": arm,
        "bucket": bucket,
        "treatment_pct": pct,
        "assignment_key": hashlib.sha256(seed.encode("utf-8")).hexdigest(),
    }


def transition_allowed(current: str, target: str) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, set())


def scheduler_plan(
    candidates: list[dict],
    active: list[dict],
    *,
    max_behavioral: int = 2,
    max_shadow: int = 10,
) -> dict:
    """Plan conflict-aware starts without granting authority."""
    active_behavioral = [
        item for item in active if (item.get("contract") or item).get("behavioral")
    ]
    active_shadow = [
        item for item in active if not (item.get("contract") or item).get("behavioral")
    ]
    behavioral_slots = max(0, int(max_behavioral) - len(active_behavioral))
    shadow_slots = max(0, int(max_shadow) - len(active_shadow))
    starts: list[dict] = []
    blocked: list[dict] = []
    comparison_pool = list(active)

    ranked = sorted(
        candidates,
        key=lambda item: (
            float(item.get("priority_score") or 0.0),
            _text(item.get("created_at")),
        ),
        reverse=True,
    )
    for item in ranked:
        contract = item.get("contract") or item
        validation = validate_contract(contract)
        if not validation["valid"]:
            blocked.append(
                {
                    "id": item.get("id"),
                    "reason": "; ".join(validation["problems"]),
                    "next_state": validation["next_state"],
                }
            )
            continue
        conflicts = []
        for running in comparison_pool:
            running_contract = running.get("contract") or running
            report = conflict_report(contract, running_contract)
            if report["conflicts"]:
                conflicts.append(
                    {
                        "experiment_id": running.get("experiment_id")
                        or running.get("id"),
                        **report,
                    }
                )
        if conflicts:
            blocked.append(
                {
                    "id": item.get("id"),
                    "reason": "conflicts with active experiment",
                    "conflicts": conflicts,
                    "next_state": "queued",
                }
            )
            continue
        if contract.get("behavioral"):
            if behavioral_slots <= 0:
                blocked.append(
                    {
                        "id": item.get("id"),
                        "reason": "behavioral experiment capacity is full",
                        "next_state": "queued",
                    }
                )
                continue
            behavioral_slots -= 1
        else:
            if shadow_slots <= 0:
                blocked.append(
                    {
                        "id": item.get("id"),
                        "reason": "shadow experiment capacity is full",
                        "next_state": "queued",
                    }
                )
                continue
            shadow_slots -= 1
        start = {
            "id": item.get("id"),
            "next_state": "awaiting_approval"
            if contract.get("behavioral")
            else "collecting",
            "requires_user_approval": bool(contract.get("behavioral")),
            "contract": contract,
        }
        starts.append(start)
        comparison_pool.append(start)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "starts": starts,
        "blocked": blocked,
        "capacity": {
            "max_behavioral": int(max_behavioral),
            "max_shadow": int(max_shadow),
            "behavioral_available": behavioral_slots,
            "shadow_available": shadow_slots,
        },
        "automatic_behavior_change": False,
    }


def effective_sample(rows: list[dict]) -> dict:
    """Report correlated sample size using symbol-day clusters."""
    raw = len(rows)
    clusters = {
        (
            _text(row.get("symbol")).upper() or "UNKNOWN",
            _text(
                row.get("closed_at")
                or row.get("result_at")
                or row.get("opened_at")
                or row.get("logged_at")
            )[:10]
            or "UNKNOWN",
        )
        for row in rows
    }
    market_days = {
        day for _, day in clusters if day and day != "UNKNOWN"
    }
    return {
        "raw_count": raw,
        "symbol_day_count": len(clusters),
        "market_day_count": len(market_days),
        "effective_count": min(raw, len(clusters)),
    }


def permutation_delta_test(
    treatment_rows: list[dict],
    control_rows: list[dict],
    *,
    iterations: int = 1000,
) -> dict:
    """Deterministic one-sided placebo test for mean net-P&L separation."""
    treatment = [
        float(row["pnl_pct"])
        for row in treatment_rows
        if row.get("pnl_pct") is not None
    ]
    control = [
        float(row["pnl_pct"])
        for row in control_rows
        if row.get("pnl_pct") is not None
    ]
    if len(treatment) < 2 or len(control) < 2:
        return {
            "available": False,
            "reason": "At least two treatment and two control outcomes are required.",
        }
    observed = (
        sum(treatment) / len(treatment)
        - sum(control) / len(control)
    )
    combined = treatment + control
    n_treatment = len(treatment)
    iterations = max(100, min(int(iterations), 5000))
    seed_material = _canonical({
        "treatment": treatment,
        "control": control,
        "iterations": iterations,
    })
    rng = random.Random(
        int(hashlib.sha256(seed_material.encode()).hexdigest()[:16], 16)
    )
    exceed = 0
    shuffled = list(combined)
    for _ in range(iterations):
        rng.shuffle(shuffled)
        left = shuffled[:n_treatment]
        right = shuffled[n_treatment:]
        delta = sum(left) / len(left) - sum(right) / len(right)
        if delta >= observed:
            exceed += 1
    return {
        "available": True,
        "observed_mean_delta_pct": round(observed, 6),
        "iterations": iterations,
        "one_sided_p_value": round((exceed + 1) / (iterations + 1), 6),
        "null": "Treatment labels are exchangeable with control labels.",
    }
