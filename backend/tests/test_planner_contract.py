from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.core.models import DecisionCard, InboxMessage, Issue, OrderConfirmation
from backend.core.store import add_all, all_rows


def test_only_one_approval_can_claim_current_chosen_option():
    client = TestClient(app)
    opts = [{"label": "wait"}, {"label": "act", "available": True, "approvers": ["Buyer", "Site lead"]}]
    card, stale = add_all([DecisionCard(options=opts, recommended=1),
                           DecisionCard(options=opts, recommended=1, status="outdated")])
    url = f"/api/decisions/{card.id}/approve"
    assert client.post(f"/api/decisions/{stale.id}/approve", json={"by": "Buyer"}).status_code == 409
    for invalid in (-1, 2):
        assert client.post(url, json={"option": invalid}).status_code == 422
    assert client.post(url, json={"by": "Buyer"}).status_code == 403
    def approve(_):
        return client.post(url, json={"by": "Site lead", "approvals": ["Buyer"]}).status_code
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(approve, range(2))) == [200, 409]


def test_ambiguous_reply_is_saved_for_review_without_changing_orders():
    issue = add_all([Issue(type="missing_revision", fingerprint="ambiguous", part_id="part", supplier_id="supplier")])[0]
    add_all([OrderConfirmation(material_id="part", po_number=str(i), supplier_id="supplier") for i in range(2)])
    result = TestClient(app).post("/api/inbox", json={"issue_id": issue.id, "supplier_id": "supplier",
                                                     "body": "Printed to rev C."})
    assert result.status_code == 200
    assert all_rows(InboxMessage)[0].result["review"]
    assert all(not row.revision for row in all_rows(OrderConfirmation))

def test_quoted_history_or_negative_answer_cannot_confirm_delivery():
    from backend.agents.planner_delivery import _text_confirmation
    line = "PO 4500318775/2, SHP-2026-04313, 20,000 units, rev C, arrival 2026-11-21T19:00+01:00"
    shipments = {"SHP-2026-04313"}
    assert _text_confirmation("We cannot confirm " + line, shipments) is None
    assert _text_confirmation("Can you confirm " + line + "?", shipments) is None
    assert _text_confirmation("Thanks.\n> Confirmed " + line, shipments) is None
    assert _text_confirmation("Confirmed " + line + "\n> Quote requested", shipments).quantity == 20000


def test_named_approval_routes_wait_for_an_effective_assignment():
    from datetime import datetime, timezone
    from backend.agents.planner_people import approval_assignments
    from backend.core.models import Document
    on = datetime(2026, 11, 21, tzinfo=timezone.utc).date()
    chart = Document(kind="org_chart", path="roster", text="""Procurement 2026-10-01
Elena D
Buyer (Chemicals)
Aki I
Buyer (Motion)
TBD
Category Lead Motion
Mika K
Head of Procurement""")
    rule = Document(kind="policy", path="company-policy", text="Effective 2025-10-01. Exceptions must be approved by the Head of Procurement.")
    a, = approval_assignments(["Buyer"], "", on, [chart])
    assert a["person"] is None
    a, = approval_assignments(["Buyer"], "", on, [chart], buyer="Aki I")
    assert a["person"] == "Aki I" and a["org_source"] == "roster"
    a, = approval_assignments(["Buyer"], "Motion", on, [chart], buyer="No longer on roster")
    assert a["person"] is None
    a, = approval_assignments(["Category lead"], "Motion", on, [chart, rule])
    assert (a["required_role"], a["signing_role"], a["person"], a["policy_source"]) == (
        "Category lead", "Head of Procurement", "Mika K", "company-policy")
    assert approval_assignments(["Category lead"], "Other", on, [chart, rule])[0]["person"] is None
    replacement = Document(kind="policy", path="amendment",
                           text="Effective 2027-01-01. Exceptions require board review.")
    assert approval_assignments(["Category lead"], "Motion", on, [chart, rule, replacement])[0]["person"] == "Mika K"
    assert approval_assignments(["Category lead"], "Motion", on.replace(year=2027),
                                [chart, rule, replacement])[0]["person"] is None

    client = TestClient(app)
    option = dict(label="Accept", available=True, approvers=["Buyer"], assignments=[
        dict(required_role="Buyer", signing_role="Buyer", person=None)])
    card, = add_all([DecisionCard(options=[option], recommended=0)])
    assert client.post(f"/api/decisions/{card.id}/approve", json={"by": "Buyer"}).status_code == 409


def test_recording_coapprovals_requires_the_designated_signers():
    client = TestClient(app)
    card, = add_all([DecisionCard(options=[dict(label="Accept", available=True, approvers=["CPO", "CFO"])], recommended=0)])
    url = f"/api/decisions/{card.id}/approve"
    assert client.post(url, json={"by": "Buyer", "approvals": ["CPO", "CFO"]}).status_code == 403
    assert client.post(url, json={"by": "CPO", "approvals": ["CFO", "Other"]}).status_code == 403
    assert client.post(url, json={"by": "CPO", "approvals": ["CFO"]}).status_code == 200


def test_po_line_basis_counts_each_verified_line_once_and_requires_one_currency():
    from backend.agents.planner_options import amended_po_basis
    from backend.core.models import PurchaseOrder
    line = PurchaseOrder(supplier_id="V", po_number="P", po_line=2, material_id="ASM", quantity=10,
                         unit_price=5.25, currency="EUR")
    same = line.model_copy(update={"source":"second export"})
    second = line.model_copy(update={"po_line":3, "unit_price":1})
    assert amended_po_basis([line, same, second], 4.25)["total_eur"] == 66.75
    assert amended_po_basis([line, same.model_copy(update={"unit_price":6})], 4.25) is None
    assert amended_po_basis([line, second.model_copy(update={"currency":"JPY"})], 4.25) is None
    assert amended_po_basis([line, second.model_copy(update={"po_line":None})], 4.25) is None


def test_selected_supplier_order_line_decides_the_buyer():
    from datetime import date
    from backend.agents.planner_options import order_buyer
    from backend.agents.planner_people import approval_assignments
    from backend.core.models import Document, PurchaseOrder

    orders = [PurchaseOrder(po_number="P", material_id="pump", supplier_id=vendor, po_line=line, buyer=buyer)
              for vendor, line, buyer in [("A", 1, "Rin"), ("A", 2, "Kim"), ("B", 2, "Ari")]]
    assert order_buyer(orders, "pump", "A", "P", 2) == "Kim"
    assert order_buyer(orders, "pump", "B", "P", 2) == "Ari"
    buyer = order_buyer(orders, "pump", "A", "P")
    chart = Document(kind="org_chart", path="roster", text="2026-01-01\nRin\nBuyer (Hydraulics)")
    route, = approval_assignments(["Buyer"], "Hydraulics", date(2026, 5, 1), [chart], buyer)
    assert route["person"] is None and "different buyers" in route["basis"]


def test_second_customer_order_thread_requires_dispatched_request_and_site_arrival():
    from backend.core.models import OutboxMessage, PurchaseOrder, Shipment, Supplier
    from backend.core.store import session

    client = TestClient(app)
    issue, = add_all([Issue(type="delivery_check", kind="human_check", fingerprint="rin-valve",
                            supplier_id="VN-R", part_id="PUMP.A", po_number="KIT:26-4")])
    request = dict(type="email", purpose="confirm_supply", supplier_id="VN-R", po_number="KIT:26-4", po_line=7,
                   shipment_id="ROAD-OS.92", part="PUMP.A", ask="Confirm revision, quantity and site arrival in Osaka.")
    add_all([Supplier(supplier_id="VN-R", name="Rin Hydraulics", contact_email="rin@example.test"),
             PurchaseOrder(po_number="KIT:26-4", po_line=7, supplier_id="VN-R", material_id="PUMP.A",
                           currency="JPY", buyer="Aya Mori", quantity=35),
             OrderConfirmation(po_number="KIT:26-4", po_line=7, supplier_id="VN-R", material_id="PUMP.A",
                               revision="R2-B", quantity=35),
             Shipment(shipment_id="ROAD-OS.92", supplier_id="VN-R", dest_site="OS"),
             Shipment(shipment_id="ROAD-OS.93", supplier_id="VN-R", dest_site="OS")])
    decision, = add_all([DecisionCard(created_by="planner", issue_ids=[issue.id], recommended=0,
                          options=[dict(label="Confirm the inbound valve", approvers=["Aya Mori"], actions=[request])])])
    assert client.post(f"/api/decisions/{decision.id}/approve", json={"by": "Aya Mori"}).status_code == 200
    mail, = [m for m in all_rows(OutboxMessage) if m.decision_id == decision.id]
    assert mail.status == "sent" and mail.supplier_id == "VN-R"
    message = "Confirmed PO KIT:26-4/7, shipment ROAD-OS.92, 30 units, revision r2-b, ETA 2026-12-03T10:00+09:00."
    def deliver(body, supplier="VN-R"):
        response = client.post("/api/inbox", json=dict(outbox_id=mail.id, issue_id=issue.id, supplier_id=supplier, body=body))
        response.raise_for_status()
        with session() as db:
            return db.get(InboxMessage, response.json()["data"]["inbox_id"]).result

    for invalid in [message.replace("ETA", "dispatch time"), message.replace("+09:00", ""),
                    message.replace("30 units", "36 units"), message + " Also shipment ROAD-OS.93.",
                    message.replace("KIT:26-4/7", "KIT:26-4/6"), message.replace("r2-b", "r3")]:
        assert "review" in deliver(invalid)
    assert "review" in deliver(message, supplier="VN-S")
    with session() as db:
        db.get(OutboxMessage, mail.id).status = "draft"
        db.commit()
    assert "dispatched" in deliver(message)["review"]
    with session() as db:
        db.get(OutboxMessage, mail.id).status = "sent"
        db.commit()
    fact = deliver(message)["confirmed_delivery"]
    assert (fact["revision"], fact["shipment_id"], fact["arrival"], fact["quantity"]) == (
        "R2-B", "ROAD-OS.92", "2026-12-03T10:00:00+09:00", 30)
    assert deliver(message)["review"].startswith("Duplicate")


def test_handler_failure_is_visible_on_approval_and_inbox_with_one_dispatch(monkeypatch):
    from backend import system
    from backend.core import events
    from backend.core.models import OutboxMessage, Supplier

    client = TestClient(app)
    issue, = add_all([Issue(fingerprint="await-delivery", type="delivery_check", supplier_id="V")])
    add_all([Supplier(supplier_id="V", name="Vendor", contact_email="vendor@example.test")])
    action = dict(type="email", purpose="confirm_supply", supplier_id="V", po_number="OR-7",
                  po_line=4, shipment_id="TRIP-22", part="valve", ask="Confirm the order and delivery time.")
    card, = add_all([DecisionCard(created_by="planner", recommended=0, issue_ids=[issue.id],
                       options=[dict(label="Verify inbound", actions=[action], approvers=["Aya Mori"])])])

    def fail(event):
        raise RuntimeError("isolated unavailable handler")
    bus = system.bus()
    chaser = next(s for s in bus._subs[events.EMAIL_REQUESTED] if s.agent == "chaser")
    approval = f"/api/decisions/{card.id}/approve"
    with monkeypatch.context() as down:
        down.setattr(chaser, "handler", fail)
        run = client.post(approval, json={"by": "Aya Mori"}).json()["data"]
    assert run["status"] == "partial" and run["decision"]["status"] == "approved"
    error, = run["failures"]
    assert (error["agent"], error["event"]) == ("chaser", events.EMAIL_REQUESTED)
    assert not all_rows(OutboxMessage)
    assert client.post(approval, json={"by": "Aya Mori"}).status_code == 409
    system._bus = None  # the committed approval can be recovered once on restart
    system.bus().run_until_idle()
    mail, = all_rows(OutboxMessage)
    records = client.get(f"/api/decisions/{card.id}/history").json()["data"]["events"]
    assert any(e.get("trace_id") == error["trace_id"] and e["title"] == "Agent step needs review" for e in records)

    ingest = next(s for s in system.bus()._subs[events.REPLY_RECEIVED] if s.agent == "ingest")
    with monkeypatch.context() as down:
        down.setattr(ingest, "handler", fail)
        result = client.post("/api/inbox", json=dict(outbox_id=mail.id, issue_id=issue.id,
                             supplier_id="V", body="Checking the timing.")).json()["data"]
    assert result["status"] == "partial" and not all_rows(InboxMessage)[0].processed
    error, = result["failures"]
    event = client.get("/api/trace", params={"trace_id": error["trace_id"]}).json()["data"][0]
    assert event["data"]["inbox_id"] == result["inbox_id"] and event["agent"] == "ingest"
    records = client.get(f"/api/decisions/{card.id}/history").json()["data"]["events"]
    assert any(e.get("trace_id") == error["trace_id"] and e["record"]["type"] == "inbox" for e in records)
    system._bus = None
    system.bus().run_until_idle()
    saved, = all_rows(InboxMessage)
    assert saved.id == result["inbox_id"] and saved.processed and saved.result["review"]
    assert len(all_rows(OutboxMessage)) == 1
