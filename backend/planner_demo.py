"""Replay the historical sleeve scenario and an explicitly simulated supplier reply in a fresh temporary database.

From the repository root: backend/.venv/bin/python -m backend.planner_demo
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory


# The 17:23 drop has the revision finding and an outstanding sleeve delivery.
# The evening receipt legitimately changes the recommended action.
SLEEVE_SCENARIO_COMMIT = "f832f462bf8fbca26f1cad93f124a62d3a43a853"


def sleeve_scenario_steps():
    from .core.replay import list_drops

    for index, drop in enumerate(list_drops(), start=1):
        if drop.commit == SLEEVE_SCENARIO_COMMIT:
            return index
    raise RuntimeError("The historical sleeve demo requires the repository's full data-drop history.")


def run(client, scratch):
    from . import system

    def get(path):
        result = client.get(path)
        result.raise_for_status()
        return result.json()["data"]

    def post(path, payload=None):
        result = client.post(path, json=payload)
        result.raise_for_status()
        return result.json()["data"]

    for _ in range(sleeve_scenario_steps()):
        post("/api/replay/step")
    cards = get("/api/decisions?status=proposed")
    revision_findings = {i["id"] for i in get("/api/issues") if i["type"] == "rev_blocked"}
    sleeve = next(card for card in cards if revision_findings.intersection(card["issue_ids"])
                  and any(o["label"] == "Order a fresh rush print" for o in card["options"]))
    chosen = sleeve["options"][sleeve["recommended"]]
    action = chosen["actions"][0]
    forecasts = get("/api/forecast")
    baseline = next(f for f in forecasts if any(x["shipment_id"] == action["shipment_id"] for x in f["details"]["inbound"]))
    inbound = next(x for x in baseline["details"]["inbound"] if x["shipment_id"] == action["shipment_id"])
    as_of = datetime.fromisoformat(baseline["details"]["as_of"])
    arrival = as_of + timedelta(hours=5)

    # Internal site release is separate from supplier evidence. Only the simulation adds this document.
    rel = "site/mock_site_release.csv"
    path = scratch / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["receiving_site", "delivery_reference", "approved_revision", "dock_released_at"])
        from .core.models import Shipment
        from .core.store import find_rows
        site = find_rows(Shipment, shipment_id=action["shipment_id"])[0].dest_site
        writer.writerow([site, action["shipment_id"], inbound["revision"], arrival.isoformat()])
    system.process_drop(added=[rel], data_root=scratch, label="MOCK site lead release for existing delivery")
    sleeve = next(c for c in get("/api/decisions?status=proposed") if c["title"] == sleeve["title"])
    chosen = sleeve["options"][sleeve["recommended"]]
    post(f"/api/decisions/{sleeve['id']}/approve", {"by": chosen["approvers"][0], "approvals": chosen["approvers"][1:]})
    out = next(m for m in get("/api/outbox") if m["decision_id"] == sleeve["id"])
    fact = {key: inbound[key] for key in ("po_number", "po_line", "shipment_id", "revision", "quantity")}
    fact["arrival"] = arrival.isoformat()
    payload = {"subject": "[MOCK] Existing delivery confirmed", "supplier_id": out["supplier_id"],
               "issue_id": out["issue_id"], "outbox_id": out["id"],
               "body": f"[MOCK] Confirmed {int(fact['quantity'])} units, rev {fact['revision']}, at {fact['arrival']}.",
               "delivery": fact}
    receipt = post("/api/inbox", payload)
    after = next(f for f in get("/api/forecast") if f["product_id"] == baseline["product_id"])
    entry = next(x for x in after["details"]["inbound"] if x["shipment_id"] == fact["shipment_id"])
    if not entry["eligible"]:
        raise RuntimeError("Mock delivery did not reach the projection.")
    errors = [e for e in get("/api/trace?limit=500") if e["action"] == "error"]
    if errors:
        raise RuntimeError(errors)
    return {"mode": "MOCK — historical sleeve scenario, simulated site release and supplier reply",
            "through_commit": SLEEVE_SCENARIO_COMMIT,
            "editions": [{k: f[k] for k in ("edition", "projected_packed", "shortfall", "limiting_part")}
                         for f in {row["product_id"]: row for row in reversed(forecasts)}.values()],
            "decision_id": sleeve["id"], "options": [{k: o[k] for k in ("label", "cost_eur", "copies_gained")}
                                                    for o in sleeve["options"]],
            "outbox_id": out["id"], "inbox_id": receipt["inbox_id"], "reply": payload,
            "before": baseline["projected_packed"], "after": after["projected_packed"],
            "remaining_shortfall": after["shortfall"], "confirmed_delivery": entry,
            "trace_run": receipt["run_id"]}


def sleeve_card(client):
    response = client.get("/api/decisions?status=proposed")
    response.raise_for_status()
    return next(c for c in response.json()["data"] if "rev blocked" in c["title"]
                and c["title"].endswith("CMP-GAM-004"))


def prepare_recount(scratch):
    """Controlled fixture inputs. The buyer still has to authorize a supplier inquiry."""
    from . import system
    from .agents.planner_inputs import load_plan
    from .core import store
    from .core.models import StockLot

    # Controlled variant: a recount makes sleeves the binding constraint.
    lot = store.find_rows(StockLot, batch="5000413060")[0]
    store.update_row(StockLot, lot.id, {"quantity": 40000}, source="site/recount", agent="ingest",
                     reason="simulated physical count", on_conflict="overwrite")
    arrival = load_plan().as_of + timedelta(hours=2)
    directory = scratch / "site"
    directory.mkdir()
    release = directory / "slot.csv"
    release.write_text("receiving_site,delivery_reference,approved_revision,dock_released_at\n"
                       f"DC-HAM,SHP-2026-04313,C,{arrival.isoformat()}\n")
    result = system.process_drop(added=["site/slot.csv"], data_root=scratch, mode="simulation",
                                 label="Pickup simulation · Site recount and dock release")
    if result["status"] != "completed":
        raise RuntimeError(f"Simulation setup incomplete: {result['failures']}")
    return arrival


def prepare_quote_request(client, scratch):
    from .core import store
    from .core.models import OutboxMessage

    arrival = prepare_recount(scratch)
    card = sleeve_card(client)
    client.post(f"/api/decisions/{card['id']}/approve", json={"by": "Paul Winter"}).raise_for_status()
    ask_id = card["id"]
    msg, = store.find_rows(OutboxMessage, decision_id=ask_id)
    reply = {"outbox_id": msg.id, "issue_id": msg.issue_id, "supplier_id": msg.supplier_id, "body": "[MOCK]"}
    fact = {"shipment_id": "SHP-2026-04313", "po_number": "4500318775", "po_line": 2, "revision": "C",
            "quantity": 20000, "arrival": arrival.isoformat()}
    return ask_id, reply, fact


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    serve = parser.add_mutually_exclusive_group()
    serve.add_argument("--serve-inquiry", action="store_true", help="serve the simulated recount before any buyer action")
    serve.add_argument("--serve-pickup", action="store_true", help="serve the simulated €500 quote for the buyer UI")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="launch-planner-") as temp:
        scratch = Path(temp)
        os.environ.update(MOCK_MODE="true", REPLAY_DB_PATH=str(scratch / "demo.db"), CHASER_SEND="false", REPLAY_DIR=str(scratch / "replay"))
        from fastapi.testclient import TestClient
        from .app.main import app
        with TestClient(app) as client:
            if args.serve_pickup or args.serve_inquiry:
                for _ in range(sleeve_scenario_steps()):
                    client.post("/api/replay/step").raise_for_status()
            if args.serve_inquiry:
                prepare_recount(scratch)
            elif args.serve_pickup:
                _, reply, fact = prepare_quote_request(client, scratch)
                quote = {**fact, "mode": "pickup", "incremental_cost_eur": 500, "valid_until": fact["arrival"]}
                client.post("/api/inbox", json={**reply, "body": "[MOCK] Pickup quote", "quote": quote}).raise_for_status()
            else:
                print(json.dumps(run(client, scratch), indent=2))
        if args.serve_pickup or args.serve_inquiry:
            import uvicorn
            uvicorn.run(app, host="127.0.0.1", port=args.port)
