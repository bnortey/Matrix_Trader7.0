import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import app as mt7


def sizing_config(**overrides):
    config = {
        "account_balance_usd": 200.0,
        "risk_pct_per_trade": 5.0,
        "max_open_positions": 4,
        "paper_sizing_mode": "fixed",
        "compound_equity_cap_usd": 1000.0,
        "compound_equity_floor_usd": 50.0,
        "compound_drawdown_fallback_pct": 20.0,
    }
    config.update(overrides)
    return config


class PaperCompoundingPolicyTests(unittest.TestCase):
    def test_fixed_mode_preserves_existing_sizing_base(self):
        policy = mt7._paper_sizing_policy(
            sizing_config(),
            equity_rows=[(200.0, 100.0, "2026-01-01T00:00:00")],
        )

        self.assertEqual(policy["mode"], "fixed")
        self.assertEqual(policy["realized_equity_usd"], 400.0)
        self.assertEqual(policy["effective_sizing_base_usd"], 200.0)
        self.assertEqual(policy["target_risk_budget_usd"], 10.0)

    def test_compound_mode_uses_closed_realized_equity_and_cap(self):
        policy = mt7._paper_sizing_policy(
            sizing_config(
                paper_sizing_mode="compound_realized",
                compound_equity_cap_usd=300.0,
            ),
            equity_rows=[(200.0, 100.0, "2026-01-01T00:00:00")],
        )

        self.assertEqual(policy["realized_equity_usd"], 400.0)
        self.assertEqual(policy["effective_sizing_base_usd"], 300.0)
        self.assertEqual(policy["state"], "compound_capped")
        self.assertTrue(policy["cap_applied"])
        self.assertEqual(policy["target_risk_budget_usd"], 15.0)
        self.assertEqual(policy["maximum_single_notional_usd"], 75.0)
        self.assertEqual(policy["maximum_concurrent_notional_usd"], 300.0)

    def test_compound_mode_shrinks_after_realized_losses(self):
        policy = mt7._paper_sizing_policy(
            sizing_config(paper_sizing_mode="compound_realized"),
            equity_rows=[
                (200.0, 50.0, "2026-01-01T00:00:00"),
                (200.0, -25.0, "2026-01-02T00:00:00"),
            ],
        )

        self.assertEqual(policy["realized_equity_usd"], 250.0)
        self.assertEqual(policy["effective_sizing_base_usd"], 250.0)
        self.assertEqual(policy["target_risk_budget_usd"], 12.5)

    def test_current_drawdown_triggers_fixed_base_fallback(self):
        policy = mt7._paper_sizing_policy(
            sizing_config(paper_sizing_mode="compound_realized"),
            equity_rows=[
                (200.0, 400.0, "2026-01-01T00:00:00"),
                (500.0, -50.0, "2026-01-02T00:00:00"),
            ],
        )

        self.assertEqual(policy["realized_equity_usd"], 750.0)
        self.assertEqual(policy["current_drawdown_pct"], 25.0)
        self.assertEqual(policy["effective_sizing_base_usd"], 200.0)
        self.assertTrue(policy["drawdown_fallback_active"])
        self.assertEqual(policy["state"], "drawdown_fixed_fallback")

    def test_equity_floor_blocks_new_entries_without_phantom_capital(self):
        policy = mt7._paper_sizing_policy(
            sizing_config(paper_sizing_mode="compound_realized"),
            equity_rows=[(200.0, -80.0, "2026-01-01T00:00:00")],
        )

        self.assertEqual(policy["realized_equity_usd"], 40.0)
        self.assertEqual(policy["effective_sizing_base_usd"], 0.0)
        self.assertFalse(policy["entry_allowed"])
        self.assertEqual(policy["state"], "equity_floor_block")


class PaperCompoundingConfigApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_db = mt7.DB_PATH
        self.old_config = mt7.PAPER_CONFIG_PATH
        mt7.DB_PATH = str(root / "signals.db")
        mt7.PAPER_CONFIG_PATH = str(root / "paper_config.json")
        Path(mt7.PAPER_CONFIG_PATH).write_text(
            json.dumps(sizing_config()),
            encoding="utf-8",
        )
        con = sqlite3.connect(mt7.DB_PATH)
        con.execute(
            "CREATE TABLE paper_trades ("
            "id INTEGER PRIMARY KEY, status TEXT, size_usd REAL, "
            "pnl_pct REAL, closed_at TEXT)"
        )
        con.commit()
        con.close()
        mt7.app.config["TESTING"] = True

    def tearDown(self):
        mt7.DB_PATH = self.old_db
        mt7.PAPER_CONFIG_PATH = self.old_config
        self.tmp.cleanup()

    def test_enabling_compounding_requires_explicit_acknowledgement(self):
        with mt7.app.test_client() as client:
            blocked = client.patch(
                "/api/paper/config",
                json={"paper_sizing_mode": "compound_realized"},
            )
            accepted = client.patch(
                "/api/paper/config",
                json={
                    "paper_sizing_mode": "compound_realized",
                    "acknowledge_compounding_risk": True,
                },
            )

        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(accepted.status_code, 200)
        payload = accepted.get_json()
        self.assertEqual(payload["config"]["paper_sizing_mode"], "compound_realized")
        self.assertEqual(
            payload["sizing_policy"]["effective_sizing_base_usd"],
            200.0,
        )
        self.assertTrue(payload["config"]["current_cohort_started_at"])
        self.assertIn(
            "compound_realized",
            payload["config"]["current_cohort_label"],
        )

    def test_mode_change_is_blocked_while_position_is_active(self):
        con = sqlite3.connect(mt7.DB_PATH)
        con.execute(
            "INSERT INTO paper_trades "
            "(id, status, size_usd, pnl_pct, closed_at) "
            "VALUES (1, 'open', 50, NULL, NULL)"
        )
        con.commit()
        con.close()

        with mt7.app.test_client() as client:
            response = client.patch(
                "/api/paper/config",
                json={
                    "paper_sizing_mode": "compound_realized",
                    "acknowledge_compounding_risk": True,
                },
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("pending or open", response.get_json()["error"])

    def test_open_trade_is_excluded_from_realized_equity(self):
        con = sqlite3.connect(mt7.DB_PATH)
        con.executemany(
            "INSERT INTO paper_trades "
            "(id, status, size_usd, pnl_pct, closed_at) VALUES (?,?,?,?,?)",
            [
                (1, "closed", 200.0, 50.0, "2026-01-01T00:00:00"),
                (2, "open", 1000.0, 999.0, None),
            ],
        )
        con.commit()
        con.close()
        cfg = sizing_config(paper_sizing_mode="compound_realized")

        policy = mt7._paper_sizing_policy(cfg)

        self.assertEqual(policy["realized_equity_usd"], 300.0)
        self.assertEqual(policy["effective_sizing_base_usd"], 300.0)


if __name__ == "__main__":
    unittest.main()
