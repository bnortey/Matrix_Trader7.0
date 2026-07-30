import io
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import app as mt7


class ProductionRegressionTests(unittest.TestCase):
    def setUp(self):
        with mt7._ops_edge_coverage_cache_lock:
            mt7._ops_edge_coverage_cache.update({
                "built_at": 0.0,
                "packet": None,
                "refreshing": False,
            })

    def test_market_regime_uses_broad_snapshot_not_sparse_unknown_agents(self):
        tickers = [
            {
                "symbol": f"ASSET{i}_USDT",
                "change_24h_pct": -3.0 if i < 80 else 0.1,
                "funding_rate": 0.0001,
            }
            for i in range(100)
        ]
        result = mt7._report_classify_market_regime(
            tickers,
            {"unknown": 90, "choppy": 4, "funding_crowded": 2},
        )
        self.assertEqual(result["label"], "risk_off_beta")
        self.assertEqual(result["source"], "broad_ticker_snapshot")
        self.assertEqual(result["sample"], 100)
        self.assertEqual(result["directional_bias"], "bearish")

    def test_market_regime_abstains_when_broad_and_signal_samples_are_small(self):
        result = mt7._report_classify_market_regime(
            [{"symbol": "A_USDT", "change_24h_pct": 1.0, "funding_rate": 0.0}],
            {"unknown": 3},
        )
        self.assertEqual(result["label"], "unknown")
        self.assertEqual(result["source"], "abstention")

    @patch("app._record_explosive_shadow_events")
    @patch("app._missed_mover_context", return_value={})
    @patch("app._report_fetch_ticker_snapshot")
    def test_missed_mover_autopsy_enriches_only_ranked_display_set(
        self,
        ticker_snapshot,
        context,
        record,
    ):
        ticker_snapshot.return_value = [
            {
                "symbol": f"MOVER{i}_USDT",
                "riseFallRate": (10 + i) / 100,
                "volume24": 2_000_000,
                "fundingRate": 0.0002,
            }
            for i in range(20)
        ]
        record.return_value = {"recorded": 5, "top_score": 80}
        result = mt7._build_missed_mover_autopsy("2026-07-30", limit=5)
        self.assertEqual(len(result["movers"]), 5)
        self.assertEqual(len(record.call_args.args[1]), 5)

    @patch("app.log_signals")
    @patch("app.run_scan")
    @patch("app.fetch_mexc")
    @patch("app.expire_stale_signals")
    @patch("app.get_strategy_registry")
    def test_selected_strategy_scan_does_not_scan_entire_registry(
        self,
        registry,
        expire,
        fetch_mexc,
        run_scan,
        log_signals,
    ):
        registry.return_value = {
            "balanced": {"name": "Balanced", "enabled": True},
            "funding_arb": {"name": "Funding Arb", "enabled": True},
        }
        fetch_mexc.return_value = [{"symbol": "BTC_USDT"}]
        run_scan.return_value = ([{"symbol": "BTC_USDT", "conviction": 70}], 1)
        response = mt7.app.test_client().post(
            "/api/scan/strategy",
            json={"exchange": "MEXC", "strategy": "balanced"},
        )
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["scope"], "selected_strategy")
        self.assertEqual(list(payload["results"]), ["balanced"])
        run_scan.assert_called_once()
        self.assertEqual(run_scan.call_args.kwargs["strategy_key"], "balanced")
        log_signals.assert_called_once()

    @patch("app._hermes_research_pipeline_summary", return_value={"library": {"source_count": 2}})
    @patch("app._research_ingest_pdf_upload")
    def test_pdf_route_accepts_batch_and_preserves_single_file_compatibility(
        self,
        ingest,
        pipeline,
    ):
        ingest.side_effect = [
            {"id": "pdf_a", "title": "A", "full_text_word_count": 10},
            {"id": "pdf_b", "title": "B", "full_text_word_count": 20},
            {"id": "pdf_c", "title": "C", "full_text_word_count": 30},
        ]
        client = mt7.app.test_client()
        response = client.post(
            "/api/intelligence/research/upload-pdf",
            data={
                "files": [
                    (io.BytesIO(b"%PDF-1.4 a"), "a.pdf"),
                    (io.BytesIO(b"%PDF-1.4 b"), "b.pdf"),
                ],
                "tags": "market,structure",
            },
            content_type="multipart/form-data",
        )
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["uploaded_count"], 2)
        self.assertEqual(payload["failed_count"], 0)
        self.assertEqual(len(payload["sources"]), 2)
        self.assertIsNone(payload["source"])

        single = client.post(
            "/api/intelligence/research/upload-pdf",
            data={"file": (io.BytesIO(b"%PDF-1.4 c"), "c.pdf"), "title": "Custom C"},
            content_type="multipart/form-data",
        ).get_json()
        self.assertEqual(single["uploaded_count"], 1)
        self.assertEqual(single["source"]["id"], "pdf_c")
        self.assertEqual(ingest.call_args.kwargs["title"], "Custom C")

    @patch("app._edge_lab_cohort_coverage_fast")
    def test_watchdog_edge_coverage_returns_warming_without_blocking(self, coverage):
        def slow_coverage():
            time.sleep(0.2)
            return {"available": True, "coverage_pct": 75.0, "matched": 3, "total": 4}

        coverage.side_effect = slow_coverage
        started = time.monotonic()
        first = mt7._edge_lab_cohort_coverage_cached()
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.1)
        self.assertTrue(first["refreshing"])
        self.assertIsNone(first["coverage_pct"])

        deadline = time.monotonic() + 2
        second = first
        while time.monotonic() < deadline:
            second = mt7._edge_lab_cohort_coverage_cached()
            if second.get("coverage_pct") == 75.0:
                break
            time.sleep(0.02)
        self.assertEqual(second["coverage_pct"], 75.0)

    @patch("app._hermes_research_pipeline_summary", return_value={"library": {"source_count": 2}})
    @patch("app._learner_running", return_value=True)
    @patch("app._file_age_seconds", return_value=60)
    @patch("app._edge_lab_cohort_coverage_cached")
    @patch("app._annotate_suggestion_authority", return_value=[])
    @patch("app._load_suggestions", return_value=[])
    @patch("app._compute_goal_actuals")
    @patch("app._load_goals", return_value={})
    @patch("app._version_payload", return_value={"app_sha": "abc123", "git_commit": "abc123"})
    def test_watchdog_treats_empty_current_cohort_as_collecting_not_zero_quality(
        self,
        version,
        goals,
        actuals,
        suggestions,
        authority,
        coverage,
        ages,
        learner,
        research,
    ):
        actuals.return_value = {
            "max_drawdown_usd": 0,
            "drawdown_pct": 0,
            "return_pct": 0,
            "scale_up_blockers": ["collect more trades"],
            "scale_up_ready": False,
            "active_safety_controls": [],
        }
        coverage.return_value = {
            "available": True,
            "coverage_pct": None,
            "matched": 0,
            "total": 0,
            "measurement_state": "collecting",
        }
        payload = mt7._ops_watchdog_payload()
        check = next(c for c in payload["checks"] if c["name"] == "Current Paper Cohort Edge Coverage")
        self.assertEqual(check["status"], "info")
        self.assertEqual(check["value"], "collecting · 0/0")
        self.assertEqual(payload["warn_count"], 1)
        self.assertNotIn("P12", " ".join(c["name"] for c in payload["checks"]))

    def test_frontend_contains_first_load_and_batch_upload_regressions(self):
        source = Path("templates/index.html").read_text(encoding="utf-8")
        self.assertIn("fetch('/api/scan/strategy'", source)
        self.assertIn("Promise.allSettled([", source)
        self.assertIn("multiple style=", source)
        self.assertIn("loadOpsWatchdog", source)
        self.assertIn("overflow-y:auto;overflow-x:hidden", source)
        self.assertIn("max-width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch", source)
        self.assertIn("const TRADER_HELP =", source)
        self.assertIn("function coverageDisplay(", source)
        self.assertIn("`${emptyLabel} · ${n}/${d}`", source)
        self.assertIn("ASSISTED-LIVE READINESS", source)
        self.assertIn("Profitable or partial rate", source)
        self.assertIn("Average result after costs", source)
        self.assertIn("Why This Is Not Ready Yet", source)
        self.assertIn("Is Profit Broad or Outlier-Driven?", source)
        self.assertIn("Evidence Conflict Map", source)
        self.assertIn("Research Approval & Safety", source)
        self.assertNotIn(">P12 Posture<", source)
        self.assertNotIn("Missed-mover autopsy not available.", source)


if __name__ == "__main__":
    unittest.main()
