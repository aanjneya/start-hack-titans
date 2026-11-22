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
