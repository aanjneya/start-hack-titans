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
from backend.core.models import DecisionCard, Document, Forecast, InboxMessage, LaunchStatus, OutboxMessage
from backend.core.store import all_rows
from backend.planner_demo import run



def _decision_mails():
    """Planner-dispatched mails only. The live Chaser also writes question mails for missing_info findings."""
    return [o for o in all_rows(OutboxMessage) if o.decision_id]

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
    assert simulate(load_plan())["FG-GH6-CE"].shortfall < rows["FG-GH6-CE"].shortfall
    # Repeated approvals and replies do not send another email or count the same units again.
    assert client.post(f"/api/decisions/{result['decision_id']}/approve", json={}).status_code == 409
    system.emit(events.DECISION_APPROVED, {"decision_id": result["decision_id"]})
    assert len(_decision_mails()) == 1
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
def test_priced_recovery_waits_for_confirmation_and_uses_whole_po(tmp_path, monkeypatch, mode):
    from backend.core import store
    from backend.core.models import StockLot

    client = TestClient(app)
    system.run_full()
    # Controlled variant: a recount makes sleeves the binding constraint.
    lot = store.find_rows(StockLot, batch="5000413060")[0]
    store.update_row(StockLot, lot.id, {"quantity": 40000}, source="site/recount", agent="ingest",
                     reason="simulated physical count", on_conflict="overwrite")
    p = load_plan()
    arrival = p.as_of + timedelta(hours=2)
    directory = tmp_path / "site"
    directory.mkdir()
    release = directory / "slot.csv"
    release.write_text("receiving_site,delivery_reference,approved_revision,dock_released_at\n"
                       f"DC-HAM,SHP-2026-04313,C,{arrival.isoformat()}\n")
    system.process_drop(added=["site/slot.csv"], data_root=tmp_path)
    def current():
        return next(c for c in client.get("/api/decisions?status=proposed").json()["data"]
                    if "rev blocked" in c["title"] and c["title"].endswith("CMP-GAM-004"))
    card = current()
    assert client.post(f"/api/decisions/{card['id']}/approve", json={"by": "Paul Winter"}).status_code == 200
    msg = _decision_mails()[-1]
    reply = {"outbox_id": msg.id, "issue_id": msg.issue_id, "supplier_id": msg.supplier_id, "body": "[MOCK]"}
    fact = {"shipment_id": "SHP-2026-04313", "po_number": "4500318775", "po_line": 2, "revision": "C",
            "quantity": 20000, "arrival": arrival.isoformat()}
    if mode == "air":
        # Simulate a generous auto-spend limit for this quote. PP-07 still requires sign-off.
        monkeypatch.setattr("backend.core.policy.needs_approval", lambda amount: amount == 0)
    quote = {**fact, "mode": mode, "incremental_cost_eur": 500, "valid_until": arrival.isoformat()}
    before = simulate(load_plan())["FG-GH6-STD"].projected_packed
    client.post("/api/inbox", json={**reply, "quote": quote}).raise_for_status()
    card = current()
    option = card["options"][card["recommended"]]
    assert option["description"] == mode and option["copies_gained"] > 0
    assert option["cost_eur"] == 500 and option["approval_value_eur"] > 50000
    signers = ["Sofia Marchetti", "Jonas Lindqvist"] if mode == "air" else ["Sofia Marchetti"]
    assert option["approvers"] == signers and option["needs_approval"]
    assert card["status"] == "proposed" and len(_decision_mails()) == 1
    url = f"/api/decisions/{card['id']}/approve"
    assert client.post(url, json={"by": "Paul Winter"}).status_code == 403
    if mode == "air":
        assert client.post(url, json={"by": signers[0]}).status_code == 403
    client.post(url, json={"by": signers[0], "approvals": signers[1:]}).raise_for_status()
    assert simulate(load_plan())["FG-GH6-STD"].projected_packed == before
    spend = _decision_mails()[-1]
    assert spend.decision_id == card["id"]
    client.post("/api/inbox", json={**reply, "outbox_id": spend.id, "delivery": fact}).raise_for_status()
    assert simulate(load_plan())["FG-GH6-STD"].projected_packed == before + option["copies_gained"]


def test_rejection_and_auto_threshold(tmp_path, monkeypatch):
    import json
    from backend.core import config, policy
    client = TestClient(app)
    system.run_full()
    card = next(c for c in all_rows(DecisionCard) if "rev blocked" in c.title and c.title.endswith("CMP-GAM-004") and c.status == "proposed")
    client.post(f"/api/decisions/{card.id}/reject", json={"by": "buyer"}).raise_for_status()
    assert not _decision_mails()
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
