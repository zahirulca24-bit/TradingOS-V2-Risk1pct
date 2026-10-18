"""TradingOS V2 Order Execution API.
Endpoints for interactive 1-click execution and real-time risk previews from the Trading Terminal.
"""

from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from demo_order_executor import place_single_demo_order
from demo_dashboard_api import read_demo_account
from demo_risk_engine import get_cached_instrument_specs


router = APIRouter(prefix="/api/execution", tags=["Execution"])


class OrderRequest(BaseModel):
    symbol: str
    direction: str
    entry_reference: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None
    rr: Optional[float] = None
    source: Optional[str] = "MANUAL_TERMINAL"


@router.post("/place-order")
def place_order_endpoint(req: OrderRequest):
    """Place a 1% risk market order on Bybit Demo with SL/TP attached."""
    try:
        res = place_single_demo_order(
            symbol=req.symbol,
            direction=req.direction,
            entry_reference=req.entry_reference,
            sl=req.sl,
            tp=req.tp,
            rr=req.rr,
            source=req.source or "MANUAL_TERMINAL",
        )
        return res
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/preview")
def preview_order_sizing(symbol: str, direction: str = "LONG", entry: Optional[float] = None, sl: Optional[float] = None, tp: Optional[float] = None):
    """Calculates live 1% position size and dollar risk for the terminal widget."""
    try:
        symbol = symbol.strip().upper()
        direction = direction.strip().upper()

        account = read_demo_account()
        total_equity = account.get("total_equity") or 0.0
        occupied_slots = account.get("occupied_slots") or 0
        remaining_slots = account.get("remaining_slots") or 0

        risk_budget_usd = round(total_equity * 0.01, 2)

        if not entry or entry <= 0:
            from universe_candles import fetch_klines
            klines = fetch_klines(symbol, "15", limit=1)
            if klines:
                entry = float(klines[-1]["close"])
            else:
                entry = 100.0

        if not sl or sl <= 0:
            sl = entry * 0.988 if direction == "LONG" else entry * 1.012

        if not tp or tp <= 0:
            tp = entry * 1.036 if direction == "LONG" else entry * 0.964

        price_loss = abs(entry - sl)
        cost_loss = (entry + sl) * 0.0011
        unit_loss = price_loss + cost_loss
        raw_qty = risk_budget_usd / unit_loss if unit_loss > 0 else 0

        specs = get_cached_instrument_specs(symbol)
        qty_step = float(specs.get("qty_step", "0.01")) if specs else 0.01
        step_factor = 1.0 / qty_step if qty_step > 0 else 100.0
        rounded_qty = math.floor(raw_qty * step_factor) / step_factor

        rr = round(abs(tp - entry) / abs(entry - sl), 2) if abs(entry - sl) > 0 else 2.0
        potential_profit_usd = round(risk_budget_usd * rr, 2)

        return {
            "status": "success",
            "symbol": symbol,
            "direction": direction,
            "equity_usd": total_equity,
            "risk_budget_usd": risk_budget_usd,
            "entry_reference": entry,
            "sl": sl,
            "tp": tp,
            "rr": rr,
            "calculated_qty": rounded_qty,
            "potential_profit_usd": potential_profit_usd,
            "occupied_slots": occupied_slots,
            "remaining_slots": remaining_slots,
            "can_execute": remaining_slots > 0 and occupied_slots < 3,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
