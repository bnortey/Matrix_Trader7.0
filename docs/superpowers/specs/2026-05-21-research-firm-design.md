# Cipher Research Group — Design Spec
**Date:** 2026-05-21  
**Status:** Approved for implementation  
**Scope:** Research firm persona layer, org chart, agent bios, daily/weekly reports

---

## 1. Overview

Transform the Intelligence tab's existing research section into a fully realized crypto research firm — **Cipher Research Group** — with named analysts, character personalities drawn from The Matrix and HBO's Industry, an org chart, and rich daily/weekly market intelligence reports.

The firm is not cosmetic. Every analyst maps 1:1 to an existing agent function in `lib/agents.py`. Their personalities influence both the UI presentation and the LLM system prompts used during signal analysis. Reports have full read access to all Matrix Trader data sources.

---

## 2. Firm Identity

**Name:** Cipher Research Group  
**Reference:** Cipher (The Matrix) — the analyst who saw through the simulation and made a calculated deal. Fitting for a firm that hunts edge in noisy markets.  
**Tagline:** "We read the market's structure. Not its noise."  
**Exchange coverage:** MEXC · Hyperliquid · Bybit (when adapter is complete)

---

## 3. Agent Roster

12 analysts. Each maps to an existing agent function. Personality drives both UI quote style and LLM system prompt flavor.

### Leadership

| Role | Name | Agent Function | Personality Reference | Character Voice |
|---|---|---|---|---|
| Chief Investment Officer | Thomas Reeves | `_run_trader` | Matrix: Thomas Anderson (Neo) + Keanu Reeves | Quiet, decisive, sees patterns. "The conviction is there. We move." |
| Chief Risk Officer | Harper Cross | `_run_risk_manager` | Industry: Harper Stern + Matrix: "crossing" simulation | Ruthless gatekeeper. "This trade doesn't pass. Full stop." |

### Narrative Division

| Role | Name | Agent Function | Personality Reference | Character Voice |
|---|---|---|---|---|
| Head of Narrative Research | Daria Wren | `_run_narrative_debate` | Industry: Daria Greenock | Cold, precise, intimidating. "Three data points confirm it." |
| Tokenomics Lead | Priya Nair | `_run_tokenomics_analyst` | Crypto-native | Methodical, supply-focused. Flags unlock risk before anyone else. |
| Sentiment & Social Intelligence | Hari Stern | `_run_sentiment_analyst` | Industry: Hari Dhar + Stern surname | Eager, anxious, loyal. "Sentiment is... complicated. Leaning bullish." |
| News & Catalyst Lead | Yasmin Cole | `_run_news_analyst` | Industry: Yasmin Kara-Hanani | Socially intelligent, well-connected. "The catalyst is real. Market knows." |
| Technical Strategy Lead | Rishi Sackey | `_run_technical_analyst` | Industry: Rishi Sackey | Grinding detail. "RSI 34.2, EMA crossover confirmed, trend score -12." |

### Structural Division

| Role | Name | Agent Function | Personality Reference | Character Voice |
|---|---|---|---|---|
| Head of Market Structure | Eric Tao | `_run_structural_debate` | Industry: Eric Tao | Commanding, strategic. "Structure is holding. The desk agrees." |
| Microstructure & Order Flow | Niobe Reyes | `_run_microstructure_analyst` | Matrix: Niobe | Skeptical, precise. "Order book says one thing. I don't trust it." |
| Funding & Positioning | Kenny Hassan | `_run_funding_analyst` | Industry: Kenny Kilbane | Experienced old hand. "Funding negative three sessions running. Shorts are getting squeezed." |
| Cross-Venue Intelligence | Ghost Kimura | `_run_cross_venue_analyst` | Matrix: Ghost | Cool, detached. "MEXC and HL diverging 0.3%. That's meaningful." |
| Volatility & Regime | Nadia Okonkwo | `_run_regime_analyst` | Crypto-native | Calm authority. "We're in volatile squeeze. Treat accordingly." |

---

## 4. Intelligence Tab Restructure — Sub-tabs

The Intelligence tab gains a sub-tab row. Existing panels move to **Overview**. Firm and reports get dedicated sub-tabs.

```
Intelligence Tab
├── Overview        ← existing: shadow validation, suggestions, strategy overrides, research briefs
├── The Firm        ← new: org chart, agent bios
└── Reports         ← new: daily brief, weekly report (report selector at top)
```

Sub-tab state persists in `I.activeSubTab` (default: `'overview'`). No URL change needed.

---

## 5. "The Firm" Sub-tab

### Org Chart

Visual hierarchy rendered in HTML/CSS (no external library):

```
                    Thomas Reeves (CIO)
                          │
          ┌───────────────┼───────────────┐
    Daria Wren          Harper Cross      Eric Tao
  (Narrative Head)       (CRO)        (Structural Head)
   ┌──┬──┬──┐                          ┌──┬──┬──┐
 Priya Hari Yasmin Rishi           Niobe Kenny Ghost Nadia
```

Harper Cross sits between the two divisions, connected directly to Thomas. Her veto power is independent of both divisions.

### Agent Bio Cards

Each analyst gets a card with:
- Name + title
- Division badge (Narrative / Structural / Leadership)
- Specialty (2-line description of what they analyze)
- Character voice quote (one line, in their personality)
- Exchange coverage icons (MEXC / HL / Bybit)
- Recent signal: last signal they influenced, conviction delta applied

Clicking a card expands it to show their last 5 signal contributions (symbol, direction, their delta, outcome if closed).

---

## 6. Reports Sub-tab

### Report Selector

```
[ Daily Brief ▾ ]  [ ← May 20 | May 21 → ]
```

Dropdown switches between Daily and Weekly. Date nav loads cached reports. Most recent report auto-loads.

### Report Generation

**Trigger:** Reports are generated server-side on first request for a given period, then cached to `data/reports/daily_YYYY-MM-DD.json` and `data/reports/weekly_YYYY-WNN.json`.

**Structure:** Each report is a JSON object with two layers:
1. `data` — all structured fields (stats, tables, lists) pulled from DB/APIs with no AI cost
2. `narrative` — dict of `analyst_key → quote string`, generated by a small number of batched `call_ai()` calls and cached alongside the data

**AI call strategy (minimize credits):**
- **Call 1:** Thomas opening note + Nadia regime forecast + Harper closing note (one prompt, three quotes returned as JSON)
- **Call 2:** Explosive move autopsy — Kenny + Niobe + Ghost quotes (one prompt, three quotes)
- **Call 3 (weekly only):** Agent spotlight extended quote + Thomas week-ahead outlook
- All other sections (movers, heatmap, session, disagreement log, strategy perf, accountability log) are template-rendered from DB data — no AI

Total: 2 AI calls per daily report, 3 per weekly. All cached — repeat loads cost nothing.

If AI is unavailable, reports render with data only and a notice "Narrative unavailable — AI credits depleted."

**Cache invalidation:** Daily reports regenerate after midnight UTC. Weekly reports regenerate after Monday 00:00 UTC.

### Daily Brief Sections (in order)

1. **Thomas Reeves — Opening Note** (AI narrative, ~60 words)
2. **Market Pulse** — 5 stats: signals today, regime, BTC correlation, signals blocked, desk agreement level
3. **Funding Heatmap** — color-coded pill grid of all scanned pairs: green (negative funding, shorts crowded), neutral, red (positive funding, longs crowded). Sourced from latest MEXC + HL ticker scan.
4. **Top Movers / Losers** — top 3 gainers and losers: pair, % move, volume, funding rate, session (Asia/London/NY)
5. **Session Breakdown** — Asia / London / NY columns: signal count, dominant move type, top mover per session
6. **Explosive Move Autopsy** — for the day's largest % move: OI before, funding, volume spike, book depth change, then Kenny + Niobe + Ghost quotes in character (AI, ~40 words each)
7. **What's Coiling** — pairs with rising OI + funding pressure + flat price: OI 4h delta, funding, price move, liquidation cluster distance, squeeze/flush label
8. **Liquidation Cluster Map** — CoinGlass data (when key active): long and short liquidation stacks by pair, current price distance. Graceful fallback if CoinGlass unavailable.
9. **Agent Disagreement Log** — signals where narrative/structural delta disagreement score > 0.4: pair, direction, conviction, which division was bullish/bearish, Thomas arbitration quote (AI)
10. **Strategy Performance by Regime** — 7-day W/L by strategy × regime matrix from `signals.db`
11. **What the Desk Got Wrong** — blocked signals that moved >5% (missed), approved signals that hit stop (wrong call). Harper quote on each miss, Thomas quote on wrong calls (AI)
12. **Nadia — Regime Forecast** (AI narrative, ~60 words, forward-looking 12h)
13. **Harper Cross — Closing Note** (AI narrative, ~40 words)

### Weekly Report Sections

All Daily sections (recapped over 7 days) plus:

14. **Weekly Move Patterns** — across all explosive moves (>8% in <10 min) that week: what % had OI rising 20%+ beforehand, ask depth drop, funding flip, MEXC leading HL. Pattern table with counts.
15. **Paper Desk Performance** — paper bot trades this week: entries, exits, W/L, avg P&L vs main signal desk. "The Paper Desk" framing.
16. **Upcoming Events** — Priya's domain: known token unlocks, funding settlement windows, macro dates the desk is watching. Sourced from CoinGlass (if active) + manual mt-learner data.
17. **Agent Spotlight** — one analyst per week rotates. Deep feature: their methodology, what they found interesting this week, one extended quote (AI, ~120 words). Rotation order: Kenny → Niobe → Ghost → Nadia → Rishi → Hari → Yasmin → Priya → Daria → Eric → Harper → Thomas. Rotation index persisted in `data/reports/spotlight_state.json` (not gitignored — survives deploys).
18. **Thomas — Week Ahead Outlook** (AI narrative, ~80 words, strategic forward read)

---

## 7. Data Access Map

Reports have full read access to all Matrix Trader data sources:

| Source | What's Used |
|---|---|
| `signals.db → signals` | All sections — outcomes, P&L, conviction, regime, strategy, agent data in signal_json |
| `signals.db → filtered_candidates` | Harper's accountability log, blocked signal tracking |
| `signals.db → position_events` | Paper desk performance, trade lifecycle |
| `signals.db → paper_trades` | Paper desk section |
| `data/risk_gates.json` | Harper's notes, what gates fired |
| `/opt/mt-learner/suggestions/pending.json` | Research briefs in Overview, pattern data |
| `/opt/mt-learner/research/briefs.json` | Weekly move patterns, Priya's upcoming events |
| Live MEXC + HL ticker (cached from last scan) | Funding heatmap, movers table, what's coiling |
| MEXC kline API | Explosive move autopsy (volume, OI proxy) |
| CoinGlass API (optional) | Liquidation cluster map, Priya's upcoming events |
| `signal_json` agent fields | Disagreement log, agent spotlight, bio cards |

---

## 8. Backend — New Routes

```
GET /api/intelligence/roster
    Returns AGENT_ROSTER dict from agents.py
    Response: { agents: [...], firm: { name, tagline, exchange_coverage } }

GET /api/intelligence/reports/daily?date=YYYY-MM-DD
    Generates or serves cached daily brief
    Caches to data/reports/daily_YYYY-MM-DD.json
    Response: { date, data: {...}, narrative: {...}, generated_at, ai_available }

GET /api/intelligence/reports/weekly?week=YYYY-WNN
    Generates or serves cached weekly report
    Caches to data/reports/weekly_YYYY-WNN.json
    Response: { week, data: {...}, narrative: {...}, generated_at, ai_available }

POST /api/intelligence/reports/regenerate
    Force-regenerates a report (clears cache for given date/week)
    Body: { type: "daily"|"weekly", key: "YYYY-MM-DD"|"YYYY-WNN" }
```

---

## 9. Backend — AGENT_ROSTER in agents.py

Add `AGENT_ROSTER` constant to `lib/agents.py`:

```python
AGENT_ROSTER = {
    "trader": {
        "name": "Thomas Reeves",
        "title": "Chief Investment Officer",
        "division": "leadership",
        "specialty": "Synthesizes all research into the final conviction delta",
        "voice": "Quiet, decisive, sees patterns. \"The conviction is there. We move.\"",
        "exchanges": ["MEXC", "HYPERLIQUID", "BYBIT"],
    },
    "risk_manager": {
        "name": "Harper Cross",
        "title": "Chief Risk Officer",
        "division": "leadership",
        "specialty": "Hard blocks, position gates, independent veto on any signal",
        "voice": "Ruthless gatekeeper. \"This trade doesn't pass. Full stop.\"",
        "exchanges": ["MEXC", "HYPERLIQUID", "BYBIT"],
    },
    "narrative_debate": {
        "name": "Daria Wren",
        "title": "Head of Narrative Research",
        "division": "narrative",
        "specialty": "Narrative debate chair — synthesizes macro, fundamentals, and sentiment",
        "voice": "Cold, precise. \"Three data points confirm it.\"",
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
        "voice": "Eager, anxious. \"Sentiment is... complicated. Leaning bullish.\"",
        "exchanges": ["MEXC", "HYPERLIQUID"],
    },
    "news": {
        "name": "Yasmin Cole",
        "title": "News & Catalyst Lead",
        "division": "narrative",
        "specialty": "Event-driven signals, macro catalysts, news-driven regime shifts",
        "voice": "Socially intelligent. \"The catalyst is real. Market knows.\"",
        "exchanges": ["MEXC", "HYPERLIQUID"],
    },
    "technical": {
        "name": "Rishi Sackey",
        "title": "Technical Strategy Lead",
        "division": "narrative",
        "specialty": "Price structure, EMA alignment, RSI context, late-move detection",
        "voice": "Grinding detail. \"RSI 34.2, EMA crossover confirmed, trend score -12.\"",
        "exchanges": ["MEXC", "HYPERLIQUID", "BYBIT"],
    },
    "structural_debate": {
        "name": "Eric Tao",
        "title": "Head of Market Structure",
        "division": "structural",
        "specialty": "Structural debate chair — synthesizes order flow, funding, and regime",
        "voice": "Commanding. \"Structure is holding. The desk agrees.\"",
        "exchanges": ["MEXC", "HYPERLIQUID", "BYBIT"],
    },
    "microstructure": {
        "name": "Niobe Reyes",
        "title": "Microstructure & Order Flow",
        "division": "structural",
        "specialty": "Book imbalance, microprice deviation, spread pressure, aggressive flow",
        "voice": "Skeptical, precise. \"Order book says one thing. I don't trust it.\"",
        "exchanges": ["MEXC", "HYPERLIQUID"],
    },
    "funding": {
        "name": "Kenny Hassan",
        "title": "Funding & Positioning Strategist",
        "division": "structural",
        "specialty": "Funding rates, OI delta trends, liquidation proximity, crowded positioning",
        "voice": "Old hand. \"Funding negative three sessions running. Shorts are getting squeezed.\"",
        "exchanges": ["MEXC", "HYPERLIQUID"],
    },
    "cross_venue": {
        "name": "Ghost Kimura",
        "title": "Cross-Venue Intelligence Lead",
        "division": "structural",
        "specialty": "MEXC vs Hyperliquid vs Bybit basis, venue-leader detection, arbitrage pressure",
        "voice": "Cool, detached. \"MEXC and HL diverging 0.3%. That's meaningful.\"",
        "exchanges": ["MEXC", "HYPERLIQUID", "BYBIT"],
    },
    "regime": {
        "name": "Nadia Okonkwo",
        "title": "Volatility & Regime Specialist",
        "division": "structural",
        "specialty": "Regime classification, ATR context, BTC correlation, no-trade-zone detection",
        "voice": "Calm authority. \"We're in volatile squeeze. Treat accordingly.\"",
        "exchanges": ["MEXC", "HYPERLIQUID", "BYBIT"],
    },
}
```

Personality is also injected into each analyst's LLM system prompt in `_run_*` functions so their actual analysis reflects their character voice.

---

## 10. Frontend — Sub-tab State

Add to `I` state object in `index.html`:
```js
activeSubTab: 'overview',   // 'overview' | 'firm' | 'reports'
activeReport: 'daily',      // 'daily' | 'weekly'
reportDate: null,           // YYYY-MM-DD string, null = today
reportWeek: null,           // YYYY-WNN string, null = current week
reportCache: {},            // keyed by "daily_YYYY-MM-DD" or "weekly_YYYY-WNN"
roster: null,               // loaded once from /api/intelligence/roster
```

---

## 11. Multi-Exchange Rule

Every report section that references signal data must be exchange-aware:
- Movers/losers table includes exchange column (MEXC / HL)
- Session breakdown aggregates across exchanges
- Funding heatmap shows MEXC and HL pairs
- Ghost Kimura's autopsy section always notes which venue led
- Agent bio cards show exchange coverage icons per analyst
- When Bybit adapter is complete, its data slots in automatically — no report template changes needed

---

## 12. Personality Injection — LLM System Prompts

Each `_run_*` analyst function gets a one-line personality prefix added to its system message:

```python
# Example for _run_funding_analyst
PERSONALITY = AGENT_ROSTER["funding"]["voice"]
system = (
    f"You are Kenny Hassan, {AGENT_ROSTER['funding']['title']} at Cipher Research Group. "
    f"Personality: {AGENT_ROSTER['funding']['voice']} "
    "Analyze funding and positioning data..."
)
```

This keeps the existing analysis logic intact while giving each analyst a consistent character voice in their outputs.

---

## 13. What's Out of Scope

- Quarterly reports — deferred until full quarter of clean data exists
- On-chain wallet activity — requires dedicated API (Glassnode/Nansen), planned for future
- Sector rotation analysis — deferred, needs category tagging on pairs
- Bybit data in reports — Bybit adapter must be completed first (parallel task)
- Real-time report updates — reports are generated once per period and cached

---

## 14. File Changes Summary

| File | Change |
|---|---|
| `lib/agents.py` | Add `AGENT_ROSTER` dict; inject personality into each `_run_*` system prompt |
| `app.py` | Add `GET /api/intelligence/roster`, `GET /api/intelligence/reports/daily`, `GET /api/intelligence/reports/weekly`, `POST /api/intelligence/reports/regenerate`; add report generation + caching logic |
| `templates/index.html` | Add sub-tab row to Intelligence tab; add `renderFirm()`, `renderOrgChart()`, `renderAgentBios()`, `renderReport()`; update `I` state; update `loadIntelligence()` |
| `data/reports/` | New directory; daily and weekly JSON cache files + `spotlight_state.json`. Add `data/reports/daily_*.json` and `data/reports/weekly_*.json` to `.gitignore` (cache files only — `spotlight_state.json` is NOT gitignored) |
| `.gitignore` | Add `data/reports/daily_*.json` and `data/reports/weekly_*.json` |
