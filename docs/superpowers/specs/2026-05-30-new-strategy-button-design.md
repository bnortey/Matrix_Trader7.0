# New Strategy Button — Design Spec
Date: 2026-05-30

## Problem

The Clone Strategy action is buried two levels deep in the Strategies tab:
click "Manage" (in the comparison table) → detail panel opens → Clone button appears.
Users miss the entry point because "Manage" is not an obvious path to creating a strategy.

## Solution

Add a `+ New Strategy` button to the Strategies tab top bar, next to the existing Refresh button.
Clicking it goes directly to the clone form (template picker pre-shown), bypassing the detail panel entirely.

## Approach

Always clone from a template. Strategy parameters (leverage, conviction threshold, flow score, ATR gates)
are interdependent — a blank form risks misconfiguration. The four built-in strategies cover every
paradigm; cloning one and customizing it is the safe path.

## UI Change

**Location:** `.sa-topbar` in `#strategies-section` (templates/index.html ~line 1964)

**Button:** `+ New Strategy` — same `.sa-refresh` style as the Refresh button beside it.

**Click behavior:**
1. Call `openStrategyManager(null)` to mount `#strategy-editor` in the DOM (required before showStrategyEditor)
2. Call `showStrategyEditor(null, 'clone')` to render the clone form with the "Start from template" dropdown

## What Does Not Change

- Per-row "Manage" button and its Clone action remain for users already familiar with the flow
- Clone form UI is unchanged
- No new API routes required
- No new DB schema changes

## Files Touched

- `templates/index.html` — two changes:
  1. Add `+ New Strategy` button to `.sa-topbar`
  2. Add a JS function (e.g. `newStrategy()`) that calls openStrategyManager then showStrategyEditor
