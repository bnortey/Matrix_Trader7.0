"""Shared AI provider router for Matrix Trader.

`call_ai()` remains the only public inference entry point.  It supports a
global model, per-feature routes, an OpenAI-compatible custom endpoint, safe
fallbacks, persistent provider circuit breakers, health-aware routing, and
provider/model telemetry without storing prompts or responses.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from urllib.parse import urlparse


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_AI_SETTINGS_PATH = Path(
    os.getenv("MT7_AI_SETTINGS_PATH") or (_PROJECT_ROOT / "data" / "ai_settings.json")
)
_AI_TELEMETRY_DB_PATH = Path(
    os.getenv("MT7_AI_TELEMETRY_DB_PATH") or (_PROJECT_ROOT / "data" / "signals.db")
)


PROVIDERS = [
    {"name": "claude", "label": "Anthropic Claude", "key_env": "ANTHROPIC_API_KEY", "tier": "paid"},
    {"name": "openai", "label": "OpenAI", "key_env": "OPENAI_API_KEY", "tier": "paid"},
    {"name": "gemini", "label": "Google Gemini", "key_env": "GEMINI_API_KEY", "tier": "free"},
    {"name": "deepseek", "label": "DeepSeek", "key_env": "DEEPSEEK_API_KEY", "tier": "low_cost"},
    {"name": "kimi", "label": "Moonshot Kimi", "key_env": "KIMI_API_KEY", "tier": "low_cost"},
    {"name": "zai", "label": "Z.ai GLM", "key_env": "ZAI_API_KEY", "tier": "low_cost"},
    {"name": "groq", "label": "Groq", "key_env": "GROQ_API_KEY", "tier": "free"},
    {"name": "ollama", "label": "Ollama Local", "key_env": "OLLAMA_BASE_URL", "tier": "local"},
    {
        "name": "custom_openai",
        "label": "Custom OpenAI-Compatible",
        "key_env": "CUSTOM_AI_API_KEY",
        "tier": "custom",
    },
]

FREE_OR_LOW_COST_PROVIDERS = {"gemini", "deepseek", "kimi", "zai", "groq", "ollama", "custom_openai"}
FREE_MODEL_TIERS = {"free", "local"}


def _model(
    provider: str,
    model: str,
    label: str,
    *,
    tier: str,
    context: str = "",
    reasoning: bool = False,
) -> dict:
    provider_cfg = next(p for p in PROVIDERS if p["name"] == provider)
    return {
        "provider": provider,
        "model": model,
        "label": label,
        "key_env": provider_cfg["key_env"],
        "tier": tier,
        "context": context,
        "reasoning": reasoning,
    }


AVAILABLE_MODELS = [
    # Anthropic
    _model("claude", "claude-opus-5", "Claude Opus 5", tier="paid", reasoning=True),
    _model("claude", "claude-sonnet-5", "Claude Sonnet 5", tier="paid", reasoning=True),
    _model("claude", "claude-fable-5", "Claude Fable 5", tier="paid"),
    _model("claude", "claude-sonnet-4-6", "Claude Sonnet 4.6 (legacy)", tier="paid"),
    _model("claude", "claude-haiku-4-5-20251001", "Claude Haiku 4.5 (legacy)", tier="paid"),
    # OpenAI. The Responses API is used for these models.
    _model("openai", "gpt-5.6-sol", "GPT-5.6 Sol", tier="paid", context="1.05M", reasoning=True),
    _model("openai", "gpt-5.6-terra", "GPT-5.6 Terra", tier="paid", context="1.05M", reasoning=True),
    _model("openai", "gpt-5.6-luna", "GPT-5.6 Luna", tier="paid", context="1.05M", reasoning=True),
    # Gemini
    _model("gemini", "gemini-3.5-flash", "Gemini 3.5 Flash (free tier)", tier="free"),
    _model("gemini", "gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite (free tier)", tier="free"),
    _model("gemini", "gemini-3.6-flash", "Gemini 3.6 Flash", tier="paid"),
    _model("gemini", "gemini-2.5-flash", "Gemini 2.5 Flash (legacy)", tier="free"),
    # DeepSeek V4
    _model("deepseek", "deepseek-v4-flash", "DeepSeek V4 Flash", tier="low_cost", context="1M", reasoning=True),
    _model("deepseek", "deepseek-v4-pro", "DeepSeek V4 Pro", tier="low_cost", context="1M", reasoning=True),
    # Moonshot Kimi
    _model("kimi", "kimi-k2.6", "Kimi K2.6", tier="low_cost", context="256K", reasoning=True),
    _model("kimi", "kimi-k3", "Kimi K3", tier="low_cost", context="1M", reasoning=True),
    # Z.ai
    _model("zai", "glm-5.2", "GLM-5.2", tier="low_cost", reasoning=True),
    # Groq hosted free developer tier
    _model("groq", "qwen/qwen3.6-27b", "Qwen 3.6 27B (Groq free)", tier="free", reasoning=True),
    _model("groq", "openai/gpt-oss-120b", "GPT-OSS 120B (Groq free)", tier="free", reasoning=True),
    _model("groq", "openai/gpt-oss-20b", "GPT-OSS 20B (Groq free)", tier="free", reasoning=True),
    _model("groq", "llama-3.3-70b-versatile", "Llama 3.3 70B (Groq free)", tier="free"),
    _model("groq", "llama-3.1-8b-instant", "Llama 3.1 8B Instant (Groq free)", tier="free"),
    # Local/free. Set OLLAMA_BASE_URL and pull the model locally.
    _model("ollama", "llama3.1:8b", "Llama 3.1 8B (Ollama local)", tier="local"),
    _model("ollama", "qwen2.5:7b", "Qwen 2.5 7B (Ollama local)", tier="local"),
]


AI_FEATURES = [
    {"key": "signal_agents", "label": "Signal analyst pipeline", "description": "Eight analyst calls used by signal enrichment."},
    {"key": "coach_review", "label": "Closed-trade coach review", "description": "Thomas Chen review generated after a trade closes."},
    {"key": "strategy_analysis", "label": "Strategy analysis", "description": "On-demand analysis of signal performance."},
    {"key": "report_polish", "label": "Cipher report polish", "description": "Narrative polish layered over deterministic reports."},
    {"key": "coach_pattern", "label": "Learner coach-pattern synthesis", "description": "Background synthesis of recurring coach-review patterns."},
]
_FEATURE_KEYS = {f["key"] for f in AI_FEATURES}

_DEFAULT_SETTINGS = {
    "provider": "claude",
    "model": "claude-sonnet-4-6",
    "fallback_policy": "selected_only",
    "routing_strategy": "health_aware",
    "claude_fallback_enabled": False,
    "background_ai_enabled": False,
    "coach_reviews_enabled": True,
    "shadow_forecasting_enabled": False,
    "shadow_forecast_min_conviction": 70,
    "shadow_forecast_daily_call_cap": 12,
    "shadow_forecast_target": 50,
    "shadow_forecast_models": [],
    "feature_routes": {},
    "custom_openai": {
        "base_url": "",
        "model": "",
        "label": "",
        "key_env": "CUSTOM_AI_API_KEY",
    },
}

_MODEL_MIGRATIONS = {
    ("deepseek", "deepseek-chat"): ("deepseek", "deepseek-v4-flash"),
    ("deepseek", "deepseek-reasoner"): ("deepseek", "deepseek-v4-flash"),
}

_KEY_ENV = {p["name"]: p["key_env"] for p in PROVIDERS}
_RECENT_CALLS: deque[dict] = deque(maxlen=100)
_RUNTIME_TOTALS: dict[tuple[str, str, str], dict] = defaultdict(
    lambda: {"calls": 0, "successes": 0, "failures": 0, "latencies": deque(maxlen=100)}
)
_RUNTIME_LOCK = threading.Lock()
_TELEMETRY_READY = False
_TELEMETRY_LOCK = threading.Lock()
_CIRCUIT_HALF_OPEN_SECONDS = 30
_CIRCUIT_TRANSIENT_THRESHOLD = 2


@dataclass(frozen=True)
class AIResult:
    text: str
    provider: str
    model: str
    feature: str
    latency_ms: int
    fallback_used: bool
    attempts: int
    called_at: str


def _deep_merge_settings(raw: dict | None) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    settings = {**_DEFAULT_SETTINGS, **raw}
    settings["feature_routes"] = dict(raw.get("feature_routes") or {})
    settings["shadow_forecast_models"] = [
        {
            "provider": str(item.get("provider") or "").strip(),
            "model": str(item.get("model") or "").strip(),
            "role": str(item.get("role") or "").strip().lower(),
        }
        for item in (raw.get("shadow_forecast_models") or [])[:2]
        if isinstance(item, dict)
        and str(item.get("provider") or "").strip()
        and str(item.get("model") or "").strip()
        and str(item.get("role") or "").strip().lower() in {"champion", "challenger"}
    ]
    settings["custom_openai"] = {
        **_DEFAULT_SETTINGS["custom_openai"],
        **(raw.get("custom_openai") or {}),
    }

    # Migrate the original one-off coach override into the feature router.
    old_provider = str(raw.get("coach_review_provider") or "").strip()
    old_model = str(raw.get("coach_review_model") or "").strip()
    if old_provider and old_model and "coach_review" not in settings["feature_routes"]:
        settings["feature_routes"]["coach_review"] = {
            "provider": old_provider,
            "model": old_model,
        }

    provider = str(settings.get("provider") or "")
    model = str(settings.get("model") or "")
    migrated = _MODEL_MIGRATIONS.get((provider, model))
    if migrated:
        settings["provider"], settings["model"] = migrated
    for feature, route in list(settings["feature_routes"].items()):
        if feature not in _FEATURE_KEYS or not isinstance(route, dict):
            settings["feature_routes"].pop(feature, None)
            continue
        route_provider = str(route.get("provider") or "")
        route_model = str(route.get("model") or "")
        route_migrated = _MODEL_MIGRATIONS.get((route_provider, route_model))
        if route_migrated:
            route_provider, route_model = route_migrated
        if route_provider and route_model:
            settings["feature_routes"][feature] = {
                "provider": route_provider,
                "model": route_model,
            }
        else:
            settings["feature_routes"].pop(feature, None)

    # Legacy keys are no longer written back.
    settings.pop("coach_review_provider", None)
    settings.pop("coach_review_model", None)
    return settings


def load_ai_settings() -> dict:
    try:
        if _AI_SETTINGS_PATH.exists():
            with _AI_SETTINGS_PATH.open(encoding="utf-8") as f:
                return _deep_merge_settings(json.load(f))
    except Exception as exc:
        print(f"ai_client: settings load failed: {exc}", file=sys.stderr)
    return _deep_merge_settings({})


def save_ai_settings(settings: dict) -> None:
    clean = _deep_merge_settings(settings)
    _AI_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _AI_SETTINGS_PATH.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(clean, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp_path, _AI_SETTINGS_PATH)


def model_catalog(settings: dict | None = None) -> list[dict]:
    """Return the built-in catalog plus the configured custom model, if any."""
    settings = settings or load_ai_settings()
    catalog = [dict(m) for m in AVAILABLE_MODELS]
    custom = settings.get("custom_openai") or {}
    custom_model = str(custom.get("model") or "").strip()
    custom_url = str(custom.get("base_url") or "").strip()
    if custom_model and custom_url:
        key_env = str(custom.get("key_env") or "CUSTOM_AI_API_KEY").strip()
        catalog.append({
            "provider": "custom_openai",
            "model": custom_model,
            "label": str(custom.get("label") or custom_model).strip(),
            "key_env": key_env,
            "tier": "custom",
            "context": "",
            "reasoning": False,
        })
    return catalog


def is_valid_model(provider: str, model: str, settings: dict | None = None) -> bool:
    return any(
        item["provider"] == provider and item["model"] == model
        for item in model_catalog(settings)
    )


def validate_custom_openai_config(config: dict) -> tuple[bool, str]:
    """Validate a custom endpoint without allowing credentials or public HTTP."""
    base_url = str(config.get("base_url") or "").strip()
    model = str(config.get("model") or "").strip()
    key_env = str(config.get("key_env") or "CUSTOM_AI_API_KEY").strip()
    if not base_url and not model:
        return True, ""
    if not base_url or not model:
        return False, "custom endpoint and model ID must both be set"
    parsed = urlparse(base_url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return False, "custom endpoint cannot contain credentials, a query, or a fragment"
    hostname = (parsed.hostname or "").lower()
    local_host = hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and local_host):
        return False, "custom endpoint must use HTTPS (HTTP is allowed only for localhost)"
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,64}", key_env):
        return False, "key environment variable must use uppercase letters, digits, and underscores"
    if len(model) > 160 or any(ch.isspace() for ch in model):
        return False, "custom model ID is invalid"
    return True, ""


def _custom_config(settings: dict) -> dict:
    return settings.get("custom_openai") or {}


def _provider_available(provider: str, settings: dict | None = None) -> bool:
    settings = settings or load_ai_settings()
    if provider == "ollama":
        return bool(os.getenv("OLLAMA_BASE_URL", "").strip())
    if provider == "custom_openai":
        custom = _custom_config(settings)
        base_url = str(custom.get("base_url") or "").strip()
        model = str(custom.get("model") or "").strip()
        key_env = str(custom.get("key_env") or "CUSTOM_AI_API_KEY").strip()
        host = (urlparse(base_url).hostname or "").lower()
        local = host in {"localhost", "127.0.0.1", "::1"}
        return bool(base_url and model and (local or os.getenv(key_env, "").strip()))
    return bool(os.getenv(_KEY_ENV.get(provider, ""), "").strip())


def provider_status(settings: dict | None = None) -> list[dict]:
    """Return provider configuration readiness without exposing secrets."""
    settings = settings or load_ai_settings()
    catalog = model_catalog(settings)
    circuits = {item["provider"]: item for item in provider_circuits()}
    out = []
    for provider in PROVIDERS:
        name = provider["name"]
        key_env = (
            str(_custom_config(settings).get("key_env") or provider["key_env"])
            if name == "custom_openai"
            else provider["key_env"]
        )
        out.append({
            "provider": name,
            "label": provider["label"],
            "key_env": key_env,
            "tier": provider["tier"],
            "available": _provider_available(name, settings),
            "circuit": circuits.get(name, {"provider": name, "state": "closed"}),
            "models": [
                {"model": m["model"], "label": m["label"], "tier": m.get("tier")}
                for m in catalog
                if m["provider"] == name
            ],
        })
    return out


def _redact_error(error: Exception | str, settings: dict) -> str:
    message = str(error).replace("\n", " ")[:300]
    env_names = set(_KEY_ENV.values())
    env_names.add(str(_custom_config(settings).get("key_env") or "CUSTOM_AI_API_KEY"))
    for env_name in env_names:
        secret = os.getenv(env_name, "")
        if secret and len(secret) >= 6:
            message = message.replace(secret, "[redacted]")
    return message


def _ensure_telemetry_table() -> None:
    global _TELEMETRY_READY
    if _TELEMETRY_READY:
        return
    with _TELEMETRY_LOCK:
        if _TELEMETRY_READY:
            return
        _AI_TELEMETRY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(_AI_TELEMETRY_DB_PATH), timeout=5)
        try:
            con.execute("""
                CREATE TABLE IF NOT EXISTS ai_call_events (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    called_at     TEXT NOT NULL,
                    feature       TEXT NOT NULL,
                    provider      TEXT NOT NULL,
                    model         TEXT NOT NULL,
                    success       INTEGER NOT NULL,
                    latency_ms    INTEGER NOT NULL,
                    fallback_used INTEGER NOT NULL DEFAULT 0,
                    attempt_index INTEGER NOT NULL DEFAULT 1,
                    error         TEXT
                )
            """)
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_ai_call_events_recent
                ON ai_call_events (called_at DESC)
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS ai_provider_circuits (
                    provider       TEXT PRIMARY KEY,
                    state          TEXT NOT NULL DEFAULT 'closed',
                    failure_count  INTEGER NOT NULL DEFAULT 0,
                    cooldown_until REAL,
                    reason         TEXT,
                    last_error     TEXT,
                    updated_at     TEXT NOT NULL
                )
            """)
            con.commit()
            _TELEMETRY_READY = True
        finally:
            con.close()


def _record_attempt(event: dict) -> None:
    compact = {
        "called_at": event["called_at"],
        "feature": event["feature"],
        "provider": event["provider"],
        "model": event["model"],
        "success": bool(event["success"]),
        "latency_ms": int(event["latency_ms"]),
        "fallback_used": bool(event["fallback_used"]),
        "attempt_index": int(event["attempt_index"]),
        "error": event.get("error") or "",
    }
    with _RUNTIME_LOCK:
        _RECENT_CALLS.appendleft(compact)
        key = (compact["feature"], compact["provider"], compact["model"])
        totals = _RUNTIME_TOTALS[key]
        totals["calls"] += 1
        totals["successes" if compact["success"] else "failures"] += 1
        totals["latencies"].append(compact["latency_ms"])
    try:
        _ensure_telemetry_table()
        con = sqlite3.connect(str(_AI_TELEMETRY_DB_PATH), timeout=5)
        try:
            con.execute(
                """
                INSERT INTO ai_call_events
                    (called_at, feature, provider, model, success, latency_ms,
                     fallback_used, attempt_index, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    compact["called_at"],
                    compact["feature"],
                    compact["provider"],
                    compact["model"],
                    int(compact["success"]),
                    compact["latency_ms"],
                    int(compact["fallback_used"]),
                    compact["attempt_index"],
                    compact["error"][:300] or None,
                ),
            )
            con.commit()
        finally:
            con.close()
    except Exception as exc:
        print(f"ai_client: telemetry write skipped: {exc}", file=sys.stderr)


def _failure_policy(error: str) -> tuple[str, int, bool]:
    """Classify a redacted provider failure into breaker behavior."""
    message = (error or "").lower()
    if any(token in message for token in (
        "insufficient balance", "credits are depleted", "credit balance",
        "billing", "payment required", "error code: 402", "status code: 402",
    )):
        return "billing", 3600, True
    if any(token in message for token in (
        "invalid api key", "invalid_api_key", "unauthorized",
        "authentication", "error code: 401", "status code: 401",
    )):
        return "authentication", 3600, True
    if any(token in message for token in (
        "rate limit", "rate_limit", "resource_exhausted", "too many requests",
        "error code: 429", "status code: 429",
    )):
        return "rate_limit", 600, True
    if any(token in message for token in (
        "provider is not configured", "provider is not supported",
    )):
        return "configuration", 300, True
    if any(token in message for token in (
        "timeout", "timed out", "connection", "temporarily unavailable",
        "service unavailable", "bad gateway", "gateway timeout",
        "error code: 500", "error code: 502", "error code: 503",
        "error code: 504", "status code: 500", "status code: 502",
        "status code: 503", "status code: 504",
    )):
        return "transient", 120, False
    return "model_error", 0, False


def _record_circuit_outcome(provider: str, success: bool, error: str = "") -> None:
    """Persist provider health so the web and learner processes share it."""
    try:
        _ensure_telemetry_table()
        con = sqlite3.connect(str(_AI_TELEMETRY_DB_PATH), timeout=5)
        con.row_factory = sqlite3.Row
        try:
            now = time.time()
            updated_at = datetime.now(timezone.utc).isoformat()
            current = con.execute(
                "SELECT * FROM ai_provider_circuits WHERE provider = ?",
                (provider,),
            ).fetchone()
            if success:
                con.execute(
                    """
                    INSERT INTO ai_provider_circuits
                        (provider, state, failure_count, cooldown_until, reason, last_error, updated_at)
                    VALUES (?, 'closed', 0, NULL, NULL, NULL, ?)
                    ON CONFLICT(provider) DO UPDATE SET
                        state = 'closed', failure_count = 0, cooldown_until = NULL,
                        reason = NULL, last_error = NULL, updated_at = excluded.updated_at
                    """,
                    (provider, updated_at),
                )
            else:
                reason, cooldown_seconds, immediate = _failure_policy(error)
                failures = (
                    int(current["failure_count"] or 0) + 1
                    if current and current["reason"] == reason
                    else 1
                )
                was_probe = bool(current and current["state"] == "half_open")
                should_open = immediate or was_probe or (
                    reason == "transient" and failures >= _CIRCUIT_TRANSIENT_THRESHOLD
                )
                state = "open" if should_open else "closed"
                cooldown_until = now + (cooldown_seconds or 120) if should_open else None
                con.execute(
                    """
                    INSERT INTO ai_provider_circuits
                        (provider, state, failure_count, cooldown_until, reason, last_error, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(provider) DO UPDATE SET
                        state = excluded.state,
                        failure_count = excluded.failure_count,
                        cooldown_until = excluded.cooldown_until,
                        reason = excluded.reason,
                        last_error = excluded.last_error,
                        updated_at = excluded.updated_at
                    """,
                    (
                        provider, state, failures, cooldown_until, reason,
                        (error or "")[:300] or None, updated_at,
                    ),
                )
            con.commit()
        finally:
            con.close()
    except Exception as exc:
        print(f"ai_client: circuit update skipped: {exc}", file=sys.stderr)


def _circuit_allows(provider: str) -> bool:
    """Return whether a provider may run, granting one cross-process half-open probe."""
    try:
        _ensure_telemetry_table()
        con = sqlite3.connect(str(_AI_TELEMETRY_DB_PATH), timeout=5, isolation_level=None)
        con.row_factory = sqlite3.Row
        try:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT * FROM ai_provider_circuits WHERE provider = ?",
                (provider,),
            ).fetchone()
            now = time.time()
            if not row:
                con.execute(
                    """
                    INSERT INTO ai_provider_circuits
                        (provider, state, failure_count, cooldown_until, reason, last_error, updated_at)
                    VALUES (?, 'half_open', 0, ?, 'initial_probe', NULL, ?)
                    """,
                    (
                        provider,
                        now + _CIRCUIT_HALF_OPEN_SECONDS,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                con.commit()
                return True
            if row["state"] == "closed":
                con.commit()
                return True
            cooldown_until = float(row["cooldown_until"] or 0)
            if cooldown_until > now:
                con.commit()
                return False
            con.execute(
                """
                UPDATE ai_provider_circuits
                SET state = 'half_open', cooldown_until = ?, updated_at = ?
                WHERE provider = ?
                """,
                (
                    now + _CIRCUIT_HALF_OPEN_SECONDS,
                    datetime.now(timezone.utc).isoformat(),
                    provider,
                ),
            )
            con.commit()
            return True
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
    except Exception as exc:
        print(f"ai_client: circuit read skipped: {exc}", file=sys.stderr)
        return True


def provider_circuits() -> list[dict]:
    """Return breaker state for every provider without exposing credentials."""
    stored: dict[str, dict] = {}
    try:
        _ensure_telemetry_table()
        con = sqlite3.connect(str(_AI_TELEMETRY_DB_PATH), timeout=5)
        con.row_factory = sqlite3.Row
        try:
            stored = {
                row["provider"]: dict(row)
                for row in con.execute(
                    "SELECT * FROM ai_provider_circuits ORDER BY provider"
                ).fetchall()
            }
        finally:
            con.close()
    except Exception:
        stored = {}
    now = time.time()
    result = []
    for provider_cfg in PROVIDERS:
        provider = provider_cfg["name"]
        row = stored.get(provider) or {
            "provider": provider,
            "state": "closed",
            "failure_count": 0,
            "cooldown_until": None,
            "reason": None,
            "last_error": None,
            "updated_at": None,
        }
        remaining = max(0, int(float(row.get("cooldown_until") or 0) - now + 0.999))
        row["cooldown_remaining_seconds"] = remaining
        if row.get("state") == "open" and remaining == 0:
            row["state"] = "probe_ready"
        result.append(row)
    return result


def reset_provider_circuits(provider: str | None = None) -> int:
    """Close one or all provider breakers and return the affected row count."""
    if provider and provider not in _KEY_ENV:
        raise ValueError("unknown provider")
    _ensure_telemetry_table()
    con = sqlite3.connect(str(_AI_TELEMETRY_DB_PATH), timeout=5)
    try:
        if provider:
            cursor = con.execute(
                "DELETE FROM ai_provider_circuits WHERE provider = ?",
                (provider,),
            )
        else:
            cursor = con.execute("DELETE FROM ai_provider_circuits")
        con.commit()
        return max(0, int(cursor.rowcount))
    finally:
        con.close()


def _recent_routing_health(limit: int = 500) -> dict[tuple[str, str], dict]:
    """Load a bounded health window used only to order fallback candidates."""
    try:
        _ensure_telemetry_table()
        con = sqlite3.connect(str(_AI_TELEMETRY_DB_PATH), timeout=5)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                """
                SELECT provider, model, success, latency_ms
                FROM ai_call_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 1000)),),
            ).fetchall()
        finally:
            con.close()
    except Exception:
        return {}
    health: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row["provider"], row["model"])
        item = health.setdefault(key, {"calls": 0, "successes": 0, "latencies": []})
        item["calls"] += 1
        item["successes"] += int(bool(row["success"]))
        item["latencies"].append(int(row["latency_ms"]))
    return health


def _rank_fallback_candidates(
    candidates: list[tuple[str, str]],
    catalog: list[dict],
) -> list[tuple[str, str]]:
    """Rank fallback models by Bayesian reliability, cost tier, then latency."""
    health = _recent_routing_health()
    catalog_by_key = {(item["provider"], item["model"]): item for item in catalog}
    tier_bonus = {"local": 14.0, "free": 12.0, "low_cost": 6.0, "custom": 4.0, "paid": 0.0}

    def score(candidate: tuple[str, str]) -> float:
        item = health.get(candidate) or {"calls": 0, "successes": 0, "latencies": []}
        calls = int(item["calls"])
        successes = int(item["successes"])
        reliability = (successes + 1) / (calls + 2)
        latency_values = item["latencies"]
        median_latency = median(latency_values) if latency_values else 5000
        latency_bonus = max(0.0, 5.0 - min(float(median_latency), 20000.0) / 4000.0)
        tier = str((catalog_by_key.get(candidate) or {}).get("tier") or "paid")
        sample_bonus = min(calls, 20) / 20
        return reliability * 100 + tier_bonus.get(tier, 0.0) + latency_bonus + sample_bonus

    return sorted(candidates, key=score, reverse=True)


def runtime_health(limit: int = 100) -> dict:
    """Return recent redacted inference health from memory or the shared ledger."""
    rows: list[dict] = []
    try:
        _ensure_telemetry_table()
        con = sqlite3.connect(str(_AI_TELEMETRY_DB_PATH), timeout=5)
        con.row_factory = sqlite3.Row
        try:
            rows = [
                dict(row)
                for row in con.execute(
                    """
                    SELECT called_at, feature, provider, model, success, latency_ms,
                           fallback_used, attempt_index, COALESCE(error, '') AS error
                    FROM ai_call_events
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (max(1, min(int(limit), 500)),),
                ).fetchall()
            ]
        finally:
            con.close()
    except Exception:
        with _RUNTIME_LOCK:
            rows = list(_RECENT_CALLS)[:limit]

    grouped: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row["provider"], row["model"])
        group = grouped.setdefault(key, {
            "provider": row["provider"],
            "model": row["model"],
            "calls": 0,
            "successes": 0,
            "latencies": [],
            "last_called_at": row["called_at"],
            "last_error": "",
        })
        group["calls"] += 1
        if row["success"]:
            group["successes"] += 1
        elif not group["last_error"]:
            group["last_error"] = row.get("error") or ""
        group["latencies"].append(int(row["latency_ms"]))
    providers = []
    for group in grouped.values():
        latencies = group.pop("latencies")
        calls = group["calls"]
        group["success_rate"] = round(group["successes"] / calls * 100, 1) if calls else None
        group["median_latency_ms"] = int(median(latencies)) if latencies else None
        providers.append(group)
    return {
        "window_calls": len(rows),
        "successful_calls": sum(1 for row in rows if row["success"]),
        "providers": providers,
        "circuits": provider_circuits(),
        "recent": rows[:12],
    }


def _strip_thinking_blocks(text: str) -> str:
    """Remove visible reasoning blocks while preserving the model's final answer."""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    return cleaned.strip()


def _route_for_feature(
    settings: dict,
    feature: str,
    provider: str | None,
    model: str | None,
) -> tuple[str, str]:
    if provider and model:
        return provider, model
    route = (settings.get("feature_routes") or {}).get(feature) or {}
    if route.get("provider") and route.get("model"):
        return str(route["provider"]), str(route["model"])
    return str(settings.get("provider") or ""), str(settings.get("model") or "")


def call_ai(
    system: str,
    user: str,
    max_tokens: int = 512,
    allowed_providers: set[str] | list[str] | tuple[str, ...] | None = None,
    provider: str | None = None,
    model: str | None = None,
    feature: str = "general",
    return_result: bool = False,
    bypass_circuit: bool = False,
    fallback_policy_override: str | None = None,
) -> str | AIResult | None:
    """Call the selected model and optionally return structured provenance.

    Explicit provider/model arguments win, then a per-feature route, then the
    global route.  Existing callers continue to receive `str | None`.
    """
    settings = load_ai_settings()
    cfg_provider, cfg_model = _route_for_feature(settings, feature, provider, model)
    policy = str(
        fallback_policy_override
        or settings.get("fallback_policy")
        or "selected_only"
    ).strip().lower()
    routing_strategy = str(settings.get("routing_strategy") or "health_aware").strip().lower()
    claude_fallback_enabled = bool(settings.get("claude_fallback_enabled"))
    allowed = set(allowed_providers) if allowed_providers else None
    catalog = model_catalog(settings)

    if policy not in {"selected_only", "free_only", "low_cost", "allow_all"}:
        policy = "selected_only"

    primary: tuple[str, str] | None = None
    if cfg_provider and cfg_model:
        primary = (cfg_provider, cfg_model)
    fallback_candidates: list[tuple[str, str]] = []
    if policy != "selected_only":
        for provider_cfg in PROVIDERS:
            provider_name = provider_cfg["name"]
            if provider_name == "claude" and not claude_fallback_enabled:
                continue
            if policy == "low_cost" and provider_name not in FREE_OR_LOW_COST_PROVIDERS:
                continue
            if not _provider_available(provider_name, settings):
                continue
            for item in catalog:
                if item["provider"] != provider_name:
                    continue
                if policy == "free_only":
                    is_local_custom = (
                        provider_name == "custom_openai"
                        and (urlparse(str(_custom_config(settings).get("base_url") or "")).hostname or "").lower()
                        in {"localhost", "127.0.0.1", "::1"}
                    )
                    if item.get("tier") not in FREE_MODEL_TIERS and not is_local_custom:
                        continue
                fallback_candidates.append((provider_name, item["model"]))

    if routing_strategy == "health_aware":
        fallback_candidates = _rank_fallback_candidates(fallback_candidates, catalog)

    deduped: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in ([primary] if primary else []) + fallback_candidates:
        if candidate not in seen:
            seen.add(candidate)
            deduped.append(candidate)

    attempts = 0
    for provider_name, model_name in deduped:
        if allowed is not None and provider_name not in allowed:
            continue
        if not bypass_circuit and not _circuit_allows(provider_name):
            continue
        attempts += 1
        called_at = datetime.now(timezone.utc).isoformat()
        fallback_used = bool(primary and (provider_name, model_name) != primary)
        started = time.perf_counter()
        error_text = ""
        result_text = ""
        try:
            if not _provider_available(provider_name, settings):
                raise RuntimeError("provider is not configured")
            fn = _DISPATCH.get(provider_name)
            if fn is None:
                raise RuntimeError("provider is not supported")
            result_text = fn(system, user, max_tokens, model_name, settings)
            if result_text:
                result_text = _strip_thinking_blocks(str(result_text))
            if not result_text:
                raise RuntimeError("provider returned an empty response")
        except Exception as exc:
            error_text = _redact_error(exc, settings)
            print(f"ai_client: {provider_name}/{model_name} failed: {error_text}", file=sys.stderr)
        latency_ms = max(0, int((time.perf_counter() - started) * 1000))
        success = bool(result_text)
        _record_attempt({
            "called_at": called_at,
            "feature": feature or "general",
            "provider": provider_name,
            "model": model_name,
            "success": success,
            "latency_ms": latency_ms,
            "fallback_used": fallback_used,
            "attempt_index": attempts,
            "error": error_text,
        })
        _record_circuit_outcome(provider_name, success, error_text)
        if success:
            result = AIResult(
                text=result_text,
                provider=provider_name,
                model=model_name,
                feature=feature or "general",
                latency_ms=latency_ms,
                fallback_used=fallback_used,
                attempts=attempts,
                called_at=called_at,
            )
            return result if return_result else result.text

        if policy == "selected_only":
            break

    return None


def _call_claude(system: str, user: str, max_tokens: int, model: str, settings: dict) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", "").strip())
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(
        block.text for block in message.content if getattr(block, "type", "") == "text"
    )


def _call_openai(system: str, user: str, max_tokens: int, model: str, settings: dict) -> str:
    import openai

    client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY", "").strip())
    response = client.responses.create(
        model=model,
        instructions=system,
        input=user,
        max_output_tokens=max_tokens,
    )
    return response.output_text


def _call_gemini(system: str, user: str, max_tokens: int, model: str, settings: dict) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY", "").strip())
    response = client.models.generate_content(
        model=model,
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
        ),
    )
    return response.text


def _call_openai_compatible(
    system: str,
    user: str,
    max_tokens: int,
    model: str,
    *,
    api_key: str,
    base_url: str,
    extra_body: dict | None = None,
) -> str:
    import openai

    client = openai.OpenAI(api_key=api_key or "local", base_url=base_url.rstrip("/"))
    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if extra_body:
        kwargs["extra_body"] = extra_body
    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message.content or ""


def _call_deepseek(system: str, user: str, max_tokens: int, model: str, settings: dict) -> str:
    return _call_openai_compatible(
        system,
        user,
        max_tokens,
        model,
        api_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
        base_url="https://api.deepseek.com",
        extra_body={"thinking": {"type": "enabled"}},
    )


def _call_kimi(system: str, user: str, max_tokens: int, model: str, settings: dict) -> str:
    extra_body = {"reasoning_effort": "high"} if model == "kimi-k3" else None
    return _call_openai_compatible(
        system,
        user,
        max_tokens,
        model,
        api_key=os.getenv("KIMI_API_KEY", "").strip(),
        base_url="https://api.moonshot.ai/v1",
        extra_body=extra_body,
    )


def _call_zai(system: str, user: str, max_tokens: int, model: str, settings: dict) -> str:
    return _call_openai_compatible(
        system,
        user,
        max_tokens,
        model,
        api_key=os.getenv("ZAI_API_KEY", "").strip(),
        base_url="https://api.z.ai/api/paas/v4",
    )


def _call_groq(system: str, user: str, max_tokens: int, model: str, settings: dict) -> str:
    import groq

    client = groq.Groq(api_key=os.getenv("GROQ_API_KEY", "").strip())
    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if "qwen" in model.lower():
        kwargs["reasoning_effort"] = "none"
    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message.content or ""


def _call_ollama(system: str, user: str, max_tokens: int, model: str, settings: dict) -> str:
    import requests

    base = os.getenv("OLLAMA_BASE_URL", "").strip().rstrip("/")
    response = requests.post(
        f"{base}/api/chat",
        json={
            "model": model,
            "stream": False,
            "options": {"num_predict": max_tokens},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        timeout=90,
    )
    response.raise_for_status()
    data = response.json()
    return (data.get("message") or {}).get("content") or data.get("response") or ""


def _call_custom_openai(system: str, user: str, max_tokens: int, model: str, settings: dict) -> str:
    custom = _custom_config(settings)
    key_env = str(custom.get("key_env") or "CUSTOM_AI_API_KEY").strip()
    return _call_openai_compatible(
        system,
        user,
        max_tokens,
        model,
        api_key=os.getenv(key_env, "").strip(),
        base_url=str(custom.get("base_url") or "").strip(),
    )


_DISPATCH = {
    "claude": _call_claude,
    "openai": _call_openai,
    "gemini": _call_gemini,
    "deepseek": _call_deepseek,
    "kimi": _call_kimi,
    "zai": _call_zai,
    "groq": _call_groq,
    "ollama": _call_ollama,
    "custom_openai": _call_custom_openai,
}
