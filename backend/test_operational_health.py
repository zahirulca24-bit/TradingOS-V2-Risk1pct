"""Unit tests for TradingOS V2 Operational Health & Monitoring (P2)."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from operational_health import (
    classify_error,
    sanitize_text,
    evaluate_freshness,
    atomic_write_json,
    build_health_report,
)


class TestOperationalHealth(unittest.TestCase):
    def test_error_classification_rate_limit(self):
        self.assertEqual(classify_error("HTTP 429 Too Many Requests"), "RATE_LIMIT")
        self.assertEqual(classify_error("Bybit IP rate limit reached"), "RATE_LIMIT")
        self.assertEqual(classify_error("Error code 10002: ratelimit"), "RATE_LIMIT")

    def test_error_classification_network_timeout(self):
        self.assertEqual(classify_error("Connection timed out after 10000ms"), "NETWORK_TIMEOUT")
        self.assertEqual(classify_error("Failed to establish connection: name resolution error"), "NETWORK_TIMEOUT")
        self.assertEqual(classify_error("HTTPSConnectionPool: read timeout"), "NETWORK_TIMEOUT")

    def test_error_classification_general(self):
        self.assertEqual(classify_error("ValueError: Invalid payload structure"), "GENERAL_ERROR")
        self.assertEqual(classify_error("Cycle expired before publication"), "GENERAL_ERROR")
        self.assertIsNone(classify_error(None))

    def test_sanitize_secrets(self):
        raw = "Error with api_key: 'abcdef123456789' and secret: 'my_very_secret_key_value'"
        clean = sanitize_text(raw)
        self.assertNotIn("abcdef123456789", clean)
        self.assertNotIn("my_very_secret_key_value", clean)
        self.assertIn("[REDACTED]", clean)

    def test_freshness_fresh_on_recent_successful_cycle(self):
        now = datetime.now(timezone.utc)
        recent_close = (now - timedelta(minutes=5)).isoformat()
        committed = {"candle_close_utc": recent_close, "status": "success"}
        attempt = {"status": "success", "at": now.isoformat()}

        freshness, age = evaluate_freshness(committed, attempt, now)
        self.assertEqual(freshness, "FRESH")
        self.assertIsNotNone(age)
        self.assertLessEqual(age, 300.5)

    def test_freshness_never_fresh_after_failed_attempt(self):
        now = datetime.now(timezone.utc)
        recent_close = (now - timedelta(minutes=5)).isoformat()
        committed = {"candle_close_utc": recent_close, "status": "success"}
        failed_attempt = {
            "status": "failed",
            "at": now.isoformat(),
            "reason": "Bybit rate limit 429",
        }

        freshness, age = evaluate_freshness(committed, failed_attempt, now)
        self.assertEqual(freshness, "STALE", "Must never report FRESH after a failed attempt")

    def test_freshness_stale_when_cycle_is_old(self):
        now = datetime.now(timezone.utc)
        old_close = (now - timedelta(minutes=25)).isoformat()
        committed = {"candle_close_utc": old_close, "status": "success"}
        attempt = {"status": "success", "at": (now - timedelta(minutes=24)).isoformat()}

        freshness, age = evaluate_freshness(committed, attempt, now)
        self.assertEqual(freshness, "STALE")
        self.assertGreater(age, 1080)

    def test_freshness_unknown_when_no_committed_cycle(self):
        freshness, age = evaluate_freshness(None, None)
        self.assertEqual(freshness, "UNKNOWN")
        self.assertIsNone(age)

    def test_atomic_write_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "test_report.json"
            sample_data = {"test": 123, "status": "ONLINE"}
            atomic_write_json(target, sample_data)

            self.assertTrue(target.is_file())
            content = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(content, sample_data)
            # Ensure no leftover tmp files
            tmp_files = list(Path(tmpdir).glob("health-*.tmp"))
            self.assertEqual(len(tmp_files), 0)

    def test_build_health_report_structure(self):
        report = build_health_report(
            backend_status="ONLINE",
            backend_pid=111,
            frontend_status="ONLINE",
            frontend_pid=222,
            scheduler_running=True,
            scheduler_logical_count=1,
            scheduler_pids=[333],
            supervisor_status="RUNNING",
            supervisor_pid=444,
            uptime_seconds=60,
            restart_counts={"backend": 0, "frontend": 0, "scanner": 0},
        )
        self.assertIn("timestamp", report)
        self.assertIn("overall_status", report)
        self.assertIn("supervisor", report)
        self.assertIn("backend", report)
        self.assertIn("frontend", report)
        self.assertIn("scanner", report)
        self.assertEqual(report["backend"]["status"], "ONLINE")
        self.assertEqual(report["frontend"]["status"], "ONLINE")
        self.assertEqual(report["scanner"]["scheduler_status"], "RUNNING")
        self.assertEqual(report["scanner"]["logical_count"], 1)


if __name__ == "__main__":
    unittest.main()
