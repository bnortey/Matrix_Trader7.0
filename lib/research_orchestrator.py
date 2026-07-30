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

IDEA_TYPE_LABELS = {
    "new_strategy": "New strategy",
    "entry_gate": "Entry-quality gate",
    "risk_gate": "Risk gate",
    "regime_filter": "Market-regime filter",
    "stop_rule": "Stop-management rule",
    "exit_rule": "Profit-taking rule",
    "sizing_rule": "Position-sizing rule",
    "scoring_rule": "Signal-scoring rule",
    "portfolio_rule": "Portfolio-allocation rule",
    "execution_rule": "Order-execution rule",
    "annotation": "Research annotation",
    "data_collection": "Data-collection study",
}

SURFACE_LABELS = {
    "signal_generation": "which setups become signals",
    "entry_eligibility": "which signals are allowed to enter",
    "loss_management": "where losses are cut",
    "profit_management": "how profits are taken",
    "capital_allocation": "how much Paper capital a trade receives",
    "signal_ranking": "how signals are ranked",
    "portfolio_allocation": "how shared Paper capital is allocated",
    "order_execution": "how simulated orders are filled",
    "observation": "labels only; no trade behavior",
    "data_collection": "telemetry collection only",
}

EVALUATOR_EXPLANATIONS = {
    "isolated_strategy_cohort": (
        "Compare the new strategy's untouched forward Paper outcomes with the "
        "current strategy baseline."
    ),
    "accepted_vs_blocked": (
        "Compare eligible opportunities assigned to the challenger with the "
        "unchanged control arm, while separately tracking winners the gate blocked."
    ),
    "same_entry_path": (
        "Replay the same entries through champion and challenger management "
        "paths so entry selection cannot take credit for the result."
    ),
    "virtual_portfolio": (
        "Replay the same chronological trades through both sizing policies and "
        "compare return at equal drawdown."
    ),
    "calibration": (
        "Compare predicted conviction with observed outcomes; better ranking "
        "must improve calibration, not merely the top historical trades."
    ),
    "chronological_portfolio": (
        "Replay decisions in time order with shared-capital constraints so "
        "overlapping positions cannot spend the same Paper dollars twice."
    ),
    "fill_quality": (
        "Compare simulated net fills, slippage, and missed-fill rates on the "
        "same eligible orders."
    ),
    "observation_only": (
        "Measure coverage and outcome separation without changing whether, "
        "when, or how a Paper trade is taken."
    ),
}

PRIMARY_METRIC_LABELS = {
    "net_expectancy_delta": "average net result versus control",
    "drawdown_adjusted_expectancy_delta": "average net result at comparable drawdown",
    "realized_r_delta": "change in realized reward per unit of planned risk",
    "geometric_return_at_drawdown": "compounded return at comparable drawdown",
    "brier_score_delta": "improvement in probability calibration",
    "portfolio_return_at_drawdown": "portfolio return at comparable drawdown",
    "net_fill_quality": "fill quality after fees and slippage",
    "coverage": "usable pre-entry coverage and outcome separation",
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


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = _text(item)
        if text and text not in out:
            out.append(text)
    return out


def trader_experiment_brief(
    contract: dict,
    *,
    context: dict | None = None,
    state: dict | None = None,
) -> dict:
    """Translate one experiment contract into an approval-ready trader brief."""
    context = context if isinstance(context, dict) else {}
    state = state if isinstance(state, dict) else {}
    idea_type = normalize_idea_type(contract.get("idea_type"))
    status = _text(state.get("status") or state.get("lifecycle_state") or "discovered")
    strategy = _text(contract.get("target_strategy") or "portfolio")
    treatment_pct = int(contract.get("treatment_pct") or 30)
    control_pct = max(0, 100 - treatment_pct)
    rules = contract.get("evidence_rules")
    rules = rules if isinstance(rules, dict) else {}
    assignments = state.get("assignments")
    assignments = assignments if isinstance(assignments, dict) else {}
    evaluation = state.get("evaluation")
    evaluation = evaluation if isinstance(evaluation, dict) else {}
    validation = state.get("validation")
    validation = validation if isinstance(validation, dict) else validate_contract(contract)
    approval_readiness = state.get("approval_readiness")
    approval_readiness = (
        approval_readiness if isinstance(approval_readiness, dict) else {}
    )

    target_closed = max(1, int(rules.get("minimum_closed_trades") or 50))
    target_days = max(0.0, float(rules.get("minimum_elapsed_days") or 7))
    target_effective_days = max(
        1, int(rules.get("minimum_effective_days") or min(20, target_closed))
    )
    closed = max(0, int(assignments.get("closed") or 0))
    elapsed_days = max(0.0, float(state.get("elapsed_days") or 0.0))
    effective_days = max(0, int(assignments.get("effective_market_days") or 0))

    blockers = _string_list(state.get("blockers"))
    blockers.extend(
        problem for problem in _string_list(validation.get("problems"))
        if problem not in blockers
    )
    missing_fields = _string_list(contract.get("missing_fields"))
    if status == "awaiting_approval" and not blockers:
        blockers.append(
            "Ready technically, but Paper behavior remains unchanged until you approve it."
        )
    if status == "needs_data" and not blockers:
        blockers.append(
            "The experiment contract is incomplete; additional pre-entry telemetry "
            "or a runnable challenger policy is required."
        )

    risks = _string_list(context.get("known_caveats"))
    for risk in (
        context.get("overfitting_risk"),
        context.get("expected_failure_mode"),
    ):
        risk_text = _text(risk)
        if risk_text and risk_text not in risks:
            risks.append(risk_text)

    funding_sensitive = any(
        "funding" in _text(value).lower()
        for value in [
            contract.get("hypothesis_family"),
            *list(contract.get("required_pre_entry_fields") or []),
        ]
    )
    cost_scope = evaluation.get("cost_scope")
    if not isinstance(cost_scope, dict):
        cost_scope = {
            "paper_fee_and_slippage": True,
            "blocked_counterfactual_fee_and_slippage": True,
            "funding_cashflows": False,
        }
    cost_note = (
        "Paper fees and modeled slippage are included when outcomes close. "
        "Realized perpetual funding cashflows are not yet booked."
    )
    if funding_sensitive:
        cost_note += (
            " Because this hypothesis is funding-sensitive, it cannot promote "
            "until that missing cashflow is reviewed or measured."
        )

    if status == "awaiting_approval" and approval_readiness and not approval_readiness.get("can_approve"):
        status_explanation = _text(
            approval_readiness.get("summary")
            or "Approval preflight found that this challenger cannot produce a valid comparison."
        )
        next_step = _text(
            approval_readiness.get("next_step")
            or "Redesign the contract, then reconcile."
        )
    elif status == "awaiting_approval":
        status_explanation = (
            "The contract passed technical preflight. It is not running and has "
            "not changed Paper behavior."
        )
        next_step = "Read this brief and explicitly approve or leave it waiting."
    elif status in {"collecting", "active", "paper_challenger"}:
        status_explanation = (
            "The Paper challenger is collecting untouched forward assignments. "
            "Historical matches do not count toward promotion."
        )
        next_step = "Keep collecting until every sample, duration, cost, and placebo gate is met."
    elif status == "needs_data":
        status_explanation = (
            "The idea is retained, but it cannot run causally with its current "
            "telemetry or policy definition."
        )
        next_step = _text(context.get("next_action")) or "Resolve the listed blockers, then reconcile."
    elif status == "promotion_candidate":
        status_explanation = (
            "The initial Paper challenger passed its declared gates. Nothing "
            "promotes automatically."
        )
        next_step = "Manual promotion creates a new untouched 80/20 confirmation cohort."
    elif status in {"falsified", "rolled_back"}:
        status_explanation = (
            "Forward evidence failed a declared gate and the challenger has no "
            "Paper authority."
        )
        next_step = "Keep the result as negative knowledge; redesign only with a new hypothesis."
    elif status in {"completed", "confirmed"}:
        status_explanation = (
            "Both Paper stages completed. This is confirmed Paper evidence, not "
            "permission for live trading."
        )
        next_step = "Any live candidate requires a separate safety and authority review."
    else:
        status_explanation = "The experiment is waiting at its current governed lifecycle stage."
        next_step = _text(context.get("next_action")) or "Reconcile and review the evidence state."

    promotion_criteria = _text(
        context.get("promotion_criteria")
        or "Positive net improvement versus control after all declared sample, "
        "duration, effective-day, cost, and placebo gates."
    )
    rollback_condition = _text(
        context.get("rollback_condition")
        or "Stop the challenger if forward net performance is worse than control "
        "or a declared safety/evidence gate fails."
    )

    comparison = EVALUATOR_EXPLANATIONS.get(
        _text(contract.get("evaluator")),
        "Compare untouched forward challenger and control outcomes.",
    )
    treatment_parts = _string_list([
        context.get("entry_filter_rule"),
        context.get("reject_filter_rule"),
    ])
    treatment_rule = " ".join(treatment_parts) or _text(
        context.get("strategy_shape")
        or contract.get("behavior_changed")
    )
    control_rule = (
        f"{control_pct}% of eligible {strategy} opportunities keep the current "
        "Paper champion behavior and do not receive the experimental rule."
    )
    treatment_rule_explanation = (
        f"{treatment_pct}% of eligible {strategy} opportunities are assigned "
        f"deterministically to the challenger. {treatment_rule}"
    )
    decision_delta = _text(approval_readiness.get("decision_delta"))
    if decision_delta == "zero":
        treatment_rule_explanation += (
            " Preflight found that this treatment matches the current champion."
        )
        control_rule = (
            f"The current Paper champion already enforces the same rule for "
            f"eligible {strategy} opportunities."
        )
        comparison = (
            "No causal comparison exists as designed because treatment and "
            "control make the same decision. Redesign is required."
        )
    elif decision_delta == "unreachable":
        treatment_rule_explanation += (
            " The target is currently unreachable, so this arm cannot receive assignments."
        )
        control_rule = (
            f"The {strategy} control cohort is also unreachable under the "
            "current Paper strategy configuration."
        )
        comparison = (
            "No causal comparison can start until the target is reachable "
            "through a separate governed strategy decision."
        )
    if not contract.get("behavioral"):
        treatment_rule_explanation = (
            "This is observation-only. It attaches research labels but does not "
            "change whether, when, or how a Paper trade is taken."
        )
        control_rule = "No trade behavior differs; unlabeled observations provide context."

    deltas = evaluation.get("deltas")
    deltas = deltas if isinstance(deltas, dict) else {}
    reasons = _string_list(evaluation.get("reasons"))
    evaluation_summary = {
        "verdict": evaluation.get("verdict"),
        "reasons": reasons,
        "average_net_lift_pct": deltas.get("avg_pnl_pct"),
        "placebo": (evaluation.get("multiplicity_and_placebo") or {}).get(
            "permutation_test"
        ),
        "gate_counterfactual": evaluation.get("gate_counterfactual"),
        "cost_scope": cost_scope,
    }

    return {
        "title": _text(context.get("title") or state.get("hypothesis") or contract.get("hypothesis_family")),
        "type_label": IDEA_TYPE_LABELS[idea_type],
        "surface_label": SURFACE_LABELS.get(
            _text(contract.get("decision_surface")),
            _text(contract.get("decision_surface")).replace("_", " "),
        ),
        "status_explanation": status_explanation,
        "purpose": _text(
            context.get("thesis")
            or context.get("strategy_shape")
            or contract.get("behavior_changed")
        ),
        "why_edge_might_exist": _text(
            context.get("expected_edge")
            or "The source evidence suggests this distinction may improve "
            "selectivity, but it must earn authority from MT7's untouched "
            "forward outcomes."
        ),
        "behavior_change": _text(
            context.get("strategy_shape")
            or context.get("gate_impact")
            or contract.get("behavior_changed")
        ),
        "treatment": treatment_rule_explanation,
        "control": control_rule,
        "comparison_method": comparison,
        "primary_metric": PRIMARY_METRIC_LABELS.get(
            _text(contract.get("primary_metric")),
            _text(contract.get("primary_metric")).replace("_", " "),
        ),
        "progress": {
            "closed": closed,
            "target_closed": target_closed,
            "closed_pct": round(min(1.0, closed / target_closed) * 100.0, 1),
            "elapsed_days": round(elapsed_days, 2),
            "target_elapsed_days": target_days,
            "elapsed_pct": round(
                min(1.0, elapsed_days / target_days) * 100.0, 1
            ) if target_days else 100.0,
            "effective_market_days": effective_days,
            "target_effective_market_days": target_effective_days,
            "effective_days_pct": round(
                min(1.0, effective_days / target_effective_days) * 100.0, 1
            ),
            "treatment_assignments": int(assignments.get("treatment") or 0),
            "control_assignments": int(assignments.get("control") or 0),
            "blocked_opportunities": int(assignments.get("blocked") or 0),
            "contaminated_assignments": int(assignments.get("contaminated") or 0),
        },
        "data": {
            "required_pre_entry_fields": _string_list(
                contract.get("required_pre_entry_fields")
            ),
            "partial_fields": _string_list(context.get("partial_fields")),
            "missing_fields": missing_fields,
            "retrospective_fields": _string_list(contract.get("retrospective_fields")),
        },
        "blockers": blockers,
        "approval_readiness": approval_readiness,
        "source_evidence": {
            "source_count": int(context.get("source_count") or 0),
            "source_titles": _string_list(context.get("source_titles")),
            "average_quality": context.get("avg_source_quality"),
            "average_relevance": context.get("avg_source_relevance"),
            "evidence_stats": context.get("evidence_stats") or {},
        },
        "risks": risks,
        "success_criteria": (
            f"{promotion_criteria} MT7 also requires at least {target_closed} "
            f"closed opportunities, {target_days:g} elapsed Paper days, "
            f"{target_effective_days} independent market days, complete relevant "
            "costs, and separation that beats the hypothesis-family placebo gate."
        ),
        "failure_criteria": rollback_condition,
        "costs": cost_note,
        "conflicts": _string_list(state.get("conflicts")),
        "promotion": (
            "First manual promotion creates a fresh 80% challenger / 20% untouched "
            "control confirmation cohort. A second manual decision can confirm "
            "Paper evidence; neither step grants live authority."
        ),
        "rollback": (
            "Stopping or falsification disarms the Paper challenger, restores the "
            "previous champion behavior, preserves the result as negative "
            "knowledge, and changes nothing live."
        ),
        "authority": (
            "Maximum authority is Paper-only. Starting and promoting behavioral "
            "experiments require explicit user approval; risk cannot increase automatically."
        ),
        "what_this_cannot_prove": (
            "A passing Paper experiment does not prove live profitability, eliminate "
            "regime risk, or justify leverage. It only supports the declared change "
            "within the measured MT7 cohort."
        ),
        "next_step": next_step,
        "evaluation": evaluation_summary,
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
