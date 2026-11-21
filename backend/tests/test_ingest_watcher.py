"""Agent 1 (Watcher) + agent 2 (Ingest & Normalizer) on the real portal data."""
import hashlib
import json

import pytest
from sqlmodel import select

from backend import system
from backend.core import config
from backend.core.models import (
    Alias, AgentLog, ColumnMapping, Conflict, EngineeringChange, FieldChange, GoodsReceipt, Issue, IssueKind,
    LaunchStatus, Material, OrderConfirmation, Product, PurchaseOrder, Shipment, ShipmentPO, Supplier,
    SupplierPartMap,
)
from backend.core.replay import list_drops
from backend.core.store import add_all, all_rows, find_rows, query, session


def _errors():
    with session() as s:
        return [e.detail for e in s.exec(select(AgentLog).where(AgentLog.agent == "ingest",
                                                                AgentLog.action == "error")).all()]


@pytest.fixture
def full():
    return system.run_full(label="test full")


def test_full_run_fills_every_table_without_errors(full):
    assert _errors() == []
    for model, minimum in [(PurchaseOrder, 400), (GoodsReceipt, 300), (Shipment, 30), (OrderConfirmation, 250),
                           (Material, 10), (Supplier, 6), (SupplierPartMap, 10), (LaunchStatus, 2)]:
        assert len(all_rows(model)) >= minimum, model.__name__


def test_legacy_sap_orders_are_normalized(full):
    legacy = [p for p in all_rows(PurchaseOrder) if p.source.endswith("legacy_2024-2025H1.csv")]
    assert legacy
    po = legacy[0]
    assert po.supplier_id.startswith("SUP-")  # LIFNR vendor number -> our id
    assert po.po_line == 1  # SAP item 00010 -> line 1
    assert 0 < po.unit_price < 100  # decimal comma "1,5200" -> 1.52
    assert po.order_date and po.requested_date and po.requested_date.month in range(1, 13)  # US dates


def test_documents_confirmations_aliases_and_statuses(full):
    rev_c = [oc for oc in all_rows(OrderConfirmation) if oc.material_id == "CMP-GAM-004" and oc.revision == "C"]
    assert rev_c, "revision from the confirmation notes"
    parts = {a.alias: a.canonical_id for a in all_rows(Alias) if a.kind == "part"}
    assert parts.get("PR-CC-0312") == "CMP-GAM-003"  # PackRight's own part number learned
    tracker = [s for s in all_rows(Shipment) if s.source.endswith(".xlsx#sheet1.xml")]
    assert tracker and all(s.supplier_id.startswith("SUP-") for s in tracker)  # names -> ids
    statuses = {s.status for s in all_rows(Shipment)}
    assert "in_transit" in statuses and "in transit" not in statuses  # one status list
    assert all_rows(ShipmentPO)
    std = next(r for r in all_rows(LaunchStatus) if r.product_id == "FG-GH6-STD")
    assert std.still_to_pack and std.rate_per_hour  # "86.000" -> 86000


def test_same_drop_twice_changes_nothing(full):
    before = {m.__name__: len(all_rows(m)) for m in (PurchaseOrder, OrderConfirmation, Shipment, FieldChange)}
    system.run_full(label="again")
    after = {m.__name__: len(all_rows(m)) for m in (PurchaseOrder, OrderConfirmation, Shipment, FieldChange)}
    assert before == after
    assert all(m.method in {"glossary", "unmapped", "llm"} for m in all_rows(ColumnMapping))


def test_replay_real_drops_without_code_change():
    drops = list_drops()
    assert len(drops) >= 3
    system.replay_step()  # baseline
    assert find_rows(Material, component_id="CMP-GAM-004")[0].drawing_rev == "B"
    assert find_rows(Product, product_id="FG-GH6-STD")[0].day_one_qty is None
    system.replay_step()  # 16:08 drop: revisions, renamed products columns, launch status
    assert find_rows(Material, component_id="CMP-GAM-004")[0].drawing_rev == "C"
    assert len(find_rows(Product, product_id="FG-GH6-STD")) == 1  # updated, not duplicated
    assert find_rows(Product, product_id="FG-GH6-STD")[0].day_one_qty == 640000
    assert find_rows(EngineeringChange, change_id="ECN-2026-118")
    with session() as s:
        rev_change = s.exec(select(FieldChange).where(FieldChange.field == "drawing_rev")).all()
    assert rev_change and rev_change[0].old_value == "B" and rev_change[0].new_value == "C"
    system.replay_step()  # 17:23 drop: export ban, supplier status
    sup = find_rows(Supplier, supplier_id="SUP-0029")[0]
    assert sup.status == "at risk"
    with session() as s:
        assert s.exec(select(Conflict).where(Conflict.entity_id == "SUP-0029", Conflict.field == "status")).first()
    assert _errors() == []


def test_supplier_reply_fills_missing_revision(full):
    oc = add_all([OrderConfirmation(confirmation_id="OA-TEST", po_number="4500999999", po_line=1,
                                    supplier_id="SUP-0028", material_id="CMP-GAM-004", quantity=20000,
                                    source="test")])[0]
    with session() as s:
        issue = Issue(fingerprint="t1", type="missing_revision", kind=IssueKind.MISSING_INFO,
                      po_number="4500999999", part_id="CMP-GAM-004", supplier_id="SUP-0028", summary="rev?")
        s.add(issue)
        s.commit()
        s.refresh(issue)
    system.receive_reply(supplier_id="SUP-0028", issue_id=issue.id,
                         body="Dzień dobry, druk ekspresowy wg rev. C. / Printed to artwork rev. C.")
    assert find_rows(OrderConfirmation, id=oc.id)[0].revision == "C"
    with session() as s:
        change = s.exec(select(FieldChange).where(FieldChange.row_id == oc.id)).one()
    assert change.agent == "ingest" and change.issue_id == issue.id and change.source.startswith("inbox/")


def test_watcher_live_diff_with_fake_portal(monkeypatch, tmp_path):
    portal = {"soony/master/sites.csv": b"site_id,name\nDC-HAM,Hamburg\n",
              "soony/erp/packout_log.csv": b"date,site,product_id,line,units_packed\n2026-11-02,DC-HAM,FG-X,L1,5\n"}

    def listing():
        return [{"path": p, "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest(), "url": p}
                for p, b in portal.items()]

    monkeypatch.setenv("MOCK_MODE", "false")
    monkeypatch.setenv("SOONY_API_TOKEN", "test")
    monkeypatch.setenv("SOONY_BASE_URL", "https://portal.test")
    monkeypatch.setenv("PORTAL_MIRROR", str(tmp_path / "mirror"))
    config.reset_settings()
    from backend.agents.watcher import Watcher

    monkeypatch.setattr(Watcher, "list_portal", lambda self: listing())
    monkeypatch.setattr(Watcher, "_get", lambda self, url: portal[url])
    first = system.watch_once()
    assert first["added"] == 2 and (tmp_path / "mirror" / "soony/erp/packout_log.csv").exists()
    assert system.watch_once() == {"added": 0, "changed": 0, "removed": 0}
    portal["soony/erp/packout_log.csv"] += b"2026-11-03,DC-HAM,FG-X,L1,7\n"
    second = system.watch_once()
    assert second["changed"] == 1 and second["added"] == 0
    monkeypatch.setenv("MOCK_MODE", "true")
    config.reset_settings()
    assert system.watch_once() == {"skipped": True}
