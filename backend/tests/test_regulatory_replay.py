"""Branch acceptance: isolated restart/retry and customer-specific procurement decisions."""
from datetime import date
from datetime import datetime, timezone
import json
import os
import sqlite3
import subprocess
import sys

from fastapi.testclient import TestClient
from sqlmodel import select

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
    ledger = client.get("/api/trace", params={"run_id":res["run_id"], "limit":800}).json()["data"]
    assert set(system.agent_names()) <= {step["agent"] for step in ledger}
    assert any(step["agent"] == "watcher" and step["action"] == "replay_manifest" for step in ledger)
    change = entry["latest_grade_change"]
    assert (change["run_id"], change["previous"], change["current"]) == (res["run_id"], "A", "CCC")
    proof = client.get("/api/trace", params={"trace_id":change["trace_id"]}).json()["data"]
    assert proof[0]["agent"] == "risk" and proof[0]["data"]["entity_id"] == "SUP-0029"
    exposure = entry["option"]["exposure"]
    assert exposure["order_value"] == {"amount": 1870260, "currency": "CNY"}
    history = exposure["delivery_history"]
    assert (history["material"], history["site"], history["on_time"], history["count"]) == ("CMP-GAM-007", "DC-HAM", 4, 4)
    assert history["last"] == "2026-11-11" and all(r["source"].endswith(".csv") for r in history["recent"])
    assert exposure["effective_after"] == "2026-12-01"
    assert exposure["packing_closes_before_notice"] == "2026-11-22"
    gate = exposure["qualification_gate"]
    assert gate["assessment"] == "PPAP" and gate["effective"] == "2025-04-01"
    assert [a["person"] for a in gate["signers"]] == ["Daniel Okafor", "Sofia Marchetti"]
    assert "PP-07" in gate["evidence"]["path"]
    card = client.get(f"/api/decisions/{entry['decision_id']}").json()["data"]
    index = next(i for i, e in enumerate(card["evidence"]) if e.get("locator") == "delivery window")
    passage = client.get(f"/api/decisions/{card['id']}/evidence/{index}").json()["data"]
    assert "move out by 6 weeks" in passage["evidence"]["quote"]
    assert "Printed parts without magnets are not affected" in passage["excerpt"]
    window = exposure["sample_window"]
    assert window["request_by_for_delivery"] == "2026-12-04"
    assert (window["as_of"], window["sample_by"], window["first_delivery"], window["days_between"]) == (
        "2026-11-21", "2026-12-01", "2026-12-14", 13)
    assert any("sampled within 10 days" in o["text"] for o in exposure["sample_offers"])
    assert exposure["previous_lead_days"] == 63 and exposure["lead_days"] == 105
    assert [(o["po"], o["line"], o["promised"], o["quantity"]) for o in exposure["orders"]] == [
        ("4500318765", 2, "2026-12-14", 23000), ("4500318781", 1, "2027-01-04", 25500),
        ("4500318786", 3, "2027-01-15", 24500)]
    grade = exposure["last_grade_change"]
    assert [(n["tier"], n["id"], n["previous"], n["rating"]) for n in exposure["rating_path"]] == [
        ("supplier", "SUP-0029", "A", "CCC"), ("part", "CMP-MAG-005", "AA", "CCC"),
        ("part", "CMP-GAM-007", "AA", "B"), ("product", "FG-GH6-CE", "BBB", "CCC")]
    assert (grade["previous"], grade["current"]) == ("A", "CCC")
    assert {"factor": "external_politics", "before": 11.2, "after": 11.2} in grade["drivers"]
    signals = {s["factor"]: s for s in exposure["signals"]}
    assert signals["external_politics"]["sources"][0]["origin"] == "Offline example"
    assert set(signals) == {"trade_news", "external_politics", "weather", "telemetry", "regulatory_change"}
    assert len(signals["external_politics"]["sources"]) == 2
    basis = exposure["score_basis"]
    assert basis["external_raw"] > basis["external"] == basis["external_cap"] == 45
    assert basis["total"] == round(basis["internal"] + basis["external"], 1)
    assert signals["telemetry"]["sources"][0]["origin"] == "Customer shipment feed"
    assert signals["telemetry"]["sources"][0]["scope"].startswith("Shipment SHP-")
    assert signals["telemetry"]["sources"][0]["observed_at"]
    assert signals["weather"]["sources"][0]["origin"] == signals["regulatory_change"]["sources"][0]["origin"] == "Offline example"
    assert not exposure["other_drivers"]
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


def test_archived_decision_source_does_not_borrow_other_or_changed_documents():
    client = TestClient(app)
    card, = store.add_all([m.DecisionCard(evidence=[dict(path="news_feed", locator="one story",
                                        quote="Production moves to July.")])])
    docs = store.add_all([
        m.Document(path="news_feed#July", title="Order update", text="Latest notice. Production moves to July. Contact Quality."),
        m.Document(path="news_feed#other", title="Other article", text="Production moves to August."),
        m.Document(path="newsXfeed#August", title="Different feed", text="Production moves to July. Different article.")])
    url = f"/api/decisions/{card.id}/evidence/"
    passage = client.get(url + "0").json()["data"]
    assert passage["title"] == "Order update" and "Contact Quality" in passage["excerpt"]
    with store.session() as db:
        row = db.get(m.Document, docs[0].id)
        row.text = "This delivery was cancelled."
        db.add(row)
        db.commit()
    revised = client.get(url + "0").json()["data"]
    assert revised["excerpt"] is None and revised["evidence"]["quote"] == passage["evidence"]["quote"]
    assert client.get(url + "-1").status_code == client.get(url + "1").status_code == 404


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


def test_sample_inquiry_requires_current_terms_for_the_same_article():
    from backend.agents.planner_regulatory import scoped_samples
    targets = {"AX-901"}
    notice = m.Document(path="customer/notice", text="AX-901 requires a licence. Alternative hinges can be sampled within 8 days.")
    assert scoped_samples([notice], targets, {"CL-77"}, "Door hinge")[0]["days"] == 8
    notice.text += " CL-77 is a separate article."
    assert scoped_samples([notice], targets, {"CL-77"}, "Door hinge") == []
    notice.text += " AX-901 can be sampled within 4 working days."
    scoped, = scoped_samples([notice], targets, {"CL-77"}, "Door hinge")
    assert "AX-901" in scoped["text"] and "days" not in scoped

    client = TestClient(app)
    for drop in list_drops():
        system.replay_step()
        if any("components_delta" in name and "PackRight" in name for name in drop.added + drop.changed):
            break
    entry, = [q for q in future(client) if q["option"]["button_label"] == "Start qualification review"]
    client.post(f"/api/decisions/{entry['decision_id']}/approve",
                json={"by": "Paul Winter", "version": entry["version"]}).raise_for_status()
    inquiry, = [q for q in future(client) if q["option"].get("followup_for") == entry["decision_id"]]
    original = entry["option"]["exposure"]["sample_offers"][0]
    with store.session() as db:
        doc = db.exec(select(m.Document).where(m.Document.path == original["source"])).one()
        doc.text = " ".join(doc.text.split()).replace(original["text"], "PR-CC-0312 clear cases can be sampled within 2 days.")
        db.add(doc)
        db.commit()
    before = len(store.all_rows(m.OutboxMessage))
    stale = client.post(f"/api/decisions/{inquiry['decision_id']}/approve",
                        json={"by": "Paul Winter", "version": inquiry["version"]})
    assert stale.status_code == 409 and len(store.all_rows(m.OutboxMessage)) == before
    assert not any(q["option"].get("followup_for") == entry["decision_id"] for q in future(client))
    assert client.get(f"/api/decisions/{inquiry['decision_id']}").json()["data"]["status"] == "outdated"


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
    assert [(n["tier"], n["id"]) for n in choice["exposure"]["rating_path"]] == [("supplier", "VN-X")]
    assert any(e["path"] == "open-orders" for e in entry["evidence"])
    assert not store.all_rows(m.Forecast) and not store.all_rows(m.OutboxMessage)


def test_market_price_can_open_and_withdraw_a_buyer_check_without_a_portal_drop(monkeypatch):
    from backend.agents import risk_signals
    from backend.agents.risk import RiskRater

    client = TestClient(app)
    supplier = "CA-N"
    store.add_all([
        m.Supplier(supplier_id=supplier, name="Northshore Components", country="CA", status="at risk",
                   qualification_valid_until=date(2020, 1, 1), source="vendor-master"),
        m.PurchaseOrder(supplier_id=supplier, material_id="G2", po_number="KIT-9", po_line=2,
                        promised_date=date(2027, 4, 1), status="awaiting_approval", buyer="Rei Ito", source="order-register"),
        m.Document(kind="org_chart", path="chart", text="Effective 2025-01-01\nRei Ito\nProcurement Buyer"),
        *[m.Shipment(shipment_id=f"ship-{i}", supplier_id=supplier, mode="sea", status="delayed",
                     updated_at=datetime(2026, 11, 21, tzinfo=timezone.utc), source="shipping") for i in (1, 2)],
        *[m.Issue(type="quality_check", raised_by="reconciler", supplier_id=supplier, severity=4,
                 kind="human_check", fingerprint=f"qc-{i}") for i in (1, 2)],
    ])
    # Exercise the real binary-market parser and rating logic; only its HTTP payload is supplied by the test.
    class Market(risk_signals.PolymarketProvider):
        def fetch(self, **context):
            return self._fetch_live(context["countries"], context["reference_now"])
    class Weather:
        name = "weather"
        available = True
        def fetch(self, **context):
            if not self.available:
                raise risk_signals.RiskFeedUnavailable("Weather service offline")
            return []
    weather = Weather()
    monkeypatch.setattr(RiskRater, "providers", (Market(), weather))
    odds = {"yes": 0.20, "updated":"2026-11-21T14:00:00Z"}
    def market_response(url, params):
        if odds["yes"] is None:
            return None
        return [dict(slug="canada-export", markets=[dict(id="canada-2027", question="Will Canada increase export tariffs?",
            updatedAt=odds["updated"], outcomes=["No", "Yes"], outcomePrices=[1 - odds["yes"], odds["yes"]])])]
    monkeypatch.setattr(risk_signals, "_cached_get_json", market_response)
    def check():
        return client.post("/api/risk/refresh").json()["data"]
    assert check()["status"] == "completed" and not future(client)
    assert client.get("/api/risk?current=true").json()["data"][0]["rating"] == "BBB"
    odds["yes"] = .85
    weather.available = False
    run = check()
    assert run["status"] == "partial"
    partial = client.get("/api/health").json()["data"]["lastRiskCheck"]
    assert partial["grades_changed"] == 1 and partial["unavailable"]["weather"]["last_known_at"]
    entry, = future(client)
    assert (entry["latest_grade_change"]["run_id"], entry["latest_grade_change"]["current"]) == (run["run_id"], "B")
    choice = entry["option"]
    assert (choice["button_label"], choice["approval_value_eur"], choice["assignments"][0]["person"]) == ("Check applicability", 0, "Rei Ito")
    assert store.find_rows(m.Issue, type="high_risk_rating")[0].kind == "human_check"
    exposure = choice["exposure"]
    assert not exposure["orders"] and exposure["verification_orders"][0]["po"] == "KIT-9"
    market = next(s for group in exposure["signals"] for s in group["sources"] if s["provider"] == "polymarket")["market"]
    assert (market["previous_yes_pct"], market["yes_pct"]) == (20, 85)
    odds["updated"] = "2026-11-21T14:10:00Z"
    weather.available = True
    assert check()["status"] == "completed" and future(client)[0]["decision_id"] == entry["decision_id"]
    restart()
    odds["yes"] = None
    weather.available = False
    failed = check()
    assert failed["status"] == "partial" and failed["failures"][0]["event"] == events.RISK_REFRESH_REQUESTED
    health = client.get("/api/health").json()["data"]["lastRiskCheck"]
    assert health["unavailable"]["polymarket"]["last_known_at"] and health["grades_changed"] is None
    assert future(client)[0]["version"] == entry["version"]
    odds["yes"] = .20
    weather.available = True
    assert check()["status"] == "completed" and not future(client)
    history = client.get(f"/api/decisions/{entry['decision_id']}/history").json()["data"]["events"]
    withdrawn, = [e for e in history if e["title"] == "Pending review withdrawn"]
    assert "B to BBB" in withdrawn["detail"] and withdrawn["trace_id"]
    stale = client.post(f"/api/decisions/{entry['decision_id']}/approve", json={"by": "Rei Ito", "version": entry["version"]})
    assert stale.status_code == 409
    assert not store.all_rows(m.Forecast) and not store.all_rows(m.OutboxMessage) and not store.all_rows(m.DropRecord)


def test_delivery_record_counts_only_completed_lines_before_the_warning():
    from backend.agents.planner_regulatory import delivery_history

    def line(number, status, arrived, site="H1"):
        return m.PurchaseOrder(po_number=number, po_line=1, plant=site, supplier_id="V", material_id="BOX",
                               promised_date=date(2026, 5, 1), actual_delivery_date=arrived, status=status, source="ERP")
    actual = date(2026, 5, 1)
    store.add_all([line("finished", "delivered", actual), line("later", "closed", date(2026, 5, 3)),
                   line("partial", "partially received", actual), line("unknown", "", actual),
                   line("cancelled", "cancelled", actual), line("other-site", "received", actual, "H2")])
    result = delivery_history("V", "BOX", "H1", date(2026, 5, 2))
    assert result["count"] == result["on_time"] == 1 and result["recent"][0]["po"] == "finished"


def test_future_exposure_counts_physical_po_lines_once_and_withholds_disputed_terms():
    from backend.agents.planner_regulatory import open_orders, order_value

    at = date(2026, 11, 21)
    cutoff = date(2026, 12, 1)
    def line(number, source, **overrides):
        fields = dict(supplier_id="BE-S", material_id="A-5", po_number=number, po_line=2, status="open",
                      promised_date=date(2027, 1, 3), quantity=20, unit_price=3, currency="CHF", source=source)
        return m.PurchaseOrder(**(fields | overrides))
    store.add_all([line("PK-1", "ERP"), line("PK-1", "register"),
                   line("PK-2", "ERP"), line("PK-2", "ERP", status="closed", promised_date=None),
                   line("PK-3", "ERP"), line("PK-3", "register", status="closed")])
    def read():
        return open_orders("BE-S", at, part="A-5", after=cutoff)
    result = read()
    assert [p.po_number for p in result.lines] == ["PK-1"] and order_value(result.lines) == {"amount":60, "currency":"CHF"}
    assert any("PK-3/2" in n and "status" in n for n in result.notes)
    store.add_all([line("PK-1", "register", quantity=24), line("PK-4", "register", po_line=None)])
    disputed = read()
    assert len(disputed.lines) == 1 and disputed.lines[0].quantity is None and order_value(disputed.lines) is None
    assert {(e["path"], e["quote"]) for e in disputed.proof if e["locator"] == "PK-1/2"} == {
        ("ERP", "quantity: 20.0"), ("register", "quantity: 24.0")}
    assert any("PK-4/?" in n for n in disputed.notes)


def test_restart_does_not_open_a_future_review_whose_finding_was_cleared():
    issue, = store.add_all([m.Issue(raised_by="risk", fingerprint="cleared-market", type="high_risk_rating",
                        kind="human_check", status="resolved", supplier_id="S", subject_type="supplier", subject_id="S")])
    card, = store.add_all([m.DecisionCard(created_by="planner", title="Check supply exposure", issue_ids=[issue.id],
        options=[dict(horizon="future", description="future supply review", product_id="", available=True,
                      approvers=["Rei Ito"], actions=[dict(type="human_review", supplier_id="S", product_id="",
                      part="", reference="applicability/S/S", copies_at_risk=None)])],
        chosen_option=0, status="approved", decided_by="Rei Ito")])
    restart()
    system.bus().run_until_idle()
    assert store.find_rows(m.DecisionCard, id=card.id)[0].status == "outdated"
    assert not store.find_rows(m.Issue, type="recovery_review") and not store.all_rows(m.OutboxMessage)


def test_public_sector_rule_reaches_only_the_supplier_articles_supported_by_the_customer(monkeypatch):
    from backend.agents import risk_signals as feeds
    from backend.agents.risk import RiskRater

    client = TestClient(app)
    title = "New export controls on advanced semiconductors"
    def json_feed(url, params=None):
        return {"results":[dict(title=title, type="Rule", html_url="https://www.federalregister.gov/documents/example")]}
    monkeypatch.setattr(feeds, "_cached_get_json", json_feed)
    monkeypatch.setattr(feeds, "_cached_get_text", lambda *a, **k: "<rss><channel/></rss>")
    _, public = feeds.RegulationChangeProvider()._fetch_live({"BE", "JP"})
    rule, = public
    assert rule.countries == []  # The publication itself names an industry, with no country reference.
    assert feeds.official_regulation("Rules for semiconductors from Portugal", {"BE"}) is None
    assert feeds.official_regulation("Annual report on the federal workforce", {"BE"}) is None
    class Recorded:
        name = "fixtures"
        def fetch(self, **kw):
            return [rule, feeds.ExternalSignal("weather", "weather", "Forecast at port", 65, "Adverse weather",
                                              countries=["BE"], ports=["BEANR"], source_url="test-weather")]
    monkeypatch.setattr(RiskRater, "providers", (Recorded(),))
    store.add_all([
        m.Material(component_id="D-12", description="Memory die", category="Semiconductors", source="material-master"),
        m.Material(component_id="T-9", description="Casting", category="Foundry", source="material-master"),
        m.SupplierPartMap(supplier_id="BE-A", component_id="D-12", share=1, source="allocations"),
        m.SupplierPartMap(supplier_id="BE-A", component_id="T-9", share=.5, source="allocations"),
        m.SupplierPartMap(supplier_id="BE-B", component_id="T-9", share=.5, source="allocations"),
        m.Document(path="buyers", kind="org_chart", text="2026-01-01\nLin Park\nBuyer"),
        m.PurchaseOrder(supplier_id="BE-A", po_number="DIE-PO", po_line=1, material_id="D-12", status="open",
                        promised_date=date(2027, 1, 10), buyer="Lin Park", source="purchasing"),
    ])
    for supplier in ("BE-A", "BE-B"):
        store.add_all([m.Supplier(supplier_id=supplier, country="BE", name=supplier, status="blocked",
                                 qualification_valid_until=date(2025, 1, 1), source="suppliers"),
                      m.PurchaseOrder(supplier_id=supplier, po_number=supplier+"-CAST", po_line=1, material_id="T-9",
                                      status="awaiting_approval", buyer="Lin Park", source="purchasing"),
                      *[m.Shipment(supplier_id=supplier, shipment_id=supplier+str(n), origin="BEANR", mode="sea", status="delayed",
                                   updated_at=datetime(2026, 11, 21, tzinfo=timezone.utc), source="transit") for n in (1,2)],
                      *[m.Issue(raised_by="reconciler", type="quality", kind="human_check", supplier_id=supplier, severity=4,
                                fingerprint=supplier+str(n)) for n in (1,2)]])
    assert client.post("/api/risk/refresh").json()["data"]["failures"] == []
    entry, = [q for q in future(client) if q["option"]["actions"][0]["supplier_id"] == "BE-A"]
    opt = entry["option"]
    assert opt["button_label"] == "Check applicability" and opt["approvers"] == ["Lin Park"]
    exposure = opt["exposure"]
    assert not exposure["orders"] and [o["po"] for o in exposure["verification_orders"]] == ["DIE-PO"]
    official, = next(s["sources"] for s in exposure["signals"] if s["factor"] == "regulatory_change")
    assert official["evidence"]["source_kind"] == "Official publication"
    match, = official["material_matches"]
    assert (match["term"], match["part"]) == ("Semiconductors", "D-12")
    assert {e["path"] for e in match["evidence"]} == {"allocations", "material-master"}
    assert not any(q["option"]["actions"][0]["supplier_id"] == "BE-B" for q in future(client))
    assert not store.all_rows(m.Forecast) and not store.all_rows(m.OutboxMessage)


def test_downstream_failure_after_risk_refresh_remains_visible_after_restart():
    client = TestClient(app)
    store.add_all([m.Supplier(supplier_id="FIX", name="Fixture")])
    def interrupted(event):
        raise RuntimeError("test interruption while updating the decision")
    system.bus().subscribe(events.RISK_UPDATED, "test-decision-subscriber", interrupted)
    attempt = client.post("/api/risk/refresh").json()["data"]
    assert attempt["status"] == "partial"
    restart()
    health = client.get("/api/health").json()["data"]
    assert health["status"] == "degraded" and health["lastRiskCheck"]["failures"] == attempt["failures"]
    assert client.post("/api/risk/refresh").json()["data"]["status"] == "completed"
    assert client.get("/api/health").json()["data"]["status"] == "ok"
