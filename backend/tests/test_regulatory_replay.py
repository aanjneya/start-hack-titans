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
    exposure = entry["option"]["exposure"]
    assert exposure["order_value"] == {"amount": 1870260, "currency": "CNY"}
    assert exposure["effective_after"] == "2026-12-01"
    gate = exposure["qualification_gate"]
    assert gate["assessment"] == "PPAP" and gate["effective"] == "2025-04-01"
    assert [a["person"] for a in gate["signers"]] == ["Daniel Okafor", "Sofia Marchetti"]
    assert "PP-07" in gate["evidence"]["path"]
    window = exposure["sample_window"]
    assert (window["as_of"], window["sample_by"], window["first_delivery"], window["days_between"]) == (
        "2026-11-21", "2026-12-01", "2026-12-14", 13)
    assert any("sampled within 10 days" in o["text"] for o in exposure["sample_offers"])
    assert exposure["previous_lead_days"] == 63 and exposure["lead_days"] == 105
    assert [(o["po"], o["line"], o["promised"], o["quantity"]) for o in exposure["orders"]] == [
        ("4500318765", 2, "2026-12-14", 23000), ("4500318781", 1, "2027-01-04", 25500),
        ("4500318786", 3, "2027-01-15", 24500)]
    grade = exposure["last_grade_change"]
    assert (grade["previous"], grade["current"]) == ("A", "CCC")
    assert {"factor": "external_politics", "before": 11.2, "after": 11.2} in grade["drivers"]
    signals = {s["factor"]: s for s in exposure["signals"]}
    assert signals["external_politics"]["sources"][0]["origin"] == "Offline example"
    assert {"weather", "telemetry", "regulatory_change"} == {d["factor"] for d in exposure["other_drivers"]}
    assert signals["trade_news"]["sources"][0]["published"] == "2026-11-21"
    assert signals["trade_news"]["sources"][0]["evidence"]["source_kind"] == "News coverage"
    assert any(e["source_kind"] == "Product BOM" for e in entry["evidence"])
    assert len(entry["sources"]) >= 2
    forecasts = client.get("/api/forecast").json()["data"]
    assert forecasts and all(f["limiting_part"] != "CMP-MAG-005" for f in forecasts)
    mails = len(store.all_rows(m.OutboxMessage))
    for idx, src in enumerate(entry["sources"][:2]):
        result = client.post(f"/api/decisions/{src['decision_id']}/approve",
                             json={"by": "Paul Winter", "option": src["option_index"]})
        assert result.status_code == (200 if idx == 0 else 409)
    review, = [i for i in store.all_rows(m.Issue) if i.impact.get("horizon") == "future"]
    assert review.impact["note"] == entry["option"]["review_brief"] and "sample terms" in review.impact["note"]
    assert review.impact["owner"] == "Paul Winter" and review.supplier_id == "SUP-0029"
    chosen = entry["sources"][0]["decision_id"]
    history = client.get(f"/api/decisions/{chosen}/history").json()["data"]["events"]
    assert {e["title"] for e in history} == {"Supplier grade changed", "Finding opened", "Decision proposed",
                                             "Action authorized", "Internal review opened"}
    opened, = [e for e in history if e["title"] == "Internal review opened"]
    assert opened["record"]["id"] == review.id and "Paul Winter" in opened["detail"]
    assert "sample terms" in opened["detail"]
    path = f"/api/decisions/{chosen}/sample-window"
    later = client.get(path, params={"requested_on": "2026-12-05"}).json()["data"]
    assert (later["sample_by"], later["days_between"]) == ("2026-12-15", -1)
    assert client.get(path, params={"requested_on": "2026-11-20"}).status_code == 422
    assert client.get(path, params={"requested_on": "9999-12-31"}).status_code == 422
    assert client.get(path, params={"requested_on": "2026-11-21", "option": 25}).status_code == 422
    assert client.get("/api/trace", params={"trace_id": opened["trace_id"]}).json()["data"][0]["data"]["issue_id"] == review.id
    assert client.get("/api/forecast").json()["data"] == forecasts and len(store.all_rows(m.OutboxMessage)) == mails

    # Opening the review did no external work. Only a second, explicit choice starts a supplier conversation.
    inquiry, = [q for q in future(client) if q["option"].get("followup_for") == chosen]
    query_id = inquiry["decision_id"]
    choice = dict(by="Paul Winter", version=inquiry["version"], option=inquiry["option_index"])
    url = f"/api/decisions/{query_id}/approve"
    assert client.post(url, json=dict(choice, by="Other buyer")).status_code == 403
    client.post(url, json=choice).raise_for_status()
    assert client.post(url, json=choice).status_code == 409
    mail, = [row for row in store.all_rows(m.OutboxMessage) if row.decision_id == query_id]
    assert mail.issue_id == review.id and mail.supplier_id == review.supplier_id
    assert "RM-" in mail.body or "CMP-MAG-005" in mail.body
    assert "4500318765/2" in mail.body and "information only" in mail.body
    answer = dict(outbox_id=mail.id, supplier_id=mail.supplier_id, body="Please see the available sample specifications.")
    response_id = client.post("/api/inbox", json=answer).json()["data"]["inbox_id"]
    receipt = next(r for r in store.all_rows(m.InboxMessage) if r.id == response_id)
    assert receipt.result == {"qualification_correspondence": {"decision_id":query_id, "review_id":review.id}}
    client.post("/api/inbox", json=answer).raise_for_status()
    assert store.all_rows(m.InboxMessage)[-1].result == {"review": f"Duplicate of inbox/{response_id}"}
    client.post("/api/inbox", json=dict(answer, supplier_id="OTHER")).raise_for_status()
    assert "review" in store.all_rows(m.InboxMessage)[-1].result
    linked = client.get(f"/api/issues/{review.id}").json()["data"]["evidence"]
    assert sum(e["path"] == f"inbox/{response_id}" for e in linked) == 1
    events = client.get(f"/api/decisions/{chosen}/history").json()["data"]["events"]
    assert sum(e["title"] == "Supplier response attached to review" for e in events) == 1
    assert sum(e["title"] == "Supplier request sent in simulation" for e in events) == 1
    assert client.get("/api/forecast").json()["data"] == forecasts and len(store.all_rows(m.OutboxMessage)) == mails + 1


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
    assert option["exposure"]["qualification_gate"] is None
    assert option["currency"] == "JPY" and option["edition"] == "Mill" and "O/4" in entry["next_step"]
    assert "88 days" in entry["next_step"] and "increased" not in entry["next_step"]
    assert {e["source_kind"] for e in entry["evidence"]} >= {"Official publication","Supplier evidence","Offline example"}
    assert client.get("/api/setup").json()["data"]["timezone"] == "Asia/Tokyo"
    competing, = store.add_all([m.SupplierPartMap(component_id="C", supplier_id="V-10", share=0.2, source="second-map")])
    system.emit(events.RISK_UPDATED, {})
    disputed, = future(client)
    assert disputed["option"]["button_label"] == "Check applicability"
    assert disputed["option"]["exposure"]["share"] is None
    assert any(e["path"] == "second-map" for e in disputed["evidence"])
    with store.session() as db:
        db.delete(db.get(m.SupplierPartMap, competing.id))
        db.commit()
    system.emit(events.RISK_UPDATED, {})
    entry, = future(client)
    conflict, = store.add_all([m.Document(kind="notice", path="clarification", supplier_id="V-9",
                              text="AX-901 is exempt from the licensing requirement.")])
    changed = client.post(f"/api/decisions/{entry['decision_id']}/approve",
                          json={"by": "Rin Abe", "version": entry["version"]})
    assert changed.status_code == 409
    disputed, = future(client)
    assert disputed["option"]["button_label"] == "Check applicability"
    assert disputed["option"]["exposure"]["uncertainties"] and not disputed["option"]["exposure"]["orders"]
    with store.session() as db:
        db.delete(db.get(m.Document, conflict.id))
        db.commit()
    system.emit(events.RISK_UPDATED, {})
    entry, = future(client)
    with store.session() as db:
        db.delete(db.get(m.SupplierPartMap, facts[2].id))
        db.commit()
    result = client.post(f"/api/decisions/{entry['decision_id']}/approve",
                         json={"by":"Rin Abe", "version":entry["version"]})
    assert result.status_code == 409
    revised, = future(client)
    assert revised["option"]["button_label"] == "Check applicability"
    assert revised["option"]["product_ids"] == [] and not store.all_rows(m.OutboxMessage)


def test_supplier_country_warning_routes_without_customer_order_evidence():
    client = TestClient(app)
    issue, = store.add_all([m.Issue(type="high_risk_rating", raised_by="risk", kind="copies_at_risk",
        subject_type="supplier", subject_id="V", supplier_id="V", fingerprint="supplier/V",
        evidence=[{"path": "https://www.wto.org/customs", "locator": "regulations:regulatory_change"}],
        suggested_actions=[{"type": "human_review", "label": "Verify compliance impact"}])])
    system.emit(events.ISSUE_RAISED, {"issue_id": issue.id, "kind": issue.kind})
    entry, = future(client)
    assert entry["option"]["button_label"] == "Check applicability"
    assert entry["option"]["exposure"]["orders"] == [] and entry["option"]["copies_gained"] is None
    with store.session() as db:
        finding = db.get(m.Issue, issue.id)
        finding.suggested_actions = [{"type": "email", "label": "Verify shipment location"}]
        db.add(finding)
        db.commit()
    system.emit(events.RISK_UPDATED, {})
    assert future(client) == []


def test_portal_manifest_advances_only_after_healthy_processing(tmp_path, monkeypatch):
    """Drive the Watcher entry point, swapping only its portal transport and live gate."""
    from dataclasses import replace
    import hashlib
    from backend.agents import watcher
    config_for_watch = replace(config.get_settings(), mock_mode=False, soony_api_token="fixture",
                               soony_base_url="https://fixture.invalid", portal_mirror=tmp_path / "mirror")
    monkeypatch.setattr(watcher, "get_settings", lambda: config_for_watch)
    path = "erp/packout_log.csv"
    data = b"date,site,product_id,line,units_packed\n2026-11-02,Hamburg,FG-X,L1,5\n"
    manifest = [{"path": path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "url": path}]
    monkeypatch.setattr(watcher.Watcher, "list_portal", lambda self: manifest)
    monkeypatch.setattr(watcher.Watcher, "_get", lambda self, url: data)
    attempts = []
    def fail_once(event):
        attempts.append(event.name)
        if len(attempts) == 1:
            raise RuntimeError("temporary downstream outage")
    system.bus().subscribe(events.TRUTH_UPDATED, "fixture_processor", fail_once)
    client = TestClient(app)
    first = client.post("/api/watch/check").json()["data"]
    assert first["status"] == "partial" and not store.all_rows(m.PortalFile)
    assert client.get("/api/health").json()["data"]["lastDrop"]["failures"][0]["agent"] == "fixture_processor"
    second = client.post("/api/watch/check").json()["data"]
    assert second["status"] == "completed" and len(attempts) == 2
    assert len(store.all_rows(m.PortalFile)) == 1 and len(store.all_rows(m.Packout)) == 1
    assert client.post("/api/watch/check").json()["data"] == {"added": 0, "changed": 0, "removed": 0}


def test_unpriced_or_mixed_currency_lines_have_no_aggregate_value():
    from backend.agents.planner_regulatory import order_value

    def line(unit_price, currency):
        return m.PurchaseOrder(po_number="P", quantity=3, unit_price=unit_price, currency=currency)
    assert order_value([line(0.1, "JPY"), line(1, "JPY")]) == {"amount": 3.3, "currency": "JPY"}
    assert order_value([]) is None
    assert order_value([line(1, "JPY"), line(1, "EUR")]) is None
    assert order_value([line(None, "EUR")]) is None


def test_sample_scenario_requires_clear_terms_and_a_future_order():
    from backend.agents.planner_regulatory import sample_window
    as_of = date(2026, 6, 20)
    order = m.PurchaseOrder(po_number="A", promised_date=date(2026, 7, 1))
    ten = {"days": 10, "source": "supplier notice"}
    assert sample_window([ten], as_of, [order])["days_between"] == 1
    assert sample_window([ten], date(2026, 7, 2), [order]) is None
    assert sample_window([ten, dict(ten, days=5)], as_of, [order]) is None
    assert sample_window([dict(source="10 working days")], as_of, [order]) is None
    assert sample_window([ten], as_of, []) is None


def test_qualification_deduplication_follows_the_component_inside_the_assembly():
    from backend.agents.planner_queue import physical_key
    def opt(component, ref):
        return dict(description="future supply review", horizon="future", exposure={"component": component},
                    actions=[dict(type="human_review", supplier_id="V", part="A", reference=ref)])
    old = opt("hinge", "qualification/V/A")
    assert physical_key(old) == physical_key(opt("hinge", "qualification/V/A/hinge"))
    assert physical_key(old) != physical_key(opt("spring", "qualification/V/A/spring"))


def test_market_warning_opens_a_targeted_check_without_promising_order_impact():
    client = TestClient(app)
    proof = dict(path="https://polymarket.com/event/trade-rule", locator="polymarket:external_politics",
                 quote="Yes=74.0%, risk_implied=74.0% (live Polymarket Gamma API)")
    store.add_all([
        m.Supplier(supplier_id="VN-X", name="Viet Manufacturing", country="VN"),
        m.PurchaseOrder(supplier_id="VN-X", material_id="A1", po_number="P1", po_line=1, status="open",
                        promised_date=date(2027, 1, 10), source="open-orders"),
        m.PurchaseOrder(supplier_id="VN-X", material_id="A1", po_number="P-old", po_line=2, status="closed"),
        m.PurchaseOrder(supplier_id="OTHER", material_id="A1", po_number="P-other", status="open"),
        m.RiskScore(entity_type="supplier", entity_id="VN-X", grade=5, rating="B", reasons=[
            dict(factor="external_politics", text="Country trade rule market", points=11.8, evidence=[proof])]),
    ])
    finding, = store.add_all([m.Issue(type="high_risk_rating", raised_by="risk", kind="copies_at_risk",
        subject_type="supplier", subject_id="VN-X", supplier_id="VN-X", fingerprint="watch/VN-X",
        evidence=[proof], suggested_actions=[{"type": "safety_stock", "label": "Verify the supply chain"}])])
    system.emit(events.ISSUE_RAISED, {"issue_id": finding.id, "kind": finding.kind})
    entry, = future(client)
    choice = entry["option"]
    assert choice["button_label"] == "Check applicability" and choice["copies_gained"] is None
    assert choice["approval_value_eur"] == 0 and not choice["exposure"]["orders"]
    assert choice["exposure"]["verification_orders"] == [
        dict(po="P1", line=1, material="A1", promised="2027-01-10")]
    assert choice["exposure"]["signals"][0]["sources"][0]["origin"] == "Live at collection"
    assert any(e["path"] == "open-orders" for e in entry["evidence"])
    assert not store.all_rows(m.Forecast) and not store.all_rows(m.OutboxMessage)
