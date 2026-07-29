import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as mt7


class CoachReviewIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = mt7.DB_PATH
        mt7.DB_PATH = str(Path(self.tmp.name) / "signals.db")
        con = sqlite3.connect(mt7.DB_PATH)
        con.execute(
            "CREATE TABLE signals ("
            "id INTEGER PRIMARY KEY, symbol TEXT, direction TEXT, strategy TEXT, "
            "strategy_key TEXT, conviction REAL, result TEXT, pnl_pct REAL, "
            "funding_rate REAL, logged_at TEXT, result_at TEXT, source TEXT, "
            "signal_json TEXT)"
        )
        con.commit()
        con.close()

    def tearDown(self):
        mt7.DB_PATH = self.old_db
        mt7._invalidate_hermes_audit_cache()
        self.tmp.cleanup()

    def _signal(self, **overrides):
        signal = {
            "id": 1,
            "symbol": "ALPHA_USDT",
            "direction": "LONG",
            "strategy": "Balanced",
            "strategy_key": "balanced",
            "conviction": 72,
            "result": "LOSS",
            "pnl_pct": -4.5,
            "funding_rate": 0.0009,
        }
        signal.update(overrides)
        return signal

    def _journey(self, **overrides):
        journey = {
            "available": True,
            "path_label": "failed_fast",
            "mae_pct": 2.8,
            "mfe_pct": 0.4,
            "capture_ratio_pct": None,
            "stop_pressure_pct": 88.0,
            "entry_delay_minutes": 10,
            "entry_to_close_minutes": 90,
            "target_hits": {},
        }
        journey.update(overrides)
        return journey

    def test_sanitizer_removes_provider_reasoning_and_keeps_review(self):
        raw = (
            "<think>\nOkay, I need to reason about the request.\n</think>\n"
            "Price moved against the entry.\n\nRequire fresh flow confirmation next time."
        )
        cleaned, quality = mt7._sanitize_coach_review_text(raw)
        self.assertNotIn("<think>", cleaned)
        self.assertNotIn("reason about", cleaned)
        self.assertTrue(cleaned.startswith("Price moved"))
        self.assertTrue(quality["reasoning_removed"])
        unfinished, unfinished_quality = mt7._sanitize_coach_review_text(
            "<think>provider reasoning ended before a final answer"
        )
        self.assertEqual(unfinished, "")
        self.assertTrue(unfinished_quality["reasoning_removed"])

    def test_structured_packet_is_evidence_backed_and_cannot_raise_risk(self):
        packet = mt7._build_coach_review_packet(
            self._signal(),
            {},
            self._journey(),
            "The path failed quickly.\n\nWait for independent confirmation next time.",
            90,
        )
        self.assertEqual(packet["version"], mt7.COACH_REVIEW_VERSION)
        self.assertEqual(packet["verdict"]["primary_issue"], "funding_misaligned")
        self.assertEqual(packet["path"]["mae_pct"], 2.8)
        self.assertGreaterEqual(packet["quality"]["structured_evidence_fields"], 6)
        rule = packet["lesson"]["next_trade_rule"].lower()
        self.assertIn("fresh mt7 signal", rule)
        self.assertIn("explicit user approval", packet["lesson"]["cohort_rule"].lower())
        self.assertIn("cannot raise leverage", " ".join(packet["limits"]).lower())

    def test_migration_sanitizes_and_versions_legacy_reviews_without_ai(self):
        signal_json = {
            "coach_review": (
                "<think>private chain of thought</think>"
                "The trade used most of its stop.\n\nDo not widen the stop next time."
            ),
            "journey_available": True,
            "journey_mae_pct": 2.8,
            "journey_mfe_pct": 0.4,
            "journey_stop_pressure": 88.0,
            "journey_path_label": "failed_fast",
        }
        con = sqlite3.connect(mt7.DB_PATH)
        con.execute(
            "INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                1, "ALPHA_USDT", "LONG", "Balanced", "balanced", 72,
                "LOSS", -4.5, 0.0009, "2026-07-28T00:00:00",
                "2026-07-28T01:00:00", "live", json.dumps(signal_json),
            ),
        )
        con.commit()
        con.close()

        with patch.object(mt7, "call_ai") as ai:
            result = mt7._upgrade_coach_review_contract()
        ai.assert_not_called()
        self.assertEqual(result["upgraded"], 1)
        self.assertEqual(result["reasoning_removed"], 1)

        con = sqlite3.connect(mt7.DB_PATH)
        stored = json.loads(con.execute(
            "SELECT signal_json FROM signals WHERE id=1"
        ).fetchone()[0])
        con.close()
        self.assertEqual(stored["coach_review_version"], mt7.COACH_REVIEW_VERSION)
        self.assertNotIn("<think>", stored["coach_review"])
        self.assertEqual(
            stored["coach_review_packet"]["verdict"]["primary_issue"],
            "funding_misaligned",
        )

    def test_ai_failure_still_produces_a_rich_deterministic_review(self):
        con = sqlite3.connect(mt7.DB_PATH)
        con.execute(
            "INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                1, "ALPHA_USDT", "LONG", "Balanced", "balanced", 72,
                "LOSS", -4.5, 0.0009, "2026-07-28T00:00:00",
                "2026-07-28T01:00:00", "live", "{}",
            ),
        )
        con.commit()
        con.close()
        sig = self._signal()
        signal_json = {}
        with patch.object(mt7, "call_ai", return_value=None):
            review = mt7._generate_coach_review(
                sig,
                1,
                signal_json,
                self._journey(),
                "journey",
                "",
                1.0,
                0.95,
                90,
            )
        self.assertIn("MAE", review)
        self.assertIn("explicit user approval", review)
        self.assertEqual(
            signal_json["coach_review_packet"]["quality"]["narrative_source"],
            "deterministic_fallback",
        )

    def test_hermes_cache_avoids_rebuilding_until_forced(self):
        mt7._invalidate_hermes_audit_cache()
        with patch.object(
            mt7,
            "_build_hermes_audit",
            side_effect=[{"value": 1}, {"value": 2}],
        ) as build:
            first = mt7._load_hermes_audit_cached()
            second = mt7._load_hermes_audit_cached()
            forced = mt7._load_hermes_audit_cached(force=True)
        self.assertEqual(first["value"], 1)
        self.assertEqual(second["cache"]["status"], "hit")
        self.assertEqual(forced["value"], 2)
        self.assertEqual(build.call_count, 2)


if __name__ == "__main__":
    unittest.main()
