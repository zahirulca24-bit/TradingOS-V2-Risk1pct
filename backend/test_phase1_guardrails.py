"""Offline Phase 1 safety tests; no network/API keys/orders required."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from liquidity_sweep import find_confirmed_levels
from market_structure import find_swings
from opportunity_ranker import confirmed_swings, classify_opportunity
from pipeline_contract import (
    is_complete_cycle,
    is_snapshot_fresh,
    last_closed_boundary,
)
from universe_candles import fetch_closed_candles


class TestClosedCandleSelection(unittest.TestCase):
    def test_current_open_candle_excluded(self):
        # UTC t=1000: t=0 15m candle closed; t=900 still open.
        rows = [
            ["900000", "10", "12", "9", "11", "100"],
            ["0", "8", "10", "7", "9", "120"],
        ]
        with patch("universe_candles.get_bybit_data", return_value={"list": rows}), patch(
            "universe_candles.time.time", return_value=1000.0
        ):
            candles = fetch_closed_candles("BTCUSDT", "15")
        self.assertEqual([c["time"] for c in candles], [0])

    def test_latest_closed_boundary(self):
        self.assertEqual(last_closed_boundary(1790000012), 1789999200)
        self.assertEqual(last_closed_boundary(900), 900)
        self.assertEqual(last_closed_boundary(899), 0)


class TestPivotTiming(unittest.TestCase):
    def setUp(self):
        self.candles = [
            {"time": i * 900, "high": high, "low": 90}
            for i, high in enumerate([101, 102, 120, 103, 104, 105])
        ]

    def test_ranker_pivot_confirmed_only_after_right_close(self):
        highs, _ = confirmed_swings(self.candles)
        pivot = next(x for x in highs if x["price"] == 120)
        self.assertEqual(pivot["confirmed_at"], 4 * 900 + 900)

    def test_future_right_candle_prevents_pivot(self):
        future = [dict(x) for x in self.candles]
        future[4]["high"] = 130
        highs, _ = confirmed_swings(future)
        self.assertNotIn(120, [x["price"] for x in highs])

    def test_liquidity_levels_not_usable_early(self):
        early, _ = find_confirmed_levels(self.candles, before_time=4499)
        on_time, _ = find_confirmed_levels(self.candles, before_time=4500)
        self.assertNotIn(120, early)
        self.assertIn(120, on_time)

    def test_one_hour_pivot_confirmed_after_two_closed_bars(self):
        hourly = [dict(c, time=i * 3600) for i, c in enumerate(self.candles)]
        highs, _ = find_swings(hourly)
        pivot = next(x for x in highs if x["price"] == 120)
        self.assertEqual(pivot["confirmed_at"], 5 * 3600)


class TestOpportunitySafety(unittest.TestCase):
    def test_neutral_not_ranked(self):
        self.assertIsNone(classify_opportunity("NEUTRAL", 100, None, None))


class TestSnapshotContracts(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 13, 12, 15, tzinfo=timezone.utc)

    def test_successful_recent_snapshot(self):
        data = {"status": "success", "generated_at": (self.now - timedelta(minutes=10)).isoformat(), "failures": []}
        self.assertTrue(is_snapshot_fresh(data, now=self.now))

    def test_old_or_partial_snapshot_is_not_fresh(self):
        old = {"status": "success", "generated_at": (self.now - timedelta(minutes=35)).isoformat(), "failures": []}
        partial = {"status": "partial", "generated_at": self.now.isoformat(), "failures": []}
        self.assertFalse(is_snapshot_fresh(old, now=self.now))
        self.assertFalse(is_snapshot_fresh(partial, now=self.now))

    def test_matching_complete_cycle(self):
        payload = {"status": "success", "cycle_id": "20261013T121500Z", "candle_close_utc": "2026-10-13T12:15:00Z", "failures": []}
        self.assertTrue(is_complete_cycle([dict(payload) for _ in range(3)]))

    def test_failed_or_mixed_cycle_rejected(self):
        payload = {"status": "success", "cycle_id": "c-123", "candle_close_utc": "2026-10-13T12:15:00Z", "failures": []}
        good = [dict(payload) for _ in range(3)]
        mixed = [dict(x) for x in good]
        mixed[-1]["cycle_id"] = "c-124"
        self.assertFalse(is_complete_cycle(mixed))
        partial = [dict(x) for x in good]
        partial[0]["failures"] = [{"symbol": "BTCUSDT"}]
        self.assertFalse(is_complete_cycle(partial))
        missing = [dict(x) for x in good]
        del missing[0]["failures"]
        self.assertFalse(is_complete_cycle(missing))


if __name__ == "__main__":
    unittest.main(verbosity=2)
