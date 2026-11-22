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
    assert _text_confirmation("We cannot confirm " + line) is None
    assert _text_confirmation("Can you confirm " + line + "?") is None
    assert _text_confirmation("Thanks.\n> Confirmed " + line) is None
    assert _text_confirmation("Confirmed " + line + "\n> Quote requested").quantity == 20000


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
