"""GET-only position-risk preview; deliberately excludes placement methods.
Strictly DRY_RUN_ONLY, trading_enabled=False.
"""
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException

from demo_dashboard_api import read_demo_account, get_demo_account
from demo_risk_engine import (
    size_dry_run,
    is_recent_cycle,
    is_recent_account,
    get_cached_instrument_specs,
    count_occupied_slots,
    MAX_OPEN_AND_PENDING
)
from pipeline_store import read_committed

router = APIRouter(prefix='/demo', tags=['Read-only Risk'])


@router.get('/risk-policy')
def risk_policy():
    """Return static and verifiable 1% risk policy parameters."""
    return {
        'environment': 'BYBIT_DEMO',
        'risk_percent': 1.0,
        'risk_fraction': 0.01,
        'max_open_and_pending': MAX_OPEN_AND_PENDING,
        'capacity_policy': 'Maximum 3 combined active position legs and distinct pending entry order commitments',
        'min_rr': 2.0,
        'mode': 'DRY_RUN_ONLY',
        'trading_enabled': False,
        'real_trading_enabled': False,
        'order_execution': 'ORDER_EXECUTION_DISABLED',
    }


@router.get('/risk-health')
def risk_health():
    """Return overall operational risk health across account and cycle data."""
    reasons = []
    account_ok = False
    cycle_ok = False

    account_data = None
    try:
        account_data = get_demo_account()
        now = datetime.now(timezone.utc)
        if (
            account_data.get("connected")
            and account_data.get("environment") == "BYBIT_DEMO"
            and account_data.get("total_equity") is not None
            and account_data.get("total_equity") > 0
        ):
            if is_recent_account(account_data.get("updated_at"), now=now):
                if account_data.get("occupied_slots") is not None:
                    account_ok = True
                else:
                    reasons.append("Account exposure capacity is unverified (position/order errors or ambiguous metadata)")
            else:
                reasons.append("Demo account data is stale (> 120s)")
        else:
            reasons.append("Demo account disconnected or equity invalid")
    except Exception as exc:
        reasons.append(f"Demo account check failed: {exc}")

    committed = None
    try:
        committed = read_committed()
        if committed and committed.get("manifest"):
            if is_recent_cycle(committed["manifest"], now=now):
                cycle_ok = True
            else:
                reasons.append("Committed scanner cycle is stale (> 15m)")
        else:
            reasons.append("No committed scanner cycle found")
    except Exception as exc:
        reasons.append(f"Committed cycle check failed: {exc}")

    if not account_ok or not committed:
        status = "UNAVAILABLE"
    elif reasons:
        status = "DEGRADED"
    else:
        status = "VALID"

    return {
        "status": status,
        "environment": "BYBIT_DEMO",
        "account_connected": account_ok,
        "cycle_fresh": cycle_ok,
        "total_equity": account_data.get("total_equity") if account_data else None,
        "risk_budget_usd": account_data.get("risk_budget_usd") if account_data else None,
        "occupied_slots": account_data.get("occupied_slots") if account_data else None,
        "remaining_slots": account_data.get("remaining_slots") if account_data else None,
        "mode": "DRY_RUN_ONLY",
        "trading_enabled": False,
        "real_trading_enabled": False,
        "order_execution": "DISABLED / NO ORDERS",
        "reasons": reasons,
    }


@router.get('/risk-preview')
def risk_preview():
    """Evaluate and return decimal-safe dry-run position sizing for all PLAN_READY candidates."""
    try:
        account = get_demo_account()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f'Demo account unavailable: {exc}') from exc

    try:
        committed = read_committed()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f'Committed cycle unavailable: {exc}') from exc

    if committed is None:
        raise HTTPException(status_code=503, detail='No committed scanner cycle')

    now = datetime.now(timezone.utc)
    cycle_fresh = is_recent_cycle(committed['manifest'], now=now)
    account_fresh = is_recent_account(account.get('updated_at'), now=now)

    # Check latest attempt from pipeline status
    latest_scan_failed = False
    try:
        from pipeline_store import read_pipeline_status
        pipe_status = read_pipeline_status()
        if pipe_status and pipe_status.get("last_attempt", {}).get("status") == "failed":
            latest_scan_failed = True
    except Exception:
        pass

    rows = committed.get('trade_plans', {}).get('results', [])
    ready_plans = [p for p in rows if p.get('status') == 'PLAN_READY']

    open_positions_count = None if account.get('positions_error') else account.get('open_positions')
    pending_entries_count = None if account.get('orders_error') or account.get('orders_first_page_only') else account.get('pending_entry_orders')

    candidates = []
    for plan in ready_plans:
        sym = plan.get('symbol')
        specs = get_cached_instrument_specs(sym) if sym else None

        preview = size_dry_run(
            plan,
            account.get('total_equity'),
            open_positions=open_positions_count,
            pending_orders=pending_entries_count,
            cycle_fresh=cycle_fresh,
            account_fresh=account_fresh,
            latest_scan_failed=latest_scan_failed,
            qty_step=specs.get('qty_step') if specs else None,
            min_qty=specs.get('min_qty') if specs else None,
            max_qty=specs.get('max_qty') if specs else None,
            min_notional=specs.get('min_notional') if specs else None,
            available_balance=account.get('available_balance'),
        )
        preview['cycle_id'] = committed['manifest']['cycle_id']
        preview['candle_close_utc'] = committed['manifest']['candle_close_utc']
        preview['equity_basis_usd'] = account.get('total_equity')
        preview['equity_updated_at'] = account.get('updated_at')
        candidates.append(preview)

    if candidates:
        try:
            from journal_db import record_dry_runs
            record_dry_runs(committed['manifest']['cycle_id'], candidates)
        except Exception:
            pass

    empty_message = None
    if not ready_plans:
        empty_message = "No confirmed trade plans in the latest committed cycle. No dry-run position preview available."


    return {
        'environment': 'BYBIT_DEMO',
        'mode': 'DRY_RUN_ONLY',
        'trading_enabled': False,
        'real_trading_enabled': False,
        'order_execution': 'ORDER_EXECUTION_DISABLED',
        'cycle_id': committed['manifest']['cycle_id'],
        'candle_close_utc': committed['manifest']['candle_close_utc'],
        'cycle_fresh': cycle_fresh,
        'account_fresh': account_fresh,
        'latest_scan_failed': latest_scan_failed,
        'risk_percent': 1.0,
        'equity_basis_usd': account.get('total_equity'),
        'risk_budget_usd': account.get('risk_budget_usd'),
        'occupied_slots': account.get('occupied_slots'),
        'remaining_slots': account.get('remaining_slots'),
        'ready_count': len(ready_plans),
        'candidates': candidates,
        'empty_message': empty_message,
        'execution_note': (
            'All candidates are dry-run research previews only. '
            'Order execution is strictly disabled. No orders are submitted or accepted.'
        ),
    }
