"""Tests for Agent 3 (Reconciler & Gate): unit tests for T3.0-T3.11 + real 3-drop replay."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlmodel import select

from backend import system
from backend.agents.reconciler import ReconcilerGate
from backend.core import events
from backend.core.coordinator import Coordinator
from backend.core.models import (
    AgentLog,
    BomLine,
    Conflict,
    Document,
    EngineeringChange,
    GoodsReceipt,
    Issue,
    IssueKind,
    LaunchStatus,
    Material,
    OrderConfirmation,
    Product,
    PurchaseOrder,
    RiskScore,
    Severity,
    Shipment,
    ShipmentMaterial,
    ShipmentPO,
    StockLot,
    Supplier,
    SupplierPartMap,
)
from backend.core.replay import list_drops
from backend.core.store import add_all, all_rows, find_rows, session, update_row


def _reconciler_issues(status: str | None = None) -> list[Issue]:
    with session() as s:
        stmt = select(Issue).where(Issue.raised_by == "reconciler")
        if status is not None:
            stmt = stmt.where(Issue.status == status)
        return list(s.exec(stmt).all())


def _reconciler_errors() -> list[str]:
    with session() as s:
        return [
            e.detail
            for e in s.exec(
                select(AgentLog).where(AgentLog.agent == "reconciler", AgentLog.action == "error")
            ).all()
        ]


def _seed_minimal_launch() -> None:
    """Seed a clean minimal launch dataset for isolated rule tests."""
    add_all([
        Product(product_id="FG-GH6-STD", name="GH6 Std", launch_date=date(2026, 11, 22),
                day_one_qty=640000, source="soony/master/products.csv"),
        LaunchStatus(product_id="FG-GH6-STD", edition="Standard",
                     as_of=datetime(2026, 11, 21, 13, 0, tzinfo=timezone.utc),
                     day_one_demand=640000, packed=554000, still_to_pack=86000,
                     rate_per_hour=6000.0, line_status="running", source="soony/context/launch_status.xlsx"),
        Material(component_id="CMP-GAM-004", description="Cover Sleeve GH6", critical=True,
                 drawing_rev="C", source="soony/master/components.csv"),
        BomLine(parent_id="FG-GH6-STD", child_id="CMP-GAM-004", qty_per=1.0, source="soony/master/bom.csv"),
        Supplier(supplier_id="SUP-0028", name="Karton Nowak", country="PL", status="active",
                 qualification_status="qualified", bank_iban="PL61109010140000071219812874",
                 source="suppliers/SUP-0028/profile.csv"),
    ])


# ---------------------------------------------------------------------------
# 1. Real 3-drop replay (T3.10 + T3.0 Finding Format compliance)
# ---------------------------------------------------------------------------
def test_replay_all_real_drops_without_code_change():
    """Replay 14:15 -> 16:08 -> 17:23 and verify key findings appear on the exact drop they belong to."""
    drops = list_drops()
    assert len(drops) >= 3

    # Drop 0 (baseline 14:15)
    system.replay_step()
    assert _reconciler_errors() == []
    drop0 = _reconciler_issues(status="open")
    by_type0 = {i.type for i in drop0}
    assert "tracker_mismatch" in by_type0
    assert any(i.shipment_id == "SHP-2026-04276" and i.kind == IssueKind.COPIES_AT_RISK for i in drop0)
    assert "missing_from_tracker" in by_type0
    assert "stale_feed" in by_type0
    assert "po_stuck_approval" in by_type0
    assert "quality_hold_failed_aql" in by_type0
    assert "quantity_jump" in by_type0
    assert "rev_blocked" not in by_type0  # ECN-2026-118 has not arrived yet
    assert "single_source_export_ban" not in by_type0  # 17:23 magnet drop has not arrived yet

    # Drop 1 (16:08: ECN-2026-118 rev B blocked, ECN-2026-119 rev A use-up allowed, CMP-GAM-010 critical)
    system.replay_step()
    assert _reconciler_errors() == []
    drop1 = _reconciler_issues(status="open")
    rev_issues = [i for i in drop1 if i.type == "rev_blocked"]
    assert len(rev_issues) == 1
    assert rev_issues[0].part_id == "CMP-GAM-004"
    assert rev_issues[0].kind == IssueKind.COPIES_AT_RISK
    assert rev_issues[0].impact.get("product_id") == "FG-GH6-STD"
    assert rev_issues[0].impact.get("hours_buffer") is not None
    # ECN-2026-119 (CMP-GAM-009, use_up=Y) must NOT be blocked
    assert not any(i.type == "rev_blocked" and i.part_id == "CMP-GAM-009" for i in drop1)
    # CMP-GAM-010 now flagged critical with only Dec sea shipments
    assert any(i.type == "shipment_late_for_launch" and i.part_id == "CMP-GAM-010" for i in drop1)

    # Drop 2 (17:23: China magnet export ban, Pune Diecast watch + expired release)
    system.replay_step()
    assert _reconciler_errors() == []
    drop2 = _reconciler_issues(status="open")
    mag = [i for i in drop2 if i.type == "single_source_export_ban" and i.part_id == "CMP-MAG-005"]
    assert len(mag) == 1
    assert mag[0].severity == Severity.LINE_STOP
    assert mag[0].impact.get("copies_at_risk") == 18900
    assert mag[0].impact.get("hours_buffer") == 0.0
    assert any(i.type == "supplier_qualification_expired" and i.supplier_id == "SUP-0018" for i in drop2)

    # T3.0: Every reconciler finding conforms to Finding Format v1
    for issue in drop2:
        assert 1 <= len(issue.summary) <= 160
        assert issue.evidence, f"missing evidence on {issue.type}/{issue.id}"
        assert issue.subject_type and issue.subject_id
        if issue.severity >= 3:
            assert issue.suggested_actions, f"missing suggested_actions on sev {issue.severity} {issue.type}"

    # Re-running the exact same state produces zero duplicate findings or conflicts
    before_issues = len(_reconciler_issues())
    before_conflicts = len(all_rows(Conflict))
    system.run_full(label="idempotency check")
    assert len(_reconciler_issues()) == before_issues
    assert len(all_rows(Conflict)) == before_conflicts


# ---------------------------------------------------------------------------
# 2. T3.7 Bank details / IBAN fraud gate -> always HUMAN_CHECK
# ---------------------------------------------------------------------------
def test_iban_change_always_routes_to_human_check():
    _seed_minimal_launch()
    add_all([
        PurchaseOrder(po_number="4500900001", po_line=1, supplier_id="SUP-0028", material_id="CMP-GAM-004",
                      quantity=10000, status="open", source="soony/erp/po.csv"),
        OrderConfirmation(confirmation_id="OA900001", po_number="4500900001", po_line=1,
                          supplier_id="SUP-0028", material_id="CMP-GAM-004", quantity=10000,
                          bank_iban="DE89370400440532013000", source="suppliers/SUP-0028/OA900001.pdf"),
        Document(path="suppliers/SUP-0028/notices/bank_details_change.pdf", kind="notice",
                 supplier_id="SUP-0028", text="Urgent: please remit to new account CY17002001280000001200527600",
                 extracted={"facts": {"signals": ["bank_change"], "ibans": ["CY17002001280000001200527600"]}},
                 source="suppliers/SUP-0028/notices/bank_details_change.pdf"),
    ])
    bus = Coordinator()
    ReconcilerGate(bus)
    bus.emit(events.TRUTH_UPDATED, {"tables": ["order_confirmations", "documents"]}, source="test")
    bus.run_until_idle()

    bank_issues = [i for i in _reconciler_issues() if i.type == "bank_change"]
    assert len(bank_issues) == 2
    assert all(i.kind == IssueKind.HUMAN_CHECK and i.severity == Severity.LINE_STOP for i in bank_issues)
    assert all(i.suggested_actions and i.suggested_actions[0]["owner"] == "human" for i in bank_issues)
    conflicts = [c for c in all_rows(Conflict) if c.field == "bank_iban"]
    assert len(conflicts) == 2


# ---------------------------------------------------------------------------
# 3. T3.8 Quantity jump (e.g. 690 -> 790) -> always HUMAN_CHECK
# ---------------------------------------------------------------------------
def test_quantity_jump_routes_to_human_check():
    _seed_minimal_launch()
    add_all([
        PurchaseOrder(po_number="4500900002", po_line=1, supplier_id="SUP-0028", material_id="CMP-GAM-004",
                      quantity=690, status="open", source="soony/erp/po.csv"),
        OrderConfirmation(confirmation_id="OA900002", po_number="4500900002", po_line=1,
                          supplier_id="SUP-0028", material_id="CMP-GAM-004", quantity=790,
                          source="suppliers/SUP-0028/OA900002.pdf"),
    ])
    bus = Coordinator()
    ReconcilerGate(bus)
    bus.emit(events.TRUTH_UPDATED, {"tables": ["order_confirmations"]}, source="test")
    bus.run_until_idle()

    jumps = [i for i in _reconciler_issues() if i.type == "quantity_jump"]
    assert len(jumps) == 1
    assert jumps[0].kind == IssueKind.HUMAN_CHECK
    assert "690" in jumps[0].summary and "790" in jumps[0].summary


# ---------------------------------------------------------------------------
# 4. T3.11 + T3.10 Supplier reply fills missing revision -> Reconciler auto-resolves
# ---------------------------------------------------------------------------
def test_supplier_reply_resolves_missing_revision_and_reopens_if_regressed():
    _seed_minimal_launch()
    add_all([
        EngineeringChange(change_id="ECN-2026-118", parent_id="FG-GH6-STD", child_id="CMP-GAM-004",
                          action="revision", rev_old="B", rev_new="C", use_up_old_rev=False,
                          effective_from=date(2026, 11, 21), reason="rev B forbidden",
                          source="soony/master/bom_delta_2026-11-21.csv"),
        PurchaseOrder(po_number="4500999001", po_line=1, supplier_id="SUP-0028", material_id="CMP-GAM-004",
                      quantity=20000, status="open", source="soony/erp/po.csv"),
    ])
    oc = add_all([
        OrderConfirmation(confirmation_id="OA-REPLY-1", po_number="4500999001", po_line=1,
                          supplier_id="SUP-0028", material_id="CMP-GAM-004", quantity=20000,
                          revision="", source="suppliers/SUP-0028/OA-REPLY-1.pdf"),
    ])[0]

    bus = Coordinator()
    ReconcilerGate(bus)
    bus.emit(events.TRUTH_UPDATED, {"tables": ["order_confirmations"]}, source="ingest")
    bus.run_until_idle()

    missing = [i for i in _reconciler_issues() if i.type == "missing_revision"]
    assert len(missing) == 1 and missing[0].status == "open" and missing[0].kind == IssueKind.MISSING_INFO
    issue_id = missing[0].id

    # Ingest applies supplier reply confirming rev C -> emits truth.updated with issue_ids
    res = update_row(OrderConfirmation, oc.id, {"revision": "C"}, source="inbox/1", agent="ingest",
                     reason="supplier reply", issue_id=issue_id)
    bus.emit(events.TRUTH_UPDATED, {"changes": [res.as_payload()], "issue_ids": [issue_id]}, source="ingest")
    bus.run_until_idle()

    updated = find_rows(Issue, id=issue_id)[0]
    assert updated.status == "resolved"

    # If a later drop regresses confirmation revision back to blank, Reconciler reopens the same issue
    update_row(OrderConfirmation, oc.id, {"revision": ""}, source="suppliers/SUP-0028/OA-REPLY-1.pdf",
               agent="ingest", on_conflict="overwrite", reason="file replaced")
    bus.emit(events.TRUTH_UPDATED, {"tables": ["order_confirmations"]}, source="ingest")
    bus.run_until_idle()

    reopened = find_rows(Issue, id=issue_id)[0]
    assert reopened.status == "reopened"


# ---------------------------------------------------------------------------
# 5. T3.1 + T3.2 Stale feed & tracker mismatch resolve when updated data arrives
# ---------------------------------------------------------------------------
def test_tracker_mismatch_and_stale_feed_auto_resolve_on_update():
    _seed_minimal_launch()
    now = datetime(2026, 11, 21, 13, 0, tzinfo=timezone.utc)
    erp_sh = add_all([
        Shipment(shipment_id="SHP-TEST-1", mode="sea", supplier_id="SUP-0028", eta=date(2026, 11, 21),
                 status="arrived_port", updated_at=now - timedelta(hours=24),
                 note="at CTA Hamburg", source="soony/erp/open_shipments.csv"),
        Shipment(shipment_id="SHP-TEST-FRESH", mode="road", supplier_id="SUP-0028", eta=date(2026, 11, 21),
                 status="in_transit", updated_at=now, source="soony/erp/open_shipments.csv"),
    ])[0]
    trk_sh = add_all([
        Shipment(shipment_id="SHP-TEST-1", mode="sea", supplier_id="SUP-0028", eta=date(2026, 11, 21),
                 status="in_transit", source="soony/logistics/tracker.xlsx#sheet1.xml"),
    ])[0]
    add_all([
        ShipmentMaterial(shipment_id="SHP-TEST-1", material_id="CMP-GAM-004", source="soony/erp/open_shipments.csv"),
        ShipmentPO(shipment_id="SHP-TEST-1", po_number="4500999002", source="soony/erp/open_shipments.csv"),
    ])

    bus = Coordinator()
    ReconcilerGate(bus)
    bus.emit(events.TRUTH_UPDATED, {"tables": ["shipments"]}, source="ingest")
    bus.run_until_idle()

    open_types = {i.type for i in _reconciler_issues(status="open")}
    assert {"tracker_mismatch", "stale_feed"} <= open_types
    assert any(c.entity_id == "SHP-TEST-1" and c.status == "open" for c in all_rows(Conflict))

    # Tracker catches up and ERP timestamp refreshes
    update_row(Shipment, trk_sh.id, {"status": "arrived_port"}, source=trk_sh.source,
               agent="ingest", on_conflict="overwrite")
    update_row(Shipment, erp_sh.id, {"updated_at": now}, source=erp_sh.source,
               agent="ingest", on_conflict="overwrite")
    bus.emit(events.TRUTH_UPDATED, {"tables": ["shipments"]}, source="ingest")
    bus.run_until_idle()

    assert not any(i.shipment_id == "SHP-TEST-1" and i.status != "resolved" for i in _reconciler_issues())
    assert all(c.status == "resolved" for c in all_rows(Conflict) if c.entity_id == "SHP-TEST-1")


# ---------------------------------------------------------------------------
# 6. Risk update escalates open Reconciler finding severity
# ---------------------------------------------------------------------------
def test_on_risk_updated_escalates_open_findings():
    _seed_minimal_launch()
    add_all([
        PurchaseOrder(po_number="4500999003", po_line=1, supplier_id="SUP-0028", material_id="CMP-GAM-004",
                      quantity=15000, status="open", source="soony/erp/po.csv"),
    ])
    bus = Coordinator()
    ReconcilerGate(bus)
    bus.emit(events.TRUTH_UPDATED, {"tables": ["purchase_orders"]}, source="ingest")
    bus.run_until_idle()

    po_issue = next(i for i in _reconciler_issues() if i.type == "unconfirmed_po")
    assert po_issue.severity == Severity.MEDIUM

    add_all([RiskScore(entity_type="part", entity_id="CMP-GAM-004", grade=6, rating="CCC",
                       reasons=[{"text": "critical"}])])
    bus.emit(events.RISK_UPDATED, {}, source="risk")
    bus.run_until_idle()

    escalated = find_rows(Issue, id=po_issue.id)[0]
    assert escalated.severity == Severity.LINE_STOP
