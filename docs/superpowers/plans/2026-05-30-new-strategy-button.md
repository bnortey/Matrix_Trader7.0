# New Strategy Button — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `+ New Strategy` button to the Strategies tab top bar that opens the clone form directly.

**Architecture:** Single-file change to `templates/index.html`. Add a `newStrategy()` JS function and a button in `.sa-topbar`. The function calls `openStrategyManager('balanced')` to mount the `#strategy-editor` DOM node, then immediately calls `showStrategyEditor('balanced', 'clone')` to render the clone form. The `'balanced'` key is used as the default template because `showStrategyEditor` requires a valid strategy key — the user can change the template via the dropdown inside the form.

**Tech Stack:** Vanilla JS, inline HTML, Flask/Jinja2 template (no build step)

---

### Task 1: Add `newStrategy()` function to index.html

**Files:**
- Modify: `templates/index.html` — add JS function near `openStrategyManager` (~line 4735)

- [ ] **Step 1: Locate the insertion point**

Open `templates/index.html`. Find `function openStrategyManager` (around line 4735). The new function goes directly above it.

- [ ] **Step 2: Insert the `newStrategy` function**

Add this block immediately before `async function openStrategyManager`:

```javascript
async function newStrategy() {
  await openStrategyManager('balanced');
  showStrategyEditor('balanced', 'clone');
}
```

- [ ] **Step 3: Verify no syntax errors**

Run the app locally and open the browser console. Navigate to the Strategies tab. Run in the console:
```javascript
newStrategy()
```
Expected: the clone form renders with "Clone Strategy" heading and "Start from template" dropdown visible. No console errors.

---

### Task 2: Add `+ New Strategy` button to the Strategies top bar

**Files:**
- Modify: `templates/index.html` — edit `.sa-topbar` (~line 1964)

- [ ] **Step 1: Locate the top bar**

Find this block in `templates/index.html` (~line 1964):

```html
        <div class="sa-topbar">
          <div>
            <div class="sa-title">Strategy Analytics</div>
            <div class="sa-subtitle">Compare strategy trust, decay, symbol fit, volatility regime, and the concepts each strategy is built around.</div>
          </div>
          <button class="sa-refresh" onclick="loadStrategyAnalytics(true)">Refresh</button>
        </div>
```

- [ ] **Step 2: Add the button**

Replace that block with:

```html
        <div class="sa-topbar">
          <div>
            <div class="sa-title">Strategy Analytics</div>
            <div class="sa-subtitle">Compare strategy trust, decay, symbol fit, volatility regime, and the concepts each strategy is built around.</div>
          </div>
          <div style="display:flex;gap:8px;">
            <button class="sa-refresh" onclick="newStrategy()">+ New Strategy</button>
            <button class="sa-refresh" onclick="loadStrategyAnalytics(true)">Refresh</button>
          </div>
        </div>
```

- [ ] **Step 3: Visual check**

Reload the Strategies tab. Confirm:
- Two buttons appear in the top-right of the top bar: `+ New Strategy` and `Refresh`
- Both use the same `.sa-refresh` style (same size, color, border)
- No layout breakage on mobile (iPhone Safari width ~390px) — buttons should wrap if needed due to `flex-wrap` inherited or the flex container

- [ ] **Step 4: Functional check**

Click `+ New Strategy`. Confirm:
- Clone form renders with heading "Clone Strategy"
- "Start from template" dropdown is visible and populated with template options
- Selecting a template pre-fills the form fields
- Clicking "Create" submits and creates the strategy
- Clicking "Cancel" dismisses the form

---

### Task 3: Deploy to production and verify

**Files:** No new files — deploy existing change.

- [ ] **Step 1: Deploy to VPS**

```bash
cd /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0
rsync -avz --exclude='.env' --exclude='data/' --exclude='__pycache__' \
      --exclude='.git' --exclude='*.pyc' ./ root@62.238.15.113:/opt/matrix-trader/
ssh root@62.238.15.113 "systemctl restart matrix-trader"
```

Expected: rsync completes, service restarts without error.

- [ ] **Step 2: Verify on production**

Open `http://62.238.15.113:8080` (or `http://207.148.66.39:8080`). Navigate to Strategies tab. Confirm `+ New Strategy` button is visible and the clone form opens correctly.

- [ ] **Step 3: Commit**

```bash
git add templates/index.html
git commit -m "feat: add + New Strategy button to strategies tab top bar"
```
