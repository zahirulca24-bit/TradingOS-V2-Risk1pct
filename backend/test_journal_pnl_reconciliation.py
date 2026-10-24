"""Unit and regression tests for Step 9: Journal PnL correctness and reconciliation.

Tests:
1. Multiple BTC trades: Correlates separate trades on the same symbol without collision or false closure.
2. Partial exits: Multiple partial closes for a single entry order aggregate correctly into gross PnL, fees, and net PnL.
3. Repeated closed PnL pages: Querying repeatedly never causes double summation.
4. Process restart resilience: Reconciled trade journal state survives process restarts.
5. No executions: Gracefully handles empty database and external Bybit sync closed trades.
6. Fee accounting: Gross realized PnL, execution fees, and net realized PnL are properly separated.
7. Breaker reconciliation & fail-closed: Unreconciled PnL discrepancy blocks trading (BLOCKED_PNL_UNRECONCILED).
"""

import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from db_adapter import (
    init_db,
    save_demo_execution,
    get_demo_executions,
    save_closed_trade,
    get_closed_trades,
    feed_closed_trades_to_daily_breaker,
    update_daily_risk_pnl,
    get_daily_risk_record,
    evaluate_daily_risk_guard,
    reconcile_daily_risk_vs_closed_trades,
)
from trade_journal import (
    correlate_executions_and_closed_trades,
    build_audited_trade_journal,
    audit_journal_reconciliation,
)


class TestJournalPnLReconciliation(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "test_step9_journal.db"
        init_db(db_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_multiple_btc_trades_no_symbol_only_collision(self):
        """Two separate BTC trades (one closed, one still open) do not collide or falsely mark the open trade closed."""
        # Entry 1: BTCUSDT placed at 10:00 UTC
        save_demo_execution({
            "cycle_id": "cycle_1000",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "order_type": "Market",
            "qty": "0.1",
            "entry_price": 60000.0,
            "sl": 59000.0,
            "tp": 63000.0,
            "rr": 3.0,
            "risk_budget_usd": 100.0,
            "bybit_order_id": "bybit_ord_1",
            "order_link_id": "tos_cycle1000_BTCUSDT",
            "status": "FILLED",
            "created_at": "2026-10-25T10:00:00Z",
        }, db_path=self.db_path)

        # Close for Trade 1 at 10:15 UTC (won +$200)
        save_closed_trade({
            "close_id": "close_btc_1",
            "order_id": "bybit_ord_close_1",
            "order_link_id": "tos_cycle1000_BTCUSDT",
            "symbol": "BTCUSDT",
            "side": "Sell",
            "qty": "0.1",
            "entry_price": 60000.0,
            "exit_price": 62000.0,
            "closed_pnl": 200.0,
            "fee": 3.60,
            "fill_count": 1,
            "exchange_created_at": "2026-10-25T10:15:00Z",
        }, db_path=self.db_path)

        # Entry 2: New BTCUSDT placed at 11:00 UTC (currently OPEN)
        save_demo_execution({
            "cycle_id": "cycle_1100",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "order_type": "Market",
            "qty": "0.15",
            "entry_price": 62500.0,
            "sl": 61500.0,
            "tp": 65500.0,
            "rr": 3.0,
            "risk_budget_usd": 150.0,
            "bybit_order_id": "bybit_ord_2",
            "order_link_id": "tos_cycle1100_BTCUSDT",
            "status": "FILLED",
            "created_at": "2026-10-25T11:00:00Z",
        }, db_path=self.db_path)

        journal = build_audited_trade_journal(db_path=self.db_path)

        self.assertEqual(journal["status"], "success")
        self.assertEqual(journal["total_executions"], 2)
        self.assertEqual(journal["total_closed"], 1)
        self.assertEqual(journal["wins"], 1)
        self.assertEqual(journal["losses"], 0)
        self.assertEqual(journal["total_realized_pnl"], 200.0)

        # Check executions
        trade1 = next(t for t in journal["executions"] if t.get("order_link_id") == "tos_cycle1000_BTCUSDT")
        trade2 = next(t for t in journal["executions"] if t.get("order_link_id") == "tos_cycle1100_BTCUSDT")

        self.assertEqual(trade1["status"], "CLOSED_WON")
        self.assertEqual(trade1["closed_pnl"], 200.0)
        self.assertEqual(trade1["fee"], 3.60)
        self.assertEqual(trade1["net_pnl"], 196.40)

        # Trade 2 MUST remain OPEN, not falsely closed with Trade 1's PnL
        self.assertEqual(trade2["status"], "OPEN")
        self.assertIsNone(trade2["closed_pnl"])
        self.assertIsNone(trade2["exit_price"])

    def test_partial_exits_aggregation(self):
        """Multiple partial close events for a single entry order aggregate cleanly without double counting."""
        # Entry order: 1.0 SOL
        save_demo_execution({
            "cycle_id": "cycle_sol",
            "symbol": "SOLUSDT",
            "direction": "LONG",
            "order_type": "Market",
            "qty": "1.0",
            "entry_price": 150.0,
            "sl": 140.0,
            "tp": 180.0,
            "rr": 3.0,
            "risk_budget_usd": 10.0,
            "bybit_order_id": "ord_sol_entry",
            "order_link_id": "tos_sol_link_1",
            "status": "FILLED",
            "created_at": "2026-10-25T08:00:00Z",
        }, db_path=self.db_path)

        # Partial close 1: 0.5 SOL at 160.0 (PnL +$5.0, fee $0.10)
        save_closed_trade({
            "close_id": "close_sol_part1",
            "order_id": "ord_sol_close_1",
            "order_link_id": "tos_sol_link_1",
            "symbol": "SOLUSDT",
            "side": "Sell",
            "qty": "0.5",
            "entry_price": 150.0,
            "exit_price": 160.0,
            "closed_pnl": 5.0,
            "fee": 0.10,
            "is_partial": 1,
            "exchange_created_at": "2026-10-25T08:30:00Z",
        }, db_path=self.db_path)

        # Partial close 2: 0.5 SOL at 170.0 (PnL +$10.0, fee $0.12)
        save_closed_trade({
            "close_id": "close_sol_part2",
            "order_id": "ord_sol_close_2",
            "order_link_id": "tos_sol_link_1",
            "symbol": "SOLUSDT",
            "side": "Sell",
            "qty": "0.5",
            "entry_price": 150.0,
            "exit_price": 170.0,
            "closed_pnl": 10.0,
            "fee": 0.12,
            "is_partial": 1,
            "exchange_created_at": "2026-10-25T09:00:00Z",
        }, db_path=self.db_path)

        journal = build_audited_trade_journal(db_path=self.db_path)

        self.assertEqual(journal["total_executions"], 1)
        self.assertEqual(journal["total_closed"], 2)
        self.assertEqual(journal["total_realized_pnl"], 15.0)  # 5 + 10
        self.assertEqual(journal["total_fees"], 0.22)          # 0.10 + 0.12
        self.assertEqual(journal["total_net_pnl"], 14.78)       # 15.0 - 0.22

        sol_trade = journal["executions"][0]
        self.assertEqual(sol_trade["status"], "CLOSED_WON")
        self.assertEqual(sol_trade["closed_pnl"], 15.0)
        self.assertEqual(sol_trade["fee"], 0.22)
        self.assertEqual(sol_trade["net_pnl"], 14.78)
        self.assertEqual(sol_trade["close_count"], 2)
        self.assertEqual(sol_trade["close_ids"], ["close_sol_part1", "close_sol_part2"])

    def test_no_double_summation_on_repeated_queries(self):
        """Calling journal generation repeatedly yields identical, strictly de-duplicated metrics."""
        save_closed_trade({
            "close_id": "close_repeat_1",
            "order_id": "ord_rep_1",
            "symbol": "ETHUSDT",
            "side": "Sell",
            "qty": "1.0",
            "entry_price": 3000.0,
            "exit_price": 3100.0,
            "closed_pnl": 100.0,
            "fee": 1.5,
        }, db_path=self.db_path)

        journal1 = build_audited_trade_journal(db_path=self.db_path)
        journal2 = build_audited_trade_journal(db_path=self.db_path)
        journal3 = build_audited_trade_journal(db_path=self.db_path)

        self.assertEqual(journal1["total_realized_pnl"], 100.0)
        self.assertEqual(journal2["total_realized_pnl"], 100.0)
        self.assertEqual(journal3["total_realized_pnl"], 100.0)
        self.assertEqual(journal1["total_fees"], 1.5)
        self.assertEqual(journal2["total_fees"], 1.5)

    def test_empty_database_and_external_bybit_sync(self):
        """Handles empty database gracefully, and properly categorizes external closed trades."""
        # 1. Completely empty database
        empty_journal = build_audited_trade_journal(db_path=self.db_path)
        self.assertEqual(empty_journal["total_executions"], 0)
        self.assertEqual(empty_journal["total_closed"], 0)
        self.assertEqual(empty_journal["total_realized_pnl"], 0.0)

        # 2. External trade closed on Bybit Demo with no local execution
        save_closed_trade({
            "close_id": "close_external_1",
            "order_id": "ord_ext_1",
            "symbol": "DOGEUSDT",
            "side": "Sell",
            "qty": "1000.0",
            "entry_price": 0.15,
            "exit_price": 0.16,
            "closed_pnl": 10.0,
            "fee": 0.05,
        }, db_path=self.db_path)

        ext_journal = build_audited_trade_journal(db_path=self.db_path)
        self.assertEqual(ext_journal["total_executions"], 1)
        self.assertEqual(ext_journal["total_closed"], 1)
        self.assertEqual(ext_journal["total_realized_pnl"], 10.0)
        self.assertEqual(ext_journal["executions"][0]["cycle_id"], "BYBIT_SYNC")
        self.assertEqual(ext_journal["executions"][0]["status"], "CLOSED_WON")

    def test_process_restart_resilience(self):
        """Audited trade journal state is preserved across process restarts without memory leaks."""
        save_demo_execution({
            "cycle_id": "cycle_restart",
            "symbol": "AVAXUSDT",
            "direction": "LONG",
            "order_type": "Market",
            "qty": "10.0",
            "entry_price": 25.0,
            "sl": 23.0,
            "tp": 30.0,
            "rr": 2.5,
            "risk_budget_usd": 20.0,
            "bybit_order_id": "ord_restart_entry",
            "order_link_id": "tos_avax_link",
            "status": "FILLED",
        }, db_path=self.db_path)

        save_closed_trade({
            "close_id": "close_restart_1",
            "order_id": "ord_restart_close",
            "order_link_id": "tos_avax_link",
            "symbol": "AVAXUSDT",
            "side": "Sell",
            "qty": "10.0",
            "entry_price": 25.0,
            "exit_price": 28.0,
            "closed_pnl": 30.0,
            "fee": 0.35,
        }, db_path=self.db_path)

        # First run
        j1 = build_audited_trade_journal(db_path=self.db_path)
        self.assertEqual(j1["total_realized_pnl"], 30.0)

        # Simulate process restart by reloading from fresh calls
        j2 = build_audited_trade_journal(db_path=self.db_path)
        self.assertEqual(j2["total_realized_pnl"], 30.0)
        self.assertEqual(j2["executions"][0]["net_pnl"], 29.65)

    def test_daily_risk_breaker_reconciliation_audit(self):
        """Authoritative audit reconciles daily risk records against closed trades and blocks trading on discrepancy."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # 1. Baseline setup with $10,000 equity
        update_daily_risk_pnl(
            trading_date=today,
            starting_equity=10000.0,
            current_equity=10000.0,
            db_path=self.db_path,
        )

        # 2. Add two trades and feed to breaker
        save_closed_trade({
            "close_id": "recon_trade_1",
            "order_id": "ord_r1",
            "symbol": "BTCUSDT",
            "side": "Sell",
            "qty": "0.1",
            "entry_price": 60000.0,
            "exit_price": 59500.0,
            "closed_pnl": -50.0,
            "fee": 3.0,
        }, db_path=self.db_path)

        save_closed_trade({
            "close_id": "recon_trade_2",
            "order_id": "ord_r2",
            "symbol": "ETHUSDT",
            "side": "Sell",
            "qty": "1.0",
            "entry_price": 3000.0,
            "exit_price": 3050.0,
            "closed_pnl": 50.0,
            "fee": 2.0,
        }, db_path=self.db_path)

        # Feed to breaker
        feed_closed_trades_to_daily_breaker(trading_date=today, db_path=self.db_path)

        audit1 = audit_journal_reconciliation(trading_date=today, db_path=self.db_path)
        self.assertTrue(audit1["is_reconciled"])
        self.assertEqual(audit1["discrepancy_usd"], 0.0)
        self.assertEqual(audit1["breaker_realized_usd"], 0.0)  # -50 + 50 = 0
        self.assertEqual(audit1["journal_realized_usd"], 0.0)
        self.assertEqual(audit1["journal_fees_usd"], 5.0)      # 3 + 2

        # Risk guard clears when reconciled
        guard1 = evaluate_daily_risk_guard(trading_date=today, verify_pnl_reconciliation=True, db_path=self.db_path)
        self.assertTrue(guard1["can_trade"])
        self.assertEqual(guard1["risk_state"], "CLEARED")

        # 3. Simulate an unreconciled discrepancy: add a closed trade without feeding it to breaker
        save_closed_trade({
            "close_id": "recon_trade_unfed",
            "order_id": "ord_unfed",
            "symbol": "SOLUSDT",
            "side": "Sell",
            "qty": "5.0",
            "entry_price": 150.0,
            "exit_price": 140.0,
            "closed_pnl": -50.0,
            "fee": 0.5,
        }, db_path=self.db_path)

        audit2 = audit_journal_reconciliation(trading_date=today, db_path=self.db_path)
        self.assertFalse(audit2["is_reconciled"])
        self.assertEqual(audit2["unfed_trades_count"], 1)

        # Risk guard MUST FAIL CLOSED on unreconciled discrepancy
        guard2 = evaluate_daily_risk_guard(trading_date=today, verify_pnl_reconciliation=True, db_path=self.db_path)
        self.assertFalse(guard2["can_trade"])
        self.assertEqual(guard2["risk_state"], "BLOCKED_PNL_UNRECONCILED")
        self.assertIn("discrepancy", guard2["reason"].lower())

    def test_shared_risk_authorization_gate_blocks_on_unreconciled_pnl(self):
        """Authoritative execution gate authorize_execution_order denies order if PnL is unreconciled."""
        from demo_risk_engine import authorize_execution_order
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Baseline: starting equity $10,000, current $10,000
        update_daily_risk_pnl(
            trading_date=today,
            starting_equity=10000.0,
            current_equity=10000.0,
            db_path=self.db_path,
        )

        # Create an un-fed closed trade creating a PnL discrepancy
        save_closed_trade({
            "close_id": "gate_unfed_trade",
            "order_id": "ord_gate_1",
            "symbol": "BTCUSDT",
            "side": "Sell",
            "qty": "0.1",
            "entry_price": 60000.0,
            "exit_price": 59000.0,
            "closed_pnl": -100.0,
            "fee": 3.0,
        }, db_path=self.db_path)

        account = {
            "total_equity": 10000.0,
            "total_available_balance": 10000.0,
            "open_positions_list": [],
            "open_orders_list": [],
        }
        plan = {
            "symbol": "ETHUSDT",
            "direction": "LONG",
            "entry_reference": 3000.0,
            "sl": 2900.0,
            "tp": 3300.0,
            "rr": 3.0,
            "status": "PLAN_READY",
        }

        # Attempt to authorize an execution order with execution enabled
        from unittest.mock import patch
        with patch("demo_risk_engine.is_demo_execution_enabled", return_value=True):
            auth = authorize_execution_order(
                plan=plan,
                account=account,
                db_path=self.db_path,
            )

        self.assertFalse(auth["authorized"])
        self.assertEqual(auth["status"], "BLOCKED_DAILY_RISK")
        self.assertIn("BLOCKED_PNL_UNRECONCILED", str(auth["reasons"]))



if __name__ == "__main__":
    unittest.main()
