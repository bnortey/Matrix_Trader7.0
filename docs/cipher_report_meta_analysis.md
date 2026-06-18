# Cipher Report Meta-Analysis

Date: 2026-06-18

## Problem

Cipher/Hermes reports had useful raw material but were not consistently trader-useful. The biggest issue was that narrative sections sounded like desk commentary while the actual trader decision was implicit.

In one production daily report, the AI-polished narrative said it was "opening a long" on an explosive mover even though MT7 had no signal for that symbol. That is the core failure mode: reports can describe market context, but they must not invent entries.

## Findings

1. Reports mixed observation, interpretation, and action in the same paragraph.
2. "Hold", "caution", and "ready" labels were not always translated into a next user action.
3. Explosive-move autopsies did not clearly distinguish:
   - MT7 caught the move,
   - MT7 blocked the move,
   - MT7 missed the move.
4. Funding and microstructure notes were useful but needed explicit "watch vs avoid vs investigate" buckets.
5. Research-card and report language sometimes used win-rate or entry language where the underlying evidence was actually a diagnostic, not a trade setup.
6. AI-polished report text needed hard safety constraints because an LLM can turn advisory context into trade instruction.

## Trader-Useful Report Standard

Every Cipher report should answer:

- What is the current posture: wait, selective, defensive, or investigate?
- What should the trader review now?
- What should the trader watch but not trade yet?
- What should the trader avoid or de-risk?
- What needs investigation because the engine missed or contradicted something?
- Which data source supports each claim?
- What would invalidate the read?

## Changes Implemented

- Added a backend `action_matrix` to daily/weekly report data.
- Added report posture: `wait`, `selective`, `defensive`, or `investigate`.
- Added structured buckets:
  - `actions`
  - `watchlist`
  - `avoid`
  - `investigations`
- Added explicit report rules:
  - Reports are advisory.
  - Only fresh MT7 signals can become trade candidates.
  - No report note may create a trade if the signal engine did not produce one.
  - Paper cohort gates, not daily reports, decide promotion.
- Added an AI narrative sanitizer that strips or replaces entry-inventing language.
- Tightened the AI report prompt to use only advisory verbs: review, watch, wait, avoid, investigate.
- Added a visible "Trader Action Matrix" section to the Reports tab.

## Production Verification

The regenerated 2026-06-18 Cipher daily report now classifies the top explosive mover as:

- Posture: `investigate`
- Investigation: `ESPORTS_USDT moved +131.13% with funding 0.00548 but had no MT7 signal or block record`
- Next step: check whether the move lacked structure at scan time, happened between scans, or exposed a missing feature

The narrative no longer says to open a trade. It calls the event a missed-mover review.

## Remaining Improvements

- Add invalidation criteria to every action item.
- Add confidence and sample quality badges to each action item.
- Link action items directly to Signals, Paper trades, Edge Lab, or Research cards.
- Compare each report's prior recommendations against subsequent outcomes.
- Add a "what changed since last report" section so users can see whether risk is improving or degrading.
