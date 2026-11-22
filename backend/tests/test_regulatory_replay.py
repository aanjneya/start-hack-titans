"""Branch acceptance: isolated restart/retry and customer-specific procurement decisions."""
from datetime import date
import json
import os
import sqlite3
import subprocess
import sys

from fastapi.testclient import TestClient

from backend import system
from backend.app.main import app
from backend.core import config, events, models as m, store
from backend.core.replay import list_drops


def future(client):
    return [q for q in client.get("/api/buyer-queue").json()["data"] if q["option"].get("horizon") == "future"]


def restart():
    store.dispose_engine()
    config.reset_settings()
    system._bus = None
    system.bus()


def test_restart_after_partial_retries_only_failed_handler(tmp_path):
    live = config.get_settings().db_path
    with sqlite3.connect(live) as db:
        db.execute("CREATE TABLE sentinel (message TEXT)")
        db.execute("INSERT INTO sentinel VALUES ('live buyer record')")
    before = live.read_bytes()
    client = TestClient(app)
    healthy = []
    def broken(event):
        raise RuntimeError("test disruption")
    system.bus().subscribe(events.TRUTH_UPDATED, "temporary_failure", broken)
    system.bus().subscribe(events.TRUTH_UPDATED, "healthy", lambda event: healthy.append(event.name))
    attempted = client.post("/api/replay").json()["data"]
    assert len(attempted) == 1
    run = attempted[0]
    assert run["status"] == "partial" and healthy
    assert all(f["agent"] == "temporary_failure" and f["event"] == events.TRUTH_UPDATED for f in run["failures"])
    traced = client.get("/api/trace", params={"run_id": run["run_id"]}).json()["data"]
    assert {f["trace_id"] for f in run["failures"]} <= {e["id"] for e in traced if e["action"] == "error"}
    assert client.get("/api/health").json()["data"]["lastDrop"]["status"] == "partial"
    sent = [m.id for m in store.all_rows(m.OutboxMessage)]
    restart()
    retried, repeated = [], []
    system.bus().subscribe(events.TRUTH_UPDATED, "temporary_failure", lambda e: retried.append(e.name))
    system.bus().subscribe(events.TRUTH_UPDATED, "healthy", lambda e: repeated.append(e.name))
    result = client.post("/api/replay/step").json()["data"]
    assert (result["step"], result["status"], result["failures"]) == (1, "completed", [])
    assert retried and not repeated and sent == [m.id for m in store.all_rows(m.OutboxMessage)]
    restart()
    assert client.post("/api/replay/step").json()["data"]["step"] == 2
    assert live.read_bytes() == before


def test_live_api_refuses_replay_and_cli_uses_separate_database(tmp_path, monkeypatch):
    monkeypatch.delenv("REPLAY_DB_PATH")
    restart()
    client = TestClient(app)
    for route in ["/api/replay", "/api/replay/step"]:
        assert client.post(route).status_code == 409
    assert client.post("/api/run", json={"reset": True}).status_code == 409
    live = config.get_settings().db_path
    before = live.read_bytes()
    cmd = [sys.executable, "-m", "backend.core.replay", "--upto", "1"]
    subprocess.run(cmd, check=True, capture_output=True)
    assert live.read_bytes() == before and live.with_name("truth.replay.db").is_file()
    alias = tmp_path / "alias.db"
    alias.symlink_to(live)
    assert subprocess.run(cmd, env={**os.environ, "REPLAY_DB_PATH": str(alias)}, capture_output=True).returncode != 0
    assert live.read_bytes() == before


def test_real_magnets_open_one_future_review():
    client = TestClient(app)
    for drop in list_drops():
        res = client.post("/api/replay/step").json()["data"]
        assert res["status"] == "completed"
        if any("components_delta" in name and "PackRight" in name for name in drop.added + drop.changed):
            break
        assert not any("105 days" in q["next_step"] for q in future(client))
    entry, = [q for q in future(client) if q["option"]["button_label"] == "Start qualification review"]
    assert "63 to 105 days" in entry["next_step"] and "2026-12-01" in entry["next_step"]
    assert entry["part"] == "CMP-GAM-007" and entry["option"]["copies_gained"] is None
    assert len(entry["sources"]) >= 2
    forecasts = client.get("/api/forecast").json()["data"]
    assert forecasts and all(f["limiting_part"] != "CMP-MAG-005" for f in forecasts)
    mails = len(store.all_rows(m.OutboxMessage))
    for idx, src in enumerate(entry["sources"][:2]):
        result = client.post(f"/api/decisions/{src['decision_id']}/approve",
                             json={"by": "Paul Winter", "option": src["option_index"]})
        assert result.status_code == (200 if idx == 0 else 409)
    review, = [i for i in store.all_rows(m.Issue) if i.impact.get("horizon") == "future"]
    assert review.impact["owner"] == "Paul Winter" and review.supplier_id == "SUP-0029"
    assert client.get("/api/forecast").json()["data"] == forecasts and len(store.all_rows(m.OutboxMessage)) == mails


def test_second_customer_regulation_without_launch_calendar(tmp_path, monkeypatch):
    setup = tmp_path / "client.json"
    setup.write_text(json.dumps(dict(customer="Pacific Tools", site="Osaka", timezone="Asia/Tokyo", currency="JPY",
        demo_buyer="Aki Ito", approval_matrix=[{"below_eur": None, "approvers": ["Sourcing manager"]}])))
    monkeypatch.setenv("CUSTOMER_SETUP", str(setup))
    config.reset_settings()
    client = TestClient(app)
    facts = [
        m.Supplier(supplier_id="V-9", country="JP", name="Kobe Tools"),
        m.Material(component_id="A", description="Actuator assembly", category="Motion"),
        m.SupplierPartMap(component_id="C", supplier_id="V-9", share=1, note="fitted by supplier into A", source="parts"),
        m.SupplierPartMap(component_id="A", supplier_id="V-9", supplier_ref="AX-901", lead_time_days=88, source="parts"),
        m.Product(product_id="M", name="Mill"), m.BomLine(parent_id="M", child_id="A"),
        m.Document(kind="org_chart", path="roles", text="2025-01-01\nRin Abe\nSourcing manager"),
        m.Document(kind="notice", path="letter", supplier_id="V-9", text="AX-901 delivery after 2026-12-18 requires a licence."),
        m.PurchaseOrder(supplier_id="V-9", material_id="A", po_number="O", po_line=4, status="open", promised_date=date(2027, 1, 3)),
    ]
    store.add_all(facts)
    issue, = store.add_all([m.Issue(type="supplier_warning", raised_by="risk", kind="human_check", supplier_id="V-9",
        part_id="C", fingerprint="warning", suggested_actions=[{"type":"second_source"}],
        evidence=[{"path":"letter"}, {"path":"https://www.wto.org/watch", "locator":"regulations:regulatory_change"},
                  {"path":"https://polymarket.com/event/a","quote":"offline snapshot"}])])
    system.emit(events.ISSUE_RAISED, {"kind":"human_check", "issue_id":issue.id})
    entry, = future(client)
    option = entry["option"]
    assert option["button_label"] == "Start qualification review" and option["approvers"] == ["Rin Abe"]
    assert option["currency"] == "JPY" and option["edition"] == "Mill" and "O/4" in entry["next_step"]
    assert "88 days" in entry["next_step"] and "increased" not in entry["next_step"]
    assert {e["source_kind"] for e in entry["evidence"]} >= {"Official publication","Supplier evidence","Offline example"}
    assert client.get("/api/setup").json()["data"]["timezone"] == "Asia/Tokyo"
    with store.session() as db:
        db.delete(db.get(m.SupplierPartMap, facts[2].id))
        db.commit()
    result = client.post(f"/api/decisions/{entry['decision_id']}/approve",
                         json={"by":"Rin Abe", "version":entry["version"]})
    assert result.status_code == 409
    revised, = future(client)
    assert revised["option"]["button_label"] == "Check applicability"
    assert revised["option"]["product_ids"] == [] and not store.all_rows(m.OutboxMessage)
