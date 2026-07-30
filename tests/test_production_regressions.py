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

    def test_frontend_contains_first_load_and_batch_upload_regressions(self):
        source = Path("templates/index.html").read_text(encoding="utf-8")
        self.assertIn("fetch('/api/scan/strategy'", source)
        self.assertIn("Promise.allSettled([", source)
        self.assertIn("multiple style=", source)
        self.assertIn("loadOpsWatchdog", source)
        self.assertIn("overflow-y:auto;overflow-x:hidden", source)
        self.assertIn("max-width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch", source)
        self.assertNotIn("Missed-mover autopsy not available.", source)


if __name__ == "__main__":
    unittest.main()
