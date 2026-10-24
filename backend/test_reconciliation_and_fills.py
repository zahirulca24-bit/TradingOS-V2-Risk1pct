"""Unit and integration regression test suite for Step 6: Idempotency, Fills, and SL/TP Exchange Reconciliation.

Strictly DRY_RUN_ONLY: Zero live exchange connections, zero real orders.
All Pybit and exchange interactions use mock HTTP sessions.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from demo_risk_engine import authorize_execution_order
from demo_order_executor import execute_demo_plans
from db_adapter import (
    init_db,
    create_order_intent,
    get_order_intent,
    update_order_intent_status,
    reserve_atomic_slot,
    get_active_slot_reservations,
    update_daily_risk_pnl,
)
from reconciliation import (
    reconcile_order_intent,
    reconcile_all_pending_intents,
    verify_position_protection,
    audit_all_positions_protection,
)


class TestReconciliationAndFills(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_reconciliation.db"
        init_db(db_path=self.db_path)

        # Baseline valid account fixture
        self.valid_account = {
            "total_equity": 10000.0,
            "available_balance": 10000.0,
            "open_positions": 0,
            "open_positions_list": [],
            "pending_orders": 0,
            "pending_orders_list": [],
            "positions_error": False,
            "orders_error": False,
        }

        # Baseline valid specs fixture
        self.valid_specs = {
            "symbol": "BTCUSDT",
            "qty_step": "0.001",
            "min_qty": "0.001",
            "max_qty": "100.0",
            "min_notional": "5.0",
            "tick_size": "0.10",
        }

        # Baseline valid trade plan
        self.valid_plan = {
            "status": "PLAN_READY",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entry_reference": 50000.0,
            "sl": 49000.0,
            "tp": 52000.0,
            "rr": 2.0,
        }

        # Pre-populate verified daily risk record
        update_daily_risk_pnl(
            trading_date="2026-10-24",
            starting_equity=10000.0,
            current_equity=10000.0,
            drawdown_limit_pct=3.0,
            db_path=self.db_path,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_timeout_after_exchange_accepts_order_recovers_state(self):
        """When place_order raises a network timeout, reconciliation recovers ACCEPTED state from exchange."""
        reports = {
            "scanner": {"cycle_id": "2026-10-24T14:00:00Z"},
            "trade_plans": {"results": [self.valid_plan]},
        }

        mock_session = MagicMock()
        # place_order times out
        mock_session.place_order.side_effect = TimeoutError("Bybit Gateway Timeout")
        # but get_open_orders returns the order accepted on exchange
        order_link_id = "tos_24140000_BTCUSDT"
        mock_session.get_open_orders.return_value = {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "list": [
                    {
                        "orderId": "bybit_live_9999",
                        "orderLinkId": order_link_id,
                        "symbol": "BTCUSDT",
                        "orderStatus": "New",
                        "avgPrice": "0",
                        "cumExecQty": "0",
                    }
                ]
            },
        }

        with patch.dict(os.environ, {"BYBIT_DEMO_EXECUTION_ENABLED": "true"}):
            with patch("demo_order_executor.get_cached_instrument_specs", return_value=self.valid_specs):
                executions = execute_demo_plans(
                    reports,
                    session=mock_session,
                    account=self.valid_account,
                    db_path=self.db_path,
                )

        self.assertEqual(len(executions), 1)
        exec_item = executions[0]
        # Recovered state is ACCEPTED rather than failed
        self.assertEqual(exec_item["status"], "ACCEPTED")
        self.assertEqual(exec_item["bybit_order_id"], "bybit_live_9999")

        # Intent was updated
        intent = get_order_intent(order_link_id, db_path=self.db_path)
        self.assertEqual(intent["status"], "ACCEPTED")

        # Atomic slot is ACTIVE
        active_slots = get_active_slot_reservations(db_path=self.db_path)
        matching = [s for s in active_slots if s["order_link_id"] == order_link_id]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["status"], "ACTIVE")

    def test_duplicate_trigger_skips_submit(self):
        """A duplicate execution cycle skips submitting an already accepted/filled intent."""
        order_link_id = "tos_24140000_BTCUSDT"
        # Pre-seed an ACCEPTED intent in database
        create_order_intent({
            "order_link_id": order_link_id,
            "cycle_id": "2026-10-24T14:00:00Z",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "qty": "0.001",
            "entry_reference": 50000.0,
            "sl": 49000.0,
            "tp": 52000.0,
            "rr": 2.0,
            "risk_budget_usd": 100.0,
            "status": "ACCEPTED",
        }, db_path=self.db_path)

        reports = {
            "scanner": {"cycle_id": "2026-10-24T14:00:00Z"},
            "trade_plans": {"results": [self.valid_plan]},
        }

        mock_session = MagicMock()
        mock_session.get_open_orders.return_value = {
            "retCode": 0,
            "result": {"list": [{"orderId": "ord_1", "orderLinkId": order_link_id, "orderStatus": "New"}]},
        }

        with patch.dict(os.environ, {"BYBIT_DEMO_EXECUTION_ENABLED": "true"}):
            with patch("demo_order_executor.get_cached_instrument_specs", return_value=self.valid_specs):
                executions = execute_demo_plans(
                    reports,
                    session=mock_session,
                    account=self.valid_account,
                    db_path=self.db_path,
                )

        # place_order MUST NOT have been called
        mock_session.place_order.assert_not_called()
        self.assertEqual(len(executions), 1)
        self.assertIn("Duplicate skipped", executions[0]["error_message"])

    def test_partial_fill_reconciliation(self):
        """Reconciliation accurately persists PARTIALLY_FILLED state and retains active slot."""
        order_link_id = "link_part_1"
        create_order_intent({
            "order_link_id": order_link_id,
            "cycle_id": "CYC1",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "qty": "0.010",
            "entry_reference": 50000.0,
            "sl": 49000.0,
            "tp": 52000.0,
            "rr": 2.0,
            "risk_budget_usd": 100.0,
            "status": "ACCEPTED",
        }, db_path=self.db_path)
        reserve_atomic_slot("BTCUSDT", order_link_id, db_path=self.db_path)

        mock_session = MagicMock()
        mock_session.get_open_orders.return_value = {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "orderId": "bybit_part_001",
                        "orderLinkId": order_link_id,
                        "orderStatus": "PartiallyFilled",
                        "avgPrice": "50015.5",
                        "cumExecQty": "0.005",
                        "cumExecFee": "0.15",
                    }
                ]
            },
        }

        res = reconcile_order_intent(order_link_id, session=mock_session, db_path=self.db_path)
        self.assertTrue(res["reconciled"])
        self.assertEqual(res["status"], "PARTIALLY_FILLED")

        intent = get_order_intent(order_link_id, db_path=self.db_path)
        self.assertEqual(intent["status"], "PARTIALLY_FILLED")
        self.assertEqual(intent["cum_exec_qty"], "0.005")
        self.assertEqual(intent["avg_fill_price"], 50015.5)

        # Slot remains ACTIVE
        active_slots = get_active_slot_reservations(db_path=self.db_path)
        self.assertEqual(len(active_slots), 1)
        self.assertEqual(active_slots[0]["status"], "ACTIVE")

    def test_full_fill_persists_actual_executions(self):
        """Full fill records confirmed execution price, fees, and orderId from exchange."""
        order_link_id = "link_fill_1"
        create_order_intent({
            "order_link_id": order_link_id,
            "cycle_id": "CYC1",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "qty": "0.010",
            "entry_reference": 50000.0,
            "sl": 49000.0,
            "tp": 52000.0,
            "rr": 2.0,
            "risk_budget_usd": 100.0,
            "status": "ACCEPTED",
        }, db_path=self.db_path)
        reserve_atomic_slot("BTCUSDT", order_link_id, db_path=self.db_path)

        mock_session = MagicMock()
        mock_session.get_open_orders.return_value = {"retCode": 0, "result": {"list": []}}
        mock_session.get_order_history.return_value = {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "orderId": "bybit_fill_777",
                        "orderLinkId": order_link_id,
                        "orderStatus": "Filled",
                        "avgPrice": "50025.50",
                        "cumExecQty": "0.010",
                        "cumExecFee": "0.30",
                    }
                ]
            },
        }

        res = reconcile_order_intent(order_link_id, session=mock_session, db_path=self.db_path)
        self.assertTrue(res["reconciled"])
        self.assertEqual(res["status"], "FILLED")
        self.assertEqual(res["order_id"], "bybit_fill_777")
        self.assertEqual(res["avg_fill_price"], 50025.50)

        intent = get_order_intent(order_link_id, db_path=self.db_path)
        self.assertEqual(intent["status"], "FILLED")
        self.assertEqual(intent["exchange_order_id"], "bybit_fill_777")
        self.assertEqual(intent["avg_fill_price"], 50025.50)
        self.assertEqual(intent["cum_exec_fee"], 0.30)

    def test_rejection_reconciles_and_releases_slot(self):
        """Reconciliation releases slot when exchange orderStatus is Rejected."""
        order_link_id = "link_rej_1"
        create_order_intent({
            "order_link_id": order_link_id,
            "cycle_id": "CYC1",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "qty": "0.010",
            "entry_reference": 50000.0,
            "sl": 49000.0,
            "tp": 52000.0,
            "rr": 2.0,
            "risk_budget_usd": 100.0,
            "status": "UNKNOWN",
        }, db_path=self.db_path)
        reserve_atomic_slot("BTCUSDT", order_link_id, db_path=self.db_path)

        mock_session = MagicMock()
        mock_session.get_open_orders.return_value = {"retCode": 0, "result": {"list": []}}
        mock_session.get_order_history.return_value = {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "orderId": "ord_rej_9",
                        "orderLinkId": order_link_id,
                        "orderStatus": "Rejected",
                    }
                ]
            },
        }

        res = reconcile_order_intent(order_link_id, session=mock_session, db_path=self.db_path)
        self.assertTrue(res["reconciled"])
        self.assertEqual(res["status"], "VERIFIED_REJECTED")

        # Slot released
        active_slots = get_active_slot_reservations(db_path=self.db_path)
        self.assertEqual(len(active_slots), 0)

    def test_confirmed_never_submitted_releases_slot(self):
        """Confirmed absence across open orders, history, and executions releases slot as NEVER_SUBMITTED."""
        order_link_id = "link_never_sub"
        create_order_intent({
            "order_link_id": order_link_id,
            "cycle_id": "CYC1",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "qty": "0.010",
            "entry_reference": 50000.0,
            "sl": 49000.0,
            "tp": 52000.0,
            "rr": 2.0,
            "risk_budget_usd": 100.0,
            "status": "UNKNOWN",
        }, db_path=self.db_path)
        reserve_atomic_slot("BTCUSDT", order_link_id, db_path=self.db_path)

        mock_session = MagicMock()
        mock_session.get_open_orders.return_value = {"retCode": 0, "result": {"list": []}}
        mock_session.get_order_history.return_value = {"retCode": 0, "result": {"list": []}}
        mock_session.get_executions.return_value = {"retCode": 0, "result": {"list": []}}

        res = reconcile_order_intent(order_link_id, session=mock_session, db_path=self.db_path)
        self.assertTrue(res["reconciled"])
        self.assertEqual(res["status"], "NEVER_SUBMITTED")

        active_slots = get_active_slot_reservations(db_path=self.db_path)
        self.assertEqual(len(active_slots), 0)

    def test_api_uncertainty_preserves_held_slot(self):
        """API failure during reconciliation keeps status UNKNOWN and retains slot reservation."""
        order_link_id = "link_uncertain_1"
        create_order_intent({
            "order_link_id": order_link_id,
            "cycle_id": "CYC1",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "qty": "0.010",
            "entry_reference": 50000.0,
            "sl": 49000.0,
            "tp": 52000.0,
            "rr": 2.0,
            "risk_budget_usd": 100.0,
            "status": "UNKNOWN",
        }, db_path=self.db_path)
        reserve_atomic_slot("BTCUSDT", order_link_id, db_path=self.db_path)

        mock_session = MagicMock()
        mock_session.get_open_orders.side_effect = ConnectionError("Bybit connection reset")

        res = reconcile_order_intent(order_link_id, session=mock_session, db_path=self.db_path)
        self.assertFalse(res["reconciled"])
        self.assertTrue(res.get("uncertain"))
        self.assertEqual(res["status"], "UNKNOWN")

        # Slot MUST NOT be released
        active_slots = get_active_slot_reservations(db_path=self.db_path)
        self.assertEqual(len(active_slots), 1)

    def test_confirmed_missing_sl_blocks_new_entries(self):
        """authorize_execution_order blocks new orders if any open position has confirmed missing Stop Loss."""
        unprotected_pos = {
            "symbol": "ETHUSDT",
            "side": "Buy",
            "size": "1.5",
            "avgPrice": "3000.0",
            "stopLoss": "0",  # missing SL
            "takeProfit": "3200.0",
        }
        account_with_unprot = dict(
            self.valid_account,
            open_positions=1,
            open_positions_list=[unprotected_pos],
        )

        with patch.dict(os.environ, {"BYBIT_DEMO_EXECUTION_ENABLED": "true"}):
            auth = authorize_execution_order(
                self.valid_plan,
                account_with_unprot,
                specs=self.valid_specs,
                db_path=self.db_path,
            )
            self.assertFalse(auth["authorized"])
            self.assertEqual(auth["status"], "BLOCKED_UNPROTECTED_EXPOSURE")
            self.assertTrue(any("ETHUSDT" in r for r in auth["reasons"]))

    def test_api_uncertainty_in_protection_audit_blocks_new_entries(self):
        """API error while verifying position conditional orders blocks new entries fail-closed."""
        pos = {
            "symbol": "SOLUSDT",
            "side": "Buy",
            "size": "10.0",
            "avgPrice": "150.0",
            "stopLoss": "",  # check conditional orders
        }
        mock_session = MagicMock()
        mock_session.get_open_orders.side_effect = TimeoutError("Bybit API timed out")

        audit = audit_all_positions_protection([pos], session=mock_session)
        self.assertFalse(audit["all_protected"])
        self.assertEqual(audit["uncertain_count"], 1)

    def test_restart_and_reconciliation_of_unknown_intents(self):
        """reconcile_all_pending_intents recovers all orphaned PENDING/UNKNOWN intents on startup."""
        create_order_intent({
            "order_link_id": "link_orphaned_1",
            "cycle_id": "CYC_OLD",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "qty": "0.001",
            "entry_reference": 50000.0,
            "sl": 49000.0,
            "tp": 52000.0,
            "rr": 2.0,
            "risk_budget_usd": 100.0,
            "status": "UNKNOWN",
        }, db_path=self.db_path)
        reserve_atomic_slot("BTCUSDT", "link_orphaned_1", db_path=self.db_path)

        mock_session = MagicMock()
        mock_session.get_open_orders.return_value = {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "orderId": "bybit_restored_555",
                        "orderLinkId": "link_orphaned_1",
                        "orderStatus": "Filled",
                        "avgPrice": "50010.0",
                        "cumExecQty": "0.001",
                        "cumExecFee": "0.03",
                    }
                ]
            },
        }

        results = reconcile_all_pending_intents(session=mock_session, db_path=self.db_path)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "FILLED")

        intent = get_order_intent("link_orphaned_1", db_path=self.db_path)
        self.assertEqual(intent["status"], "FILLED")
        self.assertEqual(intent["exchange_order_id"], "bybit_restored_555")


if __name__ == "__main__":
    unittest.main()
