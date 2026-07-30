import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import app as mt7
from lib.research_orchestrator import (
    conflict_report,
    deterministic_arm,
    effective_sample,
    normalize_contract,
    permutation_delta_test,
    scheduler_plan,
    trader_experiment_brief,
    validate_contract,
)


class ResearchOrchestratorPureTests(unittest.TestCase):
    def test_type_contract_routes_to_specific_evaluator(self):
        contract = normalize_contract({
            "rule_type": "stop_rule",
            "research_shadow_tag": "atr_stop",
            "target_strategy": "mean_reversion",
            "hypothesis": "A wider ATR stop reduces premature exits.",
            "field_readiness": {"pre_entry": ["atr_pct"]},
            "variant_policy": {"stop_atr_multiple": 1.5},
        })
        self.assertEqual(contract["idea_type"], "stop_rule")
        self.assertEqual(contract["decision_surface"], "loss_management")
        self.assertEqual(contract["evaluator"], "same_entry_path")
        self.assertTrue(validate_contract(contract)["valid"])

    def test_conflict_is_same_strategy_and_surface_not_shared_feature(self):
        funding_gate = normalize_contract({
            "rule_type": "risk_gate",
            "research_shadow_tag": "funding_gate",
            "target_strategy": "funding_arb",
            "hypothesis": "Block crowded funding.",
            "field_readiness": {"pre_entry": ["funding_rate"]},
            "runtime_wired": True,
        })
        second_gate = normalize_contract({
            "rule_type": "entry_gate",
            "research_shadow_tag": "flow_gate",
            "target_strategy": "funding_arb",
            "hypothesis": "Require flow.",
            "field_readiness": {"pre_entry": ["flow_score"]},
            "runtime_wired": True,
        })
        other_strategy = normalize_contract({
            "rule_type": "entry_gate",
            "research_shadow_tag": "flow_gate_momentum",
            "target_strategy": "momentum_breakout",
            "hypothesis": "Require flow.",
            "field_readiness": {"pre_entry": ["flow_score"]},
            "runtime_wired": True,
        })
        self.assertTrue(conflict_report(funding_gate, second_gate)["conflicts"])
        self.assertFalse(conflict_report(funding_gate, other_strategy)["conflicts"])

    def test_assignment_is_stable(self):
        left = deterministic_arm("exp:1", "BTC_USDT", "2026-07-30T12:00:00Z", 30)
        right = deterministic_arm("exp:1", "BTC_USDT", "2026-07-30T12:00:00Z", 30)
        self.assertEqual(left, right)
        self.assertIn(left["arm"], {"treatment", "control"})

    def test_scheduler_parallelizes_orthogonal_work(self):
        first = normalize_contract({
            "rule_type": "risk_gate",
            "research_shadow_tag": "funding_gate",
            "target_strategy": "funding_arb",
            "hypothesis": "Block crowded funding.",
            "field_readiness": {"pre_entry": ["funding_rate"]},
            "runtime_wired": True,
        })
        second = normalize_contract({
            "rule_type": "stop_rule",
            "research_shadow_tag": "mean_stop",
            "target_strategy": "mean_reversion",
            "hypothesis": "Use an ATR stop.",
            "field_readiness": {"pre_entry": ["atr_pct"]},
            "variant_policy": {"stop_atr_multiple": 1.5},
        })
        plan = scheduler_plan(
            [
                {"id": "a", "contract": first, "priority_score": 2},
                {"id": "b", "contract": second, "priority_score": 1},
            ],
            [],
            max_behavioral=2,
        )
        self.assertEqual(len(plan["starts"]), 2)
        self.assertTrue(all(x["requires_user_approval"] for x in plan["starts"]))
        self.assertFalse(plan["automatic_behavior_change"])

    def test_effective_sample_clusters_symbol_day(self):
        rows = [
            {"symbol": "BTC_USDT", "closed_at": "2026-07-29T01:00:00Z"},
            {"symbol": "BTC_USDT", "closed_at": "2026-07-29T02:00:00Z"},
            {"symbol": "ETH_USDT", "closed_at": "2026-07-29T03:00:00Z"},
            {"symbol": "BTC_USDT", "closed_at": "2026-07-30T01:00:00Z"},
        ]
        sample = effective_sample(rows)
        self.assertEqual(sample["raw_count"], 4)
        self.assertEqual(sample["symbol_day_count"], 3)
        self.assertEqual(sample["market_day_count"], 2)

    def test_permutation_placebo_distinguishes_clear_separation(self):
        treatment = [{"pnl_pct": 2.0} for _ in range(20)]
        control = [{"pnl_pct": -1.0} for _ in range(20)]
        result = permutation_delta_test(treatment, control, iterations=500)
        self.assertTrue(result["available"])
        self.assertLess(result["one_sided_p_value"], 0.05)

    def test_trader_brief_explains_change_progress_and_authority(self):
        contract = normalize_contract({
            "rule_type": "entry_gate",
            "research_shadow_tag": "flow_gate",
            "target_strategy": "momentum_breakout",
            "hypothesis": "Require directional order-flow confirmation.",
            "field_readiness": {"pre_entry": ["flow_score"], "missing": []},
            "runtime_wired": True,
            "minimum_closed_trades": 50,
            "minimum_elapsed_days": 7,
        })
        brief = trader_experiment_brief(
            contract,
            context={
                "title": "Flow-confirmed momentum",
                "thesis": "Flow disagreement may identify thin breakouts.",
                "entry_filter_rule": "The challenger requires confirmation.",
                "reject_filter_rule": "Unconfirmed treatment opportunities are blocked.",
                "promotion_criteria": "Positive net expectancy versus control.",
                "rollback_condition": "Stop if net expectancy is worse.",
            },
            state={
                "status": "awaiting_approval",
                "assignments": {"closed": 0, "treatment": 0, "control": 0},
                "elapsed_days": 0,
            },
        )
        self.assertIn("30% of eligible momentum_breakout", brief["treatment"])
        self.assertIn("70% of eligible momentum_breakout", brief["control"])
        self.assertEqual(brief["progress"]["target_closed"], 50)
        self.assertIn("explicit user approval", brief["authority"])
        self.assertTrue(brief["blockers"])


class ResearchOrchestratorRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old = {
            "DB_PATH": mt7.DB_PATH,
            "PAPER_CONFIG_PATH": mt7.PAPER_CONFIG_PATH,
            "STRATEGY_OVERRIDES_PATH": mt7.STRATEGY_OVERRIDES_PATH,
            "EXPERIMENT_LEDGER_PATH": mt7.EXPERIMENT_LEDGER_PATH,
        }
        mt7.DB_PATH = str(root / "research.db")
        mt7.PAPER_CONFIG_PATH = str(root / "paper_config.json")
        mt7.STRATEGY_OVERRIDES_PATH = str(root / "strategy_overrides.json")
        mt7.EXPERIMENT_LEDGER_PATH = str(root / "experiment_ledger.json")
        Path(mt7.PAPER_CONFIG_PATH).write_text(
            json.dumps({"account_balance_usd": 200, "flow_required": False}),
            encoding="utf-8",
        )
        Path(mt7.STRATEGY_OVERRIDES_PATH).write_text("{}", encoding="utf-8")
        Path(mt7.EXPERIMENT_LEDGER_PATH).write_text("[]", encoding="utf-8")
        mt7.init_db()

    def tearDown(self):
        for key, value in self.old.items():
            setattr(mt7, key, value)
        self.tmp.cleanup()

    def _item(self, tag="research_test_gate", strategy="funding_arb"):
        return {
            "research_shadow_tag": tag,
            "title": "Funding gate forward test",
            "thesis": "Crowded funding without confirming flow may trap entries.",
            "strategy_shape": "Block only predeclared crowding traps in the challenger arm.",
            "entry_filter_rule": "Classify funding, direction, trend, and flow before entry.",
            "reject_filter_rule": "Block the two declared crowding-trap buckets.",
            "expected_failure_mode": "Funding can remain extreme for long periods.",
            "promotion_criteria": "Positive net expectancy versus untouched control.",
            "rollback_condition": "Stop if the challenger underperforms control.",
            "source_count": 2,
            "source_titles": ["Paper A", "Paper B"],
            "target_strategy": strategy,
            "candidate_type": "risk_gate",
            "field_readiness": {
                "pre_entry": ["funding_rate"],
                "missing": [],
                "retrospective": [],
            },
            "rule_control": {
                "rule_type": "risk_gate",
                "scope": strategy,
                "executable": {"can_paper_apply": True},
            },
            "promotion_progress": {"target": 50},
        }

    def test_prepare_activate_assign_and_stop(self):
        prepared = mt7._research_prepare_experiment(self._item(), actor="test")
        self.assertTrue(prepared["created"])
        experiment_id = prepared["experiment"]["experiment_id"]
        self.assertEqual(prepared["experiment"]["status"], "awaiting_approval")

        active = mt7._research_activate_experiment(
            experiment_id,
            actor="test",
            reason="Test approval with explicit scope.",
        )
        self.assertEqual(active["lifecycle_state"], "paper_challenger")

        sig = {
            "symbol": "BTC_USDT",
            "_strategy_key": "funding_arb",
            "logged_at": "2026-07-30T12:00:00Z",
        }
        first = mt7._research_assignments_for_signal(sig)
        second = mt7._research_assignments_for_signal(dict(sig))
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["assignment_key"], second[0]["assignment_key"])
        self.assertEqual(first[0]["arm"], second[0]["arm"])

        mt7._research_record_assignments(
            first,
            paper_trade_id=None,
            signal_id=None,
            decision="accepted",
        )
        snapshot = mt7._research_experiment_orchestrator_payload()
        card = next(
            item for item in snapshot["experiments"]
            if item["experiment_id"] == experiment_id
        )
        self.assertEqual(card["assignments"]["treatment"] + card["assignments"]["control"], 1)
        self.assertEqual(card["brief"]["source_evidence"]["source_count"], 2)
        self.assertIn("declared crowding-trap", card["brief"]["treatment"])
        self.assertIn("Paper-only", card["brief"]["authority"])

        stopped = mt7._research_stop_source_experiments(
            "research_test_gate",
            status="rolled_back",
            actor="test",
            reason="Test rollback preserves the champion.",
        )
        self.assertEqual(stopped, [experiment_id])

    def test_conflicting_active_experiment_is_rejected(self):
        first = mt7._research_prepare_experiment(self._item("gate_one"), actor="test")
        mt7._research_activate_experiment(
            first["experiment"]["experiment_id"],
            actor="test",
            reason="Approve first isolated test.",
        )
        second = mt7._research_prepare_experiment(self._item("gate_two"), actor="test")
        with self.assertRaisesRegex(ValueError, "conflicts with active"):
            mt7._research_activate_experiment(
                second["experiment"]["experiment_id"],
                actor="test",
                reason="This should conflict.",
            )

    def test_promotion_creates_untouched_confirmation_child(self):
        prepared = mt7._research_prepare_experiment(
            self._item("promotion_gate"),
            actor="test",
        )
        experiment_id = prepared["experiment"]["experiment_id"]
        mt7._research_activate_experiment(
            experiment_id,
            actor="test",
            reason="Approve initial Paper challenger.",
        )
        con = sqlite3.connect(mt7.DB_PATH)
        con.execute(
            """UPDATE learning_experiments
               SET status='promotion_candidate',
                   lifecycle_state='promotion_candidate',
                   result_json='{"verdict":"promotion_candidate"}'
               WHERE experiment_id=?""",
            (experiment_id,),
        )
        con.commit()
        con.close()

        promoted = mt7._research_promote_experiment(
            experiment_id,
            actor="test",
            reason="Forward challenger passed every gate.",
        )
        child_id = promoted["confirmation_experiment_id"]
        self.assertEqual(promoted["confirmation_treatment_pct"], 80)
        self.assertFalse(promoted["live_authority_granted"])

        con = sqlite3.connect(mt7.DB_PATH)
        child = con.execute(
            """SELECT status, probation_stage, champion_experiment_id,
                      treatment_pct
               FROM learning_experiments WHERE experiment_id=?""",
            (child_id,),
        ).fetchone()
        con.execute(
            """UPDATE learning_experiments
               SET status='promotion_candidate'
               WHERE experiment_id=?""",
            (child_id,),
        )
        con.commit()
        con.close()
        self.assertEqual(child, ("collecting", "confirmation", experiment_id, 80))

        confirmed = mt7._research_promote_experiment(
            child_id,
            actor="test",
            reason="Untouched confirmation also passed.",
        )
        self.assertEqual(confirmed["lifecycle_state"], "confirmed")
        self.assertFalse(confirmed["live_authority_granted"])


if __name__ == "__main__":
    unittest.main()
