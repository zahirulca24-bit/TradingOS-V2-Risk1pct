"""Unit and regression tests for Step 8: Durable closed trade state, notifications, and breaker inputs.

Tests:
1. Process restart resilience: Outbox and closed trade notification state survive process restarts.
2. Repeated API page: Polling identical Bybit closed trades creates 0 duplicate DB records and 0 duplicate alerts.
3. Partial closes: Multiple partial closes on the same order get unique composite IDs and are recorded separately.
4. Same symbol multiple trades: Multiple distinct trades on the same symbol do not collide.
5. Notification dispatch failure and retry: Failed dispatch increments retry counter, remains pending/failed, and retries successfully.
6. Database error handling: Database failure raises an exception and is never silently counted as success.
7. Daily risk breaker integration: Validated closed trade PnL feeds daily risk records and trips the 3% circuit breaker when losses reach threshold.
"""

import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from closed_pnl_tracker import (
    fetch_bybit_closed_pnl,
    format_closed_trade_alert,
    generate_close_id,
    sync_and_persist_closed_trades,
    dispatch_pending_notifications,
    check_and_notify_closed_trades,
)
from db_adapter import (
    init_db,
    save_closed_trade,
    get_closed_trades,
    queue_notification,
    get_pending_notifications,
    mark_notification_sent,
    mark_notification_failed,
    mark_closed_trade_notified,
    feed_closed_trades_to_daily_breaker,
    get_daily_risk_record,
    evaluate_daily_risk_guard,
    is_daily_circuit_breaker_triggered,
)


class TestDurableClosedTradesAndOutbox(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "test_step8_closed_trades.db"
        init_db(db_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_mock_session(self, closed_pnl_list):
        session = MagicMock()
        session.get_closed_pnl.return_value = {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "list": closed_pnl_list
            }
        }
        return session

    def test_composite_close_id_generation(self):
        """Stable composite IDs are deterministically generated and prevent collisions."""
        item1 = {
            "orderId": "order_1001",
            "createdTime": 1700000000000,
            "closedPnl": 25.50,
            "qty": "0.5",
        }
        id1 = generate_close_id(item1)
        self.assertEqual(id1, "order_1001_1700000000000_25.5_0.5")

        # Explicit execId / id takes precedence if available
        item2 = {
            "orderId": "order_1001",
            "execId": "exec_abc123",
        }
        id2 = generate_close_id(item2)
        self.assertEqual(id2, "order_1001_exec_abc123")

    def test_repeated_api_page_deduplication(self):
        """Repeatedly querying the same API page yields 0 duplicate DB records and 0 duplicate outbox items."""
        mock_raw = [
            {
                "orderId": "order_btc_1",
                "symbol": "BTCUSDT",
                "side": "Buy",
                "qty": "0.1",
                "avgEntryPrice": "60000.0",
                "avgExitPrice": "62000.0",
                "closedPnl": "200.0",
                "fillCount": 1,
                "createdTime": 1700001000000,
                "updatedTime": 1700001000000,
            }
        ]
        session = self._create_mock_session(mock_raw)

        # First sync
        synced1 = sync_and_persist_closed_trades(session=session, db_path=self.db_path)
        self.assertEqual(len(synced1), 1)

        trades_in_db = get_closed_trades(db_path=self.db_path)
        self.assertEqual(len(trades_in_db), 1)

        pending_outbox = get_pending_notifications(db_path=self.db_path)
        self.assertEqual(len(pending_outbox), 1)

        # Dispatch the notification
        sent_messages = []
        dispatched = dispatch_pending_notifications(send_fn=lambda msg, **kw: sent_messages.append(msg) or True, db_path=self.db_path)
        self.assertEqual(dispatched, 1)
        self.assertEqual(len(sent_messages), 1)

        # Second sync with identical Bybit data
        synced2 = sync_and_persist_closed_trades(session=session, db_path=self.db_path)
        self.assertEqual(len(synced2), 1)

        # Assert no duplicate closed trade records and no duplicate outbox items
        trades_after = get_closed_trades(db_path=self.db_path)
        self.assertEqual(len(trades_after), 1)

        pending_after = get_pending_notifications(db_path=self.db_path)
        self.assertEqual(len(pending_after), 0)

        # Dispatch again - nothing should be sent
        dispatched_again = dispatch_pending_notifications(send_fn=lambda msg, **kw: sent_messages.append(msg) or True, db_path=self.db_path)
        self.assertEqual(dispatched_again, 0)
        self.assertEqual(len(sent_messages), 1)

    def test_process_restart_resilience(self):
        """Notification delivery state persists across process restarts without memory caches."""
        mock_raw = [
            {
                "orderId": "order_eth_1",
                "symbol": "ETHUSDT",
                "side": "Sell",
                "qty": "2.0",
                "avgEntryPrice": "3000.0",
                "avgExitPrice": "2900.0",
                "closedPnl": "200.0",
                "fillCount": 1,
                "createdTime": 1700002000000,
            }
        ]
        session = self._create_mock_session(mock_raw)

        # 1. Sync and persist
        sync_and_persist_closed_trades(session=session, db_path=self.db_path)

        # 2. Simulate process restart by reloading from DB directly
        pending_before_dispatch = get_pending_notifications(db_path=self.db_path)
        self.assertEqual(len(pending_before_dispatch), 1)
        self.assertEqual(pending_before_dispatch[0]["status"], "PENDING")

        # 3. Dispatch alert
        sent = []
        dispatch_pending_notifications(send_fn=lambda msg, **kw: sent.append(msg) or True, db_path=self.db_path)
        self.assertEqual(len(sent), 1)

        # 4. Simulate another process restart
        pending_after_restart = get_pending_notifications(db_path=self.db_path)
        self.assertEqual(len(pending_after_restart), 0)

        trades = get_closed_trades(db_path=self.db_path)
        self.assertEqual(trades[0]["notified"], 1)

    def test_partial_closes_on_same_order(self):
        """Multiple partial closes on the same order have distinct composite IDs and are persisted."""
        mock_raw = [
            {
                "orderId": "order_sol_split",
                "execId": "exec_part_1",
                "symbol": "SOLUSDT",
                "side": "Buy",
                "qty": "10.0",
                "avgEntryPrice": "150.0",
                "avgExitPrice": "155.0",
                "closedPnl": "50.0",
                "fillCount": 1,
                "createdTime": 1700003000000,
            },
            {
                "orderId": "order_sol_split",
                "execId": "exec_part_2",
                "symbol": "SOLUSDT",
                "side": "Buy",
                "qty": "10.0",
                "avgEntryPrice": "150.0",
                "avgExitPrice": "160.0",
                "closedPnl": "100.0",
                "fillCount": 2,
                "createdTime": 1700003050000,
            }
        ]
        session = self._create_mock_session(mock_raw)

        synced = sync_and_persist_closed_trades(session=session, db_path=self.db_path)
        self.assertEqual(len(synced), 2)

        trades = get_closed_trades(symbol="SOLUSDT", db_path=self.db_path)
        self.assertEqual(len(trades), 2)
        close_ids = {t["close_id"] for t in trades}
        self.assertIn("order_sol_split_exec_part_1", close_ids)
        self.assertIn("order_sol_split_exec_part_2", close_ids)

        # Verify partial indicators
        part2 = next(t for t in trades if t["close_id"] == "order_sol_split_exec_part_2")
        self.assertEqual(part2["is_partial"], 1)

    def test_same_symbol_multiple_distinct_trades(self):
        """Multiple distinct trades for the same symbol do not collide or overwrite each other."""
        mock_raw = [
            {
                "orderId": "order_btc_early",
                "symbol": "BTCUSDT",
                "side": "Buy",
                "qty": "0.1",
                "avgEntryPrice": "65000.0",
                "avgExitPrice": "66000.0",
                "closedPnl": "100.0",
                "createdTime": 1700004000000,
            },
            {
                "orderId": "order_btc_later",
                "symbol": "BTCUSDT",
                "side": "Sell",
                "qty": "0.1",
                "avgEntryPrice": "66000.0",
                "avgExitPrice": "65000.0",
                "closedPnl": "100.0",
                "createdTime": 1700005000000,
            }
        ]
        session = self._create_mock_session(mock_raw)

        synced = sync_and_persist_closed_trades(session=session, db_path=self.db_path)
        self.assertEqual(len(synced), 2)

        trades = get_closed_trades(symbol="BTCUSDT", db_path=self.db_path)
        self.assertEqual(len(trades), 2)
        order_ids = {t["order_id"] for t in trades}
        self.assertEqual(order_ids, {"order_btc_early", "order_btc_later"})

    def test_outbox_failure_and_retry(self):
        """Failed notification dispatch increments retry count, preserves status, and succeeds on retry."""
        trade_data = {
            "close_id": "close_retry_test",
            "order_id": "order_retry_1",
            "symbol": "AVAXUSDT",
            "side": "Buy",
            "qty": "5.0",
            "entry_price": 30.0,
            "exit_price": 28.0,
            "closed_pnl": -10.0,
        }
        save_closed_trade(trade_data, db_path=self.db_path)
        queue_notification("closed_trade_close_retry_test", "TRADE_CLOSED", "Test Alert", db_path=self.db_path)

        # 1. First attempt fails (simulated network exception)
        def failing_sender(msg, **kw):
            raise ConnectionError("Telegram gateway timeout")

        dispatched = dispatch_pending_notifications(send_fn=failing_sender, db_path=self.db_path)
        self.assertEqual(dispatched, 0)

        pending = get_pending_notifications(db_path=self.db_path)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["status"], "FAILED")
        self.assertEqual(pending[0]["retry_count"], 1)
        self.assertIn("Telegram gateway timeout", pending[0]["last_error"])

        # 2. Second attempt succeeds
        sent = []
        dispatched_second = dispatch_pending_notifications(send_fn=lambda msg, **kw: sent.append(msg) or True, db_path=self.db_path)
        self.assertEqual(dispatched_second, 1)

        pending_after = get_pending_notifications(db_path=self.db_path)
        self.assertEqual(len(pending_after), 0)

    def test_database_error_fails_closed(self):
        """Database errors during trade persistence raise exceptions and are never swallowed."""
        trade_data = {
            # Missing close_id to trigger ValueError
            "order_id": "order_bad_data",
            "symbol": "DOGEUSDT",
        }
        with self.assertRaises(ValueError):
            save_closed_trade(trade_data, db_path=self.db_path)

    def test_breaker_integration_and_3pct_drawdown(self):
        """Validated closed trades feed daily risk records and trigger the 3% circuit breaker."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        
        # Initialize baseline with $10,000 equity
        from db_adapter import update_daily_risk_pnl
        update_daily_risk_pnl(
            trading_date=today,
            starting_equity=10000.0,
            current_equity=10000.0,
            db_path=self.db_path,
        )

        rec0 = get_daily_risk_record(trading_date=today, db_path=self.db_path)
        self.assertEqual(rec0["starting_equity_usd"], 10000.0)
        self.assertEqual(rec0["circuit_breaker_triggered"], 0)

        # 1. First trade: small loss -$100 (-1.0% drawdown)
        trade1 = {
            "close_id": "close_dd_1",
            "order_id": "ord_dd_1",
            "symbol": "BTCUSDT",
            "side": "Buy",
            "qty": "0.1",
            "entry_price": 60000.0,
            "exit_price": 59000.0,
            "closed_pnl": -100.0,
        }
        save_closed_trade(trade1, db_path=self.db_path)
        feed_closed_trades_to_daily_breaker(trading_date=today, db_path=self.db_path)

        rec1 = get_daily_risk_record(trading_date=today, db_path=self.db_path)
        self.assertEqual(rec1["realized_pnl_usd"], -100.0)
        self.assertEqual(rec1["max_drawdown_pct"], 1.0)
        self.assertEqual(rec1["circuit_breaker_triggered"], 0)
        self.assertFalse(is_daily_circuit_breaker_triggered(trading_date=today, db_path=self.db_path))

        # 2. Second trade: -$250 loss (cumulative -$350 = -3.5% drawdown >= 3.0% limit)
        trade2 = {
            "close_id": "close_dd_2",
            "order_id": "ord_dd_2",
            "symbol": "ETHUSDT",
            "side": "Buy",
            "qty": "1.0",
            "entry_price": 3000.0,
            "exit_price": 2750.0,
            "closed_pnl": -250.0,
        }
        save_closed_trade(trade2, db_path=self.db_path)
        feed_closed_trades_to_daily_breaker(trading_date=today, db_path=self.db_path)

        rec2 = get_daily_risk_record(trading_date=today, db_path=self.db_path)
        self.assertEqual(rec2["realized_pnl_usd"], -350.0)
        self.assertEqual(rec2["max_drawdown_pct"], 3.5)
        self.assertEqual(rec2["circuit_breaker_triggered"], 1)
        self.assertTrue(is_daily_circuit_breaker_triggered(trading_date=today, db_path=self.db_path))

        # Risk guard must now block execution
        guard = evaluate_daily_risk_guard(trading_date=today, db_path=self.db_path)
        self.assertFalse(guard["can_trade"])
        self.assertEqual(guard["risk_state"], "BLOCKED_CIRCUIT_BREAKER")
        self.assertTrue(guard["circuit_breaker_triggered"])


if __name__ == "__main__":
    unittest.main()
