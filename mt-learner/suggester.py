import json
import os
import logging
import hashlib
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

MODELS_DIR = os.path.join(os.path.dirname(__file__), 'models')
SUGGESTIONS_DIR = os.path.join(os.path.dirname(__file__), 'suggestions')
PENDING_PATH = os.path.join(SUGGESTIONS_DIR, 'pending.json')
REJECTED_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'rejected_suggestions.json')

THRESHOLD_MIN_SAMPLE = 80
THRESHOLD_MIN_NE_DELTA = 1.0
REGIME_MIN_SAMPLE = 50
REGIME_MAX_WIN_RATE = 0.35
REGIME_MAX_WIN_PARTIAL_RATE = 0.45
REGIME_MAX_AVG_PNL = 0.0
REGIME_MIN_OTHER_WIN_RATE = 0.55
REGIME_MIN_OTHER_AVG_PNL = 0.0
NEW_STRAT_MIN_SAMPLE = 100
NEW_STRAT_MIN_WIN_RATE = 0.55
NEW_STRAT_MIN_AVG_PNL = -2.0  # after assumed costs
SUGGESTION_CONTRACT_VERSION = "mt7_suggestion_v1"


def _baseline_fingerprint(strategy, change_set):
    canonical = json.dumps(
        {"strategy": strategy, "change_set": change_set},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _suggestion_contract(
    *,
    strategy,
    change_set,
    sample_size,
    confidence,
    source,
    expected_benefit,
    downside,
    rollback_condition,
    baseline_source,
    baseline_snapshot_at,
):
    """Attach the same human-readable, reversible contract to every learner idea."""
    return {
        "suggestion_contract_version": SUGGESTION_CONTRACT_VERSION,
        "scope": {
            "strategy": strategy,
            "target_mode": "paper_trial_after_review",
            "exchange": "all_configured",
        },
        "change_set": change_set,
        "evidence": {
            "source": source,
            "sample_size": sample_size,
            "confidence": confidence,
            "forward_tested": False,
        },
        "expected_impact": {
            "benefit": expected_benefit,
            "uncertainty": "Historical estimate; forward shadow evidence is required.",
        },
        "downside": downside,
        "rollback_plan": {
            "condition": rollback_condition,
            "action": "Restore the exact pre-trial strategy field values and park the suggestion.",
        },
        "approval_policy": {
            "auto_apply_allowed": False,
            "explicit_user_approval_required": True,
            "application_lane": "serial_paper_config_trial",
        },
        "baseline_snapshot": {
            "source": baseline_source,
            "captured_at": baseline_snapshot_at,
            "fingerprint": _baseline_fingerprint(strategy, change_set),
        },
    }


def _read_json(filename):
    path = os.path.join(MODELS_DIR, filename)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _load_pending():
    if not os.path.exists(PENDING_PATH):
        return {'suggestions': []}
    try:
        with open(PENDING_PATH) as f:
            return json.load(f)
    except Exception:
        return {'suggestions': []}


def _load_rejected():
    try:
        with open(REJECTED_PATH) as f:
            return json.load(f)
    except Exception:
        return []


def _write_pending(data):
    os.makedirs(SUGGESTIONS_DIR, exist_ok=True)
    tmp = PENDING_PATH + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, PENDING_PATH)


def _already_exists(suggestions, stype, strategy, value_key, value):
    for s in suggestions:
        if s.get('type') != stype or s.get('strategy') != strategy:
            continue
        if s.get('status') in ('pending', 'pending_review', 'evaluating', 'shadow_evaluating', 'parked', 'applied'):
            return True
        if s.get('status') in ('dismissed', 'rejected') and s.get(value_key) == value:
            return True
    return False


def _repair_duplicate_suggestion_ids(suggestions):
    """Give every queue record a stable unique ID before actions can target it."""
    seen = set()
    repaired = []
    now = datetime.now(timezone.utc).isoformat()
    for suggestion in suggestions:
        original = str(suggestion.get("id") or "suggestion")
        if original not in seen:
            seen.add(original)
            continue
        suffix = 2
        candidate = f"{original}_r{suffix}"
        while candidate in seen:
            suffix += 1
            candidate = f"{original}_r{suffix}"
        suggestion["legacy_duplicate_id"] = original
        suggestion["id"] = candidate
        suggestion["id_repaired_at"] = now
        seen.add(candidate)
        repaired.append({"old_id": original, "new_id": candidate})
    return repaired


def _next_suggestion_id(prefix, existing, new_suggestions):
    date_label = datetime.now(timezone.utc).strftime("%Y%m%d")
    base = f"{prefix}_{date_label}"
    used = {
        str(item.get("id") or "")
        for item in list(existing) + list(new_suggestions)
        if isinstance(item, dict)
    }
    ordinal = 1
    while f"{base}_{ordinal:03d}" in used:
        ordinal += 1
    return f"{base}_{ordinal:03d}"


def _supersede_stale_threshold_proposals(suggestions, thresholds):
    """Retire read-only threshold ideas whose recorded baseline is no longer runtime truth."""
    now = datetime.now(timezone.utc).isoformat()
    changed = []
    read_only_statuses = {
        "pending",
        "pending_review",
        "shadow_evaluating",
        "parked",
    }
    strategies = (thresholds or {}).get("strategies") or {}
    for suggestion in suggestions:
        if (
            suggestion.get("type") != "threshold"
            or suggestion.get("status") not in read_only_statuses
        ):
            continue
        info = strategies.get(suggestion.get("strategy")) or {}
        runtime = info.get("runtime_threshold")
        recorded = suggestion.get("current_value")
        if runtime is None or recorded is None:
            continue
        baseline_source = str(
            (suggestion.get("baseline_snapshot") or {}).get("source") or ""
        )
        unverified_baseline = baseline_source in {
            "historical_implied_fallback",
            "unavailable",
        }
        try:
            stale = int(runtime) != int(recorded) or unverified_baseline
        except (TypeError, ValueError):
            stale = runtime != recorded or unverified_baseline
        if not stale:
            continue
        previous = suggestion.get("status")
        suggestion.update({
            "status": "superseded",
            "previous_status": previous,
            "superseded_at": now,
            "superseded_reason": (
                "runtime_baseline_unverified"
                if unverified_baseline
                else "runtime_baseline_changed"
            ),
            "runtime_actual_at_supersession": runtime,
            "proposal_baseline_at_supersession": recorded,
            "can_apply": False,
        })
        changed.append(suggestion.get("id"))
    return changed


def run_strategy_proposal_check(db_path=None):
    logger.info('run_strategy_proposal_check: starting')
    thresholds = _read_json('conviction_thresholds.json')
    regime_perf = _read_json('regime_performance.json')

    pending = _load_pending()
    existing = pending.get('suggestions', [])
    repaired_duplicate_ids = _repair_duplicate_suggestion_ids(existing)
    superseded_stale_ids = _supersede_stale_threshold_proposals(
        existing,
        thresholds,
    )
    rejected = _load_rejected()
    rejected_keys = {
        (r.get('strategy'), r.get('type'), str(r.get('suggested_value', '')))
        for r in rejected
    }
    new_suggestions = []

    # --- Threshold suggestions ---
    if thresholds:
        for strat, info in thresholds.get('strategies', {}).items():
            if info.get("runtime_threshold_source") not in {
                "strategy_override",
                "strategy_config",
                "runtime",
            }:
                logger.warning(
                    "skipping threshold suggestion for %s: runtime authority unavailable",
                    strat,
                )
                continue
            optimal = info.get('optimal_threshold')
            current = info.get('runtime_threshold')
            if current is None:
                current = info.get('current_implied_threshold')
            sample = info.get('optimal_sample_size', 0)
            ne = info.get('optimal_net_expectancy')
            current_ne = info.get('current_net_expectancy')
            ne_delta = info.get('net_expectancy_delta')
            delta = info.get('delta_from_current', 0)
            wr = info.get('optimal_win_rate')
            wpr = info.get('optimal_win_partial_rate')
            current_wpr = info.get('current_win_partial_rate')
            loss_streak = info.get('optimal_max_loss_streak')
            current_loss_streak = info.get('current_max_loss_streak')

            if sample < THRESHOLD_MIN_SAMPLE:
                continue
            if ne is None or delta <= 0:
                continue
            if ne_delta is None or ne_delta < THRESHOLD_MIN_NE_DELTA:
                continue
            if current_ne is not None and ne <= current_ne:
                continue
            if _already_exists(existing, 'threshold', strat, 'suggested_value', optimal):
                continue
            if (strat, 'threshold', str(optimal)) in rejected_keys:
                continue

            # estimate trade count delta
            trade_pct_drop = round((delta / max(optimal, 1)) * 100)
            sid = _next_suggestion_id(
                f"thresh_{strat}",
                existing,
                new_suggestions,
            )
            sug = {
                'id': sid,
                'type': 'threshold',
                'generated_at': datetime.now(timezone.utc).isoformat(),
                'strategy': strat,
                'status': 'pending_review',
                'confidence': info.get('confidence', 'low'),
                'sample_size': sample,
                'evidence_summary': (
                    f'{strat}: raising min conviction from {current} to {optimal} improves net EV '
                    f'from {round(current_ne or 0, 2)}% to {round(ne, 2)}%/trade '
                    f'(sample: {sample} trades)'
                ),
                'reasoning': (
                    f'Based on {sample} closed trades with net P&L, the optimal conviction threshold '
                    f'is {optimal} (current implied: {current}). Average net EV improves by '
                    f'{round(ne_delta,2)} percentage points per trade, from '
                    f'{round(current_ne or 0,2)}% to {round(ne,2)}%. W+P rate moves from '
                    f'{round((current_wpr or 0)*100,1)}% to {round((wpr or 0)*100,1)}%, while '
                    f'max loss streak moves from {current_loss_streak} to {loss_streak}. '
                    f'Raising the threshold will reduce trade count by roughly {trade_pct_drop}% '
                    f'but improves the actual optimization target: net EV.'
                ),
                'current_value': current,
                'suggested_value': optimal,
                'expected_net_ev_delta': f'+{round(ne_delta, 2)} pct/trade',
                'expected_win_rate_delta': f'{round((wr or 0)*100,1)}% strict WIN at new threshold',
                'expected_win_partial_rate': f'{round((wpr or 0)*100,1)}% W+P at new threshold',
                'expected_trade_count_delta': f'-{trade_pct_drop}% fewer trades',
                'objective': info.get('objective', 'net_ev_primary'),
                'api_payload': {'min_conviction': optimal},
                **_suggestion_contract(
                    strategy=strat,
                    change_set=[{
                        "field": "min_conviction",
                        "label": "Minimum conviction",
                        "current": current,
                        "proposed": optimal,
                        "unit": "score_points",
                        "direction": "increase",
                    }],
                    sample_size=sample,
                    confidence=info.get('confidence', 'low'),
                    source="conviction_thresholds.json",
                    expected_benefit=f"+{round(ne_delta, 2)} percentage points net EV/trade",
                    downside=f"Approximately {trade_pct_drop}% fewer qualifying trades.",
                    rollback_condition=(
                        "Roll back if the forward Paper trial underperforms its pre-trial "
                        "baseline, produces negative EV, or breaches its declared drawdown gate."
                    ),
                    baseline_source=info.get("runtime_threshold_source") or "historical_implied_fallback",
                    baseline_snapshot_at=info.get("runtime_snapshot_at"),
                ),
            }
            new_suggestions.append(sug)

    # --- Regime suppress suggestions ---
    if regime_perf:
        for strat, regimes in regime_perf.get('by_strategy_regime', {}).items():
            for regime, info in regimes.items():
                count = info.get('count', 0)
                wr = info.get('win_rate')
                wpr = info.get('win_partial_rate')
                avg_pnl = info.get('avg_pnl')
                if count < REGIME_MIN_SAMPLE or wr is None:
                    continue
                if avg_pnl is None:
                    continue
                poor_labels = wr < REGIME_MAX_WIN_RATE and (wpr or 0) < REGIME_MAX_WIN_PARTIAL_RATE
                poor_ev = avg_pnl < REGIME_MAX_AVG_PNL
                if not (poor_labels or poor_ev):
                    continue
                # check other regimes have decent win rates
                others = {r: d for r, d in regimes.items()
                          if r != regime
                          and (
                              (d.get('win_partial_rate') or d.get('win_rate') or 0) >= REGIME_MIN_OTHER_WIN_RATE
                              or (d.get('avg_pnl') is not None and d.get('avg_pnl') >= REGIME_MIN_OTHER_AVG_PNL)
                          )}
                if not others:
                    continue
                if _already_exists(existing, 'regime_suppress', strat, 'regime', regime):
                    continue
                if (strat, 'regime_suppress', regime) in rejected_keys:
                    continue

                others_str = ', '.join(
                    r + ':W+P ' + str(round((d.get('win_partial_rate') or d.get('win_rate') or 0) * 100, 1)) + '%'
                    + ', EV ' + str(round(d.get('avg_pnl') or 0, 2)) + '%'
                    for r, d in others.items()
                )
                sid = _next_suggestion_id(
                    f"regime_{strat}_{regime}",
                    existing,
                    new_suggestions,
                )
                sug = {
                    'id': sid,
                    'type': 'regime_suppress',
                    'generated_at': datetime.now(timezone.utc).isoformat(),
                    'strategy': strat,
                    'status': 'pending_review',
                    'confidence': 'medium' if count < 80 else 'high',
                    'sample_size': count,
                    'evidence_summary': (
                        f'{strat} in {regime}: strict win {round(wr*100,1)}%, '
                        f'W+P {round((wpr or 0)*100,1)}%, net EV {round(avg_pnl,2)}% '
                        f'across {count} trades — suppress regime'
                    ),
                    'reasoning': (
                        f'{strat} performs poorly in {regime}: strict win {round(wr*100,1)}%, '
                        f'W+P {round((wpr or 0)*100,1)}%, and net EV {round(avg_pnl,2)}% '
                        f'across {count} trades. Other regimes show better net or W+P results '
                        f'({others_str}). '
                        f'Suppressing {regime} should improve overall quality.'
                    ),
                    'regime': regime,
                    'current_behavior': 'active',
                    'win_rate_in_regime': round(wr, 4),
                    'win_partial_rate_in_regime': round(wpr, 4) if wpr is not None else None,
                    'net_ev_in_regime': round(avg_pnl, 2),
                    'comparison_regimes': {
                        r: {
                            'win_rate': round(d.get('win_rate') or 0, 4),
                            'win_partial_rate': round((d.get('win_partial_rate') or d.get('win_rate') or 0), 4),
                            'avg_pnl': round(d.get('avg_pnl') or 0, 2),
                        }
                        for r, d in others.items()
                    },
                    'expected_ev_label': f'Regime EV: {round(avg_pnl, 2)}%/trade (drag removed if suppressed)',
                    'api_payload': {'blocked_agent_regimes': [regime]},
                    **_suggestion_contract(
                        strategy=strat,
                        change_set=[{
                            "field": "blocked_agent_regimes",
                            "label": "Blocked agent regimes",
                            "current": "allowed",
                            "proposed": f"block {regime}",
                            "unit": "regime_labels",
                            "direction": "restrict",
                        }],
                        sample_size=count,
                        confidence='medium' if count < 80 else 'high',
                        source="regime_performance.json",
                        expected_benefit=(
                            f"Remove a {round(avg_pnl, 2)}% net-EV/trade regime drag."
                        ),
                        downside=(
                            "Fewer qualifying trades and possible under-participation if "
                            "the market regime changes."
                        ),
                        rollback_condition=(
                            "Roll back if the excluded regime becomes positive EV in the "
                            "forward comparison or the retained cohort deteriorates."
                        ),
                        baseline_source="runtime strategy admission policy",
                        baseline_snapshot_at=datetime.now(timezone.utc).isoformat(),
                    ),
                }
                new_suggestions.append(sug)

    # Merge: keep existing, append new
    merged = existing + new_suggestions

    # Count new_strategy pending — cap at 2
    ns_pending = sum(1 for s in merged if s.get('type') == 'new_strategy' and s.get('status') in ('pending', 'pending_review'))

    total_analyzed = 0
    if thresholds:
        for info in thresholds.get('strategies', {}).values():
            total_analyzed = max(total_analyzed, info.get('optimal_sample_size', 0))

    result = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'learner_version': 'v1',
        'db_signals_analyzed': total_analyzed,
        'shadow_mode': False,
        'repaired_duplicate_ids': repaired_duplicate_ids,
        'superseded_stale_ids': superseded_stale_ids,
        'suggestions': merged,
    }
    _write_pending(result)
    logger.info(f'run_strategy_proposal_check: done, {len(new_suggestions)} new suggestions, {len(merged)} total')
    return result
