"""Pure helpers for MT7's evidence-based learning system.

This module deliberately performs no I/O and makes no strategy or execution
changes.  It canonicalizes experiment contracts, measures closed cohorts, and
classifies evidence against predeclared promotion/falsification rules.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any


LEARNING_CONTRACT_VERSION = "mt7_learning_v1"
ACTIVE_EXPERIMENT_STATUSES = {"active", "collecting", "review"}
TERMINAL_EXPERIMENT_STATUSES = {
    "falsified",
    "promotion_candidate",
    "rolled_back",
    "completed",
    "cancelled",
}


def canonical_json(value: Any) -> str:
    """Return a stable JSON representation suitable for hashing."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )


def stable_fingerprint(value: Any, prefix: str = "sha256") -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"


def event_hash(
    experiment_id: str,
    sequence: int,
    event_type: str,
    event_at: str,
    payload: dict,
    previous_hash: str | None,
) -> str:
    return stable_fingerprint(
        {
            "experiment_id": experiment_id,
            "sequence": int(sequence),
            "event_type": event_type,
            "event_at": event_at,
            "payload": payload,
            "previous_hash": previous_hash or "",
        }
    )


def _finite_numbers(values: list[Any]) -> list[float]:
    output: list[float] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            output.append(number)
    return output


def cohort_metrics(rows: list[dict]) -> dict:
    """Calculate net, outlier-aware metrics from closed trade dictionaries."""
    usable = [
        row for row in rows
        if row.get("pnl_pct") is not None
        and str(row.get("result") or "").upper() in {"WIN", "LOSS", "PARTIAL"}
    ]
    pnls = _finite_numbers([row.get("pnl_pct") for row in usable])
    if not pnls:
        return {
            "count": 0,
            "wins": 0,
            "partials": 0,
            "losses": 0,
            "win_partial_rate": None,
            "avg_pnl_pct": None,
            "median_pnl_pct": None,
            "trimmed_avg_pnl_pct": None,
            "leave_best_out_avg_pnl_pct": None,
            "profit_factor": None,
            "max_drawdown_pct": None,
            "net_pnl_usd": None,
        }

    results = [str(row.get("result") or "").upper() for row in usable]
    wins = sum(result == "WIN" for result in results)
    partials = sum(result == "PARTIAL" for result in results)
    losses = sum(result == "LOSS" for result in results)
    ordered = sorted(pnls)
    middle = len(ordered) // 2
    median = (
        ordered[middle]
        if len(ordered) % 2
        else (ordered[middle - 1] + ordered[middle]) / 2.0
    )
    trim = int(len(ordered) * 0.1)
    trimmed = ordered[trim:len(ordered) - trim] if trim and len(ordered) > trim * 2 else ordered
    leave_best = list(pnls)
    leave_best.remove(max(leave_best))
    gains = sum(value for value in pnls if value > 0)
    losses_abs = abs(sum(value for value in pnls if value < 0))
    profit_factor = (
        gains / losses_abs
        if losses_abs > 0
        else (999.0 if gains > 0 else None)
    )

    equity = 100.0
    peak = equity
    max_drawdown = 0.0
    net_pnl_usd = 0.0
    for row, pnl in zip(usable, pnls):
        size_usd = float(row.get("size_usd") or 0.0)
        leverage = max(1.0, float(row.get("leverage") or 1.0))
        # MT7 stores leveraged return-on-margin percentages but size_usd is
        # position notional. Divide by leverage before converting to dollars.
        net_pnl_usd += size_usd * pnl / 100.0 / leverage
        equity *= max(0.0, 1.0 + pnl / 100.0)
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak * 100.0)

    return {
        "count": len(pnls),
        "wins": wins,
        "partials": partials,
        "losses": losses,
        "win_partial_rate": round((wins + partials) / len(pnls) * 100.0, 2),
        "avg_pnl_pct": round(sum(pnls) / len(pnls), 4),
        "median_pnl_pct": round(median, 4),
        "trimmed_avg_pnl_pct": round(sum(trimmed) / len(trimmed), 4),
        "leave_best_out_avg_pnl_pct": round(
            sum(leave_best) / len(leave_best), 4
        ) if leave_best else None,
        "profit_factor": round(profit_factor, 3) if profit_factor is not None else None,
        "max_drawdown_pct": round(max_drawdown, 3),
        "net_pnl_usd": round(net_pnl_usd, 2),
    }


def default_evidence_rules() -> dict:
    """Conservative defaults for forward Paper experiments."""
    return {
        "minimum_closed_trades": 50,
        "minimum_elapsed_days": 7,
        "promotion": {
            "min_avg_pnl_delta_pct": 0.0,
            "min_profit_factor": 1.15,
            "min_leave_best_out_avg_pnl_pct": 0.0,
            "max_drawdown_increase_pct": 2.0,
        },
        "falsification": {
            "max_avg_pnl_delta_pct": -0.25,
            "max_profit_factor": 0.80,
            "max_drawdown_increase_pct": 5.0,
        },
    }


def evaluate_experiment(
    treatment_rows: list[dict],
    control_rows: list[dict],
    activated_at: str,
    now_iso: str | None = None,
    rules: dict | None = None,
) -> dict:
    """Classify one forward experiment without changing system authority."""
    rules = {**default_evidence_rules(), **(rules or {})}
    promotion = {
        **default_evidence_rules()["promotion"],
        **(rules.get("promotion") or {}),
    }
    falsification = {
        **default_evidence_rules()["falsification"],
        **(rules.get("falsification") or {}),
    }
    treatment = cohort_metrics(treatment_rows)
    control = cohort_metrics(control_rows)
    now = datetime.fromisoformat((now_iso or datetime.now(timezone.utc).isoformat()).replace("Z", "+00:00"))
    start = datetime.fromisoformat(str(activated_at).replace("Z", "+00:00"))
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    elapsed_days = max(0.0, (now - start).total_seconds() / 86400.0)
    minimum_n = max(1, int(rules.get("minimum_closed_trades") or 50))
    minimum_days = max(0.0, float(rules.get("minimum_elapsed_days") or 7))
    sufficient = treatment["count"] >= minimum_n and elapsed_days >= minimum_days

    def delta(field: str) -> float | None:
        left = treatment.get(field)
        right = control.get(field)
        if left is None or right is None:
            return None
        return round(float(left) - float(right), 4)

    deltas = {
        "avg_pnl_pct": delta("avg_pnl_pct"),
        "trimmed_avg_pnl_pct": delta("trimmed_avg_pnl_pct"),
        "win_partial_rate": delta("win_partial_rate"),
        "profit_factor": delta("profit_factor"),
        "max_drawdown_pct": delta("max_drawdown_pct"),
    }
    reasons: list[str] = []
    verdict = "collecting"
    if treatment["count"] >= minimum_n and not sufficient:
        verdict = "review"
        reasons.append(f"Minimum duration is {minimum_days:g} days.")
    if sufficient:
        avg_delta = deltas["avg_pnl_pct"]
        pf = treatment.get("profit_factor")
        dd_delta = deltas["max_drawdown_pct"]
        falsified = (
            (avg_delta is not None and avg_delta <= float(falsification["max_avg_pnl_delta_pct"]))
            or (pf is not None and pf <= float(falsification["max_profit_factor"]))
            or (
                dd_delta is not None
                and dd_delta >= float(falsification["max_drawdown_increase_pct"])
            )
        )
        promotable = (
            avg_delta is not None
            and avg_delta > float(promotion["min_avg_pnl_delta_pct"])
            and pf is not None
            and pf >= float(promotion["min_profit_factor"])
            and treatment.get("leave_best_out_avg_pnl_pct") is not None
            and treatment["leave_best_out_avg_pnl_pct"]
            > float(promotion["min_leave_best_out_avg_pnl_pct"])
            and (
                dd_delta is None
                or dd_delta <= float(promotion["max_drawdown_increase_pct"])
            )
        )
        if falsified:
            verdict = "falsified"
            reasons.append("One or more predeclared downside gates failed.")
        elif promotable:
            verdict = "promotion_candidate"
            reasons.append("All predeclared forward-evidence gates passed.")
        else:
            verdict = "review"
            reasons.append("Sample is mature, but promotion and falsification gates are inconclusive.")
    else:
        reasons.append(
            f"{treatment['count']}/{minimum_n} exact-policy closed trades; "
            f"{elapsed_days:.1f}/{minimum_days:g} days."
        )

    return {
        "verdict": verdict,
        "sufficient_evidence": sufficient,
        "elapsed_days": round(elapsed_days, 2),
        "rules": rules,
        "treatment": treatment,
        "control": control,
        "delta": deltas,
        "reasons": reasons,
        "automatic_config_change": False,
        "requires_user_action": verdict in {"falsified", "promotion_candidate"},
    }


def validate_strategy_factory_contract(candidate: dict) -> dict:
    """Validate a proposed strategy variant before it can enter shadow testing."""
    candidate = candidate if isinstance(candidate, dict) else {}
    payload = candidate.get("api_payload") or candidate.get("implementation") or {}
    required = {
        "name": candidate.get("name") or payload.get("name"),
        "base_key": candidate.get("base_key") or payload.get("base_key"),
        "hypothesis": candidate.get("hypothesis") or candidate.get("thesis"),
        "mechanism": candidate.get("mechanism"),
        "entry_rules": candidate.get("entry_rules"),
        "exit_rules": candidate.get("exit_rules"),
        "failure_regimes": candidate.get("failure_regimes"),
        "data_requirements": candidate.get("data_requirements"),
        "cost_assumptions": candidate.get("cost_assumptions"),
        "control_strategy": candidate.get("control_strategy"),
        "novelty_claim": candidate.get("novelty_claim") or candidate.get("what_is_novel"),
        "falsification_criteria": candidate.get("falsification_criteria"),
        "promotion_criteria": candidate.get("promotion_criteria"),
    }
    missing = [key for key, value in required.items() if value in (None, "", [], {})]
    restricted_fields = sorted(
        {
            "leverage",
            "leverage_cap",
            "risk_pct_per_trade",
            "position_size_usd",
            "max_open_positions",
            "live_trading",
            "execution_enabled",
        }.intersection(payload)
    )
    strategy_class = (
        "strategy_variant"
        if required["base_key"]
        else "novel_strategy_requires_implementation"
    )
    return {
        "version": LEARNING_CONTRACT_VERSION,
        "valid": not missing and not restricted_fields,
        "missing": missing,
        "restricted_fields": restricted_fields,
        "strategy_class": strategy_class,
        "authority": "shadow_only",
        "auto_apply_allowed": False,
        "live_behavior_change_allowed": False,
        "candidate_fingerprint": stable_fingerprint(candidate),
    }


def maturity_score(
    *,
    exact_attribution_rate: float,
    experiment_integrity_ok: bool,
    active_experiments: int,
    mature_experiments: int,
    promoted_experiments: int,
    falsified_experiments: int,
    strategy_candidates_valid: int,
    strategy_candidates_total: int,
) -> dict:
    """Separate engineering capability from empirical proof."""
    # Architecture measures what the system can enforce now. Historical rows
    # and not-yet-mature experiments belong in the empirical score instead.
    architecture = 0.0
    architecture += 2.0 if experiment_integrity_ok else 0.5
    architecture += 2.0  # exact forward attribution + immutable policy snapshots
    architecture += 2.0  # predeclared lifecycle, evaluator, and overlap guard
    architecture += 1.5  # controlled strategy-factory validation contract
    architecture += 2.5  # manual authority, no live/size/leverage auto-escalation
    if strategy_candidates_total and not strategy_candidates_valid:
        architecture -= 1.0  # legacy candidates still need contract regeneration
    architecture = min(10.0, architecture)

    evidence = 0.0
    evidence += min(3.0, mature_experiments * 0.75)
    evidence += min(3.0, promoted_experiments * 1.0)
    evidence += min(1.5, falsified_experiments * 0.5)
    evidence += min(2.5, exact_attribution_rate * 2.5)
    evidence = min(10.0, evidence)
    overall = min(architecture, (architecture * 0.35 + evidence * 0.65))
    return {
        "architecture_score": round(architecture, 1),
        "empirical_evidence_score": round(evidence, 1),
        "overall_score": round(overall, 1),
        "score_ceiling_rule": (
            "Overall maturity cannot outrun architecture and is weighted toward "
            "forward evidence, not feature count."
        ),
    }
