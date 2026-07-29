import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as mt7
from lib.agents import AGENT_ROSTER


TICKERS = [
    {
        "symbol": "ALPHA_USDT",
        "riseFallRate": 0.12,
        "volume24": 50_000_000,
        "fundingRate": -0.0012,
    },
    {
        "symbol": "BETA_USDT",
        "riseFallRate": -0.08,
        "volume24": 30_000_000,
        "fundingRate": 0.0011,
    },
    {
        "symbol": "GAMMA_USDT",
        "riseFallRate": 0.01,
        "volume24": 9_000_000,
        "fundingRate": 0.0,
    },
]


class CipherReportDepthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_db = mt7.DB_PATH
        self.old_reports = mt7.REPORTS_DIR
        mt7.DB_PATH = str(root / "signals.db")
        mt7.REPORTS_DIR = str(root / "reports")
        con = sqlite3.connect(mt7.DB_PATH)
        con.execute(
            "CREATE TABLE signals ("
            "id INTEGER PRIMARY KEY, symbol TEXT, exchange TEXT, direction TEXT, "
            "conviction REAL, result TEXT, pnl_pct REAL, strategy_key TEXT, "
            "funding_rate REAL, logged_at TEXT, signal_json TEXT)"
        )
        con.execute(
            "CREATE TABLE filtered_candidates ("
            "id INTEGER PRIMARY KEY, symbol TEXT, gate_key TEXT, strategy_key TEXT, "
            "logged_at TEXT)"
        )
        con.execute(
            "CREATE TABLE paper_trades ("
            "id INTEGER PRIMARY KEY, status TEXT, result TEXT, pnl_pct REAL, "
            "opened_at TEXT)"
        )
        con.executemany(
            "INSERT INTO signals "
            "(symbol, exchange, direction, conviction, result, pnl_pct, strategy_key, funding_rate, logged_at, signal_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    "ALPHA_USDT", "MEXC", "LONG", 72, "WIN", 4.0, "balanced",
                    -0.0012, "2026-07-28T13:00:00",
                    json.dumps({"agent_regime": "funding_crowded", "agent_shadow_disagreement": 0.1}),
                ),
                (
                    "BETA_USDT", "MEXC", "SHORT", 68, "LOSS", -2.0, "balanced",
                    0.0011, "2026-07-27T14:00:00",
                    json.dumps({"agent_regime": "funding_crowded", "agent_shadow_disagreement": 0.55}),
                ),
            ],
        )
        con.execute(
            "INSERT INTO filtered_candidates (symbol, gate_key, strategy_key, logged_at) "
            "VALUES ('BETA_USDT', 'volatility_gate', 'balanced', '2026-07-28T14:00:00')"
        )
        con.execute(
            "INSERT INTO paper_trades (status, result, pnl_pct, opened_at) "
            "VALUES ('closed', 'WIN', 3.5, '2026-07-28T15:00:00')"
        )
        con.commit()
        con.close()

    def tearDown(self):
        mt7.DB_PATH = self.old_db
        mt7.REPORTS_DIR = self.old_reports
        self.tmp.cleanup()

    def _create_cross_venue_tables(self):
        con = sqlite3.connect(mt7.DB_PATH)
        con.execute(
            "CREATE TABLE cross_venue_snapshots ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, captured_at TEXT, base_asset TEXT, "
            "exchange TEXT, native_symbol TEXT, quote_asset TEXT, price REAL, "
            "mark_price REAL, index_price REAL, bid_price REAL, ask_price REAL, "
            "spread_bps REAL, volume_24h_usd REAL, open_interest_native REAL, "
            "open_interest_usd REAL, funding_rate REAL, funding_interval_hours REAL, "
            "funding_interval_assumed INTEGER, funding_8h_equiv REAL, "
            "change_24h_pct REAL, source_timestamp_ms INTEGER, data_quality TEXT, "
            "UNIQUE(captured_at,base_asset,exchange))"
        )
        con.execute(
            "CREATE TABLE cross_venue_collection_runs ("
            "captured_at TEXT PRIMARY KEY, latency_ms INTEGER, mexc_count INTEGER, "
            "hyperliquid_count INTEGER, bybit_count INTEGER, matched_two_venue INTEGER, "
            "matched_three_venue INTEGER, stored_rows INTEGER, errors_json TEXT)"
        )
        con.commit()
        con.close()

    def _create_catalyst_tables(self):
        con = sqlite3.connect(mt7.DB_PATH)
        con.execute(
            "CREATE TABLE catalyst_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, source_key TEXT NOT NULL, "
            "source_name TEXT NOT NULL, source_kind TEXT NOT NULL, source_url TEXT NOT NULL, "
            "external_id TEXT NOT NULL, title TEXT NOT NULL, summary TEXT, "
            "published_at TEXT, event_at TEXT, observed_at TEXT NOT NULL, status TEXT, "
            "severity TEXT, event_type TEXT, affected_assets_json TEXT, "
            "affected_venues_json TEXT, horizon TEXT, scheduled INTEGER, "
            "primary_source INTEGER, source_time_quality TEXT, content_hash TEXT, "
            "raw_meta_json TEXT, UNIQUE(source_key,external_id))"
        )
        con.execute(
            "CREATE TABLE catalyst_collection_runs ("
            "collected_at TEXT PRIMARY KEY, latency_ms INTEGER, stored_events INTEGER, "
            "new_events INTEGER, source_counts_json TEXT, source_errors_json TEXT)"
        )
        con.commit()
        con.close()

    def _create_tokenomics_tables(self):
        con = sqlite3.connect(mt7.DB_PATH)
        con.execute(
            "CREATE TABLE tokenomics_asset_snapshots ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, captured_at TEXT, symbol TEXT, "
            "coin_id TEXT, name TEXT, source_key TEXT, source_name TEXT, source_url TEXT, "
            "mapping_quality TEXT, current_price REAL, market_cap REAL, "
            "fully_diluted_value REAL, circulating_supply REAL, total_supply REAL, "
            "max_supply REAL, float_pct REAL, fdv_market_cap_ratio REAL, volume_24h REAL, "
            "source_updated_at TEXT, observed_at TEXT, data_quality TEXT, "
            "UNIQUE(captured_at,symbol,source_key))"
        )
        con.execute(
            "CREATE TABLE token_unlock_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, source_key TEXT, source_name TEXT, "
            "source_url TEXT, external_id TEXT, protocol_id TEXT, project_name TEXT, "
            "symbol TEXT, coin_id TEXT, unlock_at TEXT, observed_at TEXT, unlock_type TEXT, "
            "category TEXT, token_amount REAL, unlock_value_usd REAL, unlock_pct_float REAL, "
            "circulating_supply REAL, max_supply REAL, unlocked_pct REAL, "
            "mapping_quality TEXT, data_quality TEXT, raw_meta_json TEXT, "
            "UNIQUE(source_key,external_id))"
        )
        con.execute(
            "CREATE TABLE token_treasury_snapshots ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, snapshot_day TEXT, captured_at TEXT, "
            "source_key TEXT, source_name TEXT, source_url TEXT, external_id TEXT, "
            "project_name TEXT, symbol TEXT, coin_id TEXT, treasury_value_usd REAL, "
            "stablecoins_usd REAL, majors_usd REAL, own_tokens_usd REAL, "
            "other_tokens_usd REAL, liquid_ex_own_usd REAL, own_token_share_pct REAL, "
            "market_cap REAL, mapping_quality TEXT, data_quality TEXT, "
            "UNIQUE(snapshot_day,source_key,external_id))"
        )
        con.execute(
            "CREATE TABLE tokenomics_collection_runs ("
            "collected_at TEXT PRIMARY KEY, latency_ms INTEGER, asset_snapshots INTEGER, "
            "unlock_events INTEGER, treasury_snapshots INTEGER, mapped_symbols INTEGER, "
            "source_counts_json TEXT, source_errors_json TEXT)"
        )
        con.commit()
        con.close()

    def _create_social_tables(self):
        con = sqlite3.connect(mt7.DB_PATH)
        con.execute(
            "CREATE TABLE social_activity_snapshots ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, captured_at TEXT, source_key TEXT, "
            "source_name TEXT, source_url TEXT, topic_key TEXT, query_text TEXT, "
            "symbol TEXT, post_count INTEGER, unique_authors INTEGER, total_likes INTEGER, "
            "total_reposts INTEGER, total_replies INTEGER, top_author_share_pct REAL, "
            "duplicate_text_share_pct REAL, posts_per_hour REAL, engagement_per_post REAL, "
            "activity_vs_baseline_pct REAL, baseline_observations INTEGER, "
            "evidence_quality TEXT, quality_flags_json TEXT, examples_json TEXT, "
            "source_time_quality TEXT, UNIQUE(captured_at,source_key,topic_key))"
        )
        con.execute(
            "CREATE TABLE social_collection_runs ("
            "collected_at TEXT PRIMARY KEY, latency_ms INTEGER, stored_topics INTEGER, "
            "total_posts INTEGER, unique_authors INTEGER, source_counts_json TEXT, "
            "source_errors_json TEXT)"
        )
        con.commit()
        con.close()

    @patch.object(mt7, "_report_research_coverage")
    def test_every_employee_gets_distinct_specialty_contract(self, coverage):
        coverage.return_value = {
            "tokenomics": {"indexed_source_count": 2, "status": "research_only", "live_feed": False},
            "sentiment": {"indexed_source_count": 1, "status": "research_only", "live_feed": False},
            "catalysts": {"indexed_source_count": 3, "status": "research_only", "live_feed": False},
        }
        data = mt7._build_daily_data("2026-07-28", ticker_snapshot=TICKERS)
        mt7._enrich_report_intelligence(data)

        briefs = data["analyst_briefs"]
        self.assertEqual(set(briefs), set(AGENT_ROSTER))
        self.assertEqual(len({brief["read"] for brief in briefs.values()}), len(AGENT_ROSTER))
        for key, brief in briefs.items():
            self.assertTrue(brief["evidence"], key)
            self.assertTrue(brief["trader_use"], key)
            self.assertTrue(brief["invalidation"], key)
            self.assertTrue(brief["horizon"], key)
            self.assertIn("report_contract", AGENT_ROSTER[key])

    @patch.object(mt7, "_report_research_coverage")
    def test_priya_discloses_missing_onchain_data_instead_of_inventing_it(self, coverage):
        coverage.return_value = {
            "tokenomics": {"indexed_source_count": 0, "status": "unavailable", "live_feed": False},
            "sentiment": {"indexed_source_count": 0, "status": "unavailable", "live_feed": False},
            "catalysts": {"indexed_source_count": 0, "status": "unavailable", "live_feed": False},
        }
        data = mt7._build_daily_data("2026-07-28", ticker_snapshot=TICKERS)
        mt7._enrich_report_intelligence(data)
        priya = data["analyst_briefs"]["tokenomics"]

        self.assertEqual(priya["confidence"], "low")
        self.assertEqual(priya["coverage"], "unavailable")
        self.assertIn("cannot confirm an unlock", priya["read"].lower())
        self.assertIn("not on-chain proof", priya["read"].lower())
        self.assertTrue(any("wallet" in item.lower() for item in priya["limits"]))
        self.assertIn("circulating supply", priya["trader_use"].lower())

    @patch.object(mt7, "_report_research_coverage")
    def test_report_has_short_mid_long_horizons_and_conditional_scenarios(self, coverage):
        coverage.return_value = {
            "tokenomics": {"indexed_source_count": 0, "status": "unavailable"},
            "sentiment": {"indexed_source_count": 0, "status": "unavailable"},
            "catalysts": {"indexed_source_count": 0, "status": "unavailable"},
        }
        data = mt7._build_daily_data("2026-07-28", ticker_snapshot=TICKERS)
        mt7._enrich_report_intelligence(data)

        self.assertEqual(
            [row["window"] for row in data["horizon_outlook"]],
            ["0–24 hours", "2–7 days", "2–6 weeks"],
        )
        self.assertEqual(
            {row["scenario"] for row in data["scenario_matrix"]},
            {"Continuation", "Regime Rotation", "Crowding Unwind"},
        )
        for row in data["scenario_matrix"]:
            self.assertTrue(row["confirm_if"])
            self.assertTrue(row["fail_if"])
            self.assertTrue(row["response"])
            self.assertNotIn("guarantee", row["response"].lower())

    def test_tension_score_is_normalized_and_caution_is_not_called_contradiction(self):
        claims = [
            {
                "claim": f"Funding claim {idx}",
                "stance": "support" if idx < 4 else "caution",
                "confidence": 80,
                "market_scope": "crypto_perps",
                "mt7_applicability": "direct",
            }
            for idx in range(5)
        ]
        priority = [{
            "id": "funding",
            "shadow_tag": "research_funding_crowding_filter",
            "target_strategy": "balanced",
            "thesis": "Test funding crowding.",
            "evidence_claims": claims,
            "source_ids": [],
            "available_fields": ["funding_rate"],
        }]
        scholar = mt7._research_corpus_scholar_payload(
            sources=[],
            priority_queue=priority,
            reviews=[],
            persist=False,
        )
        item = scholar["contradiction_map"]["items"][0]

        self.assertEqual(item["contradiction_count"], 0)
        self.assertEqual(item["tension_label"], "caution")
        self.assertLess(item["tension_score"], 25)
        self.assertIn("no direct contradiction", item["why_flagged"].lower())
        self.assertIn("not stronger alpha", item["trader_interpretation"].lower())

    def test_specialist_evidence_uses_persisted_rotation_and_order_flow(self):
        con = sqlite3.connect(mt7.DB_PATH)
        con.execute(
            "CREATE TABLE ticker_snapshots ("
            "id INTEGER PRIMARY KEY, ts TEXT, symbol TEXT, exchange TEXT, price REAL, "
            "volume_24h REAL, funding_rate REAL, change_24h_pct REAL, open_interest REAL)"
        )
        con.execute(
            "CREATE TABLE market_context_snapshots ("
            "id INTEGER PRIMARY KEY, ts TEXT, btc_price REAL, btc_rsi_1h REAL, "
            "btc_trend TEXT, btc_change_24h REAL, eth_price REAL, eth_rsi_1h REAL, "
            "eth_trend TEXT, btc_ls_ratio REAL)"
        )
        con.execute(
            "CREATE TABLE order_flow_trades ("
            "id INTEGER PRIMARY KEY, exchange TEXT, symbol TEXT, ts INTEGER, "
            "price REAL, volume REAL, side TEXT, recorded_at TEXT)"
        )
        con.execute(
            "CREATE TABLE order_flow_depth_snapshots ("
            "id INTEGER PRIMARY KEY, exchange TEXT, symbol TEXT, ts INTEGER, "
            "best_bid REAL, best_ask REAL, bid_total REAL, ask_total REAL, "
            "imbalance REAL, bids_json TEXT, asks_json TEXT, recorded_at TEXT)"
        )
        con.executemany(
            "INSERT INTO ticker_snapshots "
            "(ts,symbol,exchange,price,volume_24h,funding_rate,change_24h_pct,open_interest) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [
                ("2026-07-28T01:00:00", "ALPHA_USDT", "MEXC", 10, 1000, -0.001, 2, 100),
                ("2026-07-28T01:00:00", "BETA_USDT", "MEXC", 20, 1000, 0.001, -2, 200),
                ("2026-07-28T23:00:00", "ALPHA_USDT", "MEXC", 12, 1500, -0.0002, 8, 140),
                ("2026-07-28T23:00:00", "BETA_USDT", "MEXC", 18, 900, 0.0002, -6, 180),
            ],
        )
        con.executemany(
            "INSERT INTO market_context_snapshots "
            "(ts,btc_price,btc_rsi_1h,btc_trend,btc_change_24h,eth_price,btc_ls_ratio) "
            "VALUES (?,?,?,?,?,?,?)",
            [
                ("2026-07-28T01:00:00", 100000, 45, "BEARISH", -1, 3000, 0.9),
                ("2026-07-28T23:00:00", 102000, 55, "BULLISH", 2, 3100, 1.1),
            ],
        )
        start_ts = int(mt7.datetime(2026, 7, 28, tzinfo=mt7.timezone.utc).timestamp())
        con.executemany(
            "INSERT INTO order_flow_trades "
            "(exchange,symbol,ts,price,volume,side,recorded_at) VALUES (?,?,?,?,?,?,?)",
            [
                ("MEXC", "ALPHA_USDT", start_ts + 60, 10, 7, "buy", "2026-07-28T00:01:00"),
                ("MEXC", "ALPHA_USDT", start_ts + 120, 10.1, 3, "sell", "2026-07-28T00:02:00"),
            ],
        )
        con.execute(
            "INSERT INTO order_flow_depth_snapshots "
            "(exchange,symbol,ts,best_bid,best_ask,bid_total,ask_total,imbalance,bids_json,asks_json,recorded_at) "
            "VALUES ('MEXC','ALPHA_USDT',?,?,?,?,?,?,?,?,'2026-07-28T00:03:00')",
            (start_ts + 180, 10, 10.01, 60, 40, 0.2, "[]", "[]"),
        )
        con.commit()
        con.close()

        packet = mt7._report_market_history_evidence(
            "2026-07-28",
            focus_symbols=["ALPHA_USDT"],
        )

        self.assertTrue(packet["market_history"]["available"])
        self.assertEqual(packet["market_history"]["price_rotations"][0]["symbol"], "ALPHA_USDT")
        self.assertEqual(packet["market_history"]["price_rotations"][0]["price_return_pct"], 20.0)
        self.assertEqual(packet["market_context"]["btc_window_return_pct"], 2.0)
        self.assertEqual(packet["market_context"]["btc_trend_flips"], 1)
        self.assertTrue(packet["order_flow"]["available"])
        self.assertEqual(packet["order_flow"]["symbols"][0]["flow_delta_pct"], 40.0)

    def test_weekly_report_builds_full_daily_payload_once(self):
        with patch.object(
            mt7,
            "_report_ticker_snapshot_for_date",
            return_value=(TICKERS, {"source": "test"}),
        ), patch.object(
            mt7,
            "_report_market_history_evidence",
            return_value={
                "market_history": {},
                "market_context": {},
                "structural_history": {},
                "order_flow": {},
            },
        ), patch.object(
            mt7,
            "_build_daily_data",
            wraps=mt7._build_daily_data,
        ) as build_daily:
            mt7._build_weekly_data("2026-W31")

        self.assertEqual(build_daily.call_count, 1)

    @patch.object(mt7, "fetch_bybit_tickers")
    @patch.object(mt7, "fetch_hl_meta_and_ctxs")
    @patch.object(mt7, "fetch_mexc")
    def test_cross_venue_collector_caches_normalized_three_venue_evidence(
        self,
        fetch_mexc,
        fetch_hl,
        fetch_bybit,
    ):
        self._create_cross_venue_tables()
        fetch_mexc.return_value = [
            {
                "symbol": "BTC_USDT",
                "lastPrice": "100",
                "fairPrice": "100.01",
                "indexPrice": "100",
                "bid1": "99.99",
                "ask1": "100.01",
                "amount24": "1000000",
                "holdVol": "2500",
                "fundingRate": "0.0001",
                "riseFallRate": "0.02",
            },
            {
                "symbol": "BAD_USDT",
                "lastPrice": "1",
                "amount24": "100000",
                "fundingRate": "0.0001",
                "riseFallRate": "0",
            },
        ]
        fetch_hl.return_value = (
            [{"name": "BTC", "maxLeverage": 50}],
            [{
                "markPx": "100.10",
                "oraclePx": "100.08",
                "prevDayPx": "98",
                "dayNtlVlm": "900000",
                "openInterest": "1000",
                "funding": "0.00001",
            }],
        )
        fetch_bybit.return_value = [
            {
                "symbol": "BTCUSDT",
                "lastPrice": "100.05",
                "markPrice": "100.04",
                "indexPrice": "100.03",
                "bid1Price": "100.04",
                "ask1Price": "100.06",
                "turnover24h": "800000",
                "openInterest": "900",
                "openInterestValue": "90045",
                "fundingRate": "0.00008",
                "fundingIntervalHour": "8",
                "price24hPcnt": "0.019",
            },
            {
                "symbol": "BADUSDT",
                "lastPrice": "100",
                "turnover24h": "100000",
                "fundingRate": "0.0001",
                "fundingIntervalHour": "8",
                "price24hPcnt": "0",
            },
        ]

        summary = mt7._collect_cross_venue_snapshot()

        self.assertEqual(summary["matched_three_venue"], 1)
        self.assertEqual(summary["matched_two_venue"], 2)
        self.assertEqual(summary["stored_rows"], 5)
        con = sqlite3.connect(mt7.DB_PATH)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM cross_venue_snapshots ORDER BY exchange"
        ).fetchall()
        con.close()
        self.assertEqual(len(rows), 5)
        mexc = next(row for row in rows if row["exchange"] == "MEXC" and row["base_asset"] == "BTC")
        hl = next(row for row in rows if row["exchange"] == "HYPERLIQUID")
        self.assertIsNone(mexc["open_interest_usd"])
        self.assertEqual(mexc["funding_interval_assumed"], 1)
        self.assertAlmostEqual(hl["funding_8h_equiv"], 0.00008)

        report_date = summary["captured_at"][:10]
        packet = mt7._report_market_history_evidence(
            report_date,
            focus_symbols=["BTC_USDT"],
        )
        cross = packet["cross_venue"]
        self.assertTrue(cross["available"])
        self.assertEqual(cross["matched_three_venue"], 1)
        self.assertEqual(cross["symbol_mismatches"][0]["symbol"], "BAD_USDT")
        dislocation = cross["focus_assets"][0]
        self.assertEqual(dislocation["symbol"], "BTC_USDT")
        self.assertAlmostEqual(
            dislocation["raw_price_gap_bps"] - dislocation["adjusted_price_gap_bps"],
            8.0,
            places=1,
        )
        response = mt7.app.test_client().get(
            f"/api/intelligence/cross-venue-evidence?date={report_date}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["evidence"]["available"])

    def test_cached_first_paint_does_not_wait_for_ai(self):
        with patch.object(
            mt7,
            "_report_ticker_snapshot_for_date",
            return_value=(TICKERS, {"source": "test"}),
        ), patch.object(
            mt7,
            "_call_report_ai",
        ) as report_ai:
            report = mt7._load_or_build_report("daily", "2026-07-28")

        report_ai.assert_not_called()
        self.assertEqual(report["narrative_source"], "deterministic_fast")

    def test_official_source_parsers_preserve_publication_and_effective_time(self):
        mexc_html = """
        <a title="Maximum Leverage Adjusted for ZILUSDT Futures [Jul 28, 2026, 23:30 (UTC)]"
           href="/announcements/article/maximum-leverage-adjusted-for-zilusdt-futures-jul-28-2026-23-17827791537121">
           <h3>Maximum Leverage Adjusted</h3>
        </a>
        <time dateTime="2026-07-29T02:10:03.000Z">32 minutes ago</time>
        """
        mexc = mt7._parse_mexc_announcements(mexc_html, {"ZIL"})
        self.assertEqual(len(mexc), 1)
        self.assertEqual(mexc[0]["event_type"], "leverage_change")
        self.assertEqual(mexc[0]["affected_assets"], ["ZIL_USDT"])
        self.assertEqual(mexc[0]["published_at"], "2026-07-29T02:10:03")
        self.assertEqual(mexc[0]["event_at"], "2026-07-28T23:30:00")
        self.assertEqual(mexc[0]["source_time_quality"], "published_and_effective")

        bybit = mt7._parse_bybit_announcements({
            "result": {"list": [{
                "id": "bybit-1",
                "title": "New BTCUSDT Contract Listing",
                "description": "Trading starts soon.",
                "url": "https://announcements.bybit.com/article/1",
                "dateTimestamp": "1785283200000",
                "startDateTimestamp": "1785369600000",
                "type": {"key": "new_crypto"},
            }]}
        }, {"BTC"})
        self.assertEqual(bybit[0]["event_type"], "listing")
        self.assertTrue(bybit[0]["scheduled"])
        self.assertEqual(bybit[0]["affected_assets"], ["BTC_USDT"])

        status = mt7._parse_hyperliquid_status({
            "incidents": [{
                "id": "hl-1",
                "name": "API outage",
                "status": "investigating",
                "impact": "major",
                "created_at": "2026-07-28T18:00:00Z",
                "shortlink": "https://stspg.io/hl-1",
                "incident_updates": [{"body": "The team is investigating."}],
            }]
        })
        self.assertEqual(status[0]["event_type"], "venue_incident")
        self.assertEqual(status[0]["severity"], "high")
        self.assertEqual(status[0]["affected_venues"], ["HYPERLIQUID"])

    def test_federal_reserve_parsers_create_timestamped_macro_events(self):
        rss = """ï»¿<?xml version="1.0"?>
        <rss><channel><item>
          <title>Federal Reserve issues FOMC statement</title>
          <link>https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm</link>
          <guid>monetary20260729a</guid>
          <description>Statement regarding monetary policy.</description>
          <pubDate>Wed, 29 Jul 2026 18:00:00 GMT</pubDate>
        </item></channel></rss>"""
        releases = mt7._parse_fed_monetary_rss(rss)
        self.assertEqual(len(releases), 1)
        self.assertEqual(releases[0]["event_type"], "monetary_policy")
        self.assertEqual(releases[0]["published_at"], "2026-07-29T18:00:00")

        calendar = """
        <a id="2026">2026 FOMC Meetings</a>
        <div class="fomc-meeting__month"><strong>September</strong></div>
        <div class="fomc-meeting__date">15-16*</div>
        <a id="2025">2025 FOMC Meetings</a>
        """
        meetings = mt7._parse_fomc_calendar(calendar)
        meeting = next(row for row in meetings if row["external_id"] == "fomc-2026-09-16")
        self.assertEqual(meeting["event_at"], "2026-09-16T18:00:00")
        self.assertTrue(meeting["scheduled"])
        self.assertTrue(meeting["raw_meta"]["projections"])

    def test_catalyst_cache_enriches_yasmin_and_never_fetches_at_report_time(self):
        self._create_catalyst_tables()
        now = mt7.datetime.utcnow().replace(microsecond=0)
        event = mt7._catalyst_event(
            source_key="mexc_announcements",
            source_name="MEXC Announcements",
            source_kind="exchange_announcement",
            source_url="https://www.mexc.com/announcements/article/test",
            external_id="test-1",
            title="Maximum Leverage Adjusted for ALPHAUSDT Futures",
            published_at=now,
            event_at=now + mt7.timedelta(hours=2),
            assets=["ALPHA_USDT"],
            venues=["MEXC"],
            scheduled=True,
        )
        with patch.object(
            mt7,
            "_fetch_catalyst_sources",
            return_value=({"mexc_announcements": [event]}, {}),
        ):
            summary = mt7._collect_catalyst_events()
        self.assertEqual(summary["new_events"], 1)

        date_str = now.strftime("%Y-%m-%d")
        with patch.object(mt7, "_fetch_catalyst_sources") as source_fetch:
            packet = mt7._report_market_history_evidence(
                date_str,
                focus_symbols=["ALPHA_USDT"],
            )
        source_fetch.assert_not_called()
        catalysts = packet["catalysts"]
        self.assertTrue(catalysts["available"])
        self.assertEqual(catalysts["focus_events"][0]["event_type"], "leverage_change")
        self.assertEqual(catalysts["primary_source_count"], 1)

        data = mt7._build_daily_data(date_str, ticker_snapshot=TICKERS)
        mt7._enrich_report_intelligence(data)
        yasmin = data["analyst_briefs"]["news"]
        daria = data["analyst_briefs"]["narrative_debate"]
        self.assertEqual(yasmin["coverage"], "primary_source_cached")
        self.assertIn("published", yasmin["read"].lower())
        self.assertIn("effective", yasmin["read"].lower())
        self.assertIn("does not prove", daria["read"].lower())
        self.assertIn("catalyst_watch", mt7._build_deterministic_report_narrative(data))

        response = mt7.app.test_client().get(
            f"/api/intelligence/catalyst-evidence?date={date_str}&symbols=ALPHA_USDT"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["evidence"]["available"])

    def test_tokenomics_cache_enriches_priya_without_inventing_wallet_flows(self):
        self._create_tokenomics_tables()
        now = mt7.datetime.utcnow().replace(microsecond=0)
        unlock_at = now + mt7.timedelta(days=10)
        source_rows = {
            "coin_gecko_markets": [{
                "id": "arbitrum",
                "name": "Arbitrum",
                "_mt7_symbol": "ARB",
                "_mapping_quality": "curated_exact",
                "current_price": 2,
                "market_cap": 50_000_000,
                "fully_diluted_valuation": 200_000_000,
                "circulating_supply": 25_000_000,
                "total_supply": 100_000_000,
                "max_supply": 100_000_000,
                "total_volume": 5_000_000,
                "last_updated": now.isoformat() + "Z",
            }],
            "defillama_unlocks": [{
                "protocolId": "arb-1",
                "name": "Arbitrum",
                "tSymbol": "ARB",
                "gecko_id": "arbitrum",
                "circSupply": 25_000_000,
                "maxSupply": 100_000_000,
                "tPrice": 2,
                "upcomingEvent": [{
                    "timestamp": int(unlock_at.timestamp()),
                    "noOfTokens": [1_000_000],
                    "category": "insiders",
                    "unlockType": "cliff",
                    "description": "Test schedule",
                }],
            }],
            "defillama_treasuries": [{
                "id": "arb-treasury",
                "name": "Arbitrum Treasury",
                "symbol": "ARB",
                "gecko_id": "arbitrum",
                "tvl": 10_000_000,
                "ownTokens": 8_000_000,
                "stablecoins": 1_000_000,
                "majors": 500_000,
                "others": 500_000,
                "mcap": 50_000_000,
            }],
        }
        with patch.object(
            mt7,
            "_fetch_tokenomics_sources",
            return_value=(source_rows, {}),
        ):
            summary = mt7._collect_tokenomics_evidence()

        self.assertEqual(summary["asset_snapshots"], 1)
        self.assertEqual(summary["unlock_events"], 1)
        date_str = now.strftime("%Y-%m-%d")
        with patch.object(mt7, "_fetch_tokenomics_sources") as source_fetch:
            packet = mt7._report_market_history_evidence(
                date_str,
                focus_symbols=["ARB_USDT"],
            )
        source_fetch.assert_not_called()
        tokenomics = packet["tokenomics"]
        self.assertTrue(tokenomics["available"])
        self.assertEqual(tokenomics["focus_assets"][0]["float_pct"], 25)
        self.assertEqual(tokenomics["focus_assets"][0]["fdv_market_cap_ratio"], 4)
        self.assertEqual(tokenomics["focus_unlocks"][0]["unlock_pct_float"], 4)
        self.assertEqual(tokenomics["focus_treasuries"][0]["own_token_share_pct"], 80)
        self.assertFalse(tokenomics["coverage"]["holder_concentration"])
        self.assertFalse(tokenomics["coverage"]["wallet_exchange_flows"])

        report_tickers = [{
            "symbol": "ARB_USDT",
            "riseFallRate": 0.12,
            "volume24": 50_000_000,
            "fundingRate": -0.0012,
        }, *TICKERS[1:]]
        data = mt7._build_daily_data(date_str, ticker_snapshot=report_tickers)
        mt7._enrich_report_intelligence(data)
        priya = data["analyst_briefs"]["tokenomics"]
        self.assertEqual(priya["coverage"], "aggregated_cached")
        self.assertIn("fdv/market-cap ratio", priya["read"].lower())
        self.assertIn("not evidence of an imminent sale", priya["read"].lower())
        self.assertIn("not a leverage or position-size increase", priya["trader_use"].lower())
        self.assertIn("tokenomics_watch", mt7._build_deterministic_report_narrative(data))

        response = mt7.app.test_client().get(
            f"/api/intelligence/tokenomics-evidence?date={date_str}&symbols=ARB_USDT"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["evidence"]["available"])

    def test_social_cache_enriches_hari_without_creating_directional_sentiment(self):
        self._create_social_tables()
        now = mt7.datetime.utcnow().replace(microsecond=0)
        posts = []
        for idx in range(20):
            posts.append({
                "uri": f"at://did:plc:{idx}/app.bsky.feed.post/post{idx}",
                "indexedAt": (now - mt7.timedelta(minutes=idx)).isoformat() + "Z",
                "author": {"handle": f"author{idx}.example.com"},
                "record": {
                    "text": f"Bitcoin market discussion sample {idx}",
                    "createdAt": (now - mt7.timedelta(minutes=idx)).isoformat() + "Z",
                },
                "likeCount": idx,
                "repostCount": idx // 3,
                "replyCount": idx // 4,
            })
        with patch.object(
            mt7,
            "_fetch_social_sources",
            return_value=({"bitcoin": {"posts": posts}}, {}),
        ):
            summary = mt7._collect_social_evidence()
        self.assertEqual(summary["stored_topics"], 1)
        self.assertEqual(summary["total_posts"], 20)

        date_str = now.strftime("%Y-%m-%d")
        with patch.object(mt7, "_fetch_social_sources") as source_fetch:
            packet = mt7._report_market_history_evidence(
                date_str,
                focus_symbols=["BTC_USDT"],
            )
        source_fetch.assert_not_called()
        social = packet["social"]
        self.assertTrue(social["available"])
        self.assertEqual(social["focus_topics"][0]["unique_authors"], 20)
        self.assertIn("single_social_source", social["focus_topics"][0]["quality_flags"])

        data = mt7._build_daily_data(date_str, ticker_snapshot=TICKERS)
        data["specialist_evidence"]["social"] = social
        mt7._enrich_report_intelligence(data)
        hari = data["analyst_briefs"]["sentiment"]
        self.assertEqual(hari["coverage"], "direct_activity_cached")
        self.assertIn("unique authors", hari["read"].lower())
        self.assertIn("never authorizes an entry", hari["trader_use"].lower())
        self.assertNotIn("bullish sentiment", hari["read"].lower())
        self.assertIn("social_watch", mt7._build_deterministic_report_narrative(data))

        response = mt7.app.test_client().get(
            f"/api/intelligence/social-evidence?date={date_str}&symbols=BTC_USDT"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["evidence"]["available"])

    def test_governed_source_parser_preserves_authority_and_reviewed_mapping(self):
        rss = """<?xml version="1.0"?><rss><channel><item>
          <title>ARFC: Review reserve configuration</title>
          <link>https://governance.aave.com/t/example</link>
          <guid>aave-1</guid>
          <description>Governance proposal discussion.</description>
          <pubDate>Wed, 29 Jul 2026 18:00:00 GMT</pubDate>
        </item></channel></rss>"""
        rows = mt7._parse_official_rss(
            rss,
            source_key="aave_governance",
            source_name="Aave Governance Forum",
            source_kind="official_governance_forum",
            fallback_url="https://governance.aave.com/latest.rss",
            fixed_assets=["AAVE_USDT"],
            event_type="governance",
            authority_scope="official_protocol_governance_forum",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["affected_assets"], ["AAVE_USDT"])
        self.assertEqual(rows[0]["event_type"], "governance")
        self.assertEqual(
            rows[0]["raw_meta"]["symbol_resolution"],
            "reviewed_protocol_mapping",
        )

    def test_report_evidence_audit_is_forward_only_and_has_no_scoring_authority(self):
        self._create_social_tables()
        now = mt7.datetime.utcnow().replace(microsecond=0)
        captured = now - mt7.timedelta(hours=1)
        logged = now - mt7.timedelta(minutes=30)
        con = sqlite3.connect(mt7.DB_PATH)
        con.execute(
            "INSERT INTO social_activity_snapshots "
            "(captured_at,source_key,source_name,source_url,topic_key,query_text,symbol,"
            "post_count,unique_authors,total_likes,total_reposts,total_replies,"
            "top_author_share_pct,duplicate_text_share_pct,posts_per_hour,"
            "engagement_per_post,activity_vs_baseline_pct,baseline_observations,"
            "evidence_quality,quality_flags_json,examples_json,source_time_quality) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                captured.isoformat(), "bluesky_public_search", "Bluesky Public AppView",
                "https://docs.bsky.app", "bitcoin", "bitcoin", "BTC_USDT", 20, 20,
                40, 4, 5, 5, 0, 3.3, 2.45, 25, 8, "usable_single_source",
                '["single_social_source"]', "[]", "post_timestamp",
            ),
        )
        con.execute(
            "INSERT INTO signals "
            "(symbol,exchange,direction,conviction,result,pnl_pct,strategy_key,"
            "funding_rate,logged_at,signal_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "BTC_USDT", "MEXC", "LONG", 70, "WIN", 2.5, "balanced",
                -0.0002, logged.isoformat(), "{}",
            ),
        )
        con.commit()
        con.row_factory = sqlite3.Row
        audit = mt7._report_evidence_outcome_audit(
            con,
            (now + mt7.timedelta(minutes=1)).isoformat(),
            days=30,
        )
        con.close()
        self.assertFalse(audit["scoring_eligible"])
        self.assertEqual(
            audit["signal_cohorts"]["social_activity"]["with_evidence"]["sample"],
            1,
        )
        self.assertIn("observational", audit["limits"][0].lower())

    def test_weekly_synthesis_is_distinct_from_daily_contract(self):
        data = mt7._build_daily_data("2026-07-28", ticker_snapshot=TICKERS)
        data["weekly_summary"] = {
            "signals": 12,
            "blocked": 4,
            "dominant_regime": "funding_crowded",
            "regime_counts": {"funding_crowded": 9, "choppy": 3},
            "mixed_desk_days": 2,
            "active_signal_days": 5,
        }
        data["daily_rollup"] = [
            {"market_pulse": {"signals": 1, "blocked": 0}},
            {"market_pulse": {"signals": 4, "blocked": 2}},
        ]
        data["week"] = "2026-W31"
        data["market_pulse"].update({
            "signals": 12,
            "blocked": 4,
            "dominant_regime": "funding_crowded",
            "scope": "weekly",
        })
        mt7._enrich_report_intelligence(data, weekly=True)
        daily = mt7._build_deterministic_report_narrative(data, weekly=False)
        weekly = mt7._build_deterministic_report_narrative(data, weekly=True)
        daily_sentences = mt7._report_sentences(daily)
        weekly_sentences = mt7._report_sentences(weekly)
        overlap = len(daily_sentences & weekly_sentences) / max(1, len(weekly_sentences))
        self.assertLess(overlap, 0.30)
        self.assertIn("across 5/7 calendar days", weekly["trader_open"])
        self.assertIn("three layers", weekly["week_ahead"])

    def test_role_focus_uses_specialty_evidence_not_largest_mover(self):
        data = mt7._build_daily_data("2026-07-28", ticker_snapshot=TICKERS)
        data["explosive_move"] = {
            "symbol": "GAMMA_USDT",
            "change_24h_pct": 80,
            "funding_rate": 0.0,
            "volume_24h": 1_000_000,
        }
        data["specialist_evidence"]["order_flow"] = {
            "symbols": [
                {"symbol": "BETA_USDT", "flow_delta_pct": 5, "average_imbalance": 0.1, "trade_count": 30},
                {"symbol": "ALPHA_USDT", "flow_delta_pct": -45, "average_imbalance": -0.4, "trade_count": 20},
            ]
        }
        focus = mt7._report_role_focus(data)
        self.assertEqual(focus["funding"]["symbol"], "ALPHA_USDT")
        self.assertEqual(focus["microstructure"]["symbol"], "ALPHA_USDT")
        self.assertNotEqual(focus["funding"]["symbol"], data["explosive_move"]["symbol"])
        data["analyst_focus"] = focus
        briefs = mt7._report_analyst_briefs(data)
        self.assertIn("selection_reason", briefs["funding"])

    def test_unknown_evidence_yields_wait_verdict_and_explicit_abstention(self):
        data = {
            "market_pulse": {"signals": 0, "blocked": 0, "dominant_regime": "unknown"},
            "market_breadth": {"total": 0},
            "paper_desk": {},
            "funding_heatmap": {},
            "specialist_evidence": {},
            "action_matrix": {"posture": "wait"},
        }
        mt7._enrich_report_intelligence(data)
        narrative = mt7._build_deterministic_report_narrative(data)
        self.assertEqual(data["desk_verdict"]["posture"], "wait")
        self.assertIn("abstention", narrative["regime_forecast"].lower())
        self.assertNotIn("0.00%", narrative["trader_open"])
        self.assertIn("no leverage or position-size increase", data["desk_verdict"]["risk_disclosure"].lower())

    def test_report_claims_are_forward_only_and_descriptive(self):
        con = sqlite3.connect(mt7.DB_PATH)
        con.execute(
            "CREATE TABLE report_claims ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, report_type TEXT, report_key TEXT, "
            "generated_at TEXT, analyst_key TEXT, claim_type TEXT, symbol TEXT DEFAULT '', "
            "window_start TEXT, window_end TEXT, metric TEXT, comparator TEXT, threshold REAL, "
            "expected_value TEXT, baseline_value REAL, evidence_json TEXT, source_section TEXT, "
            "status TEXT DEFAULT 'pending', resolved_at TEXT, observed_value TEXT, "
            "resolution_note TEXT, "
            "UNIQUE(report_type,report_key,analyst_key,claim_type,symbol))"
        )
        con.commit()
        con.close()
        today = mt7.datetime.utcnow().strftime("%Y-%m-%d")
        data = {
            "market_pulse": {
                "signals": 10,
                "dominant_regime": "funding_crowded",
                "desk_agreement": "high",
            },
            "market_breadth": {"total": 100, "advancers": 65, "decliners": 30},
            "analyst_focus": {
                "funding": {
                    "item": {"symbol": "ALPHA_USDT", "funding_rate": -0.0012, "change_24h_pct": 2},
                    "selection_reason": "largest imbalance",
                }
            },
        }
        inserted = mt7._register_report_claims(
            "daily",
            today,
            mt7.datetime.utcnow().isoformat() + "Z",
            data,
        )
        packet = mt7._report_claims_packet()
        self.assertEqual(inserted, 3)
        self.assertEqual(packet["authority"], "descriptive_only")
        self.assertEqual(packet["totals"]["pending"], 3)
        self.assertTrue(all(row["status"] == "pending" for row in packet["claims"]))
        repeated = mt7._register_report_claims(
            "daily",
            today,
            mt7.datetime.utcnow().isoformat() + "Z",
            data,
        )
        self.assertEqual(repeated, 0)
        self.assertEqual(mt7._report_claims_packet()["totals"]["pending"], 3)
        historical = mt7._register_report_claims(
            "daily",
            "2020-01-01",
            mt7.datetime.utcnow().isoformat() + "Z",
            data,
        )
        self.assertEqual(historical, 0)

    def test_narrative_trust_labels_distinguish_ai_polish(self):
        deterministic = mt7._report_narrative_trust("deterministic_fast")
        polished = mt7._report_narrative_trust("ai")
        self.assertEqual(deterministic["trader_open"]["class"], "deterministic_interpretation")
        self.assertEqual(polished["trader_open"]["class"], "ai_polished")
        self.assertIn("signals", deterministic["trader_open"]["evidence"])

    def test_desk_verdict_surfaces_current_regime_strategy_fit_with_sample_gate(self):
        data = {
            "market_pulse": {
                "signals": 12,
                "blocked": 0,
                "dominant_regime": "funding_crowded",
                "desk_agreement": "high",
            },
            "market_breadth": {"total": 100, "advancers": 60, "decliners": 35},
            "action_matrix": {"posture": "selective"},
            "strategy_regime_perf": [
                {
                    "strategy": "balanced",
                    "regime": "funding_crowded",
                    "sample": 8,
                    "avg_pnl_pct": 2.5,
                },
                {
                    "strategy": "funding_arb",
                    "regime": "funding_crowded",
                    "sample": 24,
                    "avg_pnl_pct": 1.2,
                },
            ],
        }
        verdict = mt7._report_desk_verdict(data)
        fit = verdict["strategy_fit"]
        self.assertTrue(fit["available"])
        self.assertEqual(fit["best_observed"]["strategy"], "balanced")
        self.assertFalse(fit["decision_ready"])
        self.assertIn("N=8", verdict["evidence"][-1])
        self.assertIn("descriptive", fit["note"])

    def test_cross_desk_debate_keeps_opposing_cases_and_advisory_resolution(self):
        debate = mt7._report_desk_debate({
            "market_pulse": {"dominant_regime": "funding_crowded"},
            "market_breadth": {"total": 100, "advancers": 65, "decliners": 30},
            "funding_heatmap": {
                "extreme_negative": [{"symbol": "A_USDT"}],
                "extreme_positive": [{"symbol": "B_USDT"}],
            },
            "paper_desk": {"avg_pnl_pct": 1.5, "closed": 30},
            "disagreements": [{"symbol": "A_USDT"}],
            "specialist_evidence": {
                "cross_venue": {"available": False},
                "catalysts": {"available": False},
                "tokenomics": {"available": False},
            },
        })
        self.assertTrue(debate["upside_case"])
        self.assertTrue(debate["downside_case"])
        self.assertIn(debate["lean"], {"mixed", "conditional_upside", "conditional_downside"})
        self.assertEqual(debate["authority"], "advisory_only")
        self.assertNotIn("enter", debate["resolution"].lower())

    def test_current_daily_cache_expires_without_invoking_ai(self):
        today = mt7.datetime.utcnow().strftime("%Y-%m-%d")
        Path(mt7.REPORTS_DIR).mkdir(parents=True, exist_ok=True)
        cache_path = Path(mt7._report_cache_path("daily", today))
        cache_path.write_text(json.dumps({
            "success": True,
            "schema_version": mt7.REPORT_SCHEMA_VERSION,
            "type": "daily",
            "key": today,
            "data": {},
            "narrative": {},
        }))
        old = mt7.time.time() - 901
        os.utime(cache_path, (old, old))
        with patch.object(mt7, "_build_daily_data", return_value={}) as build, patch.object(
            mt7,
            "_enrich_report_intelligence",
            side_effect=lambda data, weekly=False: data,
        ), patch.object(
            mt7,
            "_build_deterministic_report_narrative",
            return_value={},
        ), patch.object(mt7, "_call_report_ai") as report_ai:
            mt7._load_or_build_report("daily", today)
        build.assert_called_once()
        report_ai.assert_not_called()


if __name__ == "__main__":
    unittest.main()
