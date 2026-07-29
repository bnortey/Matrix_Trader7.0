#!/usr/bin/env python3
"""
Coach Pattern Analyst — synthesizes coach review text into qualitative strategy patterns.
Reads coach_review fields from signal_json in signals.db.
Writes coach_pattern briefs to research/briefs.json alongside numeric hypothesis briefs.

Rules:
- Uses the shared MT7 AI fallback chain: Claude first, then configured/free providers.
- Never raises — all failures are logged and skipped.
- Re-synthesizes a strategy only when 10+ new reviews have accumulated since last run.
"""

import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

MIN_REVIEWS       = 10   # minimum reviews before synthesis
MAX_REVIEWS_BATCH = 18   # cap to stay within fallback model token limits
REVIEW_EXCERPT_CHARS = 650
PROMPT_CHAR_LIMIT = 18000
REFRESH_DELTA     = 10   # re-synthesize when review count grows by this much
COACH_PATTERN_VERSION = 'coach-pattern-v2-structured'


def _sanitize_review(value: str) -> str:
    text = re.sub(
        r'<think\b[^>]*>.*?</think\s*>',
        '',
        str(value or ''),
        flags=re.IGNORECASE | re.DOTALL,
    ).strip()
    return text


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _load_env_files() -> None:
    for env_path in [
        os.path.join(os.path.dirname(__file__), '.env'),
        '/opt/matrix-trader/.env',
    ]:
        if not os.path.exists(env_path):
            continue
        try:
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#') or '=' not in line:
                        continue
                    key, value = line.split('=', 1)
                    os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        except Exception as e:
            print(f'[coach_analyst] env load skipped {env_path}: {e}')


def _call_ai_fallback(prompt: str, max_tokens: int = 700) -> str | None:
    _load_env_files()
    try:
        matrix_root = os.environ.get('MT7_APP_DIR', '/opt/matrix-trader')
        local_root = Path(__file__).resolve().parents[1]
        for path in (matrix_root, str(local_root)):
            if path and path not in sys.path:
                sys.path.insert(0, path)
        from lib.ai_client import call_ai

        # The shared feature route owns the default. These env vars remain an
        # explicit operational override only when both are set.
        provider = (os.environ.get('COACH_ANALYST_PROVIDER') or '').strip() or None
        model = (os.environ.get('COACH_ANALYST_MODEL') or '').strip() or None
        if not (provider and model):
            provider = None
            model = None
        result = call_ai(
            system=(
                'You are a quantitative trading researcher analyzing coach reviews. '
                'Identify specific, repeating patterns only. No generalisations. '
                'Respond with valid JSON only — no markdown, no prose outside the JSON.'
            ),
            user=prompt,
            max_tokens=max_tokens,
            provider=provider,
            model=model,
            feature='coach_pattern',
        )
        if result:
            route_note = f'{provider}/{model}' if provider and model else 'shared coach_pattern route'
            print(f'[coach_analyst] AI synthesis completed via {route_note}')
        return result
    except Exception as e:
        print(f'[coach_analyst] AI fallback error: {e}')
        return None


def _fetch_reviews_by_strategy(db_path: str) -> dict:
    """Return {strategy_key: [review_dicts]} for all trades with a stored coach_review."""
    by_strategy = {}
    try:
        con = sqlite3.connect(db_path)
        rows = con.execute("""
            SELECT strategy_key, symbol, direction, result, pnl_pct, volatility, signal_json
            FROM signals
            WHERE result IS NOT NULL
              AND result NOT IN ('EXPIRED', 'SKIPPED')
              AND signal_json IS NOT NULL
              AND json_extract(signal_json, '$.coach_review') IS NOT NULL
            ORDER BY logged_at DESC
        """).fetchall()
        con.close()
        for skey, sym, direction, result, pnl, vol, sj in rows:
            if not skey:
                continue
            try:
                signal_json = json.loads(sj)
                review = _sanitize_review(signal_json.get('coach_review', ''))
                packet = signal_json.get('coach_review_packet') or {}
            except Exception:
                continue
            if not review:
                continue
            by_strategy.setdefault(skey, []).append({
                'symbol':       sym,
                'direction':    direction,
                'result':       result,
                'pnl_pct':      pnl,
                'volatility':   vol,
                'coach_review': review,
                'coach_version': signal_json.get('coach_review_version'),
                'primary_issue': (packet.get('verdict') or {}).get('primary_issue'),
                'next_trade_rule': (packet.get('lesson') or {}).get('next_trade_rule'),
                'path_available': bool((packet.get('path') or {}).get('available')),
            })
    except Exception as e:
        print(f'[coach_analyst] db error: {e}')
    return by_strategy


def _load_briefs(research_dir: Path) -> dict:
    path = research_dir / 'briefs.json'
    if not path.exists():
        return {'briefs': []}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {'briefs': []}


def _write_briefs(research_dir: Path, data: dict):
    path = research_dir / 'briefs.json'
    tmp  = path.with_suffix('.json.tmp')
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _brief_id(strategy_key: str) -> str:
    return hashlib.md5(f'coach_pattern:{strategy_key}'.encode()).hexdigest()[:12]


def _needs_refresh(existing: dict | None, review_count: int) -> bool:
    if not existing:
        return True
    if (existing.get('evidence') or {}).get('contract_version') != COACH_PATTERN_VERSION:
        return True
    last = (existing.get('evidence') or {}).get('review_count', 0)
    return review_count >= last + REFRESH_DELTA


def _build_prompt(strategy_key: str, reviews: list) -> str:
    wins     = sum(1 for r in reviews if r['result'] == 'WIN')
    losses   = sum(1 for r in reviews if r['result'] == 'LOSS')
    partials = sum(1 for r in reviews if r['result'] == 'PARTIAL')
    worst_losses = sorted(
        [r for r in reviews if r['result'] == 'LOSS'],
        key=lambda r: float(r['pnl_pct'] or 0.0),
    )[:6]
    best_wins = sorted(
        [r for r in reviews if r['result'] in ('WIN', 'PARTIAL')],
        key=lambda r: float(r['pnl_pct'] or 0.0),
        reverse=True,
    )[:6]
    recent = reviews[:MAX_REVIEWS_BATCH]
    selected = []
    seen = set()
    for r in worst_losses + best_wins + recent:
        key = (r['symbol'], r['direction'], r['result'], r.get('pnl_pct'), r.get('coach_review', '')[:80])
        if key in seen:
            continue
        seen.add(key)
        selected.append(r)
        if len(selected) >= MAX_REVIEWS_BATCH:
            break

    header = (
        f'Strategy: {strategy_key}\n'
        f'Total reviews: {len(reviews)} ({wins} WIN, {losses} LOSS, {partials} PARTIAL)\n\n'
        'Sample includes worst losses, best positive outcomes, and recent reviews.\n'
        'Each review includes the stored structured diagnosis when available.\n\n'
    )
    body = ''
    for r in selected:
        pnl_s = f"{r['pnl_pct']:+.1f}%" if r['pnl_pct'] is not None else '?'
        review = (r['coach_review'] or '').replace('\n', ' ').strip()
        if len(review) > REVIEW_EXCERPT_CHARS:
            review = review[:REVIEW_EXCERPT_CHARS].rsplit(' ', 1)[0] + '...'
        body += (
            f"[{r['result']} {pnl_s} | {r['volatility'] or '?'} vol | "
            f"{r['direction']} | {r['symbol']} | issue={r.get('primary_issue') or 'legacy'} | "
            f"path={'yes' if r.get('path_available') else 'no'}]\n"
        )
        body += review + '\n---\n'

    instruction = (
        '\nAnalyse these reviews and respond with ONLY this JSON (no other text):\n'
        '{\n'
        '  "primary_finding": "The single most important pattern across all trades (1-2 sentences)",\n'
        '  "failure_patterns": ["specific pattern 1", "specific pattern 2", "specific pattern 3"],\n'
        '  "success_patterns": ["specific pattern 1", "specific pattern 2"],\n'
        '  "actionable_suggestion": "One concrete shadow-only filter or measurement rule to test"\n'
        '}\n'
        'Only include patterns present in at least 20% of relevant trades. Be specific, not generic. '
        'Never recommend raising leverage, position size, or risk. A single review cannot change a strategy.'
    )
    prompt = header + body + instruction
    if len(prompt) > PROMPT_CHAR_LIMIT:
        prompt = prompt[:PROMPT_CHAR_LIMIT].rsplit('---', 1)[0] + instruction
    return prompt


def _deterministic_pattern_summary(reviews: list) -> dict:
    issues = Counter(
        str(r.get('primary_issue') or 'unclassified')
        for r in reviews
    )
    loss_issues = Counter(
        str(r.get('primary_issue') or 'unclassified')
        for r in reviews if r.get('result') == 'LOSS'
    )
    positive_issues = Counter(
        str(r.get('primary_issue') or 'unclassified')
        for r in reviews if r.get('result') in ('WIN', 'PARTIAL')
    )
    threshold = max(1, int(len(reviews) * 0.20))
    recurring = [
        (issue, count) for issue, count in issues.most_common()
        if count >= threshold and issue != 'unclassified'
    ]
    dominant = recurring[0][0] if recurring else 'no single structured diagnosis'
    failure_patterns = [
        f"{issue.replace('_', ' ')} ({count}/{len(reviews)} reviews)"
        for issue, count in loss_issues.most_common(3)
        if count >= threshold
    ]
    success_patterns = [
        f"{issue.replace('_', ' ')} ({count}/{len(reviews)} reviews)"
        for issue, count in positive_issues.most_common(3)
        if count >= threshold
    ]
    return {
        'primary_finding': (
            f"The most frequent structured diagnosis is {dominant.replace('_', ' ')}; "
            "this is descriptive until a forward, predeclared cohort test confirms it."
        ),
        'failure_patterns': failure_patterns,
        'success_patterns': success_patterns,
        'actionable_suggestion': (
            f"Shadow-test whether excluding or separately tagging {dominant.replace('_', ' ')} "
            "setups improves net EV and profit factor without reducing trade count by more than 40%."
            if recurring else
            "Keep collecting structured reviews; no diagnosis currently clears the 20% recurrence floor."
        ),
    }


def run_coach_analysis(db_path: str, research_dir: Path) -> dict:
    """Main entry point. Returns {strategies_analyzed, briefs_updated}."""
    by_strategy = _fetch_reviews_by_strategy(db_path)
    eligible    = {k: v for k, v in by_strategy.items() if len(v) >= MIN_REVIEWS}

    if not eligible:
        print(f'[coach_analyst] no strategy has >= {MIN_REVIEWS} reviews yet '
              f'(total strategies with reviews: {len(by_strategy)})')
        return {'strategies_analyzed': 0, 'briefs_updated': 0}

    briefs_data = _load_briefs(research_dir)
    existing_by_id = {b['id']: b for b in briefs_data.get('briefs', [])
                      if b.get('type') == 'coach_pattern'}

    updated = 0
    for strategy_key, reviews in eligible.items():
        bid      = _brief_id(strategy_key)
        existing = existing_by_id.get(bid)

        if not _needs_refresh(existing, len(reviews)):
            print(f'[coach_analyst] {strategy_key}: skipping — only '
                  f'{len(reviews) - (existing.get("evidence",{}).get("review_count",0))} new reviews')
            continue

        print(f'[coach_analyst] synthesising {strategy_key} ({len(reviews)} reviews)...')
        raw = _call_ai_fallback(_build_prompt(strategy_key, reviews))
        deterministic = _deterministic_pattern_summary(reviews)

        # Parse JSON — strip markdown fences if model included them
        parsed = deterministic
        if raw:
            try:
                clean = raw.strip()
                if '```' in clean:
                    clean = clean.split('```')[1]
                    if clean.startswith('json'):
                        clean = clean[4:]
                candidate = json.loads(clean.strip())
                if isinstance(candidate, dict):
                    parsed = {
                        'primary_finding': candidate.get('primary_finding') or deterministic['primary_finding'],
                        'failure_patterns': candidate.get('failure_patterns') or deterministic['failure_patterns'],
                        'success_patterns': candidate.get('success_patterns') or deterministic['success_patterns'],
                        'actionable_suggestion': candidate.get('actionable_suggestion') or deterministic['actionable_suggestion'],
                    }
            except Exception:
                parsed = deterministic
        suggestion = str(parsed.get('actionable_suggestion') or deterministic['actionable_suggestion'])
        if any(term in suggestion.lower() for term in ('raise leverage', 'increase leverage', 'increase position', 'size up')):
            suggestion = deterministic['actionable_suggestion']
        if 'shadow' not in suggestion.lower() and 'collect' not in suggestion.lower():
            suggestion = 'Shadow-test only: ' + suggestion

        win_rate = round(sum(1 for r in reviews if r['result'] == 'WIN') / len(reviews), 3)

        brief = {
            'id':           bid,
            'type':         'coach_pattern',
            'strategy_key': strategy_key,
            'status':       'active',
            'confidence':   'emerging' if len(reviews) >= 30 else 'watching',
            'title':        f"{strategy_key.replace('_', ' ').title()} — Coach Pattern Analysis",
            'thesis':       parsed.get('primary_finding') or 'No dominant pattern identified yet.',
            'what_is_novel': suggestion or None,
            'evidence': {
                'review_count':     len(reviews),
                'win_rate':         win_rate,
                'failure_patterns': parsed.get('failure_patterns', []),
                'success_patterns': parsed.get('success_patterns', []),
                'pattern_type':     'qualitative',
                'contract_version': COACH_PATTERN_VERSION,
                'structured_review_count': sum(
                    1 for r in reviews if r.get('coach_version')
                ),
                'path_evidence_count': sum(
                    1 for r in reviews if r.get('path_available')
                ),
                'deterministic_baseline': deterministic,
                'risk_authority': 'none',
            },
            'generated_at': existing.get('generated_at', _now_iso()) if existing else _now_iso(),
            'last_updated': _now_iso(),
        }

        briefs_data['briefs'] = [b for b in briefs_data.get('briefs', []) if b.get('id') != bid]
        briefs_data['briefs'].append(brief)
        updated += 1
        print(f'[coach_analyst] {strategy_key}: brief updated '
              f'(failure_patterns={len(parsed.get("failure_patterns",[]))}, '
              f'success_patterns={len(parsed.get("success_patterns",[]))})')

        time.sleep(4)  # rate limit between strategies

    if updated:
        _write_briefs(research_dir, briefs_data)
        print(f'[coach_analyst] wrote {updated} updated briefs to {research_dir / "briefs.json"}')

    return {'strategies_analyzed': len(eligible), 'briefs_updated': updated}
