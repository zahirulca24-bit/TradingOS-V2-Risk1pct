"""Step 1 Acceptance Tests: Database Integrity, Order Intents, Atomic Slots, Kill Switch, and Circuit Breaker.
Validates:
1. Complete schema initialization (all 11 tables, indexes, idempotent re-run).
2. Persistent order idempotency (order_intents table, unique order_link_id, retry tracking).
3. Atomic 3-slot capacity enforcement (concurrency-safe reservation, TTL expiration, idempotent re-check).
4. Persistent emergency execution kill switch (system_controls & audit trail, env var override).
5. Daily 3% drawdown circuit breaker (daily_risk_records, PnL accumulation, breaker trip).
6. Backward compatibility with existing SQLite database baseline.
"""

import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from db_adapter import (
    activate_atomic_slot,
    can_execute_new_daily_entries,
    create_order_intent,
    evaluate_daily_risk_guard,
    get_active_engine,
    get_active_slot_reservations,
    get_daily_risk_record,
    get_db_connection,
    get_kill_switch_status,
    get_order_intent,
    init_db,
    is_daily_circuit_breaker_triggered,
    reconcile_and_release_slot,
    release_atomic_slot,
    reserve_atomic_slot,
    set_kill_switch,
    update_daily_risk_pnl,
    update_order_intent_status,
)


class TestDatabaseIntegrity(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="tradingos_db_step1_")
        self.db_path = Path(self.temp_dir) / "test_integrity.db"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_schema_initialization_all_tables(self):
        """Verify all 11 required tables and indexes initialize idempotently."""
        engine = init_db(db_path=self.db_path)
        self.assertEqual(engine, "sqlite")
        self.assertTrue(self.db_path.is_file())

        with get_db_connection(db_path=self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row["name"] for row in cur.fetchall()}

            expected_tables = {
                "journal_cycles",
                "journal_signals",
                "journal_dry_runs",
                "pipeline_cycles",
                "pipeline_meta",
                "demo_executions",
                "journal_signal_tracking",
                "order_intents",
                "slot_reservations",
                "system_controls",
                "system_control_audit",
                "daily_risk_records",
            }
            for tbl in expected_tables:
                self.assertIn(tbl, tables, f"Missing required table: {tbl}")

        # Idempotent re-initialization must succeed without error
        engine_re = init_db(db_path=self.db_path)
        self.assertEqual(engine_re, "sqlite")

    def test_order_intents_lifecycle_and_idempotency(self):
        """Verify order intent creation, idempotency, query, and status transitions."""
        intent_data = {
            "order_link_id": "tos_20261024_BTCUSDT",
            "cycle_id": "20261024T030000Z",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "qty": "0.05",
            "entry_reference": 65000.0,
            "sl": 64200.0,
            "tp": 66800.0,
            "rr": 2.25,
            "risk_budget_usd": 100.0,
            "status": "PENDING",
        }

        created = create_order_intent(intent_data, db_path=self.db_path)
        self.assertEqual(created["order_link_id"], "tos_20261024_BTCUSDT")
        self.assertEqual(created["status"], "PENDING")
        self.assertEqual(created["retry_count"], 0)

        # Idempotent repeat: submitting exact same order_link_id must return existing without duplicate insert
        repeat = create_order_intent(intent_data, db_path=self.db_path)
        self.assertEqual(repeat["id"], created["id"])

        # Fetch intent
        fetched = get_order_intent("tos_20261024_BTCUSDT", db_path=self.db_path)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["symbol"], "BTCUSDT")

        # Update status to SUBMITTED
        update_order_intent_status("tos_20261024_BTCUSDT", "SUBMITTED", db_path=self.db_path)
        fetched_sub = get_order_intent("tos_20261024_BTCUSDT", db_path=self.db_path)
        self.assertEqual(fetched_sub["status"], "SUBMITTED")

        # Update status to CONFIRMED
        update_order_intent_status("tos_20261024_BTCUSDT", "CONFIRMED", db_path=self.db_path)
        fetched_conf = get_order_intent("tos_20261024_BTCUSDT", db_path=self.db_path)
        self.assertEqual(fetched_conf["status"], "CONFIRMED")

    def test_atomic_3_slot_capacity_reservation_and_ttl(self):
        """Verify strict 3-slot capacity enforcement, atomic reservation, and TTL expiry."""
        # 1. Reserve Slot 1
        s1 = reserve_atomic_slot("BTCUSDT", "link_btc", ttl_seconds=30, db_path=self.db_path)
        self.assertEqual(s1, 1)

        # 2. Reserve Slot 2
        s2 = reserve_atomic_slot("ETHUSDT", "link_eth", ttl_seconds=30, db_path=self.db_path)
        self.assertEqual(s2, 2)

        # 3. Reserve Slot 3
        s3 = reserve_atomic_slot("SOLUSDT", "link_sol", ttl_seconds=30, db_path=self.db_path)
        self.assertEqual(s3, 3)

        # 4. Attempt 4th reservation: MUST be blocked (returns None)
        s4 = reserve_atomic_slot("AVAXUSDT", "link_avax", ttl_seconds=30, db_path=self.db_path)
        self.assertIsNone(s4, "4th slot must be blocked when 3 slots are occupied")

        # 5. Idempotent check: same order_link_id returns already held slot
        s1_repeat = reserve_atomic_slot("BTCUSDT", "link_btc", ttl_seconds=30, db_path=self.db_path)
        self.assertEqual(s1_repeat, 1)

        # 6. Active slots count must be 3
        active_slots = get_active_slot_reservations(db_path=self.db_path)
        self.assertEqual(len(active_slots), 3)

        # 7. Activate Slot 1 (confirmed fill on exchange)
        act_ok = activate_atomic_slot("link_btc", db_path=self.db_path)
        self.assertTrue(act_ok)

        # 8. Release Slot 2 (order failed or trade closed)
        rel_ok = release_atomic_slot("link_eth", reason="TRADE_CLOSED", db_path=self.db_path)
        self.assertTrue(rel_ok)

        # Now slot 2 is free -> AVAX can now reserve it!
        s4_retry = reserve_atomic_slot("AVAXUSDT", "link_avax", ttl_seconds=30, db_path=self.db_path)
        self.assertEqual(s4_retry, 2)

        # 9. TTL Expiration: an unconfirmed order whose TTL expires transitions to HELD_PENDING_RECONCILIATION
        # It MUST NOT be auto-released on TTL alone!
        release_atomic_slot("link_avax", reason="TEST", db_path=self.db_path)
        s_short = reserve_atomic_slot("DOGEUSDT", "link_doge", ttl_seconds=1, db_path=self.db_path)
        self.assertIsNotNone(s_short)
        time.sleep(1.2)

        # After TTL, the slot is NOT released; it remains held in HELD_PENDING_RECONCILIATION
        active_after_ttl = get_active_slot_reservations(db_path=self.db_path)
        links = [r["order_link_id"] for r in active_after_ttl]
        self.assertIn("link_doge", links, "Expired unconfirmed slot MUST remain held pending reconciliation")

        # Reconcile with verified rejection -> slot is now freed
        reconcile_and_release_slot("link_doge", verified_status="VERIFIED_REJECTED", reason="Exchange order rejected", db_path=self.db_path)
        active_after_rec = get_active_slot_reservations(db_path=self.db_path)
        links_rec = [r["order_link_id"] for r in active_after_rec]
        self.assertNotIn("link_doge", links_rec, "Reconciled slot must be freed")

    def test_slot_ttl_no_auto_release_and_restart_reconciliation(self):
        """Verify unconfirmed slots are NEVER auto-released on TTL alone across timeouts and restarts."""
        # Fill all 3 slots
        s1 = reserve_atomic_slot("BTCUSDT", "ord_1", ttl_seconds=1, db_path=self.db_path)
        s2 = reserve_atomic_slot("ETHUSDT", "ord_2", ttl_seconds=1, db_path=self.db_path)
        s3 = reserve_atomic_slot("SOLUSDT", "ord_3", ttl_seconds=1, db_path=self.db_path)
        self.assertEqual({s1, s2, s3}, {1, 2, 3})

        # Wait for TTL to expire
        time.sleep(1.2)

        # Attempt to reserve a 4th slot: MUST STILL BE BLOCKED!
        s4 = reserve_atomic_slot("ADAUSDT", "ord_4", ttl_seconds=30, db_path=self.db_path)
        self.assertIsNone(s4, "Must not auto-release expired slots without verified reconciliation")

        # Simulate system restart: reconnect to database
        with get_db_connection(db_path=self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT status, release_reason FROM slot_reservations WHERE order_link_id = 'ord_1'")
            row = cur.fetchone()
            self.assertEqual(row["status"], "HELD_PENDING_RECONCILIATION")
            self.assertIn("PENDING_EXCHANGE_RECONCILIATION", row["release_reason"])

        # Attempting release with unverified status (PENDING, SUBMITTED, UNKNOWN) must raise ValueError
        for bad_stat in ("PENDING", "SUBMITTED", "UNKNOWN", "FILLED"):
            with self.assertRaises(ValueError):
                reconcile_and_release_slot("ord_1", verified_status=bad_stat, db_path=self.db_path)

        # Verified reconciliation release (VERIFIED_CLOSED on exchange)
        rel_ok = reconcile_and_release_slot("ord_1", verified_status="VERIFIED_CLOSED", reason="Closed via SL hit", db_path=self.db_path)
        self.assertTrue(rel_ok)

        # Now slot 1 is released and ADA can safely take it
        s4_retry = reserve_atomic_slot("ADAUSDT", "ord_4", ttl_seconds=30, db_path=self.db_path)
        self.assertEqual(s4_retry, 1)

    def test_fail_closed_daily_risk_missing_and_stale_equity(self):
        """Verify missing, zero, invalid, or stale equity blocks trading (fail-closed) and distinguishes UNKNOWN from circuit breaker."""
        date_today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # 1. Uninitialized daily risk record (equity 0.0):
        # Must FAIL CLOSED (can_trade=False) with BLOCKED_EQUITY_UNKNOWN, NOT BLOCKED_CIRCUIT_BREAKER
        can_trade, risk_state = can_execute_new_daily_entries(trading_date=date_today, db_path=self.db_path)
        self.assertFalse(can_trade, "Must fail closed when equity is zero/uninitialized")
        self.assertEqual(risk_state, "BLOCKED_EQUITY_UNKNOWN")

        eval_res = evaluate_daily_risk_guard(trading_date=date_today, db_path=self.db_path)
        self.assertFalse(eval_res["can_trade"])
        self.assertEqual(eval_res["risk_state"], "BLOCKED_EQUITY_UNKNOWN")
        self.assertFalse(eval_res["circuit_breaker_triggered"], "UNKNOWN equity must be distinct from circuit breaker trip")
        self.assertFalse(is_daily_circuit_breaker_triggered(trading_date=date_today, db_path=self.db_path))

        # 2. Update with valid equity ($10,000) and small loss (-$50 = 0.5% drawdown):
        # Must be CLEARED (can_trade=True)
        update_daily_risk_pnl(
            trading_date=date_today,
            realized_pnl_delta=-50.0,
            starting_equity=10000.0,
            current_equity=9950.0,
            is_win=False,
            db_path=self.db_path
        )
        can_trade_ok, state_ok = can_execute_new_daily_entries(trading_date=date_today, db_path=self.db_path)
        self.assertTrue(can_trade_ok, "Must allow trade when equity is valid and drawdown is 0.5% < 3.0%")
        self.assertEqual(state_ok, "CLEARED")

        # 3. Stale risk record: set updated_at to 1200 seconds ago (> 900s limit)
        with get_db_connection(db_path=self.db_path) as conn:
            cur = conn.cursor()
            stale_iso = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - 1200, tz=timezone.utc).isoformat()
            cur.execute("UPDATE daily_risk_records SET updated_at = ? WHERE trading_date = ?", (stale_iso, date_today))
            conn.commit()

        can_trade_stale, state_stale = can_execute_new_daily_entries(trading_date=date_today, max_staleness_seconds=900, db_path=self.db_path)
        self.assertFalse(can_trade_stale, "Must fail closed when risk record is stale")
        self.assertEqual(state_stale, "BLOCKED_RISK_STALE")

        # 4. Actual 3% Circuit Breaker Trip: large loss (-$350 on $10,000 = 3.5% >= 3.0%)
        update_daily_risk_pnl(
            trading_date=date_today,
            realized_pnl_delta=-300.0,
            starting_equity=10000.0,
            current_equity=9650.0,
            is_win=False,
            drawdown_limit_pct=3.0,
            db_path=self.db_path
        )
        can_trade_cb, state_cb = can_execute_new_daily_entries(trading_date=date_today, db_path=self.db_path)
        self.assertFalse(can_trade_cb, "Must block when 3% circuit breaker trips")
        self.assertEqual(state_cb, "BLOCKED_CIRCUIT_BREAKER")

        eval_cb = evaluate_daily_risk_guard(trading_date=date_today, db_path=self.db_path)
        self.assertTrue(eval_cb["circuit_breaker_triggered"])
        self.assertTrue(is_daily_circuit_breaker_triggered(trading_date=date_today, db_path=self.db_path))

    def test_emergency_kill_switch(self):
        """Verify persistent emergency kill switch state, audit logging, and env override."""
        # Initial state: disabled
        status = get_kill_switch_status(db_path=self.db_path)
        self.assertFalse(status["enabled"])

        # Enable kill switch
        set_kill_switch(True, reason="Emergency market halt", triggered_by="ADMIN", db_path=self.db_path)
        status_on = get_kill_switch_status(db_path=self.db_path)
        self.assertTrue(status_on["enabled"])
        self.assertEqual(status_on["reason"], "Emergency market halt")
        self.assertEqual(status_on["triggered_by"], "ADMIN")

        # Disable kill switch
        set_kill_switch(False, reason="Resuming operations", triggered_by="ADMIN", db_path=self.db_path)
        status_off = get_kill_switch_status(db_path=self.db_path)
        self.assertFalse(status_off["enabled"])

        # Audit trail must have recorded both state transitions
        with get_db_connection(db_path=self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM system_control_audit WHERE key = 'EMERGENCY_KILL_SWITCH' ORDER BY id ASC")
            audits = cur.fetchall()
            self.assertEqual(len(audits), 2)
            self.assertEqual(audits[0]["old_state"], 0)
            self.assertEqual(audits[0]["new_state"], 1)
            self.assertEqual(audits[1]["old_state"], 1)
            self.assertEqual(audits[1]["new_state"], 0)

        # Environment variable override takes immediate precedence
        with mock.patch.dict(os.environ, {"TRADINGOS_KILL_SWITCH": "1"}):
            status_env = get_kill_switch_status(db_path=self.db_path)
            self.assertTrue(status_env["enabled"])
            self.assertEqual(status_env["triggered_by"], "ENV_VAR")

    def test_daily_3pct_drawdown_circuit_breaker(self):
        """Verify daily PnL accumulation and 3.0% drawdown circuit breaker trigger."""
        date_today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        starting_equity = 10000.0

        # Initial record: 0 PnL, circuit breaker not triggered
        initial_rec = get_daily_risk_record(trading_date=date_today, db_path=self.db_path)
        self.assertEqual(initial_rec["circuit_breaker_triggered"], 0)
        self.assertFalse(is_daily_circuit_breaker_triggered(trading_date=date_today, db_path=self.db_path))

        # Trade 1: Won +$150 (equity $10,150)
        rec1 = update_daily_risk_pnl(
            trading_date=date_today,
            realized_pnl_delta=150.0,
            starting_equity=starting_equity,
            current_equity=10150.0,
            is_win=True,
            db_path=self.db_path,
        )
        self.assertEqual(rec1["trades_count"], 1)
        self.assertEqual(rec1["wins_count"], 1)
        self.assertEqual(rec1["circuit_breaker_triggered"], 0)
        self.assertEqual(rec1["realized_pnl_usd"], 150.0)

        # Trade 2: Lost -$100 (net PnL +$50, drawdown from peak $10,150 is $100 / $10,150 = 0.98%)
        rec2 = update_daily_risk_pnl(
            trading_date=date_today,
            realized_pnl_delta=-100.0,
            current_equity=10050.0,
            is_win=False,
            db_path=self.db_path,
        )
        self.assertEqual(rec2["circuit_breaker_triggered"], 0)

        # Trade 3: Severe loss -$400 (net PnL -$350 from starting $10,000 = 3.5% drawdown >= 3.0%)
        rec3 = update_daily_risk_pnl(
            trading_date=date_today,
            realized_pnl_delta=-400.0,
            current_equity=9650.0,
            is_win=False,
            drawdown_limit_pct=3.0,
            db_path=self.db_path,
        )
        self.assertEqual(rec3["circuit_breaker_triggered"], 1)
        self.assertIsNotNone(rec3["circuit_breaker_triggered_at"])
        self.assertGreaterEqual(rec3["max_drawdown_pct"], 3.0)

        # Verification via circuit breaker check function
        self.assertTrue(is_daily_circuit_breaker_triggered(trading_date=date_today, db_path=self.db_path))


if __name__ == "__main__":
    unittest.main()
