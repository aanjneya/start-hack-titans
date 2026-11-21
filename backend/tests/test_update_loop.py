"""The updatable source of truth + one-writer reply loop (team decision Sat 19:30).

missing field -> Reconciler issue -> Chaser asks -> reply -> Ingest fills field (logged) -> truth.updated
-> Reconciler re-checks and resolves. Uses small test agents on their own Coordinator.
"""
from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import select

from backend import system
from backend.agents.base import Agent
from backend.app.main import app
from backend.core import events
from backend.core.coordinator import Coordinator, Event
from backend.core.models import Conflict, FieldChange, InboxMessage, Issue, IssueKind, OrderConfirmation
from backend.core.store import add_all, find_rows, session, update_row


def _confirmation(**kw) -> OrderConfirmation:
    return add_all([OrderConfirmation(po_number="4500318745", po_line=1, supplier_id="SUP-0028",
                                      quantity=15000, source="suppliers/x/OA1.pdf", **kw)])[0]


def test_update_row_fills_logs_coerces_and_records_conflicts():
    oc = _confirmation()
    r1 = update_row(OrderConfirmation, oc.id, {"revision": "C", "delivery_date": "2026-11-22"},
                    source="inbox/1", agent="ingest", reason="supplier reply")
    assert {c.field for c in r1.changes} == {"revision", "delivery_date"}
    assert all(c.change_type == "fill" for c in r1.changes)
    row = find_rows(OrderConfirmation, po_number="4500318745")[0]
    assert row.revision == "C" and row.delivery_date == date(2026, 11, 22) and row.updated_at is not None

    same = update_row(OrderConfirmation, oc.id, {"revision": "C"}, source="inbox/2", agent="ingest")
    assert not same.changes and not same.conflicts  # same value -> nothing, so no loop

    clash = update_row(OrderConfirmation, oc.id, {"revision": "B"}, source="inbox/3", agent="ingest")
    assert not clash.changes and len(clash.conflicts) == 1  # default: keep value, record conflict
    assert find_rows(OrderConfirmation, id=oc.id)[0].revision == "C"

    fix = update_row(OrderConfirmation, oc.id, {"revision": "B"}, source="inbox/4", agent="ingest",
                     on_conflict="overwrite", reason="buyer confirmed")
    assert fix.changes[0].change_type == "overwrite" and fix.changes[0].old_value == "C"
    with session() as s:
        assert len(s.exec(select(FieldChange)).all()) == 3
        assert len(s.exec(select(Conflict)).all()) == 1


def test_full_reply_loop_fills_missing_field_and_resolves_issue():
    bus = Coordinator()
    asked = []

    class Reconciler(Agent):
        name = "reconciler"
        subscriptions = {events.TRUTH_UPDATED: "check"}

        def check(self, event: Event) -> None:
            for oc in find_rows(OrderConfirmation):
                key = f"{oc.po_number}/{oc.po_line}"
                if not oc.revision:
                    self.raise_issue(type="missing_revision", kind=IssueKind.MISSING_INFO, key=key,
                                     summary=f"No revision on confirmation {key}", supplier_id=oc.supplier_id,
                                     po_number=oc.po_number)
                else:
                    with session() as s:
                        for issue in s.exec(select(Issue).where(Issue.type == "missing_revision")).all():
                            self.resolve_issue(issue.id, f"revision {oc.revision} received")

    class Chaser(Agent):
        name = "chaser"
        subscriptions = {events.ISSUE_RAISED: "ask"}

        def ask(self, event: Event) -> None:
            if event.payload["kind"] != IssueKind.MISSING_INFO:
                return
            asked.append(event.payload["issue_id"])
            # simulated supplier answer, stored by the Chaser, applied by Ingest
            msg = system.store_reply(supplier_id="SUP-0028", body="Tak, rewizja C.",
                                     issue_id=event.payload["issue_id"], source=self.name)
            self.emit(events.REPLY_RECEIVED, {"inbox_id": msg.id, "supplier_id": "SUP-0028",
                                              "issue_id": event.payload["issue_id"]})

    class Ingest(Agent):
        name = "ingest"
        subscriptions = {events.REPLY_RECEIVED: "apply"}

        def apply(self, event: Event) -> None:
            with session() as s:
                msg = s.get(InboxMessage, event.payload["inbox_id"])
                issue = s.get(Issue, msg.issue_id)
            oc = find_rows(OrderConfirmation, po_number=issue.po_number)[0]
            result = self.update_row(OrderConfirmation, oc.id, {"revision": "C"}, source=f"inbox/{msg.id}",
                                     issue_id=issue.id, reason="supplier reply")
            self.emit_truth_updated([result], issue_ids=[issue.id])

    Reconciler(bus), Chaser(bus), Ingest(bus)
    _confirmation()  # revision missing
    bus.emit(events.TRUTH_UPDATED, {"files": ["suppliers/x/OA1.pdf"]}, source="test")
    steps = bus.run_until_idle()

    assert asked and steps < 20  # asked exactly once, no endless loop
    assert find_rows(OrderConfirmation)[0].revision == "C"
    with session() as s:
        issue = s.exec(select(Issue)).one()
        change = s.exec(select(FieldChange)).one()
    assert issue.status == "resolved"
    assert change.field == "revision" and change.agent == "ingest" and change.issue_id == issue.id


def test_api_inbox_and_changes():
    client = TestClient(app)
    posted = client.post("/api/inbox", json={"supplier_id": "SUP-0028", "body": "ETA moved to Sunday"}).json()
    assert posted["ok"] and posted["data"]["inbox_id"]
    assert client.get("/api/inbox").json()["data"][0]["body"] == "ETA moved to Sunday"
    assert client.get("/api/changes").json()["ok"]
