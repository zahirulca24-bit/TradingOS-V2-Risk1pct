import unittest
from unittest.mock import patch

from trade_plan import build_trade_plan


class TestTradePlan(unittest.TestCase):

    def setUp(self):
        # Six example 15m candles, with the last one as signal
        self.candles_15m = [
            {
                "time": i * 900,
                "open": 100,
                "high": 104,
                "low": 96,
                "close": 100,
            }
            for i in range(6)
        ]

        # Previous candle is bearish
        self.candles_15m[-2].update({
            "open": 101,
            "close": 99,
        })

        # Last candle sweeps 98 and bullish-engulfs previous body
        self.candles_15m[-1].update({
            "open": 98.5,
            "high": 103,
            "low": 97,
            "close": 102,
        })

        self.candles_1h = [{} for _ in range(6)]

    def evaluate(self, trend="BULLISH", highs=None, lows=None):
        if highs is None:
            highs = [115]

        if lows is None:
            lows = [98]

        with patch(
            "trade_plan.detect_trend",
            return_value={"trend": trend}
        ), patch(
            "trade_plan.find_confirmed_levels",
            return_value=(highs, lows)
        ):
            return build_trade_plan(
                self.candles_1h,
                self.candles_15m
            )

    def test_valid_long_plan(self):
        result = self.evaluate()

        self.assertEqual(result["status"], "PLAN_READY")
        self.assertEqual(result["direction"], "LONG")
        self.assertEqual(result["pattern"], "ENGULFING")
        self.assertGreaterEqual(result["rr"], 2)

    def test_neutral_is_excluded(self):
        result = self.evaluate(trend="NEUTRAL")

        self.assertEqual(result["status"], "EXCLUDED")
        self.assertEqual(
            result["reason"],
            "NEUTRAL_1H_STRUCTURE"
        )

    def test_no_sweep_is_wait(self):
        self.candles_15m[-1].update({
            "open": 100,
            "high": 103,
            "low": 99,
            "close": 102,
        })

        result = self.evaluate()

        self.assertEqual(result["status"], "WAIT")
        self.assertEqual(
            result["reason"],
            "NO_SWEEP_ON_LAST_CLOSED_CANDLE"
        )

    def test_sweep_without_pattern_is_rejected(self):
        self.candles_15m[-1].update({
            "open": 100,
            "high": 105,
            "low": 97,
            "close": 100.5,
        })

        result = self.evaluate()

        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(
            result["reason"],
            "SWEEP_WITHOUT_REQUIRED_PATTERN"
        )

    def test_low_rr_is_rejected(self):
        result = self.evaluate(highs=[107])

        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(
            result["reason"],
            "RISK_REWARD_TOO_LOW"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
