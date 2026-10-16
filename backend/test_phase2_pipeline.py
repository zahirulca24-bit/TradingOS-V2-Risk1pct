"""Phase 2 offline integrity tests. Never call Bybit private APIs or place orders."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import crypto_universe
import pipeline_store
from pipeline_contract import is_complete_cycle
from scanner_pipeline import build_reports, cycle_identity, evaluate_symbol, sort_opportunities, validated_pair, run_once
from scanner_scheduler import next_scan_time


def make_candles(seconds, number=100, end_close=7200):
    # Ensure the last candle closes at end_close.
    last_start = end_close - seconds
    return [{"time": last_start - (number - 1 - i) * seconds,
             "open": 100., "high": 105., "low": 95., "close": 101., "volume": 3.}
            for i in range(number)]


class TestCryptoUniverse(unittest.TestCase):
    def test_crypto_allowed_stock_and_metals_rejected(self):
        template = {"status": "Trading", "contractType": "LinearPerpetual",
                    "quoteCoin": "USDT", "settleCoin": "USDT"}
        self.assertTrue(crypto_universe.eligible_instrument({**template, "baseCoin": "BTC", "symbol": "BTCUSDT"}))
        for base in ("NVDA", "SOXL", "XAU", "KLAC", "SNDK", "SKHYNIX", "OTHERUNKNOWN"):
            self.assertFalse(crypto_universe.eligible_instrument({**template, "baseCoin": base, "symbol": base + "USDT"}))

    def test_wrong_contract_type_and_quotes_rejected(self):
        template = {"baseCoin": "BTC", "symbol": "BTCUSDT", "status": "Trading", "settleCoin": "USDT", "quoteCoin": "USDT", "contractType": "LinearPerpetual"}
        self.assertFalse(crypto_universe.eligible_instrument({**template, "contractType": "LinearFutures"}))
        self.assertFalse(crypto_universe.eligible_instrument({**template, "status": "Settled"}))
        self.assertFalse(crypto_universe.eligible_instrument({**template, "quoteCoin": "USDC"}))

    def test_tickers_sorted_and_filtered(self):
        template = {"status": "Trading", "contractType": "LinearPerpetual", "quoteCoin": "USDT", "settleCoin": "USDT"}
        instruments = [{**template, "symbol": a + "USDT", "baseCoin": a} for a in ("BTC", "ETH", "XAU")]
        tickers = [{"symbol": "ETHUSDT", "turnover24h": "300"}, {"symbol": "XAUUSDT", "turnover24h": "999999"}, {"symbol": "BTCUSDT", "turnover24h": "500"}]
        def fake(endpoint, _params):
            return {"list": instruments if "instruments" in endpoint else tickers}
        with patch("crypto_universe.get_bybit_data", side_effect=fake):
            self.assertEqual([x["symbol"] for x in crypto_universe.get_crypto_tickers()], ["BTCUSDT", "ETHUSDT"])


class TestCandleAndCycle(unittest.TestCase):
    def test_wait_after_close(self):
        with self.assertRaises(RuntimeError):
            cycle_identity(3600 + 19)
        boundary, ident, iso = cycle_identity(3600 + 20)
        self.assertEqual((boundary, ident), (3600, "19700101T010000Z"))
        self.assertEqual(iso, "1970-01-01T01:00:00+00:00")

    def test_scheduler_next_boundary(self):
        self.assertEqual(next_scan_time(3600 + 10).timestamp(), 3620)
        self.assertEqual(next_scan_time(3600 + 20).timestamp(), 4520)

    def test_last_closed_candle_exactly_matches_cycle(self):
        candles15 = make_candles(900, end_close=7200)
        candles60 = make_candles(3600, end_close=7200)
        with patch("scanner_pipeline.fetch_closed_candles", side_effect=[candles60, candles15]):
            _, last15 = validated_pair("BTCUSDT", 7200)
        self.assertEqual(last15[-1]["time"], 6300)
        with patch("scanner_pipeline.fetch_closed_candles", side_effect=[candles60, make_candles(900, end_close=6300)]):
            with self.assertRaisesRegex(RuntimeError, "15m"):
                validated_pair("BTCUSDT", 7200)
        with patch("scanner_pipeline.fetch_closed_candles", side_effect=[make_candles(3600, end_close=3600), candles15]):
            with self.assertRaisesRegex(RuntimeError, "1H"):
                validated_pair("BTCUSDT", 7200)

    def test_no_neutral_rows(self):
        hourly, fifteen = make_candles(3600), make_candles(900)
        with patch("scanner_pipeline.detect_trend", return_value={"trend": "NEUTRAL"}):
            self.assertIsNone(evaluate_symbol("BTCUSDT", hourly, fifteen))

    def test_scanner_and_trade_plan_data_reused_and_aligned(self):
        hourly, fifteen = make_candles(3600), make_candles(900)
        with patch("scanner_pipeline.detect_trend", return_value={"trend": "BULLISH"}), patch(
            "scanner_pipeline.detect_liquidity_sweep", return_value=None
        ), patch("scanner_pipeline.build_trade_plan", return_value={"trend": "BULLISH", "status": "WAIT", "reason": "NO_SWEEP_ON_LAST_CLOSED_CANDLE"}), patch(
            "scanner_pipeline.select_zones", return_value=(None, None)
        ), patch("scanner_pipeline.classify_opportunity", return_value={"direction": "LONG", "priority": 4, "status": "NO_VALID_ZONE", "distance_pct": None, "reason": "No zone"}):
            reports = build_reports([("BTCUSDT", hourly, fifteen)], boundary=7200, cycle_id="19700101T020000Z", generated_at="1970-01-01T02:00:20+00:00", examined=3, neutral=2)
        self.assertTrue(is_complete_cycle(list(reports.values())))
        pipeline_store.validate_reports(reports)
        self.assertEqual(reports["scanner"]["total_scanned"], 1)
        self.assertEqual(reports["scanner"]["universe_examined"], 3)
        self.assertEqual(reports["trade_plans"]["ready_count"], 0)
        self.assertEqual(reports["opportunities"]["top_count"], 1)
        self.assertEqual(reports["opportunities"]["top_10"][0]["trade_plan_reason"], "NO_SWEEP_ON_LAST_CLOSED_CANDLE")

    def test_full_cycle_fetch_once_per_timeframe_and_publish(self):
        hourly, fifteen = make_candles(3600, end_close=7200), make_candles(900, end_close=7200)
        fake_plan = {"trend": "BULLISH", "status": "WAIT", "reason": "NO_SWEEP_ON_LAST_CLOSED_CANDLE"}
        fake_rank = {"direction": "LONG", "priority": 4, "status": "NO_VALID_ZONE", "distance_pct": None, "reason": "No zone"}
        with patch("scanner_pipeline.read_committed", return_value=None), patch(
            "scanner_pipeline.get_crypto_tickers", return_value=[{"symbol": "BTCUSDT"}]
        ), patch("scanner_pipeline.fetch_closed_candles", side_effect=[hourly, fifteen]) as fetch, patch(
            "scanner_pipeline.detect_trend", return_value={"trend": "BULLISH"}
        ), patch("scanner_pipeline.detect_liquidity_sweep", return_value=None), patch(
            "scanner_pipeline.build_trade_plan", return_value=fake_plan
        ), patch("scanner_pipeline.select_zones", return_value=(None, None)), patch(
            "scanner_pipeline.classify_opportunity", return_value=fake_rank
        ), patch("scanner_pipeline.publish", return_value="19700101T020000Z") as published, patch(
            "scanner_pipeline.time.time", return_value=7220
        ):
            self.assertEqual(run_once(now=7220, quiet=True), "success")
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(published.call_count, 1)
        reports = published.call_args.args[0]
        pipeline_store.validate_reports(reports)
        self.assertEqual(reports["scanner"]["total_scanned"], 1)

    def test_fetch_failure_does_not_publish(self):
        with patch("scanner_pipeline.read_committed", return_value=None), patch(
            "scanner_pipeline.get_crypto_tickers", return_value=[{"symbol": "BTCUSDT"}]
        ), patch("scanner_pipeline.fetch_closed_candles", side_effect=TimeoutError("market unavailable")), patch(
            "scanner_pipeline.publish"
        ) as published, patch("scanner_pipeline.time.time", return_value=7220):
            with self.assertRaisesRegex(RuntimeError, "cycle not committed"):
                run_once(now=7220, quiet=True)
        published.assert_not_called()

    def test_rank_ready_sort_never_enforces_signal(self):
        rows = [
            {"symbol": "BTCUSDT", "priority": 2, "distance_pct": 0.1},
            {"symbol": "ETHUSDT", "priority": 1, "distance_pct": 0.0},
        ]
        self.assertEqual(sort_opportunities(rows)[0]["symbol"], "ETHUSDT")


class TestPublication(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patch_base = patch.object(pipeline_store, "BASE", self.root)
        self.patch_state = patch.object(pipeline_store, "STATE_DIR", self.root / "pipeline_state")
        self.patch_manifest = patch.object(pipeline_store, "MANIFEST", self.root / "pipeline_state" / "current_cycle.json")
        self.patch_engine = patch("db_adapter.get_active_engine", return_value="sqlite")
        self.patch_load = patch("db_adapter.load_committed_pipeline_cycle", return_value=None)
        self.patch_save = patch("db_adapter.save_pipeline_cycle", return_value=None)
        for p in (self.patch_base, self.patch_state, self.patch_manifest, self.patch_engine, self.patch_load, self.patch_save):
            p.start()
        self.addCleanup(self.tmp.cleanup)
        for p in (self.patch_base, self.patch_state, self.patch_manifest, self.patch_engine, self.patch_load, self.patch_save):
            self.addCleanup(p.stop)

    def reports(self):
        ident = "19700101T020000Z"
        common = {"status": "success", "cycle_id": ident, "candle_close_utc": "1970-01-01T02:00:00+00:00", "generated_at": "1970-01-01T02:00:20+00:00", "failures": [], "trading_enabled": False}
        return {
            "scanner": {**common, "results": [{"symbol": "BTCUSDT", "trend": "BULLISH"}]},
            "trade_plans": {**common, "results": [{"symbol": "BTCUSDT", "status": "WAIT"}]},
            "opportunities": {**common, "top_10": [{"symbol": "BTCUSDT"}], "remaining": []},
        }

    def test_publish_and_read_committed(self):
        with self.assertRaisesRegex(ValueError, "reports"):
            pipeline_store.publish({"scanner": {}})
        self.assertFalse(pipeline_store.MANIFEST.exists())
        reports = self.reports()
        pipeline_store.publish(reports)
        committed = pipeline_store.read_committed()
        self.assertEqual(committed["scanner"]["cycle_id"], reports["scanner"]["cycle_id"])
        self.assertTrue((self.root / "scanner_snapshot.json").is_file())

    def test_mixed_cycles_refused(self):
        reports = self.reports()
        reports["trade_plans"]["cycle_id"] = "19700101T021500Z"
        with self.assertRaises(ValueError):
            pipeline_store.publish(reports)
        self.assertFalse(pipeline_store.MANIFEST.exists())

    def test_partial_candidate_never_publish(self):
        reports = self.reports()
        reports["scanner"]["failures"] = [{"symbol": "BTCUSDT"}]
        with self.assertRaises(ValueError):
            pipeline_store.publish(reports)
        self.assertFalse(pipeline_store.MANIFEST.exists())

    def test_tampered_committed_rejected(self):
        reports = self.reports()
        pipeline_store.publish(reports)
        path = self.root / "pipeline_state" / "cycles" / "19700101T020000Z" / "scanner_snapshot.json"
        content = json.loads(path.read_text())
        content["cycle_id"] = "19700101T021500Z"
        path.write_text(json.dumps(content))
        with self.assertRaises(ValueError):
            pipeline_store.read_committed()


if __name__ == "__main__":
    unittest.main(verbosity=2)
