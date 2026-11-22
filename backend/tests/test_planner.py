from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend import system
from backend.agents.planner_forecast import Plan, simulate
from backend.agents.planner_inputs import load_plan
from backend.agents.planner_options import approvers
from backend.app.main import app
from backend.core import events
from backend.core.replay import list_drops
from backend.core.models import AgentLog, DecisionCard, Document, Forecast, InboxMessage, Issue, LaunchStatus, OutboxMessage
from backend.core.store import all_rows
from backend.planner_demo import SLEEVE_SCENARIO_COMMIT, prepare_quote_request, run, sleeve_card, sleeve_scenario_steps


def _decision_mails(decision_id=None):
    return [m for m in all_rows(OutboxMessage) if m.decision_id is not None
            and (decision_id is None or m.decision_id == decision_id)]


@pytest.fixture
def sleeve_snapshot():
    for _ in range(sleeve_scenario_steps()):
        system.replay_step()


def test_partial_late_and_repeated_deliveries_use_remaining_line_capacity():
    at = datetime(2026, 1, 1, 8, tzinfo=timezone.utc)
    end = at + timedelta(hours=2)
    first = LaunchStatus(product_id="priority", still_to_pack=5, packed=3, rate_per_hour=5)
    other = LaunchStatus(product_id="other", still_to_pack=20, packed=1, rate_per_hour=10)
    p = Plan(at, end, [(at, end)], [first, other], {r.product_id: {"shared": 1} for r in [first, other]},
             Counter(shared=5))
    key = ("supplier", "po", 1, "shipment")
    assert simulate(p)["priority"].projected_packed == 8
    assert simulate(p)["other"].projected_packed == 1
    p.arrivals[key] = (at + timedelta(minutes=90), "shared", 15)
    assert simulate(p)["other"].projected_packed == 6
    assert simulate(p, {key: (at, "shared", 2)})["other"].projected_packed == 3
    assert simulate(p, {key: (end, "shared", 50)})["other"].shortfall == 20


def test_real_replay_lots_rush_and_correlated_inbox(tmp_path):
    client = TestClient(app)
    result = run(client, tmp_path)
    assert result["before"] == result["after"]
    assert result["remaining_shortfall"] > 0
    assert result["confirmed_delivery"]["eligible"]
    expensive = next(o for o in result["options"] if "rush" in o["label"])
    assert expensive["copies_gained"] == 0 and expensive["cost_eur"] == 1215
    p = load_plan()
    rows = simulate(p)
    standard = rows["FG-GH6-STD"]
    assert standard.details["working_hours"] == 23
    assert standard.still_to_pack == 86000
    assert rows["FG-GH6-CE"].still_to_pack == 18900
    assert "CMP-MAG-005" not in p.bom["FG-GH6-CE"]
    sleeves = [x for x in p.lots if x["part"] == "CMP-GAM-004"]
    assert sum(x["quantity"] for x in sleeves if x["status"] == "eligible") == 122000
    assert sum(x["quantity"] for x in sleeves if x["status"] == "revision blocked") == 64000
    assert p.stock["CMP-GAM-004"] == 80000
    assert any(x["status"] == "Quality hold" for x in p.lots)
    # Changing Quality evidence independently changes the projection.
    from backend.core.store import session
    from sqlmodel import select
    with session() as s:
        inspection = s.exec(select(Document).where(Document.kind == "inspection")).first()
        inspection.text = "Replacement inspection: lot released."
        s.add(inspection)
        s.commit()
    released = load_plan()
    held_parts = {lot["part"] for lot in p.lots if lot["status"] == "Quality hold"}
    assert any(released.stock[part] > p.stock[part] for part in held_parts)
    # Isolate Quality: when the art books are covered, releasing the inspected lot gains copies.
    for scenario in (p, released):
        scenario.stock["CMP-GAM-008"] += 8000
    assert simulate(released)["FG-GH6-CE"].shortfall < simulate(p)["FG-GH6-CE"].shortfall
    # Repeated approvals and replies do not send another email or count the same units again.
    assert client.post(f"/api/decisions/{result['decision_id']}/approve", json={}).status_code == 409
    system.emit(events.DECISION_APPROVED, {"decision_id": result["decision_id"]})
    assert len(_decision_mails(result["decision_id"])) == 1
    reply = result["reply"]
    client.post("/api/inbox", json=reply).raise_for_status()
    assert "Duplicate" in all_rows(InboxMessage)[-1].result["review"]
    bad = {**reply, "delivery": {**reply["delivery"], "po_line": 99}}
    client.post("/api/inbox", json=bad).raise_for_status()
    assert all_rows(InboxMessage)[-1].result["review"]
    corrected = {**reply["delivery"], "quantity": 1000, "arrival": (p.deadline + timedelta(hours=1)).isoformat()}
    client.post("/api/inbox", json={**reply, "delivery": corrected}).raise_for_status()
    assert "Conflicting" in all_rows(InboxMessage)[-1].result["review"]
    corrected["supersedes_inbox_id"] = result["inbox_id"]
    client.post("/api/inbox", json={**reply, "delivery": corrected}).raise_for_status()
    assert all_rows(InboxMessage)[-1].result["confirmed_delivery"]["quantity"] == 1000
    assert any(at > p.deadline and qty == 1000 for at, _, qty in load_plan().arrivals.values())
    count = len(all_rows(DecisionCard)), len(all_rows(Forecast))
    system.emit(events.TRUTH_UPDATED, {"tables": ["inbox"]})
    assert count == (len(all_rows(DecisionCard)), len(all_rows(Forecast)))
    # A quote enters the card as a scenario, never as a confirmed supply event.
    before = dict(load_plan().arrivals)
    quoted = {**reply["delivery"], "mode": "air", "incremental_cost_eur": 2000,
              "valid_until": p.deadline.isoformat()}
    client.post("/api/inbox", json={**reply, "delivery": None, "quote": quoted}).raise_for_status()
    assert all_rows(InboxMessage)[-1].result["supplier_quote"]["mode"] == "air"
    assert before == load_plan().arrivals
    assert any(o["description"] == "air" for c in all_rows(DecisionCard) for o in c.options)
    docs = all_rows(Document)
    assert "Jonas Lindqvist" in approvers(1500, "Print & Packaging", p, docs, air=True, monthly_spend=200000)
    assert "Sofia Marchetti" in approvers(1500, "Print & Packaging", p, docs, air=True, monthly_spend=1000)



@pytest.mark.parametrize("mode", ["pickup", "air"])
def test_priced_recovery_waits_for_confirmation_and_uses_whole_po(tmp_path, monkeypatch, mode, sleeve_snapshot):
    client = TestClient(app)
    ask_id, reply, fact = prepare_quote_request(client, tmp_path)
    arrival = datetime.fromisoformat(fact["arrival"])
    if mode == "air":
        # Simulate a generous auto-spend limit for this quote. PP-07 still requires sign-off.
        monkeypatch.setattr("backend.core.policy.needs_approval", lambda amount: amount == 0)
    quote = {**fact, "mode": mode, "incremental_cost_eur": 500, "valid_until": arrival.isoformat()}
    before = simulate(load_plan())["FG-GH6-STD"].projected_packed
    if mode == "pickup":
        text = (f"Pickup quote. PO {fact['po_number']}/{fact['po_line']}, {fact['shipment_id']}, "
                f"{fact['quantity']} units, rev {fact['revision']}, arrival {fact['arrival']}, "
                f"incremental cost EUR 500, valid until {arrival.isoformat()}.")
        client.post("/api/inbox", json={**reply, "body": text}).raise_for_status()
    else:
        client.post("/api/inbox", json={**reply, "quote": quote}).raise_for_status()
    card = sleeve_card(client)
    option = card["options"][card["recommended"]]
    assert option["description"] == mode and option["copies_gained"] > 0
    assert option["cost_eur"] == 500 and option["approval_value_eur"] == 53150
    signers = ["Sofia Marchetti", "Jonas Lindqvist"] if mode == "air" else ["Sofia Marchetti"]
    assert option["approval_roles"] == (["Category lead", "CPO"] if mode == "air" else ["Category lead"])
    assert option["approvers"] == signers and option["needs_approval"]
    assert card["status"] == "proposed" and len(_decision_mails(ask_id)) == 1 and not _decision_mails(card["id"])
    entries = client.get("/api/buyer-queue").json()["data"]
    entry, = [e for e in entries if e["option"]["description"] == mode]
    assert any(src["decision_id"] == card["id"] for src in entry["sources"])
    assert entry["option"]["copies_gained"] == 5200
    card = next(c for c in client.get("/api/decisions").json()["data"] if c["id"] == entry["decision_id"])
    url = f"/api/decisions/{card['id']}/approve"
    selection = {"option": entry["option_index"], "version": entry["version"]}
    assert client.post(url, json={**selection, "by": "Paul Winter"}).status_code == 403
    if mode == "air":
        assert client.post(url, json={"by": signers[0]}).status_code == 403
    client.post(url, json={**selection, "by": signers[0], "approvals": signers[1:]}).raise_for_status()
    assert simulate(load_plan())["FG-GH6-STD"].projected_packed == before
    spend, = _decision_mails(card["id"])
    assert spend.decision_id == card["id"]
    if mode == "air":
        client.post("/api/inbox", json={**reply, "outbox_id": spend.id, "delivery": fact}).raise_for_status()
    else:
        # The supplier replies to Tim's actual message. Substitute only the mailbox transport and its gate.
        from dataclasses import replace
        from types import SimpleNamespace
        from backend.agents import chaser
        from backend.app.main import _chaser

        assert spend.message_id and spend.status == "sent"
        assert "4500318775/2" in spend.body and fact["shipment_id"] in spend.body
        assert any(log.action == "mock_send" and log.data.get("outbox_id") == spend.id
                   for log in all_rows(AgentLog))
        mail = chaser.mailbox.IncomingMail("1", "<supplier-reply@example.test>", spend.message_id, "",
                "supplier-dispatch@example.test", "Re: " + spend.subject, f"Confirmed PO 4500318775/2, SHP-2026-04313, "
                f"20000 units, rev C, arrival {arrival.isoformat()}.")
        settings = replace(chaser.get_settings(), chaser_send=True)
        with monkeypatch.context() as transport:
            transport.setattr(chaser, "get_settings", lambda: settings)
            transport.setattr(chaser, "llm", SimpleNamespace(is_mock=lambda: False))
            transport.setattr(chaser.mailbox, "is_configured", lambda: True)
            transport.setattr(chaser.mailbox, "fetch_replies", lambda ids, contacts: [mail] if spend.message_id in ids else [])
            assert _chaser().poll_inbox()["matched"] == 1
            assert _chaser().poll_inbox()["matched"] == 0
        system.bus().run_until_idle()
        inbound, = [m for m in all_rows(InboxMessage) if m.outbox_id == spend.id]
        assert inbound.processed and inbound.issue_id == spend.issue_id
        assert inbound.result["confirmed_delivery"]["quantity"] == fact["quantity"]
        assert _decision_mails(card["id"])[0].status == "replied"
        assert not [log.detail for log in all_rows(AgentLog) if log.action == "error"]
    assert simulate(load_plan())["FG-GH6-STD"].projected_packed == before + option["copies_gained"]
    history = client.get(f"/api/decisions/{card['id']}/history").json()["data"]["events"]
    titles = [e["title"] for e in history]
    assert titles.count("Supplier quote matched") == titles.count("Delivery confirmation matched") == 1
    assert titles.count("Supplier request sent in simulation") == titles.count("Action authorized") == 2
    assert titles.index("Supplier quote matched") < max(i for i, title in enumerate(titles) if title == "Action authorized")
    projection, = [e for e in history if e["title"] == "Standard forecast recalculated"]
    assert "+5,200" in projection["detail"] and "605,200" in projection["detail"]
    assert {e["record"]["id"] for e in history if e["record"]["type"] == "outbox"} == {
        _decision_mails(ask_id)[0].id, spend.id}
    client.post("/api/inbox", json={**reply, "outbox_id": spend.id, "delivery": fact}).raise_for_status()
    events_after = client.get(f"/api/decisions/{card['id']}/history").json()["data"]["events"]
    assert [e["title"] for e in events_after if e not in history] == ["Duplicate reply retained"]


def test_rejection_and_auto_threshold(tmp_path, monkeypatch, sleeve_snapshot):
    import json
    from backend.core import config, policy
    client = TestClient(app)
    card = next(c for c in all_rows(DecisionCard) if "rev blocked" in c.title and c.title.endswith("CMP-GAM-004") and c.status == "proposed")
    url = f"/api/decisions/{card.id}/reject"
    assert client.post(url, json={"by": "another buyer"}).status_code == 403
    assert client.post(url, json={"by": card.approver, "option": -1}).status_code == 422
    client.post(url, json={"by": card.approver}).raise_for_status()
    assert not _decision_mails(card.id)
    policy_path = tmp_path / "auto.json"
    settings = {"customer": "Example", "autonomy": {"mode": "auto", "auto_execute_below_eur": 1},
                "approval_matrix": [{"below_eur": None, "approvers": ["Buyer"]}]}
    policy_path.write_text(json.dumps(settings))
    monkeypatch.setenv("CUSTOMER_SETUP", str(policy_path))
    config.reset_settings()
    policy.reset_cache()
    # An explicitly rejected recommendation stays rejected for the same evidence.
    system.emit(events.TRUTH_UPDATED, {"tables": ["inbox"]})
    return_cards = [c for c in all_rows(DecisionCard) if c.title == card.title]
    assert not any(o.issue_id in card.issue_ids for o in _decision_mails())
    assert return_cards[-1].status == "rejected"
    assert any(c.decided_by == "policy" for c in all_rows(DecisionCard) if c.title != card.title)
    assert policy.needs_approval(1) and not policy.needs_approval(0)


def test_latest_replay_retires_received_sleeve_request():
    """Exercise every available drop; a receipt retires the old request even if later cards change."""
    historical_id = None
    saw_receipt = False
    for drop in list_drops():
        system.replay_step()
        cards = all_rows(DecisionCard)
        for card in cards:
            if card.status == "proposed" and card.options[0].get("remaining_shortfall", 0):
                assert card.options[card.recommended]["description"] != "baseline"
        if drop.commit == SLEEVE_SCENARIO_COMMIT:
            historical_id, = [c.id for c in all_rows(DecisionCard) if c.status == "proposed"
                               and "rev blocked" in c.title and c.title.endswith("CMP-GAM-004")]
        if "soony/erp/goods_receipts_update.csv" in drop.added:
            saw_receipt = True
            plan = load_plan()
            editions = {r.product_id: r for r in plan.editions}
            assert plan.as_of.isoformat().startswith("2026-11-21T20:50")
            assert editions["FG-GH6-STD"].still_to_pack == 38000
            assert editions["FG-GH6-CE"].still_to_pack == 11700
            assert plan.stock["CMP-GAM-003"] == 6000 and plan.stock["CMP-GAM-001"] == 42900
            assert plan.stock["CMP-GAM-008"] == 5800
            balance = next(i for i in plan.inbound if i["shipment_id"] == "SHP-2026-04311"
                           and i["part"] == "CMP-GAM-001")
            assert balance["quantity"] == 20000 and balance["eta"] == "2026-11-24" and not balance["eligible"]
            sleeve = next(lot for lot in plan.lots if lot["batch"] == "5000413071")
            assert sleeve["quantity"] == 16000
            forecast = simulate(plan)
            assert forecast["FG-GH6-STD"].shortfall == 32000
            assert forecast["FG-GH6-CE"].shortfall == 5900
            proposed = [c.options[c.recommended] for c in cards if c.status == "proposed" and c.recommended is not None]
            concession = next(o for c in cards if c.status == "proposed" for o in c.options
                              if o["description"] == "quality concession review")
            assert concession["approvers"] == ["Daniel Okafor", "Dr. Maren Vogt"]
            assert concession["needs_approval"] and concession["copies_gained"] == 0
            books = next(o for o in proposed if o["description"] == "supplier stock offer")
            assert books["cost_eur"] == 16800 and books["copies_gained"] == 2500
            pickup = next(o for o in proposed if o["description"] == "port pickup")
            assert pickup["cost_eur"] is None and pickup["copies_gained"] > 0
            assert next(c.status for c in cards if c.id == historical_id) == "outdated"
            for c in cards:
                if c.status == "proposed" and c.recommended is not None:
                    assert all(a.get("shipment_id") != "SHP-2026-04313" or a.get("po_number") != "4500318775"
                               for a in c.options[c.recommended]["actions"])
    assert saw_receipt
    assert not [log.detail for log in all_rows(AgentLog) if log.action == "error"]
    forecasts = simulate(load_plan())
    assert forecasts
    assert all(f.projected_packed is not None and f.shortfall >= 0 for f in forecasts.values())


def test_received_sleeves_route_review_and_repair_persisted_cards():
    for drop in list_drops():
        system.replay_step()
        if "soony/erp/goods_receipts_update.csv" in drop.added:
            break
    else:
        pytest.fail("The sleeve receipt is missing from the replay history.")
    cards = all_rows(DecisionCard)
    sleeve, = [c for c in cards if c.status == "proposed" and c.title.startswith("Standard")
               and "rev blocked" in c.title and c.title.endswith("CMP-GAM-004")]
    assert sleeve.options[sleeve.recommended]["description"] == "human review"
    # Restoring a database from before the recommendation fix also retires the stale card.
    from backend.core.store import session
    with session() as db:
        persisted = db.get(DecisionCard, sleeve.id)
        persisted.recommended = 0
        db.add(persisted)
        db.commit()
    system._bus = None
    system.bus().run_until_idle()
    cards = all_rows(DecisionCard)
    assert next(c.status for c in cards if c.id == sleeve.id) == "outdated"
    sleeve, = [c for c in cards if c.title == sleeve.title and c.status == "proposed"]
    review = sleeve.options[sleeve.recommended]
    assert review["description"] == "human review" and review["available"] and review["needs_approval"]
    assert review["actions"][0]["part"] == simulate(load_plan())["FG-GH6-STD"].limiting_part
    assert review["remaining_shortfall"] == 32000
    rush, = [o for o in sleeve.options if o["label"] == "Order a fresh rush print"]
    assert rush["cost_eur"] == 1215 and rush["copies_gained"] == 0 and not rush["available"]
    client = TestClient(app)
    url = f"/api/decisions/{sleeve.id}/approve"
    approval = {"by": review["approvers"][0], "approvals": review["approvers"][1:]}
    response = client.post(url, json=approval)
    response.raise_for_status()
    assert response.json()["data"]["decision"]["status"] == "executed"
    system.emit(events.DECISION_APPROVED, {"decision_id": sleeve.id})
    followup, = [i for i in all_rows(Issue) if i.type == "recovery_review"]
    assert followup.kind == "human_check" and followup.part_id == review["actions"][0]["part"]
    assert followup.impact["copies_at_risk"] == 32000
    assert not _decision_mails(sleeve.id)
