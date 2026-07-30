# Matrix Trader 7.0 — AI Handoff Document

> Paste this entire file at the start of any AI session (ChatGPT, Gemini,
> etc.) when continuing work on this project. Read every word before
> writing a single line of code. This file was auto-generated from the
> actual codebase — it reflects current state, not planned state.
> Update it at the end of every session before deploying.

Last updated: 2026-07-30
Latest implementation commit: 624d17d feat: explain research experiments clearly
app.py: 37,703 lines
index.html: 19,710 lines

---

## 2026-07-30 — Approval-ready research experiment briefs

**Built, tested, committed, and deployed to production `207.148.66.39`:**

- Reworked every Research Autopilot experiment card into a plain-language,
  approval-ready brief under Intelligence → Strategy Ideas. The collapsed card
  shows the experiment type, target strategy, decision surface, authority,
  allocation, progress, outcomes, verdict, and exact reason it is not running.
- Added an expandable full brief covering the hypothesis, expected source of
  edge, exact treatment and control behavior, cohort assignment, primary
  metric, forward-test progress, validation blockers, source evidence and
  quality, relevant trading costs, failure risks, promotion requirements,
  rollback behavior, authority ceiling, and what the test cannot prove.
- Made current runtime behavior explicit for the initial gate experiments:
  the order-flow challenger uses MT7's existing flow confirmation, while the
  funding challenger blocks only the predeclared extreme-funding cases without
  flow confirmation. The control arm remains the current strategy policy.
- Preserved experiment integrity by refreshing explanatory provenance only.
  Existing frozen contracts, policy fingerprints, approvals, lifecycle
  timestamps, assignments, and outcome progress are not rewritten by the
  explanation refresh.
- Added exact decision thresholds to every brief: at least 50 closed outcomes,
  seven elapsed days, 20 independent market days, complete relevant costs,
  placebo/falsification checks, and the two-stage manual Paper promotion path.
  The UI states that no experiment can change live trading or increase risk.
- Added richer research provenance to candidates and ledger entries, including
  thesis, expected edge, strategy shape, entry and rejection rules, cohort
  definition, known caveats, failure modes, gate impact, overfit risk, and
  missing or partial evidence fields.

**Verification:**

- Final full local suite: `112/112` tests passed, including `10/10`
  orchestrator-focused tests. Python compile, inline JavaScript parse, and
  `git diff --check` passed.
- Desktop and exact `390×845` mobile browser acceptance passed on the deployed
  source state with no console errors, no horizontal overflow, fully expanded
  details, and summary tiles that wrap instead of clipping.
- Production file hashes match the tested local backend, dashboard, and
  orchestrator module. The served dashboard contains the full brief and
  decision-rule UI; `matrix-trader` and `mt-learner` are active.
- Production SQLite integrity is `ok` with 4,006 signals and 2,754 paper-trade
  rows. All four research contracts expose a populated brief and a 50-outcome
  target. Two await approval, two need data, zero are approved, zero behavioral
  challengers are active, and live behavior remains unchanged.
- Before deployment, a verified 450 MB recovery snapshot was created at
  `/opt/matrix-trader/backups/20260730T191600Z-experiment-briefs`.

---

## 2026-07-30 — Research experiment orchestration and causal promotion ladder

**Built, tested, committed, and deployed to production `207.148.66.39`:**

- Added a typed research contract and lifecycle for new strategies, entry and
  risk gates, regime filters, stops, exits, sizing, scoring, portfolio,
  execution, annotation, and data-collection ideas. Every idea now declares
  the decision surface it changes, evaluator, treatment allocation, conflict
  keys, authority ceiling, evidence requirements, and rollback contract.
- Added a conflict-aware scheduler with a maximum of two simultaneous
  behavioral Paper challengers and ten shadow/observation experiments.
  Orthogonal ideas may run together; experiments sharing a strategy decision
  surface, portfolio capital, or active legacy learner policy test are
  serialized. Deterministic assignment keeps treatment/control arms stable.
- Added many-to-many assignment and outcome attribution. Research experiments
  no longer rely on one experiment ID stored on a trade, and blocked-entry
  counterfactuals are followed forward rather than counted as automatic wins.
  Effective sample size is clustered by symbol-day and market-day.
- Hardened evaluation against false discovery and overfitting:
  family-wise test counts, Bonferroni-adjusted thresholds, deterministic
  permutation placebo checks, one predeclared extension, untouched 80/20
  confirmation cohorts, explicit cost-completeness metadata, and a promotion
  block for funding-sensitive claims until realized funding cashflows exist.
- Added automatic preparation, scheduling, progress reconciliation, stale
  parking, and falsification rollback. Automatic authority ends at Paper:
  starting a behavioral challenger and either promotion stage require an
  explicit user action and reason; no route enables live trading.
- Split promotion into two manual Paper stages. A first promotion creates a
  fresh confirmation child with a new policy fingerprint; a second promotion
  marks it confirmed. Failed Paper activation writes a compensating
  non-behavioral control record so a rejected experiment cannot remain armed.
- Corrected historical research semantics: mixed-policy retrospective matches
  can produce historical candidates or rejects, but are never labelled causal
  forward evidence. Post-outcome stop-quality analysis is diagnostic only.
- Added a Research Autopilot panel under Intelligence → Strategy Ideas with
  capacity, lifecycle, assignment, blockage, effectiveness, approval,
  promotion, reconciliation, and rollback controls.

**Verification:**

- Final full local suite: `111/111` tests passed, including `9/9`
  orchestrator-focused tests. Python compile, inline JavaScript parse, and
  `git diff --check` passed.
- API smoke checks passed for Research Orchestrator, Learning Effectiveness,
  and research shadow-result endpoints.
- Desktop and `390×844` browser acceptance passed for the new panel with no
  console errors and no horizontal overflow.
- Production file hashes match the tested local backend, dashboard, and
  orchestrator module. `matrix-trader` and `mt-learner` are active, and
  production SQLite integrity is `ok`.
- The first production observation-only reconciliation prepared four typed
  contracts and activated none:
  - `research_funding_crowding_filter` and
    `research_order_flow_confirmation` are awaiting explicit Paper approval.
  - `research_sentiment_macro_regime` and
    `research_volatility_stop_quality` remain `needs_data`.
  - Behavioral-active and live-behavior-active counts are both zero.
- Production rollback snapshot:
  `/opt/matrix-trader/backups/20260730T184241Z-research-orchestrator`.
  It is 448 MB and contains the prior backend, dashboard, and a verified
  consistent SQLite backup.
- No live authority, leverage escalation, order placement, or automatic
  experiment activation was introduced.

---

## 2026-07-29 — Trader-comprehension and evidence-state sprint

**Built, tested, and deployed to production `207.148.66.39`:**

- Added one reusable, tap-friendly explanation system for MT7 metrics and
  research concepts. Help disclosures now explain:
  - what the item means,
  - why a trader should care,
  - how to read the value,
  - and what the value cannot prove.
- Reworked primary language across Signals, Market, Tools, Strategies,
  History, Intelligence, Research, Paper, and Assisted Live.
  - Internal shorthand is no longer the primary label for key decisions:
    `W+P`, `EV`, `PF`, `P12`, `shadow`, `gate`, and `cohort` are translated
    into phrases such as profitable-or-partial rate, average result after
    costs, profit factor, research-only, readiness requirement, and current
    Paper trial.
  - Technical identifiers remain visible only where they are useful for
    super-user auditing.
- Standardized evidence states so MT7 visibly distinguishes:
  - measured values,
  - collecting/waiting samples,
  - unavailable data,
  - stale data,
  - review items,
  - blocked items,
  - and safety locks.
  Zero is no longer used as a substitute for “no sample.”
- Clarified the purpose and limits of the densest workspaces:
  - Hermes is an independent system audit, not a signal generator.
  - Edge Lab is historical/research evidence with no trading authority.
  - Research sources must move through review, research-only measurement,
    Paper validation, and explicit approval.
  - Assisted Live is a readiness checklist, not an order screen or a
    recommendation to trade.
- Rebuilt Paper’s current-trial review into trader-facing evidence:
  closed trades collected, profitable-or-partial rate, average result after
  costs, recent-window consistency, outlier dependence, drawdown, and the
  change from earlier trades.
- Fixed the Edge Lab coverage ambiguity end to end.
  - Watchdog and Paper now use the same denominator: closed trades in the
    current Paper trial.
  - `0/0` is `Collecting`, not `0%` quality.
  - Open and pending Paper trades cannot dilute the displayed coverage rate.
  - Production currently reports `100.0% · 1/1` closed current-trial trades
    matched to a usable pre-entry Edge state.
- Preserved super-user model choice and all existing execution controls. No
  strategy rule, conviction formula, leverage, position size, account
  exposure, live setting, or order authority changed in this sprint.

**Verification and preservation:**

- Full local suite: `102/102` tests passed. Python compile, inline JavaScript
  parse, and `git diff --check` passed.
- Desktop and `390×844` browser acceptance passed across all primary tabs,
  including Research, Hermes, Edge Lab, Paper, and Assisted Live.
  - Document width matched viewport width on every tested screen.
  - Tap-to-open metric explanations worked on mobile.
  - Production browser console returned zero errors.
- Production Watchdog is `review · 0 fail / 1 warn`; the only warning is
  Assisted-Live readiness, which correctly remains blocked by current-trial
  evidence.
- Both `matrix-trader` and `mt-learner` are active. Production file hashes
  match the tested local files.
- Production SQLite integrity is `ok`: `3,983` signals and `2,721` Paper rows
  (`305 closed`, `30 entry_expired`, `2 expired`, `2,382 flow_rejected`,
  `1 open`, `1 pending`). Counts were identical before and after restart.
- Server rollback snapshot:
  `/opt/matrix-trader/backups/20260730T034110Z-trader-comprehension`.
  It is `401 MB` and contains the prior application, dashboard, a consistent
  full SQLite backup, pre-deploy statistics, and SHA-256 hashes.
- The recent full off-server disaster-recovery copy remains in the private
  `bnortey/mt7-production-backups` repository. This sprint did not rewrite the
  database or Edge Lab store, so no additional multi-gigabyte Git LFS upload
  was necessary.

**Sprint status:** complete. MT7 now exposes advanced evidence without forcing
the trader to decode implementation vocabulary or mistake missing data for a
measured zero.

---

## 2026-07-29 — Production dashboard reliability and measured-regime fix

**Built, tested, and deployed to production `207.148.66.39`:**

- Replaced the dashboard's normal all-strategy scan call with
  `POST /api/scan/strategy`, which scans only the selected exchange and
  strategy. The deliberate broad-scan route still exists.
  - The old normal path took `112.84s` in production.
  - Browser acceptance completed a MEXC Balanced scan in `5.8s`; a separate
    production request completed in `9.08s` across `1,043` contracts.
  - Empty valid results now explain that no signals met the selected threshold
    instead of presenting a failed-fetch retry state.
- Fixed Missed Mover Autopsy first-load behavior.
  - The panel now shows an immediate loading state and an explicit retry error.
  - A successful autopsy is retained even if auxiliary radar/evaluation calls
    fail.
  - Expensive kline evidence is collected only for the ranked display cohort
    and in bounded parallel workers instead of serially for every large mover.
  - Production latency fell from roughly `53s` to `13.0s`; the rendered panel
    showed 38 candidates and 12 ranked shadow rows during acceptance.
- Fixed Ops Watchdog's permanent loading state.
  - Edge Lab cohort coverage is refreshed in a thread-safe 15-minute
    background cache, so the first health request can return a truthful
    warming state without opening the roughly 20 GB Edge Lab database on the
    request path.
  - Watchdog now has its own 30-second frontend timeout, visible failure
    message, and retry control.
  - Production returned in `2.54s`; desktop and mobile showed
    `REVIEW · 0 fail / 2 warn`.
- Upgraded Cipher's market-regime measurement to
  `cipher-v11-measured-market-regime`.
  - The daily report now classifies the broad ticker snapshot using breadth,
    median move, market-wide expansion, and extreme-funding participation.
  - Sparse agent-regime coverage and missing agent fields can no longer turn
    the whole market into `unknown`.
  - MT7 still abstains explicitly when both the broad market and classified
    signal samples are genuinely too small.
  - Production classified the current `1,043`-contract market as `choppy`,
    medium confidence, mixed directional bias, with the report evidence
    visible in a new Measured Market Regime section.
- Added batch Research PDF ingestion.
  - The upload dialog accepts up to 20 PDFs at once, with common tags and MT7
    fields, per-file results, and partial-success errors.
  - Limits are 25 MB per PDF and 250 MB per batch.
  - The legacy single-file `file` field and single-file title override remain
    compatible.
- Fixed Intelligence mobile containment. The page itself no longer shifts
  horizontally when selecting right-side tabs; the tab strip scrolls
  independently while report and Watchdog content remain inside the viewport.

**Verification and preservation:**

- Full local suite: `101/101` tests passed. Python compile, inline JavaScript
  parse, and `git diff --check` passed.
- Desktop and 390 × 844 mobile browser acceptance passed for selected-strategy
  scans, Missed Mover first-load/render, Ops Watchdog, Cipher's measured
  regime, and the batch PDF dialog. The mobile document width remained exactly
  390 px.
- No strategy rules, leverage, position size, risk gates, live settings, or
  trading database schema were changed.
- Production SQLite integrity is `ok`: `3,970` signals and `2,719` Paper rows
  (`304 closed`, `30 entry_expired`, `2 expired`, `2,381 flow_rejected`,
  `1 open`, `1 pending`). Both `matrix-trader` and `mt-learner` are active.
- Server rollback snapshot:
  `/opt/matrix-trader/backups/20260730T020000Z-dashboard-reliability`.
  It is 467 MB and contains the pre-deploy application files, consistent
  database, verified compressed database, hashes, and pre-deploy statistics.

---

## 2026-07-29 — Report intelligence closeout

**Built and deployed to production `207.148.66.39`:**

- Upgraded Cipher daily and weekly reports to
  `cipher-v10-decision-intelligence`.
  - The Desk Verdict now includes current-regime strategy fit when the
    evidence passes its sample gate; unknown regimes do not manufacture a
    regime-specific fit claim.
  - Added a true Cross-Desk Debate with conditional upside and downside cases,
    a resolution, confirmation evidence, invalidation evidence, and an
    advisory-only safety statement.
  - The existing accountability, evidence freshness, data-limit, and
    exposure-change disclosures remain visible.
- Upgraded trade coaching to `coach-v2-evidence`.
  - Every eligible resolved review now has a structured packet containing the
    trade snapshot, verdict, primary path diagnosis, MAE/MFE/capture/stop
    pressure, funding alignment, what held up, what failed, a specific
    next-trade rule, evidence, quality metadata, limitations, and explicit
    advisory-only authority.
  - Provider reasoning such as `<think>...</think>` is sanitized before it can
    reach the trader. AI failure falls back to a deterministic evidence-based
    review instead of leaving an empty or vague result.
  - Legacy reviews migrate locally without paid-provider calls. Regeneration
    clears the old packet and version so the complete contract is rebuilt.
- Upgraded Hermes to `hermes_metrics_v3_coach_intelligence`.
  - Coach themes now use structured path diagnoses instead of loose keyword
    matching.
  - Hermes reports diagnosis recurrence, strategy × diagnosis
    concentrations, structured/path/action-rule coverage, hidden historical
    reasoning contamination, and exact repeated-sentence counts.
  - Added a Coach Intelligence Audit panel plus recent evidence-backed review
    drill-down.
  - Added a five-minute audit cache so repeated daily, weekly, and Hermes views
    do not repeatedly rescan the full review corpus. Explicit refreshes and
    data-changing actions still rebuild or invalidate it.
- Upgraded the learner coach analyst to
  `coach-pattern-v2-structured`. Its nine eligible production briefs now use
  structured diagnoses and path evidence with a 20% recurrence floor.
  Suggestions remain shadow-only and have no leverage, sizing, risk, scoring,
  filter, or execution authority.
- Upgraded the History and pair-workspace review views to render the
  structured evidence packet instead of presenting only a prose block.

**Production migration and evidence quality:**

- Migrated `3,633/3,633` eligible production reviews to
  `coach-v2-evidence`.
- Removed reasoning artifacts from `1,242` historical outputs; 52
  reasoning-only outputs were replaced with deterministic evidence-based
  reviews.
- Visible production review state after migration:
  - `0` residual `<think>` blocks,
  - `0` empty reviews,
  - `100.0%` structured coverage,
  - `100.0%` actionable-rule coverage,
  - `97.1%` path-evidence coverage,
  - seven exact repeated sentences, representing `1.0%` recurrence and an
    acceptable quality status.
- Rebuilt all nine eligible coach-pattern research briefs deterministically;
  all nine use the v2 contract and all nine retain `risk_authority=none`.

**Safety, performance, and verification:**

- Reports, reviews, and pattern briefs remain advisory. No report can create a
  trade or directly change scoring, filters, stops, leverage, position size,
  account exposure, or execution.
- Any exposure-increasing idea must show the old and proposed size, leverage,
  liquidation distance, and drawdown impact and still requires explicit user
  approval.
- Full local suite: `94/94` tests passed. Python compile, inline JavaScript
  parse, and `git diff --check` also passed.
- The full Hermes audit rebuild measured `10.47s`; a warm cached request
  measured `0.24s`. The five-minute invalidation-aware cache keeps that heavy
  reconstruction off the normal navigation path.
- Desktop and 390 × 844 mobile browser acceptance passed for Cipher, Hermes,
  and the structured History coach-review view. There was no document-level
  horizontal overflow and no browser warning or error.
- Production migration preserved trading state exactly: `3,947` signals and
  `2,714` Paper rows (`304 closed`, `30 entry_expired`, `2 expired`,
  `2,377 flow_rejected`, `1 pending`). SQLite integrity is `ok`; both
  `matrix-trader` and `mt-learner` are active.
- Server rollback snapshot:
  `/opt/matrix-trader/backups/20260729T205700Z-report-closeout`.
  It contains the pre-deploy application files, consistent `signals.db`,
  verified compressed database, pre-deploy statistics and hashes, plus the
  pre-refresh coach brief corpus.
- Private GitHub backup commit:
  `e19f179ba83656ab071c396f52ab86e95251df3b`. A clean VPS restore
  verification matched the remote ref, passed all SHA-256 checks, and passed
  `zstd -t` for the database and reporting-code archives.

---

## 2026-07-29 — Causal learning and strategy-factory sprint

**Built and deployed to production `207.148.66.39`:**

- Added the `mt7_learning_v1` causal-learning contract. MT7 now separates
  pattern discovery from causal forward evidence instead of treating mixed
  historical policies as proof that a suggestion worked.
- Every newly created Paper row records:
  - the full effective strategy/Paper/research policy snapshot,
  - a canonical SHA-256 policy fingerprint,
  - the learning experiment ID when the row belongs to an active applied
    trial.
- Added `learning_experiments` and `learning_experiment_events`. Experiment
  events are append-only and SHA-256 hash-chained, so mutation, sequence gaps,
  and broken ancestry can be detected.
- Applied learner suggestions now register an immutable, forward-only Paper
  experiment after explicit approval. The experiment predeclares its
  hypothesis, mechanism, scope, exact change set, baseline, minimum sample,
  minimum duration, promotion gates, falsification gates, and rollback plan.
- Active applied experiments are serial per strategy. Parallel research and
  suggestion shadow studies remain read-only; overlapping causal policy
  windows are rejected.
- Mature experiments are evaluated using net P&L, win+partial rate, profit
  factor, trimmed expectancy, leave-best-out expectancy, dollar P&L, and
  drawdown relative to the immediately preceding same-strategy control
  window.
- The evaluator automatically classifies evidence as `collecting`, `review`,
  `falsified`, or `promotion_candidate`. It does **not** change strategy
  configuration. A falsified trial exposes an explicit rollback route that
  requires a user reason and only reverts the suggestion's exact owned
  control.
- Added the controlled strategy-factory contract. A strategy idea must now
  declare entry rules, exit rules, mechanism, failure regimes, data
  requirements, cost assumptions, control strategy, novelty claim,
  falsification criteria, and promotion criteria. Leverage, risk, sizing,
  execution, and live fields are rejected from factory payloads.
- Upgraded the deterministic mt-learner research builders and re-evaluation
  path. Production now reports `21/21` current strategy candidates with valid
  shadow-only experiment contracts. The former order-flow filter proposal is
  correctly excluded from the strategy factory and remains in research
  governance.
- Added:
  - `GET /api/intelligence/learning-effectiveness`
  - `POST /api/intelligence/learning/evaluate`
  - `POST /api/intelligence/learning/experiments/<id>/rollback`
  - `POST /api/intelligence/learning/strategy-factory/validate`
- Added a compact Causal Learning Ledger to Intelligence → Suggestions. It
  shows architecture maturity separately from empirical proof, exact-policy
  attribution, experiment state, factory-contract coverage, integrity, active
  bottlenecks, and immutable safety posture.

**Current honest maturity:**

- Architecture: `10.0/10`.
- Empirical forward proof: `0.0/10`.
- Evidence-weighted overall: `3.5/10`.
- This low evidence score is intentional and correct. All 2,714 existing Paper
  rows predate exact policy attribution and remain useful for discovery but
  are excluded from causal claims. The next explicitly approved applied
  suggestion starts exact-policy collection automatically; default maturity
  requires at least 50 closed trades and seven elapsed days.

**Safety:**

- Auto-apply: off.
- Auto-promotion: off.
- Automatic leverage increase: off.
- Automatic position-size increase: off.
- Research-to-live behavior changes: off.
- Falsification-to-config mutation: off; rollback remains explicit.
- Security/operational hardening is the next sprint, intentionally sequenced
  after this learning sprint per user direction.

**Verification and backups:**

- Full local discovery: `87/87` tests passed.
- Python compile, inline JavaScript parse, SQL placeholder/column parity, and
  `git diff --check` passed.
- Local browser acceptance passed with the complete learning panel and no UI
  collapse. Production browser acceptance shows architecture `10.0/10`,
  factory contracts `21/21`, learner online, no authority conflicts, and the
  two honest evidence bottlenecks.
- Production hashes match local. `matrix-trader` and `mt-learner` are active,
  post-deploy logs contain no traceback/exception/error, and SQLite integrity
  is `ok`.
- Paper state survived exactly: 2,714 rows (`304 closed`, `30 entry_expired`,
  `2 expired`, `2,377 flow_rejected`, `1 pending`), 3,947 signals, and
  `$741.32` recorded closed Paper P&L.
- Server rollback snapshot:
  `/opt/matrix-trader/backups/20260729T202152Z-learning-intelligence`
  (application files plus consistent pre-migration `signals.db` and verified
  compressed copy).
- Private GitHub backup commit:
  `8afe8e1966ea8e4b993fc970aaadf52ef3d95e8a`. A clean VPS clone downloaded
  only the new LFS object and matched SHA-256
  `dbef59b562910110373060d10bfbfcece8e36a2d9b9da93d51408520be1fe98d`.
  Temporary clone/staging directories were removed after verification.

---

## 2026-07-29 — Edge Lab v2 production migration and performance hardening

**Built and deployed to production `207.148.66.39`:**

- Completed the Edge Lab roadmap across path truth, factor credibility,
  strategy conditioning, challenger modeling, UI, bounded migration, and
  storage safety.
- Path labels are versioned as `edge_path_v2` and now retain actual exit type,
  exit timing, exit-bounded MFE/MAE, gross realized path return, 24-hour
  horizon return, and explicit ambiguity bounds.
- Factor analysis is version-gated and now uses:
  - current-run dynamic baselines,
  - fee/slippage-adjusted net expectancy without inventing historical funding,
  - symbol-day effective sample size,
  - discovery/confirmation time splits,
  - Benjamini-Hochberg multiple-testing control,
  - ambiguity and symbol-concentration warnings,
  - paired v2 migration coverage,
  - an explicit 24-hour Edge vs 84-hour MT7 outcome-window warning.
- Added strategy-conditioned Paper validation and rejected-candidate
  counterfactuals for every instrumented strategy gate. Edge Lab is no longer
  funding-arbitrage-specific.
- Added filter-only suggestion drafts. Every draft states that leverage,
  position size, and account exposure remain unchanged; registration is
  measurement-only and never mutates scoring or strategy configuration.
- Added the grouped-hour, leverage-normalized net-utility v2 meta-labeler
  challenger while leaving the frozen v1 contract untouched. The first
  production challenger run used 158 exact Paper snapshots, completed three
  grouped walk-forward folds, and passed 6/7 research checks. It failed to
  beat the temporal RMSE baseline, so `authority_eligible=false` remains
  correct.
- Added `edge_lab_upgrade.py`, which rebuilds five stale symbols per scheduled
  run instead of rewriting the 20 GB research database in one blocking job.
- Added `edge_lab_maintenance.py`; it is dry-run by default, requires
  `--backup-confirmed` to prune, and only removes source JSON when the matching
  v2 projection is verified.

**Production migration and observed performance:**

- Pre-deploy rollback snapshot:
  `/opt/matrix-trader/backups/20260729T183804Z-edge-lab-v2`.
  - Full compressed Edge DB: `1.2 GB`.
  - Consistent compressed `signals.db`: `66 MB`.
  - Uncompressed consistent `signals.db` retained server-side.
  - All compressed assets passed `zstd -t`; all 40 snapshot files passed
    SHA-256 verification.
- Off-server disaster recovery is now live in the separate private repository
  `https://github.com/bnortey/mt7-production-backups`.
  - Sanitized backup commit:
    `f14f23f026c0fc6f05a259eed6a9ce19a1c39b9c`.
  - Git LFS stores `edge_lab.db.zst` (`1.2 GB`), `signals.db.zst`
    (`66 MB`), and `rollback-metadata.tar.zst`; `.env`, exchange/AI
    credentials, wallet keys, and all SSH private keys are excluded.
  - A repository-scoped VPS deploy key has read/write access only to this
    private backup repository.
  - Verification used a clean clone back onto the VPS. All three files passed
    the committed SHA-256 manifest, `git lfs fsck` passed, and the remote
    `main` ref matched the local backup commit. The temporary verification
    clone was then removed; the GitHub copy and server-local rollback snapshot
    remain.
  - The duplicate `github-upload` staging repository was removed after remote
    verification, reclaiming `2.5 GB`; the retained server rollback directory
    is `1.6 GB`.
- The first bounded rebuild migrated five symbols and 42,125 closed v2 paths
  in 87.52 seconds with zero failures.
- A production profiler pass found and fixed three migration/report
  performance faults before leaving the sprint:
  1. The v2 materializer would have rewritten all 4.36 million legacy rows.
     It now projects only paired current-version rows.
  2. Version predicates used `COALESCE`, preventing the new composite source
     index from being used. Direct predicates and
     `idx_candle_labels_versions_id` reduce no-op materialization to 3.53
     seconds.
  3. Concentration, coverage, symbol-count, and fingerprint bookkeeping
     scanned the legacy population. They now use the paired-v2 boundary and
     indexed counts.
- Production factor generation fell from roughly 90 seconds wall time during
  the first v2 run to 17.97 seconds; measured analysis fell from 101.0 to 12.4
  seconds and concentration from 73.81 to 1.81 seconds.
- Current factor scope is intentionally only five symbols / 42,125 paired-v2
  rows (about 1.6% coverage). Every generic factor remains `research_only`
  during migration regardless of apparent edge.
- After the remote restore test passed, audited maintenance removed `39,260`
  old `candle_labels` source rows whose current v2 projections were verified.
  The source-label population moved from `4,362,764` to `4,323,504`; a
  follow-up dry run found zero eligible rows. No feature projections, Paper
  trades, signals, configs, or live behavior were removed.
- No `VACUUM` was run. SQLite retained `326,684,672` bytes of internal reusable
  pages, avoiding a blocking 20 GB database rewrite while allowing later
  writes to reuse the space.
- The post-maintenance factor report rebuilt successfully with 42,125
  paired-v2 rows in 12.18 seconds. The v2 challenger still passes 6/7 checks
  but fails temporal-baseline RMSE, so it remains shadow-only.
- Edge Lab daily and weekly timers are active again. The daily run is scheduled
  for `2026-07-30 03:45 UTC`; the weekly run for `2026-08-02 03:29 UTC`.

**Safety and acceptance:**

- Paper data survived the restart exactly: 2,714 rows before and after
  deployment (`304 closed`, `30 entry_expired`, `2 expired`,
  `2,377 flow_rejected`, `1 pending`); signal count remained `3,947`.
- Paper data also survived offsite backup and retention maintenance unchanged:
  `304 closed`, `30 entry_expired`, `2 expired`, `2,377 flow_rejected`,
  `1 pending`, `0 open`, and `$741.31` recorded closed P&L.
- `matrix-trader` and `mt-learner` are active; production logs contain no new
  traceback, exception, or application error.
- Production APIs report `edge_factor_v2`, 42,125 eligible rows, five eligible
  symbols, one review-only draft, and both strategy validation and v2
  meta-labeling with `authority_eligible=false`.
- Browser acceptance passed on desktop and `390×844` mobile with no document
  overflow and no console warnings/errors.
- Final local regression: 80/80 tests passed, plus Python compile, shell syntax,
  and `git diff --check`.
- `git-lfs` was installed on the VPS solely so the private backup can be pushed
  directly from the server without using Mac storage. The sanitized upload
  staging directory excludes `.env`, exchange/AI credentials, wallet keys, and
  SSH private keys.

**Ongoing guardrails:**

- Keep `bnortey/mt7-production-backups` private. Never put `signals.db`,
  `edge_lab.db`, their compressed copies, credentials, or keys in the public
  `bnortey/Matrix_Trader7.0` repository.
- Do not run `VACUUM` on the 20 GB Edge Lab database during active service
  without a new verified backup, a maintenance window, and sufficient
  temporary disk space.
- Generic factor states remain `research_only` during bounded v2 migration.
  No suggestion, factor, or meta-label output may raise leverage, position
  size, or account exposure automatically.

---

## 2026-07-29 — Cipher accountable report-intelligence sprint

**Built and deployed to production `207.148.66.39`:**

- Upgraded the report contract to `cipher-v9-accountable-intelligence`; v8 report caches rebuild from persisted evidence.
- Completed the seven-part report-intelligence roadmap:
  1. Added a bounded exact-sentence repetition baseline against up to 15 prior reports, with a visible 30% review threshold.
  2. Split weekly reporting into a genuinely weekly synthesis contract. It now analyzes daily progression, active coverage days, regime persistence/rotation, weekly gating, measured breadth/context coverage, next-week confirmation tests, and scheduled-event risk instead of reusing daily prose.
  3. Added specialty-aware focus selection. Funding chooses the largest unresolved carry imbalance, microstructure chooses the strongest retained flow/book divergence, cross-venue chooses the largest identity-safe liquidity-qualified dislocation, technicals prefer liquid movers, and catalyst/tokenomics/social leads use their own quality contracts.
  4. Added a compact `Desk Verdict` with posture, evidence, invalidation, authority, and an explicit exposure-risk disclosure.
  5. Replaced false-neutral fallbacks with evidence-aware abstention. Missing data no longer prints fabricated `0.00%` movers, a one-checkpoint BTC return, or asks traders to confirm that an `unknown` regime remains dominant.
  6. Added a structured forward report-claims ledger. Claims are written before their observation window, the first claim is immutable across report regeneration, mature claims are resolved by the existing 15-minute outcome loop, unscorable claims stay visible, and the ledger is permanently `descriptive_only`.
  7. Added paragraph-level trust labels distinguishing deterministic interpretation from AI-polished evidence-grounded language, with the measured evidence families listed beside each narrative.
- Added `GET /api/intelligence/report-claims`; it is read-only and has no scoring, strategy, risk, sizing, leverage, or execution authority.
- Agent profile reports now show why each specialty focus was selected, the focus symbol when applicable, evidence quality, trader use, invalidation, and coverage limits.
- Added a mobile-safe `Report Accountability` section showing repetition status, pending/resolved/unscorable claim totals, open forward claims by daily/weekly scope, and per-analyst resolved accuracy.
- All new calculations use persisted/cached data. Report generation still performs no request-time exchange, social, catalyst, tokenomics, or hosted-AI fetch unless the user explicitly presses Regenerate for editorial polish.

**Verification:**

- `tests.test_cipher_report_depth`: `21/21` passing, including weekly/daily distinctness, role-aware focus, abstention behavior, immutable forward claims, and trust labels.
- Full discovery: `71/72` passing. The only failure remains the pre-existing duplicate suggestion-ID sequencing test (`test_duplicate_suggestion_ids_are_repaired_and_future_ids_do_not_collide`), outside this sprint.
- Python compile, inline JavaScript parse, and `git diff --check` passed.
- Local deterministic generation measured about `74ms` daily and `105ms` weekly.
- Production uncached generation measured `1.680s` daily and `1.700s` weekly. Current production exact-sentence overlap is `20.0%` versus prior daily reports and `0.0%` between the current weekly synthesis and paired daily.
- Production file hashes match local. `matrix-trader` is active, the report-claims API returns `descriptive_only`, and post-restart service logs contain no application errors.
- Browser acceptance passed on production:
  - daily and weekly show schema v9, Desk Verdict, trust labels, and Report Accountability,
  - weekly unknown-regime coverage correctly withholds regime-specific preference,
  - Priya’s profile selects HYPE from measured float/FDV risk and displays distinct evidence/trader-use/invalidation text,
  - desktop and `390×844` mobile have no document-level horizontal overflow,
  - browser console contains no warnings or errors.
- Production rollback bundle: `/opt/matrix-trader/backups/20260729T053656Z-report-intelligence-v9` (prior app, dashboard, and full SQLite database).

**Sprint status:** complete. Reports are richer and more accountable without adding latency to scans or granting narrative/claim accuracy any authority over entries, conviction, exposure, or execution.

---

## 2026-07-29 — Cipher report-enrichment sprint completion

**Built and deployed to production `207.148.66.39`:**

- Upgraded the report contract to `cipher-v8-evidence-audit`.
- Completed Hari Stern’s direct social-intelligence phase with an asynchronous 30-minute collector:
  - uses Bluesky’s public AppView search contract with a bounded non-authenticated host fallback,
  - samples no more than eight reviewed market/asset topics and 50 recent English posts per topic,
  - persists aggregate activity rather than full profiles,
  - measures post rate, unique authors, top-author concentration, duplicate-language share, engagement per post, and change versus MT7’s own trailing seven-day median,
  - labels thin, concentrated, baseline-collecting, and single-source samples explicitly,
  - never converts engagement or activity into bullish/bearish sentiment, conviction, leverage, position size, or execution.
- Replaced Hari’s funding-only proxy report with a distinct evidence-led brief and a full `Social Attention & Credibility` daily/weekly section. It states what was sampled, the evidence-quality flags, the trader use, the invalidation test, and the Bluesky-only limitation.
- Added `GET /api/intelligence/social-evidence`; it is read-only and never performs request-time social calls.
- Expanded the existing asynchronous official catalyst collector with:
  - crypto-filtered U.S. SEC and CFTC press-release feeds,
  - Ethereum Foundation protocol/security publications,
  - reviewed Aave, Arbitrum, and Optimism governance forums,
  - Solana’s official protocol status feed.
- Every new catalyst retains an authority scope and symbol-resolution contract. Governance discussion is not described as passage or implementation; regulator items remain market-wide unless the source proves an asset mapping.
- Added a structured on-chain capability boundary to Priya’s tokenomics packet:
  - direct holder concentration and labeled flows remain unavailable,
  - MT7 now exposes the exact prerequisites for safe coverage: reviewed chain contracts, exclusions for vesting/treasury/bridge/burn/exchange addresses, historical snapshots, label provenance, and bridge/internal-transfer de-duplication,
  - same-symbol and top-wallet queries are explicitly rejected as cross-chain identity evidence.
- Added a forward-only `Report Evidence Outcome Audit`:
  - evidence must exist before a signal timestamp,
  - resolved cohorts compare social, official-catalyst, and tokenomics-risk context with their no-evidence controls,
  - retained daily ≥8% movers show evidence coverage, signal coverage, and missed-mover counts,
  - the audit is observational, always returns `scoring_eligible: false`, and cannot mutate signals, sizing, leverage, or execution,
  - the cohort join runs in the background after the social cycle and is stored as a compact snapshot; reports and the API only read that cache,
  - exposed at `GET /api/intelligence/report-evidence-evaluation`.
- Added compact, mobile-safe report UI for social examples/quality, outcome cohorts, catalyst authority labels, and the on-chain capability boundary. All network collectors remain asynchronous, cached, bounded, and outside scan/report request paths.
- Added environment-template controls for social/catalyst/tokenomics cadence and retention.
- Fixed a production acceptance defect in the legacy Intelligence workspace: the broad status and suggestion summaries can take more than eight seconds while SQLite is serving the other panels. Their bounded client deadline is now 20 seconds, so that work no longer collapses the entire tab into a generic load failure.

**Local verification before deployment:**

- Live governed catalyst collection stored `128` retained events across exchange, macro, protocol, governance, regulator, and status source families. Local Bybit announcement access returned a region/WAF `403`; the collector recorded the source error and completed without blocking other feeds.
- Live social collection completed in `900ms`, stored five topic snapshots from `181` recent posts, and returned zero source errors.
- Cached endpoint latency:
  - social evidence `4.7ms`,
  - catalyst evidence `3.9ms`,
  - report-evidence evaluation `4.0ms` from the background snapshot cache.
- Uncached local current daily report built in `86.7ms` after evidence collection.
- Python compile and inline JavaScript parse passed.
- `tests.test_cipher_report_depth`: `16/16` passing.
- Full discovery: `66/67` passing. The only failure remains the pre-existing duplicate suggestion-ID sequencing test (`test_duplicate_suggestion_ids_are_repaired_and_future_ids_do_not_collide`), which is outside this report-enrichment sprint.

**Production verification:**

- The governed catalyst refresh retained `178` events (`92` new) with zero source errors in `5.017s`. Reviewed raw source counts were MEXC `4`, Bybit `50`, Hyperliquid `60`, Federal Reserve monetary releases `15`, FOMC dates `25`, Ethereum Foundation `18`, Aave governance `30`, Arbitrum governance `30`, Optimism governance `30`, and Solana status `30`. The currently fetched SEC/CFTC crypto-filtered sets contained zero qualifying releases and were reported as empty—not as collector failures.
- The direct social refresh stored five topic snapshots from `181` posts with zero source errors in `1.876s`.
- The background evidence cohort calculation completed in `2.502s`; its cached API returned in `14.4ms`.
- Current daily report latency was `1.165s` uncached and `39.5ms` cached. The rendered report identifies schema `cipher-v8-evidence-audit`, Hari as `direct_activity_cached`, the social sample as `usable_single_source`, and the outcome audit as `descriptive_only / background_snapshot / scoring false`.
- Browser acceptance passed on the deployed UI:
  - daily and weekly reports render the enriched written outlook, scenarios, social evidence, official catalysts, tokenomics boundary, and forward outcome audit,
  - Hari, Priya, and Daria expose distinct specialty reads, evidence used, trader use, invalidation, and limitations,
  - desktop and `390×844` mobile have no document-level horizontal overflow; wide evidence tables scroll inside their cards,
  - the browser console contains no warnings or errors.
- Python compile, inline JavaScript parse, service health, cached-endpoint checks, and source collection checks passed.
- Production backup: `/opt/matrix-trader/backups/20260729T035533Z-cipher-evidence-audit`.

**Sprint status:** all five outstanding report-roadmap items are implemented. Direct cross-chain holder/wallet flows are resolved as an explicit capability boundary—not silently treated as delivered—and report evidence remains descriptive until future forward samples justify a separate scoring experiment.

---

## 2026-07-29 — Cipher tokenomics and supply-risk evidence phase

**Built and deployed to production `207.148.66.39`:**

- Upgraded the report contract to `cipher-v7-tokenomics-evidence`; earlier report caches rebuild from persisted evidence.
- Added a low-frequency asynchronous tokenomics collector (six-hour cadence, staggered startup). Scan, report, and agent-profile requests never wait on these networks.
- Added free, explicitly attributed evidence:
  - CoinGecko market snapshots for current price, market cap, FDV, circulating/total/max supply, float percentage, volume, and source timestamp.
  - DefiLlama public-page observations for next disclosed unlock groups and treasury composition.
- Added bounded SQLite stores:
  - `tokenomics_asset_snapshots` retains 180 days of reviewed supply/valuation observations,
  - `token_unlock_events` stores deduplicated next-event schedules,
  - `token_treasury_snapshots` stores one observation per project/day,
  - `tokenomics_collection_runs` retains 30 days of source counts, errors, latency, and coverage.
- Token identity is conservative:
  - CoinGecko evidence can attach to an MT7 pair only through a reviewed ID map.
  - Same-symbol aggregator matches are not assumed to identify the MEXC contract.
  - Unverified unlock/treasury rows remain stored for audit but cannot become focus-asset conclusions.
- Linear emissions are labeled as rate changes. MT7 does not convert them into fake one-time token amounts, percent-of-float cliffs, or dollar unlocks.
- Added `GET /api/intelligence/tokenomics-evidence`, a read-only cached endpoint with no request-time source fetch.
- Priya now writes an evidence-led specialty brief containing:
  - reported circulating float and FDV/market-cap pressure,
  - mapped next-cliff timing, percent of circulating supply, cached value, and category when available,
  - mapped treasury value/composition when available,
  - trader use, invalidation, source freshness, mapping quality, and explicit coverage limits.
- Daily and weekly deterministic reports now include an 80+ word `Tokenomics & Supply Risk` note plus compact supply, unlock, and treasury tables.
- The action matrix can flag focus-asset low-float/FDV or near-term cliff risk. Any resulting exposure recommendation is limited to review, tighter admission, or an explicit reduction; tokenomics evidence can never justify higher leverage or a larger position.
- Direct holder concentration, labeled wallet transfers, treasury transfers, and exchange-flow data remain explicitly unavailable. Market data and treasury composition are not described as whale activity or sale intent.
- Fixed Priya’s profile modal on mobile: its report/evidence grid now collapses to one readable column instead of compressing the written analysis.
- No tokenomics observation can create a signal, alter conviction, change sizing/leverage automatically, or execute a trade.

**Production verification:**

- Live source collection completed in `1.552s` with zero source errors:
  - `26` reviewed CoinGecko asset snapshots,
  - `356` raw DefiLlama unlock projects yielding `255` deduplicated next-event records,
  - `408` treasury observations.
- Current reviewed schedule coverage includes six upcoming events in the report look-ahead. Example: ARB’s cached Aug. 15 cliff is labeled `1.36%` of circulating float and `watch`, while SOL’s linear row is shown as `rate change / not a cliff`.
- HYPE currently demonstrates the supply-risk contract: `22.2%` reported circulating float, `4.49×` FDV/market cap, `high` float-risk label.
- Cached tokenomics endpoint latency: `47ms`.
- Uncached current daily report latency: `1.377s`; cached daily latency: `54ms`; uncached weekly: `1.625s`.
- Current reports expose `aggregated_cached` Priya coverage, source errors, feed age, reviewed mapping status, and direct on-chain blind spots.
- Browser validation passed on the live production UI:
  - the report section renders the tokenomics narrative, coverage metrics, supply table, reviewed unlock table, and explicit missing-data labels,
  - desktop and `390×844` mobile viewports have no document-level horizontal overflow,
  - wide evidence tables scroll only inside their own containers,
  - Priya’s mobile modal is readable in one column and contains her measured read, evidence, trader use, invalidation, and limitations,
  - browser console has no warnings or errors.
- Python compile, inline JavaScript parse, collector/parser/report endpoint tests, file-hash verification, and service health passed.
- `tests.test_cipher_report_depth`: `13/13` passing.
- Full discovery: `63/64` passing. The only failure is the pre-existing duplicate suggestion-ID sequencing test (`test_duplicate_suggestion_ids_are_repaired_and_future_ids_do_not_collide`).
- Production backup: `/opt/matrix-trader/backups/20260729T033200Z-tokenomics-evidence`.

**Next report roadmap phase:**

1. Add direct social activity and source-credibility evidence for Hari without treating engagement volume as an entry signal.
2. Add protocol governance/security and selected regulator sources only after defining the same source-quality, timestamp, and symbol-resolution contracts.
3. Evaluate optional chain-specific holder and labeled-flow providers; do not claim cross-chain coverage until it is reliable, affordable, and identity-safe.
4. Measure whether tokenomics qualifiers improve missed-mover diagnosis and forward paper outcomes before any scoring experiment.
5. Keep all collectors asynchronous, cached, bounded, source-labeled, and outside scan/report execution paths.

---

## 2026-07-29 — Cipher primary-source catalyst intelligence phase

**Built and deployed to production `207.148.66.39`:**

- Upgraded the report contract to `cipher-v6-primary-catalysts`; prior report caches rebuild from persisted evidence rather than being relabeled.
- Added an asynchronous official-source collector that runs every 15 minutes, staggered after the existing snapshot workers. Report and scan requests never wait on these networks.
- Source families:
  - official MEXC Announcement Center,
  - official Bybit V5 Announcements API,
  - Hyperliquid Statuspage incidents and scheduled maintenance,
  - Federal Reserve monetary-policy RSS,
  - official FOMC meeting calendar.
- Added bounded SQLite stores:
  - `catalyst_events` retains 90 days plus future scheduled events,
  - `catalyst_collection_runs` retains 30 days of source counts, errors, latency, and new/stored totals.
- Every event stores its canonical primary-source URL, publication time, event/effective time, source-time quality, status, severity, deterministic event class, affected assets/venues, and a content hash. Publication and effective time are not conflated.
- Deterministic classes include listing, delisting, leverage/funding change, maintenance, venue incident, security, and monetary policy. They do not infer bullish/bearish direction.
- MEXC token extraction is constrained by the current MT7 symbol universe to avoid turning prose acronyms into fake asset matches.
- Added `GET /api/intelligence/catalyst-evidence`, a read-only cache endpoint with no request-time source fetch.
- Yasmin now reports the highest-priority official item, source, timing, event class, severity, affected assets, market-reaction test, trader use, and invalidation.
- Daria now separates the event the source proves from the market story/causality it does not prove, then requires breadth/flow/structure agreement.
- Daily and weekly deterministic narratives now include a 90+ word catalyst watch; the weekly report uses actual cached event evidence instead of learner briefs as its upcoming-event source.
- Added a restrained `Primary-Source Catalyst Radar` with source links, publication/effective timestamps, compact source-health metrics, and contextual help.
- Cross-desk blind spots now describe the bounded official source set accurately instead of claiming there is no primary catalyst coverage.
- AI report polish can edit the catalyst note, but the detailed deterministic version remains the free, fast baseline and cannot be collapsed into generic copy.
- Fixed a separate Intelligence-tab sluggishness defect discovered during browser validation: the optional missed-mover endpoint could hang indefinitely and block the entire workspace. Optional Intelligence requests now abort after five seconds and degrade gracefully; core status/suggestion requests have an eight-second ceiling.
- No catalyst item can create a signal, alter conviction, change sizing/leverage, or execute a trade.

**Production verification:**

- First official-source batch stored `86` retained events with no source errors:
  - MEXC `4` current announcement cards,
  - Bybit `50`,
  - Hyperliquid status/maintenance `60` raw items before retention filtering,
  - Federal Reserve monetary releases `15`,
  - FOMC meetings `25` parsed across bounded calendar years.
- First collection latency was `1.342s`; the autonomous follow-up completed in `3.031s` with zero errors and zero duplicate inserts.
- Cached catalyst endpoint latency: `14ms`.
- Uncached production report latency: daily `1.102s`, weekly `1.506s`; cached daily latency: `35ms`.
- Current daily report exposes three time-matched official source families, zero source errors, a 91-word catalyst watch, and `primary_source_cached` Yasmin/Daria coverage.
- Browser validation passed on the live production UI:
  - the Intelligence workspace no longer remains frozen on `Loading…` when missed-mover analysis stalls,
  - the radar renders official source links and separates publication/effective times,
  - Yasmin and Daria profile modals show the new evidence-rich specialty briefs,
  - a `390×844` viewport has no document-level horizontal overflow and the event cards stack cleanly.
- Python compile, inline JavaScript parse, all parser/collector/report endpoint tests, service health, and source-health checks passed.
- `tests.test_cipher_report_depth`: `12/12` passing.
- Full discovery: `62/63` passing. The only failure is the pre-existing duplicate suggestion-ID sequencing test (`test_duplicate_suggestion_ids_are_repaired_and_future_ids_do_not_collide`).
- Production backup: `/opt/matrix-trader/backups/20260729T025500Z-primary-catalysts`.

**Next report roadmap phase:**

1. Add supply, unlock, and treasury evidence for Priya using authoritative or clearly attributed sources. **Completed in `cipher-v7-tokenomics-evidence`; direct holder/wallet-flow coverage remains a deliberate gap.**
2. Add direct social activity plus source-credibility evidence for Hari without making engagement volume a trading signal.
3. Extend official catalyst coverage to protocol governance/security and selected regulators only after defining source-quality and symbol-resolution contracts.
4. Measure whether catalyst alignment improves missed-mover diagnosis and forward paper outcomes before allowing any scoring experiment.
5. Keep every collector asynchronous, cached, bounded, source-labeled, and outside scan/report execution paths.

---

## 2026-07-29 — Cipher synchronized cross-venue evidence phase

**Built and deployed to production `207.148.66.39`:**

- Upgraded report schema to `cipher-v5-cross-venue`, invalidating older cached report payloads.
- Added a background collector that fetches the complete public MEXC, Hyperliquid, and Bybit ticker universes concurrently every 15 minutes. It is independent of scans and report requests.
- Added bounded SQLite evidence tables:
  - `cross_venue_snapshots` retains seven days of synchronized normalized observations,
  - `cross_venue_collection_runs` retains health, latency, venue counts, match counts, and errors for 30 days.
- Exact-base matching remains anchored to MEXC. Bybit is context-only and was not added to the signal scanner or execution routes.
- Normalized comparable fields include price, mark/index, MEXC/Bybit top spread, USD turnover, venue-supported USD OI, 24h change, and funding converted to an 8-hour equivalent.
- Data-quality controls prevent false precision:
  - MEXC funding interval is labeled assumed,
  - MEXC ticker OI remains labeled native contract units and is never compared as USD,
  - Hyperliquid is labeled USDC/mark-price context,
  - exact-base matches wider than 500 bps are quarantined as token-identity/staleness conflicts,
  - an 8 bps USDC quote buffer is shown separately from every raw venue gap,
  - thin-liquidity comparisons are labeled.
- Ghost, Eric, and Kenny now receive measured venue coverage, price dispersion, normalized funding divergence, direction agreement, venue leadership (only after two observations), and supported USD OI movement.
- Deterministic Ghost narratives no longer claim that arbitrary venue gaps forecast the next move. Venue evidence is explicitly confirmation context, not an entry or arbitrage instruction.
- Added a compact cross-venue table to `Measured Market Evolution` and a read-only cached health/evidence endpoint: `GET /api/intelligence/cross-venue-evidence`.
- Current-day report caches now expire after 15 minutes and current-week caches after one hour. Historical reports remain immutable. Automatic refresh stays deterministic and does not invoke AI.

**Production verification:**

- Initial batch completed in `272ms` with no venue errors:
  - MEXC `913`,
  - Hyperliquid `177`,
  - Bybit USDT perpetuals `663`,
  - `524` MEXC-anchored assets on two or more venues,
  - `161` assets on all three venues,
  - `1,209` normalized rows stored.
- The production batch exposed `ON_USDT` as an implausible exact-symbol collision; the new identity guard correctly quarantines it rather than showing a false ~20,000 bps opportunity.
- Uncached daily report latency with two retained venue batches: `1.214s`; cached latency: `0.037s`.
- The report request path makes zero venue calls and zero AI calls on first paint.
- Python compile, inline JavaScript parse, read-only endpoint, production report schema, and service health passed.
- Production browser smoke test passed: the report renders the new coverage metrics and venue table, console has no warnings/errors, and a 390×844 viewport has no document-level horizontal overflow (wide tables remain locally scrollable).
- `tests.test_cipher_report_depth`: `9/9` passing, including normalization, persistence, quote buffering, identity quarantine, cache expiry, and cached endpoint coverage.
- Full discovery: `58/59` passing. The only failure is the pre-existing duplicate suggestion-ID sequencing test (`test_duplicate_suggestion_ids_are_repaired_and_future_ids_do_not_collide`).
- Production backup: `/opt/matrix-trader/backups/20260729T022015Z-cross-venue`.

**Next report roadmap phase:**

1. Add timestamped primary-source news, official announcements, listing/governance/security events, and macro-calendar evidence for Yasmin and Daria.
2. Add supply, unlock, treasury, holder-concentration, and on-chain flow evidence for Priya.
3. Add direct social activity plus source-credibility evidence for Hari.
4. Add synchronized depth only for a small priority/watchlist set if measured trader value justifies its API and storage cost.
5. Keep every new collector asynchronous, cached, rate-limited, evidence-labeled, and outside scan/report paths.

---

## 2026-07-29 — Cipher evidence layer + report performance phase

**Built and deployed to production `207.148.66.39`:**

- Upgraded report schema to `cipher-v4-evidence`.
- Daily and weekly Cipher reports now consume bounded evidence packets from MT7's persisted:
  - hourly all-market ticker history,
  - BTC/ETH market-context history,
  - MEXC recorded trade tape,
  - MEXC order-book depth history,
  - paper outcomes and execution costs.
- Added measured breadth evolution, price/OI rotation, funding change, BTC return/RSI/trend transitions, aggressive-flow delta, book imbalance, and spread evidence.
- Enriched Thomas, Harper, Daria, Rishi, Eric, Niobe, Kenny, and Nadia briefs with those measured inputs. Missing tokenomics, social, news, and cross-venue sources remain explicitly unclaimed.
- Paper report evidence now includes W+P rate, net expectancy, profit factor, realized dollar P&L, maximum drawdown, fee/slippage drag, and compounding-trade count.
- Strategy/regime tables now include sample size and average realized P&L.
- Added a compact `Measured Market Evolution` report section instead of another large dashboard surface.

**Efficiency and robustness work:**

- Weekly reports no longer rebuild the full daily report eight times. Seven daily rollups use one bounded DB pass; the full payload is built once.
- Added indexed timestamp queries for signals, filtered candidates, and paper trades. Removed `date(column)` wrappers from report reads so SQLite can use those indexes.
- Normal report navigation never blocks on a live exchange call or AI provider:
  - market evidence comes from the snapshot worker,
  - first paint uses deterministic cached narrative,
  - explicit `Regenerate` may apply optional AI polish.
- Added a 90-day `ticker_daily_snapshots` table. Hourly history remains bounded to seven days; the daily table preserves longer-horizon breadth/rotation evidence at roughly 1/24 the storage cost.
- Existing hourly history is backfilled into daily snapshots once at startup.
- AI polish cannot replace a detailed deterministic note with materially shorter generic copy.
- Snapshot source and age are displayed on the report.

**Production verification:**

- `matrix-trader` restarted successfully and is active.
- New timestamp indexes are present.
- Daily history backfilled to `8,256` daily-symbol rows across `8` days immediately after deploy.
- Uncached production report latency: daily `1.06s`, weekly `1.25s`.
- Cached production report latency: daily `0.04s`, weekly `0.04s`.
- Daily production evidence: `2,072` hourly market rows; paper cohort `53` closed, `56.6%` W+P, `+4.26%` expectancy, `1.55` profit factor, `+$112.78` realized, `$38.53` max drawdown.
- Frontend marker, report APIs, Python compile, inline-JS parse, and service health passed.
- `tests.test_cipher_report_depth`: `7/7` passing.
- Full test discovery has one unrelated existing failure in duplicate suggestion-ID sequencing (`test_duplicate_suggestion_ids_are_repaired_and_future_ids_do_not_collide`).
- Production backup: `/opt/matrix-trader/backups/20260729T020643Z-cipher-v4`.

**Next report roadmap phase:**

1. ~~Add synchronized MEXC/Hyperliquid/Bybit evidence for Ghost and Eric.~~ Completed in `cipher-v5-cross-venue`.
2. Add timestamped primary-source news, official announcements, and macro-event evidence for Yasmin and Daria.
3. Add supply/unlock/on-chain evidence for Priya.
4. Add direct social activity/credibility evidence for Hari.
5. Keep every new collector asynchronous, cached, rate-limited, and outside scan/report request paths.

---

## What This Project Is

Matrix Trader 7.0 is a local web application for high-leverage crypto trading on MEXC and Hyperliquid perpetual swap markets. A Python Flask backend serves a single-file dark-theme dashboard. The user scans 800+ MEXC perp tickers, receives ranked LONG/SHORT signals with entry/TP/SL ladders derived from ATR, views a 4-section AI trade brief, and executes trades manually. Signal history is auto-logged to SQLite. A paper bot runs automated simulated trades. An external mt-learner service analyzes outcomes and generates improvement suggestions. Probabilistic AI forecasting exists only as a forward-tested shadow research lane with zero authority over conviction, risk, paper trading, leverage, or execution. MT7 is not a SaaS product.

---

## Why These Rules Exist (MT2–MT6 Failures)

| MT6 Mistake | MT7 Rule |
|---|---|
| Matrix chat bot as delivery mechanism | Web app only |
| ARIMA price forecasting | No unvalidated forecasting authority. Forward forecasts stay probabilistic, capped, and shadow-only until independently proven. |
| Two competing TUI implementations | One interface: the web dashboard |
| Coinglass API key committed in plaintext | All keys in `.env`, never committed |
| 17 planning markdown files instead of code | Ship before you plan |
| God class `EnhancedTradingBot` (900+ lines) | `app.py` stays flat — one file |
| Multi-exchange as primary venues | MEXC is primary. Hyperliquid is secondary. Others are context only. |

---

## Hard Rules

1. **Ship before you plan.** Running code before the next feature.
2. **One file, one job.** `app.py` stays flat. `lib/` files are pure functions.
3. **No features that don't serve the trader.** If it doesn't help make a better trade decision, it doesn't ship.
4. **The mobile test is non-negotiable.** Every UI change must work on iPhone Safari.
5. **No committed secrets.** `.env` only. Never committed.
6. **Error handling is a feature.** Every API call is wrapped in try/except. App never crashes.
7. **Signal quality over quantity.** 20 high-conviction signals beats 200 weak ones.
8. **The tool is for trading, not for looking at.** Aesthetics serve the signal, not the other way around.
9. **State objects are completely isolated.** Never share state between tabs.
10. **No JS frameworks.** Vanilla JS only.
11. **No glassmorphism, gradients, or drop shadows.** Dark flat UI only.
12. **Read the actual files before writing a single line.** Do not assume state from memory or prior sessions.
13. **No databases for application state.** SQLite for signal history and outcome tracking only.

---

## Execution Safety Rules

Immutable. Cannot be softened by any future session prompt or task description.

1. Live trading is disabled by default. `LIVE_TRADING_ENABLED=false` in `.env` is the master gate.
2. Paper simulation must run successfully before assisted live begins.
3. User confirmation required before every order in assisted mode — no silent placement.
4. Kill switch must be implemented and tested before live trading activates. It is implemented.
5. No automatic leverage escalation under any condition.
6. No averaging down.
7. No blind retry loops on failed order placement.
8. No execution on stale signal data (signal age > 5 minutes at order time).

---

## File Structure

```
Matrix_Trader_7.0/
├── CLAUDE.md              ← Claude Code orientation; phase status defers to HANDOFF.md
├── AGENTS.md              ← Codex orientation (mirrors CLAUDE.md); keep in sync
├── HANDOFF.md             ← this file; update every session
├── README.md              ← public-facing setup guide
├── STRATEGIES.md          ← user-facing strategy guide
├── SERVER_GUIDE.md        ← VPS access, deploy, and service management (Vultr Singapore)
├── .gitignore             ← covers .env, __pycache__, data/, *.db
├── .env                   ← secrets/config only — never commit
├── requirements.txt       ← all deps installed
├── app.py                 ← entire Flask backend — 9,388 lines; keep flat, one file
├── backtest.py            ← standalone script; do NOT import from app.py
├── templates/
│   └── index.html         ← entire frontend: HTML + CSS + JS; 9,214 lines; one file
├── static/                ← directory exists; no CSS file — all CSS is inline in index.html
├── docs/
│   ├── design-brief.md    ← original design doc; read-only reference
│   ├── project-status.md  ← may be stale; HANDOFF.md is authoritative
│   └── superpowers/
│       ├── specs/         ← approved design specs
│       └── plans/         ← implementation plans
├── .claude/
│   └── commands/
│       └── handoff.md     ← /handoff skill: regenerates HANDOFF.md from codebase
├── data/                  ← gitignored; auto-created at runtime; never commit
│   ├── signals.db         ← SQLite: signals, paper_trades, position_events, custom_strategies, filtered_candidates
│   ├── risk_gates.json    ← live risk gate config
│   ├── paper_config.json  ← paper bot operational settings
│   ├── trading_goals.json ← goal definition file (account balance, targets)
│   ├── strategy_overrides.json ← per-strategy config overrides from learner apply
│   ├── rejected_suggestions.json ← rejection log (read by mt-learner)
│   ├── ai_settings.json   ← active AI model + per-feature model overrides
│   └── reports/           ← cached Cipher intelligence reports (daily/weekly JSON)
│   └── hermes/            ← latest_memo.json + archive/ from Hermes consultancy
└── lib/                   ← pure utility functions only; no Flask, no API calls
    ├── agents.py          ← 12-analyst Cipher Research Group + 8-analyst signal pipeline
    ├── ai_client.py       ← AI provider fallback chain; call_ai() is the only public fn
    │                         Supports provider/model override params for per-feature pinning
    ├── exchange_context.py ← canonical exchange-agnostic data contract
    ├── adapters/          ← exchange normalization registry
    │   ├── __init__.py
    │   ├── mexc.py
    │   └── hyperliquid.py
    ├── indicators.py      ← RSI, EMA, VWAP, ATR, volatility_regime, daily_trend_direction
    ├── laddering.py       ← generate_ladders(price, atr, tiers, direction)
    ├── hl_execution.py    ← Hyperliquid execution: place_limit_order, kill_switch, get_positions
    ├── risk_controls.py   ← compute_daily_pnl, compute_position_size, get_readiness_verdict
    ├── mexc_private.py    ← MEXC private API client (read-only account, positions, balance)
    ├── mexc_stream.py     ← WebSocket client — built, not actively used
    ├── coinglass_client.py ← optional CoinGlass V4 client; fails closed if key missing
    └── hyperliquid_client.py ← Hyperliquid public scan + read-only account client

/opt/mt-learner/           ← External learner service on VPS; local mt-learner/ mirror also exists
    learner.py             ← Scheduler: 4 jobs on 30min/2hr/6hr/24hr intervals
    analyzer.py            ← Feature, threshold, regime analysis from signals.db (net-EV-aware)
    suggester.py           ← Generates pending.json with status: "pending_review" using net EV + W+P evidence
    researcher.py          ← Generates strategy hypothesis briefs
    coach_analyst.py       ← Coach review analysis
    models/                ← feature_weights.json, conviction_thresholds.json, regime_performance.json
    suggestions/pending.json ← Read by /api/intelligence/suggestions
    research/briefs.json   ← Read by /api/intelligence/research
    logs/                  ← learner.log (5MB rotating), last_heartbeat.txt
```

**Touch policy:**
- `app.py` and `index.html`: always read the relevant section before editing
- `lib/` files: pure functions only; no imports from app.py; no Flask
- `data/`: never touch directly; managed by `init_db()` and runtime writes
- `docs/`: read-only reference; never edit
- `.env`: never read, write, or commit
- `static/`: no CSS file; do not create one — CSS lives inline in index.html

---

## Tech Stack

From `requirements.txt` — all installed:

```
flask>=3.0.0
requests>=2.31.0
pandas>=2.0.0
numpy>=1.24.0
websocket-client>=1.6.0
python-dotenv>=1.0.0
anthropic>=0.39.0
google-generativeai>=0.8.0
openai>=1.0.0
groq>=0.9.0
eth-account>=0.8.0
msgpack>=1.0.0
```

SQLite3 is stdlib. AI routing supports Claude, OpenAI, Gemini, DeepSeek, Kimi, Z.ai, Groq, Ollama, and one configurable OpenAI-compatible endpoint. Always use `call_ai()` from `lib/ai_client.py`—never import providers directly. `call_ai()` accepts a `feature` key plus optional explicit `provider` and `model` pins. Per-call routing health is recorded in `ai_call_events` without prompts, responses, or secrets.

---

## Environment Variables

```bash
ANTHROPIC_API_KEY=        # AI trade briefs, coach reviews, reports
OPENAI_API_KEY=           # GPT-5.6 family
GEMINI_API_KEY=           # Gemini hosted/free-tier models
GROQ_API_KEY=             # Groq hosted free-tier models
DEEPSEEK_API_KEY=         # DeepSeek V4
KIMI_API_KEY=             # Kimi K2.6/K3
ZAI_API_KEY=              # Z.ai GLM
OLLAMA_BASE_URL=          # optional local Ollama endpoint
CUSTOM_AI_API_KEY=        # optional custom OpenAI-compatible endpoint key
MATRIX_PORT=8080          # optional — defaults to 8080
MEXC_API_KEY=             # optional — private account endpoints
MEXC_API_SECRET=          # optional
COINGLASS_API_KEY=        # optional — CoinGlass OI/liquidation enrichment
HL_WALLET_ADDRESS=        # optional — Hyperliquid read-only account
HL_PRIVATE_KEY=           # required for P11 live execution on Hyperliquid
LIVE_TRADING_ENABLED=false # master gate — must be explicitly true to place orders
REPORT_NARRATIVE_MODE=free # deterministic | free | auto
SCORE_VERSION=v1          # v1 (legacy step) | v2 (saturating ramp)
MT7_API_TOKEN=            # optional bearer token for API auth
ALLOW_PAPER_RESET=false   # emergency maintenance only; reset stays disabled unless explicitly true
MAX_DAILY_LOSS_USDT=0     # optional daily loss circuit breaker
REGIME_COUNTER_ENABLED=false # counter-trend conviction boost
LEARNER_PENDING_PATH=     # override for pending.json path
LEARNER_REJECTED_PATH=    # override for rejected_suggestions.json path
LEARNER_HEARTBEAT_PATH=   # override for last_heartbeat.txt path
```

---

## MEXC API Reference

```
Base URL: https://contract.mexc.com/api/v1

GET /contract/ticker                    — all perp tickers (800+ pairs)
GET /contract/detail                    — contract specs
GET /contract/kline/{symbol}            — OHLCV data (max 2000 candles/request at Min1)
GET /contract/depth/{symbol}            — order book
GET /contract/funding_rate/{symbol}     — current funding rate
GET /private/account/assets             — account balance (auth required)
GET /private/position/open_positions    — open positions (auth required)

Intervals: Min1, Min5, Min15, Min30, Min60, Hour4, Hour8, Day1
```

Sentiment APIs (no key needed):
- OKX L/S: `https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio`
- OKX OI:  `https://www.okx.com/api/v5/public/open-interest`

---

## Flask Routes

| Route | Method | Description |
|---|---|---|
| `/` | GET | Serves index.html dashboard |
| `/api/scan` | GET | Single strategy scan: scores 800+ tickers, enriches top 30, logs to DB |
| `/api/scan/all` | POST | Fetches tickers once, runs all enabled strategies |
| `/api/hl/scan` | POST | Hyperliquid scan |
| `/api/bybit/scan` | POST | Bybit scan stub (no adapter — fails gracefully) |
| `/api/exchanges` | GET | Lists enabled exchanges and status |
| `/api/exchanges/config` | PATCH | Enable/disable exchanges |
| `/api/scan/multi` | POST | Multi-exchange scan across enabled exchanges |
| `/api/market` | GET | All scored market-browser tickers |
| `/api/market/summary` | GET | Market-wide stats summary |
| `/api/signal/<symbol>` | GET | Fully enriches a single symbol on demand |
| `/api/signal/result` | PATCH | Tags a signal WIN/LOSS/PARTIAL/EXPIRED/SKIPPED |
| `/api/signals/stats` | GET | Aggregate signal stats |
| `/api/signals/history` | GET | Signal history with filters |
| `/api/signal/detail/<id>` | GET | Full trade detail + coach review |
| `/api/signal/detail/<id>/regenerate-review` | POST | Clear cached coach review |
| `/api/outcomes/check` | POST | Auto-evaluate open signals against klines |
| `/api/prices` | GET | Batch price fetch |
| `/api/stream/prices` | GET | SSE price updates every 3s |
| `/api/strategies` | GET | All strategies with performance stats |
| `/api/strategies/analytics` | GET | Chart-ready strategy analytics |
| `/api/strategies/portfolio` | GET | Strategy Portfolio Lab simulator |
| `/api/risk-gates` | GET | Live risk gate config + historical impact |
| `/api/risk-gates/extreme_vol_firebreak` | PATCH | Toggle extreme vol firebreak |
| `/api/risk-gates/<gate_key>` | PATCH | Change gate mode (block/shadow/off) |
| `/api/risk-gates/symbol-override` | POST | Add per-symbol risk gate override |
| `/api/risk-gates/symbol-override/<symbol>` | DELETE | Remove per-symbol override |
| `/api/strategy-overrides` | GET | Current per-strategy config overrides |
| `/api/strategy-overrides/<key>` | DELETE | Remove a strategy override |
| `/api/strategies/custom` | POST | Create custom strategy |
| `/api/strategies/custom/<key>` | PATCH | Edit or enable/disable custom strategy |
| `/api/strategies/custom/<key>` | DELETE | Delete custom strategy |
| `/api/strategies/builtin/<key>` | PATCH | Pause/resume built-in strategy |
| `/api/analysis` | POST | AI strategy review (last 200 tagged outcomes) |
| `/api/backfill/pnl` | POST | MAINTENANCE — re-evaluate historical signals |
| `/api/backfill/journey` | POST | MAINTENANCE — backfill journey metrics |
| `/api/cleanup/phantom-events` | POST | MAINTENANCE — delete orphan position events |
| `/api/account/daily-pnl` | GET | Today's realized P&L from signals DB |
| `/api/account/readiness` | GET | Bot readiness metrics |
| `/api/account/status` | GET | MEXC account connection status and equity |
| `/api/account/positions` | GET | Live MEXC positions |
| `/api/account/balance` | GET | MEXC account balance and margin |
| `/api/hl/account` | GET | Hyperliquid read-only account summary |
| `/api/intelligence/hermes` | GET | Full Hermes context packet + latest memo |
| `/api/intelligence/hermes/coach-reviews` | GET | Full coach review corpus (paginated; filterable by result/strategy) |
| `/api/intelligence/status` | GET | Shadow validation status |
| `/api/intelligence/hypotheses` | GET | Parallel shadow hypothesis evaluation; read-only, no config mutation |
| `/api/intelligence/suggestions` | GET | Learner suggestions with baseline metrics |
| `/api/intelligence/suggestions/<id>` | PATCH | Legacy apply/dismiss (backward compat) |
| `/api/intelligence/suggestions/<id>/apply` | POST | Apply suggestion — one-at-a-time enforced |
| `/api/intelligence/suggestions/<id>/reject` | POST | Reject suggestion, write to rejection log |
| `/api/intelligence/suggestions/<id>/park` | POST | Park stale evaluating suggestion without rejecting or reverting overlay |
| `/api/intelligence/research` | GET | Strategy hypothesis briefs |
| `/api/intelligence/roster` | GET | Cipher Research Group analyst roster |
| `/api/intelligence/cross-venue/<symbol>` | GET | On-demand read-only chart/structure comparison for one symbol |
| `/api/intelligence/cross-venue-evidence` | GET | Latest cached synchronized MEXC/Hyperliquid/Bybit report evidence and collector health |
| `/api/intelligence/reports/daily` | GET | Daily Cipher report (cached); rejects future dates |
| `/api/intelligence/reports/weekly` | GET | Weekly Cipher report (cached) |
| `/api/intelligence/reports/regenerate` | POST | Force regenerate a cached report |
| `/api/execution/status` | GET | Hyperliquid execution readiness |
| `/api/execution/place` | POST | Place limit order (gated by LIVE_TRADING_ENABLED) |
| `/api/execution/kill-switch` | POST | Cancel all orders + close all positions |
| `/api/ai/health` | GET | AI provider health check |
| `/api/ai/circuits/reset` | POST | Reset one or all AI provider cooldowns |
| `/api/ai/benchmarks` | GET/POST | Read benchmark history or start an explicit shadow model benchmark |
| `/api/ai/benchmarks/<run_id>/promote` | POST | Manually route an eligible benchmark model to its tested workflow |
| `/api/ai/forecasting` | GET | Forward shadow configuration, candidates, calibration/return scoreboard, regimes, and safety flags |
| `/api/ai/forecasting/run` | POST | Start one bounded collect/evaluate cycle (`all`, `collect`, or `evaluate`) |
| `/api/settings/ai` | GET | Current AI model settings (global + per-feature) |
| `/api/settings/ai` | PATCH | Update global or coach_review model |
| `/api/paper/trades` | GET | Paper trade history |
| `/api/paper/filter-stats` | GET | Live winner/loser ATR% and trend_score averages |
| `/api/paper/stats` | GET | Paper bot aggregate stats |
| `/api/paper/account` | GET | Paper account value, P&L, drawdown |
| `/api/goals` | GET / PATCH | Goal definition file + computed actuals |
| `/api/paper/config` | GET / PATCH | Paper bot operational config |
| `/api/flow/<symbol>` | GET | Order flow data for symbol |
| `/api/paper/reset` | POST | Emergency-only paper reset; disabled unless `ALLOW_PAPER_RESET=true`, bearer token + typed confirmation required, DB backup created first |

Background threads: `_outcome_loop` (15 min), `_snapshot_loop` (1 hr), `_coach_review_loop` (10 min, 5 trades/batch), `_paper_bot_loop` (60s exit check, scan on interval).

---

## Signal Data Shape

Full dict returned by `enrich_signal()`:

```python
{
  "symbol":               str,
  "exchange":             str,   # "MEXC" or "HYPERLIQUID"
  "direction":            str,   # "LONG" or "SHORT"
  "strategy":             str,
  "strategy_key":         str,
  "leverage_cap":         int,
  "conviction":           int,   # 0–100
  "price":                float,
  "entries":              list[float],
  "exits":                list[float],
  "stop_loss":            float,
  "change_24h_pct":       float,
  "change_4h_pct":        float,
  "change_1h_pct":        float,
  "funding_rate":         float,
  "open_interest":        float,
  "next_funding_minutes": int | None,
  "volume_24h":           float,
  "atr_pct":              float,
  "volatility":           str,   # "low" | "medium" | "high" | "extreme"
  "rsi_1h":               float,
  "trend_score":          int,
  "vol_spike_ratio":      float | None,
  "daily_trend":          str | None,
  "daily_trend_aligned":  bool | None,
  "tags":                 list[str],
  "signal_why":           str,
  "ai_report":            str,
  "okx_ls_long_pct":      float | None,
  "okx_oi":               float | None,
  "sentiment_tracked":    bool,
  "strategy_is_custom":   bool,
  "strategy_config":      dict,
  "kline_depth_1h":       int,
  "kline_depth_4h":       int,
  "data_quality":         str,
  "agent_exchange":       str | None,
  "agent_regime":         str | None,
  "agent_blocked":        bool | None,
  "agent_version":        str | None,
  "agent_shadow_delta":   int | None,
  "agent_shadow_disagreement": float | None,
}
```

`signal_json` DB column stores: agent outputs, coach_review, coach_review_at, ai_report, ladder data, journey metrics.

---

## JavaScript State Objects

```js
const S = {
  phase:    'idle',
  signals:  [],
  filtered: [],
  selected: -1,
  dir:      'all',
  sort:     'conviction',
  strategy: 'balanced',
  exchange: localStorage.getItem('mt7_exchange') || 'mexc',
  totalPairs: 0,
  scanTime:   null,
  timerId:    null,
  countdownId: null,
  autoRefreshId: null,
  volFilter:  'any',
  minVolume:  0,
};

const M = {
  phase:        'idle',
  pairs:        [],
  filtered:     [],
  pairsByExchange: {},
  dir:          'all',
  sort:         'conviction_base',
  search:       '',
  page:         0,
  pageSize:     50,
  renderedCount: 0,
  sortDir:      'desc',
  autoRefreshId: null,
};

let currentTab = 'signals';
let currentTVSymbol   = null;
let currentTVInterval = '60';
let currentTVExchange = 'MEXC';
```

`A` — Strategies tab: analytics payload, selected strategy, explainer state.
`I` — Intelligence tab: report cache, suggestions, briefs, roster, Hermes memo.
`H` — History tab: open positions, price cache, closed signals, SSE stream.

State objects are completely isolated. Never cross-reference between tabs.

---

## Dashboard Structure

Seven tabs:

| Tab button | Section div | Loaded by |
|---|---|---|
| `#tab-signals` | `#signals-section` | `scanSignals()` on button click |
| `#tab-market` | `#market-section` | `loadMarket()` on tab switch |
| `#tab-tools` | `#tools-section` | Static + `loadAIModels()` on tab switch |
| `#tab-strategies` | `#strategies-section` | `loadStrategyAnalytics()` + `loadGoalBenchmark()` |
| `#tab-history` | `#history-section` | `loadHistory()` on tab switch |
| `#tab-intelligence` | `#intelligence-section` | `loadIntelligence()` on tab switch |
| `#tab-paper` | `#paper-section` | `loadPaperTrading()` on tab switch |

**Shared detail panel** (`#detail-panel`, `<aside>`): always write innerHTML to `#panel-body`, never to the aside itself.

**Intelligence sub-tabs:** Overview · The Firm · Reports · Suggestions · Edge Lab · Shadow Validation · Hermes

**Strategies tab:** Goal Benchmark strip (`#goal-benchmark`) at top, then strategy explainer, then analytics.

**Tools tab:** AI Model card has two dropdowns — Active Model (global) and Coach Review Model (per-feature override).

---

## TradingView Integration

```js
function toTVSymbol(mexcSymbol, exchange) {
  exchange = exchange || 'MEXC';
  // MEXC: BTC_USDT → MEXC:BTCUSDT.P
  // HYPERLIQUID: BTC_USDC → HYPERLIQUID:BTCUSD.P
}
```

`loadTVChart(symbol, interval, exchange)` sets `currentTVExchange` before rendering.

---

## Color System

```css
:root {
  --bg:     #0b0d12;
  --bg2:    #0e1016;
  --bg3:    #0a0b0f;
  --border: rgba(255,255,255,0.06);
  --text:   #e8e8f0;
  --text2:  rgba(255,255,255,0.45);
  --text3:  rgba(255,255,255,0.25);
  --green:  #00e676;
  --red:    #ff5252;
  --amber:  #ffab40;
  --blue:   #448aff;
  --mono:   'SF Mono', Menlo, 'Courier New', monospace;
}
```

No glassmorphism, no gradients, no drop shadows. Flat dark UI only.

---

## Phase Status

| Phase | What | Status |
|---|---|---|
| P0 | Flask app, MEXC scan, basic scoring, web dashboard | ✅ Done |
| P1 | Indicators, entry/TP/SL, market browser, charts, risk calc, compound planner | ✅ Done |
| P2a | Strategy registry (Balanced/Funding Arb/Momentum/Mean Rev) | ✅ Done |
| P2b | Why-line, freshness dot, invalidation condition | ✅ Done |
| P2c | Template-based AI signal report (4-section trade brief) | ✅ Done |
| P2d | Market sentiment (OKX live, graceful fallback) | ✅ Done |
| P2e | Retry logic, specific error messages, volatility/volume filters, localStorage | ✅ Done |
| UX | First-run guide, strategy tooltips, tag hover tooltips | ✅ Done |
| Backtest | backtest.py — 14 symbols × 4 strategies, real funding rate history | ✅ Done |
| P3a | SQLite signal history — auto-log on scan, PATCH outcome, GET history | ✅ Done |
| P3b | History tab UI — summary bar, outcome buttons, filters, win rate | ✅ Done |
| P3c | AI strategy review — POST /api/analysis, Claude API, History tab button | ✅ Done |
| P3d | Open positions panel — live P&L, SSE price stream, auto-tagging, equity curve | ✅ Done |
| P3d+ | exit_price capture, closed signal detail panel, coach review | ✅ Done |
| P3e | SSE live price refresh for open positions | ✅ Done |
| Strategy Lab | strategy_key end-to-end, /api/strategies, dynamic UI, explainer, custom CRUD | ✅ Done |
| Strategy Analytics | dedicated Strategies tab, analytics charts, regime/symbol breakdowns | ✅ Done |
| Paper trading data integrity | pnl_pct + leverage columns, blended PARTIAL, auto-EXPIRED, backfill | ✅ Done |
| Kline depth gate | enrich_signal() gates pairs with < 50 1h / < 20 4h candles | ✅ Done |
| P5a | Strategy risk gate + Portfolio Lab | ✅ Done |
| P5b | Risk Gates control panel: live block/shadow/off modes | ✅ Done |
| P5c | Paper Trading Lifecycle v2: position_events ledger, TP/SL lifecycle badges | ✅ Done |
| P5d | Min-ladder-spread guard, Balanced extreme-vol SHORT gate in SHADOW | ✅ Done |
| P6a | Optional CoinGlass V4 enrichment | ✅ Done |
| P7a | CoinGlass signal tags: funding confirm, liq asymmetry, fragility | ✅ Done |
| P7b | Strategy lifecycle: pause/resume, direction lock, volatility allowlist | ✅ Done |
| Cipher Research Group | 12-analyst intelligence reports, daily/weekly Cipher briefs, first-person narratives | ✅ Done |
| mt-learner | External VPS service: feature analysis, threshold suggestions, researcher hypotheses | ✅ Done |
| P8 | MEXC read-only account + Bot Readiness tracker | ✅ Done |
| P9 | Trade Readiness Panel — pre-flight checklist, position sizing recommendation | ✅ Done |
| P10 | Paper bot — live on VPS, 50 closed trades, dynamic position sizing | ✅ Done (running) |
| Self-improving loop A+B | Goals file, apply/reject API, benchmark strip, Suggestions sub-tab | ✅ Done |
| Super User AI Control Center | Global + five task routes, free-first preset, 9 providers/custom endpoint, health-aware fallbacks, persistent circuit breakers, connection test, redacted call telemetry | ✅ Deployed 2026-07-27 |
| Model Benchmark Lab | Five workflow suites, score-only persistence, champion/challenger gates, explicit audited workflow promotion | ✅ Deployed 2026-07-27 |
| Forward AI Shadow Validation | 15m/1h/4h probabilistic ledger, calibration and net-after-cost scoring, no-change/MT7 baselines, champion/challenger and regime scoreboard | ✅ Deployed 2026-07-27 — no trading authority |
| Hermes Advisory Group | External consultancy bridge: context packet API, Hermes sub-tab, memo display, weekly timer | ✅ Done |
| Hermes coach reviews | Two-tier system: compact theme summary in packet + full corpus endpoint | ✅ Done |
| Paper/live data isolation | source field in dedup guard; paper bot only links/updates source='paper' signals | ✅ Done |
| P11 | Execution layer built (Hyperliquid kill switch, order placement, confirmation modal) | ✅ Built — NOT activated. Waiting on paper bot validation. |
| Paper bot realism | Pending entry1 wait, max-hold expiry, net fee/slippage P&L, chunked Min1 evaluator parity, UI stats | ✅ Done |
| Paper equity compounding | User-selectable Fixed Base / Compound Realized Equity sizing, caps/floor/drawdown fallback, per-trade audit snapshot | ✅ Deployed — Fixed Base remains active until explicitly changed |
| mt-learner net objective | Threshold/regime suggestions optimize actual net `pnl_pct`, W+P, loss streak | ✅ Done/deployed |
| Liquidation price engine | `lib/risk_liquidation.py` — exchange-aware liq price on signals, paper trades, UI | ✅ Done |
| Paper hard-dollar P&L | `pnl_usd`, `gross_pnl_usd`, `cost_usd` on paper trades; dollar stats in Paper tab | ✅ Done |
| Paper closed detail panel | Clickable closed paper trade rows open right-side detail panel with full breakdown | ✅ Done |
| Org chart drill-downs | Cipher analyst cards + Hermes desk cards open profile/mandate modals with report data | ✅ Done |
| Order flow chart markers | Pair workspace chart overlays Absorb/Δ Div/Sweep/Exhaust event markers on candles | ✅ Done |
| Chart marker controls | Toggle bar + legend for Trade Events / Order Flow / Large Prints / Levels / Liquidation; localStorage | ✅ Done |
| Strategy Context card | Pair workspace sidebar: recent 20/10 perf, symbol fit, direction fit, cold streak warnings | ✅ Done |
| Hermes on-demand run | `POST /api/intelligence/hermes/run` + Run Now button with async status polling | ✅ Done |
| Market fullscreen chart | `.panel-fullscreen .chart-panel` expands to 58vh; chart reloads on toggle | ✅ Done |
| Edge Lab pipeline | V2 versioned research pipeline: exit-aware path economics, rolling dynamic baselines, cost adjustment, symbol-day effective n, discovery/confirmation, FDR, ambiguity and concentration, generalized strategy/counterfactual validation, gated measurement-only drafts → `edge_lab.db` + `factor_report.json` | ✅ Implemented locally; production migration/deploy pending |
| Edge Lab cohort attribution | `/api/paper/cohort-edge` + Paper tab panel attribute trades to fully closed Min15 factor states, with explicit coverage reasons and feature/outcome freshness | ✅ Repaired/deployed — historical coverage 96.8%, current cohort 100% |
| Statistical meta-labeler | Frozen v1 remains untouched; v2 challenger predicts leverage-normalized net Paper utility with grouped hourly walk-forward splits and zero authority | ✅ V2 challenger implemented locally; v1 evidence remains 2/10 and untouched |
| Robust paper/P12 evidence gate | 50-trade isolated minimum, net %/$ EV, W+P, dollar PF, rolling 20/50 stability, 10% trimmed EV, leave-best-out P&L, cohort drawdown, safety controls | ✅ Deployed — current cohort 3/50, live locked |
| P12 | Micro-live automation — one proven strategy, automated, exposure caps | ⏳ Pending — gated on robust paper evidence (55%+ W+P, PF >=1.25, positive robust EV, controlled drawdown, 50+ trades) |

---

## Current Task List

**Next in priority order:**

1. **Operate, do not tune, the completed Edge Lab v2 migration**: the daily low-priority runner upgrades five stale symbols per run and every factor remains `research_only` until the rolling 90-day window reaches 95% v2 label and feature coverage. Keep meta-labeler v1 frozen (`2/10` gates) and collect untouched forward evidence. V2 is a separate grouped-time net-utility challenger with no authority; do not tune it against the same validation outcomes.
2. **Keep forecasting forward-only in parallel**: collect at least 50 valid outcomes per model/horizon and compare Brier skill, net-after-cost return, abstention, and regime stability against MT7 and no-change baselines. Do not backfill stale signals or grant forecasts trading authority.
3. **Collect the clean paper cohort under the frozen robust gate**: P12 now requires 50 isolated closes even when the experiment progress target is 20. Do not change the current policy while the cohort is only `3/50`. At 50, require net %/$ EV > 0, W+P >=55%, dollar PF >=1.25, positive/stable recent 20 and 50 windows, positive 10% trimmed EV, positive P&L after removing the best winner, cohort drawdown <=20%, and no active safety control.
4. **Prepare P12 only after the preceding gates pass**. P11 remains built but inactive; do not add live credentials, raise risk, or automate execution yet.

**Implemented Paper feature — optional realized-equity compounding:**

- `paper_sizing_mode` supports `fixed` and `compound_realized`. Fixed remains the backward-compatible default; changing modes never changes leverage, risk percentage, maximum positions, stop behavior, or margin mode.
- Compound mode uses configured starting balance plus P&L from closed Paper trades only. Open/unrealized P&L is excluded.
- Guardrails: configurable compound cap, operating equity floor that blocks new entries, and current-drawdown fallback that limits sizing to no more than the fixed base.
- Enabling compound mode requires an explicit risk acknowledgement, is blocked while Paper positions are pending/open, and automatically starts a new validation cohort. Returning to Fixed Base is always available once the active-position gate clears.
- The Paper UI shows realized equity, effective sizing base, risk target dollars, maximum single/concurrent notional, and current drawdown. New trades persist their sizing mode/base/equity/risk policy for audit.
- This feature is Paper-only. It grants no Live authority and must be compared with the fixed-base cohort before any Live design review.

**Do NOT do yet:**
- Add `HL_PRIVATE_KEY` to VPS — paper bot has not proven edge
- Activate P12 automation — gated on robust paper validation and the immutable execution checks
- Raise risk or scale account size while Hermes drawdown status is yellow/red
- Enable compounded sizing for Live — Paper forward validation and the independent Live gates must pass first
- Let Hermes directly mutate configs, trade, or read private exchange keys — Hermes is advisory only

**Production server:** Vultr Singapore `207.148.66.39` — SSH key-auth only.
**Hermes workstation:** old Hetzner `62.238.15.113` — isolated advisory agent host only.

---

## What NOT To Do

- Do not call `enrich_signal()` from `backtest.py` — it makes live API calls
- Do not import from `app.py` in a way that triggers Flask server startup
- Do not add new SQLite columns without a migration — wrap in `try/except OperationalError`
- Do not use `datetime.now()` — always `datetime.utcnow()`; all timestamps are UTC ISO without Z
- Do not add JS frameworks — no React, Vue, jQuery, Alpine
- Do not write innerHTML to `$('detail-panel')` — write to `$('panel-body')` only
- Do not filter direction server-side in `/api/signals/history` — client-side only
- Do not commit `.env`, `data/`, or `__pycache__/`
- Do not modify one tab's state object from another tab's code
- Do not call any AI provider directly — always use `call_ai()` from `lib/ai_client.py`
- Do not import `anthropic` at top of `app.py` — lazy import inside `lib/ai_client.py`
- Do not add new strategies by editing only one place — metadata spans `app.py` and `index.html`
- Do not write TP/SL events to `position_events` without a prior `ENTRY_FILLED` event
- Do not run `POST /api/backfill/pnl` from a browser — use `curl -X POST` from the VPS shell
- Do not let agents read raw exchange dicts — normalize through `lib/adapters` into `ExchangeContext` first
- Do not make MEXC or Hyperliquid API calls inside agents — use data passed from `enrich_signal()`
- Do not add SQLite columns for agent fields — agent output belongs in `signal_json`
- Do not hardcode AI provider names in routes — always go through `call_ai()`
- Do not cache coach reviews that contain `<think>` blocks — clear via regenerate route
- Do not generate reports for future dates — backend returns 400, frontend caps navigation at today
- Do not use "choppy" or "low_liquidity" as `allowed_volatility` values — not valid system regimes
- Do not pass behavioral regime labels into `api_payload.allowed_volatility`; learner regime suppressions must use `api_payload.blocked_agent_regimes`
- Do not sync goals file changes to paper config — they are independent
- Do not use the `_apply_suggestion_config()` fallthrough for unknown suggestion types — must explicitly return `False`
- Do not use `source` in `log_signals()` dedup without including it in the WHERE clause — paper and live signals for the same symbol/strategy must be separate rows
- Do not allow `_paper_check_exits()` to write results back to `source='live'` signal rows — always guard with `AND source='paper'`
- Do not compare paper strict WIN rate to live WIN+PARTIAL rate — use the same metric on both sides
- Do not treat paper `pnl_pct` as gross after 2026-05-24 — it is net after configurable fee/slippage; use `gross_pnl_pct` when gross value is needed
- Do not let learner threshold suggestions optimize strict WIN labels alone — `mt-learner/analyzer.py` now treats actual `pnl_pct` as net EV and W+P/loss streak as secondary evidence
- Do not hard-code strategy learnings directly into `STRATEGIES`; use reversible overlays in `data/strategy_overrides.json` and record learner applications in `data/experiment_ledger.json`

---

## How to Run

```bash
pip install -r requirements.txt
cp .env.example .env   # add ANTHROPIC_API_KEY at minimum
python3 app.py
# Local:  http://localhost:8080
# iPhone: http://<LAN_IP>:8080 (same WiFi)
```

**VPS deploy (Vultr Singapore):**
```bash
cd /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0
rsync -avz --exclude='.env' --exclude='data/' --exclude='__pycache__' \
      --exclude='.git' --exclude='*.pyc' ./ root@207.148.66.39:/opt/matrix-trader/
ssh root@207.148.66.39 "systemctl restart matrix-trader"

# mt-learner only:
rsync -avz --exclude='__pycache__' --exclude='*.pyc' \
      --exclude='models/' --exclude='suggestions/' --exclude='research/' \
      mt-learner/ root@207.148.66.39:/opt/mt-learner/
ssh root@207.148.66.39 "systemctl restart mt-learner"
```

---

## Returning to Claude

Start every Claude Code session with:

```
Read CLAUDE.md and HANDOFF.md before touching anything.
[Your task here]
```

---

## Session Notes

### 2026-07-28 — Session summary (Paper realized-equity compounding, deployed)

- Added `Fixed Base` and `Compound Realized Equity` Paper sizing modes. Existing/legacy configs default to Fixed Base, so deployment did not change position size, leverage, risk percentage, or active strategy settings.
- Compound mode calculates eligible equity as configured starting balance plus dollar P&L from closed Paper trades only. Pending/open positions and unrealized P&L are excluded.
- Added configurable `$1,000` compound sizing cap, `$50` operating equity floor, and `20%` current-drawdown fallback defaults. The floor blocks new entries without inventing capital; the fallback limits sizing to no more than the fixed base.
- Both risk-derived sizing and the Paper position cap receive the same effective sizing base. Leverage, risk percentage, max positions, stop behavior, and isolated-margin/liquidation calculations remain independent.
- Added per-trade audit columns: `sizing_mode`, `sizing_base_usd`, `realized_equity_usd`, `sizing_risk_pct`, and `sizing_policy_json`.
- Enabling Compound requires explicit acknowledgement, is rejected while any Paper position is pending/open, and automatically starts a new isolated cohort. Switching modes never resets historical trades.
- Paper API/dashboard now expose realized equity, effective sizing base, dollar risk target, maximum single and concurrent notional, cap/floor state, and current drawdown. Closed trade detail records the sizing policy used at entry.
- Production remains in Fixed Base because one Paper entry is pending. Live verification: realized equity `$941.00`, sizing base `$200.00`, 5% risk target `$10.00`, maximum single position `$50.00`, maximum concurrent notional `$250.00`, current drawdown `1.20%`. The explicit enablement preview shows proposed base `$941.00`, risk target `$47.05`, maximum single notional `$235.25`, and maximum concurrent notional `$1,176.25`. A Compound enable request returned HTTP 409 and stored mode remained Fixed.
- Verification: 51 targeted tests passed (8 new compounding tests), Python/JavaScript syntax checks passed, SQL insert columns/placeholders match, SQLite migration completed, both APIs return the sizing policy, and desktop plus 390×844 mobile browser checks passed.
- Rollback: `/opt/matrix-trader/backups/20260728-paper-compounding/` contains the prior app, dashboard, Paper config, and full 349 MB SQLite database.

### 2026-07-28 — Session summary (universal learner suggestion contract, deployed)

- Generalized the learner improvement path across every strategy key instead of special-casing Funding Arb. `mt-learner/analyzer.py` now reads the active runtime threshold for standard and custom strategies from `/api/strategies?include_disabled=1`; historical implied thresholds remain evidence only.
- Threshold generation now fails closed when runtime authority is unavailable. Read-only proposals with a changed or unverified baseline are automatically superseded; applied/evaluating controls are never rewritten by this cleanup.
- Added the universal `mt7_suggestion_v1` explainability contract to all suggestion API records: exact current→proposed changes, evidence source/sample/confidence, forward-test state, expected benefit, downside, rollback, scope, runtime authority, completeness, and audit fingerprint.
- Every suggestion is manual-review-only (`auto_apply_allowed=false`). Application requires an acknowledged contract version and matching baseline fingerprint. Stale, incomplete, or unsupported changes are blocked.
- Leverage, risk, position-size, maximum-position, margin-mode, and execution-related suggestions are classified as restricted. They must show dollar risk targets, notional/margin/concurrent-exposure estimates and loss/liquidation semantics, then require the exact phrase `APPROVE <suggestion_id>`. This does not weaken the immutable no-automatic-leverage-escalation rule.
- Dashboard Suggestions cards now render the contract explicitly. Pending controls show `Review & Apply`; restricted controls render a red capital-at-risk disclosure. Mobile browser verification at 390×844 confirmed the contract remains readable.
- Repaired a production queue collision by assigning the second duplicate record `thresh_balanced_focus_short_20260728_001_r2`; future IDs use a collision-free allocator. Production queue has 10 records and 10 unique IDs.
- Current production pending reviews are runtime-authoritative and remain unapplied: Funding Arb `69→81` (`strategy_override`) and Balanced Focus Short `65→67` (`strategy_config`). The previous Funding Arb `60→81` and unverified Balanced Focus Short proposal were superseded.
- Production safety verification: an apply call without contract acknowledgement returned HTTP 409 and left the suggestion `pending_review`; both `matrix-trader` and `mt-learner` are active. No strategy configuration was applied.
- Tests: 43 targeted unit tests passed, including universal-strategy runtime authority, stale/unverified supersession, restricted capital disclosure/confirmation, application gates, and duplicate-ID repair.
- Deployment backups: `/opt/matrix-trader/backups/20260728-suggestion-contract/` and `/opt/mt-learner/backups/20260728-suggestion-contract/`.

### 2026-07-28 — Session summary (Hermes metric contract + fail-closed report publishing, deployed)

- Re-audited the latest Hermes memo against production code and data. The material Hermes concerns are real but remain evidence/measurement issues, not reasons to relax MT7 safety: keep the isolated current Paper policy unchanged until 50 closed trades, do not scale risk or enable assisted live, and recompute the stale Funding Arb threshold proposal before review.
- Added the `hermes_metrics_v2` contract to the MT7 packet. Source-live signal-evaluator outcomes, disabled-strategy counterfactual shadow outcomes, and simulated Paper trades now have separate lanes, windows, units, and execution semantics. Signal totals use percentage points; Paper dollar fields use `size_usd * pnl_pct / 100`.
- Hermes now receives the authoritative current Paper policy, fixed `$200` sizing base, maximum `5` open positions, capability inventory, readiness blockers, and explicit risk semantics. `risk_pct_per_trade` is a sizing input, not a guaranteed realized-loss cap.
- Learner audit now exposes proposal baseline versus runtime truth. `thresh_funding_arb_20260717_001` is explicitly conflicted: proposal baseline `60`, runtime actual `69`, suggested `81`. It must not be applied; recompute from `69` and collect fresh forward evidence.
- Corrected the old-VPS Hermes wrapper on `62.238.15.113`. It no longer feeds prior memo prose back as evidence, includes the new authority/capability contracts, and reduces duplicated research context from a 164 KB prompt to about 62 KB of decision-relevant summaries.
- Added fail-closed memo publication. A generated memo is rejected before replacing `latest_memo` if it mislabels the all-time Paper count, conflates the 30-day linked-signal sample, omits the 4/50 cohort gate, misses the stale threshold conflict/do-not-apply instruction, invents non-overlapping rolling-window requirements, couples every shadow experiment, or describes signal-selection gates as capital-loss protection.
- Promoted a semantically validated memo and synced it into production MT7. Production reports `301` all-time simulated Paper trades, current cohort `4/50`, `$741.00` all-time simulated Paper P&L, live 30-day signal-evaluator `n=40`, and disabled shadow `n=1,575`; the memo correctly keeps these datasets separate.
- Verification: `app.py` compiles; all `34` focused tests pass, including the 3 new Hermes metric-contract regressions; `/api/intelligence/hermes` returns `success=true`, `hermes_metrics_v2`, and the validated memo; `matrix-trader` is active.
- Rollbacks: production app backup `/opt/matrix-trader/backups/app.py.pre-hermes-v2-20260728T1936Z`; report-runner backup `/opt/mt7-hermes/run_consultancy.sh.pre-metrics-v2-20260728`; replaced production memo backup `/opt/matrix-trader/backups/hermes-20260728T201234Z/`.

### 2026-07-28 — Session summary (robust paper/P12 evidence gate, deployed)

- Replaced the lenient paper-promotion decision with a conservative P12 evidence policy. The configured 20-trade experiment target remains a progress marker, but P12/assisted-review readiness now has an immutable minimum of 50 isolated closed trades.
- Added net evidence in both percentage and actual position-size dollars, W+P >=55%, dollar profit factor >=1.25, recent 20/50 stability, a 10% symmetric trimmed mean, P&L after removing the single best dollar winner, top-winner concentration, and cohort-only peak-to-trough drawdown.
- Recent-window stability requires both 20- and 50-trade windows to have positive net %/$ EV, PF >=1.0, and W+P >=50%. Outlier resilience requires both trimmed EV and leave-best-out dollar P&L to stay positive. Cohort drawdown is capped at the tighter of the configured account limit and 20%.
- Fixed the readiness unit bug where a stored `0.667` fraction was rendered and compared as `0.7%`. Production now correctly reports the current cohort W+P as `66.7%`.
- The older mixed-policy sample is now explicitly non-blocking context. It can explain improvement or regression but cannot authorize the current policy.
- `/api/paper/cohort-review`, `/api/paper/account`, and `/api/live/readiness` expose the same robust evidence. The Live route still hard-codes `can_auto_trade=false`; no paper config, execution setting, leverage, position size, or live credential changed.
- Current production truth: cohort `recovery_trial_balanced_focus_short_2026-07-28` is `3/50`, net average `+18.58% / +$9.29`, W+P `66.7%`, PF `5.38`, trimmed EV `+18.58%`, P&L excluding the best winner `+$6.92`, top-winner share `61.2%`, and cohort drawdown `3.18%`. Every performance gate remains `wait` until the sample reaches 50, and an active strategy cooldown independently keeps Live locked.
- Added `tests/test_paper_readiness.py`. The suite proves that one-winner outlier profit is rejected, recent decay is detected despite positive full-sample averages, and broad 60-trade evidence can clear every blocking robust gate. All `31` tests pass.
- Deployed a production-specific app/dashboard patch. `matrix-trader`, `mt-learner`, and both Edge Lab timers are active; all three readiness APIs return HTTP 200. Desktop and 390×844 browser checks show no overflow or console errors. Rollback bundle: `/opt/matrix-trader/backups/20260728-robust-paper-gate`.

### 2026-07-28 — Session summary (statistical meta-labeler Phase 1, deployed)

- Added `edge_lab/meta_labeler.py` and `edge_lab_meta.py`: a deterministic NumPy logistic meta-labeler with temporal calibration and expanding walk-forward evaluation. It reads paper outcomes from `signals.db`, joins only fully closed pre-entry Min15 states from Edge Lab, and writes research artifacts only to `edge_lab.db`.
- Feature joins enforce `feature_timestamp + 15m <= trade_timestamp` and a maximum 30-minute feature age. The outcome target is positive net `pnl_pct`, so fees/slippage already included by the paper evaluator remain part of the label.
- Every walk-forward fold uses only earlier trades for base-model fitting and an inner chronological calibration holdout. Evaluation includes Brier score, log loss, calibration error, accuracy, no-filter and strategy baselines, fold stability, decision-bucket EV/PF/drawdown, and feature PSI drift.
- Added ten predeclared evidence gates: >=95% coverage, held-out sample, Brier wins over both baselines, ECE <=0.10, a useful >=20-trade shadow-allow slice with positive/incremental EV, acceptable drift, and majority fold wins. Even if all gates pass, `authority_eligible` remains hard-coded `false`.
- Added `meta_label_runs` and `meta_label_predictions` audit tables inside `edge_lab.db`. Production verification confirms there are no meta-labeler tables or writes in `signals.db`; the API safety contract reports no conviction, config, sizing, order, or execution authority.
- The active-score ledger is forward-only and immutable: it stores only the first score made before an outcome exists, updates that row when the trade closes, and excludes the trade from all later active rescoring. Historical closed trades are never backfilled as forward evidence.
- Added `GET /api/intelligence/meta-labeler`; `/api/intelligence/factor-report` includes the latest overview. The Edge Lab Intelligence tab now shows coverage, held-out sample, Brier, ECE, forward outcomes toward the 50-trade target, shadow-allow evidence, every gate, version, and an explicit no-authority badge.
- The initial frozen production run used entries from `2026-07-08T16:41:37.802459`: `147/152` joined (`96.71%`), `74` walk-forward test trades, model Brier `0.268597` versus no-filter `0.247214` and strategy `0.245439`, accuracy `48.65%`, ECE `0.189055`, and no trades above the strict 0.60 shadow-allow threshold.
- Initial evidence is negative: only coverage and test-sample gates pass (`2/10`). Brier skill is negative versus both baselines, and max feature PSI is `1.489738`, dominated by the shift between `funding_arb` and `funding_arb_focus_short`. Three current open/pending scores were stored as `shadow_block`; none affected the paper bot.
- Production run 2 verified the forward ledger migration: all three original unresolved scores remain single immutable records, none have closed, and the forward cohort therefore correctly reports `0/50`. No active trade was duplicated or retrospectively labeled.
- Do not tune v1 against these same held-out trades. The next valid challenger is predeclared: after >=50 closed trades in the new July 28 cohort, evaluate a funding-arb-specific challenger trained only on the prior window and compare it against frozen v1 and both baselines.
- Scheduled the shadow run after every daily/weekly Edge Lab refresh. Routine output is compact; model weights and row features are not exposed through the API.
- Added `tests/test_meta_labeler.py` for chronological fold boundaries, closed-candle cutoff, end-to-end calibration/persistence, immutable forward-score evaluation, duplicate prevention, and the signals-DB/authority boundary. All `27` focused tests pass; Python, shell, JavaScript, API, checksum, desktop, and 390×844 browser verification pass with no console errors.
- Production services and both Edge Lab timers are active. Rollback bundle: `/opt/matrix-trader/backups/20260728-meta-labeler`.

### 2026-07-28 — Session summary (Edge Lab coverage repair, deployed)

- Diagnosed the July 8 validation cohort before changing code: only `45/154` trades matched fresh Edge Lab rows (`29.2%`); `63` were on symbols never ingested, `36` had stale features, and `10` were still inside the old 24-hour outcome-maturity window.
- Removed the structural coupling between features and future outcomes. `candle_feature_snapshots` now stores every feature-complete Min15 state immediately, while `candle_labels` continues to require its full 96-candle/24-hour forward path. No outcomes, paper trades, scoring, or execution settings are written into the snapshot table.
- Attribution is leakage-safe: a Min15 row is eligible only after its candle has fully closed before the trade. The API retains a labeled-feature fallback for older historical rows while the new independent snapshot history accumulates.
- Raised the cohort ingestion limit from `60` to `200`; the daily refresh now explicitly ingests all active-cohort symbols before the top-volume universe. Routine builders use `--skip-smoke` to avoid an optional full JSON scan across the 19 GB label table.
- Added per-trade `coverage_reason`, `feature_source`, snapshot/outcome max timestamps, aggregate reason counts, and unmatched-symbol diagnostics to `/api/paper/cohort-edge` and the operations watchdog.
- Backfilled the July 8–28 validation universe: production now holds `235,070` snapshots across `71` symbols. Historical coverage is `150/155` (`96.8%`), up from about `30%`; current reset-cohort coverage is `1/1` (`100%`) and the Edge Lab watchdog passes.
- All five historical misses are explained: two `AERGO_USDT` trades have no current contract history, two `BUILDONBOB_USDT` trades only have stale pre-delisting history, and one `AEHRSTOCK_USDT` trade has no kline history available from MEXC. Both USDC equity-perp cohort symbols returned usable history and were ingested.
- Added `tests/test_edge_lab_coverage.py` for feature/outcome separation, fully closed-candle matching, staleness, and missing-symbol classification. All `24` focused tests pass; Python and shell validation pass.
- Deployed a production-specific `app.py` patch plus Edge Lab builder/storage/scheduler changes. `matrix-trader`, `mt-learner`, and both Edge Lab timers are active. Rollback bundle: `/opt/matrix-trader/backups/20260728-edge-coverage`.

### 2026-07-28 — Session summary (learner authority semantics, deployed)

- Established an explicit authority contract: `pending`/`pending_review`, `shadow_evaluating`, and `parked` are read-only; `evaluating` is an applied config trial; `applied` owns active scan authority; `superseded` and `rejected` own none.
- Added built-in control introspection for learner thresholds, blocked agent regimes, and volatility allowlists. `/api/intelligence/suggestions` now reports each suggestion's `control_authority`, any state conflict, the authority contract, and conflict counts.
- Active Strategy Overrides now reports field-level provenance. Production shows Balanced conviction and low-liquidity suppression as learner-applied, Funding Arb choppy suppression as learner-applied, and Funding Arb conviction `69` as manual/legacy.
- Applying/promoting an exact control that is already active is rejected instead of creating a duplicate trial. Resuming a parked item into shadow is rejected if matching authority remains active. Rejecting an authority-owning item requires it to be parked/rolled back first.
- Parking an applied trial now removes only that suggestion's exact override and preserves unrelated fields. The UI labels this action `Park & Roll Back`; shadow cards explicitly say read-only, applied cards show active authority, and conflicting states render a red warning.
- Migrated legacy production labels while `mt-learner` was stopped: `regime_balanced_low_liquidity_20260524_001` and `regime_funding_arb_choppy_20260529_001` became `applied`; duplicate `regime_balanced_low_liquidity_20260608_002` became `superseded`. The override SHA-256 was identical before/after, so scan behavior did not change. The migration is recorded in `experiment_ledger.json`.
- Production now reports `2` read-only shadows, `0` applied trials, and `0` authority conflicts. The learner queue watchdog passes.
- Added `tests/test_learner_authority.py` for deterministic ownership migration, exact-field rollback, audit recording, and active-control shadow rejection. All `22` focused AI/learner tests pass.
- Deployed production-specific `app.py` and dashboard builds. Rollback bundle: `/opt/matrix-trader/backups/20260728-learner-authority`. Both services are active with no warning logs.
- Browser verification passed on desktop and `390×844`: Applied Control, Read-only Shadow, Superseded, provenance, and zero-conflict states all render; no horizontal overflow or console errors.

### 2026-07-28 — Session summary (roadmap audit + AI admission control, deployed)

- Audited the clean paper cohort `recovery_trial_custom_balanced_no_extreme_vol_2026-07-08`: `147` closed, W+P `51.0%`, average net `+1.48%`, hard P&L `+$108.77`, PF `1.16`, and max drawdown `$109`. It trails the comparable baseline by `6.64` average-P&L points and remains `hold_review`.
- Robustness is weak: removing the best three trades reduces cohort PF to `1.009`; removing the best five produces PF `0.928` and `-$50.27`. The recent 50 recovered to PF `1.714`, but the newest 25 softened to PF `0.87`. Median trade P&L is `-7.42%`.
- Strategy/regime splits are actionable research hypotheses, not promotion evidence: `funding_arb|SHORT` was strongest (`33` trades, PF `2.19`), while `funding_arb_focus_short` was weak (`12`, PF `0.455`). High volatility, choppy, news-catalyst, and compression slices underperformed; `volatile_squeeze` performed strongly. Positive/negative agent deltas did not cleanly separate outcomes.
- Edge Lab matched only `45/147` cohort trades (`30.2%`). Its “favorable” matches averaged `-3.11%`, while unmatched trades averaged `+2.46%`; current factor attribution therefore does not validate the edge.
- Found learner state drift: two overrides are displayed as `shadow_evaluating` while scan admission actively applies their blocked regimes. State semantics must be repaired before adding another learning layer.
- Forward AI forecasting has one evaluated observation at 15m/1h and is statistically non-informative. It remains capped, forward-only, and zero-authority.
- AI telemetry exposed a reliability failure: since July 27 there were `1,735` provider attempts and only `384` successes. One signal scan generated `1,540` agent attempts; hundreds hit unfunded DeepSeek/Gemini models concurrently before their first failure could persist a circuit, then Groq hit its free rate limit.
- Fixed `_circuit_allows()` so a provider with no circuit record atomically acquires one cross-process `half_open` initial-probe lease. Concurrent requests now skip that provider until the probe succeeds or opens the shared breaker. Healthy providers remain concurrent; explicit Super User tests can still bypass a circuit.
- Added a 20-thread regression test proving exactly one request reaches an initially untested failing provider. All `19` AI router/benchmark/forecast tests pass.
- Deployed `lib/ai_client.py` to production with rollback copy `/opt/matrix-trader/backups/20260728-ai-admission/ai_client.py`. Both `matrix-trader` and `mt-learner` restarted active; production checksum matches local, AI/paper APIs respond, and neither service logged warnings after restart.

### 2026-07-27 — Session summary (Forward AI shadow validation, deployed)

- Added `lib/ai_forecasting.py`, an isolated forward-outcome ledger for structured UP/FLAT/DOWN probabilities at 15m, 1h, and 4h. One model call produces all three horizons; prompts and raw responses are never stored.
- Collection is bounded to fresh (`<=5m`), current-quality live signals at or above the configured conviction floor. Production is set to conviction `70`, a hard `12` model-call UTC daily cap, and an evidence target of `50` evaluated forecasts per model/horizon.
- Added deterministic realized-outcome evaluation from the first available Min1 close at each horizon. The scoreboard reports multiclass Brier score, skill versus a no-change baseline and the MT7 signal-direction baseline, direction accuracy, abstention quality, unleveraged net-after-`0.12%` research return, profit factor, shadow drawdown, and volatility-regime splits.
- Evidence readiness requires the target sample plus a Brier win over MT7, positive net return, direction accuracy >=50%, and >=95% valid-call rate. Readiness is descriptive only; there is no automatic promotion.
- Added the Forward AI Scoreboard to the Super User AI Control Center plus `GET /api/ai/forecasting` and authenticated `POST /api/ai/forecasting/run`. The dashboard can select one champion and one optional challenger, enable/disable collection, set the conviction floor/cap/target, run a bounded cycle, and inspect the evidence.
- Safety boundary is explicit in code and API: forecasts cannot change conviction, risk gates, paper trades, orders, leverage, or execution. Returns are unleveraged research metrics. Provider circuits and the hard daily cap are enforced before calls.
- Deployed the production-specific app/dashboard patch, `lib/ai_client.py`, and the new forecasting module to Vultr `207.148.66.39`. Rollback files are in `/opt/matrix-trader/backups/20260727-ai-forward`.
- Production champion is Groq Qwen 3.6 27B (free), based on its qualified 94/100 `signal_agents` benchmark. Challenger remains empty: Llama 3.1 8B scored 81.5 but missed correctness (`27.5/40`), while Llama 3.3 70B scored 60.3 with correctness `7.5/40`; neither passed the `35/40` correctness gate.
- The first bounded cycle made zero calls and created zero rows because the newest qualifying live signal was from July 22. This is intentional: no stale backfill. Collection begins after the next genuine live scan creates a qualifying signal.
- Verification passed: 18 focused router/benchmark/forecast tests, Python compilation, staged JavaScript parsing, production service/API/schema checks, explicit zero-authority safety flags, a real four-call free-model challenger benchmark, and responsive desktop/390px browser checks with no horizontal overflow or console errors.

### 2026-07-27 — Session summary (Model Benchmark Lab, deployed)

- Added `lib/ai_benchmark.py`, a shadow-only evaluator with two fixed synthetic MT7 cases for each of the five routed workflows: signal analysts, coach review, strategy analysis, Cipher report polish, and learner coach-pattern synthesis.
- Every response is scored deterministically out of 100: format 20, correctness 40, risk discipline 30, and calibration 10. Latency only breaks close recommendation ties and cannot rescue an unsafe result.
- Promotion gates require every case to complete, quality >= 70, format >= 18, correctness >= 35, and risk >= 24. The harness never changes routes automatically; an eligible result exposes an explicit `Use for workflow` action and records the old/new route in `ai_benchmark_promotions`.
- Added `ai_benchmark_runs`, `ai_benchmark_results`, and `ai_benchmark_promotions`. Prompts and model responses are never persisted; results store provider/model provenance, component scores, latency, verdict, and a redacted failure only.
- Candidate selection is capped at eight. Unconfigured providers and providers with open circuits cannot start a run. The UI confirms the exact number of external calls before starting.
- Versioned the clarified benchmark contract as `mt7_static_v1`; the initial pre-vocabulary run is retained as `mt7_static_v0` for auditability.
- Deployed a production-specific app/dashboard patch and the new evaluator to Vultr `207.148.66.39`. Rollback files are in `/opt/matrix-trader/backups/20260727-ai-benchmark`.
- Production free-tier signal-analysis canary: Groq Qwen 3.6 27B scored 94/100 (risk 30/30, correctness 40/40, format 20/20, median 706 ms) and became the first qualified champion. Groq GPT-OSS 120B returned no usable response in either v1 case and remains `needs_work`.
- No model was auto-promoted. The `signal_agents` route still inherits the Super User active route and the promotion ledger remains empty.
- Verification passed: 14 focused tests, Python compilation, JavaScript parsing, Flask API contract checks, production schema/API checks, real background benchmark execution, responsive desktop/390px UI, champion table rendering, and zero browser console errors.

### 2026-07-27 — Session summary (AI circuit breakers + health-aware routing, deployed)

- Added a persistent `ai_provider_circuits` ledger shared by `matrix-trader` and `mt-learner`. Billing/authentication failures open for one hour, exhausted rate limits for ten minutes, configuration failures for five minutes, and transient outages after two consecutive strikes for two minutes.
- Open circuits permit only one cross-process half-open probe after cooldown. A successful response closes and clears the breaker; a failed probe reopens it.
- Added health-aware fallback ordering based on bounded recent success history, median latency, and model cost tier. The explicit Super User model remains first while its circuit is closed; only fallback candidates are reordered.
- Explicit model tests bypass the current circuit and test only the selected model. The Tools panel shows open cooldowns and includes a `Reset provider cooldowns` control for deliberate re-probing after keys, balances, or quotas change.
- Added `routing_strategy` (`health_aware` or fixed `ordered`) to AI settings plus `POST /api/ai/circuits/reset`.
- Deployed a production-specific app/dashboard patch plus the shared router to Vultr `207.148.66.39`. Rollback files are in `/opt/matrix-trader/backups/20260727-ai-circuit`.
- Production verification: one DeepSeek V4 Flash test returned its existing 402 and opened the billing circuit; the next normal `coach_pattern` call skipped DeepSeek and completed through Groq Qwen 3.6 27B in one actual attempt. Both services remain active.
- Verification passed: 9 router tests, Python compilation, JavaScript parsing, Flask API smoke tests, production API/schema checks, real provider failover, no browser console errors, and no horizontal overflow at desktop or 390px mobile width.

### 2026-07-27 — Session summary (AI provider/routing foundation, deployed)

- Expanded the shared `lib/ai_client.py` router to Claude, OpenAI GPT-5.6, Gemini, DeepSeek V4, Kimi K2.6/K3, Z.ai GLM, current Groq free-tier models, Ollama, and one user-configured OpenAI-compatible endpoint.
- Added task routes for signal analysts, coach reviews, strategy analysis, Cipher report polish, and learner coach-pattern synthesis. Explicit model pins still win; otherwise each task inherits its route or the active global model.
- Replaced the two-model Tools card with the Super User AI Control Center. It includes unrestricted catalog selection, task routing, a free-first shortcut with a hard free-tier/local fallback boundary, custom endpoint configuration, explicit connection testing, and recent inference health.
- Added `ai_call_events` telemetry. It records provider, model, task, success, latency, fallback use, attempt number, and a redacted error only—never prompts, responses, or API keys.
- Migrates retired `deepseek-chat` / `deepseek-reasoner` selections to `deepseek-v4-flash` at load time and migrates the legacy coach override into `feature_routes`.
- Forecasting remains deliberately outside this batch and has no authority over signal conviction, risk gates, or execution.
- Switched Gemini integration from the retired `google-generativeai` client to the current `google-genai` SDK.
- Deployed a production-specific bundle to Vultr `207.148.66.39` without copying unrelated local `app.py` / dashboard work. Rollback files are in `/opt/matrix-trader/backups/20260727-ai-router`.
- Restarted `matrix-trader` and `mt-learner`; both are active. Production exposes 24 models, 5 feature routes, and the `ai_call_events` ledger.
- Production provider state after deploy: Claude, Gemini, DeepSeek, and Groq keys are configured; Gemini credits are depleted, DeepSeek balance is insufficient, and Groq successfully completed the fallback calls.
- Verification passed: 5 AI router unit tests, Python compilation, frontend JavaScript parse, Flask settings/health smoke tests, production API checks, service logs, and live browser checks at desktop and 390px mobile width.

### 2026-07-01 — Session summary (profitability meta-analysis — no code changes)

- Deep meta-analysis of strategies/profitability written to `MT7_META_ANALYSIS_2026-07-01.md` (repo root). Analysis-only session; no app code, config, or DB writes.
- Data: local `signals.db` (production snapshot to 2026-05-08, 750 closed live signals, leveraged gross pnl_pct) **plus live production API queries** (GET-only) against `207.148.66.39:8080` on 2026-07-01.
- **⚠ Production build regression found:** `/api/paper/account` returns the pre-June-28 schema — `drawdown_pct=129.93` (return-as-drawdown bug back), no `return_pct`/`max_drawdown_usd`/`scale_up_blockers`, and `scale_up_ready=true` from the old lenient logic. Local `app.py` has the fix; production does not. Redeploy + restart + re-verify before trusting any drawdown or scale-up output.
- **Focus-short cohort update (live):** 104 closed since June 9 — W+P 60.6%, strict 34.6%, avg net +7.05%, PF 1.87, hard P&L +$366.63, worst trade -$17.11. Passes the documented P12 gate on its face; blocked only on the redeploy/drawdown verification above.
- Hypothesis lab live verdicts: `regime_balanced_low_liquidity` = promote candidate (96 affected, avg -6.49, PF 0.83 vs 1.23); `momentum needs flow` promote candidate but comparison n=3; `funding_crowded` collecting/early-positive; `funding_arb/choppy` review.
- **Intelligence-layer audit (queried live):** mt-learner is DOWN (`learner_running=false`, baseline stale 2026-06-06) and the suggestion queue regressed to pre-June-7 state (both regime suggestions un-parked → one-experiment guard deadlocked; no suggestion can apply). Production Hermes `latest_memo` is the pre-fix June 28 05:58Z "Critical — 129.93% drawdown" memo (built on the buggy metric) and its body contains leaked LLM preamble — Hermes memos lack the coach-review cache guard. Agent shadow validation (905 signals): 83% neutral deltas, win/loss delta separation <1 conviction point — LLM delta channel economically irrelevant; regime classifier is the valuable output. Edge Lab cohort attribution coverage only 5.6% (6/104; cohort symbols are tokenized-stock perps outside the Edge Lab universe) and its 6 "favorable" trades avg -11.82. Full assessment + self-monitoring recommendations in `MT7_META_ANALYSIS_2026-07-01.md` §6.
- Headline findings: baseline PF 1.04 (0.98 trimmed) — no base edge; SHORT PF 1.65 vs LONG PF 0.50; LONG+funding≤-0.1% ("squeeze longs") n=251 PF 0.37 is the single biggest leak; LONG RSI≥70 PF 0.24; `bid_heavy`/`discount` LONG boosts anti-predict while their SHORT mirrors work; KAT_USDT alone -2,091 total.
- Stacked filter (no squeeze-longs, no high/extreme-vol LONGs, ATR≤5, conviction≥65) kept 119/750 trades at PF 2.73 (trim-6 PF 3.02) — proposed as next paper cohort alongside `paper_stop_mode: tp_ladder_lock`.
- Top recommendations (shadow-first): market-regime LONG gate using stored btc_context; squeeze-LONG hard gate; overbought LONG hard block; fee-aware net EV for live signals; short-only `momentum_breakdown` strategy; per-symbol frequency cap. Full ranking and sequencing in the report.

### 2026-06-28 — Session summary (drawdown audit fix + Hermes rerun)

- Audited the June 28 Hermes memo claim that paper drawdown was `129.93%`. Root cause was `_compute_goal_actuals()` using total paper P&L divided by starting balance as `drawdown_pct`; that value was actually account return.
- Added true fixed-dollar peak-to-trough paper equity metrics: `return_pct`, `peak_value_usd`, `max_drawdown_usd`, `drawdown_peak_at`, and `drawdown_trough_at`. `/api/paper/account`, `/api/goals`, and `/api/intelligence/hermes` now report real drawdown separately from return.
- Updated paper safety controls to use true peak-to-trough drawdown instead of total P&L vs starting balance.
- Tightened `scale_up_ready`: EV/sample/drawdown must pass, paper cohort must have enough trades, W+P >= 55%, profit factor >= 1.25, and no active paper safety controls. The API now exposes `scale_up_blockers`, `active_safety_controls`, and paper cohort gate stats.
- Updated the Goals dashboard drawdown tile to display drawdown as a positive risk percentage and show peak/max-loss context.
- Deployed to production `207.148.66.39` and restarted `matrix-trader`. Production verified: `current_value_usd=459.86`, `return_pct=129.93`, `drawdown_pct=13.51`, `max_drawdown_usd=51.40`, `peak_value_usd=491.25`, `scale_up_ready=false`, blocker `active paper safety control`.
- Triggered a fresh Hermes run. New memo generated at `2026-06-28T23:28:41Z` now says `Cautious Improvement — Drawdown Contained`, reports drawdown `13.51%`, and correctly says not to scale while the active cold-streak paper safety control is present.

### 2026-06-12 — Session summary (paper reset hardening + Hermes yellow flag review)

- Reviewed the June 12 Hermes memo. Verdict is Cautious Hold / Yellow Flag: reported account growth is strong, but drawdown breach and paper/live EV divergence override scale-up optimism.
- Immediate engineering task is reset/auth hardening, not P12. Local reset endpoint is disabled by default with `ALLOW_PAPER_RESET=false`; if deliberately enabled, it requires `MT7_API_TOKEN`, `Authorization: Bearer ...`, typed body confirmation `DELETE PAPER TRADES`, and creates a DB backup before deleting paper trades.
- Dashboard reset control is removed; `resetPaperTrades()` now only alerts that reset is disabled.
- `/api/paper/reset` should be treated as emergency maintenance only. Never expose it to the public internet without `MT7_API_TOKEN` set.
- Hermes recommends approving two learner suppressions in shadow-only mode first: `regime_funding_arb_choppy_20260608_001` and `regime_balanced_low_liquidity_20260608_002`. Do not promote to live blocking until 50 closed affected signals validate EV improvement and trade-count impact.
- P12 remains blocked: do not add `HL_PRIVATE_KEY`, do not scale risk, and do not blend paper/live metrics while the paper EV discrepancy is unresolved.

### 2026-06-12 — Session summary (parallel hypothesis lab)

- Added read-only `/api/intelligence/hypotheses` to evaluate multiple shadow hypotheses side by side without touching scanner, paper, or live config.
- Initial tracked hypotheses: `regime_funding_arb_choppy_20260608_001`, `regime_balanced_low_liquidity_20260608_002`, `research_order_flow_confirmation`, and `research_funding_crowding_filter`.
- Endpoint reports live and paper affected/comparison slices separately, with affected count, W+P, avg net P&L, dollar P&L when available, profit factor, deltas vs same-strategy comparison rows, and a per-source verdict.
- Added Intelligence tab "Parallel Hypothesis Lab" panel. This is intentionally observational; the old one-at-a-time learner apply guard remains for actual config mutation.
- Local validation passed: Python compile, frontend JS parse, API smoke test, and in-app browser render check for the Intelligence panel.

### 2026-06-07 — Session summary (project review, mean_reversion disable, GitHub push)

- Reviewed full project state. Paper bot config confirmed correct on VPS: `disabled_strategies` properly blocks `balanced`, `custom_balanced_no_extreme_vol`, `funding_arb`, `momentum_breakout`. "Leaking" trades observed in post-tightening data were pre-June-1 trades already open when the disable took effect — not a bug.
- Post-tightening cohort as of this session: 29 closed, W+P 55.2%, avg net +1.07%, PF 1.09. `funding_arb_focus_short` is the only strong performer (50% W+P, avg net +10.56% over 28 trades). `mean_reversion` dragging at avg net -20% over 7 trades.
- Disabled `mean_reversion` via Paper tab Configuration UI. Active paper strategies are now `balanced_focus_short` and `funding_arb_focus_short` only.
- Learner status: `learner_running` was showing false due to heartbeat file age (10-minute window check). Heartbeat fix deployed by Codex. Suggestions queue: `thresh_balanced_20260523_001=applied`, both regime suggestions parked by Codex session earlier today.
- Set up GitHub SSH auth for the first time: generated `ed25519` key on Mac, added to GitHub. Switched remote from HTTPS to SSH (`git remote set-url origin git@github.com:bnortey/Matrix_Trader7.0.git`).
- Pushed all 7 local commits to GitHub for the first time. Repo is now backed up remotely.
- Updated HANDOFF.md to reflect current state.
- Reviewed Claude's uncommitted Symbol Loss Gate work and fixed the safety issues before commit: raw `risk_gates.json` metadata is now read/written without dropping `symbol_overrides`, auto-block writes merge with existing manual overrides, the gate uses a configurable recent live-signal lookback (`symbol_loss_gate_lookback_days`, default `30`) instead of all-time history, auto-blocks clear when the recent window recovers, and manual unblocks create a cooldown (`symbol_loss_gate_unblock_cooldown_hours`, default `168`) so the next scan does not immediately re-block the symbol.
- Local temp-file smoke test passed: manual overrides survived auto-blocking, old-window and paper rows were ignored, `/api/risk-gates/symbol-loss-stats` returned the configured lookback, and unblock wrote a durable cooldown record.

### 2026-06-07 — Session summary (learner heartbeat + focus-short paper cohort)

- Fixed misleading `learner_running: false` UI/API status: `mt-learner/learner.py` now writes `logs/last_heartbeat.txt` every 60 seconds while the scheduler loop is alive, not only when the 30-minute feature job runs.
- Deployed `mt-learner/learner.py` to production `207.148.66.39`; restarted `mt-learner`; service is active and `/api/intelligence/suggestions` now reports `learner_running: true`.
- Confirmed Claude's earlier diagnosis: disabled-strategy "leaks" in the post-tightening cohort were old open/pending trades closing after the cutoff, not new disabled entries. Current paper config was already blocking `balanced`, `custom_balanced_no_extreme_vol`, `funding_arb`, and `momentum_breakout`.
- Tightened paper config again: disabled `mean_reversion` after the post-tightening sample showed `7` closed trades with negative avg net P&L on both LONG and SHORT sides.
- Started new paper cohort: `current_cohort_started_at="2026-06-07T14:50:00"`, `current_cohort_label="Focus-short only cohort"`. Active strategies are now `balanced_focus_short` and `funding_arb_focus_short`; current cohort count starts at `0/20`.
- The pending `regime_funding_arb_choppy_20260529_001` suggestion was not applied. The app correctly blocks it because `regime_balanced_low_liquidity_20260524_001` is still `evaluating`; also, base `funding_arb` is disabled, so applying it now would create another stale evaluation guard. Revisit after adding an explicit "finish/park experiment" path or re-enabling base `funding_arb`.
- Added `POST /api/intelligence/suggestions/<id>/park` plus a Suggestions-tab `Park Evaluation` button for evaluating suggestions that cannot reach the trade window. Parking is neutral: it unlocks the learner queue, records `parked_at` / `park_reason`, appends an experiment-ledger note, and does not write a rejection record or revert overlays.
- Parked `regime_balanced_low_liquidity_20260524_001` because `balanced` is disabled and the evaluation was stuck at `9/20`; the `balanced.blocked_agent_regimes=["low_liquidity"]` guard remains active.
- Applied then parked `regime_funding_arb_choppy_20260529_001`: production `strategy_overrides.json` now has `funding_arb.blocked_agent_regimes=["choppy"]` and `funding_arb.min_conviction=69`; it is parked because base `funding_arb` is disabled and cannot generate an active evaluation cohort.
- Final learner status: `learner_running=true`; suggestions are `thresh_balanced_20260523_001=applied`, `regime_balanced_low_liquidity_20260524_001=parked`, `regime_funding_arb_choppy_20260529_001=parked`. Learner queue is unlocked for future suggestions.
- Verified Edge Lab Lite on production: `edge-lab-lite.timer` is active, last run was `2026-06-07 03:25:28 UTC`, next run is `2026-06-14 03:19:38 UTC`. The run completed at `2026-06-07T03:41:38Z`.
- Production Edge Lab outputs: `data/edge_lab.db` is present (`9.2G`), `data/factor_report.json` is present (`236K`), and `/api/intelligence/factor-report` returns success with `2,157,163` candles across templates `TP0_5_SL0_5`, `TP1_0_SL0_5`, `TP1_5_SL0_75`, `TP2_0_SL1_0`.
- Added the production Edge Lab Lite runner and systemd units to the repo: `scripts/run_edge_lab_lite.sh`, `scripts/systemd/edge-lab-lite.service`, `scripts/systemd/edge-lab-lite.timer`. Checksum dry-run against production shows content matches; only timestamp/group metadata differs.
- Added `/api/paper/cohort-edge` plus a compact Paper tab "Edge Lab Cohort Attribution" panel. It matches current cohort paper trades to same-symbol `Min15` Edge Lab candle states within a configurable 30-minute window, reports coverage, favorable/mild/unfavorable alignment buckets, strategy breakdowns, top positive factors, and recent closed trade attribution. This is research-only and does not mutate paper config or signal scoring.
- Deployed `app.py` and `templates/index.html` to production `207.148.66.39`; `matrix-trader` restarted and is active. Production endpoint verification returned `success=true` for the Focus-short only cohort with `0` current cohort trades, `0%` coverage, Edge Lab report metadata present, and latest Edge candle `2026-06-06T03:30:00`. That empty attribution is expected until the focus-short cohort produces trades and the weekly Edge Lab dataset catches up.
- Added a daily incremental Edge Lab refresh alongside the weekly Lite run: `scripts/run_edge_lab_daily.sh`, `scripts/systemd/edge-lab-daily.service`, and `scripts/systemd/edge-lab-daily.timer`. Daily timer runs Mon-Sat around `03:45 UTC` with jitter; weekly Lite remains Sunday. Both share the same lock so jobs cannot overlap.
- Manual production run of `edge-lab-daily.service` completed successfully on `2026-06-07T15:25:07Z`: processed `120` eligible top-volume symbols, fetched `81,509` recent candles, inserted/materialized `5,279` new rows, rebuilt `factor_report.json`, and advanced `/api/paper/cohort-edge` max Edge candle from `2026-06-06T03:30:00` to `2026-06-06T15:15:00`. Runtime was about `9m42s`; factor analysis is still the long pole (`~364s`).
- Cleaned Edge Lab incremental fetch logging so short lookback runs no longer produce false "partial history" warnings against the 90-day backfill expectation.

### 2026-05-24 — Session summary (paper realism, regime cleanup, net-EV learner)

**Built/deployed to Vultr `207.148.66.39`:**
- Market regime widget fixed: `low_liquidity` is now a first-class regime bucket instead of being collapsed into `unknown`.
- Added agent regimes in `lib/agents.py`: `compression`, `breakout_trend`, `liquidation_cascade`, `funding_crowded`, `risk_off_beta`; backend/frontend regime distribution knows these buckets.
- Paper bot pending-entry lifecycle:
  - Confirmed paper trades insert as `status='pending'`.
  - `_paper_check_entries()` promotes to `open` only after Min1 candle touches `entry1`.
  - Pending entries expire after `entry_timeout_hours` (default 24h) as `EXPIRED`, not losses.
  - `queued_at` and `filled_at` added/backfilled on `paper_trades`.
- Paper max-hold lifecycle:
  - `_paper_expire_stale_open_trades()` expires open paper trades after `max_holding_hours` (default 80h), freeing slots without assigning loss P&L.
  - Existing stale ENJ paper trade expired cleanly; linked `source='paper'` signal updated to `EXPIRED`.
- Paper net-cost accounting:
  - Configurable defaults: `paper_maker_fee_bps=0`, `paper_taker_fee_bps=2`, `paper_slippage_bps=3`.
  - Added `gross_pnl_pct`, `fee_cost_pct`, `slippage_cost_pct`; paper `pnl_pct` is now net after costs.
  - Backfilled 41 closed paper trades: gross avg `+1.01%`, net avg `+0.53%`, avg cost drag `0.48%`, total net `+21.60%`.
  - Paper UI now shows Strict Win Rate, W+P Rate, Net Avg P&L, Gross Avg P&L, Avg Cost, Pending Entries, Entry Expired, Hold Expired.
- mt-learner objective upgrade:
  - `analyzer.py` threshold analysis now optimizes actual `pnl_pct` net EV, includes W+P rate and max loss streak.
  - Feature analysis compares positive vs negative P&L rows instead of WIN vs LOSS labels.
  - Regime analysis emits W+P, avg net P&L, total net P&L.
  - `suggester.py` only proposes threshold changes when net EV improves; regime suppression uses W+P and/or net EV evidence.
  - Deployed `/opt/mt-learner/analyzer.py` and `/opt/mt-learner/suggester.py`; `mt-learner` restarted and active.

**Production checks after deploy:**
- `matrix-trader` active; `/api/paper/config` exposes cost and hold settings.
- `/api/paper/stats` after chunked Min1 parity: W+P `56.2%`, strict win `37.5%`, net avg `+3.63%`, gross avg `+4.09%`, avg cost `0.46%`, closed `48`, open `1`, pending `1`, hold-expired `1`.
- `mt-learner --dry-run` passed threshold/regime/proposal jobs; service restarted and active.
- Current meaningful pending suggestion: suppress `balanced` in `low_liquidity` (`112` trades, strict win `26.2%`, W+P `44.7%`, net EV `-8.9%`).

**Min1 evaluator parity fix:**
- `_fetch_klines_for_signal()` now accepts `end_ts` and chunks MEXC Min1 requests with `start`/`end` windows when the requested span exceeds a single 1440-candle call.
- `api_outcomes_check()` now evaluates live open signals with `evaluate_outcome(... interval="Min1", kline_limit=1440)` and writes `evaluation_version="min1_chunked_v1"`.
- `_paper_check_exits()` keeps using Min1, but long holds now replay the full configured holding window instead of only 24h; linked paper signals write `evaluation_version="paper_min1_chunked_v1"`.
- Production validation on `207.148.66.39`:
  - Direct 72h BTC probe fetched `4316` Min1 candles in chunks.
  - `/api/outcomes/check` evaluated `17` live open signals successfully, tagged `0`, skipped `17`.
  - Manual paper exit pass closed `3` long-held paper trades using chunked windows (`3556`, `3445`, and `3011` Min1 candles).

**Flexible learner overlay fix:**
- Added reversible `blocked_agent_regimes` strategy overlay support in `app.py`; this is intentionally separate from ATR `allowed_volatility` (`low`, `medium`, `high`, `extreme`).
- `run_scan()` now applies `blocked_agent_regimes` after `enrich_signal()` because `agent_regime` exists only after the agent pipeline runs.
- `strategy_to_api()` and the Strategy manager UI expose blocked agent regimes; active overrides panel shows min-conviction, ATR volatility, and blocked agent-regime overlays separately.
- `_apply_suggestion_config()` now accepts learner `regime_suppress` payloads as `blocked_agent_regimes`; old ATR-style `allowed_volatility` suppressions still work only for valid ATR regimes.
- `mt-learner/suggester.py` now emits `api_payload: {"blocked_agent_regimes": [regime]}` for future behavioral regime suppressions.
- Added `data/experiment_ledger.json` append-only records for applied learner experiments.
- Production `207.148.66.39` status after apply:
  - `data/strategy_overrides.json`: `balanced.min_conviction=60`, `balanced.blocked_agent_regimes=["low_liquidity"]`; `funding_arb.min_conviction=69`.
  - `thresh_balanced_20260523_001` was marked `applied` to clear the one-experiment guard; its threshold override remains active.
  - `regime_balanced_low_liquidity_20260524_001` is now `evaluating` with baseline snapshot at `2026-05-25T03:27:41Z` (`paper_ev_per_trade_pct=0.82`, `win_partial_rate=0.4932`, `current_value_usd=248.30`).

**Coach analyst AI fix:**
- `mt-learner/coach_analyst.py` no longer calls Groq directly. It loads env keys from `/opt/mt-learner/.env` and `/opt/matrix-trader/.env`, imports shared `lib.ai_client.call_ai()`, starts with Claude Sonnet (`claude-sonnet-4-6`), then falls through to configured/free providers if Claude has no key/credits or fails.
- Prompt now samples worst losses, best positive outcomes, and recent reviews, trims each excerpt, and caps prompt size around 18k chars so fallback models do not hit Groq-style 413 limits.
- Production dry-run on `207.148.66.39` succeeded with Anthropic 200s and updated 4 coach-pattern briefs: `funding_arb_focus_short`, `balanced_focus_short`, `mean_reversion`, `momentum_breakout`. `mt-learner` restarted and active.

**Follow-up correction after Claude wrong-VPS investigation:**
- Claude initially inspected/deployed to old host `62.238.15.113`; current production is `207.148.66.39`.
- The missing graduated strategy was actually present on `207.148.66.39`, but malformed as `custom_balanced_no_treme_vol` / `Balanced (no treme vol)`.
- Root cause was in `mt-learner/researcher.py` re-evaluation parsing: `stop_pressure:balancedxextreme` was split on raw `"x"`, producing `"treme"` because `extreme` contains `x`.
- Fixed/deployed researcher parser to use the known strategy prefix; invalid stop-pressure volatility now returns no proposed strategy rather than silently producing a bogus clone.
- Repaired production DB and brief: strategy is now `custom_balanced_no_extreme_vol` / `Balanced (no extreme vol)` with `allowed_volatility=["low","medium","high"]`. No signals had used the bad key.
- Do not accept the app-side “strip invalid allowed_volatility values” approach; validation should stay strict and researcher should emit valid payloads.

**Still pending before P12:**
- Paper gate review: current all-closed paper stats are `108` closed, W+P `47.2%`, strict win `27.8%`, net avg `+0.14%`, hard P&L `+$106.48`, profit factor `1.11`, best trade `+$116.94`, worst trade `-$67.25`. Sample size is sufficient, but the edge is not strong enough for P12 yet.

### 2026-06-01 — Session summary (order-flow chart overlay production fix)

- Fixed the production pair-workspace chart mode behavior on `207.148.66.39`: `Order Flow` and `Footprint` now keep the candlestick chart visible and render their flow/footprint panels below it; only `DOM` remains a chart-replacement ladder view.
- Added order-flow panel framing/scroll constraints so the panel does not consume the whole pair workspace.
- Deployed `templates/index.html` to `/opt/matrix-trader/templates/index.html`; restarted `matrix-trader`; service is active.
- Browser-verified live on `http://207.148.66.39:8080/#/pair/743`: Footprint shows `Charting by TradingView` plus `Latest FP Delta`; Order Flow shows chart plus Value Area/Bid Walls.
- Checked `mt-learner`: `systemctl is-active mt-learner` is `active`, logs show jobs running on June 1, and `/api/intelligence/suggestions` reports `learner_running: true`.
- Current production paper stats at check time: `108` closed trades, W+P `47.2%`, strict win `27.8%`, avg net P&L `+0.14%`, hard P&L `+$106.48`, profit factor `1.11`, best trade `+$116.94`, worst trade `-$67.25`. This is enough sample, but not enough edge for P12.

### 2026-06-01 — Session summary (paper gate analysis + strategy tightening)

- Analyzed all `108` closed production paper trades directly from `/opt/matrix-trader/data/signals.db`.
- Headline: overall W+P `47.2%`, strict win `27.8%`, avg net `+0.14%`, hard P&L `+$106.48`, profit factor `1.11`.
- Outlier dependency is too high: removing the top/bottom 3 trades changes the sample to `102` trades, avg net `-1.18%`, hard P&L `-$29.83`, profit factor `0.96`; removing top/bottom 5 drops to avg net `-1.86%`, profit factor `0.87`.
- Direction split is decisive: `LONG` trades are `58` closed, W+P `36.2%`, avg net `-5.39%`, hard P&L `-$194.26`, PF `0.68`; `SHORT` trades are `50` closed, W+P `60.0%`, avg net `+6.56%`, hard P&L `+$300.73`, PF `1.74`.
- Base `funding_arb` is not P12-ready: `74` closed, W+P `43.2%`, avg net `-1.8%`, hard P&L `-$20.14`, PF `0.95`. Its LONG side is the main drag: `funding_arb|LONG` has `48` closed, W+P `33.3%`, avg net `-3.82%`, hard P&L `-$51.06`, PF `0.84`; `funding_arb|SHORT` is modestly positive.
- `funding_arb_focus_short` remains the cleaner experiment: `9` closed, W+P `55.6%`, avg net `+7.74%`, hard P&L `+$29.52`, PF `1.60`, but sample is still small.
- Regime warning: `low_liquidity` remains dangerous despite some winners: `20` closed, avg net `-8.27%`, hard P&L `-$158.88`, PF `0.61`.
- Production paper config was tightened via `PATCH /api/paper/config`: `disabled_strategies` is now `["balanced", "custom_balanced_no_extreme_vol", "funding_arb", "momentum_breakout"]`. This stops new base `funding_arb` and `momentum_breakout` entries. Existing open/pending trades were not touched.
- Next paper review should judge the post-tightening sample separately, with focus on `funding_arb_focus_short`, `balanced_focus_short`, and any remaining `mean_reversion` experiment. Do not mix pre-tightening base `funding_arb` performance into future P12 readiness.
- Added compact post-tightening cohort tracking to `/api/paper/stats` and the Paper tab. Config keys: `current_cohort_started_at="2026-06-01T14:17:46"` and `current_cohort_label="Post-tightening cohort"`.
- The Paper tab now shows one slim cohort strip above the existing stat cards: progress toward 20 closed trades, W+P, avg net P&L, profit factor, hard P&L, and active strategies. It intentionally avoids another large card section.
- Live browser verification passed on production: cohort strip displayed `Post-tightening cohort`, active strategies `balanced_focus_short`, `funding_arb_focus_short`, `mean_reversion`, and progress `1/20` shortly after deploy.

### 2026-06-01 — Session summary (live paper prices + visible footprint/order flow)

- Fixed why open paper positions showed `Entry` equal to `Current`: `/api/stream/prices` emits `{symbol, price}` events, but the frontend was treating each event as a symbol→price map. The Paper tab now parses both shapes safely.
- `/api/paper/trades` now seeds `current_px` for open/pending paper trades from one MEXC ticker call so first render uses market price instead of falling back to entry while the SSE stream connects.
- Paper open rows now update live price, P&L, stop distance, TP1 distance, and remove the fallback `~` marker once live price is available.
- Pair workspace now subscribes to the same live market stream for the selected trade and updates the Position Management `Current`, `P&L`, and `Risk To Stop` values in place.
- Fixed hidden order-flow/footprint visibility: `.orderflow-panel` is inserted directly after `#pair-chart-container`, before marker controls/tabs, so Footprint and Order Flow are visible immediately under the candle chart.
- Deployed `app.py` and `templates/index.html` to production `207.148.66.39`; `matrix-trader` restarted and is active.
- Production API verification: `/api/paper/trades` returned live `current_px` for open trades (`COTI_USDT` entry `0.01267497`, current `0.01281`; `MANTRA_USDT` entry `0.00852374`, current `0.00833`). SSE verification returned live `{symbol, price}` events for both symbols.
- Browser verification on `http://207.148.66.39:8080`: Paper open rows showed live Current/P&L instead of entry fallback; `#/pair/751` showed live Current/P&L in the right Position Management panel; Footprint mode rendered chart plus footprint panel with `Tape Delta`, `Latest FP Delta`, `POC`, and imbalance data.

---

### 2026-05-26 — Session summary (Hermes on-demand, market fullscreen chart, doc updates)

- `POST /api/intelligence/hermes/run` + `GET /api/intelligence/hermes/run/status` — triggers `scripts/run_hermes_weekly_from_vultr.sh` in background thread (5 min timeout); returns 409 if already running.
- "Run Now" button added to Hermes panel header with async status polling and auto-refresh on completion.
- Market tab fullscreen fix: `.panel-fullscreen .chart-panel { height: 58vh !important }` — chart expands when panel goes fullscreen. `togglePanelFullscreen()` now reloads active chart after toggle so it renders at new size.
- HANDOFF.md and README.md updated to reflect all work since May 24.

### 2026-05-26 — Session summary (Codex: order flow markers, chart controls, strategy context)

- Pair workspace chart now overlays order-flow event markers from recorded flow history: Absorb (diamond), Δ Div (circle), Sweep (arrow), Exhaust (square). Merged with paper trade lifecycle markers.
- Flow event context added to chart hover/readout: event type and confidence score on candle hover.
- Chart marker toggle bar + legend added under pair workspace chart. Toggles: Trade Events, Order Flow Events, Large Prints, Levels, Liquidation. Preferences in localStorage.
- Strategy Context card added to pair workspace right sidebar. Shows: recent 20/10 perf, symbol-specific EV, direction fit, current streak, avg win/loss, cold streak warnings.
- `GET /api/paper/strategy-context/<trade_id>` backend endpoint added.
- `edge_lab/` package built locally: candle fetch (`mexc_data.py`), path labeler, feature engine, materializer, factor engine (ATR/RSI/regime/trend/volume/tag), factor report → `data/factor_report.json`. Separate `edge_lab.db`. CLI runners: `edge_lab_build.py`, `edge_lab_factors.py`, `edge_lab_materialize.py`. Also: `fade_hypothesis.py`, `analyze.py` standalone analysis scripts. **Not yet deployed to VPS.**

### 2026-05-25 — Session summary (org chart drill-downs)

- Built richer clickable org-chart cards in `templates/index.html`.
- Cipher Research Group analyst cards now open a larger profile modal with role/specialty, exchange coverage, decision inputs owned, latest daily report excerpts, and domain-specific evidence tables.
  - Example verified: Kenny Hassan shows Funding Autopsy plus funding heatmap rows.
  - Report data is loaded from `/api/intelligence/reports/daily` and cached in `I.reportCache`.
- Hermes Advisory Group desk cards are now clickable and open a desk modal with mandate, owned metrics/signals, latest synced Hermes memo section, and recent memo archive.
  - Example verified: Performance Audit Desk shows Bottom-Line Scoreboard slot, paper/live EV ownership, W+P ownership, sample size, and archive entries.
- Deployed to production `207.148.66.39`; `matrix-trader` restarted and active.
- Validation: frontend script parse passed (`JS OK`); headless browser smoke test clicked Cipher firm card and Hermes desk card successfully. Only browser console issue observed was a harmless `404` for a missing static resource/favicon.

### 2026-05-25 — Session summary (paper hard P&L + closed detail)

- Paper stats now expose hard-dollar performance in `/api/paper/stats`: total/avg paper P&L dollars, gross dollar P&L, cost dollars, profit factor, best/worst trade dollars.
- `/api/paper/trades` now enriches each trade row with `pnl_usd`, `gross_pnl_usd`, and `cost_usd` using `size_usd * pnl_pct / 100`.
- Paper tab now shows Paper Account, Hard P&L, Avg $ / Trade, Profit Factor, Costs Paid, Best Trade $, and Worst Trade $.
- Closed paper trades table now includes `$ P&L` and `SIZE` columns. Rows are clickable.
- Clicking a closed paper trade opens a right-side detail panel similar to History with hard P&L, net/gross/cost breakdown, position size, leverage, entry/exit/stop/TP, flow score/reasons, duration, and linked signal journey/coach review when available.
- Fixed a production `api/signal/detail` 500 caused by `_generate_coach_review()` referencing `load_ai_settings` without importing it locally.
- Deployed to `207.148.66.39`; `matrix-trader` active.
- Validation: Python compile passed, frontend JS parse passed, API returned hard-dollar fields, and headless browser smoke test confirmed hard P&L cards, `$ P&L` column, clickable closed row, paper detail panel, and linked trade journey.

---

### 2026-05-24 — Session summary (paper/live data integrity + coach review improvements + Hermes depth)

**Built:**
- Per-feature AI model selector: `coach_review_model` / `coach_review_provider` in `data/ai_settings.json`; second dropdown in Tools tab AI card; `call_ai()` extended with `provider`/`model` override params in `lib/ai_client.py`
- Hermes two-tier coach review system: compact theme summary (10 keyword categories, result breakdown) always in Hermes packet; `coach_reviews_recent_20` full text; new `/api/intelligence/hermes/coach-reviews` deep-dive endpoint (paginated, filterable by result/strategy)
- Paper/live signal source isolation: `log_signals()` dedup now includes `source`; paper bot post-log lookup requires `source='paper'`; `_paper_check_exits()` guards signal UPDATE with `AND source='paper'`
- Historical data repair: 37 signal rows on VPS corrected from `source='live'` → `source='paper'`

**Decided:**
- Coach reviews: full context was attempted after Anthropic credits topped up, but mt-learner `coach_analyst.py` still has Groq 413 payload failures as of later 2026-05-24 session; next session should cap/summarize payloads.
- Paper bot W+P was actually 55% (not 35%) — prior metric was comparing apples to oranges (strict WIN vs W+P). Later work moved paper P&L to net-after-cost accounting and chunked Min1 parity; current net avg is `+3.63%` over 48 closed trades.
- Four-lever paper realism is complete for current MT7 needs: entry1 wait, max-hold expiry, fee/slippage deduction, and full chunked Min1 evaluator parity for long holds.

**Deferred:**
- P12 micro-live automation

**Watch out for:**
- Paper W+P 55% is still a small sample (40 trades) — EV is the more important metric and it's near zero
- `source` column must be in dedup guard or paper/live rows will collide for same symbol/strategy
- `_paper_check_exits()` must never write back to live signal rows — always `AND source='paper'`
- When comparing paper vs live performance always use the same metric (both W+P or both strict WIN)

---

### 2026-05-23/24 — Session summary (Hermes Advisory Group bridge)

- Added `/api/intelligence/hermes` and Hermes sub-tab to MT7 Intelligence
- Deployed old-VPS runner at `/opt/mt7-hermes/run_consultancy.sh` on `62.238.15.113`
- Installed `mt7-hermes-weekly.timer` on Vultr (Sundays 05:30 UTC)
- First Hermes memo generated and synced to production
- Fixed self-improving loop bugs: unknown suggestion types, legacy PATCH guard, goals/paper config decoupling
- Fixed future-date reports: backend returns 400, frontend caps navigation at today
- Fixed researcher.py vocabulary: behavioral labels ("choppy") must not flow into `api_payload.allowed_volatility`

---

### 2026-05-23 — Session summary (Ops / Vultr / MEXC migration)

- Migrated production to Vultr Singapore `207.148.66.39`; SSH key-auth only
- MEXC subaccount IP whitelist set to Vultr IP; private endpoints now work
- `lib/mexc_private.py` fixed: correct signing target, `/private/` endpoint paths
- Edge Lab Lite weekly timer installed (Sundays 03:15 UTC)

---

### 2026-05-22 — Session summary (Cipher Research Group gaps + first-person narrative rewrite)

Reviewed Codex's Intelligence tab implementation. Fixed 4 gaps: analyst bio expand modal, date/week navigation, in-memory report cache, weekly spotlight render. Full narrative rewrite: all analyst notes now first-person with specific data references and forward calls. Deployed to VPS.

---

### 2026-05-13 — Session summary (Phase 2 agents + History stats + P11 execution layer)

Phase 2 agents live — `agent_shadow_delta` applied to conviction. History tab stats overhauled. P11 execution layer shipped: EIP-712 signing, kill switch, order placement gated by `LIVE_TRADING_ENABLED`.

---

### 2026-05-12 — Session summary (0-signal bug fix + P9 Trade Readiness Panel)

Fixed 0-signal bug: strategies now run sequentially in `api_scan_all()`. P9 Trade Readiness Panel shipped.

---

### 2026-05-19/20 — Session summary (signal quality research + scoring improvements + paper bot simulation fix)

6 scoring improvements deployed. Paper bot exit evaluation fixed to use Min1 klines.
