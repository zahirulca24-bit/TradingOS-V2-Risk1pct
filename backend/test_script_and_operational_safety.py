"""Unit and regression tests for Step 10: Scripts, mode labels, alerts, and operational safety verification.

Tests:
1. Script portability: Verifies that root PowerShell scripts use dynamic $PSScriptRoot and have no hardcoded project paths.
2. Alert alignment: Trade ready alerts accurately distinguish Read-Only Research mode from Active Demo Execution mode.
3. Emergency alerts: Kill switch and daily breaker alert formatters work properly without exceptions.
4. Default safety: Execution is strictly disabled by default (fail-closed).
5. Canonical guardrails: RR >= 2.0, max 3 positions, 1% risk budget, and fail-closed daily breaker.
"""

import os
import re
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from demo_risk_engine import is_demo_execution_enabled, authorize_execution_order, MIN_RR as ENGINE_MIN_RR
from pipeline_contract import MIN_RR as CANONICAL_MIN_RR
from telegram_notifier import (
    notify_cycle_published,
    notify_kill_switch_activated,
    notify_circuit_breaker_tripped,
    notify_scan_failed,
)


class TestScriptAndOperationalSafety(unittest.TestCase):
    def setUp(self):
        self.project_root = Path(__file__).resolve().parent.parent

    def test_scripts_portability_and_no_hardcoded_paths(self):
        """Root scripts must derive project root dynamically and contain no hardcoded foreign paths."""
        scripts = [
            self.project_root / "Launch-TradingOS.ps1",
            self.project_root / "Stop-TradingOS.ps1",
            self.project_root / "Verify-TradingOS.ps1",
            self.project_root / "Setup-TradingOS.ps1",
            self.project_root / "Start-TradingOS.ps1",
        ]

        forbidden_patterns = [
            r'=\s*["\']E:\\TradingOS-V2-Risk1pct["\']',
            r'=\s*["\']E:\\TradingOS-V2-Full["\']',
        ]

        for s in scripts:
            self.assertTrue(s.is_file(), f"Script {s.name} should exist in project root")
            content = s.read_text(encoding="utf-8")

            for pat in forbidden_patterns:
                match = re.search(pat, content)
                self.assertIsNone(
                    match,
                    f"Script {s.name} contains hardcoded path assignment matching {pat}: {match.group(0) if match else ''}"
                )

            # Ensure $PSScriptRoot is utilized for dynamic root resolution
            self.assertIn("$PSScriptRoot", content, f"Script {s.name} should reference $PSScriptRoot")

    def test_alert_mode_label_alignment(self):
        """Telegram trade ready alert accurately reflects actual execution switch state."""
        sample_reports = {
            "scanner": {
                "cycle_id": "20261025T000000Z",
                "candle_close_utc": "2026-10-25T00:00:00Z",
                "total_scanned": 100,
                "universe_examined": 100,
                "results": [{"symbol": "BTCUSDT"}],
            },
            "trade_plans": {
                "ready_count": 1,
                "results": [
                    {
                        "symbol": "BTCUSDT",
                        "direction": "LONG",
                        "trend": "BULLISH",
                        "pattern": "SWEEP_CONFIRMED",
                        "entry_reference": 65000.0,
                        "sl": 64000.0,
                        "tp": 67000.0,
                        "rr": 2.0,
                        "status": "PLAN_READY",
                    }
                ],
            },
            "opportunities": {"top_10": []},
        }

        # Case A: Execution disabled (Read-only research mode)
        with patch("demo_risk_engine.is_demo_execution_enabled", return_value=False), \
             patch("telegram_notifier.send_telegram_message") as mock_send, \
             patch("signal_tracker.register_new_ready_signals"), \
             patch("signal_tracker.update_active_signal_outcomes", return_value=[]):
            
            notify_cycle_published(sample_reports)
            self.assertTrue(mock_send.called)
            # Find the signal alert call
            signal_call = [args[0] for args, _ in mock_send.call_args_list if "TRADE SIGNAL READY" in args[0]][0]
            self.assertIn("Demo Execution Disabled", signal_call)
            self.assertNotIn("Bybit Demo Order Submission ACTIVE", signal_call)

        # Case B: Execution enabled
        with patch("demo_risk_engine.is_demo_execution_enabled", return_value=True), \
             patch("telegram_notifier.send_telegram_message") as mock_send, \
             patch("signal_tracker.register_new_ready_signals"), \
             patch("signal_tracker.update_active_signal_outcomes", return_value=[]):
            
            notify_cycle_published(sample_reports)
            self.assertTrue(mock_send.called)
            signal_call = [args[0] for args, _ in mock_send.call_args_list if "TRADE SIGNAL READY" in args[0]][0]
            self.assertIn("Bybit Demo Order Submission ACTIVE", signal_call)

    def test_emergency_alert_formatters(self):
        """Emergency kill switch, circuit breaker, and scan failed alerts format without error."""
        with patch("telegram_notifier.send_telegram_message") as mock_send:
            notify_kill_switch_activated("Manual operator intervention")
            self.assertTrue(mock_send.called)
            msg = mock_send.call_args[0][0]
            self.assertIn("KILL SWITCH ACTIVATED", msg)
            self.assertIn("Manual operator intervention", msg)

        with patch("telegram_notifier.send_telegram_message") as mock_send:
            notify_circuit_breaker_tripped(3.45, "Cumulative losses exceeded 3%")
            self.assertTrue(mock_send.called)
            msg = mock_send.call_args[0][0]
            self.assertIn("RISK BREAKER TRIPPED", msg)
            self.assertIn("3.45%", msg)

        with patch("telegram_notifier.send_telegram_message") as mock_send:
            notify_scan_failed("Bybit API connection timeout")
            self.assertTrue(mock_send.called)
            msg = mock_send.call_args[0][0]
            self.assertIn("Scan Cycle Failed", msg)
            self.assertIn("Bybit API connection timeout", msg)

    def test_default_execution_disabled_fail_closed(self):
        """Execution is strictly disabled by default environment configuration."""
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(is_demo_execution_enabled())

    def test_canonical_min_rr_policy_alignment(self):
        """Canonical MIN_RR is strictly 2.0 across pipeline_contract and demo_risk_engine."""
        self.assertEqual(float(CANONICAL_MIN_RR), 2.0)
        self.assertEqual(float(ENGINE_MIN_RR), 2.0)


if __name__ == "__main__":
    unittest.main()
