"""Tests for the shared backend (T0.1-T0.8). Run from repo root: backend/.venv/bin/pytest backend/tests -q"""
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlmodel import select

from backend import system
from backend.agents.base import Agent
from backend.app.main import app
from backend.core import events, llm, trace
from backend.core.coordinator import Coordinator
from backend.core.models import AgentLog, DecisionCard, Evidence, Issue, IssueKind, StockLot
from backend.core.replay import list_drops, materialize
from backend.core.store import add_all, all_rows, latest_by, query, session


def test_tables_and_part_journey_view_exist():
    assert query("SELECT count(*) AS n FROM part_journey")[0]["n"] == 0
    assert query("SELECT count(*) AS n FROM agent_log")[0]["n"] >= 0


def test_never_overwrite_and_latest_by():
    add_all([StockLot(material_id="CMP-X", batch="1", quantity=10, source="a.csv")])
    add_all([StockLot(material_id="CMP-X", batch="1", quantity=7, source="b.csv")])
    rows = all_rows(StockLot)
    assert len(rows) == 2  # history kept
    current = latest_by(rows, "material_id", "batch")
    assert len(current) == 1 and current[0].quantity == 7


def test_coordinator_routes_and_survives_errors():
    bus = Coordinator()
    seen = []
    bus.subscribe("a", "first", lambda e: (seen.append("first"), bus.emit("b", {}, "first")))
    bus.subscribe("a", "broken", lambda e: 1 / 0)
    bus.subscribe("b", "second", lambda e: seen.append("second"))
    bus.emit("a")
    assert bus.run_until_idle() == 3
    assert seen == ["first", "second"]
    errors = [e for e in trace.recent(agent="broken") if e.action == "error"]
    assert errors and "ZeroDivisionError" in errors[0].detail


def test_raise_issue_dedupes_and_reopens():
    class Probe(Agent):
        name = "probe"

    agent = Probe(Coordinator())
    first = agent.raise_issue(type="t", kind=IssueKind.HUMAN_CHECK, summary="x", key="k",
                              evidence=[Evidence(path="soony/erp/stock_snapshot.csv", locator="row 3")])
    agent.resolve_issue(first.id, "fixed")
    again = agent.raise_issue(type="t", kind=IssueKind.HUMAN_CHECK, summary="x again", key="k")
    assert again.id == first.id and again.status == "reopened"
    with session() as s:
        assert len(s.exec(select(Issue)).all()) == 1


def test_llm_mock_mode_uses_registered_mocks():
    class Out(BaseModel):
        qty: int

    assert llm.complete("hi", agent="t", task="unknown-task") is None
    llm.register_mock("extract-qty", lambda **kw: {"qty": 42})
    assert llm.extract("text", Out, agent="t", task="extract-qty").qty == 42
    assert any(e.action == "llm_call" for e in trace.recent(agent="t"))


def test_full_loop_runs_and_logs_every_agent():
    result = system.run_full(label="test")
    assert result["added"] > 100  # the real portal mirror in the repo
    agents_seen = {e.agent for e in trace.recent(limit=1000, run_id=result["run_id"])}
    for name in ["coordinator", "ingest", "reconciler", "risk", "planner"]:
        assert name in agents_seen


def test_api_health_run_and_decision_flow():
    client = TestClient(app)
    body = client.get("/api/health").json()
    assert body["ok"] and body["data"]["mockMode"] is True and "planner" in body["data"]["agents"]

    run = client.post("/api/run", json={"added": ["soony/erp/open_shipments.csv"], "label": "one file"}).json()
    assert run["ok"] and run["data"]["added"] == 1

    with session() as s:
        card = DecisionCard(title="test", options=[{"label": "A"}, {"label": "B"}], recommended=1)
        s.add(card)
        s.commit()
        s.refresh(card)
    approved = client.post(f"/api/decisions/{card.id}/approve", json={"by": "maren"}).json()
    assert approved["data"]["decision"]["status"] == "approved"
    assert approved["data"]["decision"]["chosen_option"] == 1
    assert client.post(f"/api/decisions/{card.id}/approve", json={}).status_code == 409
    assert client.get("/api/trace").json()["data"]


def test_replay_finds_real_drops_in_git_history(tmp_path):
    drops = list_drops()
    assert drops, "expected the 15-min data log commits in git history"
    first = drops[0]
    assert len(first.added) > 100  # the baseline
    root = materialize(first, tmp_path / "snap")
    assert (root / "soony").exists() and (root / "suppliers").exists()
