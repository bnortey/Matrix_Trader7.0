import sqlite3
import json
import os
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

MODELS_DIR = os.path.join(os.path.dirname(__file__), 'models')
MIN_THRESHOLD_SAMPLE = 30

FEATURES = [
    'conviction', 'rsi_1h', 'trend_score', 'atr_pct',
    'funding_rate', 'imbalance',
    'agent_shadow_delta', 'agent_shadow_disagreement',
    'agent_narrative_bull', 'agent_structural_bull',
]

FEATURE_SQL = {
    'conviction':                  'CAST(conviction AS REAL)',
    'rsi_1h':                      'CAST(rsi_1h AS REAL)',
    'trend_score':                 'CAST(trend_score AS REAL)',
    'atr_pct':                     'CAST(atr_pct AS REAL)',
    'funding_rate':                'CAST(funding_rate AS REAL)',
    'imbalance':                   "CAST(json_extract(signal_json, '$.imbalance') AS REAL)",
    'agent_shadow_delta':          "CAST(json_extract(signal_json, '$.agent_shadow_delta') AS REAL)",
    'agent_shadow_disagreement':   "CAST(json_extract(signal_json, '$.agent_shadow_disagreement') AS REAL)",
    'agent_narrative_bull':        "CAST(json_extract(signal_json, '$.agent_narrative_bull') AS REAL)",
    'agent_structural_bull':       "CAST(json_extract(signal_json, '$.agent_structural_bull') AS REAL)",
}

def _write_json(filename, data):
    path = os.path.join(MODELS_DIR, filename)
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _closed_pnl_rows(cur, strategy_key, threshold):
    cur.execute("""
        SELECT result, pnl_pct, logged_at
        FROM signals
        WHERE strategy_key = ?
          AND result IN ('WIN','PARTIAL','LOSS')
          AND pnl_pct IS NOT NULL
          AND conviction >= ?
        ORDER BY logged_at ASC
    """, (strategy_key, threshold))
    return cur.fetchall()


def _max_loss_streak(rows):
    longest = 0
    current = 0
    for result, pnl, _ in rows:
        if result == 'LOSS' or float(pnl or 0) < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _threshold_metrics(rows):
    total = len(rows)
    if not total:
        return None
    wins = sum(1 for r, _, _ in rows if r == 'WIN')
    partials = sum(1 for r, _, _ in rows if r == 'PARTIAL')
    losses = sum(1 for r, _, _ in rows if r == 'LOSS')
    pnls = [float(p or 0.0) for _, p, _ in rows]
    avg_net = sum(pnls) / len(pnls)
    return {
        'sample_size': total,
        'wins': wins,
        'partials': partials,
        'losses': losses,
        'strict_win_rate': wins / total,
        'win_partial_rate': (wins + partials) / total,
        'net_expectancy': avg_net,
        'total_net_pnl': sum(pnls),
        'max_loss_streak': _max_loss_streak(rows),
    }


def _objective_score(metrics):
    if not metrics:
        return None
    # Optimize net EV first. Use W+P and loss clustering only as tie-breakers
    # so the learner never prefers prettier labels over worse net P&L.
    return (
        metrics['net_expectancy']
        + metrics['win_partial_rate'] * 0.25
        - metrics['max_loss_streak'] * 0.05
    )


def run_feature_analysis(db_path):
    logger.info('run_feature_analysis: starting')
    t0 = datetime.now(timezone.utc)
    try:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
        cur = conn.cursor()

        cur.execute('SELECT COUNT(*) FROM signals WHERE result IS NOT NULL')
        total = cur.fetchone()[0] or 0

        cur.execute("SELECT COUNT(*) FROM signals WHERE result IS NOT NULL AND pnl_pct IS NOT NULL")
        with_pnl = cur.fetchone()[0] or 0

        features_out = {}
        for feat, sql_expr in FEATURE_SQL.items():
            try:
                cur.execute(f"""
                    SELECT
                        AVG(CASE WHEN pnl_pct > 0 THEN {sql_expr} END),
                        AVG(CASE WHEN pnl_pct < 0 THEN {sql_expr} END),
                        COUNT(CASE WHEN pnl_pct > 0 AND {sql_expr} IS NOT NULL THEN 1 END),
                        COUNT(CASE WHEN pnl_pct < 0 AND {sql_expr} IS NOT NULL THEN 1 END),
                        MIN({sql_expr}), MAX({sql_expr})
                    FROM signals
                    WHERE result IN ('WIN','PARTIAL','LOSS')
                      AND pnl_pct IS NOT NULL
                """)
                row = cur.fetchone()
                pos_mean, neg_mean, pos_count, neg_count, fmin, fmax = row
                if pos_mean is None or neg_mean is None:
                    divergence = None
                    tier = 'insufficient'
                else:
                    divergence = abs(pos_mean - neg_mean)
                    frange = (fmax - fmin) if fmax is not None and fmin is not None and fmax != fmin else 1
                    pct = divergence / frange if frange else 0
                    if pos_count < 10 or neg_count < 10:
                        tier = 'insufficient'
                    elif pct > 0.20:
                        tier = 'high'
                    elif pct > 0.10:
                        tier = 'medium'
                    else:
                        tier = 'low'
                features_out[feat] = {
                    'positive_pnl_mean': round(pos_mean, 4) if pos_mean is not None else None,
                    'negative_pnl_mean': round(neg_mean, 4) if neg_mean is not None else None,
                    'divergence': round(divergence, 4) if divergence is not None else None,
                    'positive_pnl_count': pos_count or 0,
                    'negative_pnl_count': neg_count or 0,
                    'predictive_tier': tier,
                }
            except Exception as ex:
                logger.warning(f'feature {feat} error: {ex}')
                features_out[feat] = {'predictive_tier': 'insufficient', 'positive_pnl_count': 0, 'negative_pnl_count': 0}

        top_predictors = sorted(
            [f for f in features_out if features_out[f].get('divergence') is not None],
            key=lambda f: features_out[f]['divergence'],
            reverse=True
        )

        conn.close()
        elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
        result = {
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'total_signals': total,
            'signals_with_pnl': with_pnl,
            'features': features_out,
            'top_predictors': top_predictors,
            'note': f'preliminary — {total} total closed trades',
        }
        _write_json('feature_weights.json', result)
        logger.info(f'run_feature_analysis: done in {elapsed:.1f}s, {total} signals, top={top_predictors[:3]}')
        return result
    except Exception as e:
        logger.error(f'run_feature_analysis failed: {e}')
        raise


def run_threshold_analysis(db_path):
    logger.info('run_threshold_analysis: starting')
    t0 = datetime.now(timezone.utc)
    try:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
        cur = conn.cursor()

        cur.execute("SELECT DISTINCT strategy_key FROM signals WHERE strategy_key IS NOT NULL AND result IS NOT NULL")
        strategies = [r[0] for r in cur.fetchall()]

        out = {}
        for strat in strategies:
            cur.execute("""
                SELECT MIN(conviction) FROM signals
                WHERE strategy_key = ? AND result IS NOT NULL AND conviction IS NOT NULL
            """, (strat,))
            row = cur.fetchone()
            implied_min = int(row[0]) if row and row[0] is not None else 55

            best_thresh = implied_min
            best_metrics = None
            best_score = None
            best_count = 0
            current_metrics = _threshold_metrics(_closed_pnl_rows(cur, strat, implied_min))

            for t in range(55, 91):
                metrics = _threshold_metrics(_closed_pnl_rows(cur, strat, t))
                if not metrics or metrics['sample_size'] < MIN_THRESHOLD_SAMPLE:
                    continue
                score = _objective_score(metrics)
                if best_score is None or score > best_score:
                    best_score = score
                    best_thresh = t
                    best_metrics = metrics
                    best_count = metrics['sample_size']

            confidence = 'low' if best_count < 30 else ('medium' if best_count < 80 else 'high')
            current_ne = current_metrics['net_expectancy'] if current_metrics else None
            best_ne = best_metrics['net_expectancy'] if best_metrics else None
            out[strat] = {
                'current_implied_threshold': implied_min,
                'optimal_threshold': best_thresh,
                'current_net_expectancy': round(current_ne, 4) if current_ne is not None else None,
                'optimal_net_expectancy': round(best_ne, 4) if best_ne is not None else None,
                'net_expectancy_delta': round(best_ne - current_ne, 4) if best_ne is not None and current_ne is not None else None,
                'current_win_rate': round(current_metrics['strict_win_rate'], 4) if current_metrics else None,
                'optimal_win_rate': round(best_metrics['strict_win_rate'], 4) if best_metrics else None,
                'current_win_partial_rate': round(current_metrics['win_partial_rate'], 4) if current_metrics else None,
                'optimal_win_partial_rate': round(best_metrics['win_partial_rate'], 4) if best_metrics else None,
                'current_max_loss_streak': current_metrics['max_loss_streak'] if current_metrics else None,
                'optimal_max_loss_streak': best_metrics['max_loss_streak'] if best_metrics else None,
                'optimal_sample_size': best_count,
                'delta_from_current': best_thresh - implied_min,
                'confidence': confidence,
                'objective': 'net_ev_primary_wp_and_loss_streak_tiebreak',
                'note': f'{best_count} net-P&L trades at threshold {best_thresh}',
            }

        conn.close()
        elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
        result = {
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'strategies': out,
        }
        _write_json('conviction_thresholds.json', result)
        logger.info(f'run_threshold_analysis: done in {elapsed:.1f}s, {len(strategies)} strategies')
        return result
    except Exception as e:
        logger.error(f'run_threshold_analysis failed: {e}')
        raise


def run_regime_analysis(db_path):
    logger.info('run_regime_analysis: starting')
    t0 = datetime.now(timezone.utc)
    try:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
        cur = conn.cursor()

        cur.execute("""
            SELECT strategy_key,
                   json_extract(signal_json, '$.agent_regime') as regime,
                   result, pnl_pct
            FROM signals
            WHERE result IS NOT NULL
              AND json_extract(signal_json, '$.agent_regime') IS NOT NULL
        """)
        rows = cur.fetchall()
        conn.close()

        from collections import defaultdict
        groups = defaultdict(lambda: defaultdict(list))
        for strat, regime, result, pnl in rows:
            if strat and regime:
                groups[strat][regime].append((result, pnl))

        out = {}
        for strat, regimes in groups.items():
            out[strat] = {}
            for regime, trades in regimes.items():
                wins = sum(1 for r, _ in trades if r == 'WIN')
                losses = sum(1 for r, _ in trades if r == 'LOSS')
                partials = sum(1 for r, _ in trades if r == 'PARTIAL')
                denom = wins + losses + partials
                wr = wins / denom if denom else None
                wpr = (wins + partials) / denom if denom else None
                pnls = [p for _, p in trades if p is not None]
                avg_pnl = sum(pnls) / len(pnls) if pnls else None
                note = 'insufficient' if len(trades) < 10 else 'preliminary'
                out[strat][regime] = {
                    'win_rate': round(wr, 4) if wr is not None else None,
                    'win_partial_rate': round(wpr, 4) if wpr is not None else None,
                    'avg_pnl': round(avg_pnl, 2) if avg_pnl is not None else None,
                    'total_pnl': round(sum(pnls), 2) if pnls else None,
                    'count': len(trades),
                    'note': note,
                }

        elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
        result = {
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'by_strategy_regime': out,
        }
        _write_json('regime_performance.json', result)
        logger.info(f'run_regime_analysis: done in {elapsed:.1f}s')
        return result
    except Exception as e:
        logger.error(f'run_regime_analysis failed: {e}')
        raise
