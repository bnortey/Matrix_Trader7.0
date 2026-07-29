import json
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import lib.ai_client as ai_client


class AIClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_settings_path = ai_client._AI_SETTINGS_PATH
        self.old_telemetry_path = ai_client._AI_TELEMETRY_DB_PATH
        self.old_telemetry_ready = ai_client._TELEMETRY_READY
        ai_client._AI_SETTINGS_PATH = Path(self.tmp.name) / "ai_settings.json"
        ai_client._AI_TELEMETRY_DB_PATH = Path(self.tmp.name) / "signals.db"
        ai_client._TELEMETRY_READY = False

    def tearDown(self):
        ai_client._AI_SETTINGS_PATH = self.old_settings_path
        ai_client._AI_TELEMETRY_DB_PATH = self.old_telemetry_path
        ai_client._TELEMETRY_READY = self.old_telemetry_ready
        self.tmp.cleanup()

    def test_retired_deepseek_model_and_coach_override_migrate(self):
        ai_client._AI_SETTINGS_PATH.write_text(
            json.dumps({
                "provider": "deepseek",
                "model": "deepseek-chat",
                "coach_review_provider": "deepseek",
                "coach_review_model": "deepseek-reasoner",
            }),
            encoding="utf-8",
        )

        settings = ai_client.load_ai_settings()

        self.assertEqual((settings["provider"], settings["model"]), ("deepseek", "deepseek-v4-flash"))
        self.assertEqual(
            settings["feature_routes"]["coach_review"],
            {"provider": "deepseek", "model": "deepseek-v4-flash"},
        )
        self.assertNotIn("coach_review_provider", settings)

    def test_custom_endpoint_rejects_public_http_but_allows_local_http(self):
        valid, _ = ai_client.validate_custom_openai_config({
            "base_url": "http://localhost:1234/v1",
            "model": "local-model",
            "key_env": "CUSTOM_AI_API_KEY",
        })
        invalid, error = ai_client.validate_custom_openai_config({
            "base_url": "http://example.com/v1",
            "model": "remote-model",
            "key_env": "CUSTOM_AI_API_KEY",
        })

        self.assertTrue(valid)
        self.assertFalse(invalid)
        self.assertIn("HTTPS", error)

    def test_feature_route_wins_and_returns_provenance(self):
        settings = ai_client.load_ai_settings()
        settings["provider"] = "claude"
        settings["model"] = "claude-sonnet-4-6"
        settings["feature_routes"] = {
            "strategy_analysis": {
                "provider": "custom_openai",
                "model": "test-model",
            }
        }
        settings["custom_openai"] = {
            "base_url": "http://localhost:1234/v1",
            "model": "test-model",
            "label": "Test model",
            "key_env": "CUSTOM_AI_API_KEY",
        }
        ai_client.save_ai_settings(settings)

        def fake_call(system, user, max_tokens, model, current_settings):
            self.assertEqual(model, "test-model")
            return "<think>private reasoning</think>MT7 result"

        with patch.dict(ai_client._DISPATCH, {"custom_openai": fake_call}):
            result = ai_client.call_ai(
                system="system",
                user="user",
                feature="strategy_analysis",
                return_result=True,
            )

        self.assertIsInstance(result, ai_client.AIResult)
        self.assertEqual(result.text, "MT7 result")
        self.assertEqual(result.provider, "custom_openai")
        self.assertEqual(result.model, "test-model")
        health = ai_client.runtime_health()
        self.assertEqual(health["window_calls"], 1)
        self.assertEqual(health["successful_calls"], 1)
        self.assertNotIn("system", json.dumps(health))
        self.assertNotIn("user", json.dumps(health))

    def test_remote_custom_endpoint_requires_key_but_local_does_not(self):
        local_settings = ai_client.load_ai_settings()
        local_settings["custom_openai"] = {
            "base_url": "http://127.0.0.1:1234/v1",
            "model": "local",
            "key_env": "CUSTOM_AI_API_KEY",
        }
        remote_settings = ai_client.load_ai_settings()
        remote_settings["custom_openai"] = {
            "base_url": "https://models.example/v1",
            "model": "remote",
            "key_env": "CUSTOM_AI_API_KEY",
        }
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CUSTOM_AI_API_KEY", None)
            self.assertTrue(ai_client._provider_available("custom_openai", local_settings))
            self.assertFalse(ai_client._provider_available("custom_openai", remote_settings))

    def test_free_only_fallback_skips_configured_paid_providers(self):
        settings = ai_client.load_ai_settings()
        settings.update({
            "provider": "custom_openai",
            "model": "local-test",
            "fallback_policy": "free_only",
            "custom_openai": {
                "base_url": "http://localhost:1234/v1",
                "model": "local-test",
                "label": "Local test",
                "key_env": "CUSTOM_AI_API_KEY",
            },
        })
        ai_client.save_ai_settings(settings)

        def local_failure(*args, **kwargs):
            raise RuntimeError("local offline")

        def paid_must_not_run(*args, **kwargs):
            raise AssertionError("paid provider ran under free_only")

        def free_success(system, user, max_tokens, model, current_settings):
            return "free result"

        clean_env = {
            key: ""
            for key in {
                "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
                "KIMI_API_KEY", "ZAI_API_KEY", "OLLAMA_BASE_URL",
            }
        }
        clean_env.update({"DEEPSEEK_API_KEY": "configured-paid", "GROQ_API_KEY": "configured-free"})
        with patch.dict(os.environ, clean_env, clear=False), patch.dict(
            ai_client._DISPATCH,
            {
                "custom_openai": local_failure,
                "deepseek": paid_must_not_run,
                "groq": free_success,
            },
        ):
            result = ai_client.call_ai(
                system="system",
                user="user",
                feature="signal_agents",
                return_result=True,
            )

        self.assertIsInstance(result, ai_client.AIResult)
        self.assertEqual(result.provider, "groq")
        self.assertEqual(result.text, "free result")

    def test_billing_circuit_opens_and_suppresses_repeated_primary_attempts(self):
        settings = ai_client.load_ai_settings()
        settings.update({
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "fallback_policy": "low_cost",
            "routing_strategy": "health_aware",
        })
        ai_client.save_ai_settings(settings)
        calls = {"deepseek": 0, "groq": 0}

        def depleted(*args, **kwargs):
            calls["deepseek"] += 1
            raise RuntimeError("Error code: 402 - Insufficient Balance")

        def free_success(*args, **kwargs):
            calls["groq"] += 1
            return "fallback result"

        clean_env = {provider["key_env"]: "" for provider in ai_client.PROVIDERS}
        clean_env.update({"DEEPSEEK_API_KEY": "configured", "GROQ_API_KEY": "configured"})
        with patch.dict(os.environ, clean_env, clear=False), patch.dict(
            ai_client._DISPATCH,
            {"deepseek": depleted, "groq": free_success},
        ):
            first = ai_client.call_ai("system", "user", feature="coach_pattern", return_result=True)
            second = ai_client.call_ai("system", "user", feature="coach_pattern", return_result=True)

        self.assertEqual(first.provider, "groq")
        self.assertEqual(second.provider, "groq")
        self.assertEqual(calls["deepseek"], 1)
        circuit = next(c for c in ai_client.provider_circuits() if c["provider"] == "deepseek")
        self.assertEqual(circuit["state"], "open")
        self.assertEqual(circuit["reason"], "billing")
        self.assertGreater(circuit["cooldown_remaining_seconds"], 3500)

    def test_concurrent_first_calls_grant_only_one_provider_probe(self):
        settings = ai_client.load_ai_settings()
        settings.update({
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "fallback_policy": "selected_only",
        })
        ai_client.save_ai_settings(settings)
        ai_client.provider_circuits()

        worker_count = 20
        start = threading.Barrier(worker_count)
        call_lock = threading.Lock()
        calls = 0

        def depleted(*args, **kwargs):
            nonlocal calls
            with call_lock:
                calls += 1
            time.sleep(0.1)
            raise RuntimeError("Error code: 402 - Insufficient Balance")

        def make_call(_):
            start.wait(timeout=5)
            return ai_client.call_ai(
                "system",
                "user",
                feature="signal_agents",
                return_result=True,
            )

        clean_env = {provider["key_env"]: "" for provider in ai_client.PROVIDERS}
        clean_env["DEEPSEEK_API_KEY"] = "configured"
        with patch.dict(os.environ, clean_env, clear=False), patch.dict(
            ai_client._DISPATCH,
            {"deepseek": depleted},
        ), ThreadPoolExecutor(max_workers=worker_count) as pool:
            results = list(pool.map(make_call, range(worker_count)))

        self.assertEqual(calls, 1)
        self.assertTrue(all(result is None for result in results))
        circuit = next(c for c in ai_client.provider_circuits() if c["provider"] == "deepseek")
        self.assertEqual(circuit["state"], "open")
        self.assertEqual(circuit["reason"], "billing")

    def test_explicit_bypass_probe_closes_an_open_circuit(self):
        settings = ai_client.load_ai_settings()
        settings.update({
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "fallback_policy": "selected_only",
        })
        ai_client.save_ai_settings(settings)
        ai_client._record_circuit_outcome(
            "deepseek", False, "Error code: 402 - Insufficient Balance"
        )

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "configured"}, clear=False), patch.dict(
            ai_client._DISPATCH,
            {"deepseek": lambda *args, **kwargs: "MT7_AI_OK"},
        ):
            blocked = ai_client.call_ai("system", "user", return_result=True)
            probed = ai_client.call_ai(
                "system", "user", return_result=True, bypass_circuit=True
            )

        self.assertIsNone(blocked)
        self.assertEqual(probed.provider, "deepseek")
        circuit = next(c for c in ai_client.provider_circuits() if c["provider"] == "deepseek")
        self.assertEqual(circuit["state"], "closed")
        self.assertEqual(circuit["failure_count"], 0)

    def test_transient_failures_require_two_strikes_and_reset_is_available(self):
        ai_client._record_circuit_outcome("groq", False, "connection timed out")
        first = next(c for c in ai_client.provider_circuits() if c["provider"] == "groq")
        ai_client._record_circuit_outcome("groq", False, "service unavailable")
        second = next(c for c in ai_client.provider_circuits() if c["provider"] == "groq")

        self.assertEqual(first["state"], "closed")
        self.assertEqual(first["failure_count"], 1)
        self.assertEqual(second["state"], "open")
        self.assertEqual(second["reason"], "transient")
        self.assertEqual(ai_client.reset_provider_circuits("groq"), 1)
        reset = next(c for c in ai_client.provider_circuits() if c["provider"] == "groq")
        self.assertEqual(reset["state"], "closed")

    def test_health_aware_order_prefers_observed_reliable_free_model(self):
        ai_client._record_attempt({
            "called_at": "2026-07-27T12:00:00+00:00",
            "feature": "signal_agents",
            "provider": "groq",
            "model": "qwen/qwen3.6-27b",
            "success": True,
            "latency_ms": 700,
            "fallback_used": True,
            "attempt_index": 2,
            "error": "",
        })
        ai_client._record_attempt({
            "called_at": "2026-07-27T12:00:01+00:00",
            "feature": "signal_agents",
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "success": False,
            "latency_ms": 400,
            "fallback_used": False,
            "attempt_index": 1,
            "error": "insufficient balance",
        })

        ranked = ai_client._rank_fallback_candidates(
            [
                ("deepseek", "deepseek-v4-flash"),
                ("groq", "qwen/qwen3.6-27b"),
            ],
            ai_client.model_catalog(),
        )

        self.assertEqual(ranked[0], ("groq", "qwen/qwen3.6-27b"))


if __name__ == "__main__":
    unittest.main()
