import unittest
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from demo_risk_engine import (
    size_dry_run,
    is_recent_cycle,
    is_recent_account,
    count_occupied_slots,
    classify_order,
    is_entry_order,
    RISK_FRACTION,
    MAX_OPEN_AND_PENDING,
    EXECUTION_WARNING,
)

PLAN_LONG = {
    'symbol': 'BTCUSDT',
    'status': 'PLAN_READY',
    'direction': 'LONG',
    'entry_reference': 100.0,
    'sl': 95.0,
    'tp': 112.0,
    'rr': 2.4,
}

PLAN_SHORT = {
    'symbol': 'ETHUSDT',
    'status': 'PLAN_READY',
    'direction': 'SHORT',
    'entry_reference': 100.0,
    'sl': 105.0,
    'tp': 88.0,
    'rr': 2.4,
}

BASE_ARGS = dict(
    open_positions=0,
    pending_orders=0,
    cycle_fresh=True,
    account_fresh=True,
    latest_scan_failed=False,
)

SPECS = dict(
    qty_step='0.01',
    min_qty='0.01',
    max_qty='1000',
    min_notional='5',
    available_balance='2000',
    fee_rate='0.0006',
    slippage_rate='0.0005',
)


class ComprehensiveRiskSafetyAuditTests(unittest.TestCase):

    # =========================================================================
    # AUDIT A: MAXIMUM 3 OPEN + PENDING EXPOSURE SLOTS (12 MANDATORY CASES)
    # =========================================================================

    # Mandatory Case 1: 0 positions + 0 pending entries = 0 occupied slots.
    def test_audit_a_case_01_zero_positions_zero_entries(self):
        slots = count_occupied_slots([], [])
        self.assertEqual(slots, 0)
        res = size_dry_run(PLAN_LONG, 1000.0, open_positions=0, pending_orders=0, cycle_fresh=True, **SPECS)
        self.assertEqual(res['existing_slots'], 0)
        self.assertEqual(res['remaining_slots'], 3)
        self.assertEqual(res['status'], 'DRY_RUN_VALID')

    # Mandatory Case 2: 1 position + 1 unrelated pending entry = 2 slots.
    def test_audit_a_case_02_one_pos_one_unrelated_entry(self):
        positions = [{'symbol': 'BTCUSDT', 'size': '0.5'}]
        pending = [{'orderId': 'ord_eth_1', 'symbol': 'ETHUSDT', 'reduceOnly': False}]
        slots = count_occupied_slots(positions, pending)
        self.assertEqual(slots, 2)
        res = size_dry_run(PLAN_LONG, 1000.0, open_positions=positions, pending_orders=pending, cycle_fresh=True, **SPECS)
        self.assertEqual(res['existing_slots'], 2)
        self.assertEqual(res['remaining_slots'], 1)
        self.assertEqual(res['status'], 'DRY_RUN_VALID')

    # Mandatory Case 3: 1 BTC position + 2 separate BTC entry orders = 3 occupied slots.
    def test_audit_a_case_03_one_btc_pos_two_btc_entry_orders(self):
        positions = [{'symbol': 'BTCUSDT', 'size': '0.5'}]
        pending = [
            {'orderId': 'btc_entry_1', 'symbol': 'BTCUSDT', 'reduceOnly': False},
            {'orderId': 'btc_entry_2', 'symbol': 'BTCUSDT', 'reduceOnly': False},
        ]
        slots = count_occupied_slots(positions, pending)
        self.assertEqual(slots, 3)
        # With 3 occupied slots, a new candidate must be blocked for capacity!
        res = size_dry_run(PLAN_LONG, 1000.0, open_positions=positions, pending_orders=pending, cycle_fresh=True, **SPECS)
        self.assertEqual(res['status'], 'BLOCKED_CAPACITY')
        self.assertIn('MAX_OPEN_AND_PENDING_REACHED', res['reasons'])
        self.assertEqual(res['existing_slots'], 3)
        self.assertEqual(res['remaining_slots'], 0)

    # Mandatory Case 4: 3 different pending entry orders on the same symbol = 3 occupied slots.
    def test_audit_a_case_04_three_pending_entries_same_symbol(self):
        pending = [
            {'orderId': 'sol_1', 'symbol': 'SOLUSDT', 'reduceOnly': False},
            {'orderId': 'sol_2', 'symbol': 'SOLUSDT', 'reduceOnly': False},
            {'orderId': 'sol_3', 'symbol': 'SOLUSDT', 'reduceOnly': False},
        ]
        slots = count_occupied_slots([], pending)
        self.assertEqual(slots, 3)

    # Mandatory Case 5: 1 BTC position + 1 BTC entry order = 2 slots unless proven otherwise.
    def test_audit_a_case_05_one_btc_pos_one_btc_entry(self):
        positions = [{'symbol': 'BTCUSDT', 'size': '0.5'}]
        pending = [{'orderId': 'btc_add_1', 'symbol': 'BTCUSDT', 'reduceOnly': False}]
        slots = count_occupied_slots(positions, pending)
        self.assertEqual(slots, 2)
        res = size_dry_run(PLAN_LONG, 1000.0, open_positions=positions, pending_orders=pending, cycle_fresh=True, **SPECS)
        self.assertEqual(res['existing_slots'], 2)
        self.assertEqual(res['remaining_slots'], 1)

    # Mandatory Case 6: 2 hedge-mode legs on BTC + 1 pending entry = 3 slots.
    def test_audit_a_case_06_hedge_mode_two_legs_one_entry(self):
        # Bybit hedge mode: positionIdx=1 (Buy/Long leg), positionIdx=2 (Sell/Short leg)
        positions = [
            {'symbol': 'BTCUSDT', 'size': '0.5', 'positionIdx': 1},
            {'symbol': 'BTCUSDT', 'size': '0.3', 'positionIdx': 2},
        ]
        pending = [{'orderId': 'ord_sol_1', 'symbol': 'SOLUSDT', 'reduceOnly': False}]
        slots = count_occupied_slots(positions, pending)
        self.assertEqual(slots, 3)

    # Mandatory Case 7: Same order returned twice with same immutable order ID = counted once.
    def test_audit_a_case_07_duplicate_api_record_same_order_id(self):
        pending = [
            {'orderId': 'dup_order_123', 'symbol': 'BTCUSDT', 'reduceOnly': False},
            {'orderId': 'dup_order_123', 'symbol': 'BTCUSDT', 'reduceOnly': False},
        ]
        slots = count_occupied_slots([], pending)
        self.assertEqual(slots, 1)

    # Mandatory Case 8: Distinct orders with different IDs but identical symbol and price = counted separately.
    def test_audit_a_case_08_distinct_orders_same_symbol_and_price(self):
        pending = [
            {'orderId': 'order_A', 'symbol': 'BTCUSDT', 'price': '60000', 'reduceOnly': False},
            {'orderId': 'order_B', 'symbol': 'BTCUSDT', 'price': '60000', 'reduceOnly': False},
        ]
        slots = count_occupied_slots([], pending)
        self.assertEqual(slots, 2)

    # Mandatory Case 9: Exit-only reduce-only orders = not counted as new entries.
    def test_audit_a_case_09_exit_only_orders_excluded(self):
        pending = [
            {'orderId': 'exit_reduce', 'symbol': 'BTCUSDT', 'reduceOnly': True},
            {'orderId': 'exit_close_trigger', 'symbol': 'ETHUSDT', 'closeOnTrigger': True},
            {'orderId': 'exit_tp', 'symbol': 'SOLUSDT', 'stopOrderType': 'TakeProfit'},
            {'orderId': 'exit_sl', 'symbol': 'SOLUSDT', 'stopOrderType': 'StopLoss'},
            {'orderId': 'real_entry', 'symbol': 'AVAXUSDT', 'reduceOnly': False},
        ]
        slots = count_occupied_slots([], pending)
        self.assertEqual(slots, 1)  # Only AVAXUSDT real_entry is counted!

    # Mandatory Case 10: Ambiguous orders missing critical metadata = BLOCKED_DATA.
    def test_audit_a_case_10_ambiguous_orders_block_data(self):
        ambiguous_orders = [
            {'orderId': 'ambig_1', 'symbol': 'BTCUSDT', 'reduceOnly': 'corrupted_flag'},
            {'orderId': 'ambig_2', 'symbol': None},
            'not_a_dict_order',
        ]
        for bad_ord in ambiguous_orders:
            slots = count_occupied_slots([], [bad_ord])
            self.assertIsNone(slots, f"Ambiguous order {bad_ord!r} did not return None")
            res = size_dry_run(PLAN_LONG, 1000.0, open_positions=[], pending_orders=[bad_ord], cycle_fresh=True, **SPECS)
            self.assertEqual(res['status'], 'BLOCKED_DATA')
            self.assertIn('POSITION_OR_ORDER_COUNT_UNKNOWN', res['reasons'])

    # Mandatory Case 11: Three occupied slots + a new proposed candidate = BLOCKED_CAPACITY.
    def test_audit_a_case_11_three_slots_blocks_capacity(self):
        res = size_dry_run(PLAN_LONG, 1000.0, open_positions=2, pending_orders=1, cycle_fresh=True, **SPECS)
        self.assertEqual(res['status'], 'BLOCKED_CAPACITY')
        self.assertIn('MAX_OPEN_AND_PENDING_REACHED', res['reasons'])
        self.assertEqual(res['remaining_slots'], 0)

    # Mandatory Case 12: Incomplete position or order pagination = BLOCKED_DATA.
    def test_audit_a_case_12_incomplete_pagination_blocks_data(self):
        # If open_positions or pending_orders is None (pagination could not be verified)
        res_pos_none = size_dry_run(PLAN_LONG, 1000.0, open_positions=None, pending_orders=0, cycle_fresh=True, **SPECS)
        self.assertEqual(res_pos_none['status'], 'BLOCKED_DATA')
        self.assertIn('POSITION_OR_ORDER_COUNT_UNKNOWN', res_pos_none['reasons'])

        res_ord_none = size_dry_run(PLAN_LONG, 1000.0, open_positions=0, pending_orders=None, cycle_fresh=True, **SPECS)
        self.assertEqual(res_ord_none['status'], 'BLOCKED_DATA')
        self.assertIn('POSITION_OR_ORDER_COUNT_UNKNOWN', res_ord_none['reasons'])

    # =========================================================================
    # AUDIT B: CORRECT EQUITY & 1% RISK BUDGET
    # =========================================================================

    def test_audit_b_exact_one_percent_equity(self):
        equity = 889.7553
        res = size_dry_run(PLAN_LONG, equity, **BASE_ARGS, **SPECS)
        expected_budget = float(Decimal('889.7553') * RISK_FRACTION)
        self.assertAlmostEqual(res['risk_budget_usd'], expected_budget, places=6)
        self.assertLessEqual(res['total_conservative_projected_risk_usd'], expected_budget)

    def test_audit_b_zero_negative_missing_equity_blocks(self):
        for bad_eq in [0, -100, None, float('nan'), float('inf')]:
            res = size_dry_run(PLAN_LONG, bad_eq, **BASE_ARGS, **SPECS)
            self.assertEqual(res['status'], 'BLOCKED_DATA')
            self.assertIn('INVALID_PLAN_OR_EQUITY', res['reasons'])

    def test_audit_b_insufficient_available_balance_blocks_capacity(self):
        specs_low_balance = {**SPECS, 'available_balance': '50.0'}
        res = size_dry_run(PLAN_LONG, 1000.0, **BASE_ARGS, **specs_low_balance)
        self.assertEqual(res['status'], 'BLOCKED_CAPACITY')
        self.assertIn('INSUFFICIENT_UNLEVERAGED_AVAILABLE_BALANCE', res['reasons'])

    # =========================================================================
    # AUDIT C: FEES, SLIPPAGE & MATHEMATICAL RISK MODEL
    # =========================================================================

    def test_audit_c_long_stop_loss_math_and_fees(self):
        # Entry=100, SL=95. price_loss = 5. cost = 195 * (0.0006 + 0.0005) = 195 * 0.0011 = 0.2145
        # unit_loss = 5.2145. budget = $10.0 -> raw = 1.9177 -> Qty = 1.91 (step 0.01)
        res = size_dry_run(PLAN_LONG, 1000.0, **BASE_ARGS, **SPECS)
        self.assertEqual(res['status'], 'DRY_RUN_VALID')
        self.assertEqual(res['proposed_quantity'], '1.91')
        expected_sl = 1.91 * 5.0  # 9.55
        expected_fees_slip = 1.91 * 195.0 * 0.0011  # 0.409695
        self.assertAlmostEqual(res['estimated_stop_loss_amount_usd'], expected_sl, places=4)
        self.assertAlmostEqual(res['estimated_fees_and_slippage_usd'], expected_fees_slip, places=4)
        self.assertAlmostEqual(res['total_conservative_projected_risk_usd'], expected_sl + expected_fees_slip, places=4)
        self.assertLessEqual(res['total_conservative_projected_risk_usd'], 10.0)

    def test_audit_c_short_stop_loss_math_and_fees(self):
        # Entry=100, SL=105, TP=88. price_loss = 5. cost = 205 * 0.0011 = 0.2255
        # unit_loss = 5.2255. budget = $10.0 -> raw = 1.9137 -> Qty = 1.91 (step 0.01)
        res = size_dry_run(PLAN_SHORT, 1000.0, **BASE_ARGS, **SPECS)
        self.assertEqual(res['status'], 'DRY_RUN_VALID')
        self.assertEqual(res['proposed_quantity'], '1.91')
        expected_sl = 1.91 * 5.0
        expected_fees_slip = 1.91 * 205.0 * 0.0011
        self.assertAlmostEqual(res['estimated_stop_loss_amount_usd'], expected_sl, places=4)
        self.assertAlmostEqual(res['estimated_fees_and_slippage_usd'], expected_fees_slip, places=4)
        self.assertAlmostEqual(res['total_conservative_projected_risk_usd'], expected_sl + expected_fees_slip, places=4)
        self.assertLessEqual(res['total_conservative_projected_risk_usd'], 10.0)

    def test_audit_c_downward_step_rounding_never_exceeds_budget(self):
        specs_coarse_step = {**SPECS, 'qty_step': '1.0'}
        res = size_dry_run(PLAN_LONG, 1000.0, **BASE_ARGS, **specs_coarse_step)
        self.assertEqual(res['status'], 'DRY_RUN_VALID')
        self.assertEqual(Decimal(res['proposed_quantity']), Decimal('1'))
        self.assertLessEqual(res['total_conservative_projected_risk_usd'], 10.0)

    def test_audit_c_never_round_upward_to_meet_min_qty(self):
        specs_high_min = {**SPECS, 'min_qty': '10.0'}
        res = size_dry_run(PLAN_LONG, 1000.0, **BASE_ARGS, **specs_high_min)
        self.assertEqual(res['status'], 'BLOCKED_RISK')
        self.assertIn('QTY_OUTSIDE_EXCHANGE_LIMITS', res['reasons'])

    def test_audit_c_missing_or_invalid_fees_slippage_blocks(self):
        # Zero fee/slippage or negative fee/slippage must block with INVALID_EXCHANGE_SPECS_OR_COSTS
        bad_specs_1 = {**SPECS, 'fee_rate': '0'}
        res_1 = size_dry_run(PLAN_LONG, 1000.0, **BASE_ARGS, **bad_specs_1)
        self.assertEqual(res_1['status'], 'BLOCKED_DATA')
        self.assertIn('INVALID_EXCHANGE_SPECS_OR_COSTS', res_1['reasons'])

        bad_specs_2 = {**SPECS, 'slippage_rate': '-0.001'}
        res_2 = size_dry_run(PLAN_LONG, 1000.0, **BASE_ARGS, **bad_specs_2)
        self.assertEqual(res_2['status'], 'BLOCKED_DATA')
        self.assertIn('INVALID_EXCHANGE_SPECS_OR_COSTS', res_2['reasons'])

    def test_audit_c_execution_warning_disclaimer_present(self):
        res = size_dry_run(PLAN_LONG, 1000.0, **BASE_ARGS, **SPECS)
        self.assertIn('execution_warning', res)
        self.assertIn('Actual realized loss may exceed modeled loss', res['execution_warning'])

    def test_audit_c_edge_case_micro_price_token(self):
        micro_plan = {
            'symbol': 'PEPEUSDT',
            'status': 'PLAN_READY',
            'direction': 'LONG',
            'entry_reference': 0.00001234,
            'sl': 0.00001150,
            'tp': 0.00001450,
            'rr': 2.57,
        }
        micro_specs = {
            'qty_step': '100',
            'min_qty': '100',
            'max_qty': '10000000000',
            'min_notional': '5',
            'available_balance': '5000',
            'fee_rate': '0.0006',
            'slippage_rate': '0.0005',
        }
        res = size_dry_run(micro_plan, 1000.0, **BASE_ARGS, **micro_specs)
        self.assertEqual(res['status'], 'DRY_RUN_VALID')
        self.assertLessEqual(res['total_conservative_projected_risk_usd'], 10.0)

    def test_audit_c_edge_case_macro_price_token(self):
        macro_plan = {
            'symbol': 'BTCUSDT',
            'status': 'PLAN_READY',
            'direction': 'SHORT',
            'entry_reference': 95000.0,
            'sl': 96500.0,
            'tp': 91000.0,
            'rr': 2.67,
        }
        macro_specs = {
            'qty_step': '0.001',
            'min_qty': '0.001',
            'max_qty': '1000',
            'min_notional': '5',
            'available_balance': '50000',
            'fee_rate': '0.0006',
            'slippage_rate': '0.0005',
        }
        res = size_dry_run(macro_plan, 50000.0, **BASE_ARGS, **macro_specs)
        self.assertEqual(res['status'], 'DRY_RUN_VALID')
        # Budget = 50000 * 0.01 = 500
        self.assertEqual(res['risk_budget_usd'], 500.0)
        self.assertLessEqual(res['total_conservative_projected_risk_usd'], 500.0)

    # =========================================================================
    # AUDIT D: STRICT READ-ONLY BOUNDARY (ZERO EXECUTION METHODS)
    # =========================================================================

    def test_audit_d_no_order_placement_methods_exist(self):
        from pathlib import Path
        for fname in ['demo_risk_api.py', 'demo_risk_engine.py', 'demo_dashboard_api.py']:
            code = (Path(__file__).parent / fname).read_text(encoding='utf-8')
            for forbidden in [
                'place_order(',
                'amend_order(',
                'cancel_order(',
                'cancel_all_orders(',
                'set_trading_stop(',
                'set_leverage(',
                'switch_position_mode(',
                'real_trading_enabled = True',
                'trading_enabled = True',
            ]:
                self.assertNotIn(forbidden, code, f"Forbidden execution method found: '{forbidden}' in {fname}")

    def test_audit_d_mode_is_dry_run_only(self):
        from demo_risk_api import risk_policy
        pol = risk_policy()
        self.assertEqual(pol['environment'], 'BYBIT_DEMO')
        self.assertEqual(pol['mode'], 'DRY_RUN_ONLY')
        self.assertFalse(pol['trading_enabled'])
        self.assertFalse(pol['real_trading_enabled'])
        self.assertEqual(pol['order_execution'], 'ORDER_EXECUTION_DISABLED')


if __name__ == '__main__':
    unittest.main()
