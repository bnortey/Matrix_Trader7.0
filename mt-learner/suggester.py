import json
import os
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

MODELS_DIR = os.path.join(os.path.dirname(__file__), 'models')
SUGGESTIONS_DIR = os.path.join(os.path.dirname(__file__), 'suggestions')
PENDING_PATH = os.path.join(SUGGESTIONS_DIR, 'pending.json')
REJECTED_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'rejected_suggestions.json')

THRESHOLD_MIN_SAMPLE = 80
THRESHOLD_MIN_NE_DELTA = 5.0
REGIME_MIN_SAMPLE = 50
REGIME_MAX_WIN_RATE = 0.35
REGIME_MIN_OTHER_WIN_RATE = 0.55
NEW_STRAT_MIN_SAMPLE = 100
NEW_STRAT_MIN_WIN_RATE = 0.55
NEW_STRAT_MIN_AVG_PNL = -2.0  # after assumed costs


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
        if s.get('status') in ('pending', 'pending_review', 'evaluating', 'applied'):
            return True
        if s.get('status') in ('dismissed', 'rejected') and s.get(value_key) == value:
            return True
    return False


def run_strategy_proposal_check(db_path=None):
    logger.info('run_strategy_proposal_check: starting')
    thresholds = _read_json('conviction_thresholds.json')
    regime_perf = _read_json('regime_performance.json')

    pending = _load_pending()
    existing = pending.get('suggestions', [])
    rejected = _load_rejected()
    rejected_keys = {
        (r.get('strategy'), r.get('type'), str(r.get('suggested_value', '')))
        for r in rejected
    }
    new_suggestions = []

    # --- Threshold suggestions ---
    if thresholds:
        for strat, info in thresholds.get('strategies', {}).items():
            optimal = info.get('optimal_threshold')
            current = info.get('current_implied_threshold')
            sample = info.get('optimal_sample_size', 0)
            ne = info.get('optimal_net_expectancy')
            delta = info.get('delta_from_current', 0)
            wr = info.get('optimal_win_rate')

            if sample < THRESHOLD_MIN_SAMPLE:
                continue
            if ne is None or delta <= 0:
                continue
            if _already_exists(existing, 'threshold', strat, 'suggested_value', optimal):
                continue
            if (strat, 'threshold', str(optimal)) in rejected_keys:
                continue

            # estimate trade count delta
            trade_pct_drop = round((delta / max(optimal, 1)) * 100)
            sid = f'thresh_{strat}_{datetime.now(timezone.utc).strftime("%Y%m%d")}_{len(new_suggestions)+1:03d}'
            sug = {
                'id': sid,
                'type': 'threshold',
                'generated_at': datetime.now(timezone.utc).isoformat(),
                'strategy': strat,
                'status': 'pending_review',
                'confidence': info.get('confidence', 'low'),
                'sample_size': sample,
                'evidence_summary': f'{strat}: raising min conviction from {current} to {optimal} improves net expectancy (sample: {sample} trades)',
                'reasoning': (
                    f'Based on {sample} closed trades for {strat}, the optimal conviction threshold '
                    f'is {optimal} (current implied: {current}). At threshold {optimal}, win rate is '
                    f'{round((wr or 0)*100,1)}% with net expectancy {round(ne,2)}. '
                    f'Raising the threshold will reduce trade count by roughly {trade_pct_drop}% '
                    f'but improve quality of entries.'
                ),
                'current_value': current,
                'suggested_value': optimal,
                'expected_win_rate_delta': f'+{round((wr or 0)*100,1)}% at new threshold',
                'expected_trade_count_delta': f'-{trade_pct_drop}% fewer trades',
                'api_payload': {'min_conviction': optimal},
            }
            new_suggestions.append(sug)

    # --- Regime suppress suggestions ---
    if regime_perf:
        for strat, regimes in regime_perf.get('by_strategy_regime', {}).items():
            for regime, info in regimes.items():
                count = info.get('count', 0)
                wr = info.get('win_rate')
                if count < REGIME_MIN_SAMPLE or wr is None:
                    continue
                if wr >= REGIME_MAX_WIN_RATE:
                    continue
                # check other regimes have decent win rates
                others = {r: d for r, d in regimes.items()
                          if r != regime and (d.get('win_rate') or 0) >= REGIME_MIN_OTHER_WIN_RATE}
                if not others:
                    continue
                if _already_exists(existing, 'regime_suppress', strat, 'regime', regime):
                    continue
                if (strat, 'regime_suppress', regime) in rejected_keys:
                    continue

                allowed = [r for r in regimes if r != regime]
                others_str = ', '.join(
                    r + ':' + str(round(d['win_rate'] * 100, 1)) + '%'
                    for r, d in others.items()
                )
                sid = f'regime_{strat}_{regime}_{datetime.now(timezone.utc).strftime("%Y%m%d")}_{len(new_suggestions)+1:03d}'
                sug = {
                    'id': sid,
                    'type': 'regime_suppress',
                    'generated_at': datetime.now(timezone.utc).isoformat(),
                    'strategy': strat,
                    'status': 'pending_review',
                    'confidence': 'medium' if count < 80 else 'high',
                    'sample_size': count,
                    'evidence_summary': f'{strat} in {regime}: win rate {round(wr*100,1)}% across {count} trades — suppress regime',
                    'reasoning': (
                        f'{strat} performs poorly in {regime} regime: {round(wr*100,1)}% win rate '
                        f'across {count} trades. Other regimes show better results '
                        f'({others_str}). '
                        f'Suppressing {regime} should improve overall quality.'
                    ),
                    'regime': regime,
                    'current_behavior': 'active',
                    'win_rate_in_regime': round(wr, 4),
                    'comparison_regimes': {r: round(d['win_rate'], 4) for r, d in others.items()},
                    'api_payload': {'allowed_volatility': allowed},
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
        'suggestions': merged,
    }
    _write_pending(result)
    logger.info(f'run_strategy_proposal_check: done, {len(new_suggestions)} new suggestions, {len(merged)} total')
    return result
