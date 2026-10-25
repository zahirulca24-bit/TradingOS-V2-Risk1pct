# -*- coding: utf-8 -*-
"""
Test T‑05 – verify that a Bybit‑accepted order is *not* recorded as FILLED
when no fill confirmation is available.

All external side‑effects (Bybit HTTP, DB writes, Telegram, account/spec look‑ups)
are fully mocked. No real network calls are performed.
"""

import pytest
from unittest import mock

# Functions under test
from backend.demo_order_executor import (
    execute_demo_plans,
    place_single_demo_order,
)

# ---------------------------------------------------------------------------
# In‑memory store that mimics the DB write function
# ---------------------------------------------------------------------------
_saved_executions = []


def _mock_save_demo_execution(record):
    """Replace ``save_demo_execution`` – just keep the record in memory."""
    _saved_executions.append(record)

# ---------------------------------------------------------------------------
# Mock Bybit HTTP session – always returns a successful ``retCode == 0``
# but **does not** indicate a filled order.
# ---------------------------------------------------------------------------
class _MockBybitSession:
    def place_order(self, **_kwargs):
        return {"retCode": 0, "result": {"orderId": "MOCK123"}}


def _mock_http(*_args, **_kwargs):
    return _MockBybitSession()

# ---------------------------------------------------------------------------
# Mock data used by both execution paths
# ---------------------------------------------------------------------------
MOCK_ACCOUNT = {
    "total_equity": 10_000.0,
    "available_balance": 9_500.0,
    "occupied_slots": 0,
    "remaining_slots": 3,
}
MOCK_SPECS = {
    "qty_step": "0.01",
    "min_qty": "0.01",
    "max_qty": "1000",
    "tick_size": "0.01",
    "min_notional": "5",
}
MOCK_PLAN = {
    "status": "PLAN_READY",
    "symbol": "BTCUSDT",
    "direction": "LONG",
    "entry_reference": 20_000.0,
    "sl": 19_800.0,
    "tp": 20_400.0,
    "rr": 1.5,
}
MOCK_REPORTS = {
    "trade_plans": {"results": [MOCK_PLAN]},
    "scanner": {"cycle_id": "20261020T120000Z"},
}

# ---------------------------------------------------------------------------
# Test 1 – automated executor (execute_demo_plans)
# ---------------------------------------------------------------------------
def test_execute_demo_plans_does_not_record_filled(monkeypatch):
    # Patch all side‑effects
    monkeypatch.setattr(
        "backend.demo_order_executor.save_demo_execution",
        _mock_save_demo_execution,
        raising=False,
    )
    monkeypatch.setattr(
        "backend.demo_order_executor.read_demo_account",
        lambda: MOCK_ACCOUNT,
        raising=False,
    )
    monkeypatch.setattr(
        "backend.demo_order_executor.get_cached_instrument_specs",
        lambda _sym: MOCK_SPECS,
        raising=False,
    )
    monkeypatch.setattr(
        "backend.demo_order_executor.send_telegram_message",
        lambda *_a, **_k: None,
        raising=False,
    )
    monkeypatch.setattr(
        "backend.demo_order_executor.HTTP",
        _mock_http,
        raising=False,
    )

    _saved_executions.clear()

    # Run the executor
    executions = execute_demo_plans(MOCK_REPORTS)

    # Exactly one execution record should have been persisted
    assert len(_saved_executions) == 1, "expected a single saved execution"
    rec = _saved_executions[0]

    # **Requirement T‑05**: status must NOT be "FILLED" when only retCode == 0
    assert rec["status"] != "FILLED", (
        "T‑05 failure – executor marks order as FILLED without fill confirmation"
    )
    assert rec["bybit_order_id"] == "MOCK123"

    # The function also returns the list of executions; it should match the record
    assert executions == [rec]

# ---------------------------------------------------------------------------
# Test 2 – manual terminal helper (place_single_demo_order)
# ---------------------------------------------------------------------------
def test_place_single_demo_order_does_not_record_filled(monkeypatch):
    # Patch side‑effects similarly to the first test
    monkeypatch.setattr(
        "backend.demo_order_executor.save_demo_execution",
        _mock_save_demo_execution,
        raising=False,
    )
    monkeypatch.setattr(
        "backend.demo_order_executor.read_demo_account",
        lambda: MOCK_ACCOUNT,
        raising=False,
    )
    monkeypatch.setattr(
        "backend.demo_order_executor.get_cached_instrument_specs",
        lambda _sym: MOCK_SPECS,
        raising=False,
    )
    monkeypatch.setattr(
        "backend.demo_order_executor.HTTP",
        _mock_http,
        raising=False,
    )

    _saved_executions.clear()

    # Call the manual order helper – only required args are symbol and direction
    resp = place_single_demo_order(symbol="BTCUSDT", direction="LONG")

    # The raw Bybit response should still contain retCode == 0
    assert resp["retCode"] == 0
    assert resp["result"]["orderId"] == "MOCK123"

    # One execution record must be saved
    assert len(_saved_executions) == 1, "expected a single saved execution"
    rec = _saved_executions[0]

    # Verify T‑05 – status must NOT be "FILLED"
    assert rec["status"] != "FILLED", (
        "T‑05 failure – manual helper records FILLED on retCode 0"
    )
    assert rec["bybit_order_id"] == "MOCK123"

# ---------------------------------------------------------------------------
# Allow direct invocation via ``python test_t05_order_status.py``
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    pytest.main([__file__])
