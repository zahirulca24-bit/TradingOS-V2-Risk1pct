import sys
from pathlib import Path
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import main

client = TestClient(main.app)

def test_list_agents_endpoint():
    res = client.get("/api/day-trader/agents")
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 11
    assert len(data["agents"]) == 11
    agent_ids = [a["id"] for a in data["agents"]]
    assert "trader-scanner" in agent_ids
    assert "trader-timemachine" in agent_ids
    assert "trader-risk" in agent_ids
    assert "trader-broker" in agent_ids


def test_get_state_endpoint():
    res = client.get("/api/day-trader/state")
    assert res.status_code == 200
    data = res.json()
    assert "config" in data
    assert "portfolio" in data


def test_run_individual_agent_timemachine():
    res = client.post(
        "/api/day-trader/run-agent",
        json={
            "agent_id": "trader-timemachine",
            "symbol": "TSLA"
        }
    )
    assert res.status_code == 200
    data = res.json()
    assert data["agent"] == "Time Machine"
    assert "replays" in data["result"]["stdout"].lower() or data["result"]["success"]


def test_run_full_pipeline_endpoint():
    res = client.post(
        "/api/day-trader/run-pipeline",
        json={
            "symbol": "TSLA",
            "budget": 5000,
            "max_loss": 50,
            "use_simulation_fixture": True
        }
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "completed"
    assert len(data["steps"]) == 6
    step_agents = [s["agent"] for s in data["steps"]]
    assert "trader-news" in step_agents
    assert "trader-exit" in step_agents
    assert "trader-timemachine" in step_agents
    assert "trader-budget" in step_agents
    assert "trader-risk" in step_agents
    assert "trader-broker" in step_agents
