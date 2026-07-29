#!/usr/bin/env python3
"""Matrix Trader Research Firm — deterministic hypothesis discovery from closed trade data.

No LLM calls. No network calls. Pure SQL + arithmetic only.
Reads signals.db read-only. Writes to research/briefs.json atomically.
"""

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path


LEARNER_VERSION = "v1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _load_briefs(research_dir: Path) -> dict:
    path = research_dir / "briefs.json"
    if not path.exists():
        return {"briefs": []}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {"briefs": []}


def _write_briefs(research_dir: Path, data: dict):
    path = research_dir / "briefs.json"
    tmp = research_dir / "briefs.json.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _confidence_from_trades(n: int) -> str:
    if n >= 100:
        return "ready"
    if n >= 50:
        return "proposal"
    if n >= 30:
        return "emerging"
    return "watching"


def _confidence_from_journey_trades(n: int) -> str:
    """Confidence thresholds for journey-data hypothesis types (TYPE 4, 5).
    Emerging starts at 20 (not 30) — journey data accumulates slower."""
    if n >= 100:
        return "ready"
    if n >= 50:
        return "proposal"
    if n >= 20:
        return "emerging"
    return "watching"


def _strategy_display_name(conn, strategy_key: str) -> str:
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT strategy FROM signals WHERE strategy_key=? LIMIT 1",
            (strategy_key,),
        )
        row = cur.fetchone()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    return strategy_key.replace("_", " ").title()


def _make_brief_id(strategy_key: str, btype: str, cluster_desc: str) -> str:
    import hashlib
    raw = f"{strategy_key}|{btype}|{cluster_desc}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _find_existing(briefs: list, strategy_key: str, btype: str, cluster_desc: str) -> dict | None:
    for b in briefs:
        if (
            b.get("strategy_key") == strategy_key
            and b.get("type") == btype
            and b.get("evidence", {}).get("cluster_description") == cluster_desc
        ):
            return b
    return None


def _load_feature_thresholds(research_dir: Path) -> tuple[float, float]:
    """Return (trend_score_threshold, atr_pct_threshold) from feature_weights.json."""
    paths_to_try = [
        Path("/opt/mt-learner/feature_weights.json"),
        research_dir.parent / "feature_weights.json",
        research_dir.parent / "models" / "feature_weights.json",
    ]
    for p in paths_to_try:
        if p.exists():
            try:
                with open(p) as f:
                    fw = json.load(f)
                features = fw.get("features", {})
                ts_win = features.get("trend_score", {}).get("win_mean")
                ts_loss = features.get("trend_score", {}).get("loss_mean")
                atr_win = features.get("atr_pct", {}).get("win_mean")
                atr_loss = features.get("atr_pct", {}).get("loss_mean")
                if ts_win is not None and ts_loss is not None:
                    # threshold = midpoint of win_mean and loss_mean
                    ts_thresh = (ts_win + ts_loss) / 2
                    atr_thresh = (atr_win + atr_loss) / 2 if atr_win is not None else 3.5
                    return ts_thresh, atr_thresh
            except Exception:
                pass
    print("[researcher] [TYPE3] feature_weights.json not found — using defaults")
    return 10.0, 3.5  # defaults from prompt


def _check_strategy_already_exists(conn, base_key: str, api_payload: dict) -> str | None:
    """Return the custom strategy key if an equivalent already exists, else None."""
    try:
        cur = conn.cursor()
        cur.execute("SELECT key, config_json FROM custom_strategies WHERE enabled=1")
        for row in cur.fetchall():
            ckey, cfg_json = row
            try:
                cfg = json.loads(cfg_json) if cfg_json else {}
                if cfg.get("base_key") == base_key:
                    # Check parameter overlap — simple heuristic
                    payload_lov = set(api_payload.get("allowed_volatility") or [])
                    cfg_lov = set(cfg.get("allowed_volatility") or [])
                    payload_mc = api_payload.get("min_conviction", 0)
                    cfg_mc = cfg.get("min_conviction", 0)
                    if payload_lov and payload_lov == cfg_lov and abs(payload_mc - cfg_mc) <= 5:
                        return ckey
                    if not payload_lov and not cfg_lov and abs(payload_mc - cfg_mc) <= 5:
                        return ckey
            except Exception:
                pass
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Proposed strategy builders
# ---------------------------------------------------------------------------

def _strategy_factory_contract(
    *,
    name: str,
    base_key: str,
    description: str,
    payload: dict,
    mechanism: str,
    failure_regimes: list[str],
) -> dict:
    """Return a complete shadow-only strategy-variant experiment contract."""
    entry_filters = {
        key: value
        for key, value in payload.items()
        if key not in {"name", "base_key"} and value not in (None, [], {})
    }
    return {
        "name": name,
        "base_key": base_key,
        "description": description,
        "hypothesis": description,
        "mechanism": mechanism,
        "entry_rules": {
            "inherit_base_signal": base_key,
            "additional_filters": entry_filters,
            "no_entry_without_fresh_mt7_signal": True,
        },
        "exit_rules": {
            "inherit_base_ladder_and_stop": True,
            "no_averaging_down": True,
            "max_holding_policy": "paper_config",
        },
        "failure_regimes": failure_regimes or ["unknown", "regime_shift"],
        "data_requirements": [
            "fresh normalized exchange ticker",
            "closed net-P&L Paper outcomes",
            "exact strategy-policy fingerprint",
            "fees and slippage assumptions",
        ],
        "cost_assumptions": {
            "source": "paper_config",
            "net_metrics_required": True,
            "zero_cost_backtest_forbidden": True,
        },
        "control_strategy": base_key,
        "novelty_claim": (
            "This is a constrained strategy variant, not a novel algorithm; "
            "the declared entry filter is the only causal treatment."
        ),
        "falsification_criteria": {
            "minimum_closed_trades": 50,
            "minimum_elapsed_days": 7,
            "max_avg_pnl_delta_pct": -0.25,
            "max_profit_factor": 0.80,
            "max_drawdown_increase_pct": 5.0,
        },
        "promotion_criteria": {
            "minimum_closed_trades": 50,
            "minimum_elapsed_days": 7,
            "min_profit_factor": 1.15,
            "positive_leave_best_out_ev": True,
            "max_drawdown_increase_pct": 2.0,
        },
        "authority": "shadow_only",
        "auto_apply_allowed": False,
        "live_behavior_change_allowed": False,
        "api_payload": payload,
    }


def _proposed_strategy_type1(strategy_key: str, display_name: str, regime: str,
                              baseline_mc: int, direction: str) -> dict | None:
    if direction == "suppression":
        return None  # suppression never gets proposed_strategy

    # Map regime label to volatility filter if applicable
    vol_map = {
        "volatile_squeeze": ["extreme", "high"],
        "high_vol": ["high"],
        "extreme_vol": ["extreme"],
        "low_vol": ["low"],
        "trending_bullish": None,
        "trending_bearish": None,
        "choppy": None,
        "ranging": None,
    }
    allowed_vol = vol_map.get(regime)
    min_mc = max(65, baseline_mc)
    slug = regime.replace("_", "-")
    payload = {
        "name": f"{display_name} ({slug})",
        "base_key": strategy_key,
        "allowed_volatility": allowed_vol,
        "direction_lock": None,
        "min_conviction": min_mc,
    }
    return _strategy_factory_contract(
        name=payload["name"],
        base_key=strategy_key,
        description=f"Focused on {regime} regime where this strategy outperforms baseline.",
        payload=payload,
        mechanism=(
            f"Conditioning {strategy_key} entries on {regime} should isolate the "
            "historically stronger regime cohort."
        ),
        failure_regimes=["regime_shift", "unknown", f"{regime}_edge_decay"],
    )


def _proposed_strategy_type2(strategy_key: str, display_name: str,
                              volatility: str, conv_band: str) -> dict:
    conv_lower = int(conv_band.split("-")[0]) if "-" in conv_band else int(conv_band.rstrip("+"))
    payload = {
        "name": f"{display_name} ({volatility}-vol/{conv_band}c)",
        "base_key": strategy_key,
        "allowed_volatility": [volatility],
        "direction_lock": None,
        "min_conviction": conv_lower,
    }
    return _strategy_factory_contract(
        name=payload["name"],
        base_key=strategy_key,
        description=f"Targets {volatility} volatility with conviction {conv_band} sweet spot.",
        payload=payload,
        mechanism=(
            "The volatility and conviction intersection should remove lower-quality "
            "signals while preserving the identified positive-EV cluster."
        ),
        failure_regimes=["volatility_transition", "conviction_calibration_drift"],
    )


def _proposed_strategy_type3(strategy_key: str, display_name: str) -> dict:
    payload = {
        "name": f"{display_name} (clean setup)",
        "base_key": strategy_key,
        "allowed_volatility": None,
        "direction_lock": None,
        "min_conviction": 75,
    }
    return _strategy_factory_contract(
        name=payload["name"],
        base_key=strategy_key,
        description="Focuses on low trend_score + low atr_pct setups where win rates are higher.",
        payload=payload,
        mechanism=(
            "A higher admission threshold approximates the observed clean-setup "
            "cluster until explicit trend and ATR filters are implemented."
        ),
        failure_regimes=["high_volatility", "strong_trend", "threshold_proxy_failure"],
    )


# ---------------------------------------------------------------------------
# Title generators

# Canonical ATR-based volatility values — the system's actual language (lib/indicators.py volatility_regime())
_ATR_VOLATILITY_REGIMES = ["low", "medium", "high", "extreme"]

# Behavioral pattern labels used for researcher analysis only — never passed as allowed_volatility to the system
_BEHAVIORAL_REGIMES = ["choppy", "low_liquidity"]

# Full set for analysis loops — only _ATR_VOLATILITY_REGIMES goes into api_payload
_ALL_VOLATILITY_REGIMES = _ATR_VOLATILITY_REGIMES + _BEHAVIORAL_REGIMES


def _proposed_strategy_stop_pressure(strategy_key: str, display_name: str, volatility: str) -> dict:
    """Propose a custom strategy clone that excludes the noisy volatility regime."""
    volatility = (volatility or "").strip().lower()
    if volatility not in _ATR_VOLATILITY_REGIMES:
        return None
    allowed = [v for v in _ATR_VOLATILITY_REGIMES if v != volatility]
    payload = {
        "name": f"{display_name} (no {volatility} vol)",
        "base_key": strategy_key,
        "allowed_volatility": allowed,
        "direction_lock": None,
    }
    description = (
        f"Excludes {volatility} volatility where ATR stops are hit by noise "
        f"before real adverse moves ({volatility} vol stop pressure pattern)."
    )
    return _strategy_factory_contract(
        name=payload["name"],
        base_key=strategy_key,
        description=description,
        payload=payload,
        mechanism=(
            f"Removing {volatility} volatility should reduce premature stop-outs "
            "without changing the base strategy's scoring or exits."
        ),
        failure_regimes=["volatility_reclassification", "opportunity_cost"],
    )


# ---------------------------------------------------------------------------

def _title_type1(display_name: str, regime: str, delta_pp: float, direction: str) -> str:
    if direction == "outperformance":
        t = f"{display_name} — {regime.replace('_', ' ').title()} (+{abs(delta_pp):.0f}pp win rate)"
    else:
        t = f"{display_name} — Avoid {regime.replace('_', ' ').title()} ({delta_pp:+.0f}pp win rate)"
    return t[:60]


def _title_type2(display_name: str, volatility: str, conv_band: str) -> str:
    t = f"{display_name} — {volatility.title()} Vol + Conv {conv_band}"
    return t[:60]


def _title_type3(display_name: str, positive: bool) -> str:
    if positive:
        return f"{display_name} — Clean Setup Cluster"[:60]
    return f"{display_name} — High ATR Underperformance"[:60]


def _title_type4(display_name: str, regime: str, avg_capture: float) -> str:
    t = f"{display_name} — {regime.replace('_', ' ').title()} Entry Timing ({avg_capture:.0f}% avg capture)"
    return t[:70]


def _title_type5(display_name: str, volatility: str, avg_stop_pressure: float) -> str:
    t = f"{display_name} — {volatility.title()} Vol Stop Pressure ({avg_stop_pressure:.0f}% avg)"
    return t[:70]


# ---------------------------------------------------------------------------
# Brief assembly
# ---------------------------------------------------------------------------

def _assemble_brief(
    existing: dict | None,
    brief_id: str,
    btype: str,
    strategy_key: str,
    title: str,
    thesis: str,
    what_is_novel: str,
    evidence: dict,
    proposed_strategy,
    now_iso: str,
) -> dict:
    n = evidence["cluster_trades"]
    confidence = _confidence_from_trades(n)

    if existing:
        prev_conf = existing.get("confidence", "watching")
        history = existing.get("confidence_history", [])
        if prev_conf != confidence:
            history.append({"at": now_iso, "confidence": confidence, "trades": n})
        existing.update({
            "last_updated": now_iso,
            "title": title,
            "thesis": thesis,
            "what_is_novel": what_is_novel,
            "evidence": evidence,
            "confidence": confidence,
            "confidence_history": history,
            "status": "active",
            "retirement_reason": None,
        })
        if confidence in ("proposal", "ready") and proposed_strategy:
            existing["proposed_strategy"] = proposed_strategy
        return existing

    return {
        "id": brief_id,
        "type": btype,
        "generated_at": now_iso,
        "last_updated": now_iso,
        "strategy_key": strategy_key,
        "title": title,
        "thesis": thesis,
        "what_is_novel": what_is_novel,
        "evidence": evidence,
        "confidence": confidence,
        "confidence_history": [{"at": now_iso, "confidence": confidence, "trades": n}],
        "watchlist_threshold": 15,
        "status": "active",
        "retirement_reason": None,
        "proposed_strategy": proposed_strategy if confidence in ("proposal", "ready") else None,
    }


# ---------------------------------------------------------------------------
# run_hypothesis_discovery
# ---------------------------------------------------------------------------

def run_hypothesis_discovery(db_path: str, research_dir: Path) -> None:
    print(f"[researcher] run_hypothesis_discovery start, db={db_path}")
    research_dir.mkdir(exist_ok=True)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        existing_data = _load_briefs(research_dir)
        existing_briefs: list = existing_data.get("briefs", [])
        now_iso = _now_iso()
        new_or_updated: list = []
        updated_ids: set = set()

        # ---- Baseline per strategy ----
        cur = conn.cursor()
        cur.execute("""
            SELECT strategy_key,
                   COUNT(*) as n,
                   ROUND(SUM(CASE WHEN result='WIN' THEN 1.0
                                  ELSE 0 END) / COUNT(*), 4) as strict_win_rate,
                   ROUND(SUM(CASE WHEN result IN ('WIN','PARTIAL') THEN 1.0
                                  ELSE 0 END) / COUNT(*), 4) as win_partial_rate,
                   ROUND(SUM(CASE WHEN result='WIN' THEN 1.0
                                  WHEN result='PARTIAL' THEN 0.5
                                  ELSE 0 END) / COUNT(*), 4) as win_rate,
                   ROUND(AVG(pnl_pct), 2) as avg_pnl
            FROM signals
            WHERE result IS NOT NULL
              AND pnl_pct IS NOT NULL
            GROUP BY strategy_key
        """)
        baselines = {row["strategy_key"]: dict(row) for row in cur.fetchall()}

        total_closed = sum(b["n"] for b in baselines.values())

        # ---- TYPE 1: Regime × Strategy edge clusters ----
        cur.execute("""
            SELECT
                strategy_key,
                json_extract(signal_json, '$.agent_regime') as regime,
                COUNT(*) as n,
                ROUND(SUM(CASE WHEN result='WIN' THEN 1.0
                               WHEN result='PARTIAL' THEN 0.5
                               ELSE 0 END) / COUNT(*), 4) as win_rate,
                ROUND(AVG(pnl_pct), 2) as avg_pnl
            FROM signals
            WHERE result IS NOT NULL
              AND pnl_pct IS NOT NULL
              AND json_extract(signal_json, '$.agent_regime') IS NOT NULL
              AND json_extract(signal_json, '$.agent_regime') NOT IN ('unknown', '')
            GROUP BY strategy_key, regime
            HAVING COUNT(*) >= 15
        """)
        for row in cur.fetchall():
            sk = row["strategy_key"]
            regime = row["regime"]
            bl = baselines.get(sk, {})
            if not bl:
                continue
            bl_wr = bl["win_rate"] or 0
            cl_wr = row["win_rate"] or 0
            delta_pp = (cl_wr - bl_wr) * 100
            if abs(delta_pp) < 15:
                continue  # not enough edge

            direction = "outperformance" if delta_pp >= 15 else "suppression"
            display_name = _strategy_display_name(conn, sk)
            cluster_desc = f"regime={regime}"
            brief_id = _make_brief_id(sk, "regime_edge", cluster_desc)
            existing = _find_existing(existing_briefs, sk, "regime_edge", cluster_desc)

            title = _title_type1(display_name, regime, delta_pp, direction)
            thesis = (
                f"{regime.replace('_',' ').title()} regime shows {abs(delta_pp):.0f}pp "
                f"{'higher' if delta_pp > 0 else 'lower'} win rate than {sk} baseline "
                f"({cl_wr*100:.1f}% vs {bl_wr*100:.1f}%) over {row['n']} trades."
            )[:200]

            if direction == "suppression":
                what_is_novel = (
                    f"Consider adding {regime} to the volatility suppress list "
                    f"for {display_name} in the Strategies tab settings."
                )[:200]
            else:
                what_is_novel = (
                    f"Isolating {regime} signals in {display_name} shows avg P&L of "
                    f"{row['avg_pnl']:.1f}% vs baseline {bl['avg_pnl']:.1f}%."
                )[:200]

            evidence = {
                "baseline_win_rate": bl_wr,
                "baseline_avg_pnl": bl["avg_pnl"],
                "baseline_trades": bl["n"],
                "cluster_win_rate": cl_wr,
                "cluster_avg_pnl": row["avg_pnl"],
                "cluster_trades": row["n"],
                "cluster_description": cluster_desc,
            }

            conf = _confidence_from_trades(row["n"])
            proposed = None
            if conf in ("proposal", "ready") and direction == "outperformance":
                existing_mc = bl.get("min_conviction", 65)
                proposed = _proposed_strategy_type1(sk, display_name, regime, existing_mc, direction)
                # Check for duplicate custom strategy
                if proposed:
                    dup_key = _check_strategy_already_exists(conn, sk, proposed["api_payload"])
                    if dup_key:
                        proposed = None
                        # retire brief if dup found
                        b = _assemble_brief(existing, brief_id, "regime_edge", sk,
                                            title, thesis, what_is_novel, evidence, None, now_iso)
                        b["status"] = "retired"
                        b["confidence"] = "retired"
                        b["retirement_reason"] = f"strategy already exists as: {dup_key}"
                        new_or_updated.append(b)
                        updated_ids.add(brief_id)
                        continue

            b = _assemble_brief(existing, brief_id, "regime_edge", sk,
                                title, thesis, what_is_novel, evidence, proposed, now_iso)
            new_or_updated.append(b)
            updated_ids.add(brief_id)

        # ---- TYPE 2: Volatility × Conviction sweet spot ----
        cur.execute("""
            SELECT
                strategy_key,
                volatility,
                CASE WHEN conviction < 65 THEN '55-64'
                     WHEN conviction < 75 THEN '65-74'
                     WHEN conviction < 85 THEN '75-84'
                     ELSE '85+' END as conv_band,
                COUNT(*) as n,
                ROUND(SUM(CASE WHEN result='WIN' THEN 1.0
                               WHEN result='PARTIAL' THEN 0.5
                               ELSE 0 END) / COUNT(*), 4) as win_rate,
                ROUND(AVG(pnl_pct), 2) as avg_pnl
            FROM signals
            WHERE result IS NOT NULL
              AND pnl_pct IS NOT NULL
              AND volatility IS NOT NULL
            GROUP BY strategy_key, volatility, conv_band
            HAVING COUNT(*) >= 20
        """)
        for row in cur.fetchall():
            sk = row["strategy_key"]
            vol = row["volatility"]
            band = row["conv_band"]
            cl_wr = row["win_rate"] or 0
            bl = baselines.get(sk, {})
            if not bl:
                continue
            if cl_wr < 0.55 or (row["avg_pnl"] or 0) <= 5.0:
                continue

            display_name = _strategy_display_name(conn, sk)
            cluster_desc = f"volatility={vol},conv_band={band}"
            brief_id = _make_brief_id(sk, "vol_conviction_sweet_spot", cluster_desc)
            existing = _find_existing(existing_briefs, sk, "vol_conviction_sweet_spot", cluster_desc)

            title = _title_type2(display_name, vol, band)
            bl_wr = bl["win_rate"] or 0
            thesis = (
                f"{vol.title()} volatility + conviction {band} shows {cl_wr*100:.1f}% win rate "
                f"and {row['avg_pnl']:.1f}% avg P&L over {row['n']} trades "
                f"(baseline {bl_wr*100:.1f}%)."
            )[:200]
            what_is_novel = (
                f"This volatility-conviction band outperforms the {display_name} baseline "
                f"by {(cl_wr - bl_wr)*100:.0f}pp win rate with {row['n']} confirmed trades."
            )[:200]

            evidence = {
                "baseline_win_rate": bl_wr,
                "baseline_avg_pnl": bl["avg_pnl"],
                "baseline_trades": bl["n"],
                "cluster_win_rate": cl_wr,
                "cluster_avg_pnl": row["avg_pnl"],
                "cluster_trades": row["n"],
                "cluster_description": cluster_desc,
            }

            conf = _confidence_from_trades(row["n"])
            proposed = None
            if conf in ("proposal", "ready"):
                proposed = _proposed_strategy_type2(sk, display_name, vol, band)
                dup_key = _check_strategy_already_exists(conn, sk, proposed["api_payload"])
                if dup_key:
                    proposed = None

            b = _assemble_brief(existing, brief_id, "vol_conviction_sweet_spot", sk,
                                title, thesis, what_is_novel, evidence, proposed, now_iso)
            new_or_updated.append(b)
            updated_ids.add(brief_id)

        # ---- TYPE 3: Feature divergence (trend_score + atr_pct) ----
        ts_thresh, atr_thresh = _load_feature_thresholds(research_dir)

        cur.execute("""
            SELECT
                strategy_key,
                COUNT(*) as n,
                ROUND(SUM(CASE WHEN result='WIN' THEN 1.0
                               WHEN result='PARTIAL' THEN 0.5
                               ELSE 0 END) / COUNT(*), 4) as win_rate,
                ROUND(AVG(pnl_pct), 2) as avg_pnl,
                'clean' as cluster_type
            FROM signals
            WHERE result IS NOT NULL
              AND pnl_pct IS NOT NULL
              AND COALESCE(trend_score, json_extract(signal_json,'$.trend_score')) < ?
              AND COALESCE(atr_pct, json_extract(signal_json,'$.atr_pct')) < ?
            GROUP BY strategy_key
            HAVING COUNT(*) >= 20
        """, (ts_thresh, atr_thresh))
        clean_rows = {row["strategy_key"]: dict(row) for row in cur.fetchall()}

        cur.execute("""
            SELECT
                strategy_key,
                COUNT(*) as n,
                ROUND(SUM(CASE WHEN result='WIN' THEN 1.0
                               WHEN result='PARTIAL' THEN 0.5
                               ELSE 0 END) / COUNT(*), 4) as win_rate,
                ROUND(AVG(pnl_pct), 2) as avg_pnl
            FROM signals
            WHERE result IS NOT NULL
              AND pnl_pct IS NOT NULL
              AND (COALESCE(trend_score, json_extract(signal_json,'$.trend_score')) >= ?
                   OR COALESCE(atr_pct, json_extract(signal_json,'$.atr_pct')) >= ?)
            GROUP BY strategy_key
            HAVING COUNT(*) >= 20
        """, (ts_thresh, atr_thresh))
        noisy_rows = {row["strategy_key"]: dict(row) for row in cur.fetchall()}

        for sk, clean in clean_rows.items():
            noisy = noisy_rows.get(sk)
            bl = baselines.get(sk, {})
            if not noisy or not bl:
                continue
            cl_wr = clean["win_rate"] or 0
            noisy_wr = noisy["win_rate"] or 0
            if cl_wr - noisy_wr < 0.20:
                continue

            positive = cl_wr >= noisy_wr
            display_name = _strategy_display_name(conn, sk)
            cluster_desc = f"feature_divergence_clean:ts<{ts_thresh:.1f},atr<{atr_thresh:.1f}"
            brief_id = _make_brief_id(sk, "feature_divergence", cluster_desc)
            existing = _find_existing(existing_briefs, sk, "feature_divergence", cluster_desc)

            title = _title_type3(display_name, positive)
            bl_wr = bl["win_rate"] or 0
            thesis = (
                f"Low trend_score (<{ts_thresh:.0f}) + low ATR (<{atr_thresh:.1f}%) cluster shows "
                f"{cl_wr*100:.1f}% win rate vs {noisy_wr*100:.1f}% noisy cluster "
                f"({clean['n']} vs {noisy['n']} trades)."
            )[:200]
            what_is_novel = (
                f"Restricting {display_name} to clean setups (low trend_score + low ATR) "
                f"produces +{(cl_wr - noisy_wr)*100:.0f}pp win rate over noisy setups."
            )[:200]

            evidence = {
                "baseline_win_rate": bl_wr,
                "baseline_avg_pnl": bl["avg_pnl"],
                "baseline_trades": bl["n"],
                "cluster_win_rate": cl_wr,
                "cluster_avg_pnl": clean["avg_pnl"],
                "cluster_trades": clean["n"],
                "cluster_description": cluster_desc,
            }

            conf = _confidence_from_trades(clean["n"])
            proposed = None
            if conf in ("proposal", "ready"):
                proposed = _proposed_strategy_type3(sk, display_name)
                dup_key = _check_strategy_already_exists(conn, sk, proposed["api_payload"])
                if dup_key:
                    proposed = None

            b = _assemble_brief(existing, brief_id, "feature_divergence", sk,
                                title, thesis, what_is_novel, evidence, proposed, now_iso)
            new_or_updated.append(b)
            updated_ids.add(brief_id)

        # ---- TYPE 4: Capture ratio cluster (entry timing problem) ----
        cur.execute("""
            SELECT
                strategy_key,
                COALESCE(json_extract(signal_json, '$.agent_regime'), 'unknown') as regime,
                COUNT(*) as n,
                ROUND(AVG(
                    CAST(json_extract(signal_json, '$.journey_capture_ratio') AS REAL)
                ), 1) as avg_capture,
                ROUND(AVG(pnl_pct), 2) as avg_pnl
            FROM signals
            WHERE result IN ('WIN', 'PARTIAL')
              AND pnl_pct IS NOT NULL
              AND json_extract(signal_json, '$.journey_available') = 1
              AND json_extract(signal_json, '$.journey_capture_ratio') IS NOT NULL
            GROUP BY strategy_key, regime
            HAVING COUNT(*) >= 10
        """)
        for row in cur.fetchall():
            sk = row["strategy_key"]
            regime = row["regime"]
            avg_capture = row["avg_capture"] or 0
            if avg_capture >= 25:
                continue  # not an entry timing problem

            display_name = _strategy_display_name(conn, sk)
            cluster_desc = f"capture_ratio:{sk}x{regime}"
            brief_id = _make_brief_id(sk, "capture_ratio_cluster", cluster_desc)
            existing = _find_existing(existing_briefs, sk, "capture_ratio_cluster", cluster_desc)

            title = _title_type4(display_name, regime, avg_capture)
            n = row["n"]
            thesis = (
                f"Win/partial trades in {regime} regime capture only {avg_capture:.0f}% of "
                f"favorable movement on average ({n} trades). Signal direction is sometimes "
                f"correct but entries are mistimed relative to the move."
            )[:200]
            what_is_novel = (
                f"Standard P&L analysis masks this — a win with near-zero capture ratio "
                f"reveals systematic entry timing failure for {display_name} in {regime} regime. "
                f"Consider raising min_conviction for this regime to filter for higher-quality setups."
            )[:200]

            bl = baselines.get(sk, {})
            evidence = {
                "baseline_win_rate": bl.get("win_rate"),
                "baseline_avg_pnl": bl.get("avg_pnl"),
                "baseline_trades": bl.get("n"),
                "cluster_win_rate": None,  # TYPE 4 is capture-ratio, not win-rate, cluster
                "cluster_avg_pnl": row["avg_pnl"],
                "cluster_trades": n,
                "cluster_description": cluster_desc,
                "avg_capture_ratio": avg_capture,
            }

            confidence = _confidence_from_journey_trades(n)
            b = _assemble_brief(existing, brief_id, "capture_ratio_cluster", sk,
                                title, thesis, what_is_novel, evidence, None, now_iso)
            # Force correct confidence (uses journey thresholds, not standard)
            b["confidence"] = confidence
            new_or_updated.append(b)
            updated_ids.add(brief_id)

        # ---- TYPE 5: Stop pressure pattern (stop too tight) ----
        cur.execute("""
            SELECT
                strategy_key,
                volatility,
                COUNT(*) as n,
                ROUND(AVG(
                    CAST(json_extract(signal_json, '$.journey_stop_pressure') AS REAL)
                ), 1) as avg_stop_pressure,
                ROUND(AVG(
                    CAST(json_extract(signal_json, '$.journey_mfe_pct') AS REAL)
                ), 2) as avg_mfe,
                SUM(CASE WHEN json_extract(signal_json, '$.journey_path_label') = 'failed_fast'
                    THEN 1 ELSE 0 END) as failed_fast_count
            FROM signals
            WHERE result = 'LOSS'
              AND pnl_pct IS NOT NULL
              AND json_extract(signal_json, '$.journey_available') = 1
              AND json_extract(signal_json, '$.journey_stop_pressure') IS NOT NULL
            GROUP BY strategy_key, volatility
            HAVING COUNT(*) >= 10
        """)
        stop_rows = [dict(r) for r in cur.fetchall()]
        for row in stop_rows:
            sk = row["strategy_key"]
            volatility = row["volatility"] or "unknown"
            avg_stop_pressure = row["avg_stop_pressure"] or 0
            if avg_stop_pressure < 80:
                continue  # not a stop pressure problem

            display_name = _strategy_display_name(conn, sk)
            cluster_desc = f"stop_pressure:{sk}x{volatility}"
            brief_id = _make_brief_id(sk, "stop_pressure_pattern", cluster_desc)
            existing = _find_existing(existing_briefs, sk, "stop_pressure_pattern", cluster_desc)

            title = _title_type5(display_name, volatility, avg_stop_pressure)
            n = row["n"]
            failed_fast = row["failed_fast_count"] or 0
            thesis = (
                f"Losing trades in {volatility} volatility use {avg_stop_pressure:.0f}% of the "
                f"planned stop distance on average ({n} losses). {failed_fast} of {n} are "
                f"'failed_fast' (< 1% MFE before stop). Stop distance may be too tight."
            )[:200]
            what_is_novel = (
                f"The ATR-based stop uses a fixed multiplier regardless of regime. In {volatility} "
                f"volatility, normal price noise may exceed the stop before a real adverse move "
                f"— producing losses where direction was not necessarily wrong. "
                f"Consider wider stop multiplier for {volatility} vol in P9 execution engine."
            )[:200]

            bl = baselines.get(sk, {})
            all_vol_row = conn.execute("""
                SELECT
                    COUNT(*) as n,
                    ROUND(SUM(CASE WHEN result='WIN' THEN 1.0 ELSE 0 END) / COUNT(*), 4) as strict_win_rate,
                    ROUND(SUM(CASE WHEN result IN ('WIN','PARTIAL') THEN 1.0 ELSE 0 END) / COUNT(*), 4) as win_partial_rate,
                    ROUND(SUM(CASE WHEN result='WIN' THEN 1.0
                                   WHEN result='PARTIAL' THEN 0.5
                                   ELSE 0 END) / COUNT(*), 4) as win_score,
                    ROUND(AVG(pnl_pct), 2) as avg_pnl
                FROM signals
                WHERE strategy_key = ?
                  AND COALESCE(volatility, 'unknown') = ?
                  AND result IS NOT NULL
                  AND pnl_pct IS NOT NULL
            """, (sk, volatility))
            all_vol = dict(all_vol_row.fetchone() or {})
            evidence = {
                "baseline_win_rate": bl.get("win_rate"),
                "baseline_strict_win_rate": bl.get("strict_win_rate"),
                "baseline_win_partial_rate": bl.get("win_partial_rate"),
                "baseline_avg_pnl": bl.get("avg_pnl"),
                "baseline_trades": bl.get("n"),
                "cluster_win_rate": all_vol.get("win_score"),
                "cluster_strict_win_rate": all_vol.get("strict_win_rate"),
                "cluster_win_partial_rate": all_vol.get("win_partial_rate"),
                "cluster_avg_pnl": all_vol.get("avg_pnl"),
                "cluster_trades": all_vol.get("n") or n,
                "cluster_description": cluster_desc,
                "stop_pressure_loss_count": n,
                "avg_stop_pressure": avg_stop_pressure,
                "avg_mfe_before_stop": row["avg_mfe"],
                "failed_fast_count": failed_fast,
            }

            confidence = _confidence_from_journey_trades(n)
            proposed = None
            if confidence in ("proposal", "ready"):
                proposed = _proposed_strategy_stop_pressure(sk, display_name, volatility)
                if proposed:
                    dup_key = _check_strategy_already_exists(conn, sk, proposed["api_payload"])
                    if dup_key:
                        proposed = None
            b = _assemble_brief(existing, brief_id, "stop_pressure_pattern", sk,
                                title, thesis, what_is_novel, evidence, proposed, now_iso)
            b["confidence"] = confidence
            new_or_updated.append(b)
            updated_ids.add(brief_id)


        # ---- TYPE 6: Order flow confirmation × win rate ----
        # Measures whether signals with flow_confirmed=1 (tape + book aligned)
        # outperform unconfirmed signals. Only runs once >= 20 closed signals
        # have flow data on each side.
        try:
            cur.execute("""
                SELECT
                    flow_confirmed,
                    COUNT(*) as n,
                    SUM(CASE WHEN result IN ('WIN','PARTIAL') THEN 1.0 ELSE 0 END) / COUNT(*) as win_rate,
                    AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as avg_pnl
                FROM signals
                WHERE result IS NOT NULL
                  AND result NOT IN ('EXPIRED','SKIPPED')
                  AND flow_confirmed IS NOT NULL
                GROUP BY flow_confirmed
                HAVING COUNT(*) >= 20
            """)
            flow_rows = {int(r[0]): {"n": r[1], "win_rate": round(r[2], 4), "avg_pnl": round(r[3], 2)} for r in cur.fetchall()}
            confirmed_data   = flow_rows.get(1)
            unconfirmed_data = flow_rows.get(0)

            if confirmed_data and unconfirmed_data:
                delta_pp = (confirmed_data["win_rate"] - unconfirmed_data["win_rate"]) * 100
                total_n  = confirmed_data["n"] + unconfirmed_data["n"]

                if abs(delta_pp) >= 10:
                    brief_id  = "flow_confirmation_edge"
                    direction = "outperformance" if delta_pp >= 10 else "suppression"
                    title = (
                        f"Order Flow Confirmation — {abs(delta_pp):.0f}pp {'higher' if delta_pp > 0 else 'lower'} win rate"
                    )
                    thesis = (
                        f"Signals where tape delta and order book depth confirmed the trade direction "
                        f"(flow_confirmed=1) show a {abs(delta_pp):.0f}pp {'better' if delta_pp > 0 else 'worse'} "
                        f"win rate vs unconfirmed signals. "
                        f"Confirmed: {confirmed_data['win_rate']*100:.1f}% ({confirmed_data['n']} trades). "
                        f"Unconfirmed: {unconfirmed_data['win_rate']*100:.1f}% ({unconfirmed_data['n']} trades)."
                    )
                    what_is_novel = (
                        f"Flow confirmation {'adds' if delta_pp > 0 else 'subtracts'} {abs(delta_pp):.0f}pp to win rate. "
                        f"This suggests {'adding flow confirmation as a pre-entry filter' if delta_pp > 0 else 'flow confirmation is a noise signal here — consider removing it as a gate'}."
                    )
                    confidence = _confidence_from_trades(min(confirmed_data["n"], unconfirmed_data["n"]))
                    evidence = {
                        "baseline_win_rate": unconfirmed_data["win_rate"],
                        "baseline_avg_pnl":  unconfirmed_data["avg_pnl"],
                        "baseline_trades":   unconfirmed_data["n"],
                        "cluster_win_rate":  confirmed_data["win_rate"],
                        "cluster_avg_pnl":   confirmed_data["avg_pnl"],
                        "cluster_trades":    confirmed_data["n"],
                        "delta_pp":          round(delta_pp, 1),
                        "total_flow_signals": total_n,
                    }
                    proposed = None
                    if confidence in ("proposal", "ready") and delta_pp >= 10:
                        proposed = {
                            "type": "filter_add",
                            "description": "Require flow_confirmed=1 before entry",
                            "api_payload": {"flow_required": True, "min_flow_score": 50},
                        }
                    existing = next((b for b in existing_briefs if b.get("id") == brief_id), None)
                    b = _assemble_brief(existing, brief_id, "flow_confirmation_edge", "all",
                                        title, thesis, what_is_novel, evidence, proposed, now_iso)
                    b["confidence"] = confidence
                    new_or_updated.append(b)
                    updated_ids.add(brief_id)
        except Exception as _e6:
            print(f"[researcher] TYPE 6 error: {_e6}")

        # Merge: keep existing briefs not updated, plus new/updated
        retained = [b for b in existing_briefs if b.get("id") not in updated_ids]
        all_briefs = retained + new_or_updated
        active_count = sum(1 for b in all_briefs if b.get("status") == "active")

        output = {
            "generated_at": now_iso,
            "learner_version": LEARNER_VERSION,
            "total_signals_analyzed": total_closed,
            "active_briefs": active_count,
            "briefs": all_briefs,
        }
        _write_briefs(research_dir, output)
        print(
            f"[researcher] done — {len(all_briefs)} briefs total, "
            f"{active_count} active, {len(new_or_updated)} new/updated"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# run_brief_reeval
# ---------------------------------------------------------------------------

def run_brief_reeval(db_path: str, research_dir: Path) -> None:
    print(f"[researcher] run_brief_reeval start")
    existing_data = _load_briefs(research_dir)
    briefs: list = existing_data.get("briefs", [])
    if not briefs:
        print("[researcher] no briefs to re-evaluate")
        return

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    now_iso = _now_iso()
    retired_count = 0
    updated_count = 0

    try:
        cur = conn.cursor()
        # Recompute baseline
        cur.execute("""
            SELECT strategy_key,
                   COUNT(*) as n,
                   ROUND(SUM(CASE WHEN result='WIN' THEN 1.0
                                  WHEN result='PARTIAL' THEN 0.5
                                  ELSE 0 END) / COUNT(*), 4) as win_rate,
                   ROUND(AVG(pnl_pct), 2) as avg_pnl
            FROM signals
            WHERE result IS NOT NULL AND pnl_pct IS NOT NULL
            GROUP BY strategy_key
        """)
        baselines = {row["strategy_key"]: dict(row) for row in cur.fetchall()}

        ts_thresh, atr_thresh = _load_feature_thresholds(research_dir)

        for brief in briefs:
            if brief.get("status") == "retired":
                continue

            sk = brief.get("strategy_key", "")
            btype = brief.get("type", "")
            cluster_desc = brief.get("evidence", {}).get("cluster_description", "")
            bl = baselines.get(sk, {})
            if not bl:
                continue

            try:
                new_ev = None

                if btype == "regime_edge":
                    regime = cluster_desc.replace("regime=", "")
                    cur.execute("""
                        SELECT COUNT(*) as n,
                               ROUND(SUM(CASE WHEN result='WIN' THEN 1.0
                                              WHEN result='PARTIAL' THEN 0.5
                                              ELSE 0 END) / COUNT(*), 4) as win_rate,
                               ROUND(AVG(pnl_pct), 2) as avg_pnl
                        FROM signals
                        WHERE result IS NOT NULL AND pnl_pct IS NOT NULL
                          AND strategy_key=?
                          AND json_extract(signal_json,'$.agent_regime')=?
                    """, (sk, regime))
                    row = cur.fetchone()
                    if row and row["n"] >= 15:
                        new_ev = dict(row)

                elif btype == "vol_conviction_sweet_spot":
                    parts = dict(p.split("=") for p in cluster_desc.split(",") if "=" in p)
                    vol = parts.get("volatility", "")
                    band = parts.get("conv_band", "65-74")
                    band_lower = int(band.split("-")[0]) if "-" in band else int(band.rstrip("+"))
                    band_upper = int(band.split("-")[1]) if "-" in band else 100
                    cur.execute("""
                        SELECT COUNT(*) as n,
                               ROUND(SUM(CASE WHEN result='WIN' THEN 1.0
                                              WHEN result='PARTIAL' THEN 0.5
                                              ELSE 0 END) / COUNT(*), 4) as win_rate,
                               ROUND(AVG(pnl_pct), 2) as avg_pnl
                        FROM signals
                        WHERE result IS NOT NULL AND pnl_pct IS NOT NULL
                          AND strategy_key=? AND volatility=?
                          AND conviction >= ? AND conviction < ?
                    """, (sk, vol, band_lower, band_upper if band_upper < 100 else 200))
                    row = cur.fetchone()
                    if row and row["n"] >= 20:
                        new_ev = dict(row)

                elif btype == "feature_divergence":
                    cur.execute("""
                        SELECT COUNT(*) as n,
                               ROUND(SUM(CASE WHEN result='WIN' THEN 1.0
                                              WHEN result='PARTIAL' THEN 0.5
                                              ELSE 0 END) / COUNT(*), 4) as win_rate,
                               ROUND(AVG(pnl_pct), 2) as avg_pnl
                        FROM signals
                        WHERE result IS NOT NULL AND pnl_pct IS NOT NULL
                          AND strategy_key=?
                          AND COALESCE(trend_score, json_extract(signal_json,'$.trend_score')) < ?
                          AND COALESCE(atr_pct, json_extract(signal_json,'$.atr_pct')) < ?
                    """, (sk, ts_thresh, atr_thresh))
                    row = cur.fetchone()
                    if row and row["n"] >= 20:
                        new_ev = dict(row)

                elif btype == "capture_ratio_cluster":
                    cur.execute("""
                        SELECT COUNT(*) as n,
                               ROUND(AVG(CAST(json_extract(signal_json, '$.journey_capture_ratio') AS REAL)), 1) as avg_capture,
                               ROUND(AVG(pnl_pct), 2) as avg_pnl
                        FROM signals
                        WHERE result IN ('WIN', 'PARTIAL')
                          AND pnl_pct IS NOT NULL
                          AND strategy_key=?
                          AND json_extract(signal_json, '$.journey_available') = 1
                          AND json_extract(signal_json, '$.journey_capture_ratio') IS NOT NULL
                    """, (sk,))
                    row = cur.fetchone()
                    if row and row["n"] >= 10:
                        avg_capture = row["avg_capture"] or 0
                        # Retire if capture ratio improved above 35%
                        if avg_capture >= 35:
                            brief["status"] = "retired"
                            brief["confidence"] = "retired"
                            brief["retirement_reason"] = f"Capture ratio improved to {avg_capture:.0f}% on re-evaluation with {row['n']} trades"
                            retired_count += 1
                        else:
                            new_ev = {"n": row["n"], "win_rate": None, "avg_pnl": row["avg_pnl"]}
                            old_ev = brief.get("evidence", {})
                            old_ev["avg_capture_ratio"] = avg_capture
                            old_ev["cluster_trades"] = row["n"]
                            brief["evidence"] = old_ev
                            conf = _confidence_from_journey_trades(row["n"])
                            brief["confidence"] = conf
                        brief["last_updated"] = now_iso
                        updated_count += 1
                    continue

                elif btype == "stop_pressure_pattern":
                    cur.execute("""
                        SELECT COUNT(*) as n,
                               ROUND(AVG(CAST(json_extract(signal_json, '$.journey_stop_pressure') AS REAL)), 1) as avg_stop_pressure,
                               ROUND(AVG(pnl_pct), 2) as avg_pnl
                        FROM signals
                        WHERE result = 'LOSS'
                          AND pnl_pct IS NOT NULL
                          AND strategy_key=?
                          AND json_extract(signal_json, '$.journey_available') = 1
                          AND json_extract(signal_json, '$.journey_stop_pressure') IS NOT NULL
                    """, (sk,))
                    row = cur.fetchone()
                    if row and row["n"] >= 10:
                        avg_sp = row["avg_stop_pressure"] or 0
                        # Retire if stop pressure improved below 70%
                        if avg_sp < 70:
                            brief["status"] = "retired"
                            brief["confidence"] = "retired"
                            brief["retirement_reason"] = f"Stop pressure improved to {avg_sp:.0f}% on re-evaluation with {row['n']} trades"
                            retired_count += 1
                        else:
                            old_ev = brief.get("evidence", {})
                            old_ev["avg_stop_pressure"] = avg_sp
                            old_ev["cluster_trades"] = row["n"]
                            brief["evidence"] = old_ev
                            conf = _confidence_from_journey_trades(row["n"])
                            brief["confidence"] = conf
                            # Regenerate the full experiment contract. Legacy
                            # proposed_strategy payloads were only renamed
                            # config clones and are not valid factory inputs.
                            if conf in ("proposal", "ready"):
                                try:
                                    cdesc = brief.get("evidence", {}).get("cluster_description", "")
                                    prefix = f"stop_pressure:{sk}x"
                                    volatility = cdesc[len(prefix):] if cdesc.startswith(prefix) else ""
                                    if volatility:
                                        display_name = _strategy_display_name(conn, sk)
                                        proposed = _proposed_strategy_stop_pressure(sk, display_name, volatility)
                                        dup_key = _check_strategy_already_exists(conn, sk, proposed["api_payload"]) if proposed else None
                                        if dup_key:
                                            brief["proposed_strategy"] = None
                                            brief["status"] = "retired"
                                            brief["confidence"] = "retired"
                                            brief["retirement_reason"] = (
                                                f"strategy already exists as: {dup_key}"
                                            )
                                            retired_count += 1
                                        elif proposed:
                                            brief["proposed_strategy"] = proposed
                                except Exception:
                                    pass
                        brief["last_updated"] = now_iso
                        updated_count += 1
                    continue

                if new_ev is None:
                    continue

                old_ev = brief.get("evidence", {})
                prev_n = old_ev.get("cluster_trades", 0)
                new_n = new_ev.get("n", 0)
                new_wr = new_ev.get("win_rate") or 0
                bl_wr = bl.get("win_rate") or 0
                conf = _confidence_from_trades(new_n)

                brief["evidence"] = {
                    **old_ev,
                    "baseline_win_rate": bl_wr,
                    "baseline_avg_pnl": bl.get("avg_pnl"),
                    "baseline_trades": bl.get("n"),
                    "cluster_win_rate": new_wr,
                    "cluster_avg_pnl": new_ev.get("avg_pnl"),
                    "cluster_trades": new_n,
                }
                brief["last_updated"] = now_iso
                history = brief.get("confidence_history", [])

                # Retirement check — edge narrowed on two consecutive evals
                prev_conf = brief.get("confidence", "watching")
                if (
                    prev_conf in ("emerging", "proposal", "ready")
                    and new_wr < (bl_wr + 0.05)
                    and new_n >= prev_n + 10
                ):
                    # Check previous entry in history
                    if len(history) >= 1:
                        prev_hist_conf = history[-1].get("confidence", "watching")
                        # If the last recorded confidence was already below emerging after improvement
                        # we approximate "two consecutive weak" by checking win_rate
                        prev_trades_at_hist = history[-1].get("trades", 0)
                        if prev_trades_at_hist < new_n and prev_hist_conf in ("emerging", "proposal", "ready"):
                            # Two consecutive: current weak + we were emerging+ before
                            # Retire only if this is the second consecutive weak reading
                            if len(history) >= 2:
                                last_two_weak = all(
                                    h.get("confidence") in ("watching",) or True  # approximate
                                    for h in history[-2:]
                                )
                                # Simple: if edge stayed weak (win_rate < bl+0.05) for 10+ new trades
                                brief["status"] = "retired"
                                brief["confidence"] = "retired"
                                brief["retirement_reason"] = (
                                    f"Edge narrowed to baseline on re-evaluation "
                                    f"with {new_n} total trades"
                                )
                                retired_count += 1
                                history.append({"at": now_iso, "confidence": "retired", "trades": new_n})
                                brief["confidence_history"] = history
                                updated_count += 1
                                continue

                if prev_conf != conf:
                    history.append({"at": now_iso, "confidence": conf, "trades": new_n})
                brief["confidence"] = conf
                brief["confidence_history"] = history
                updated_count += 1

            except Exception as e:
                print(f"[researcher] reeval error for brief {brief.get('id')}: {e}")

    finally:
        conn.close()

    active_count = sum(1 for b in briefs if b.get("status") == "active")
    existing_data["briefs"] = briefs
    existing_data["active_briefs"] = active_count
    existing_data["generated_at"] = now_iso
    _write_briefs(research_dir, existing_data)
    print(
        f"[researcher] reeval done — {updated_count} updated, "
        f"{retired_count} retired, {active_count} active"
    )
