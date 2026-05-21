# Cipher Research Group Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Cipher Research Group — 12 named analysts with Matrix/Industry personalities, an org chart, agent bio cards, and daily/weekly market intelligence reports inside the Intelligence tab.

**Architecture:** Three-phase build. Phase 1 adds `AGENT_ROSTER` to `lib/agents.py` and injects personality into each analyst's LLM system prompt. Phase 2 adds four new routes to `app.py` with report generation, DB queries, live ticker fetches, and AI narrative caching. Phase 3 restructures the Intelligence tab in `index.html` into three sub-tabs (Overview / The Firm / Reports) and renders the full report UI.

**Tech Stack:** Python/Flask backend, SQLite via stdlib, vanilla JS + inline HTML, `call_ai()` from `lib/ai_client.py`, MEXC public ticker API, existing `data/signals.db`.

---

## File Map

| File | Change |
|---|---|
| `lib/agents.py` | Add `AGENT_ROSTER` dict (module-level); inject personality prefix into each `_run_*` system message |
| `app.py` | Add `GET /api/intelligence/roster`; add `_build_daily_data()`, `_build_weekly_data()`, `_generate_report_narrative()`, `_load_or_build_report()`; add `GET /api/intelligence/reports/daily`, `GET /api/intelligence/reports/weekly`, `POST /api/intelligence/reports/regenerate`; add `data/reports/` mkdir to `init_db()` |
| `templates/index.html` | Extend `I` state; restructure intelligence-section HTML with sub-tab row; add `switchIntelTab()`, `renderFirm()`, `renderAgentBios()`, `renderReportPanel()`, `renderDailyBrief()`, `renderWeeklyExtra()`; update `loadIntelligence()` |
| `.gitignore` | Add `data/reports/daily_*.json` and `data/reports/weekly_*.json` |

---

## Phase 1 — AGENT_ROSTER & Personality Injection

### Task 1: Add AGENT_ROSTER to lib/agents.py

**Files:**
- Modify: `lib/agents.py` (after `REGIME_WEIGHTS` dict, ~line 111)

- [ ] **Step 1: Add AGENT_ROSTER constant after REGIME_WEIGHTS**

Open `lib/agents.py`. After the closing brace of `REGIME_WEIGHTS`, insert:

```python
AGENT_ROSTER = {
    "trader": {
        "name": "Thomas Reeves",
        "title": "Chief Investment Officer",
        "division": "leadership",
        "specialty": "Synthesizes all research into the final conviction delta",
        "voice": 'Quiet, decisive, sees patterns. "The conviction is there. We move."',
        "exchanges": ["MEXC", "HYPERLIQUID", "BYBIT"],
    },
    "risk_manager": {
        "name": "Harper Cross",
        "title": "Chief Risk Officer",
        "division": "leadership",
        "specialty": "Hard blocks, position gates, independent veto on any signal",
        "voice": 'Ruthless gatekeeper. "This trade doesn\'t pass. Full stop."',
        "exchanges": ["MEXC", "HYPERLIQUID", "BYBIT"],
    },
    "narrative_debate": {
        "name": "Daria Wren",
        "title": "Head of Narrative Research",
        "division": "narrative",
        "specialty": "Narrative debate chair — synthesizes macro, fundamentals, and sentiment",
        "voice": 'Cold, precise, intimidating. "Three data points confirm it."',
        "exchanges": ["MEXC", "HYPERLIQUID", "BYBIT"],
    },
    "tokenomics": {
        "name": "Priya Nair",
        "title": "Tokenomics Lead",
        "division": "narrative",
        "specialty": "On-chain supply, unlock risk, float fragility, tokenomics pressure",
        "voice": "Methodical. Flags unlock risk before anyone else.",
        "exchanges": ["MEXC", "HYPERLIQUID"],
    },
    "sentiment": {
        "name": "Hari Stern",
        "title": "Sentiment & Social Intelligence",
        "division": "narrative",
        "specialty": "Social momentum, sentiment credibility, crowd psychology",
        "voice": 'Eager, anxious, loyal. "Sentiment is... complicated. Leaning bullish."',
        "exchanges": ["MEXC", "HYPERLIQUID"],
    },
    "news": {
        "name": "Yasmin Cole",
        "title": "News & Catalyst Lead",
        "division": "narrative",
        "specialty": "Event-driven signals, macro catalysts, news-driven regime shifts",
        "voice": 'Socially intelligent. "The catalyst is real. Market knows."',
        "exchanges": ["MEXC", "HYPERLIQUID"],
    },
    "technical": {
        "name": "Rishi Sackey",
        "title": "Technical Strategy Lead",
        "division": "narrative",
        "specialty": "Price structure, EMA alignment, RSI context, late-move detection",
        "voice": 'Grinding detail. "RSI 34.2, EMA crossover confirmed, trend score -12."',
        "exchanges": ["MEXC", "HYPERLIQUID", "BYBIT"],
    },
    "structural_debate": {
        "name": "Eric Tao",
        "title": "Head of Market Structure",
        "division": "structural",
        "specialty": "Structural debate chair — synthesizes order flow, funding, and regime",
        "voice": 'Commanding, strategic. "Structure is holding. The desk agrees."',
        "exchanges": ["MEXC", "HYPERLIQUID", "BYBIT"],
    },
    "microstructure": {
        "name": "Niobe Reyes",
        "title": "Microstructure & Order Flow",
        "division": "structural",
        "specialty": "Book imbalance, microprice deviation, spread pressure, aggressive flow",
        "voice": 'Skeptical, precise. "Order book says one thing. I don\'t trust it."',
        "exchanges": ["MEXC", "HYPERLIQUID"],
    },
    "funding": {
        "name": "Kenny Hassan",
        "title": "Funding & Positioning Strategist",
        "division": "structural",
        "specialty": "Funding rates, OI delta trends, liquidation proximity, crowded positioning",
        "voice": 'Old hand. "Funding negative three sessions running. Shorts are getting squeezed."',
        "exchanges": ["MEXC", "HYPERLIQUID"],
    },
    "cross_venue": {
        "name": "Ghost Kimura",
        "title": "Cross-Venue Intelligence Lead",
        "division": "structural",
        "specialty": "MEXC vs Hyperliquid vs Bybit basis, venue-leader detection, arb pressure",
        "voice": 'Cool, detached. "MEXC and HL diverging 0.3%. That\'s meaningful."',
        "exchanges": ["MEXC", "HYPERLIQUID", "BYBIT"],
    },
    "regime": {
        "name": "Nadia Okonkwo",
        "title": "Volatility & Regime Specialist",
        "division": "structural",
        "specialty": "Regime classification, ATR context, BTC correlation, no-trade-zone detection",
        "voice": 'Calm authority. "We\'re in volatile squeeze. Treat accordingly."',
        "exchanges": ["MEXC", "HYPERLIQUID", "BYBIT"],
    },
}

FIRM_META = {
    "name": "Cipher Research Group",
    "tagline": "We read the market's structure. Not its noise.",
    "exchange_coverage": ["MEXC", "HYPERLIQUID", "BYBIT"],
}
```

- [ ] **Step 2: Verify import-safe**

```bash
cd /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0
python3 -c "from lib.agents import AGENT_ROSTER, FIRM_META; print(len(AGENT_ROSTER), 'analysts')"
```

Expected: `12 analysts`

- [ ] **Step 3: Commit**

```bash
git add lib/agents.py
git commit -m "feat: add AGENT_ROSTER and FIRM_META to lib/agents.py"
```

---

### Task 2: Inject personality into each _run_* system prompt

**Files:**
- Modify: `lib/agents.py` — 8 analyst functions + 2 debate functions + trader + risk_manager

Each `_run_*` function has a `system = (...)` string. Prepend a one-line persona prefix. Pattern for every function:

```python
# BEFORE (example from _run_tokenomics_analyst):
system = (
    "You are a tokenomics analyst for a crypto perp trading system. "
    ...
)

# AFTER:
persona = AGENT_ROSTER["tokenomics"]
system = (
    f"You are {persona['name']}, {persona['title']} at Cipher Research Group. "
    f"Personality: {persona['voice']} "
    "You are a tokenomics analyst for a crypto perp trading system. "
    ...
)
```

- [ ] **Step 1: Update _run_tokenomics_analyst** (~line 205)

Replace the `system = (` block in `_run_tokenomics_analyst`:

```python
        persona = AGENT_ROSTER["tokenomics"]
        system = (
            f"You are {persona['name']}, {persona['title']} at Cipher Research Group. "
            f"Personality: {persona['voice']} "
            "Assess float fragility from available market structure data. "
            "Output only JSON."
        )
```

- [ ] **Step 2: Update _run_sentiment_analyst** (~line 234)

```python
        persona = AGENT_ROSTER["sentiment"]
        system = (
            f"You are {persona['name']}, {persona['title']} at Cipher Research Group. "
            f"Personality: {persona['voice']} "
            "Infer market sentiment from price and volume data. "
            "Output only JSON."
        )
```

- [ ] **Step 3: Update _run_news_analyst** (~line 261)

```python
        persona = AGENT_ROSTER["news"]
        system = (
            f"You are {persona['name']}, {persona['title']} at Cipher Research Group. "
            f"Personality: {persona['voice']} "
            "Assess news and catalyst risk for a crypto perp trading system. "
            "Output only JSON."
        )
```

- [ ] **Step 4: Update _run_technical_analyst** (~line 290)

```python
        persona = AGENT_ROSTER["technical"]
        system = (
            f"You are {persona['name']}, {persona['title']} at Cipher Research Group. "
            f"Personality: {persona['voice']} "
            "Assess technical structure for a crypto perp trading system. "
            "Output only JSON."
        )
```

- [ ] **Step 5: Update _run_microstructure_analyst** (~line 327)

```python
        persona = AGENT_ROSTER["microstructure"]
        system = (
            f"You are {persona['name']}, {persona['title']} at Cipher Research Group. "
            f"Personality: {persona['voice']} "
            "Assess order book pressure for a crypto perp trading system. "
            "Output only JSON."
        )
```

- [ ] **Step 6: Update _run_funding_analyst** (~line 365)

```python
        persona = AGENT_ROSTER["funding"]
        system = (
            f"You are {persona['name']}, {persona['title']} at Cipher Research Group. "
            f"Personality: {persona['voice']} "
            "Assess funding and positioning signals. "
            "Output only JSON."
        )
```

- [ ] **Step 7: Update _run_cross_venue_analyst** (~line 399)

```python
        persona = AGENT_ROSTER["cross_venue"]
        system = (
            f"You are {persona['name']}, {persona['title']} at Cipher Research Group. "
            f"Personality: {persona['voice']} "
            "Assess cross-venue price and basis divergence. "
            "Output only JSON."
        )
```

- [ ] **Step 8: Update _run_regime_analyst** (~line 437)

```python
        persona = AGENT_ROSTER["regime"]
        system = (
            f"You are {persona['name']}, {persona['title']} at Cipher Research Group. "
            f"Personality: {persona['voice']} "
            "Classify volatility regime as: trending, choppy, volatile_squeeze, news_catalyst, low_liquidity, institutional. "
            "Output only JSON."
        )
```

- [ ] **Step 9: Verify import still clean**

```bash
python3 -c "from lib.agents import run_agent_pipeline; print('ok')"
```

Expected: `ok`

- [ ] **Step 10: Commit**

```bash
git add lib/agents.py
git commit -m "feat: inject Cipher Research Group personas into analyst system prompts"
```

---

## Phase 2 — Backend Report APIs

### Task 3: Add data/reports/ directory init + /api/intelligence/roster route

**Files:**
- Modify: `app.py` — `init_db()` function and new route

- [ ] **Step 1: Add data/reports/ mkdir to init_db()**

In `init_db()` (~line 203), after `os.makedirs("data", exist_ok=True)` add:

```python
    os.makedirs("data/reports", exist_ok=True)
```

- [ ] **Step 2: Add roster route to app.py**

Find the block of `/api/intelligence/*` routes (~line 6001). After `api_intelligence_research()`, add:

```python
@app.route('/api/intelligence/roster')
def api_intelligence_roster():
    """Return Cipher Research Group analyst roster and firm metadata."""
    try:
        from lib.agents import AGENT_ROSTER, FIRM_META
        return jsonify({
            'success': True,
            'firm': FIRM_META,
            'agents': AGENT_ROSTER,
        })
    except Exception as e:
        print(f'[api/intelligence/roster] error: {e}', file=sys.stderr)
        return jsonify({'success': False, 'error': str(e)}), 500
```

- [ ] **Step 3: Verify with curl**

```bash
python3 app.py &
sleep 2
curl -s http://localhost:8080/api/intelligence/roster | python3 -m json.tool | head -20
kill %1
```

Expected: JSON with `firm.name = "Cipher Research Group"` and `agents` dict with 12 keys.

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "feat: add /api/intelligence/roster route and data/reports/ directory init"
```

---

### Task 4: Add report data builder helpers to app.py

**Files:**
- Modify: `app.py` — add helper functions above the intelligence routes

Add these helpers in `app.py` just before the `@app.route('/api/intelligence/roster')` block. They query `signals.db`, the MEXC ticker, and mt-learner files.

- [ ] **Step 1: Add _report_classify_session() helper**

```python
def _report_classify_session(utc_iso: str) -> str:
    """Classify a UTC ISO timestamp into trading session."""
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(utc_iso.replace('Z', ''))
        h = dt.hour
        if 0 <= h < 8:
            return 'ASIA'
        if 8 <= h < 13:
            return 'LONDON'
        if 13 <= h < 21:
            return 'NY'
        return 'ASIA'  # 21-24 UTC = late NY / early Asia
    except Exception:
        return 'UNKNOWN'
```

- [ ] **Step 2: Add _report_fetch_ticker_snapshot() helper**

```python
def _report_fetch_ticker_snapshot() -> list:
    """Fetch MEXC ticker for funding heatmap and movers. Returns list of dicts."""
    try:
        resp = requests.get(
            'https://contract.mexc.com/api/v1/contract/ticker',
            timeout=10
        )
        data = resp.json()
        if data.get('success') and data.get('data'):
            return data['data']
    except Exception as e:
        print(f'[report_ticker] {e}', file=sys.stderr)
    return []
```

- [ ] **Step 3: Add _build_daily_data() helper**

```python
def _build_daily_data(date_str: str) -> dict:
    """
    Build all template-driven (no AI) data for a daily report.
    date_str: 'YYYY-MM-DD'
    """
    import json as _json
    from datetime import datetime, timedelta

    con = get_db()
    data = {}

    # ── Signals today ──────────────────────────────────────────────
    rows = con.execute("""
        SELECT symbol, exchange, direction, strategy, strategy_key,
               conviction, funding_rate, change_24h_pct, volume_24h,
               atr_pct, trend_score, result, logged_at, signal_json
        FROM signals
        WHERE date(logged_at) = ?
        ORDER BY conviction DESC
    """, (date_str,)).fetchall()
    cols = ['symbol','exchange','direction','strategy','strategy_key',
            'conviction','funding_rate','change_24h_pct','volume_24h',
            'atr_pct','trend_score','result','logged_at','signal_json']
    signals_today = [dict(zip(cols, r)) for r in rows]

    data['signals_today'] = signals_today
    data['signal_count'] = len(signals_today)
    data['exchange_breakdown'] = {}
    for s in signals_today:
        ex = s.get('exchange', 'MEXC')
        data['exchange_breakdown'][ex] = data['exchange_breakdown'].get(ex, 0) + 1

    # ── Blocked signals today ──────────────────────────────────────
    blocked_rows = con.execute("""
        SELECT symbol, exchange, direction, gate_key, gate_mode, logged_at
        FROM filtered_candidates
        WHERE date(logged_at) = ?
    """, (date_str,)).fetchall()
    data['blocked_count'] = len(blocked_rows)
    data['blocked'] = [dict(zip(['symbol','exchange','direction','gate_key','gate_mode','logged_at'], r))
                       for r in blocked_rows]

    # ── Session breakdown ──────────────────────────────────────────
    sessions = {'ASIA': [], 'LONDON': [], 'NY': [], 'UNKNOWN': []}
    for s in signals_today:
        sess = _report_classify_session(s.get('logged_at', ''))
        sessions[sess].append(s)
    data['sessions'] = {k: {'count': len(v), 'symbols': [x['symbol'] for x in v]}
                        for k, v in sessions.items()}

    # ── Dominant regime today ──────────────────────────────────────
    regime_counts = {}
    for s in signals_today:
        try:
            sj = _json.loads(s.get('signal_json') or '{}')
            regime = sj.get('agent_regime', 'unknown')
            regime_counts[regime] = regime_counts.get(regime, 0) + 1
        except Exception:
            pass
    data['dominant_regime'] = max(regime_counts, key=regime_counts.get) if regime_counts else 'unknown'
    data['regime_counts'] = regime_counts

    # ── BTC correlation proxy ──────────────────────────────────────
    btc_rows = con.execute("""
        SELECT trend_score, change_24h_pct FROM signals
        WHERE date(logged_at) = ? AND symbol LIKE 'BTC%'
        LIMIT 1
    """, (date_str,)).fetchall()
    data['btc_corr_proxy'] = btc_rows[0][0] if btc_rows else None

    # ── Agent disagreement log ─────────────────────────────────────
    disagreements = []
    for s in signals_today:
        try:
            sj = _json.loads(s.get('signal_json') or '{}')
            score = sj.get('agent_shadow_disagreement') or sj.get('agent_disagreement', 0)
            if score and score > 0.4:
                disagreements.append({
                    'symbol': s['symbol'],
                    'exchange': s['exchange'],
                    'direction': s['direction'],
                    'conviction': s['conviction'],
                    'disagreement_score': score,
                    'narrative_bull': sj.get('agent_narrative_bull'),
                    'structural_bull': sj.get('agent_structural_bull'),
                })
        except Exception:
            pass
    data['disagreements'] = disagreements

    # ── Top movers / losers from today's signals ───────────────────
    movers = sorted(signals_today, key=lambda x: x.get('change_24h_pct') or 0, reverse=True)
    data['top_gainers'] = [{
        'symbol': s['symbol'], 'exchange': s['exchange'],
        'change_24h_pct': s.get('change_24h_pct'), 'volume_24h': s.get('volume_24h'),
        'funding_rate': s.get('funding_rate'),
        'session': _report_classify_session(s.get('logged_at', '')),
    } for s in movers[:3]]
    data['top_losers'] = [{
        'symbol': s['symbol'], 'exchange': s['exchange'],
        'change_24h_pct': s.get('change_24h_pct'), 'volume_24h': s.get('volume_24h'),
        'funding_rate': s.get('funding_rate'),
        'session': _report_classify_session(s.get('logged_at', '')),
    } for s in movers[-3:] if (s.get('change_24h_pct') or 0) < 0]

    # ── Explosive move (biggest abs mover today) ───────────────────
    if movers:
        biggest = max(signals_today, key=lambda x: abs(x.get('change_24h_pct') or 0))
        try:
            sj = _json.loads(biggest.get('signal_json') or '{}')
        except Exception:
            sj = {}
        data['explosive_move'] = {
            'symbol': biggest['symbol'],
            'exchange': biggest['exchange'],
            'direction': biggest['direction'],
            'change_24h_pct': biggest.get('change_24h_pct'),
            'volume_24h': biggest.get('volume_24h'),
            'funding_rate': biggest.get('funding_rate'),
            'atr_pct': biggest.get('atr_pct'),
            'conviction': biggest.get('conviction'),
            'agent_regime': sj.get('agent_regime', 'unknown'),
        }
    else:
        data['explosive_move'] = None

    # ── Strategy performance by regime (last 7 days) ───────────────
    perf_rows = con.execute("""
        SELECT strategy_key, result, signal_json
        FROM signals
        WHERE date(logged_at) >= date(?, '-7 days')
          AND result IN ('WIN','LOSS','PARTIAL')
          AND signal_json IS NOT NULL
    """, (date_str,)).fetchall()
    strat_regime_perf = {}
    for (sk, result, sj_raw) in perf_rows:
        try:
            sj = _json.loads(sj_raw or '{}')
            regime = sj.get('agent_regime', 'unknown')
        except Exception:
            regime = 'unknown'
        key = (sk, regime)
        if key not in strat_regime_perf:
            strat_regime_perf[key] = {'win': 0, 'loss': 0, 'partial': 0}
        strat_regime_perf[key][result.lower()] += 1
    data['strategy_regime_perf'] = [
        {'strategy': k[0], 'regime': k[1], **v}
        for k, v in strat_regime_perf.items()
    ]

    # ── What the desk got wrong ────────────────────────────────────
    # Blocked signals that moved (compare yesterday's blocks vs today's prices)
    data['desk_wrong'] = []  # populated by narrative generator with move context

    # ── Funding heatmap from ticker ────────────────────────────────
    ticker = _report_fetch_ticker_snapshot()
    heatmap = []
    for t in ticker:
        fr = t.get('fundingRate') or t.get('funding_rate')
        sym = t.get('symbol', '')
        if fr is not None and sym:
            heatmap.append({'symbol': sym, 'exchange': 'MEXC', 'funding_rate': float(fr)})
    heatmap.sort(key=lambda x: x['funding_rate'])
    data['funding_heatmap'] = {
        'extreme_negative': [h for h in heatmap if h['funding_rate'] < -0.01][:8],
        'mild_negative':    [h for h in heatmap if -0.01 <= h['funding_rate'] < -0.001][:8],
        'neutral':          [h for h in heatmap if abs(h['funding_rate']) <= 0.001][:5],
        'mild_positive':    [h for h in heatmap if 0.001 < h['funding_rate'] <= 0.01][:8],
        'extreme_positive': [h for h in heatmap if h['funding_rate'] > 0.01][:8],
    }

    # ── What's coiling (extreme funding + flat price) ──────────────
    coiling = []
    for t in ticker:
        fr = t.get('fundingRate') or t.get('funding_rate') or 0
        change = t.get('riseFallRate') or 0
        sym = t.get('symbol', '')
        if abs(fr) > 0.008 and abs(change) < 0.03 and sym:
            coiling.append({
                'symbol': sym, 'exchange': 'MEXC',
                'funding_rate': float(fr),
                'change_24h_pct': float(change) * 100,
                'watch': 'SHORT SQUEEZE' if fr < 0 else 'LONG FLUSH',
            })
    coiling.sort(key=lambda x: abs(x['funding_rate']), reverse=True)
    data['whats_coiling'] = coiling[:6]

    # ── Liquidation clusters from CoinGlass ───────────────────────
    data['liquidation_clusters'] = []
    try:
        cg_key = os.getenv('COINGLASS_API_KEY')
        if cg_key:
            from lib.coinglass_client import CoinglassClient
            cg = CoinglassClient(cg_key)
            # Use existing client — best-effort
            data['liquidation_clusters'] = []  # extend when CoinGlass method available
    except Exception:
        pass

    con.close()
    return data
```

- [ ] **Step 4: Verify _build_daily_data runs without error**

```bash
python3 -c "
import sys; sys.path.insert(0, '.')
from app import _build_daily_data
from datetime import datetime
d = _build_daily_data(datetime.utcnow().strftime('%Y-%m-%d'))
print('signal_count:', d['signal_count'])
print('dominant_regime:', d['dominant_regime'])
print('heatmap extreme_negative:', len(d['funding_heatmap']['extreme_negative']))
print('whats_coiling:', len(d['whats_coiling']))
"
```

Expected: prints counts without exceptions. Signal count may be 0 if running locally.

- [ ] **Step 5: Commit**

```bash
git add app.py
git commit -m "feat: add _build_daily_data() report data builder helpers"
```

---

### Task 5: Add report narrative generator and _build_weekly_data()

**Files:**
- Modify: `app.py` — add after `_build_daily_data()`

- [ ] **Step 1: Add _generate_report_narrative() helper**

```python
def _generate_report_narrative(data: dict, report_type: str) -> dict:
    """
    Generate AI narrative quotes for a report. Makes 2 batched call_ai() calls.
    Returns dict of analyst_key -> quote string.
    Falls back to empty strings if AI unavailable.
    """
    narrative = {}

    # ── Call 1: Leadership notes (Thomas opening, Nadia forecast, Harper closing) ──
    summary = {
        'signal_count': data.get('signal_count', 0),
        'exchange_breakdown': data.get('exchange_breakdown', {}),
        'dominant_regime': data.get('dominant_regime', 'unknown'),
        'blocked_count': data.get('blocked_count', 0),
        'disagreement_count': len(data.get('disagreements', [])),
        'top_gainers': data.get('top_gainers', [])[:2],
        'top_losers': data.get('top_losers', [])[:2],
        'whats_coiling_count': len(data.get('whats_coiling', [])),
        'btc_corr_proxy': data.get('btc_corr_proxy'),
        'report_type': report_type,
    }
    if report_type == 'weekly':
        summary['weekly_win_rate'] = data.get('weekly_win_rate')
        summary['weekly_signal_count'] = data.get('weekly_signal_count')

    leadership_prompt = (
        "You are generating character-voice quotes for the Cipher Research Group "
        f"{'daily brief' if report_type == 'daily' else 'weekly report'}. "
        "Each character has a distinct personality. Return ONLY valid JSON, no markdown.\n\n"
        f"Market data:\n{json.dumps(summary, default=str)}\n\n"
        "Return exactly this JSON:\n"
        '{\n'
        '  "thomas_opening": "Thomas Reeves (CIO) note — quiet, decisive, references specific data, 50-60 words",\n'
        '  "nadia_forecast": "Nadia Okonkwo (Regime Specialist) 12h forecast — calm authority, data-driven, forward-looking, 50-60 words",\n'
        '  "harper_closing": "Harper Cross (CRO) closing — ruthless gatekeeper, sharp, never softens bad news, 35-40 words"\n'
        '}\n\n'
        "Character voices:\n"
        "- Thomas Reeves: Quiet, decisive. References specific pairs/numbers. Never wastes words.\n"
        "- Nadia Okonkwo: Calm authority. Uses regime terminology. Always gives an actionable forward call.\n"
        "- Harper Cross: Ruthless. Blunt. Never comforts. 'This trade doesn't pass. Full stop.'"
    )
    try:
        raw = call_ai(system="You generate analyst voice quotes. Return only JSON.", user=leadership_prompt, max_tokens=500)
        if raw:
            clean = raw.strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip()
            parsed = json.loads(clean)
            narrative.update({k: v for k, v in parsed.items() if isinstance(v, str)})
    except Exception as e:
        print(f'[report_narrative/leadership] {e}', file=sys.stderr)

    # ── Call 2: Explosive move autopsy (Kenny, Niobe, Ghost) ──────
    move = data.get('explosive_move')
    if move:
        move_prompt = (
            "You are generating analyst quotes for Cipher Research Group explosive move autopsy.\n\n"
            f"Move data:\n{json.dumps(move, default=str)}\n\n"
            "Return exactly this JSON:\n"
            '{\n'
            '  "kenny": "Kenny Hassan (Funding) on this move — experienced old hand, 35-45 words",\n'
            '  "niobe": "Niobe Reyes (Microstructure) on this move — skeptical, precise, 35-45 words",\n'
            '  "ghost": "Ghost Kimura (Cross-Venue) on this move — cool, detached, 35-45 words"\n'
            '}\n\n'
            "Character voices:\n"
            "- Kenny Hassan: Old hand. Knows positioning. 'Funding was telling us this for hours.'\n"
            "- Niobe Reyes: Skeptical. Watches the book. 'Order book said one thing. I flagged it.'\n"
            "- Ghost Kimura: Cool. Multi-venue. 'MEXC led. Arb closed the gap in 90 seconds.'"
        )
        try:
            raw = call_ai(system="You generate analyst voice quotes. Return only JSON.", user=move_prompt, max_tokens=400)
            if raw:
                clean = raw.strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip()
                parsed = json.loads(clean)
                narrative.update({k: v for k, v in parsed.items() if isinstance(v, str)})
        except Exception as e:
            print(f'[report_narrative/autopsy] {e}', file=sys.stderr)

    # ── Call 3 (weekly only): Agent spotlight + Thomas week-ahead ──
    if report_type == 'weekly':
        spotlight_key = data.get('spotlight_analyst_key', 'funding')
        spotlight_persona = {}
        try:
            from lib.agents import AGENT_ROSTER
            spotlight_persona = AGENT_ROSTER.get(spotlight_key, {})
        except Exception:
            pass
        weekly_prompt = (
            "You are generating weekly report quotes for Cipher Research Group.\n\n"
            f"Week data:\n{json.dumps({'win_rate': data.get('weekly_win_rate'), 'signal_count': data.get('weekly_signal_count'), 'dominant_regime': data.get('dominant_regime')}, default=str)}\n\n"
            f"Agent spotlight: {spotlight_persona.get('name', 'Kenny Hassan')} — {spotlight_persona.get('title', '')}. Voice: {spotlight_persona.get('voice', '')}\n\n"
            "Return exactly this JSON:\n"
            '{\n'
            '  "spotlight_quote": "Extended 100-120 word quote from the spotlight analyst about their methodology and what they found interesting this week",\n'
            '  "thomas_week_ahead": "Thomas Reeves week-ahead strategic outlook — quiet, decisive, 70-80 words"\n'
            '}'
        )
        try:
            raw = call_ai(system="You generate analyst voice quotes. Return only JSON.", user=weekly_prompt, max_tokens=600)
            if raw:
                clean = raw.strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip()
                parsed = json.loads(clean)
                narrative.update({k: v for k, v in parsed.items() if isinstance(v, str)})
        except Exception as e:
            print(f'[report_narrative/weekly] {e}', file=sys.stderr)

    return narrative
```

- [ ] **Step 2: Add _build_weekly_data() helper**

```python
def _build_weekly_data(week_str: str) -> dict:
    """
    Build weekly report data. week_str: 'YYYY-WNN' (e.g. '2026-W21').
    Extends daily data with 7-day aggregates and weekly-specific sections.
    """
    import json as _json
    from datetime import datetime, timedelta

    # Parse week string to date range
    year, wnum = week_str.split('-W')
    year, wnum = int(year), int(wnum)
    week_start = datetime.strptime(f'{year}-W{wnum:02d}-1', '%G-W%V-%u')
    week_end = week_start + timedelta(days=6)
    start_str = week_start.strftime('%Y-%m-%d')
    end_str = week_end.strftime('%Y-%m-%d')

    con = get_db()
    data = {'week': week_str, 'start_date': start_str, 'end_date': end_str}

    # Weekly signal counts and outcomes
    rows = con.execute("""
        SELECT result, strategy_key, exchange, signal_json
        FROM signals
        WHERE date(logged_at) BETWEEN ? AND ?
    """, (start_str, end_str)).fetchall()

    total = len(rows)
    wins = sum(1 for r in rows if r[0] == 'WIN')
    losses = sum(1 for r in rows if r[0] == 'LOSS')
    partials = sum(1 for r in rows if r[0] == 'PARTIAL')
    data['weekly_signal_count'] = total
    data['weekly_win_rate'] = round(wins / max(total, 1) * 100, 1)
    data['weekly_outcomes'] = {'win': wins, 'loss': losses, 'partial': partials}

    # Build daily data for the most recent day of the week as base
    today = datetime.utcnow().strftime('%Y-%m-%d')
    if start_str <= today <= end_str:
        daily = _build_daily_data(today)
    else:
        daily = _build_daily_data(end_str)
    data.update(daily)

    # Weekly move patterns — explosive moves (>8% in signals this week)
    explosive = []
    for (result, sk, ex, sj_raw) in rows:
        try:
            sj = _json.loads(sj_raw or '{}')
            chg = abs(sj.get('change_24h_pct') or 0)
            if chg > 8:
                explosive.append({'result': result, 'strategy': sk, 'exchange': ex, 'change': chg})
        except Exception:
            pass
    data['weekly_explosive_count'] = len(explosive)
    data['weekly_explosive_moves'] = explosive[:10]

    # Paper bot this week
    try:
        paper_rows = con.execute("""
            SELECT result, pnl_pct FROM signals
            WHERE date(logged_at) BETWEEN ? AND ?
              AND strategy_key LIKE 'paper%'
        """, (start_str, end_str)).fetchall()
        data['paper_week'] = {
            'count': len(paper_rows),
            'wins': sum(1 for r in paper_rows if r[0] == 'WIN'),
            'losses': sum(1 for r in paper_rows if r[0] == 'LOSS'),
            'avg_pnl': round(sum((r[1] or 0) for r in paper_rows) / max(len(paper_rows), 1), 2),
        }
    except Exception:
        data['paper_week'] = {'count': 0, 'wins': 0, 'losses': 0, 'avg_pnl': 0}

    # Agent spotlight rotation
    try:
        spotlight_path = 'data/reports/spotlight_state.json'
        spotlight_order = ['funding','microstructure','cross_venue','regime',
                           'technical','sentiment','news','tokenomics',
                           'narrative_debate','structural_debate','risk_manager','trader']
        if os.path.exists(spotlight_path):
            with open(spotlight_path) as f:
                state = json.load(f)
            idx = state.get('index', 0)
        else:
            idx = 0
        data['spotlight_analyst_key'] = spotlight_order[idx % len(spotlight_order)]
        # Advance index for next week
        new_state = {'index': (idx + 1) % len(spotlight_order)}
        with open(spotlight_path, 'w') as f:
            json.dump(new_state, f)
    except Exception:
        data['spotlight_analyst_key'] = 'funding'

    # mt-learner research briefs
    try:
        briefs_path = '/opt/mt-learner/research/briefs.json'
        if os.path.exists(briefs_path):
            with open(briefs_path) as f:
                briefs_data = json.load(f)
            data['research_briefs'] = briefs_data.get('briefs', [])[:3]
        else:
            data['research_briefs'] = []
    except Exception:
        data['research_briefs'] = []

    con.close()
    return data
```

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "feat: add _generate_report_narrative() and _build_weekly_data() helpers"
```

---

### Task 6: Add report routes to app.py

**Files:**
- Modify: `app.py` — add three new routes after `/api/intelligence/roster`

- [ ] **Step 1: Add _load_or_build_report() helper**

```python
def _load_or_build_report(cache_path: str, build_fn, narrative_type: str) -> dict:
    """Load cached report or build fresh. Caches result to cache_path."""
    try:
        if os.path.exists(cache_path):
            with open(cache_path) as f:
                cached = json.load(f)
            return cached
    except Exception:
        pass

    data = build_fn()
    narrative = _generate_report_narrative(data, narrative_type)
    result = {
        'data': data,
        'narrative': narrative,
        'generated_at': datetime.utcnow().isoformat(),
        'ai_available': bool(narrative.get('thomas_opening')),
    }
    try:
        with open(cache_path, 'w') as f:
            json.dump(result, f, default=str)
    except Exception as e:
        print(f'[report_cache] write error: {e}', file=sys.stderr)
    return result
```

- [ ] **Step 2: Add /api/intelligence/reports/daily route**

```python
@app.route('/api/intelligence/reports/daily')
def api_intelligence_reports_daily():
    """Return daily brief for a given date. Generates and caches on first request."""
    try:
        date_str = request.args.get('date', datetime.utcnow().strftime('%Y-%m-%d'))
        cache_path = f'data/reports/daily_{date_str}.json'
        result = _load_or_build_report(
            cache_path,
            lambda: _build_daily_data(date_str),
            'daily'
        )
        return jsonify({'success': True, 'date': date_str, **result})
    except Exception as e:
        print(f'[api/intelligence/reports/daily] error: {e}', file=sys.stderr)
        return jsonify({'success': False, 'error': str(e)}), 500
```

- [ ] **Step 3: Add /api/intelligence/reports/weekly route**

```python
@app.route('/api/intelligence/reports/weekly')
def api_intelligence_reports_weekly():
    """Return weekly report for a given ISO week. Generates and caches on first request."""
    try:
        from datetime import datetime as _dt
        default_week = _dt.utcnow().strftime('%G-W%V')
        week_str = request.args.get('week', default_week)
        cache_path = f'data/reports/weekly_{week_str.replace("-", "_")}.json'
        result = _load_or_build_report(
            cache_path,
            lambda: _build_weekly_data(week_str),
            'weekly'
        )
        return jsonify({'success': True, 'week': week_str, **result})
    except Exception as e:
        print(f'[api/intelligence/reports/weekly] error: {e}', file=sys.stderr)
        return jsonify({'success': False, 'error': str(e)}), 500
```

- [ ] **Step 4: Add /api/intelligence/reports/regenerate route**

```python
@app.route('/api/intelligence/reports/regenerate', methods=['POST'])
def api_intelligence_reports_regenerate():
    """Force-regenerate a cached report by deleting its cache file."""
    try:
        body = request.get_json() or {}
        report_type = body.get('type', 'daily')
        key = body.get('key', '')
        if report_type == 'daily':
            cache_path = f'data/reports/daily_{key}.json'
        else:
            cache_path = f'data/reports/weekly_{key.replace("-", "_")}.json'
        deleted = False
        if os.path.exists(cache_path):
            os.remove(cache_path)
            deleted = True
        return jsonify({'success': True, 'deleted': deleted, 'cache_path': cache_path})
    except Exception as e:
        print(f'[api/intelligence/reports/regenerate] error: {e}', file=sys.stderr)
        return jsonify({'success': False, 'error': str(e)}), 500
```

- [ ] **Step 5: Verify all routes with curl**

```bash
python3 app.py &
sleep 2

# Roster
curl -s "http://localhost:8080/api/intelligence/roster" | python3 -m json.tool | grep name

# Daily report (may take 10-15s on first run — AI calls)
curl -s "http://localhost:8080/api/intelligence/reports/daily" | python3 -m json.tool | grep -E '"generated_at"|"ai_available"|"signal_count"'

# Regenerate
curl -s -X POST "http://localhost:8080/api/intelligence/reports/regenerate" \
  -H "Content-Type: application/json" \
  -d "{\"type\":\"daily\",\"key\":\"$(date -u +%Y-%m-%d)\"}" | python3 -m json.tool

kill %1
```

Expected: roster returns firm name, daily returns `generated_at` and `signal_count`, regenerate returns `deleted: true`.

- [ ] **Step 6: Update .gitignore**

Add to `.gitignore`:

```
data/reports/daily_*.json
data/reports/weekly_*.json
```

- [ ] **Step 7: Commit**

```bash
git add app.py .gitignore
git commit -m "feat: add /api/intelligence/reports/daily, /weekly, /regenerate routes"
```

---

## Phase 3 — Frontend Intelligence Tab Restructure

### Task 7: Add sub-tab state + HTML structure + tab switching

**Files:**
- Modify: `templates/index.html` — `I` state object (~line 4099), intelligence-section HTML (~line 1560), `loadIntelligence()` (~line 6546)

- [ ] **Step 1: Extend I state object** (~line 4099)

Replace the existing `const I = {` block:

```javascript
const I = {
  loading:        false,
  status:         null,
  suggestions:    [],
  learnerRunning: false,
  briefs:         [],
  briefsLoading:  false,
  activeSubTab:   'overview',   // 'overview' | 'firm' | 'reports'
  activeReport:   'daily',      // 'daily' | 'weekly'
  reportDate:     null,         // YYYY-MM-DD, null = today
  reportWeek:     null,         // YYYY-WNN, null = current week
  reportCache:    {},           // key: "daily_YYYY-MM-DD" or "weekly_YYYY-WNN"
  roster:         null,         // loaded once from /api/intelligence/roster
  reportLoading:  false,
};
```

- [ ] **Step 2: Replace intelligence-section HTML** (~line 1560)

Replace the existing `<div id="intelligence-section" ...>` block with:

```html
<!-- ════ INTELLIGENCE SECTION ════ -->
<div id="intelligence-section" class="hidden" style="flex:1;overflow-y:auto;">

  <!-- Sub-tab bar -->
  <div style="display:flex;gap:0;border-bottom:1px solid var(--border);padding:0 16px;background:var(--bg2);position:sticky;top:0;z-index:10;">
    <button onclick="switchIntelTab('overview')" id="intel-tab-overview"
      style="padding:10px 16px;background:transparent;border:none;border-bottom:2px solid var(--blue);color:var(--text);font-size:12px;cursor:pointer;font-weight:600;">
      Overview
    </button>
    <button onclick="switchIntelTab('firm')" id="intel-tab-firm"
      style="padding:10px 16px;background:transparent;border:none;border-bottom:2px solid transparent;color:var(--text2);font-size:12px;cursor:pointer;">
      The Firm
    </button>
    <button onclick="switchIntelTab('reports')" id="intel-tab-reports"
      style="padding:10px 16px;background:transparent;border:none;border-bottom:2px solid transparent;color:var(--text2);font-size:12px;cursor:pointer;">
      Reports
    </button>
    <div style="flex:1;display:flex;align-items:center;justify-content:flex-end;padding-right:4px;">
      <span style="font-size:10px;color:var(--text3);letter-spacing:1px;">⬡ CIPHER RESEARCH GROUP</span>
    </div>
  </div>

  <!-- Sub-tab panels -->
  <div id="intel-panel-overview" style="padding:16px;">
    <div id="intelligence-content"><div style="color:var(--text2);padding:2rem;">Loading...</div></div>
  </div>
  <div id="intel-panel-firm" class="hidden" style="padding:16px;">
    <div id="intel-firm-content"><div style="color:var(--text2);padding:2rem;">Loading...</div></div>
  </div>
  <div id="intel-panel-reports" class="hidden" style="padding:16px;">
    <div id="intel-reports-content"><div style="color:var(--text2);padding:2rem;">Loading...</div></div>
  </div>

</div><!-- /intelligence-section -->
```

- [ ] **Step 3: Add switchIntelTab() function** (add near `loadIntelligence`)

```javascript
function switchIntelTab(tab) {
  I.activeSubTab = tab;
  ['overview','firm','reports'].forEach(t => {
    const btn = document.getElementById('intel-tab-' + t);
    const panel = document.getElementById('intel-panel-' + t);
    const active = t === tab;
    if (btn) {
      btn.style.borderBottomColor = active ? 'var(--blue)' : 'transparent';
      btn.style.color = active ? 'var(--text)' : 'var(--text2)';
      btn.style.fontWeight = active ? '600' : '400';
    }
    if (panel) toggle(panel.id, active);
  });
  if (tab === 'firm' && !I.roster) loadRoster();
  if (tab === 'reports') loadReportPanel();
}
```

- [ ] **Step 4: Add loadRoster() function**

```javascript
async function loadRoster() {
  const el = document.getElementById('intel-firm-content');
  if (!el) return;
  try {
    const res = await fetch('/api/intelligence/roster').then(r => r.json());
    if (res.success) {
      I.roster = res;
      renderFirm();
    }
  } catch(e) {
    if (el) el.innerHTML = '<div style="color:var(--red);padding:2rem;">Failed to load roster.</div>';
  }
}
```

- [ ] **Step 5: Verify tab switching renders without JS errors**

Start app, open browser to `http://localhost:8080`, click Intelligence tab, then click sub-tabs. Expected: no JS console errors, panels switch, "The Firm" triggers roster fetch.

- [ ] **Step 6: Commit**

```bash
git add templates/index.html
git commit -m "feat: Intelligence tab sub-tabs — Overview/The Firm/Reports with switchIntelTab()"
```

---

### Task 8: Render "The Firm" sub-tab — org chart

**Files:**
- Modify: `templates/index.html` — add `renderFirm()` function

- [ ] **Step 1: Add renderFirm() function** (add after `switchIntelTab`)

```javascript
function renderFirm() {
  const el = document.getElementById('intel-firm-content');
  if (!el || !I.roster) return;
  const { firm, agents } = I.roster;

  const divColor = { narrative: 'var(--green)', structural: 'var(--blue)', leadership: 'var(--amber)' };

  function agentNode(key, style='') {
    const a = agents[key];
    if (!a) return '';
    const col = divColor[a.division] || 'var(--text2)';
    return `<div style="background:var(--bg3);border:1px solid ${col}22;border-top:2px solid ${col};border-radius:4px;padding:6px 10px;text-align:center;min-width:90px;cursor:pointer;${style}"
      onclick="expandAgentBio('${key}')">
      <div style="color:${col};font-size:8px;letter-spacing:0.5px;">${a.title.split(' ').slice(-1)[0].toUpperCase()}</div>
      <div style="color:var(--text);font-size:11px;font-weight:600;margin-top:1px;">${a.name.split(' ')[0]}</div>
      <div style="color:var(--text2);font-size:9px;">${a.name.split(' ')[1] || ''}</div>
    </div>`;
  }

  el.innerHTML = `
    <!-- Firm header -->
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:20px;padding-bottom:16px;border-bottom:1px solid var(--border);">
      <span style="font-size:28px;color:var(--blue);">⬡</span>
      <div>
        <div style="font-size:18px;font-weight:700;color:var(--text);">${firm.name}</div>
        <div style="font-size:12px;color:var(--text2);margin-top:2px;font-style:italic;">${firm.tagline}</div>
        <div style="display:flex;gap:6px;margin-top:6px;">
          ${firm.exchange_coverage.map(ex => `<span style="background:var(--bg2);border:1px solid var(--border);padding:1px 6px;border-radius:3px;font-size:9px;color:var(--text2);">${ex}</span>`).join('')}
        </div>
      </div>
    </div>

    <!-- Org chart -->
    <div style="font-size:10px;color:var(--text2);letter-spacing:1px;margin-bottom:12px;">ORGANIZATIONAL STRUCTURE</div>
    <div style="overflow-x:auto;padding-bottom:8px;">
      <div style="min-width:600px;">

        <!-- CIO -->
        <div style="display:flex;justify-content:center;margin-bottom:6px;">
          ${agentNode('trader', 'min-width:160px;')}
        </div>

        <!-- Connector lines -->
        <div style="display:flex;justify-content:space-around;align-items:flex-start;margin-bottom:0;position:relative;">
          <div style="position:absolute;top:0;left:25%;right:25%;height:1px;background:var(--border);"></div>
          <div style="position:absolute;top:0;left:50%;width:1px;height:12px;background:var(--border);transform:translateX(-50%);"></div>
          <div style="width:1px;height:12px;background:var(--border);margin-top:0;flex:0 0 auto;margin-left:25%;"></div>
          <div style="width:1px;height:12px;background:var(--border);margin-top:0;flex:0 0 auto;"></div>
          <div style="width:1px;height:12px;background:var(--border);margin-top:0;flex:0 0 auto;margin-right:25%;"></div>
        </div>

        <!-- Division heads + Risk -->
        <div style="display:flex;gap:8px;margin-bottom:8px;margin-top:6px;">

          <!-- Narrative division -->
          <div style="flex:1;background:var(--bg2);border:1px solid var(--green)22;border-radius:6px;padding:10px;">
            <div style="color:var(--green);font-size:9px;letter-spacing:1px;text-align:center;margin-bottom:8px;">NARRATIVE DIVISION</div>
            <div style="display:flex;justify-content:center;margin-bottom:8px;">
              ${agentNode('narrative_debate', 'width:100%;')}
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;">
              ${agentNode('tokenomics')}${agentNode('sentiment')}
              ${agentNode('news')}${agentNode('technical')}
            </div>
          </div>

          <!-- Risk CRO (center) -->
          <div style="width:110px;flex-shrink:0;background:var(--bg2);border:1px solid var(--red)33;border-radius:6px;padding:10px;display:flex;flex-direction:column;align-items:center;justify-content:center;">
            ${agentNode('risk_manager', 'width:100%;')}
            <div style="margin-top:8px;font-size:8px;color:var(--red);text-align:center;">Independent veto</div>
          </div>

          <!-- Structural division -->
          <div style="flex:1;background:var(--bg2);border:1px solid var(--blue)22;border-radius:6px;padding:10px;">
            <div style="color:var(--blue);font-size:9px;letter-spacing:1px;text-align:center;margin-bottom:8px;">STRUCTURAL DIVISION</div>
            <div style="display:flex;justify-content:center;margin-bottom:8px;">
              ${agentNode('structural_debate', 'width:100%;')}
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;">
              ${agentNode('microstructure')}${agentNode('funding')}
              ${agentNode('cross_venue')}${agentNode('regime')}
            </div>
          </div>

        </div>
        <div style="text-align:center;color:var(--text3);font-size:9px;margin-top:4px;">Click any analyst to see their bio</div>
      </div>
    </div>

    <!-- Bio cards (populated by expandAgentBio) -->
    <div id="intel-bio-expanded" style="margin-top:16px;"></div>

    <!-- Full roster list -->
    <div style="margin-top:20px;">
      <div style="font-size:10px;color:var(--text2);letter-spacing:1px;margin-bottom:10px;">FULL ROSTER</div>
      <div id="intel-bio-cards" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:8px;">
        ${Object.entries(agents).map(([key, a]) => renderAgentCard(key, a)).join('')}
      </div>
    </div>
  `;
}

function renderAgentCard(key, a) {
  const divColor = { narrative: 'var(--green)', structural: 'var(--blue)', leadership: 'var(--amber)' };
  const col = divColor[a.division] || 'var(--text2)';
  return `<div style="background:var(--bg2);border:1px solid var(--border);border-left:3px solid ${col};border-radius:4px;padding:10px;cursor:pointer;"
    onclick="expandAgentBio('${key}')">
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
      <div>
        <div style="color:var(--text);font-size:13px;font-weight:600;">${a.name}</div>
        <div style="color:${col};font-size:9px;letter-spacing:0.5px;">${a.title}</div>
      </div>
    </div>
    <div style="color:var(--text2);font-size:11px;margin-bottom:6px;">${a.specialty}</div>
    <div style="color:var(--text3);font-size:10px;font-style:italic;">${a.voice}</div>
    <div style="display:flex;gap:4px;margin-top:6px;">
      ${a.exchanges.map(ex => `<span style="background:var(--bg3);padding:1px 4px;border-radius:2px;font-size:8px;color:var(--text3);">${ex}</span>`).join('')}
    </div>
  </div>`;
}

function expandAgentBio(key) {
  if (!I.roster) return;
  const a = I.roster.agents[key];
  if (!a) return;
  const divColor = { narrative: 'var(--green)', structural: 'var(--blue)', leadership: 'var(--amber)' };
  const col = divColor[a.division] || 'var(--text2)';
  const el = document.getElementById('intel-bio-expanded');
  if (!el) return;
  el.innerHTML = `
    <div style="background:var(--bg2);border:1px solid ${col}44;border-radius:6px;padding:14px;border-left:3px solid ${col};">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;">
        <div>
          <div style="color:var(--text);font-size:16px;font-weight:700;">${a.name}</div>
          <div style="color:${col};font-size:10px;letter-spacing:1px;">${a.title} · Cipher Research Group</div>
        </div>
        <button onclick="document.getElementById('intel-bio-expanded').innerHTML=''"
          style="background:transparent;border:none;color:var(--text2);cursor:pointer;font-size:16px;">✕</button>
      </div>
      <div style="color:var(--text2);font-size:12px;margin-bottom:10px;">${a.specialty}</div>
      <div style="background:var(--bg3);border-left:2px solid ${col};padding:8px 10px;border-radius:0 4px 4px 0;">
        <div style="color:${col};font-size:9px;letter-spacing:1px;margin-bottom:3px;">${a.name.split(' ')[0].toUpperCase()} · VOICE</div>
        <div style="color:var(--text);font-size:12px;font-style:italic;">${a.voice}</div>
      </div>
      <div style="display:flex;gap:4px;margin-top:8px;">
        ${a.exchanges.map(ex => `<span style="background:var(--bg3);border:1px solid var(--border);padding:2px 6px;border-radius:3px;font-size:9px;color:var(--text2);">${ex}</span>`).join('')}
      </div>
    </div>
  `;
  el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}
```

- [ ] **Step 2: Test in browser**

Open Intelligence tab → The Firm sub-tab. Expected: org chart renders with correct names, clicking an analyst card shows bio expanded panel, all 12 agents appear in roster list.

- [ ] **Step 3: Commit**

```bash
git add templates/index.html
git commit -m "feat: The Firm sub-tab — org chart, agent bio cards, expand on click"
```

---

### Task 9: Add Reports sub-tab shell and loadReportPanel()

**Files:**
- Modify: `templates/index.html` — add report panel loader and selector UI

- [ ] **Step 1: Add loadReportPanel() function**

```javascript
async function loadReportPanel() {
  const el = document.getElementById('intel-reports-content');
  if (!el) return;
  if (I.reportLoading) return;
  I.reportLoading = true;

  // Render shell with selector first
  const today = new Date().toISOString().split('T')[0];
  const currentDate = I.reportDate || today;
  const weekNum = getISOWeek(new Date());
  const currentWeek = I.reportWeek || weekNum;

  el.innerHTML = `
    <!-- Report selector -->
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px;flex-wrap:wrap;">
      <div style="display:flex;gap:0;background:var(--bg2);border:1px solid var(--border);border-radius:4px;overflow:hidden;">
        <button id="report-btn-daily" onclick="switchReportType('daily')"
          style="padding:6px 14px;background:var(--blue);border:none;color:#000;font-size:12px;cursor:pointer;font-weight:600;">
          Daily Brief
        </button>
        <button id="report-btn-weekly" onclick="switchReportType('weekly')"
          style="padding:6px 14px;background:transparent;border:none;color:var(--text2);font-size:12px;cursor:pointer;">
          Weekly Report
        </button>
      </div>
      <div id="report-date-nav" style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text2);">
        <span id="report-period-label">${currentDate}</span>
        <button onclick="navigateReport(-1)" style="background:transparent;border:1px solid var(--border);color:var(--text2);padding:2px 8px;border-radius:3px;cursor:pointer;">←</button>
        <button onclick="navigateReport(1)" style="background:transparent;border:1px solid var(--border);color:var(--text2);padding:2px 8px;border-radius:3px;cursor:pointer;">→</button>
      </div>
      <button onclick="regenerateReport()" style="margin-left:auto;background:transparent;border:1px solid var(--border);color:var(--text3);font-size:11px;padding:4px 10px;border-radius:3px;cursor:pointer;">
        ↺ Regenerate
      </button>
    </div>
    <div id="report-body"><div style="color:var(--text2);padding:2rem;">Loading report...</div></div>
  `;

  await fetchAndRenderReport();
  I.reportLoading = false;
}

function getISOWeek(date) {
  const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
  d.setUTCDate(d.getUTCDate() + 4 - (d.getUTCDay() || 7));
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  const weekNo = Math.ceil((((d - yearStart) / 86400000) + 1) / 7);
  return `${d.getUTCFullYear()}-W${String(weekNo).padStart(2,'0')}`;
}

function switchReportType(type) {
  I.activeReport = type;
  ['daily','weekly'].forEach(t => {
    const btn = document.getElementById('report-btn-' + t);
    if (btn) {
      btn.style.background = t === type ? 'var(--blue)' : 'transparent';
      btn.style.color = t === type ? '#000' : 'var(--text2)';
      btn.style.fontWeight = t === type ? '600' : '400';
    }
  });
  fetchAndRenderReport();
}

function navigateReport(dir) {
  if (I.activeReport === 'daily') {
    const d = new Date(I.reportDate || new Date().toISOString().split('T')[0]);
    d.setDate(d.getDate() + dir);
    I.reportDate = d.toISOString().split('T')[0];
    const lbl = document.getElementById('report-period-label');
    if (lbl) lbl.textContent = I.reportDate;
  } else {
    // Week navigation: parse YYYY-WNN, add/subtract 7 days
    const current = I.reportWeek || getISOWeek(new Date());
    const [yr, wn] = current.split('-W').map(Number);
    const d = new Date(); d.setFullYear(yr);
    // approximate: just shift by 7 days
    const base = new Date(yr, 0, 1 + (wn - 1) * 7);
    base.setDate(base.getDate() + dir * 7);
    I.reportWeek = getISOWeek(base);
    const lbl = document.getElementById('report-period-label');
    if (lbl) lbl.textContent = I.reportWeek;
  }
  fetchAndRenderReport();
}

async function regenerateReport() {
  const key = I.activeReport === 'daily'
    ? (I.reportDate || new Date().toISOString().split('T')[0])
    : (I.reportWeek || getISOWeek(new Date()));
  await fetch('/api/intelligence/reports/regenerate', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({type: I.activeReport, key})
  });
  // Clear cache and reload
  const cacheKey = I.activeReport + '_' + key;
  delete I.reportCache[cacheKey];
  fetchAndRenderReport();
}

async function fetchAndRenderReport() {
  const body = document.getElementById('report-body');
  if (!body) return;
  body.innerHTML = '<div style="color:var(--text2);padding:2rem;">Loading...</div>';

  try {
    let url, cacheKey;
    if (I.activeReport === 'daily') {
      const date = I.reportDate || new Date().toISOString().split('T')[0];
      cacheKey = 'daily_' + date;
      url = `/api/intelligence/reports/daily?date=${date}`;
    } else {
      const week = I.reportWeek || getISOWeek(new Date());
      cacheKey = 'weekly_' + week;
      url = `/api/intelligence/reports/weekly?week=${encodeURIComponent(week)}`;
    }

    let report = I.reportCache[cacheKey];
    if (!report) {
      const res = await fetch(url).then(r => r.json());
      if (!res.success) throw new Error(res.error || 'Report failed');
      I.reportCache[cacheKey] = res;
      report = res;
    }

    if (I.activeReport === 'daily') {
      renderDailyBrief(report);
    } else {
      renderWeeklyReport(report);
    }
  } catch(e) {
    if (body) body.innerHTML = `<div style="color:var(--red);padding:2rem;">Failed to load report: ${e.message}</div>`;
  }
}
```

- [ ] **Step 2: Verify Reports sub-tab loads without errors**

Click Reports sub-tab in browser. Expected: selector appears, loading spinner shows, report fetches (may be slow on first run if AI calls needed), no JS errors.

- [ ] **Step 3: Commit**

```bash
git add templates/index.html
git commit -m "feat: Reports sub-tab shell — selector, date nav, report fetch + cache"
```

---

### Task 10: Render daily brief — all sections

**Files:**
- Modify: `templates/index.html` — add `renderDailyBrief()` function

- [ ] **Step 1: Add renderDailyBrief() function**

```javascript
function renderDailyBrief(report) {
  const body = document.getElementById('report-body');
  if (!body) return;
  const d = report.data || {};
  const n = report.narrative || {};

  function fPct(v) { return v != null ? (v >= 0 ? '+' : '') + Number(v).toFixed(1) + '%' : '—'; }
  function fFund(v) { return v != null ? (Number(v) * 100).toFixed(4) + '%' : '—'; }
  function fVol(v) { return v != null ? '$' + (Number(v)/1e6).toFixed(1) + 'M' : '—'; }

  const aiNote = report.ai_available ? '' :
    '<div style="background:var(--bg2);border:1px solid var(--amber)33;border-radius:4px;padding:6px 10px;margin-bottom:12px;font-size:11px;color:var(--amber);">AI narrative unavailable — credits depleted. Data sections shown.</div>';

  // Section helper
  function sectionLabel(label) {
    return `<div style="font-size:9px;color:var(--text3);letter-spacing:1px;margin:16px 0 6px;">${label}</div>`;
  }

  // Analyst quote block
  function analystQuote(name, title, color, quote) {
    if (!quote) return '';
    return `<div style="border-left:2px solid ${color};padding:6px 10px;background:var(--bg3);border-radius:0 4px 4px 0;margin-bottom:4px;">
      <div style="color:${color};font-size:8px;letter-spacing:1px;margin-bottom:3px;">${name.toUpperCase()} · ${title.toUpperCase()}</div>
      <div style="color:var(--text2);font-size:11px;font-style:italic;">"${quote}"</div>
    </div>`;
  }

  // 1. Thomas opening
  const thomasNote = n.thomas_opening ? analystQuote('Thomas Reeves','CIO','var(--blue)', n.thomas_opening) : '';

  // 2. Market pulse
  const exBreakdown = Object.entries(d.exchange_breakdown || {}).map(([ex,cnt]) =>
    `<span style="color:var(--text3);font-size:9px;">${cnt} ${ex}</span>`).join(' · ');
  const pulse = `<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:6px;">
    ${[
      ['SIGNALS', d.signal_count ?? 0, exBreakdown],
      ['REGIME', d.dominant_regime || '—', ''],
      ['BTC CORR', d.btc_corr_proxy != null ? Number(d.btc_corr_proxy/100).toFixed(2) : '—', 'proxy'],
      ['BLOCKED', d.blocked_count ?? 0, 'Harper · risk gates'],
      ['DISAGREEMENTS', (d.disagreements || []).length, 'high score signals'],
    ].map(([lbl, val, sub]) => `<div style="background:var(--bg2);border:1px solid var(--border);border-radius:4px;padding:8px;text-align:center;">
      <div style="color:var(--text3);font-size:8px;">${lbl}</div>
      <div style="color:var(--text);font-size:16px;font-weight:700;margin:2px 0;">${val}</div>
      <div style="color:var(--text3);font-size:9px;">${sub}</div>
    </div>`).join('')}
  </div>`;

  // 3. Funding heatmap
  const hm = d.funding_heatmap || {};
  const allHm = [
    ...(hm.extreme_negative||[]).map(x => ({...x, cls:'extreme-neg'})),
    ...(hm.mild_negative||[]).map(x => ({...x, cls:'mild-neg'})),
    ...(hm.neutral||[]).map(x => ({...x, cls:'neutral'})),
    ...(hm.mild_positive||[]).map(x => ({...x, cls:'mild-pos'})),
    ...(hm.extreme_positive||[]).map(x => ({...x, cls:'extreme-pos'})),
  ];
  const hmColors = {
    'extreme-neg': ['#0d2a1a','#166534','var(--green)'],
    'mild-neg': ['#0e1a12','#1e3a2a','#7ecf88'],
    'neutral': ['var(--bg2)','var(--border)','var(--text3)'],
    'mild-pos': ['#1a0e0e','#3a1e1e','#cf8888'],
    'extreme-pos': ['#2a0d0d','#7f1d1d','var(--red)'],
  };
  const hmPills = allHm.map(x => {
    const [bg, border, color] = hmColors[x.cls];
    return `<div style="background:${bg};border:1px solid ${border};border-radius:3px;padding:3px 7px;text-align:center;">
      <div style="color:${color};font-size:9px;font-weight:600;">${x.symbol.replace('_USDT','')}</div>
      <div style="color:${color};font-size:8px;">${fFund(x.funding_rate)}</div>
    </div>`;
  }).join('');
  const heatmap = `<div style="background:var(--bg2);border:1px solid var(--border);border-radius:4px;padding:10px;">
    <div style="display:flex;gap:4px;flex-wrap:wrap;margin-bottom:8px;">${hmPills || '<span style="color:var(--text3);font-size:11px;">No ticker data</span>'}</div>
    <div style="display:flex;gap:12px;font-size:9px;">
      <span style="color:var(--green);">■ Negative = shorts crowded</span>
      <span style="color:var(--text3);">■ Neutral</span>
      <span style="color:var(--red);">■ Positive = longs crowded</span>
    </div>
  </div>`;

  // 4. Top movers/losers table
  function moverRow(m) {
    const chg = m.change_24h_pct;
    const col = chg > 0 ? 'var(--green)' : 'var(--red)';
    return `<tr style="border-top:1px solid var(--border);">
      <td style="padding:5px 6px;color:var(--text);font-weight:600;">${m.symbol}</td>
      <td style="padding:5px 6px;text-align:right;color:${col};">${fPct(chg)}</td>
      <td style="padding:5px 6px;text-align:right;color:var(--text2);">${fVol(m.volume_24h)}</td>
      <td style="padding:5px 6px;text-align:right;color:${(m.funding_rate||0)<0?'var(--green)':'var(--red)'};">${fFund(m.funding_rate)}</td>
      <td style="padding:5px 6px;text-align:right;color:var(--text3);font-size:10px;">${m.session||''}</td>
    </tr>`;
  }
  const moversTable = `<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
    <div>
      <div style="color:var(--green);font-size:9px;margin-bottom:4px;">▲ GAINERS</div>
      <table style="width:100%;border-collapse:collapse;font-size:11px;">
        <tr style="color:var(--text3);"><td style="padding:2px 6px;">PAIR</td><td style="padding:2px 6px;text-align:right;">MOVE</td><td style="padding:2px 6px;text-align:right;">VOL</td><td style="padding:2px 6px;text-align:right;">FUND</td><td style="padding:2px 6px;text-align:right;">SESS</td></tr>
        ${(d.top_gainers||[]).map(moverRow).join('') || '<tr><td colspan="5" style="padding:8px;color:var(--text3);">No data</td></tr>'}
      </table>
    </div>
    <div>
      <div style="color:var(--red);font-size:9px;margin-bottom:4px;">▼ LOSERS</div>
      <table style="width:100%;border-collapse:collapse;font-size:11px;">
        <tr style="color:var(--text3);"><td style="padding:2px 6px;">PAIR</td><td style="padding:2px 6px;text-align:right;">MOVE</td><td style="padding:2px 6px;text-align:right;">VOL</td><td style="padding:2px 6px;text-align:right;">FUND</td><td style="padding:2px 6px;text-align:right;">SESS</td></tr>
        ${(d.top_losers||[]).map(moverRow).join('') || '<tr><td colspan="5" style="padding:8px;color:var(--text3);">No data</td></tr>'}
      </table>
    </div>
  </div>`;

  // 5. Session breakdown
  const sess = d.sessions || {};
  const sessionColors = {ASIA:'var(--amber)',LONDON:'var(--blue)',NY:'#a78bfa'};
  const sessionBreakdown = `<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;">
    ${['ASIA','LONDON','NY'].map(s => {
      const info = sess[s] || {count:0, symbols:[]};
      return `<div style="background:var(--bg2);border:1px solid var(--border);border-top:2px solid ${sessionColors[s]||'var(--border)'};border-radius:4px;padding:8px;">
        <div style="color:${sessionColors[s]||'var(--text2)'};font-size:9px;font-weight:600;margin-bottom:4px;">${s}</div>
        <div style="color:var(--text);font-size:14px;font-weight:700;">${info.count} signal${info.count!==1?'s':''}</div>
        <div style="color:var(--text3);font-size:9px;margin-top:2px;">${info.symbols.slice(0,3).join(', ')}</div>
      </div>`;
    }).join('')}
  </div>`;

  // 6. Explosive move autopsy
  const move = d.explosive_move;
  let autopsy = '<div style="color:var(--text3);font-size:11px;">No explosive moves detected today.</div>';
  if (move) {
    autopsy = `<div style="background:var(--bg2);border:1px solid #2a1a4a;border-radius:6px;padding:10px;">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;">
        <span style="color:var(--text);font-weight:700;font-size:13px;">${move.symbol} · ${fPct(move.change_24h_pct)}</span>
        <span style="background:#2a1a4a;color:#a78bfa;padding:2px 8px;border-radius:3px;font-size:9px;">${move.agent_regime?.replace('_',' ').toUpperCase() || '—'}</span>
      </div>
      <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:4px;margin-bottom:8px;">
        ${[
          ['FUNDING',fFund(move.funding_rate)],
          ['VOLUME',fVol(move.volume_24h)],
          ['ATR%', move.atr_pct ? Number(move.atr_pct).toFixed(2)+'%' : '—'],
          ['CONVICTION', move.conviction || '—'],
        ].map(([lbl,val]) => `<div style="background:var(--bg3);border-radius:3px;padding:5px;text-align:center;">
          <div style="color:var(--text3);font-size:8px;">${lbl}</div>
          <div style="color:var(--text);font-size:11px;font-weight:600;">${val}</div>
        </div>`).join('')}
      </div>
      ${analystQuote('Kenny Hassan','Funding','var(--blue)', n.kenny)}
      ${analystQuote('Niobe Reyes','Microstructure','#a78bfa', n.niobe)}
      ${analystQuote('Ghost Kimura','Cross-Venue','var(--green)', n.ghost)}
    </div>`;
  }

  // 7. What's coiling
  const coilingRows = (d.whats_coiling || []).map(c => `
    <tr style="border-top:1px solid var(--border);">
      <td style="padding:5px 6px;color:var(--text);font-weight:600;">${c.symbol}</td>
      <td style="padding:5px 6px;text-align:right;color:${c.funding_rate<0?'var(--green)':'var(--red)'};">${fFund(c.funding_rate)}</td>
      <td style="padding:5px 6px;text-align:right;color:var(--text2);">${fPct(c.change_24h_pct)}</td>
      <td style="padding:5px 6px;text-align:right;">
        <span style="background:${c.watch==='SHORT SQUEEZE'?'#1a2a0a':'#2a0a0a'};color:${c.watch==='SHORT SQUEEZE'?'var(--green)':'var(--red)'};padding:1px 5px;border-radius:2px;font-size:8px;">${c.watch}</span>
      </td>
    </tr>`).join('');
  const coilingTable = `<div style="background:var(--bg2);border:1px solid var(--border);border-radius:4px;overflow:hidden;">
    <table style="width:100%;border-collapse:collapse;font-size:11px;">
      <tr style="color:var(--text3);background:var(--bg3);"><td style="padding:5px 6px;">PAIR</td><td style="padding:5px 6px;text-align:right;">FUNDING</td><td style="padding:5px 6px;text-align:right;">PRICE 24H</td><td style="padding:5px 6px;text-align:right;">WATCH</td></tr>
      ${coilingRows || '<tr><td colspan="4" style="padding:8px;color:var(--text3);">No coiling setups detected</td></tr>'}
    </table>
  </div>`;

  // 8. Agent disagreement log
  const disagRows = (d.disagreements || []).map(dis => `
    <div style="border-left:2px solid var(--amber);padding:6px 10px;background:var(--bg2);border-radius:0 4px 4px 0;margin-bottom:4px;">
      <div style="display:flex;justify-content:space-between;margin-bottom:3px;">
        <span style="color:var(--text);font-weight:600;font-size:12px;">${dis.symbol} ${dis.direction}</span>
        <span style="color:var(--text3);font-size:9px;">conviction: ${dis.conviction} · score: ${Number(dis.disagreement_score).toFixed(2)}</span>
      </div>
      <div style="display:flex;gap:8px;font-size:10px;">
        <span style="color:var(--green);">Narrative: ${dis.narrative_bull > 50 ? 'BULLISH' : 'BEARISH'}</span>
        <span style="color:var(--text3);">vs</span>
        <span style="color:var(--red);">Structural: ${dis.structural_bull > 50 ? 'BULLISH' : 'BEARISH'}</span>
      </div>
    </div>`).join('');
  const disagSection = disagRows || '<div style="color:var(--text3);font-size:11px;">No significant disagreements today.</div>';

  // 9. Strategy performance by regime
  const perfRows = (d.strategy_regime_perf || []).slice(0, 8).map(p => {
    const total = p.win + p.loss + (p.partial||0);
    const wr = total > 0 ? Math.round(p.win / total * 100) : 0;
    return `<tr style="border-top:1px solid var(--border);">
      <td style="padding:5px 6px;color:var(--text);">${p.strategy}</td>
      <td style="padding:5px 6px;color:var(--text2);">${p.regime}</td>
      <td style="padding:5px 6px;text-align:right;color:var(--green);">${p.win}W</td>
      <td style="padding:5px 6px;text-align:right;color:var(--red);">${p.loss}L</td>
      <td style="padding:5px 6px;text-align:right;color:${wr>=50?'var(--green)':'var(--red)'};">${wr}%</td>
    </tr>`;
  }).join('');

  // 10. Nadia forecast + Harper closing
  const nadiaNote = n.nadia_forecast ? analystQuote('Nadia Okonkwo','Regime Forecast','var(--blue)', n.nadia_forecast) : '';
  const harperNote = n.harper_closing ? analystQuote('Harper Cross','CRO · Closing Note','var(--red)', n.harper_closing) : '';

  // Assemble
  body.innerHTML = `
    <div style="max-width:1000px;">
      ${aiNote}
      ${thomasNote}
      ${sectionLabel('MARKET PULSE')}${pulse}
      ${sectionLabel('FUNDING HEATMAP · CROWDING ACROSS WATCHLIST')}${heatmap}
      ${sectionLabel('TOP MOVERS · LAST 24H · MEXC + HYPERLIQUID')}${moversTable}
      ${sectionLabel('SESSION BREAKDOWN')}${sessionBreakdown}
      ${sectionLabel('EXPLOSIVE MOVE AUTOPSY')}${autopsy}
      ${sectionLabel("WHAT'S COILING · RISING PRESSURE · NO MOVE YET")}${coilingTable}
      ${sectionLabel('AGENT DISAGREEMENT LOG')}${disagSection}
      ${sectionLabel('STRATEGY PERFORMANCE BY REGIME · LAST 7 DAYS')}
      <div style="background:var(--bg2);border:1px solid var(--border);border-radius:4px;overflow:hidden;">
        <table style="width:100%;border-collapse:collapse;font-size:11px;">
          <tr style="color:var(--text3);background:var(--bg3);">
            <td style="padding:5px 6px;">STRATEGY</td><td style="padding:5px 6px;">REGIME</td>
            <td style="padding:5px 6px;text-align:right;">W</td><td style="padding:5px 6px;text-align:right;">L</td>
            <td style="padding:5px 6px;text-align:right;">WIN%</td>
          </tr>
          ${perfRows || '<tr><td colspan="5" style="padding:8px;color:var(--text3);">Insufficient data</td></tr>'}
        </table>
      </div>
      ${nadiaNote ? sectionLabel('NADIA OKONKWO · REGIME FORECAST · NEXT 12H') + nadiaNote : ''}
      ${harperNote ? sectionLabel('HARPER CROSS · CRO · CLOSING NOTE') + harperNote : ''}
      <div style="margin-top:8px;color:var(--text3);font-size:9px;">
        Generated ${report.generated_at || '—'} UTC · ${report.ai_available ? 'AI narratives included' : 'Data only — AI unavailable'}
      </div>
    </div>
  `;
}
```

- [ ] **Step 2: Test daily brief in browser**

Open Reports sub-tab → Daily Brief. Expected: all sections render, analyst quotes appear if AI is available (or data-only mode if credits depleted), no JS errors.

- [ ] **Step 3: Commit**

```bash
git add templates/index.html
git commit -m "feat: renderDailyBrief() — all 10 sections including heatmap, autopsy, regime perf"
```

---

### Task 11: Add weekly report renderer

**Files:**
- Modify: `templates/index.html` — add `renderWeeklyReport()` function

- [ ] **Step 1: Add renderWeeklyReport() function**

```javascript
function renderWeeklyReport(report) {
  const body = document.getElementById('report-body');
  if (!body) return;
  const d = report.data || {};
  const n = report.narrative || {};

  function fPct(v) { return v != null ? (v >= 0 ? '+' : '') + Number(v).toFixed(1) + '%' : '—'; }

  function analystQuote(name, title, color, quote) {
    if (!quote) return '';
    return `<div style="border-left:2px solid ${color};padding:6px 10px;background:var(--bg3);border-radius:0 4px 4px 0;margin-bottom:4px;">
      <div style="color:${color};font-size:8px;letter-spacing:1px;margin-bottom:3px;">${name.toUpperCase()} · ${title.toUpperCase()}</div>
      <div style="color:var(--text2);font-size:11px;font-style:italic;">"${quote}"</div>
    </div>`;
  }

  function sectionLabel(label) {
    return `<div style="font-size:9px;color:var(--text3);letter-spacing:1px;margin:16px 0 6px;">${label}</div>`;
  }

  // Weekly summary stats
  const outcomes = d.weekly_outcomes || {};
  const total = d.weekly_signal_count || 0;
  const weekSummary = `<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-bottom:16px;">
    ${[
      ['SIGNALS', total, ''],
      ['WIN RATE', (d.weekly_win_rate || 0) + '%', `${outcomes.win||0}W / ${outcomes.loss||0}L / ${outcomes.partial||0}P`],
      ['DOMINANT REGIME', d.dominant_regime || '—', ''],
      ['PERIOD', `${d.start_date||''} → ${d.end_date||''}`, ''],
    ].map(([lbl,val,sub]) => `<div style="background:var(--bg2);border:1px solid var(--border);border-radius:4px;padding:8px;text-align:center;">
      <div style="color:var(--text3);font-size:8px;">${lbl}</div>
      <div style="color:var(--text);font-size:${lbl==='PERIOD'?'10':'14'}px;font-weight:700;margin:2px 0;">${val}</div>
      <div style="color:var(--text3);font-size:9px;">${sub}</div>
    </div>`).join('')}
  </div>`;

  // Weekly move patterns
  const expCount = d.weekly_explosive_count || 0;
  const movePatternsHtml = expCount === 0
    ? '<div style="color:var(--text3);font-size:11px;">No explosive moves (&gt;8%) recorded this week.</div>'
    : `<div style="background:var(--bg2);border:1px solid var(--border);border-radius:4px;padding:10px;">
        <div style="color:var(--text2);font-size:11px;margin-bottom:8px;">${expCount} explosive moves (&gt;8% in signals) this week across MEXC + Hyperliquid</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;">
          <div style="border-left:3px solid var(--green);padding:4px 8px;">
            <div style="color:var(--green);font-size:9px;">OI DATA</div>
            <div style="color:var(--text2);font-size:10px;">Check Kenny's weekly note for OI patterns leading explosive moves</div>
          </div>
          <div style="border-left:3px solid var(--amber);padding:4px 8px;">
            <div style="color:var(--amber);font-size:9px;">FUNDING SIGNAL</div>
            <div style="color:var(--text2);font-size:10px;">Funding direction before moves tracked in daily briefs</div>
          </div>
        </div>
      </div>`;

  // Paper desk
  const paper = d.paper_week || {};
  const paperHtml = paper.count > 0
    ? `<div style="background:var(--bg2);border:1px solid var(--border);border-radius:4px;padding:10px;">
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:4px;">
          ${[['TRADES',paper.count],['WINS',paper.wins],['LOSSES',paper.losses],['AVG P&L',fPct(paper.avg_pnl)]].map(([l,v]) =>
            `<div style="text-align:center;"><div style="color:var(--text3);font-size:8px;">${l}</div><div style="color:var(--text);font-size:13px;font-weight:700;">${v}</div></div>`
          ).join('')}
        </div>
      </div>`
    : '<div style="color:var(--text3);font-size:11px;">No paper trades recorded this week.</div>';

  // Agent spotlight
  const spotlightKey = d.spotlight_analyst_key || 'funding';
  const spotlightAgent = I.roster?.agents?.[spotlightKey] || {};
  const spotlightColor = {narrative:'var(--green)',structural:'var(--blue)',leadership:'var(--amber)'}[spotlightAgent.division] || 'var(--text2)';
  const spotlightHtml = `<div style="background:var(--bg2);border:1px solid ${spotlightColor}33;border-left:3px solid ${spotlightColor};border-radius:4px;padding:12px;">
    <div style="color:${spotlightColor};font-size:9px;letter-spacing:1px;margin-bottom:6px;">THIS WEEK · ${spotlightAgent.name || spotlightKey} · ${spotlightAgent.title || ''}</div>
    <div style="color:var(--text2);font-size:11px;margin-bottom:8px;">${spotlightAgent.specialty || ''}</div>
    ${n.spotlight_quote
      ? `<div style="color:var(--text);font-size:12px;font-style:italic;line-height:1.6;">"${n.spotlight_quote}"</div>`
      : '<div style="color:var(--text3);font-size:11px;">Spotlight narrative unavailable.</div>'}
  </div>`;

  // Thomas week ahead
  const thomasWeekAhead = n.thomas_week_ahead ? analystQuote('Thomas Reeves','CIO · Week Ahead','var(--blue)', n.thomas_week_ahead) : '';

  // Render daily sections first, then append weekly extras
  // Temporarily render daily into a div, extract innerHTML
  renderDailyBrief(report);
  const dailyHtml = body.innerHTML;

  body.innerHTML = dailyHtml + `
    ${sectionLabel('WEEKLY SUMMARY')}${weekSummary}
    ${sectionLabel('WEEKLY MOVE PATTERNS')}${movePatternsHtml}
    ${sectionLabel('PAPER DESK · THIS WEEK')}${paperHtml}
    ${sectionLabel('AGENT SPOTLIGHT')}${spotlightHtml}
    ${n.thomas_week_ahead ? sectionLabel('THOMAS REEVES · WEEK AHEAD') + thomasWeekAhead : ''}
  `;
}
```

- [ ] **Step 2: Test weekly report**

Click Weekly Report in browser. Expected: renders daily sections + 5 additional weekly sections (summary, move patterns, paper desk, spotlight, week ahead). Spotlight analyst name appears. No JS errors.

- [ ] **Step 3: Commit**

```bash
git add templates/index.html
git commit -m "feat: renderWeeklyReport() — weekly sections including spotlight, paper desk, move patterns"
```

---

### Task 12: Final wiring, deploy, verify

**Files:**
- Modify: `app.py` — ensure `data/reports/` created in init_db
- Deploy to VPS

- [ ] **Step 1: Confirm data/reports/ mkdir is in init_db()**

```bash
grep -n "data/reports" app.py
```

Expected: line in `init_db()` creating `data/reports`.

- [ ] **Step 2: Full local smoke test**

```bash
python3 app.py &
sleep 2

# All new routes respond
curl -s http://localhost:8080/api/intelligence/roster | python3 -c "import sys,json; d=json.load(sys.stdin); print('Roster OK:', len(d['agents']), 'agents')"
curl -s "http://localhost:8080/api/intelligence/reports/daily" | python3 -c "import sys,json; d=json.load(sys.stdin); print('Daily OK:', d.get('signal_count', d.get('data',{}).get('signal_count','?')))"

kill %1
```

Expected: `Roster OK: 12 agents`, `Daily OK: <number>`

- [ ] **Step 3: Deploy to VPS**

```bash
cd /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0
rsync -avz --exclude='.env' --exclude='data/' --exclude='__pycache__' \
      --exclude='.git' --exclude='*.pyc' --exclude='.superpowers/' \
      ./ root@62.238.15.113:/opt/matrix-trader/
ssh root@62.238.15.113 "systemctl restart matrix-trader && sleep 3 && systemctl status matrix-trader --no-pager | head -5"
```

- [ ] **Step 4: Verify on VPS**

```bash
ssh root@62.238.15.113 "curl -s http://localhost:8080/api/intelligence/roster | python3 -c \"import sys,json; d=json.load(sys.stdin); print('Roster OK:', len(d['agents']))\""
ssh root@62.238.15.113 "curl -s 'http://localhost:8080/api/intelligence/reports/daily' | python3 -c \"import sys,json; d=json.load(sys.stdin); print('Daily generated:', d.get('generated_at','?')[:10])\""
```

Expected: `Roster OK: 12`, `Daily generated: 2026-05-21`

- [ ] **Step 5: Open browser and test full UI on VPS**

Navigate to `http://62.238.15.113:8080` → Intelligence tab → test all three sub-tabs:
- Overview: existing panels still render
- The Firm: org chart loads, clicking analyst shows bio
- Reports: daily brief loads with live VPS data (800+ signals means rich data)

- [ ] **Step 6: Update HANDOFF.md and commit**

Update `HANDOFF.md` session summary section with what was built. Then:

```bash
git add HANDOFF.md
git commit -m "docs: update HANDOFF.md — Cipher Research Group complete"
```

---

## Self-Review Notes

**Spec coverage check:**
- ✅ AGENT_ROSTER with all 12 analysts (Task 1)
- ✅ Personality injection into _run_* prompts (Task 2)
- ✅ /api/intelligence/roster (Task 3)
- ✅ _build_daily_data() with all data sources (Task 4)
- ✅ _generate_report_narrative() with 2-3 batched AI calls (Task 5)
- ✅ /api/intelligence/reports/daily and /weekly (Task 6)
- ✅ /api/intelligence/reports/regenerate (Task 6)
- ✅ Intelligence tab sub-tabs (Task 7)
- ✅ Org chart with all 12 agents (Task 8)
- ✅ Agent bio cards + expand behavior (Task 8)
- ✅ Report selector + date nav + cache (Task 9)
- ✅ Daily brief all sections (Task 10)
- ✅ Weekly extras + spotlight rotation (Task 11)
- ✅ .gitignore update (Task 6 Step 6)
- ✅ Multi-exchange: exchange column in movers, exchange_breakdown in pulse, Ghost Kimura cross-venue autopsy
- ✅ Graceful degradation when AI unavailable (ai_available flag, aiNote banner)
- ✅ Liquidation clusters: CoinGlass path present, fails closed gracefully
- ✅ mt-learner: research briefs in weekly, fails closed if path missing

**Known limitations (by design):**
- "What the Desk Got Wrong" data requires comparing blocked signals to subsequent price moves — this needs a separate price comparison job. Currently returns empty list. Implement as a follow-up.
- OI before explosive move is approximated from available signal data (no real-time OI snapshots stored). Document in UI as "OI data requires real-time monitoring."
- CoinGlass liquidation cluster rendering deferred — data layer is wired but rendering section omitted from daily brief until CoinGlass method is confirmed available.
