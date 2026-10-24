"""Regression tests for Step 3: Scanner Reliability & Fault Isolation.

Covers:
1. Isolated symbol data failure (skipped with logged reason, cycle completes).
2. Stale candle detection (isolated symbol skipped, widespread triggers cycle abort).
3. Widespread Bybit API failure (consecutive API errors trigger fail-closed cycle abort).
4. Widespread data degradation abort (failure count exceeds limit).
5. Insufficient valid coverage abort (cannot publish unrepresentative cycle).
6. Failed-cycle retry lifecycle (failed cycle remains uncommitted, recovers on retry, deduplicates once successful).
"""

import json
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

os.environ["TRADINGOS_DISABLE_INNER_SCHEDULER"] = "1"

import scanner_pipeline
from scanner_pipeline import (
    run_once,
    validated_pair,
    DataValidationError,
    StaleCandleError,
    InsufficientHistoryError,
    SystemicScannerError,
    MAX_CONSECUTIVE_API_FAILURES,
    MAX_ISOLATED_FAILURES,
    MIN_EXAMINED_COVERAGE,
)


def make_candles(seconds, count=100, end_close=7200):
    last_start = end_close - seconds
    return [
        {
            "time": last_start - (count - 1 - i) * seconds,
            "open": 100.0,
            "high": 105.0,
            "low": 95.0,
            "close": 101.0,
            "volume": 10.0,
        }
        for i in range(count)
    ]


class TestScannerReliabilityAndFaultIsolation(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state_dir = self.root / "pipeline_state"
        self.manifest = self.state_dir / "current_cycle.json"

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except Exception:
            pass

    def test_stale_candle_validation_raises_specific_errors(self):
        """validated_pair raises StaleCandleError when 15m or 1H candle close does not match boundary."""
        fresh_15m = make_candles(900, end_close=7200)
        fresh_1h = make_candles(3600, end_close=7200)

        # 1. Stale 15m candle (closed at 6300 instead of 7200)
        stale_15m = make_candles(900, end_close=6300)
        with patch("scanner_pipeline.fetch_closed_candles", side_effect=[fresh_1h, stale_15m]):
            with self.assertRaises(StaleCandleError) as ctx:
                validated_pair("BTCUSDT", 7200)
            self.assertIn("15m candle is missing or belongs to wrong cycle", str(ctx.exception))
            self.assertTrue(issubclass(StaleCandleError, RuntimeError))

        # 2. Stale 1H candle (closed at 3600 instead of 7200)
        stale_1h = make_candles(3600, end_close=3600)
        with patch("scanner_pipeline.fetch_closed_candles", side_effect=[stale_1h, fresh_15m]):
            with self.assertRaises(StaleCandleError) as ctx:
                validated_pair("BTCUSDT", 7200)
            self.assertIn("1H candle is missing or belongs to wrong cycle", str(ctx.exception))

        # 3. Insufficient history (< 10 candles)
        short_15m = make_candles(900, count=5, end_close=7200)
        with patch("scanner_pipeline.fetch_closed_candles", side_effect=[fresh_1h, short_15m]):
            with self.assertRaises(InsufficientHistoryError) as ctx:
                validated_pair("BTCUSDT", 7200)
            self.assertIn("Not enough closed 15m/1H candles", str(ctx.exception))

    def test_isolated_symbol_failure_is_skipped_and_cycle_completes(self):
        """Isolated invalid symbols (e.g. bad OHLC) are skipped; valid symbols complete the cycle."""
        tickers = [{"symbol": "BADUSDT"}, {"symbol": "GOODUSDT"}]
        fresh_15m = make_candles(900, end_close=7200)
        fresh_1h = make_candles(3600, end_close=7200)

        def mock_validated_pair(symbol, boundary):
            if symbol == "BADUSDT":
                raise ValueError("Invalid OHLC data")
            return fresh_1h, fresh_15m

        fake_plan = {"trend": "BULLISH", "status": "WAIT", "reason": "TEST"}
        fake_rank = {"distance_pct": 0.5, "priority": 1, "tier": "NEAR", "tag": "READY_SOON"}

        with patch("scanner_pipeline.read_committed", return_value=None), \
             patch("scanner_pipeline.get_crypto_tickers", return_value=tickers), \
             patch("scanner_pipeline.validated_pair", side_effect=mock_validated_pair), \
             patch("scanner_pipeline.detect_trend", return_value={"trend": "BULLISH"}), \
             patch("scanner_pipeline.detect_liquidity_sweep", return_value=None), \
             patch("scanner_pipeline.build_trade_plan", return_value=fake_plan), \
             patch("scanner_pipeline.select_zones", return_value=(None, None)), \
             patch("scanner_pipeline.classify_opportunity", return_value=fake_rank), \
             patch("scanner_pipeline.publish", return_value="19700101T020000Z") as mock_pub, \
             patch("scanner_pipeline.time.time", return_value=7220):

            status = run_once(now=7220, quiet=True)
            self.assertEqual(status, "success")
            mock_pub.assert_called_once()
            reports = mock_pub.call_args.args[0]

            # BADUSDT was skipped, GOODUSDT was scanned
            self.assertEqual(reports["scanner"]["total_scanned"], 1)
            self.assertEqual(reports["scanner"]["skipped_count"], 1)
            self.assertEqual(reports["scanner"]["results"][0]["symbol"], "GOODUSDT")

    def test_isolated_stale_candle_is_skipped(self):
        """Isolated symbol with stale candles is skipped with category StaleCandleError."""
        tickers = [{"symbol": "STALEUSDT"}, {"symbol": "FRESHUSDT"}]
        fresh_15m = make_candles(900, end_close=7200)
        fresh_1h = make_candles(3600, end_close=7200)

        def mock_validated_pair(symbol, boundary):
            if symbol == "STALEUSDT":
                raise StaleCandleError("15m candle is missing or belongs to wrong cycle")
            return fresh_1h, fresh_15m

        fake_plan = {"trend": "BULLISH", "status": "WAIT", "reason": "TEST"}
        fake_rank = {"distance_pct": 0.5, "priority": 1, "tier": "NEAR", "tag": "READY_SOON"}

        with patch("scanner_pipeline.read_committed", return_value=None), \
             patch("scanner_pipeline.get_crypto_tickers", return_value=tickers), \
             patch("scanner_pipeline.validated_pair", side_effect=mock_validated_pair), \
             patch("scanner_pipeline.detect_trend", return_value={"trend": "BULLISH"}), \
             patch("scanner_pipeline.detect_liquidity_sweep", return_value=None), \
             patch("scanner_pipeline.build_trade_plan", return_value=fake_plan), \
             patch("scanner_pipeline.select_zones", return_value=(None, None)), \
             patch("scanner_pipeline.classify_opportunity", return_value=fake_rank), \
             patch("scanner_pipeline.publish", return_value="19700101T020000Z") as mock_pub, \
             patch("scanner_pipeline.time.time", return_value=7220):

            status = run_once(now=7220, quiet=True)
            self.assertEqual(status, "success")
            reports = mock_pub.call_args.args[0]
            self.assertEqual(reports["scanner"]["skipped_count"], 1)
            self.assertEqual(reports["scanner"]["results"][0]["symbol"], "FRESHUSDT")

    def test_widespread_api_failure_aborts_cycle(self):
        """Consecutive Bybit API failures abort cycle with SystemicScannerError, leaving uncommitted."""
        tickers = [{"symbol": f"COIN{i}USDT"} for i in range(10)]

        with patch("scanner_pipeline.read_committed", return_value=None), \
             patch("scanner_pipeline.get_crypto_tickers", return_value=tickers), \
             patch("scanner_pipeline.validated_pair", side_effect=RuntimeError("Bybit 503 Service Unavailable")), \
             patch("scanner_pipeline.publish") as mock_pub, \
             patch("scanner_pipeline.time.time", return_value=7220):

            with self.assertRaises(SystemicScannerError) as ctx:
                run_once(now=7220, quiet=True)

            self.assertIn("Systemic Bybit API failure", str(ctx.exception))
            self.assertIn("consecutive", str(ctx.exception))
            self.assertIn("cycle not committed", str(ctx.exception))
            mock_pub.assert_not_called()

    def test_widespread_validation_failures_aborts_cycle(self):
        """When total failures exceed MAX_ISOLATED_FAILURES, cycle aborts."""
        tickers = [{"symbol": f"COIN{i}USDT"} for i in range(25)]

        with patch("scanner_pipeline.read_committed", return_value=None), \
             patch("scanner_pipeline.get_crypto_tickers", return_value=tickers), \
             patch("scanner_pipeline.validated_pair", side_effect=StaleCandleError("stale candle")), \
             patch("scanner_pipeline.publish") as mock_pub, \
             patch("scanner_pipeline.time.time", return_value=7220):

            with self.assertRaises(SystemicScannerError) as ctx:
                run_once(now=7220, quiet=True)

            self.assertIn("Widespread data validation failure", str(ctx.exception))
            self.assertIn("cycle not committed", str(ctx.exception))
            mock_pub.assert_not_called()

    def test_insufficient_valid_coverage_aborts_cycle(self):
        """If valid coverage is below minimum threshold, cycle aborts rather than publishing partial view."""
        tickers = [{"symbol": f"COIN{i}USDT"} for i in range(20)]
        fresh_15m = make_candles(900, end_close=7200)
        fresh_1h = make_candles(3600, end_close=7200)

        # Only 2 symbols succeed, 18 fail (less than MIN_EXAMINED_COVERAGE = 15)
        def mock_val(sym, boundary):
            idx = int(sym.replace("COIN", "").replace("USDT", ""))
            if idx >= 2:
                raise StaleCandleError("stale")
            return fresh_1h, fresh_15m

        with patch("scanner_pipeline.read_committed", return_value=None), \
             patch("scanner_pipeline.get_crypto_tickers", return_value=tickers), \
             patch("scanner_pipeline.validated_pair", side_effect=mock_val), \
             patch("scanner_pipeline.detect_trend", return_value={"trend": "BULLISH"}), \
             patch("scanner_pipeline.publish") as mock_pub, \
             patch("scanner_pipeline.time.time", return_value=7220):

            with self.assertRaises(SystemicScannerError) as ctx:
                run_once(now=7220, quiet=True)

            self.assertIn("Widespread data validation failure", str(ctx.exception))
            self.assertIn("cycle not committed", str(ctx.exception))
            mock_pub.assert_not_called()

    def test_failed_cycle_retry_lifecycle(self):
        """Failed cycle leaves state uncommitted, allowing clean retry that succeeds and then deduplicates."""
        tickers = [{"symbol": "BTCUSDT"}]
        fresh_15m = make_candles(900, end_close=7200)
        fresh_1h = make_candles(3600, end_close=7200)
        fake_plan = {"trend": "BULLISH", "status": "WAIT", "reason": "TEST"}
        fake_rank = {"distance_pct": 0.5, "priority": 1, "tier": "NEAR", "tag": "READY_SOON"}

        committed_state = None

        def fake_read():
            return committed_state

        def fake_publish(reports):
            nonlocal committed_state
            committed_state = {
                "manifest": {
                    "cycle_id": reports["scanner"]["cycle_id"],
                    "candle_close_utc": reports["scanner"]["candle_close_utc"],
                },
                "scanner": reports["scanner"],
                "trade_plans": reports["trade_plans"],
                "opportunities": reports["opportunities"],
            }
            return reports["scanner"]["cycle_id"]

        # Step 1: Attempt 1 fails due to Bybit outage
        with patch("scanner_pipeline.read_committed", side_effect=fake_read), \
             patch("scanner_pipeline.get_crypto_tickers", return_value=tickers), \
             patch("scanner_pipeline.validated_pair", side_effect=RuntimeError("Bybit connection reset")), \
             patch("scanner_pipeline.publish", side_effect=fake_publish), \
             patch("scanner_pipeline.time.time", return_value=7225):

            with self.assertRaises(SystemicScannerError):
                run_once(now=7225, quiet=True)

            # Verification: Nothing was committed!
            self.assertIsNone(committed_state)

        # Step 2: Attempt 2 (Retry) after Bybit recovers within same candle window
        with patch("scanner_pipeline.read_committed", side_effect=fake_read), \
             patch("scanner_pipeline.get_crypto_tickers", return_value=tickers), \
             patch("scanner_pipeline.validated_pair", return_value=(fresh_1h, fresh_15m)), \
             patch("scanner_pipeline.detect_trend", return_value={"trend": "BULLISH"}), \
             patch("scanner_pipeline.detect_liquidity_sweep", return_value=None), \
             patch("scanner_pipeline.build_trade_plan", return_value=fake_plan), \
             patch("scanner_pipeline.select_zones", return_value=(None, None)), \
             patch("scanner_pipeline.classify_opportunity", return_value=fake_rank), \
             patch("scanner_pipeline.publish", side_effect=fake_publish), \
             patch("scanner_pipeline.time.time", return_value=7250):

            status_retry = run_once(now=7250, quiet=True)
            self.assertEqual(status_retry, "success")
            self.assertIsNotNone(committed_state)
            self.assertEqual(committed_state["manifest"]["cycle_id"], "19700101T020000Z")

        # Step 3: Attempt 3 (Duplicate trigger on same candle boundary)
        with patch("scanner_pipeline.read_committed", side_effect=fake_read), \
             patch("scanner_pipeline.time.time", return_value=7260):

            status_dup = run_once(now=7260, quiet=True)
            # Must return 'already_current' without re-running scan
            self.assertEqual(status_dup, "already_current")


if __name__ == "__main__":
    unittest.main()
