Generate an updated HANDOFF.md for Matrix Trader 7.0 by reading the actual
current state of the codebase. Do not use assumptions or memory — read the
files directly.

## Step 1 — Gather current state

Run these commands and read the output:

```bash
# Recent commits
git -C /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0 log --oneline -20

# Line counts (to report accurate file sizes)
wc -l /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0/app.py
wc -l /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0/templates/index.html

# Current requirements
cat /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0/requirements.txt

# Current Flask routes
grep -n "@app.route" /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0/app.py

# Current JS state objects
grep -n "^const S\|^const M\|^let currentTab\|^let currentTV" /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0/templates/index.html

# Tab sections in HTML
grep -n "id=\"tab-\|id=\".*-section\"" /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0/templates/index.html

# Current env vars expected
grep -n "os.getenv\|load_dotenv" /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0/app.py
```

Also read:
- /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0/CLAUDE.md
- /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0/lib/indicators.py (first 30 lines)
- /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0/lib/laddering.py (first 20 lines)

## Step 2 — Write the updated HANDOFF.md

Write the file to:
/Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0/HANDOFF.md

The file must contain exactly these sections in this order. Every section
must reflect what the code actually contains right now, not what was
planned or remembered.

---

### Section 1: Header and warning

```
# Matrix Trader 7.0 — AI Handoff Document

> Paste this entire file at the start of any AI session (ChatGPT, Gemini,
> etc.) when continuing work on this project. Read every word before
> writing a single line of code. This file was auto-generated from the
> actual codebase — it reflects current state, not planned state.

Last updated: [today's date]
Last commit: [most recent git commit hash and message]
app.py: [N] lines
index.html: [N] lines
```

---

### Section 2: What this project is

One paragraph. What it does, what it is not. Keep it under 100 words.

---

### Section 3: Why these rules exist

The MT2–MT6 failure table. Do not change this — it is historical fact.

---

### Section 4: Hard rules

The 12 hard rules. Do not soften or reorder them. If new rules were
added this session, append them.

---

### Section 5: File structure

Show actual file structure with real line counts from wc -l output.
Mark which files exist and which are planned.

---

### Section 6: Tech stack

What is actually installed (from requirements.txt), not what is planned.

---

### Section 7: MEXC API reference

Static. Do not change unless new endpoints were added this session.

---

### Section 8: Flask routes

List every @app.route found in app.py with its method and a one-line
description of what it does. Get this from the actual grep output.

---

### Section 9: Signal data shape

The full signal dict from enrich_signal(). Read app.py to confirm the
actual fields — do not use the version from memory.

---

### Section 10: JavaScript state objects

Copy the actual S, M, and module-level let variables from index.html
exactly as they appear in the code. Get these from the grep output.

---

### Section 11: Dashboard structure

List the actual tabs and sections that exist in index.html right now.
Check the grep output for id="tab-* and id="*-section".

---

### Section 12: TradingView integration

The toTVSymbol function and chart configuration. Read from index.html
if it changed this session.

---

### Section 13: Color system

Static CSS variables. Do not change unless new variables were added.

---

### Section 14: Phase status

Check CLAUDE.md for the phase roadmap. Update checkboxes to reflect
what was actually completed based on git commits and CLAUDE.md state.
Be honest — only mark done what is actually done.

---

### Section 15: Current task list

What is actually next based on CLAUDE.md and the recent commits. List
in priority order. Be specific — not "improve UX" but "add template-
based AI signal report to detail panel using signal dict fields."

---

### Section 16: What NOT to do

The standard prohibition list. Append anything new that came up this
session as a specific violation to avoid.

---

### Section 17: How to run

Static. Only update if port or command changed.

---

### Section 18: Task framing template

The standard template for asking any AI to make changes. Do not modify.

---

### Section 19: Verification checklist

The standard 13-point checklist. Append any new checks that came up
this session.

---

### Section 20: Returning to Claude

The standard return protocol. Do not modify.

---

### Section 21: Session notes (NEW — append each session)

Add a dated entry summarizing what was built or decided this session.
Format:

```
### [DATE] — Session summary
Built: [what was completed]
Decided: [any architectural decisions made]
Deferred: [what was discussed but not built]
Watch out for: [anything GPT should be extra careful about]
```

This section grows over time. Do not delete old entries.

---

## Step 3 — Commit

After writing the file, run:

```bash
cd /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0
git add HANDOFF.md
git commit -m "docs: update handoff document [auto]"
echo ""
echo "✓ HANDOFF.md updated and committed"
echo "  Copy this file when switching to another AI"
```

## Step 4 — Confirm

Report back:
- The date stamp written into the file
- The last commit hash written into the file  
- The line counts for app.py and index.html
- Any new items added to Session Notes
- Confirmation that git commit succeeded
