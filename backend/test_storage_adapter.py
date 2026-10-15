"""Unit and integration tests for TradingOS V2 Dual Database Storage Adapter.
Validates:
1. SQLite fallback engine and zero-regression baseline.
2. PostgreSQL adapter query adaptation, parameter normalization, and DSN handling.
3. Durable pipeline cycle and status persistence.
4. Distributed advisory locking behavior and concurrency control.
5. Idempotent ingestion and duplicate prevention.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from db_adapter import (
    adapt_sql_for_postgres,
    get_active_engine,
    get_db_connection,
    init_db,
    load_committed_pipeline_cycle,
    load_pipeline_last_attempt,
    normalize_postgres_dsn,
    postgres_distributed_lock,
    save_pipeline_cycle,
    save_pipeline_last_attempt,
    PIPELINE_LOCK_KEY,
)
from journal_db import (
    get_daily_reports,
    ingest_cycle,
    record_dry_runs,
)
from pipeline_lock import exclusive_pipeline_lock


from test_journal import make_valid_cycle


class TestStorageAdapter(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="tradingos_adapter_test_")
        self.db_path = Path(self.temp_dir) / "test_journal.db"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_engine_detection_defaults_to_sqlite(self):
        with patch.dict(os.environ, {}, clear=True):
            engine = get_active_engine()
            self.assertEqual(engine, "sqlite")
            # Explicit db_path forces SQLite
            self.assertEqual(get_active_engine(db_path=self.db_path), "sqlite")

    def test_engine_detection_postgres_when_dsn_configured(self):
        dsn = "postgres://user:secret@ep-test.neon.tech/tradingos"
        with patch("db_adapter.PSYCOPG2_AVAILABLE", True):
            engine = get_active_engine(dsn=dsn)
            self.assertEqual(engine, "postgres")

    def test_normalize_postgres_dsn(self):
        raw = "postgres://user:secret@ep-test.neon.tech/tradingos"
        normalized = normalize_postgres_dsn(raw)
        self.assertTrue(normalized.startswith("postgresql://"))
        self.assertIn("sslmode=require", normalized)

        already_ssl = "postgresql://user:pass@host/db?sslmode=verify-full"
        self.assertEqual(normalize_postgres_dsn(already_ssl), already_ssl)

    def test_adapt_sql_for_postgres(self):
        sqlite_query = "SELECT * FROM journal_signals WHERE cycle_id = ? AND symbol = ?"
        pg_query = adapt_sql_for_postgres(sqlite_query)
        self.assertEqual(pg_query, "SELECT * FROM journal_signals WHERE cycle_id = %s AND symbol = %s")

        sqlite_insert = "INSERT OR IGNORE INTO journal_cycles (cycle_id) VALUES (?)"
        pg_insert = adapt_sql_for_postgres(sqlite_insert)
        self.assertIn("ON CONFLICT (cycle_id) DO NOTHING", pg_insert)
        self.assertNotIn("OR IGNORE", pg_insert)
        self.assertIn("%s", pg_insert)

    def test_sqlite_schema_initialization(self):
        engine = init_db(db_path=self.db_path)
        self.assertEqual(engine, "sqlite")
        self.assertTrue(self.db_path.is_file())

        with get_db_connection(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row["name"] for row in cur.fetchall()}
            self.assertIn("journal_cycles", tables)
            self.assertIn("journal_signals", tables)
            self.assertIn("journal_dry_runs", tables)
            self.assertIn("pipeline_cycles", tables)
            self.assertIn("pipeline_meta", tables)

    def test_durable_pipeline_storage_cycle_roundtrip(self):
        reports = make_valid_cycle("20261015T180000Z")
        reports["scanner"]["trading_enabled"] = False
        saved_id = save_pipeline_cycle(reports, db_path=self.db_path)
        self.assertEqual(saved_id, "20261015T180000Z")

        loaded = load_committed_pipeline_cycle(db_path=self.db_path)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["manifest"]["cycle_id"], "20261015T180000Z")
        self.assertEqual(loaded["scanner"]["cycle_id"], "20261015T180000Z")
        self.assertEqual(len(loaded["scanner"]["results"]), 3)
        self.assertEqual(loaded["scanner"]["trading_enabled"], False)

    def test_pipeline_last_attempt_roundtrip(self):
        attempt = {"status": "success", "at": "2026-10-15T18:00:20+00:00"}
        save_pipeline_last_attempt(attempt, db_path=self.db_path)

        loaded = load_pipeline_last_attempt(db_path=self.db_path)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["status"], "success")
        self.assertEqual(loaded["at"], "2026-10-15T18:00:20+00:00")

    def test_journal_ingest_and_idempotency(self):
        reports = make_valid_cycle("20261015T180000Z")
        ok1, count1 = ingest_cycle(reports, db_path=self.db_path)
        self.assertTrue(ok1)
        self.assertEqual(count1, 3)

        # Re-ingesting the exact same cycle must be idempotent and succeed without duplicate inserts
        ok2, count2 = ingest_cycle(reports, db_path=self.db_path)
        self.assertTrue(ok2)
        self.assertEqual(count2, 0)

        with get_db_connection(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT count(*) as cnt FROM journal_signals WHERE cycle_id = ?", ("20261015T180000Z",))
            self.assertEqual(cur.fetchone()["cnt"], 3)

    def test_dry_run_recording_and_upsert(self):
        dry_runs = [
            {
                "symbol": "BTCUSDT",
                "direction": "LONG",
                "status": "DRY_RUN_VALID",
                "reasons": [],
                "reason_messages": [],
                "equity_basis_usd": 50000.0,
                "risk_budget_usd": 500.0,
                "proposed_quantity": "0.714",
                "qty_step": "0.001",
                "entry_reference": 65500.0,
                "sl": 64800.0,
                "tp": 67000.0,
                "rr": 2.14,
                "estimated_fees_and_slippage_usd": 15.0,
                "total_conservative_projected_risk_usd": 515.0,
                "existing_slots": 0,
                "remaining_slots": 3,
            }
        ]
        inserted = record_dry_runs("20261015T180000Z", dry_runs, db_path=self.db_path)
        self.assertEqual(inserted, 1)

        # Upsert with updated values
        dry_runs[0]["status"] = "BLOCKED_MAX_POSITIONS"
        dry_runs[0]["remaining_slots"] = 0
        updated = record_dry_runs("20261015T180000Z", dry_runs, db_path=self.db_path)
        self.assertEqual(updated, 1)

        with get_db_connection(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT status, remaining_slots FROM journal_dry_runs WHERE cycle_id = ? AND symbol = ?", ("20261015T180000Z", "BTCUSDT"))
            row = cur.fetchone()
            self.assertEqual(row["status"], "BLOCKED_MAX_POSITIONS")
            self.assertEqual(row["remaining_slots"], 0)

    def test_daily_reports_aggregation(self):
        reports = make_valid_cycle("20261015T180000Z", candle_close="2026-10-15T18:00:00+00:00")
        ingest_cycle(reports, db_path=self.db_path)

        daily = get_daily_reports(target_date="2026-10-15", db_path=self.db_path)
        self.assertEqual(len(daily), 1)
        r = daily[0]
        self.assertEqual(r["utc_date"], "2026-10-15")
        self.assertEqual(r["cycles_committed"], 1)
        self.assertEqual(r["total_signal_observations"], 3)
        self.assertEqual(r["plan_ready_count"], 1)
        self.assertEqual(r["wait_count"], 1)
        self.assertEqual(r["executed_trades"], "0 known to this read-only journal, not a claim about exchange-wide account activity")

    def test_postgres_advisory_lock_mocked_success_and_contention(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        # Scenario 1: Lock successfully acquired
        mock_cur.fetchone.return_value = (True,)
        with patch("psycopg2.connect", return_value=mock_conn):
            with postgres_distributed_lock(dsn="postgres://user:pass@ep-test.neon.tech/db"):
                mock_cur.execute.assert_called_with("SELECT pg_try_advisory_lock(%s)", (PIPELINE_LOCK_KEY,))
            mock_cur.execute.assert_called_with("SELECT pg_advisory_unlock(%s)", (PIPELINE_LOCK_KEY,))

        # Scenario 2: Lock already held (contention)
        mock_cur.fetchone.return_value = (False,)
        with patch("psycopg2.connect", return_value=mock_conn):
            with self.assertRaises(RuntimeError) as ctx:
                with postgres_distributed_lock(dsn="postgres://user:pass@ep-test.neon.tech/db"):
                    pass
            self.assertIn("actively executing", str(ctx.exception))

    def test_exclusive_pipeline_lock_file_fallback(self):
        # When SQLite is active, exclusive_pipeline_lock uses local OS file lock
        with exclusive_pipeline_lock(db_path=self.db_path):
            # Attempting second lock concurrently must raise RuntimeError
            with self.assertRaises(RuntimeError):
                with exclusive_pipeline_lock(db_path=self.db_path):
                    pass

    def test_postgres_lock_held_throughout_execution_and_released_on_error(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_cur.fetchone.return_value = (True,)

        events = []

        def track_unlock(*args, **kwargs):
            events.append("unlocked")

        mock_cur.execute.side_effect = lambda sql, params=None: track_unlock() if "pg_advisory_unlock" in str(sql) else None

        # Verify lock remains held throughout scanner execution
        with patch("psycopg2.connect", return_value=mock_conn):
            with postgres_distributed_lock(dsn="postgres://user:pass@ep-test.neon.tech/db"):
                events.append("executing_scan")
                # While scan is executing, unlock must not have been called
                self.assertNotIn("unlocked", events)
            # After exit, unlock must have been called
            self.assertIn("unlocked", events)

        # Verify lock is cleanly released even if scanner execution raises an error
        events.clear()
        with patch("psycopg2.connect", return_value=mock_conn):
            with self.assertRaises(ValueError):
                with postgres_distributed_lock(dsn="postgres://user:pass@ep-test.neon.tech/db"):
                    events.append("error_during_scan")
                    raise ValueError("Scan interrupted")
            self.assertIn("unlocked", events)
            mock_conn.close.assert_called()

    def test_postgres_transaction_commit_and_rollback(self):
        from db_adapter import PostgresConnectionWrapper
        mock_conn = MagicMock()

        # Normal execution commits
        with PostgresConnectionWrapper(mock_conn) as conn:
            pass
        mock_conn.commit.assert_called_once()
        mock_conn.close.assert_called_once()

        # Exception causes rollback
        mock_conn.reset_mock()
        with self.assertRaises(RuntimeError):
            with PostgresConnectionWrapper(mock_conn) as conn:
                raise RuntimeError("DB error")
        mock_conn.rollback.assert_called_once()
        mock_conn.commit.assert_not_called()
    def test_cloud_fail_closed_and_browser_authentication(self):
        import asyncio
        from unittest.mock import AsyncMock
        from main import prevent_anonymous_cloud_data_access

        async def run_checks():
            req = MagicMock(method='GET')
            req.url.path = '/scanner/cycle'
            nxt = AsyncMock(return_value='next_called')

            # 1. Local environment (RENDER unset) allows access
            with patch.dict(os.environ, {}, clear=True):
                req.headers = {}
                res = await prevent_anonymous_cloud_data_access(req, nxt)
                self.assertEqual(res, 'next_called')

            # 2. Cloud environment (RENDER=true) with no token configured:
            # Untrusted / anonymous origin fails closed (403)
            with patch.dict(os.environ, {'RENDER': 'true'}, clear=True):
                req.headers = {'origin': 'https://untrusted-attacker.com'}
                res = await prevent_anonymous_cloud_data_access(req, nxt)
                self.assertEqual(res.status_code, 403)
                # Origin is not authentication; fail closed without a token.
                req.headers = {'origin': 'https://trading-os-v2-risk1pct.vercel.app'}
                res = await prevent_anonymous_cloud_data_access(req, nxt)
                self.assertEqual(res.status_code, 403)

            # 3. Cloud environment with token configured:
            # Unauthenticated request rejected (401)
            with patch.dict(os.environ, {'RENDER': 'true', 'TRADINGOS_AUTH_TOKEN': 'secret-123'}, clear=True):
                req.headers = {'origin': 'https://trading-os-v2-risk1pct.vercel.app'}
                res = await prevent_anonymous_cloud_data_access(req, nxt)
                self.assertEqual(res.status_code, 401)

                # Invalid token rejected (401)
                req.headers = {'Authorization': 'Bearer wrong-token'}
                res = await prevent_anonymous_cloud_data_access(req, nxt)
                self.assertEqual(res.status_code, 401)

                # Valid Bearer token authenticated
                req.headers = {'Authorization': 'Bearer secret-123'}
                res = await prevent_anonymous_cloud_data_access(req, nxt)
                self.assertEqual(res, 'next_called')

                # Valid X-TradingOS-Auth token authenticated
                req.headers = {'X-TradingOS-Auth': 'secret-123'}
                res = await prevent_anonymous_cloud_data_access(req, nxt)
                self.assertEqual(res, 'next_called')

        asyncio.run(run_checks())


if __name__ == "__main__":
    unittest.main()

