"""Acceptance for the buyer-facing action queue, including concurrent source decisions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from backend import system
from backend.agents.planner_queue import buyer_queue
from backend.app.main import app
from backend.core.models import DecisionCard, Document, Forecast, InboxMessage, Issue, OutboxMessage
from backend.core.replay import list_drops, materialize
from backend.core.store import add_all, all_rows, session
from backend.planner_demo import prepare_quote_request, sleeve_scenario_steps


def queue(client):
    response = client.get("/api/buyer-queue")
    response.raise_for_status()
    return response.json()["data"]


def test_evening_queue_preserves_findings_and_ranks_conditional_actions():
    client = TestClient(app)
    for drop in list_drops():
        system.replay_step()
        if "soony/erp/goods_receipts_update.csv" in drop.added:
            break
    items = queue(client)
    books, pickup = items[:2]
    assert books["option"]["description"] == "supplier stock offer"
    assert (books["option"]["cost_eur"], books["option"]["copies_gained"]) == (16800, 2500)
    assert "freight" in " ".join(books["requirements"]) and "dock" in " ".join(books["requirements"])
    assert pickup["option"]["description"] == "port pickup" and pickup["option"]["copies_gained"] == 25200
    assert pickup["option"]["cost_eur"] is None and pickup["shared_shipment"] == "SHP-2026-04276"
    assert any(s["related_review"] for s in books["sources"])
    assert len([i for i in items if i["option"]["description"] == "port pickup"]) == 1
    assert len([i for i in items if i["option"]["description"] == "supplier stock offer"]) == 1
    assert not any(i["option"]["description"] == "human review" for i in items)
    sources = {s["decision_id"] for i in items for s in i["sources"]}
    proposed = {c.id for c in all_rows(DecisionCard) if c.status == "proposed"}
    assert proposed == sources
    assert all(client.get(f"/api/issues/{n}").status_code == 200 for n in
               {n for i in items for s in i["sources"] for n in s["issue_ids"]})
    assert all(i["evidence"] for i in items)
    expected = [(e["key"], e["version"]) for e in items]
    count = len(all_rows(DecisionCard)), len(all_rows(OutboxMessage))
    system.process_drop(added=drop.added, changed=drop.changed, removed=drop.removed,
                        data_root=materialize(drop), label="Repeat evening evidence")
    assert [(e["key"], e["version"]) for e in queue(client)] == expected
    assert count == (len(all_rows(DecisionCard)), len(all_rows(OutboxMessage)))
    forecasts = {f.product_id: f for f in all_rows(Forecast)}
    assert forecasts["FG-GH6-STD"].projected_packed == 608000
    assert forecasts["FG-GH6-CE"].projected_packed == 66100


def test_identity_and_concurrent_authorization_across_source_cards():
    from backend.agents.planner_queue import physical_key
    client = TestClient(app)
    def proposal(line, label, who="Buyer", kind="information request"):
        action = dict(type="email", part="P", supplier_id="SUP-0028", po_number="PO-X", po_line=line,
                      shipment_id="SHP-X", ask=label)
        opt = dict(label=label, description=kind, actions=[action], available=True, approvers=[who],
                   cost_eur=0, copies_gained=2)
        issue, = add_all([Issue(type="test_finding", kind="copies_at_risk", summary=label,
                                fingerprint=f"{kind}/{line}/{label}/{who}", supplier_id="SUP-0028")])
        return DecisionCard(created_by="planner", issue_ids=[issue.id], title="Edition", recommended=0, options=[opt])
    first, second, _, _, _ = add_all([proposal(1, "Confirm supply"), proposal(1, "Ask for confirmation"),
                                      proposal(2, "Confirm supply"), proposal(1, "Confirm supply", "Lead"),
                                      proposal(1, "Obtain quote", kind="expedite")])
    with session() as db:
        groups = buyer_queue(db)
    assert len(groups) == 4
    shared, = [e for e in groups if len(e["sources"]) == 2]
    assert {s["decision_id"] for s in shared["sources"]} == {first.id, second.id}
    assert physical_key(first.options[0]) == physical_key(second.options[0])
    stale = client.post(f"/api/decisions/{first.id}/approve", json={"by": "Buyer", "version": "outdated"})
    assert stale.status_code == 409 and not all_rows(OutboxMessage)

    def approve(card):
        return client.post(f"/api/decisions/{card.id}/approve", json={"by": "Buyer", "option": 0}).status_code
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(approve, (first, second))) == [200, 409]
    assert sum(c.status in ("approved", "executed") for c in all_rows(DecisionCard)) == 1
    assert len(all_rows(OutboxMessage)) == 1
    assert len([x for x in queue(client) if x["option"]["description"] == "information request"]) == 1


@pytest.mark.parametrize("change", ["late", "expired", "dock"])
def test_stale_priced_action_cannot_dispatch(tmp_path, change):
    client = TestClient(app)
    for _ in range(sleeve_scenario_steps()):
        system.replay_step()
    _, reply, fact = prepare_quote_request(client, tmp_path)
    quote = dict(fact, mode="pickup", incremental_cost_eur=500, valid_until=fact["arrival"])
    client.post("/api/inbox", json=dict(reply, quote=quote)).raise_for_status()
    entry, = [e for e in queue(client) if e["option"]["description"] == "pickup"]
    assert entry["option"]["approval_value_eur"] == 53150 and entry["option"]["approvers"] == ["Sofia Marchetti"]
    before = len(all_rows(OutboxMessage))
    # Change evidence before a truth.updated handler has superseded the displayed source card.
    with session() as db:
        if change == "dock":
            doc = next(d for d in db.exec(select(Document)).all() if d.path.startswith("site/slot.csv")
                       and d.extracted.get("rows"))
            doc.extracted = {**doc.extracted, "rows": []}
        else:
            doc = db.get(InboxMessage, entry["option"]["actions"][0]["quote_inbox_id"])
            key, days = ("arrival", 4) if change == "late" else ("valid_until", -1)
            updated = {**doc.result["supplier_quote"], key: (datetime.fromisoformat(fact["arrival"])
                                                             + timedelta(days=days)).isoformat()}
            doc.result = {"supplier_quote": updated}
        db.add(doc)
        db.commit()
    response = client.post(f"/api/decisions/{entry['decision_id']}/approve", json={
        "by": "Sofia Marchetti", "option": entry["option_index"], "version": entry["version"]})
    assert response.status_code == 409 and "refresh" in response.text.lower()
    assert len(all_rows(OutboxMessage)) == before
    assert not any(e["option"]["description"] == "pickup" for e in queue(client))
    options = [o for c in all_rows(DecisionCard) if c.status == "proposed" for o in c.options if o["description"] == "pickup"]
    assert options and all(not o["available"] and o["approvers"] == ["Sofia Marchetti"] for o in options)
    assert all(o["copies_gained"] == (0 if change == "late" else 5200) for o in options)


def test_competing_freight_quotes_can_be_compared_but_only_one_can_be_ordered(tmp_path):
    client = TestClient(app)
    for _ in range(sleeve_scenario_steps()):
        system.replay_step()
    _, reply, fact = prepare_quote_request(client, tmp_path)
    for mode, cost in (("air", 600), ("pickup", 500)):
        quote = dict(fact, mode=mode, incremental_cost_eur=cost, valid_until=fact["arrival"])
        client.post("/api/inbox", json=dict(reply, quote=quote)).raise_for_status()
    offers = {e["option"]["description"]: e for e in queue(client)
              if e["option"]["actions"][0].get("quote_inbox_id")}
    assert set(offers) == {"air", "pickup"}
    assert all(e["option"]["copies_gained"] == 5200 for e in offers.values())

    assert offers["pickup"]["freight_comparison"]["preferred"]
    assert not offers["air"]["freight_comparison"]["preferred"]
    assert offers["pickup"]["freight_comparison"]["fee_per_additional_copy"] == pytest.approx(500 / 5200)
    assert offers["pickup"]["freight_comparison"]["alternatives"] == [
        {"label": "Accept air quote", "fee": 600, "gain": 5200}]

    def approve(service):
        entry = offers[service]
        owners = entry["option"]["approvers"]
        return client.post(f"/api/decisions/{entry['decision_id']}/approve", json={
            "by": owners[0], "approvals": owners[1:], "option": entry["option_index"], "version": entry["version"]})
    count = len(all_rows(OutboxMessage))
    approve("pickup").raise_for_status()
    assert approve("air").status_code == 409
    assert len(all_rows(OutboxMessage)) == count + 1
    assert not any(e["option"]["actions"][0].get("quote_inbox_id") for e in queue(client))
