"""Unit and integration regression test suite for Step 7: Position Lifecycle Monitoring and Slot Reconciliation.

Strictly DRY_RUN_ONLY: Zero live exchange connections, zero real orders.
All Pybit and exchange interactions use mock HTTP sessions.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from db_adapter import (
    init_db,
    create_order_intent,
    get_order_intent,
    reserve_atomic_slot,
    get_active_slot_reservations,
    update_daily_risk_pnl,
)
from position_monitor import (
    poll_and_reconcile_positions,
    run_position_monitor_iteration,
)
from pipeline_lock import (
    exclusive_position_monitor_lock,
    is_position_monitor_locked,
)
from main import app


class TestPositionMonitoringAndLifecycle(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_pos_monitor.db"
        init_db(db_path=self.db_path)

        # Pre-seed verified daily risk record
        update_daily_risk_pnl(
            trading_date="2026-10-24",
            starting_equity=10000.0,
            current_equity=10000.0,
            drawdown_limit_pct=3.0,
            db_path=self.db_path,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_full_lifecycle_open_to_closed(self):
        """Verify full lifecycle: RESERVED -> ACTIVE on fill -> VERIFIED_CLOSED on position exit."""
        order_link_id = "link_cycle_101"
        create_order_intent({
            "order_link_id": order_link_id,
            "cycle_id": "CYC1",
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
        reserve_atomic_slot("BTCUSDT", order_link_id, db_path=self.db_path)

        mock_session = MagicMock()

        # Stage 1: Order still pending in open orders (not yet filled)
        mock_session.get_positions.return_value = {"retCode": 0, "result": {"list": []}}
        mock_session.get_open_orders.return_value = {
            "retCode": 0,
            "result": {"list": [{"symbol": "BTCUSDT", "orderLinkId": order_link_id, "orderStatus": "New"}]},
        }

        r1 = poll_and_reconcile_positions(session=mock_session, db_path=self.db_path)
        self.assertEqual(r1["status"], "SUCCESS")
        slots = get_active_slot_reservations(db_path=self.db_path)
        self.assertEqual(len(slots), 1)

        # Stage 2: Order fills -> active position exists with SL
        mock_session.get_positions.return_value = {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "Buy",
                        "size": "0.001",
                        "avgPrice": "50000.0",
                        "stopLoss": "49000.0",
                        "takeProfit": "52000.0",
                    }
                ]
            },
        }
        mock_session.get_open_orders.return_value = {"retCode": 0, "result": {"list": []}}

        r2 = poll_and_reconcile_positions(session=mock_session, db_path=self.db_path)
        self.assertEqual(r2["status"], "SUCCESS")
        self.assertEqual(r2["open_positions_count"], 1)
        slots = get_active_slot_reservations(db_path=self.db_path)
        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0]["status"], "ACTIVE")

        # Stage 3: Position closes on exchange (e.g. SL or TP hit)
        mock_session.get_positions.return_value = {"retCode": 0, "result": {"list": []}}
        mock_session.get_open_orders.return_value = {"retCode": 0, "result": {"list": []}}
        mock_session.get_order_history.return_value = {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "orderId": "ord_101",
                        "orderLinkId": order_link_id,
                        "orderStatus": "Filled",
                        "avgPrice": "50000.0",
                        "cumExecQty": "0.001",
                        "cumExecFee": "0.03",
                    }
                ]
            },
        }

        r3 = poll_and_reconcile_positions(session=mock_session, db_path=self.db_path)
        self.assertEqual(r3["status"], "SUCCESS")
        # Slot must now be released as VERIFIED_CLOSED
        slots = get_active_slot_reservations(db_path=self.db_path)
        self.assertEqual(len(slots), 0)

        intent = get_order_intent(order_link_id, db_path=self.db_path)
        self.assertEqual(intent["status"], "VERIFIED_CLOSED")

    def test_manual_intervention_closure(self):
        """When an open position is manually closed by the user in Bybit terminal, slot is released."""
        order_link_id = "link_manual_close"
        create_order_intent({
            "order_link_id": order_link_id,
            "cycle_id": "CYC1",
            "symbol": "ETHUSDT",
            "direction": "LONG",
            "qty": "0.1",
            "entry_reference": 3000.0,
            "sl": 2900.0,
            "tp": 3200.0,
            "rr": 2.0,
            "risk_budget_usd": 100.0,
            "status": "FILLED",
        }, db_path=self.db_path)
        reserve_atomic_slot("ETHUSDT", order_link_id, db_path=self.db_path)

        mock_session = MagicMock()
        # Position is no longer in open positions
        mock_session.get_positions.return_value = {"retCode": 0, "result": {"list": []}}
        mock_session.get_open_orders.return_value = {"retCode": 0, "result": {"list": []}}
        mock_session.get_order_history.return_value = {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "orderId": "ord_eth_man",
                        "orderLinkId": order_link_id,
                        "orderStatus": "Filled",
                        "avgPrice": "3000.0",
                        "cumExecQty": "0.1",
                    }
                ]
            },
        }

        res = poll_and_reconcile_positions(session=mock_session, db_path=self.db_path)
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(len(res["released_slots"]), 1)
        self.assertEqual(res["released_slots"][0]["reason"], "VERIFIED_CLOSED")

        active_slots = get_active_slot_reservations(db_path=self.db_path)
        self.assertEqual(len(active_slots), 0)

    def test_manual_order_cancellation(self):
        """When an order is cancelled on Bybit before filling, slot is released as VERIFIED_CANCELLED."""
        order_link_id = "link_cancel_1"
        create_order_intent({
            "order_link_id": order_link_id,
            "cycle_id": "CYC1",
            "symbol": "SOLUSDT",
            "direction": "LONG",
            "qty": "1.0",
            "entry_reference": 150.0,
            "sl": 140.0,
            "tp": 170.0,
            "rr": 2.0,
            "risk_budget_usd": 100.0,
            "status": "ACCEPTED",
        }, db_path=self.db_path)
        reserve_atomic_slot("SOLUSDT", order_link_id, db_path=self.db_path)

        mock_session = MagicMock()
        mock_session.get_positions.return_value = {"retCode": 0, "result": {"list": []}}
        mock_session.get_open_orders.return_value = {"retCode": 0, "result": {"list": []}}
        mock_session.get_order_history.return_value = {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "orderId": "ord_sol_cancel",
                        "orderLinkId": order_link_id,
                        "orderStatus": "Cancelled",
                        "cumExecQty": "0",
                    }
                ]
            },
        }

        res = poll_and_reconcile_positions(session=mock_session, db_path=self.db_path)
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(len(res["released_slots"]), 1)
        self.assertEqual(res["released_slots"][0]["reason"], "VERIFIED_CANCELLED")

        active_slots = get_active_slot_reservations(db_path=self.db_path)
        self.assertEqual(len(active_slots), 0)

    def test_exchange_data_unavailable_fail_closed(self):
        """When Bybit API is down, monitor retains conservative slots and does not release capacity."""
        order_link_id = "link_api_down"
        reserve_atomic_slot("BTCUSDT", order_link_id, db_path=self.db_path)

        mock_session = MagicMock()
        mock_session.get_positions.side_effect = ConnectionError("Bybit Gateway 504")

        res = poll_and_reconcile_positions(session=mock_session, db_path=self.db_path)
        self.assertEqual(res["status"], "EXCHANGE_UNAVAILABLE")
        self.assertTrue(res["conservative_slots"])

        # Slots must NOT have been released
        active_slots = get_active_slot_reservations(db_path=self.db_path)
        self.assertEqual(len(active_slots), 1)

    def test_duplicate_polling_lock_contention(self):
        """Concurrent position monitor calls encounter lock contention and return BUSY."""
        with exclusive_position_monitor_lock(db_path=self.db_path):
            # Inner call attempts to acquire the lock concurrently
            mock_session = MagicMock()
            res = poll_and_reconcile_positions(session=mock_session, db_path=self.db_path)
            self.assertEqual(res["status"], "BUSY")
            self.assertTrue(res["conservative_slots"])

    def test_missing_sl_triggers_emergency_alert(self):
        """Unprotected open position flags emergency alert and fails protection audit."""
        mock_session = MagicMock()
        mock_session.get_positions.return_value = {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "Buy",
                        "size": "0.005",
                        "avgPrice": "50000.0",
                        "stopLoss": "0",  # Unprotected!
                    }
                ]
            },
        }
        mock_session.get_open_orders.return_value = {"retCode": 0, "result": {"list": []}}

        with patch("position_monitor.send_telegram_message") as mock_tg:
            res = poll_and_reconcile_positions(session=mock_session, db_path=self.db_path, alert_unprotected=True)

        self.assertEqual(res["status"], "SUCCESS")
        prot_audit = res["protection_audit"]
        self.assertFalse(prot_audit["all_protected"])
        self.assertEqual(prot_audit["unprotected_count"], 1)
        mock_tg.assert_called_once()
        self.assertIn("EMERGENCY: UNPROTECTED OPEN POSITION DETECTED", mock_tg.call_args[0][0])

    def test_process_restart_reconciliation(self):
        """After process restart with orphaned slots, position monitor safely synchronizes DB."""
        # Orphaned active slot in DB from prior process
        reserve_atomic_slot("ETHUSDT", "link_orphaned_eth", db_path=self.db_path)

        mock_session = MagicMock()
        # Exchange confirms ETHUSDT is active and healthy
        mock_session.get_positions.return_value = {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "symbol": "ETHUSDT",
                        "side": "Buy",
                        "size": "0.5",
                        "avgPrice": "3000.0",
                        "stopLoss": "2950.0",
                    }
                ]
            },
        }
        mock_session.get_open_orders.return_value = {"retCode": 0, "result": {"list": []}}

        res = poll_and_reconcile_positions(session=mock_session, db_path=self.db_path)
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["active_slots_remaining"], 1)

    def test_reconcile_positions_api_endpoint(self):
        """FastAPI endpoint /api/execution/reconcile-positions executes successfully."""
        client = TestClient(app)
        with patch("position_monitor.get_monitor_bybit_session") as mock_get_sess:
            mock_session = MagicMock()
            mock_session.get_positions.return_value = {"retCode": 0, "result": {"list": []}}
            mock_session.get_open_orders.return_value = {"retCode": 0, "result": {"list": []}}
            mock_get_sess.return_value = mock_session

            response = client.post("/api/execution/reconcile-positions")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["status"], "SUCCESS")


if __name__ == "__main__":
    unittest.main()
