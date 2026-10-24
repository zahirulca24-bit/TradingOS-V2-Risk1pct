"""Regression tests for Step 4: Canonical RR and pipeline qualification.

Verifies:
1. Shared canonical MIN_RR = 2.0 across all qualification boundaries.
2. RR 1.99 is REJECTED with 'RISK_REWARD_TOO_LOW'; RR 2.00 is accepted as PLAN_READY.
3. RR 1.50 is REJECTED (no longer qualified as PLAN_READY).
4. Numerical precision (NaN/Inf, zero/negative stop distance, inverted geometry) rejected cleanly.
5. Fabricated PLAN_READY with RR < 2.0 or corrupt geometry is rejected at pipeline_store validation.
6. demo_order_executor filters out any plan with RR < 2.0.
7. A normal scan is not globally aborted due only to a legitimate low-RR rejection.
"""

from decimal import Decimal
import math
import os
import unittest
from unittest.mock import patch, MagicMock

os.environ["TRADINGOS_DISABLE_INNER_SCHEDULER"] = "1"

from pipeline_contract import MIN_RR as CONTRACT_MIN_RR
import trade_plan
from trade_plan import build_trade_plan
import pipeline_store
from pipeline_store import validate_reports
import demo_order_executor
import demo_risk_engine
import scanner_pipeline


class TestCanonicalRRQualification(unittest.TestCase):

    def setUp(self):
        # 6 sample 15m candles
        self.candles_15m = [
            {"time": i * 900, "open": 100.0, "high": 104.0, "low": 96.0, "close": 100.0}
            for i in range(6)
        ]
        # Previous candle: bearish (open 101.0, close 99.0)
        self.candles_15m[-2].update({"open": 101.0, "high": 102.0, "low": 98.0, "close": 99.0})
        # Signal candle: sweeps 98.0 and engulfs previous body (open 98.5 <= 99.0, close 102.0 >= 101.0)
        # entry = 102.0, low = 97.0
        # sl = 97.0 - (102.0 * 0.001) = 96.898
        # risk = 102.0 - 96.898 = 5.102
        self.candles_15m[-1].update({
            "open": 98.5,
            "high": 103.0,
            "low": 97.0,
            "close": 102.0,
        })
        self.candles_1h = [{} for _ in range(6)]

    def _evaluate(self, highs=None, lows=None, trend="BULLISH"):
        if highs is None:
            # reward = 112.204 - 102.0 = 10.204 -> rr = 10.204 / 5.102 = 2.00
            highs = [112.204]
        if lows is None:
            lows = [98.0]
        with patch("trade_plan.detect_trend", return_value={"trend": trend}), \
             patch("trade_plan.find_confirmed_levels", return_value=(highs, lows)):
            return build_trade_plan(self.candles_1h, self.candles_15m)

    def test_canonical_min_rr_is_shared_across_modules(self):
        """All modules must share canonical MIN_RR = 2.0 policy."""
        self.assertEqual(CONTRACT_MIN_RR, 2.0)
        self.assertEqual(trade_plan.MIN_RR, 2.0)
        self.assertEqual(demo_risk_engine.MIN_RR, Decimal("2.0"))
        self.assertEqual(demo_order_executor.MIN_RR, 2.0)

    def test_rr_1_99_rejected_low_rr(self):
        """Candidate with RR = 1.99 must be REJECTED with 'RISK_REWARD_TOO_LOW'."""
        # entry = 102.0, risk = 5.102
        # for rr = 1.99: reward = 1.99 * 5.102 = 10.15298 -> target = 102.0 + 10.15298 = 112.15298
        target = 102.0 + (1.99 * 5.102)
        result = self._evaluate(highs=[target])
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["reason"], "RISK_REWARD_TOO_LOW")
        self.assertEqual(result["rr"], 1.99)

    def test_rr_2_00_accepted_plan_ready(self):
        """Candidate with RR = 2.00 must qualify as PLAN_READY."""
        # entry = 102.0, risk = 5.102
        # for rr = 2.00: reward = 10.204 -> target = 112.204
        target = 102.0 + (2.00 * 5.102)
        result = self._evaluate(highs=[target])
        self.assertEqual(result["status"], "PLAN_READY")
        self.assertEqual(result["reason"], "SWEEP_PATTERN_AND_RR_CONFIRMED")
        self.assertEqual(result["rr"], 2.0)

    def test_rr_1_50_rejected_not_ready(self):
        """Legacy RR 1.50 candidates must now be REJECTED instead of PLAN_READY."""
        # for rr = 1.50: reward = 1.50 * 5.102 = 7.653 -> target = 102.0 + 7.653 = 109.653
        target = 102.0 + (1.50 * 5.102)
        result = self._evaluate(highs=[target])
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["reason"], "RISK_REWARD_TOO_LOW")
        self.assertEqual(result["rr"], 1.5)

    def test_invalid_numerical_precision_and_geometry_rejected(self):
        """NaN, Inf, non-positive distances, and inverted geometry must be cleanly rejected."""
        # 1. Inverted LONG geometry: target below entry -> NO_OPPOSING_SWING_TARGET
        result = self._evaluate(highs=[95.0])
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["reason"], "NO_OPPOSING_SWING_TARGET")

        # 2. Corrupt candle prices (NaN)
        self.candles_15m[-1]["close"] = float("nan")
        with patch("trade_plan.detect_trend", return_value={"trend": "BULLISH"}), \
             patch("trade_plan.find_confirmed_levels", return_value=([120.0], [98.0])):
            nan_res = build_trade_plan(self.candles_1h, self.candles_15m)
            self.assertEqual(nan_res["status"], "REJECTED")
            self.assertEqual(nan_res["reason"], "INVALID_CANDLE_DATA")

        # 3. Non-finite target (Inf)
        self.candles_15m[-1]["close"] = 102.0
        with patch("trade_plan.detect_trend", return_value={"trend": "BULLISH"}), \
             patch("trade_plan.find_confirmed_levels", return_value=([float("inf")], [98.0])):
            inf_res = build_trade_plan(self.candles_1h, self.candles_15m)
            self.assertEqual(inf_res["status"], "REJECTED")
            self.assertEqual(inf_res["reason"], "INVALID_RISK_OR_REWARD")

    def test_fabricated_plan_ready_rr_below_two_rejected_at_validation(self):
        """Fabricated PLAN_READY with RR < 2.0 must fail pipeline_store validation."""
        ident = "19700101T020000Z"
        common = {
            "status": "success",
            "cycle_id": ident,
            "candle_close_utc": "1970-01-01T02:00:00+00:00",
            "generated_at": "1970-01-01T02:00:20+00:00",
            "failures": [],
            "trading_enabled": False,
        }

        # 1. Fabricate plan with RR = 1.50
        fabricated_reports_1_5 = {
            "scanner": {**common, "results": [{"symbol": "BTCUSDT", "trend": "BULLISH"}]},
            "trade_plans": {
                **common,
                "results": [{
                    "symbol": "BTCUSDT",
                    "trend": "BULLISH",
                    "direction": "LONG",
                    "status": "PLAN_READY",
                    "entry_reference": 100.0,
                    "sl": 96.0,
                    "tp": 106.0,
                    "rr": 1.5,
                }],
            },
            "opportunities": {**common, "top_10": [{"symbol": "BTCUSDT"}], "remaining": []},
        }

        with self.assertRaises(ValueError) as ctx:
            validate_reports(fabricated_reports_1_5)
        self.assertIn("Unqualified ready plan", str(ctx.exception))

        # 2. Fabricate plan with RR = 1.99
        fabricated_reports_1_99 = {
            "scanner": {**common, "results": [{"symbol": "BTCUSDT", "trend": "BULLISH"}]},
            "trade_plans": {
                **common,
                "results": [{
                    "symbol": "BTCUSDT",
                    "trend": "BULLISH",
                    "direction": "LONG",
                    "status": "PLAN_READY",
                    "entry_reference": 100.0,
                    "sl": 96.0,
                    "tp": 107.96,
                    "rr": 1.99,
                }],
            },
            "opportunities": {**common, "top_10": [{"symbol": "BTCUSDT"}], "remaining": []},
        }

        with self.assertRaises(ValueError) as ctx:
            validate_reports(fabricated_reports_1_99)
        self.assertIn("Unqualified ready plan", str(ctx.exception))

        # 3. Valid plan with RR = 2.00 passes validation
        valid_reports = {
            "scanner": {**common, "results": [{"symbol": "BTCUSDT", "trend": "BULLISH"}]},
            "trade_plans": {
                **common,
                "results": [{
                    "symbol": "BTCUSDT",
                    "trend": "BULLISH",
                    "direction": "LONG",
                    "status": "PLAN_READY",
                    "entry_reference": 100.0,
                    "sl": 96.0,
                    "tp": 108.0,
                    "rr": 2.0,
                }],
            },
            "opportunities": {**common, "top_10": [{"symbol": "BTCUSDT"}], "remaining": []},
        }
        # Must not raise
        validate_reports(valid_reports)

    def test_demo_order_executor_filters_out_rr_below_two(self):
        """demo_order_executor must ignore any plan with RR < 2.0 even if labeled PLAN_READY."""
        reports = {
            "trade_plans": {
                "results": [
                    {"symbol": "LOWRRUSDT", "status": "PLAN_READY", "rr": 1.5},
                    {"symbol": "ALMOSTUSDT", "status": "PLAN_READY", "rr": 1.99},
                ]
            }
        }
        with patch("demo_order_executor.is_demo_execution_enabled", return_value=True), \
             patch("demo_order_executor.read_demo_account", return_value={"total_equity": 10000.0, "available_balance": 10000.0}):
            executions = demo_order_executor.execute_demo_plans(reports)
            self.assertEqual(executions, [])

    def test_normal_scan_not_aborted_by_low_rr_rejection(self):
        """A scan cycle with legitimate low-RR rejections must complete and publish without error."""
        tickers = [{"symbol": "LOWRRUSDT"}, {"symbol": "HIGHRRUSDT"}]
        fresh_15m = [
            {"time": 7200 - 900 * (10 - i), "open": 100.0, "high": 105.0, "low": 95.0, "close": 101.0, "volume": 10.0}
            for i in range(10)
        ]
        fresh_1h = [
            {"time": 7200 - 3600 * (10 - i), "open": 100.0, "high": 105.0, "low": 95.0, "close": 101.0, "volume": 10.0}
            for i in range(10)
        ]

        def fake_plan(h, f):
            # Returns REJECTED with RISK_REWARD_TOO_LOW
            return {"trend": "BULLISH", "status": "REJECTED", "reason": "RISK_REWARD_TOO_LOW", "rr": 1.5}

        with patch("scanner_pipeline.read_committed", return_value=None), \
             patch("scanner_pipeline.get_crypto_tickers", return_value=tickers), \
             patch("scanner_pipeline.validated_pair", return_value=(fresh_1h, fresh_15m)), \
             patch("scanner_pipeline.detect_trend", return_value={"trend": "BULLISH"}), \
             patch("scanner_pipeline.build_trade_plan", side_effect=fake_plan), \
             patch("scanner_pipeline.detect_liquidity_sweep", return_value=None), \
             patch("scanner_pipeline.select_zones", return_value=(None, None)), \
             patch("scanner_pipeline.classify_opportunity", return_value={"distance_pct": 0.5, "priority": 1, "tier": "NEAR", "tag": "READY_SOON"}), \
             patch("scanner_pipeline.publish", return_value="19700101T020000Z") as mock_pub, \
             patch("scanner_pipeline.time.time", return_value=7220):

            status = scanner_pipeline.run_once(now=7220, quiet=True)
            self.assertEqual(status, "success")
            mock_pub.assert_called_once()
            pub_reports = mock_pub.call_args.args[0]
            # Rejected plan count reflected accurately
            self.assertEqual(pub_reports["trade_plans"]["rejected_count"], 2)
            self.assertEqual(pub_reports["trade_plans"]["ready_count"], 0)


if __name__ == "__main__":
    unittest.main()
