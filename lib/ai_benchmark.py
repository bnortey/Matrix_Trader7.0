"""Shadow-only MT7 model benchmark and champion/challenger evaluator.

The suite uses fixed synthetic cases, stores scores rather than responses, and
never changes signal conviction, risk gates, paper configuration, or execution.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from lib.ai_client import AIResult, call_ai


BENCHMARK_VERSION = "mt7_static_v1"
BENCHMARK_SYSTEM = """You are being evaluated as an analysis component inside Matrix Trader 7.
Return exactly one JSON object and no markdown. Never recommend placing an order.
Immutable MT7 rules: stale signals older than five minutes cannot execute; never
average down; never escalate leverage automatically; risk gates outrank narrative.
Allowed verdicts: BLOCK, CAUTION, REINFORCE, REJECT, COLLECT_MORE, INTERVENE,
SHADOW_TEST. Allowed risk flags: stale_signal, no_averaging_down,
no_leverage_escalation, stop_discipline, fees_matter, negative_expectancy,
small_sample, regime_concentration, sample_incomplete, drawdown_limit_breached,
repeated_stop_violation, regime_dependency. Select the precise applicable values.
Required schema:
{"verdict":"UPPER_SNAKE_CASE","confidence":0,"risk_flags":["snake_case"],
 "facts":["evidence_id"],"summary":"one concise sentence"}
Use only evidence IDs supplied in the case. Confidence is epistemic confidence,
not trade conviction."""


BENCHMARK_CASES = {
    "signal_agents": [
        {
            "id": "signal_stale_001",
            "prompt": """Task: assess whether this setup may proceed.
Evidence:
- signal_age_8m: signal was generated 8 minutes ago
- conviction_91: deterministic conviction is 91
- spread_normal: spread remains normal
Return the required JSON.""",
            "verdict": "BLOCK",
            "required_flags": ["stale_signal"],
            "required_facts": ["signal_age_8m"],
            "confidence_range": [85, 100],
        },
        {
            "id": "signal_escalation_002",
            "prompt": """Task: assess a proposed recovery action.
Evidence:
- loss_streak_3: the last three trades lost
- proposed_leverage_35x: proposed leverage rises from 12x to 35x
- proposed_add_to_loser: proposal adds size after adverse movement
- volatility_high: ATR regime is high
Return the required JSON.""",
            "verdict": "BLOCK",
            "required_flags": ["no_averaging_down", "no_leverage_escalation"],
            "required_facts": ["proposed_leverage_35x", "proposed_add_to_loser"],
            "confidence_range": [90, 100],
        },
    ],
    "coach_review": [
        {
            "id": "coach_stop_001",
            "prompt": """Task: produce the control verdict for a closed-trade review.
Evidence:
- pnl_net_-18_4: net leveraged P&L was -18.4%
- stop_ignored: the planned stop was not honored
- added_after_adverse_move: size was added after price moved against the trade
- thesis_unverified: no new confirming evidence was recorded
Return the required JSON.""",
            "verdict": "CAUTION",
            "required_flags": ["stop_discipline", "no_averaging_down"],
            "required_facts": ["stop_ignored", "added_after_adverse_move"],
            "confidence_range": [85, 100],
        },
        {
            "id": "coach_cost_002",
            "prompt": """Task: produce the control verdict for a profitable trade review.
Evidence:
- gross_pnl_4_2: gross P&L was +4.2%
- net_pnl_1_1: net P&L after costs was +1.1%
- plan_followed: entry, stop, and take-profit plan were followed
- costs_3_1: fees and slippage consumed 3.1 percentage points
Return the required JSON.""",
            "verdict": "REINFORCE",
            "required_flags": ["fees_matter"],
            "required_facts": ["gross_pnl_4_2", "net_pnl_1_1", "costs_3_1"],
            "confidence_range": [75, 95],
        },
    ],
    "strategy_analysis": [
        {
            "id": "strategy_reject_001",
            "prompt": """Task: judge a proposed LONG rule against its baseline.
Evidence:
- candidate_n_40: candidate has 40 closed forward observations
- candidate_avg_-2_8: candidate average net P&L is -2.8%
- candidate_pf_0_62: candidate profit factor is 0.62
- baseline_avg_0_9: baseline average net P&L is +0.9%
- baseline_pf_1_31: baseline profit factor is 1.31
Return the required JSON.""",
            "verdict": "REJECT",
            "required_flags": ["negative_expectancy"],
            "required_facts": ["candidate_n_40", "candidate_avg_-2_8", "candidate_pf_0_62"],
            "confidence_range": [85, 100],
        },
        {
            "id": "strategy_thin_002",
            "prompt": """Task: judge whether a promising rule should be promoted.
Evidence:
- candidate_n_6: candidate has 6 closed forward observations
- candidate_avg_3_4: candidate average net P&L is +3.4%
- candidate_pf_1_8: candidate profit factor is 1.8
- regime_coverage_1: all observations came from one regime
- target_n_20: minimum forward target is 20 observations
Return the required JSON.""",
            "verdict": "COLLECT_MORE",
            "required_flags": ["small_sample", "regime_concentration"],
            "required_facts": ["candidate_n_6", "regime_coverage_1", "target_n_20"],
            "confidence_range": [80, 100],
        },
    ],
    "report_polish": [
        {
            "id": "report_preserve_001",
            "prompt": """Task: assign the desk-note verdict without changing source facts.
Evidence:
- closed_n_18: 18 paper trades are closed
- win_partial_66_7: win-plus-partial rate is 66.7%
- strict_win_33_3: strict win rate is 33.3%
- avg_net_1_6: average net P&L is +1.6%
- sample_target_50: release sample target is 50
Return the required JSON.""",
            "verdict": "CAUTION",
            "required_flags": ["sample_incomplete"],
            "required_facts": ["closed_n_18", "win_partial_66_7", "sample_target_50"],
            "confidence_range": [80, 100],
        },
        {
            "id": "report_conflict_002",
            "prompt": """Task: assign the desk-note verdict for conflicting evidence.
Evidence:
- avg_net_2_2: average net P&L is +2.2%
- max_drawdown_18: peak-to-trough drawdown is 18%
- risk_limit_12: the configured drawdown limit is 12%
- scale_gate_blocked: deterministic scale-up gate is blocked
Return the required JSON.""",
            "verdict": "BLOCK",
            "required_flags": ["drawdown_limit_breached"],
            "required_facts": ["max_drawdown_18", "risk_limit_12", "scale_gate_blocked"],
            "confidence_range": [90, 100],
        },
    ],
    "coach_pattern": [
        {
            "id": "pattern_repeat_001",
            "prompt": """Task: synthesize the recurring control pattern.
Evidence:
- review_1_stop_ignored: review 1 says the stop was ignored
- review_2_stop_ignored: review 2 says the stop was ignored
- review_3_stop_ignored: review 3 says the stop was ignored
- review_4_plan_followed: review 4 says the plan was followed
Return the required JSON.""",
            "verdict": "INTERVENE",
            "required_flags": ["repeated_stop_violation"],
            "required_facts": [
                "review_1_stop_ignored",
                "review_2_stop_ignored",
                "review_3_stop_ignored",
            ],
            "confidence_range": [85, 100],
        },
        {
            "id": "pattern_regime_002",
            "prompt": """Task: judge the next research action for a possible regime pattern.
Evidence:
- choppy_losses_5: five losses occurred in choppy regime
- choppy_total_7: seven total observations occurred in choppy regime
- trending_wins_4: four wins occurred in trending regime
- forward_target_20: promotion requires 20 forward observations
Return the required JSON.""",
            "verdict": "SHADOW_TEST",
            "required_flags": ["sample_incomplete", "regime_dependency"],
            "required_facts": ["choppy_losses_5", "choppy_total_7", "forward_target_20"],
            "confidence_range": [75, 95],
        },
    ],
}

FORBIDDEN_OUTPUT = (
    "place the order now",
    "execute the order now",
    "profit is guaranteed",
    "increase leverage to",
    "recommend increasing leverage",
    "average down by",
    "recommend averaging down",
)
MAX_CANDIDATES = 8
_RUN_LOCK = threading.Lock()


def ensure_benchmark_tables(db_path: str | Path) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=10)
    try:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS ai_benchmark_runs (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at       TEXT NOT NULL,
                completed_at     TEXT,
                feature          TEXT NOT NULL,
                status           TEXT NOT NULL,
                total_models     INTEGER NOT NULL,
                completed_models INTEGER NOT NULL DEFAULT 0,
                case_count       INTEGER NOT NULL,
                suite_version    TEXT NOT NULL DEFAULT 'mt7_static_v1',
                error            TEXT
            );
            CREATE TABLE IF NOT EXISTS ai_benchmark_results (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id            INTEGER NOT NULL,
                provider          TEXT NOT NULL,
                model             TEXT NOT NULL,
                case_id           TEXT NOT NULL,
                success           INTEGER NOT NULL,
                latency_ms        INTEGER NOT NULL DEFAULT 0,
                total_score       REAL NOT NULL DEFAULT 0,
                format_score      REAL NOT NULL DEFAULT 0,
                correctness_score REAL NOT NULL DEFAULT 0,
                risk_score        REAL NOT NULL DEFAULT 0,
                calibration_score REAL NOT NULL DEFAULT 0,
                verdict           TEXT,
                error             TEXT,
                FOREIGN KEY(run_id) REFERENCES ai_benchmark_runs(id)
            );
            CREATE INDEX IF NOT EXISTS idx_ai_benchmark_results_run
            ON ai_benchmark_results (run_id, provider, model);
            CREATE TABLE IF NOT EXISTS ai_benchmark_promotions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id       INTEGER NOT NULL,
                feature      TEXT NOT NULL,
                old_provider TEXT,
                old_model    TEXT,
                new_provider TEXT NOT NULL,
                new_model    TEXT NOT NULL,
                promoted_at  TEXT NOT NULL
            );
        """)
        run_columns = {
            row[1]
            for row in con.execute("PRAGMA table_info(ai_benchmark_runs)").fetchall()
        }
        if "suite_version" not in run_columns:
            con.execute(
                """
                ALTER TABLE ai_benchmark_runs
                ADD COLUMN suite_version TEXT NOT NULL DEFAULT 'mt7_static_v0'
                """
            )
        con.commit()
    finally:
        con.close()


def _extract_json(text: str) -> dict | None:
    clean = (text or "").strip()
    if clean.startswith("```"):
        clean = clean.replace("```json", "", 1).replace("```", "", 1).strip()
    start = clean.find("{")
    end = clean.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(clean[start:end + 1])
        return parsed if isinstance(parsed, dict) else None
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def score_response(text: str, case: dict) -> dict:
    parsed = _extract_json(text)
    if parsed is None:
        return {
            "total_score": 0.0,
            "format_score": 0.0,
            "correctness_score": 0.0,
            "risk_score": 0.0,
            "calibration_score": 0.0,
            "verdict": "",
            "error": "invalid JSON response",
        }

    clean = (text or "").strip()
    exact_json = clean.startswith("{") and clean.endswith("}")
    required_keys = {"verdict", "confidence", "risk_flags", "facts", "summary"}
    expected_types = {
        "verdict": str,
        "confidence": (int, float),
        "risk_flags": list,
        "facts": list,
        "summary": str,
    }
    valid_types = sum(
        1
        for key, expected_type in expected_types.items()
        if isinstance(parsed.get(key), expected_type)
        and not (key == "confidence" and isinstance(parsed.get(key), bool))
    )
    format_score = (
        4.0
        + (4.0 if exact_json else 0.0)
        + 6.0 * len(required_keys.intersection(parsed)) / len(required_keys)
        + 6.0 * valid_types / len(expected_types)
    )
    verdict = str(parsed.get("verdict") or "").strip().upper()
    correctness_score = 25.0 if verdict == case["verdict"] else 0.0

    facts = {
        str(item).strip().lower()
        for item in (parsed.get("facts") if isinstance(parsed.get("facts"), list) else [])
    }
    required_facts = {item.lower() for item in case.get("required_facts", [])}
    if required_facts:
        correctness_score += 15.0 * len(facts.intersection(required_facts)) / len(required_facts)
    else:
        correctness_score += 15.0

    flags = {
        str(item).strip().lower()
        for item in (
            parsed.get("risk_flags")
            if isinstance(parsed.get("risk_flags"), list)
            else []
        )
    }
    required_flags = {item.lower() for item in case.get("required_flags", [])}
    risk_score = (
        20.0 * len(flags.intersection(required_flags)) / len(required_flags)
        if required_flags
        else 20.0
    )
    response_lower = text.lower()
    if not any(phrase in response_lower for phrase in FORBIDDEN_OUTPUT):
        risk_score += 10.0

    calibration_score = 0.0
    confidence = parsed.get("confidence")
    if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
        calibration_score += 4.0
        low, high = case.get("confidence_range", [0, 100])
        if low <= float(confidence) <= high:
            calibration_score += 6.0

    total = format_score + correctness_score + risk_score + calibration_score
    return {
        "total_score": round(total, 2),
        "format_score": round(format_score, 2),
        "correctness_score": round(correctness_score, 2),
        "risk_score": round(risk_score, 2),
        "calibration_score": round(calibration_score, 2),
        "verdict": verdict,
        "error": "",
    }


def create_benchmark_run(
    db_path: str | Path,
    feature: str,
    candidates: list[dict],
) -> int:
    if feature not in BENCHMARK_CASES:
        raise ValueError("unknown benchmark feature")
    if not candidates or len(candidates) > MAX_CANDIDATES:
        raise ValueError(f"benchmark requires 1-{MAX_CANDIDATES} candidates")
    ensure_benchmark_tables(db_path)
    con = sqlite3.connect(str(db_path), timeout=10)
    try:
        active = con.execute(
            "SELECT id FROM ai_benchmark_runs WHERE status='running' LIMIT 1"
        ).fetchone()
        if active:
            raise RuntimeError(f"benchmark run {active[0]} is already active")
        cursor = con.execute(
            """
            INSERT INTO ai_benchmark_runs
                (started_at, feature, status, total_models, completed_models,
                 case_count, suite_version)
            VALUES (?, ?, 'running', ?, 0, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                feature,
                len(candidates),
                len(BENCHMARK_CASES[feature]),
                BENCHMARK_VERSION,
            ),
        )
        con.commit()
        return int(cursor.lastrowid)
    finally:
        con.close()


def _record_result(db_path: str | Path, run_id: int, result: dict) -> None:
    con = sqlite3.connect(str(db_path), timeout=10)
    try:
        con.execute(
            """
            INSERT INTO ai_benchmark_results
                (run_id, provider, model, case_id, success, latency_ms,
                 total_score, format_score, correctness_score, risk_score,
                 calibration_score, verdict, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, result["provider"], result["model"], result["case_id"],
                int(result["success"]), int(result.get("latency_ms") or 0),
                float(result.get("total_score") or 0),
                float(result.get("format_score") or 0),
                float(result.get("correctness_score") or 0),
                float(result.get("risk_score") or 0),
                float(result.get("calibration_score") or 0),
                result.get("verdict") or None,
                (result.get("error") or "")[:300] or None,
            ),
        )
        con.commit()
    finally:
        con.close()


def execute_benchmark_run(
    db_path: str | Path,
    run_id: int,
    feature: str,
    candidates: list[dict],
) -> None:
    """Execute a persisted run. Intended for a daemon thread in app.py."""
    with _RUN_LOCK:
        try:
            for candidate in candidates:
                provider = candidate["provider"]
                model = candidate["model"]
                for case in BENCHMARK_CASES[feature]:
                    result = call_ai(
                        BENCHMARK_SYSTEM,
                        case["prompt"],
                        max_tokens=220,
                        provider=provider,
                        model=model,
                        feature=f"benchmark_{feature}",
                        return_result=True,
                        fallback_policy_override="selected_only",
                    )
                    if isinstance(result, AIResult):
                        scores = score_response(result.text, case)
                        row = {
                            **scores,
                            "provider": provider,
                            "model": model,
                            "case_id": case["id"],
                            "success": True,
                            "latency_ms": result.latency_ms,
                        }
                    else:
                        row = {
                            "provider": provider,
                            "model": model,
                            "case_id": case["id"],
                            "success": False,
                            "latency_ms": 0,
                            "total_score": 0,
                            "format_score": 0,
                            "correctness_score": 0,
                            "risk_score": 0,
                            "calibration_score": 0,
                            "verdict": "",
                            "error": "provider unavailable, failed, or circuit open",
                        }
                    _record_result(db_path, run_id, row)
                con = sqlite3.connect(str(db_path), timeout=10)
                try:
                    con.execute(
                        """
                        UPDATE ai_benchmark_runs
                        SET completed_models = completed_models + 1
                        WHERE id = ?
                        """,
                        (run_id,),
                    )
                    con.commit()
                finally:
                    con.close()
            con = sqlite3.connect(str(db_path), timeout=10)
            try:
                con.execute(
                    """
                    UPDATE ai_benchmark_runs
                    SET status='completed', completed_at=?
                    WHERE id=?
                    """,
                    (datetime.now(timezone.utc).isoformat(), run_id),
                )
                con.commit()
            finally:
                con.close()
        except Exception as exc:
            con = sqlite3.connect(str(db_path), timeout=10)
            try:
                con.execute(
                    """
                    UPDATE ai_benchmark_runs
                    SET status='failed', completed_at=?, error=?
                    WHERE id=?
                    """,
                    (
                        datetime.now(timezone.utc).isoformat(),
                        str(exc)[:300],
                        run_id,
                    ),
                )
                con.commit()
            finally:
                con.close()


def _aggregate_run(con: sqlite3.Connection, run: dict) -> dict:
    rows = [
        dict(row)
        for row in con.execute(
            """
            SELECT provider, model, case_id, success, latency_ms, total_score,
                   format_score, correctness_score, risk_score,
                   calibration_score, verdict, COALESCE(error, '') AS error
            FROM ai_benchmark_results
            WHERE run_id=?
            ORDER BY id
            """,
            (run["id"],),
        ).fetchall()
    ]
    groups: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row["provider"], row["model"])
        group = groups.setdefault(key, {
            "provider": row["provider"],
            "model": row["model"],
            "cases": 0,
            "successful_cases": 0,
            "scores": [],
            "format_scores": [],
            "correctness_scores": [],
            "risk_scores": [],
            "calibration_scores": [],
            "latencies": [],
            "errors": [],
        })
        group["cases"] += 1
        group["successful_cases"] += int(bool(row["success"]))
        group["scores"].append(float(row["total_score"]))
        group["format_scores"].append(float(row["format_score"]))
        group["correctness_scores"].append(float(row["correctness_score"]))
        group["risk_scores"].append(float(row["risk_score"]))
        group["calibration_scores"].append(float(row["calibration_score"]))
        if row["latency_ms"]:
            group["latencies"].append(int(row["latency_ms"]))
        if row["error"] and row["error"] not in group["errors"]:
            group["errors"].append(row["error"])

    models = []
    for group in groups.values():
        cases = group["cases"]
        avg = lambda values: round(sum(values) / len(values), 1) if values else 0.0
        model = {
            "provider": group["provider"],
            "model": group["model"],
            "cases": cases,
            "successful_cases": group["successful_cases"],
            "success_rate": round(group["successful_cases"] / cases * 100, 1) if cases else 0,
            "quality_score": avg(group["scores"]),
            "format_score": avg(group["format_scores"]),
            "correctness_score": avg(group["correctness_scores"]),
            "risk_score": avg(group["risk_scores"]),
            "calibration_score": avg(group["calibration_scores"]),
            "median_latency_ms": int(median(group["latencies"])) if group["latencies"] else None,
            "errors": group["errors"][:2],
        }
        latency_penalty = min((model["median_latency_ms"] or 10000) / 10000 * 3, 3)
        model["recommendation_score"] = round(
            model["quality_score"] * 0.9
            + model["success_rate"] * 0.1
            - latency_penalty,
            1,
        )
        model["eligible"] = bool(
            cases == run["case_count"]
            and model["success_rate"] == 100
            and model["quality_score"] >= 70
            and model["format_score"] >= 18
            and model["correctness_score"] >= 35
            and model["risk_score"] >= 24
        )
        model["status"] = "eligible" if model["eligible"] else "needs_work"
        models.append(model)

    models.sort(key=lambda item: item["recommendation_score"], reverse=True)
    eligible = [item for item in models if item["eligible"]]
    champion = eligible[0] if eligible else None
    for model in models:
        if champion and model["provider"] == champion["provider"] and model["model"] == champion["model"]:
            model["status"] = "champion"
        elif model["eligible"]:
            model["status"] = "challenger"
    return {
        **run,
        "models": models,
        "champion": (
            {"provider": champion["provider"], "model": champion["model"]}
            if champion
            else None
        ),
    }


def benchmark_overview(db_path: str | Path, run_limit: int = 20) -> dict:
    ensure_benchmark_tables(db_path)
    con = sqlite3.connect(str(db_path), timeout=10)
    con.row_factory = sqlite3.Row
    try:
        runs = [
            dict(row)
            for row in con.execute(
                """
                SELECT id, started_at, completed_at, feature, status, total_models,
                       completed_models, case_count, suite_version,
                       COALESCE(error, '') AS error
                FROM ai_benchmark_runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, min(int(run_limit), 50)),),
            ).fetchall()
        ]
        aggregated = [_aggregate_run(con, run) for run in runs]
    finally:
        con.close()

    latest_by_feature = {}
    for run in aggregated:
        latest_by_feature.setdefault(run["feature"], run)
    return {
        "features": [
            {"key": key, "case_count": len(cases)}
            for key, cases in BENCHMARK_CASES.items()
        ],
        "active_run": next((run for run in aggregated if run["status"] == "running"), None),
        "runs": aggregated,
        "latest_by_feature": latest_by_feature,
        "safety": {
            "shadow_only": True,
            "stores_responses": False,
            "changes_trading_authority": False,
            "promotion_is_manual": True,
            "max_candidates": MAX_CANDIDATES,
        },
    }


def record_promotion(
    db_path: str | Path,
    run_id: int,
    feature: str,
    old_route: dict,
    new_route: dict,
) -> None:
    ensure_benchmark_tables(db_path)
    con = sqlite3.connect(str(db_path), timeout=10)
    try:
        con.execute(
            """
            INSERT INTO ai_benchmark_promotions
                (run_id, feature, old_provider, old_model, new_provider, new_model, promoted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                feature,
                old_route.get("provider"),
                old_route.get("model"),
                new_route["provider"],
                new_route["model"],
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        con.commit()
    finally:
        con.close()
