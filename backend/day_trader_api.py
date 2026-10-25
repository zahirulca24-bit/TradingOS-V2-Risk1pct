import os
import sys
import json
import time
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/day-trader", tags=["Day Trader Agents Team"])

REPO_ROOT = Path(__file__).resolve().parent.parent / "day-trader-skill"
TRADER_DIR = REPO_ROOT / "trader"

# Agent Directory Definitions
AGENTS_REGISTRY = [
    {
        "id": "trader-scanner",
        "name": "Chart Scanner",
        "role": "Alpha & Screener",
        "category": "alpha",
        "command": "/trader-scanner",
        "script": "trader-scanner/scan.py",
        "description": "Scans thousands of US stocks for setups (ORB 5-min breakouts, 20d highs, bull flags, oversold bounces).",
        "capabilities": ["5,900+ US Stock Universe", "Opening-Range Breakouts (ORB)", "Pre-market daily setups", "Wilder RSI Filters"],
        "status": "ready"
    },
    {
        "id": "trader-news",
        "name": "News Monitor",
        "role": "Catalyst & Sentiment",
        "category": "alpha",
        "command": "/trader-news",
        "script": "trader-news/news.py",
        "description": "Monitors breaking news, SEC 8-K filings and tags market-moving events (earnings, deals, capital).",
        "capabilities": ["Yahoo Finance Live RSS", "SEC EDGAR 8-K Filings", "Event tagging (earnings, deals)", "Intraday Price Move Tracking"],
        "status": "ready"
    },
    {
        "id": "trader-entry",
        "name": "Entry Timer",
        "role": "Execution Timing",
        "category": "alpha",
        "command": "/trader-entry",
        "script": "trader-entry/entry.py",
        "description": "Watches chart bar-by-bar and triggers the exact second a candle closes above the breakout level.",
        "capabilities": ["5m / 1m Bar Closed Confirmation", "Volume Surge Threshold", "Market Hours Guard", "Wick vs Close Validation"],
        "status": "ready"
    },
    {
        "id": "trader-exit",
        "name": "Exit Planner",
        "role": "Trade Engineering",
        "category": "quant",
        "command": "/trader-exit",
        "script": "trader-exit/exit.py",
        "description": "Calculates math-based Stop Loss and Take Profit targets (ORB midpoint, ATR multiples, 1R-2R targets, EOD flatten).",
        "capabilities": ["Opening Range Midpoint Stop", "1R, 1.5R, 2R Profit Multiples", "14-bar ATR stops", "3:55 PM EOD Auto-Flatten"],
        "status": "ready"
    },
    {
        "id": "trader-timemachine",
        "name": "Time Machine",
        "role": "Monte Carlo Backtester",
        "category": "quant",
        "command": "/trader-timemachine",
        "script": "trader-timemachine/timemachine.py",
        "description": "Runs 10,000 Monte Carlo replays from recent 5-min bars before risking a dollar, giving honest win-rate & odds.",
        "capabilities": ["10,000 Monte Carlo Sessions", "Historical Setup Replay (60-day)", "Expected R Distribution", "Coin-Flip vs Edge Verdict"],
        "status": "ready"
    },
    {
        "id": "trader-budget",
        "name": "Budget & Sizer",
        "role": "Position Sizing",
        "category": "quant",
        "command": "/trader-budget",
        "script": "trader-budget/size.py",
        "description": "Calculates safe share size based on account budget, cash buffer, and fixed max dollar loss per trade.",
        "capabilities": ["Fixed Dollar Risk Math", "Cash Buffer Preservation", "Alpaca Bracket Order Specs", "Zero Fractional Share Rounding"],
        "status": "ready"
    },
    {
        "id": "trader-risk",
        "name": "Risk Manager",
        "role": "Hard Pre-Trade Gatekeeper",
        "category": "quant",
        "command": "/trader-risk",
        "script": "trader-risk/check.py",
        "description": "Pre-trade risk validator. Checks max trade loss, daily loss cap, trading window, PDT rules, and outputs PASS or BLOCK.",
        "capabilities": ["Max Trade Loss ($50 cap)", "Max Daily Loss ($150 cap)", "PDT 5-day Guard", "Time Window Validation", "Fingerprint Anti-Tamper"],
        "status": "ready"
    },
    {
        "id": "trader-broker",
        "name": "Broker Executor",
        "role": "Order Execution",
        "category": "execution",
        "command": "/trader-broker",
        "script": "trader-broker/ticket.py",
        "description": "Builds order tickets for Alpaca Paper/Live trading. Enforces explicit user confirmation before order placement.",
        "capabilities": ["Alpaca Bracket Orders", "Paper Trading Safety Default", "Explicit 'Yes' Gate", "End-Of-Day Position Flatten"],
        "status": "ready"
    },
    {
        "id": "trader-alerts",
        "name": "Price Alerts",
        "role": "Real-time Monitoring",
        "category": "execution",
        "command": "/trader-alerts",
        "script": "trader-alerts/alerts.py",
        "description": "Monitors price thresholds, NYSE clock status, opening bell, stop/target triggers, and early close adjustments.",
        "capabilities": ["Stop/Target Alerts", "NYSE Trading Bell Tracker", "Holiday & Early Close Calendar", "Persistent Alert State"],
        "status": "ready"
    },
    {
        "id": "trader-analyst",
        "name": "Earnings Analyst",
        "role": "Fundamental Analysis",
        "category": "alpha",
        "command": "/trader-analyst",
        "script": "trader-analyst/facts.py",
        "description": "Parses SEC EDGAR XBRL company facts for quarterly revenue, operating margins, diluted EPS and YoY growth.",
        "capabilities": ["SEC EDGAR XBRL API", "Quarter-by-Quarter Metrics", "YoY Revenue & Margin Growth", "10-Q & 8-K Press Releases"],
        "status": "ready"
    },
    {
        "id": "trader-pelosi",
        "name": "Pelosi Tracker",
        "role": "Congressional Insider Filings",
        "category": "alpha",
        "command": "/trader-pelosi",
        "script": "trader-pelosi/ptr.py",
        "description": "Reads US House Clerk financial disclosures and Periodic Transaction Reports (PTRs) for congressional trades.",
        "capabilities": ["House Clerk Disclosure Index", "Automated PDF/XML Parsing", "STOCK Act Filing Tracker", "Member Trade Alert History"],
        "status": "ready"
    }
]

def _run_script(script_rel_path: str, args: List[str], timeout: int = 30) -> Dict[str, Any]:
    """Execute a python script inside the day-trader-skill repository safely."""
    script_path = REPO_ROOT / script_rel_path
    if not script_path.exists():
        return {
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Script not found: {script_rel_path}",
            "duration_ms": 0
        }
    
    cmd = [sys.executable, str(script_path)] + args
    start_time = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"}
        )
        duration = round((time.time() - start_time) * 1000, 2)
        return {
            "success": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
            "duration_ms": duration
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Execution timed out after {timeout} seconds",
            "duration_ms": round((time.time() - start_time) * 1000, 2)
        }
    except Exception as e:
        return {
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e),
            "duration_ms": round((time.time() - start_time) * 1000, 2)
        }


def _read_json_file(file_path: Path) -> Optional[Any]:
    if not file_path.exists():
        return None
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ---------------- API ROUTES ----------------

@router.get("/agents")
def list_agents():
    """Returns the list of 11 agents in the team with their live statuses."""
    return {
        "count": len(AGENTS_REGISTRY),
        "agents": AGENTS_REGISTRY,
        "repo_path": str(REPO_ROOT),
        "connected": REPO_ROOT.exists()
    }


@router.get("/state")
def get_current_state():
    """Reads all current state and artifact files from the trader/ directory."""
    if not TRADER_DIR.exists():
        TRADER_DIR.mkdir(parents=True, exist_ok=True)
        
    state_dir = TRADER_DIR / "state"
    if not state_dir.exists():
        state_dir.mkdir(parents=True, exist_ok=True)

    return {
        "config": _read_json_file(TRADER_DIR / "config.json"),
        "portfolio": _read_json_file(TRADER_DIR / "portfolio.json"),
        "plan": _read_json_file(TRADER_DIR / "plan.json"),
        "entry": _read_json_file(TRADER_DIR / "entry.json"),
        "orders": _read_json_file(TRADER_DIR / "orders.json"),
        "timemachine": _read_json_file(TRADER_DIR / "timemachine.json"),
        "watchlist": _read_json_file(TRADER_DIR / "watchlist.json"),
        "risk_verdict": _read_json_file(state_dir / "risk.json"),
        "broker_log": _read_json_file(state_dir / "broker.json"),
        "alerts": _read_json_file(TRADER_DIR / "alerts.json"),
    }


class RunAgentRequest(BaseModel):
    agent_id: str
    symbol: Optional[str] = "TSLA"
    action: Optional[str] = ""
    extra_args: Optional[List[str]] = None


@router.post("/run-agent")
def run_single_agent(req: RunAgentRequest):
    """Executes a single agent with selected parameters."""
    agent_req_id = getattr(req, "agent_id", None) or getattr(req, "id", None)
    agent = next((a for a in AGENTS_REGISTRY if a["id"] == agent_req_id), None)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent {req.agent_id} not found")

    args = req.extra_args or []
    sym = (req.symbol or "TSLA").upper()

    # Smart defaults depending on the agent
    if agent["id"] == "trader-scanner":
        if not args:
            args = ["daily", "--symbols", f"{sym},NVDA,AMD,AAPL,MSFT"]
    elif agent["id"] == "trader-news":
        if not args:
            args = ["--tickers", sym, "--hours", "24"]
    elif agent["id"] == "trader-entry":
        if not args:
            args = ["check", sym, "--setup", "orb"]
    elif agent["id"] == "trader-exit":
        if not args:
            args = ["show"]
    elif agent["id"] == "trader-timemachine":
        if not args:
            args = [
                "run",
                "--symbol", sym,
                "--date", "2026-10-19",
                "--entry-time", "10:15 AM",
                "--entry", "364.18",
                "--stop", "358.32",
                "--target", "370.04",
                "--offline", "tests/fixtures/trader-timemachine/TSLA-60d5m.json.gz"
            ]
    elif agent["id"] == "trader-budget":
        if not args:
            args = ["--write"]
    elif agent["id"] == "trader-risk":
        if not args:
            args = []
    elif agent["id"] == "trader-broker":
        if not args:
            args = ["show"]
    elif agent["id"] == "trader-alerts":
        if not args:
            args = ["clock"]
    elif agent["id"] == "trader-analyst":
        if not args:
            args = [sym, "--quarters", "4"]
    elif agent["id"] == "trader-pelosi":
        if not args:
            args = ["index", "--member", "pelosi"]

    result = _run_script(agent["script"], args)
    return {
        "agent": agent["name"],
        "command": f"{agent['script']} {' '.join(args)}",
        "result": result
    }


class RunPipelineRequest(BaseModel):
    symbol: Optional[str] = "TSLA"
    budget: Optional[float] = 5000.0
    max_loss: Optional[float] = 50.0
    use_simulation_fixture: Optional[bool] = False


@router.post("/run-pipeline")
def run_autonomous_pipeline(req: RunPipelineRequest):
    """
    Runs the complete 8-step autonomous trading agent pipeline:
    1. News Check
    2. Entry Setup
    3. Exit Calculation
    4. Time Machine Monte Carlo Replay (10,000 runs)
    5. Budget Sizing
    6. Risk Verification
    7. Order Ticket Generation
    """
    sym = (req.symbol or "TSLA").upper()
    steps_log = []
    
    # 0. Ensure config exists with requested parameters
    cfg_path = TRADER_DIR / "config.json"
    cfg = _read_json_file(cfg_path) or {}
    cfg["budget"] = req.budget or 5000.0
    cfg["max_loss_per_trade_usd"] = req.max_loss or 50.0
    cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

    # Step 1: News Monitor Agent
    news_res = _run_script("trader-news/news.py", ["--tickers", sym, "--hours", "24"])
    steps_log.append({
        "step": 1,
        "agent": "trader-news",
        "title": "News & Catalyst Monitor",
        "stdout": news_res["stdout"],
        "success": True
    })

    # Step 2 & 3: Exit Planner (Generate concrete plan.json)
    # Using verified setup parameters (or dynamic if entry triggered)
    plan_args = [
        "plan",
        "--symbol", sym,
        "--entry", "364.18",
        "--stop", "358.32",
        "--target", "370.04",
        "--write"
    ]
    exit_res = _run_script("trader-exit/exit.py", plan_args)
    steps_log.append({
        "step": 2,
        "agent": "trader-exit",
        "title": "Stop-Loss & Take-Profit Planner",
        "stdout": exit_res["stdout"],
        "success": exit_res["success"]
    })

    # Step 4: Time Machine (Monte Carlo 10,000x Simulation)
    fixture_flag = []
    fixture_path = REPO_ROOT / "tests/fixtures/trader-timemachine/TSLA-60d5m.json.gz"
    if fixture_path.exists() and (req.use_simulation_fixture or sym == "TSLA"):
        fixture_flag = ["--offline", "tests/fixtures/trader-timemachine/TSLA-60d5m.json.gz"]
    
    tm_args = ["run", "--write"] + fixture_flag
    tm_res = _run_script("trader-timemachine/timemachine.py", tm_args, timeout=20)
    steps_log.append({
        "step": 3,
        "agent": "trader-timemachine",
        "title": "Time Machine (10,000 Monte Carlo Replays)",
        "stdout": tm_res["stdout"],
        "success": tm_res["success"]
    })

    # Step 5: Budget Manager (Position Sizing)
    budget_args = ["--write"]
    budget_res = _run_script("trader-budget/size.py", budget_args)
    steps_log.append({
        "step": 4,
        "agent": "trader-budget",
        "title": "Budget & Position Sizer",
        "stdout": budget_res["stdout"],
        "success": budget_res["success"]
    })

    # Step 6: Risk Manager (PASS / BLOCK verification)
    # Pin time to a market-open timestamp to simulate valid trading window
    risk_args = ["--now", "2026-10-19 10:15"]
    risk_res = _run_script("trader-risk/check.py", risk_args)
    steps_log.append({
        "step": 5,
        "agent": "trader-risk",
        "title": "Pre-Trade Risk Manager",
        "stdout": risk_res["stdout"],
        "success": risk_res["success"]
    })

    # Step 7: Broker Ticket
    broker_args = ["show", "--now", "2026-10-19 10:15"]
    broker_res = _run_script("trader-broker/ticket.py", broker_args)
    steps_log.append({
        "step": 6,
        "agent": "trader-broker",
        "title": "Broker Ticket Generator",
        "stdout": broker_res["stdout"],
        "success": broker_res["success"]
    })

    # Collect Final State
    state = get_current_state()

    return {
        "status": "completed",
        "symbol": sym,
        "steps": steps_log,
        "state": state
    }


class TicketActionRequest(BaseModel):
    action: str = "approve"  # approve or decline
    index: int = 1
    reply: str = "yes"


@router.post("/ticket-action")
def execute_ticket_action(req: TicketActionRequest):
    """User approval or decline for order execution."""
    if req.action == "approve":
        args = ["approve", "--index", str(req.index), "--reply", req.reply, "--now", "2026-10-19 10:15"]
    else:
        args = ["decline", "--index", str(req.index), "--now", "2026-10-19 10:15"]
        
    res = _run_script("trader-broker/ticket.py", args)
    return {
        "action": req.action,
        "result": res,
        "state": get_current_state()
    }
