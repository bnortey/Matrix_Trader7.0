# Matrix Trader 7.0 — Design Brief

> Paste this into any AI session before touching CSS or layout. It documents
> the current design system, what was intentional, what was expedient, and
> where we want to take it. Last updated: 2026-04-21.

---

## 1. The Design Mandate

**Matrix Trader is a tool, not a product.** Design serves the signal.
Every pixel either helps the trader make a faster, better-informed decision
or it wastes space. Aesthetic ambition is fine — but never at the cost of
scan-tab legibility or detail-panel scannability at 2am.

The guiding phrase: **terminal-grade precision, luxury-grade finish.**
Think Bloomberg Terminal if it had been designed by Linear, not by a bank.

---

## 2. Current Design System (as-built)

### 2.1 Color Tokens

```css
:root {
  --bg:     #0b0d12;   /* near-black with blue undertone */
  --bg2:    #0e1016;   /* slightly lighter card surface */
  --bg3:    #0a0b0f;   /* darkest — header, tab bar, stat bar */
  --border: rgba(255,255,255,0.06);  /* very subtle separation */

  --text:   #e8e8f0;   /* primary — warm off-white, slight blue */
  --text2:  rgba(255,255,255,0.45); /* secondary — labels, supporting */
  --text3:  rgba(255,255,255,0.25); /* tertiary — timestamps, hints */

  --green:  #00e676;   /* LONG direction, profit, S-tier, CTA buttons */
  --red:    #ff5252;   /* SHORT direction, loss, danger */
  --amber:  #ffab40;   /* warnings, aging signals, open positions */
  --blue:   #448aff;   /* A-tier signals */

  --mono:   'SF Mono', Menlo, 'Courier New', monospace;
}
```

**What's working:** The near-black background with slight blue undertone reads
as "trading terminal" rather than "generic dark mode." The `--green` / `--red`
semantic pairing is consistent throughout and immediately communicates
direction.

**What's weak:** The three background levels (`--bg`, `--bg2`, `--bg3`) are
so close in value that layering doesn't read clearly. Cards and surfaces blur
together. The border at 6% opacity is nearly invisible — it's doing less work
than it should.

### 2.2 Typography

- **Body:** `-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif` — 14px base, 1.4 line height
- **Monospace:** `'SF Mono', Menlo, 'Courier New'` — used for prices, scores, numeric data
- **Font smoothing:** `-webkit-font-smoothing: antialiased` — yes

**Signal row hierarchy (current):**
```
Symbol name:  --mono, 13px, weight 700
Price:        --mono, 10px, text3
Conv score:   --mono, 22px, weight 700  ← biggest number on the row
Tier badge:   10px, weight 800, uppercase
Tags:         9px, uppercase, letter-spacing 0.06em
Why line:     11px, italic, text2
```

**Detail panel hierarchy (current):**
```
Section labels: 9px, uppercase, letter-spacing 0.1em, text3, weight 700
Price:          --mono, 20px, weight 700
Ladder prices:  --mono (via fmtPrice)
Context grid:   11px cells with label/value pairs
```

**What's working:** The mono / sans contrast is strong. Prices look like
prices, not body text.

**What's weak:** No display/heading typeface. The 22px conviction score is
doing heavy lifting as the largest element but it's just a weight-700 mono
number — not a designed moment. Section labels are fine but feel generic.

### 2.3 Spacing System

Currently ad-hoc — no formal spacing scale. Observed values:
`2px, 3px, 4px, 5px, 6px, 7px, 8px, 10px, 12px, 14px, 16px, 20px, 24px, 40px`

This is too many steps with no intentional rhythm. Padding is often chosen
by eye within each component, leading to inconsistency between tabs.

### 2.4 Border Radius

- Buttons: `4px–6px`
- Cards / panels: `5px–10px`
- Dots / avatars: `50%`
- Detail panel: `10px`
- Tags: `2px–3px`

**What's weak:** No consistent radius scale. `2px` tags next to `10px`
cards next to `3px` badges look like three different design eras.

### 2.5 Motion / Transitions

- Button hovers: `opacity 150ms`
- Row hovers: `background 150ms`
- Skeleton loading: `pulse` keyframe, 1.5s ease-in-out
- Spinner: `spin` 0.8s linear

No spring physics, no entrance animations, no micro-interactions beyond
hover states. This is intentional for now (performance, focus). The progress
bar in `#pos-status-bar` has `transition: width 0.3s` — the only animated
data element.

### 2.6 Component Inventory

| Component | Location | Notes |
|---|---|---|
| Header | Static HTML | Logo mark, scan button, scan meta |
| Tab bar | Static HTML | 4 tabs: Signals, Market, Tools, History |
| Strategy bar | Signals tab | 4 mode buttons + freshness dot |
| Stat bar | Signals tab | 4 stats: pairs, signals, top conviction, last scan |
| Filter bar | Signals tab | Direction toggle + sort/vol selects |
| Signal row | JS: `rowHTML()` | Symbol, score, tier, direction pill, tags, why |
| Detail panel | JS: `renderDetail()` | Shared panel for all tabs |
| Trade plan ladder | JS: `ladderHTML()` | Entry/TP/Stop price ladder |
| Context grid | JS: `contextHTML()` | RSI, ATR, funding, sentiment, etc. |
| AI report | JS: `reportHTML()` | Labelled sections from Claude |
| Market browser | JS: `renderMarket()` | 6-column grid, sortable |
| Tools tab | Static HTML | Risk calc + compound planner |
| History tab | JS-rendered | Perf banner, open positions, closed signals |
| Perf banner | JS: `updatePerfBanner()` | 6 stats: signals, winrate, P&L, open, best R, streak |
| Position status bar | JS: `buildStatusBarHTML()` | Live P&L, progress bar, TP markers |
| Open guide modal | Static HTML | First-run tutorial |

---

## 3. What's Intentionally Minimal (Don't Touch)

These decisions were made deliberately and should survive any design pass:

1. **No sidebars.** The layout is a single list + detail panel. No nav trees.
2. **No data tables with 8+ columns.** Market browser at 6 columns is the max.
3. **Black/near-black background only.** No dark-grey panels, no colored backgrounds.
4. **Green = action / LONG / profit. Red = danger / SHORT / loss.** These are semantic, not decorative. Never invert.
5. **Mono font for every number.** Prices, scores, percentages — all monospace. This is non-negotiable for number alignment and terminal feel.
6. **No rounded hero cards with shadows.** This isn't a SaaS dashboard. Surfaces should be subtle.
7. **The detail panel is a right-side sheet (desktop) / bottom sheet (mobile).** Don't redesign this into a modal.

---

## 4. What's Expedient (Built Fast, Can Be Improved)

These were built for speed and function, not finish. These are the primary design debt areas:

### 4.1 The Signal Row

Currently a dense block of information packed into ~44px. The conviction
score is the hero number (22px mono) but it competes with the direction pill,
symbol name, and tags for attention. The visual hierarchy is functional but
not designed.

**Vision:** The conviction score should own a dedicated column — think of
it like a rating digit in a financial data terminal. The direction should be
communicated through color at the row level (left border color), not just a
pill. Tags should collapse into a single indicator or be hidden behind hover.

### 4.2 The Detail Panel

Currently: a flat list of labeled sections separated by `margin-bottom: 16px`.
Everything is the same visual weight. The trade plan ladder, chart, context
grid, and AI report all feel like siblings rather than a hierarchy.

**Vision:** The trade plan ladder should be the dominant element — the thing
you actually trade from. The chart is supporting context. The AI report is
supplementary insight. The context grid is reference data. Each should have
a distinct visual weight appropriate to its importance.

### 4.3 The Performance Banner

Added in the current session. Functional but text-forward. The numbers are
big but they're sitting in a flat card with no visual differentiation.

**Vision:** Win rate and P&L should be visually dominant over signals count
and streak. Color should do more work — not just on the number but on the
cell background, creating a light field of green/red that reads from across
the room.

### 4.4 The History Table

Currently a standard HTML table. It works but reads as administrative UI,
not a trading tool.

**Vision:** Each row is a trade with an outcome. Winning rows should feel
slightly celebratory — a green left border, a faint green glow on the
result cell. Losing rows should feel sober, not alarming.

### 4.5 Controls (Buttons, Selects, Inputs)

All ghost buttons and selects use `border: 1px solid rgba(255,255,255,0.06)`.
They're barely visible until you know they're there. This is fine for
secondary controls but makes the UI feel like it has no affordances.

---

## 5. Design Vision — Where We're Heading

### 5.1 The Aesthetic

**Reference points (study these, don't copy them):**
- Linear — spatial layout, clean typography hierarchy, subtle surface depth
- Vercel dashboard — dark, understated, data-dense without feeling cramped
- TradingView's dark mode — the right amount of chrome for financial data
- Raycast — sharp corners, precise type, micro-interactions that feel earned

**Not these:**
- Crypto exchange casino aesthetic (neon gradients, animated tickers)
- Bloomberg Terminal overload (too much, no hierarchy)
- Generic shadcn/ui dark mode (too soft, too rounded)

### 5.2 The Token Upgrades to Plan

When ready to design-pass, these CSS variables should be revisited:

```css
/* Current — too similar, too flat */
--bg:  #0b0d12
--bg2: #0e1016   /* only 0.015 luminance difference */
--bg3: #0a0b0f

/* Proposed direction — add real depth with subtle warm/cool split */
--bg:        #0c0e14;   /* base canvas — cooler */
--surface:   #121520;   /* cards, panels — warmer, readable */
--surface2:  #181c28;   /* elevated surfaces, hover states */
--bg-header: #090b10;   /* header/navbar — darkest */

/* Border needs more presence */
--border:       rgba(255,255,255,0.07);
--border-subtle: rgba(255,255,255,0.04);
--border-strong: rgba(255,255,255,0.12);

/* Text3 is too invisible */
--text3: rgba(255,255,255,0.32);  /* bump from 0.25 */
```

### 5.3 Typography Upgrade

**Add a display weight for hero numbers:**
The conviction score, win rate, P&L percentage — these are the moments where
the UI should feel exciting. They need display weight, not just `font-weight: 700`.

```css
/* Add to :root */
--font-display: 'SF Pro Display', -apple-system, sans-serif;

/* For hero stats — tighter tracking, heavier weight */
.hero-number {
  font-family: var(--font-display);
  font-weight: 800;
  letter-spacing: -0.03em;  /* tight — the Bloomberg/Linear look */
  font-feature-settings: 'tnum';  /* tabular figures */
}
```

Tabular figures (`font-feature-settings: 'tnum'`) should be applied to ALL
numeric displays so columns align without monospace.

### 5.4 Spacing Scale

Adopt a strict 4px base unit:

```
4px  — micro gaps (between badge and text)
8px  — small gaps (tag padding, between inline elements)
12px — component padding (row padding, card padding)
16px — section gaps (between ladder rows, context items)
20px — card padding (detail panel sections)
24px — section dividers
32px — major section gaps
48px — page-level spacing
```

### 5.5 Radius Scale

```
2px  — tags, micro badges (sharp — terminal feel)
4px  — buttons, inputs, small cards
6px  — standard cards, detail sections
10px — large panels, modal surfaces
16px — full panel (bottom sheet on mobile)
```

Kill the `3px`, `5px`, `9px` variants. Pick one from the scale.

### 5.6 Depth System

Currently: depth is created only through background color differences.
Plan to add one layer of subtle depth:

```css
/* Surface elevation via thin border + optional glow */
.surface-1 {
  background: var(--surface);
  border: 1px solid var(--border);
}
.surface-elevated {
  background: var(--surface2);
  border: 1px solid var(--border-strong);
  box-shadow: 0 4px 24px rgba(0,0,0,0.4);  /* dark shadow only */
}
```

**No light glow shadows.** Box shadows are black-transparent only —
the terminal doesn't glow white.

Exception: directional signals can have a very faint color shadow to
communicate direction:
```css
/* LONG signal card — barely perceptible green ambient */
box-shadow: 0 0 0 1px rgba(0,230,118,0.08);
```

### 5.7 Signal Row — Redesign Direction

**Current layout:**
```
[Symbol + price] [Score] [Tier] [Dir pill] [Chg%] [Tags + Why]
```

**Target layout — two clear zones:**
```
LEFT ZONE (fixed width):
  [LONG/SHORT indicator — left border color + subtle bg tint]
  [Symbol] [Tier badge]
  [Why line — truncated]

RIGHT ZONE (flush right):
  [Score — large mono]
  [Chg% + funding rate — stacked]
  [Tags — max 3, rest hidden]
```

The key change: conviction score moves to the right and becomes the
**dominant right-side element**, not competing with the direction pill.

### 5.8 Detail Panel — Redesign Direction

**Current:** Flat list of labeled sections, all equal weight.

**Target:** Three visual tiers:

**Tier 1 — Act now:** Status bar (for positions) + Trade plan ladder
- Largest type, highest contrast, first in DOM
- The ladder gets a light left border in direction color

**Tier 2 — Confirm:** Chart + Key context (RSI, ATR, daily trend, funding)
- Medium prominence, condensed

**Tier 3 — Background:** Full context grid + AI report
- Small type, dimmer — reference only

### 5.9 Micro-interactions to Add

1. **Signal row hover:** Currently just `background 150ms`. Add a
   subtle `translateX(2px)` on the row — a Bloomberg-style "lean right"
   that hints at clickability.

2. **Outcome buttons:** When you tag WIN/LOSS, the button should do a
   brief color pulse — `animation: pulse-once 300ms ease-out` — before
   settling. Currently: instant background color, no feedback.

3. **Scan progress bar:** Currently a flat 2px green line. Add a shimmer
   sweep animation during active scan — a traveling highlight over the bar.

4. **Number updates:** When P&L values update from price refresh, flash
   the number briefly (white, then back to green/red). `@keyframes flash`.
   Used in the position status bar and perf banner.

### 5.10 Mobile — Priority Improvements

The mobile layout works but feels like a desktop app squeezed into phone
width. Target improvements:

1. **Bottom sheet detail panel:** Already works. Add `border-radius: 16px 16px 0 0`
   and a pull handle bar at the top.

2. **Swipe to close:** The overlay click closes the panel. Add swipe-down
   gesture (touch events) so it feels native.

3. **Signal row:** Currently 44px min-height. On mobile it should expand
   slightly — 52px min with larger touch targets on the tier/direction badges.

4. **Performance banner on mobile (< 500px):** Already collapses to compact
   mode. Consider showing only 3 stats (win rate, P&L, open) in a 3-up grid
   instead of the full 6 compressed.

---

## 6. What NOT to Do (Design Specifics)

In addition to the hard rules in CLAUDE.md, these design anti-patterns are banned:

- **No glassmorphism.** `backdrop-filter: blur()` is banned. It's a 2021 trend
  and it tanks performance on older iPhones.
- **No gradient backgrounds.** `linear-gradient` on card backgrounds is banned.
  The terminal is flat. Gradients on text (for effect) may be used sparingly.
- **No drop shadows with color.** `box-shadow: 0 4px 12px rgba(0,230,118,0.3)` —
  no. Shadows are black only.
- **No skeleton loaders for the detail panel.** It loads fast. A spinner is fine.
- **No infinite scroll animations or ticker tapes.** This isn't Bloomberg TV.
- **No emoji in the UI.** The first-run guide has some. Remove them in the design pass.
- **No card grids.** Signal list is a list. Market browser is a list. No masonry,
  no card grid, no Pinterest layout.
- **No color backgrounds on full sections.** A faint `rgba(0,230,118,0.02)` on
  a LONG active row — fine. A green-tinted card background — no.

---

## 7. Design Pass Phases

When you're ready to act on this (after the data collection period):

### Phase D1 — Token & Typography (smallest blast radius)
- Update CSS variables (bg levels, border opacity, text3)
- Add tabular figure feature settings to numeric elements
- Tighten letter-spacing on hero numbers
- Normalize border-radius to the 2/4/6/10/16 scale
- Add `--border-strong` for elevated surfaces

### Phase D2 — Signal Row
- Redesign `.sig-row` layout: left zone + right zone
- Move conviction score to right, larger
- Direction communicated through left border + subtle row tint (not just pill)
- Tags: max 3 visible, `+N more` if more

### Phase D3 — Detail Panel
- Three visual tiers: act / confirm / background
- Ladder gets direction-colored left border
- Section labels get more separation — horizontal rule or more whitespace

### Phase D4 — History Tab
- Perf banner: color-field backgrounds on win rate and P&L cells
- History table: direction-colored left borders on rows
- WIN rows: faint green ambient; LOSS rows: faint red ambient

### Phase D5 — Motion & Polish
- Hover micro-interactions on signal rows
- Outcome button pulse animation
- Scan progress bar shimmer
- Number flash on price update
- Mobile pull-handle + swipe-to-close

### Phase D6 — Mobile Pass
- Full mobile review across all tabs
- Touch target audit
- Bottom sheet refinements
- Perf banner 3-up mobile layout

---

## 8. Current Design Debt Locations (File: templates/index.html)

| Component | CSS lines | Known issue |
|---|---|---|
| Signal row | 197–231 | Ad-hoc spacing, competing visual hierarchy |
| Detail panel | 411–470 | Flat weight, no visual tier system |
| Stat bar | 127–136 | Feels like a chip tray, not a dashboard |
| Outcome buttons | 575–594 | No animation on state change |
| Tags | 226–231 | 2px radius looks different from everything else |
| Perf banner | 712–750 | Flat card, numbers need more differentiation |
| History table | (JS-generated) | No directional color on rows |
| Mobile overrides | 750–798 | Minimal — mostly layout fixes, not design |

---

## 9. Session Notes

### 2026-04-21 — Design brief created
This document was created during the 2-3 week data collection period (P3).
No design changes made — brief only. The current UI is functional and usable.
Design pass begins after the data period confirms which screens get the most
use. Expected to start with Phase D1 (tokens) then D2 (signal row) as those
are the highest-frequency interaction surfaces.
