"""FINDING FORMAT v1: one shape for every hand-off to the Planner / the human (.brain/DATA_FORMAT.md)."""
import pytest
from fastapi.testclient import TestClient

from backend.agents.base import Agent
from backend.app.main import app
from backend.core.coordinator import Coordinator
from backend.core.models import Evidence, IssueKind, Severity


class Probe(Agent):
    name = "reconciler"


def test_full_finding_is_stored_and_served():
    bus = Coordinator()
    agent = Probe(bus)
    issue = agent.raise_issue(
        type="rev_blocked", kind=IssueKind.COPIES_AT_RISK, severity=Severity.HIGH, key="CMP-GAM-004/B",
        summary="Rev B sleeves can't be packed: 40,000 Standard copies short",
        reason="ECN-2026-118 makes rev C mandatory from today; stock and SHP-2026-04313 are rev B.",
        part_id="CMP-GAM-004", supplier_id="SUP-0028", product_id="FG-GH6-STD",
        impact={"product_id": "FG-GH6-STD", "copies_at_risk": 40000, "hours_buffer": 6.5,
                "due_by": "2026-11-21T22:00:00+01:00"},
        suggested_actions=[{"type": "email", "label": "Ask Karton Nowak which revision SHP-2026-04313 is",
                            "target": "SUP-0028", "owner": "chaser"},
                           {"type": "expedite", "label": "Order the rush rev C run (offer of 21.11.)",
                            "target": "SUP-0028", "owner": "planner", "copies_gained": 40000}],
        evidence=[Evidence(path="soony/master/bom_delta_2026-11-21.csv", locator="ECN-2026-118")],
        confidence=0.9)
    assert issue.subject_type == "part" and issue.subject_id == "CMP-GAM-004"  # inferred
    assert issue.impact["copies_at_risk"] == 40000 and issue.suggested_actions[1]["copies_gained"] == 40000
    event = bus._queue[-1]
    assert event.name == "issue.raised" and event.payload["_meta"]["from"] == "reconciler"
    data = TestClient(app).get("/api/issues").json()["data"][0]
    for key in ("summary", "reason", "impact", "suggested_actions", "confidence", "subject_type", "schema_version"):
        assert key in data


@pytest.mark.parametrize("bad", [
    {"severity": 9},
    {"kind": "whatever"},
    {"summary": "x" * 200},
    {"confidence": 1.5},
    {"suggested_actions": [{"type": "teleport", "label": "nope"}]},
])
def test_invalid_findings_are_rejected(bad):
    args = dict(type="t", kind=IssueKind.HUMAN_CHECK, severity=2, key="k", summary="ok")
    args.update(bad)
    with pytest.raises(ValueError):
        Probe(Coordinator()).raise_issue(**args)
