"""Whole loop through the HTTP API, offline (MOCK mode, temp DB, nothing is sent):
every real drop -> findings -> decision cards -> approve an email option -> Chaser mail (simulated)
-> supplier confirms -> reply bound to the request -> Planner recomputes. No value from the data is hardcoded."""
from fastapi.testclient import TestClient
from sqlmodel import select

from backend.core.models import OrderConfirmation
from backend.core.replay import list_drops
from backend.core.store import session


def _data(resp):
    assert resp.status_code == 200, resp.text[:300]
    return resp.json()["data"]


def test_whole_loop_through_the_api(monkeypatch):
    monkeypatch.setenv("CHASER_SEND", "false")
    from backend.app.main import app

    with TestClient(app) as c:
        assert _data(c.get("/api/health"))["mockMode"] is True

        # 1) every real drop arrives, one after another
        n = len(list_drops())
        for step in range(1, n + 1):
            res = _data(c.post("/api/replay/step"))
            assert res["step"] == step and res["of"] == n
        assert _data(c.post("/api/replay/step"))["done"] is True

        issues = _data(c.get("/api/issues"))
        assert {i["raised_by"] for i in issues} >= {"ingest", "reconciler", "risk", "planner"}
        assert _data(c.get("/api/forecast")) and _data(c.get("/api/risk"))
        cards = _data(c.get("/api/decisions?status=proposed"))
        assert cards, "the Planner proposes at least one decision"

        # 2) a human approves an option that asks a supplier (with the approvers the card names)
        pick = next(((d, i, o, a) for d in cards for i, o in enumerate(d["options"]) if o.get("available", True)
                     for a in o.get("actions", []) if a.get("type") == "email"), None)
        assert pick, "some card offers 'ask the supplier'"
        card, idx, option, action = pick
        approvers = option.get("approvers") or ["Demo"]
        _data(c.post(f"/api/decisions/{card['id']}/approve",
                     json={"by": approvers[0], "approvals": approvers[1:], "option": idx}))

        # 3) the Chaser wrote the email for that decision (simulated in MOCK mode)
        mail = next(m for m in _data(c.get("/api/outbox")) if m.get("decision_id") == card["id"])
        assert mail["supplier_id"] == action["supplier_id"] and mail["to_email"]

        # 4) the supplier confirms exactly what was asked -> bound to the request, mail marked replied
        with session() as s:
            oc = s.exec(select(OrderConfirmation).where(
                OrderConfirmation.supplier_id == action["supplier_id"],
                OrderConfirmation.po_number == action["po_number"], OrderConfirmation.po_line == action["po_line"],
                OrderConfirmation.material_id == action["part"])).one()
        delivery = {"po_number": action["po_number"], "po_line": action["po_line"],
                    "shipment_id": action["shipment_id"], "revision": oc.revision or "A",
                    "quantity": int(oc.quantity or 1),
                    "arrival": "2026-11-22T09:00:00+01:00"}
        _data(c.post("/api/inbox", json={"supplier_id": action["supplier_id"], "issue_id": mail["issue_id"],
                                         "outbox_id": mail["id"], "body": "We confirm.", "delivery": delivery}))
        reply = _data(c.get("/api/inbox"))[0]
        assert reply["processed"] and "confirmed_delivery" in reply["result"], reply["result"]
        assert next(m for m in _data(c.get("/api/outbox")) if m["id"] == mail["id"])["status"] == "replied"

        # 5) nothing failed anywhere, and every agent took part
        trace = _data(c.get("/api/trace?limit=5000"))
        assert [e for e in trace if e["action"] == "error"] == []
        assert {e["agent"] for e in trace} >= {"ingest", "reconciler", "risk", "planner", "chaser", "human"}
