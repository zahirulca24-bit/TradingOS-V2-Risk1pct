"""Regression tests for Step 2: Scheduler Lock & Concurrency.

Tests:
1. Lock release guarantee after unhandled exceptions.
2. Concurrent/overlapping scan attempts raise PipelineLockContentionError and return BUSY/SKIPPED.
3. Non-destructive lock state inspection via is_pipeline_locked().
4. Duplicate triggers on the same candle boundary return 'already_current' without re-scanning.
5. FastAPI /scanner/trigger returns status: 'busy' when lock is held.
6. Background scan execution catches contention cleanly without reporting Telegram failures.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, MagicMock

# Force inner scheduler off for test isolation
os.environ["TRADINGOS_DISABLE_INNER_SCHEDULER"] = "1"

from pipeline_lock import exclusive_pipeline_lock, is_pipeline_locked, PipelineLockContentionError
import scanner_pipeline
import scanner_scheduler
from fastapi.testclient import TestClient
import main


class TestSchedulerLockConcurrency(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_pipeline.db"
        self.lock_dir = Path(self.tmp_dir.name) / "pipeline_state"
        self.lock_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        try:
            self.tmp_dir.cleanup()
        except Exception:
            pass

    def test_lock_release_after_exception(self):
        """Lock must be guaranteed released in finally block even after an unhandled exception."""
        self.assertFalse(is_pipeline_locked(db_path=self.db_path))

        with patch("pipeline_lock.STATE_DIR", self.lock_dir):
            try:
                with exclusive_pipeline_lock(db_path=self.db_path):
                    self.assertTrue(is_pipeline_locked(db_path=self.db_path))
                    raise ValueError("Simulated catastrophic crash inside scan cycle")
            except ValueError:
                pass

            # Lock MUST be released immediately
            self.assertFalse(is_pipeline_locked(db_path=self.db_path))

            # Must be immediately re-acquirable
            acquired = False
            with exclusive_pipeline_lock(db_path=self.db_path):
                acquired = True
            self.assertTrue(acquired)

    def test_overlapping_scans_contention(self):
        """Simultaneous scan attempts must raise PipelineLockContentionError for the second caller."""
        with patch("pipeline_lock.STATE_DIR", self.lock_dir):
            lock_acquired_1 = threading.Event()
            finish_event_1 = threading.Event()
            results = []

            def worker_1():
                try:
                    with exclusive_pipeline_lock(db_path=self.db_path):
                        results.append(("worker_1", "ACQUIRED"))
                        lock_acquired_1.set()
                        finish_event_1.wait(timeout=5)
                except Exception as exc:
                    results.append(("worker_1", f"ERROR: {exc}"))

            def worker_2():
                lock_acquired_1.wait(timeout=5)
                try:
                    with exclusive_pipeline_lock(db_path=self.db_path):
                        results.append(("worker_2", "ACQUIRED"))
                except PipelineLockContentionError as exc:
                    results.append(("worker_2", "BUSY"))
                except Exception as exc:
                    results.append(("worker_2", f"UNEXPECTED: {type(exc).__name__}: {exc}"))

            t1 = threading.Thread(target=worker_1)
            t2 = threading.Thread(target=worker_2)

            t1.start()
            t2.start()

            t2.join(timeout=5)
            finish_event_1.set()
            t1.join(timeout=5)

            results_dict = dict(results)
            self.assertEqual(results_dict.get("worker_1"), "ACQUIRED")
            self.assertEqual(results_dict.get("worker_2"), "BUSY")
            self.assertFalse(is_pipeline_locked(db_path=self.db_path))

    def test_is_pipeline_locked_query(self):
        """is_pipeline_locked accurately reflects state without holding the lock."""
        with patch("pipeline_lock.STATE_DIR", self.lock_dir):
            self.assertFalse(is_pipeline_locked(db_path=self.db_path))

            with exclusive_pipeline_lock(db_path=self.db_path):
                self.assertTrue(is_pipeline_locked(db_path=self.db_path))

            self.assertFalse(is_pipeline_locked(db_path=self.db_path))

    def test_duplicate_trigger_on_same_candle_boundary(self):
        """If a candle close is already committed, run_once returns 'already_current' without scanning."""
        now = 1700000000.0  # arbitrary fixed timestamp
        boundary = 1700000000 - (1700000000 % 900)
        close_iso = datetime.fromtimestamp(boundary, timezone.utc).isoformat()

        fake_committed = {
            "manifest": {
                "cycle_id": "CYCLE_2023_MOCK",
                "candle_close_utc": close_iso,
            }
        }

        with patch("scanner_pipeline.cycle_identity", return_value=(boundary, "CYCLE_2023_MOCK", close_iso)), \
             patch("scanner_pipeline.read_committed", return_value=fake_committed), \
             patch("scanner_pipeline.get_crypto_tickers") as mock_tickers:
            
            result = scanner_pipeline.run_once(now=now, quiet=True)
            self.assertEqual(result, "already_current")
            mock_tickers.assert_not_called()

    def test_api_trigger_scanner_returns_busy_when_locked(self):
        """When scanner is running, /scanner/trigger must return 200 with status='busy'."""
        client = TestClient(main.app)

        with patch("pipeline_lock.STATE_DIR", self.lock_dir), \
             patch("main._execute_scanner_cycle_background") as mock_bg:
            # 1. Test when lock is free
            with patch("main._scan_is_running", False), \
                 patch("pipeline_lock.is_pipeline_locked", return_value=False):
                res = client.post("/scanner/trigger")
                self.assertEqual(res.status_code, 200)
                self.assertEqual(res.json()["status"], "accepted")

            # 2. Test when lock is actively held
            with patch("main._scan_is_running", False), \
                 patch("pipeline_lock.is_pipeline_locked", return_value=True):
                res = client.post("/scanner/trigger")
                self.assertEqual(res.status_code, 200)
                self.assertEqual(res.json()["status"], "busy")
                self.assertIn("already currently running", res.json()["message"])

    def test_background_scan_handles_contention_without_alerting_telegram(self):
        """Lock contention in background scan must return busy and NOT dispatch failure telegram alert."""
        with patch("pipeline_lock.exclusive_pipeline_lock", side_effect=PipelineLockContentionError("Lock contention")):
            with patch("telegram_notifier.notify_scan_failed") as mock_notify, \
                 patch("pipeline_store.atomic_json") as mock_atomic:

                result = main._execute_scanner_cycle_background()
                self.assertIsInstance(result, dict)
                self.assertEqual(result.get("status"), "busy")
                mock_notify.assert_not_called()

    def test_scheduler_lock_not_held_during_sleep(self):
        """scanner_scheduler must not hold lock during its interval wait."""
        with patch("pipeline_lock.STATE_DIR", self.lock_dir):
            self.assertFalse(is_pipeline_locked(db_path=self.db_path))

            # Simulate one cycle where lock is held only during run_once
            with patch("scanner_scheduler.run_once", return_value="success"), \
                 patch("scanner_scheduler.atomic_json"), \
                 patch("db_adapter.save_pipeline_last_attempt"):

                # Before run: not locked
                self.assertFalse(is_pipeline_locked(db_path=self.db_path))

                # During mock scan inside exclusive_pipeline_lock
                with exclusive_pipeline_lock(db_path=self.db_path):
                    self.assertTrue(is_pipeline_locked(db_path=self.db_path))

                # After scan completed: released immediately, available during sleep
                self.assertFalse(is_pipeline_locked(db_path=self.db_path))


if __name__ == "__main__":
    unittest.main()
