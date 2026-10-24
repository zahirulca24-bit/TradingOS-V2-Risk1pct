"""Unit and integration regression test suite for Step 5: Shared Risk Authorization, Execution Gate, and Daily Controls.

Strictly DRY_RUN_ONLY: Zero live exchange connections, zero real orders.
All Pybit and exchange interactions use mock HTTP sessions.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from demo_risk_engine import (
    authorize_execution_order,
    is_demo_execution_enabled,
    quantize_value,
    format_step_string,
)
from demo_order_executor import (
    execute_demo_plans,
    place_single_demo_order,
)
from db_adapter import (
    init_db,
    set_kill_switch,
    update_daily_risk_pnl,
    get_order_intent,
    get_active_slot_reservations,
    reserve_atomic_slot,
)


class TestSharedRiskAuthorization(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_risk_auth.db"
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

        # Baseline valid trade plan (RR = 2.0, Long)
        # Entry: 50000, SL: 49000 (risk=1000), TP: 52000 (reward=2000) -> RR = 2.0
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

    def test_kill_switch_denial(self):
        """Authoritative gate must strictly block execution when kill switch is active."""
        set_kill_switch(enabled=True, reason="Emergency safety halt", db_path=self.db_path)

        with patch.dict(os.environ, {"BYBIT_DEMO_EXECUTION_ENABLED": "true"}):
            auth = authorize_execution_order(
                self.valid_plan,
                self.valid_account,
                specs=self.valid_specs,
                db_path=self.db_path,
            )
            self.assertFalse(auth["authorized"])
            self.assertEqual(auth["status"], "BLOCKED_KILL_SWITCH")
            self.assertTrue(any("Emergency safety halt" in r for r in auth["reasons"]))

    def test_execution_disabled_denial(self):
        """Authoritative gate must block execution when BYBIT_DEMO_EXECUTION_ENABLED is false."""
        with patch.dict(os.environ, {"BYBIT_DEMO_EXECUTION_ENABLED": "false"}):
            auth = authorize_execution_order(
                self.valid_plan,
                self.valid_account,
                specs=self.valid_specs,
                db_path=self.db_path,
            )
            self.assertFalse(auth["authorized"])
            self.assertEqual(auth["status"], "BLOCKED_EXECUTION_DISABLED")

    def test_missing_or_invalid_account_equity(self):
        """Authoritative gate must fail closed if equity is None, zero, or unverified."""
        with patch.dict(os.environ, {"BYBIT_DEMO_EXECUTION_ENABLED": "true"}):
            # Zero equity
            acc_zero = dict(self.valid_account, total_equity=0.0)
            auth_zero = authorize_execution_order(
                self.valid_plan, acc_zero, specs=self.valid_specs, db_path=self.db_path
            )
            self.assertFalse(auth_zero["authorized"])
            self.assertEqual(auth_zero["status"], "BLOCKED_DATA")

            # Degraded positions error
            acc_degraded = dict(self.valid_account, positions_error=True)
            auth_degraded = authorize_execution_order(
                self.valid_plan, acc_degraded, specs=self.valid_specs, db_path=self.db_path
            )
            self.assertFalse(auth_degraded["authorized"])
            self.assertEqual(auth_degraded["status"], "BLOCKED_DATA")

    def test_daily_risk_guard_fail_closed(self):
        """Authoritative gate must fail closed if daily risk circuit breaker triggers or equity is stale."""
        # Trigger 3% circuit breaker
        update_daily_risk_pnl(
            trading_date="2026-10-24",
            realized_pnl_delta=-350.0,
            starting_equity=10000.0,
            current_equity=9650.0,
            db_path=self.db_path,
        )

        with patch.dict(os.environ, {"BYBIT_DEMO_EXECUTION_ENABLED": "true"}):
            auth = authorize_execution_order(
                self.valid_plan,
                self.valid_account,
                specs=self.valid_specs,
                db_path=self.db_path,
            )
            self.assertFalse(auth["authorized"])
            self.assertEqual(auth["status"], "BLOCKED_DAILY_RISK")
            self.assertTrue(any("BLOCKED_CIRCUIT_BREAKER" in r for r in auth["reasons"]))

    def test_combined_3_slot_capacity_enforcement(self):
        """Authoritative gate blocks when exchange positions + pending + DB reservations >= 3."""
        with patch.dict(os.environ, {"BYBIT_DEMO_EXECUTION_ENABLED": "true"}):
            # 2 open positions on exchange + 1 DB reservation = 3 slots occupied
            acc_2_pos = dict(
                self.valid_account,
                open_positions=2,
                open_positions_list=[{"symbol": "ETHUSDT"}, {"symbol": "SOLUSDT"}],
            )
            reserve_atomic_slot("DOGEUSDT", "link_doge_1", db_path=self.db_path)

            auth = authorize_execution_order(
                self.valid_plan,
                acc_2_pos,
                specs=self.valid_specs,
                db_path=self.db_path,
            )
            self.assertFalse(auth["authorized"])
            self.assertEqual(auth["status"], "BLOCKED_CAPACITY")
            self.assertTrue(any("capacity reached: 3/3" in r for r in auth["reasons"]))

    def test_duplicate_symbol_reservation_denial(self):
        """Authoritative gate rejects a plan if the symbol already has an active DB reservation."""
        with patch.dict(os.environ, {"BYBIT_DEMO_EXECUTION_ENABLED": "true"}):
            reserve_atomic_slot("BTCUSDT", "link_btc_prior", db_path=self.db_path)

            auth = authorize_execution_order(
                self.valid_plan,
                self.valid_account,
                specs=self.valid_specs,
                db_path=self.db_path,
            )
            self.assertFalse(auth["authorized"])
            self.assertEqual(auth["status"], "BLOCKED_DUPLICATE")

    def test_canonical_min_rr_enforcement(self):
        """Authoritative gate rejects plans with RR < 2.0."""
        with patch.dict(os.environ, {"BYBIT_DEMO_EXECUTION_ENABLED": "true"}):
            plan_low_rr = dict(self.valid_plan, rr=1.85, tp=51850.0)
            auth = authorize_execution_order(
                plan_low_rr,
                self.valid_account,
                specs=self.valid_specs,
                db_path=self.db_path,
            )
            self.assertFalse(auth["authorized"])
            self.assertEqual(auth["status"], "BLOCKED_RISK")
            self.assertTrue(any("RISK_REWARD_TOO_LOW" in str(r) or "2.0" in str(r) for r in auth["reasons"]))

    def test_authorized_plan_risk_sizing_and_quantization(self):
        """Valid plan is authorized with 1% equity budget and properly quantized SL/TP."""
        with patch.dict(os.environ, {"BYBIT_DEMO_EXECUTION_ENABLED": "true"}):
            auth = authorize_execution_order(
                self.valid_plan,
                self.valid_account,
                specs=self.valid_specs,
                db_path=self.db_path,
            )
            self.assertTrue(auth["authorized"])
            self.assertEqual(auth["status"], "AUTHORIZED")
            self.assertIsNotNone(auth["quantity"])
            self.assertEqual(auth["quantized_sl"], "49000.00")
            self.assertEqual(auth["quantized_tp"], "52000.00")
            # Risk budget for 10,000 equity at 1.0% is 100.00 USD
            self.assertEqual(float(auth["risk_budget_usd"]), 100.0)
            self.assertLessEqual(float(auth["estimated_risk_usd"]), 100.0)

    def test_execute_demo_plans_acceptance_lifecycle(self):
        """execute_demo_plans reserves slot and creates PENDING intent BEFORE mock Bybit accept."""
        reports = {
            "scanner": {"cycle_id": "2026-10-24T12:00:00Z"},
            "trade_plans": {"results": [self.valid_plan]},
        }

        mock_session = MagicMock()
        mock_session.place_order.return_value = {
            "retCode": 0,
            "retMsg": "OK",
            "result": {"orderId": "bybit_ord_12345"},
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
        self.assertEqual(exec_item["status"], "ACCEPTED")
        self.assertEqual(exec_item["bybit_order_id"], "bybit_ord_12345")

        order_link_id = exec_item["order_link_id"]
        # Verify persistent order intent was transitioned to ACCEPTED
        intent = get_order_intent(order_link_id, db_path=self.db_path)
        self.assertIsNotNone(intent)
        self.assertEqual(intent["status"], "ACCEPTED")

        # Verify atomic slot is ACTIVE
        active_slots = get_active_slot_reservations(db_path=self.db_path)
        matching_slot = [s for s in active_slots if s["order_link_id"] == order_link_id]
        self.assertEqual(len(matching_slot), 1)
        self.assertEqual(matching_slot[0]["status"], "ACTIVE")

    def test_execute_demo_plans_rejection_lifecycle(self):
        """execute_demo_plans reconciles and releases slot when Bybit returns non-zero retCode."""
        reports = {
            "scanner": {"cycle_id": "2026-10-24T12:00:00Z"},
            "trade_plans": {"results": [self.valid_plan]},
        }

        mock_session = MagicMock()
        mock_session.place_order.return_value = {
            "retCode": 10001,
            "retMsg": "params error: qty invalid",
            "result": {},
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
        self.assertEqual(exec_item["status"], "REJECTED")

        order_link_id = exec_item["order_link_id"]
        # Verify persistent order intent is VERIFIED_REJECTED
        intent = get_order_intent(order_link_id, db_path=self.db_path)
        self.assertIsNotNone(intent)
        self.assertEqual(intent["status"], "VERIFIED_REJECTED")

        # Verify atomic slot is RELEASED
        active_slots = get_active_slot_reservations(db_path=self.db_path)
        matching_slot = [s for s in active_slots if s["order_link_id"] == order_link_id]
        self.assertEqual(len(matching_slot), 0)

    def test_execute_demo_plans_network_exception_holds_slot(self):
        """execute_demo_plans must NOT release slot when exchange call raises an exception."""
        reports = {
            "scanner": {"cycle_id": "2026-10-24T12:00:00Z"},
            "trade_plans": {"results": [self.valid_plan]},
        }

        mock_session = MagicMock()
        mock_session.place_order.side_effect = ConnectionResetError("Connection dropped by remote peer")

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
        self.assertEqual(exec_item["status"], "UNKNOWN")

        order_link_id = exec_item["order_link_id"]
        # Verify order intent is UNKNOWN
        intent = get_order_intent(order_link_id, db_path=self.db_path)
        self.assertIsNotNone(intent)
        self.assertEqual(intent["status"], "UNKNOWN")

        # Verify atomic slot is STILL HELD / RESERVED (never released on unknown)
        active_slots = get_active_slot_reservations(db_path=self.db_path)
        matching_slot = [s for s in active_slots if s["order_link_id"] == order_link_id]
        self.assertEqual(len(matching_slot), 1)
        self.assertIn(matching_slot[0]["status"], ("RESERVED", "HELD_PENDING_RECONCILIATION"))

    def test_place_single_demo_order_denial_on_kill_switch(self):
        """place_single_demo_order refuses order when kill switch is active."""
        set_kill_switch(enabled=True, reason="Operator halt", db_path=self.db_path)

        with patch.dict(os.environ, {"BYBIT_DEMO_EXECUTION_ENABLED": "true"}):
            with patch("demo_order_executor.get_cached_instrument_specs", return_value=self.valid_specs):
                with self.assertRaises(RuntimeError) as ctx:
                    place_single_demo_order(
                        symbol="BTCUSDT",
                        direction="LONG",
                        entry_reference=50000.0,
                        sl=49000.0,
                        tp=52000.0,
                        rr=2.0,
                        account=self.valid_account,
                        db_path=self.db_path,
                    )
                self.assertIn("BLOCKED_KILL_SWITCH", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
