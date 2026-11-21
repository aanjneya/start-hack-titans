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


def test_scan_ocr_is_cross_checked_against_goods_receipts(monkeypatch):
    """Live OCR is simulated with a mock answer for one real delivery note (WZ-260906-369)."""
    from backend.core import llm
    from backend.core.models import Document

    def fake_ocr(prompt="", files=(), **kw):
        if not any("WZ-260906-369" in str(f) for f in files):
            return None  # other scans stay "pending" like offline
        return {"document_type": "delivery_note", "document_number": "WZ-260906-369",
                "document_date": "2026-09-06", "supplier_name": "Karton Nowak Sp. z o.o.",
                "po_numbers": ["4500318712"], "received_by": "Schulz P.", "language": "pl",
                "lines": [{"part_number": "CMP-GAM-005", "description": "Cover Sleeve Grand Heist 5",
                           "quantity": 120000, "unit": "PCS", "packages": 125}],
                "handwritten_notes": "5 cartons missing", "summary_en": "Delivery note for sleeves."}

    monkeypatch.setitem(llm._MOCKS, "ingest.scan_ocr", fake_ocr)
    system.run_full(label="with ocr")
    doc = find_rows(Document, path="soony/receiving/DC-HAM/20260907_WZ-260906-369.jpg")[0]
    checks = doc.extracted["checks"]
    assert doc.kind == "delivery_note" and doc.supplier_id == "SUP-0028"
    assert checks["results"]["number_matches_file_name"] and checks["results"]["po_known"]
    assert checks["results"]["qty_CMP-GAM-005_matches_receipt"] is False  # paper 120,000 vs booked 125,000
    assert any("booked 125,000" in n for n in checks["notes"]) and any("missing" in n for n in checks["notes"])
    pending = [d for d in all_rows(Document) if d.kind == "scan"]
    assert pending and all(d.extracted.get("ocr", "").startswith("pending") for d in pending)


def test_llm_sends_images_and_pdfs_as_blocks(tmp_path):
    from backend.core.llm import _content

    img, pdf = tmp_path / "a.jpg", tmp_path / "b.pdf"
    img.write_bytes(b"\xff\xd8fake")
    pdf.write_bytes(b"%PDF-1.4 fake")
    blocks = _content("read it", [img, pdf])
    assert [b["type"] for b in blocks] == ["image", "document", "text"]
    assert blocks[0]["source"]["media_type"] == "image/jpeg" and blocks[1]["source"]["media_type"] == "application/pdf"
    assert _content("just text") == "just text"


def test_ocr_is_not_repeated_for_unchanged_scans(monkeypatch):
    from backend.core import llm

    calls = []

    def fake_ocr(files=(), **kw):
        calls.append(files[0])
        return {"document_type": "delivery_note", "document_number": "X", "lines": []}

    monkeypatch.setitem(llm._MOCKS, "ingest.scan_ocr", fake_ocr)
    system.run_full(label="first")
    first = len(calls)
    system.run_full(label="second")
    assert first >= 60 and len(calls) == first  # second run reused the cached OCR


def test_ingest_findings_follow_the_finding_format(full):
    ingest = next(a for a in system._agents if a.name == "ingest")
    ingest._scans, ingest._unmapped, ingest._unresolved = [], [], {"part 'XX-1' of SUP-0029 (test.pdf)"}
    ingest._raise_summary_issues()
    by_type = {i.type: i for i in all_rows(Issue)}
    for t in ("scans_unread", "unresolved_ids"):
        issue = by_type[t]
        assert issue.reason and issue.suggested_actions and issue.subject_type == "drop", t
        assert issue.schema_version == 1
    assert by_type["unresolved_ids"].severity == 3 and len(by_type["unresolved_ids"].suggested_actions) == 2


def test_hand_corrected_quantity_and_split_lines(monkeypatch):
    """Real cases from the live OCR run: WZ-261116-473 (80,000 printed, 8,000 by hand, 80,000 booked) and
    DN-260821-169 (one part on two lines that add up to the booked receipt)."""
    from backend.core import llm
    from backend.core.models import Document

    def fake_ocr(prompt="", files=(), **kw):
        name = str(files[0])
        if "WZ-261116-473" in name:
            return {"document_type": "WZ document", "document_number": "WZ-261116-473",
                    "supplier_name": "Karton Nowak Sp. z o.o., Poznań", "po_numbers": [],
                    "lines": [{"part_number": "CMP-GAM-008", "quantity_as_printed": "80.000",
                               "quantity_corrected_by_hand": "8000"}],
                    "handwritten_notes": "Quantity corrected by hand to 8000"}
        if "DN-260821-169" in name:
            return {"document_type": "delivery note", "document_number": "DN-260821-169",
                    "supplier_name": "Hwaseong Memory Co., Ltd., Hwaseong",
                    "lines": [{"part_number": "CMP-GAM-002", "quantity_as_printed": "21,000"},
                              {"part_number": "CMP-GAM-002", "quantity_as_printed": "22,000"}],
                    "handwritten_notes": "None other than the signature."}
        return None

    monkeypatch.setitem(llm._MOCKS, "ingest.scan_ocr", fake_ocr)
    system.run_full(label="hand corrections")
    wz = find_rows(Document, path="soony/receiving/DC-HAM/20261118_WZ-261116-473.jpg")[0].extracted
    assert wz["checks"]["results"]["qty_CMP-GAM-008_matches_receipt"] is False
    assert any("corrected by hand to 8,000" in n and "booked 80,000" in n for n in wz["checks"]["notes"])
    dn = find_rows(Document, path="soony/receiving/DC-HAM/20260930_DN-260821-169.jpg")[0].extracted
    assert dn["checks"]["results"]["qty_CMP-GAM-002_matches_receipt"] is True  # 21k + 22k = 43k booked
    assert dn["ocr"]["handwritten_notes"] == ""  # "none other than the signature" is noise
    assert not any("Hwaseong Memory Co., Ltd., Hwaseong" in a.alias for a in all_rows(Alias) if a.kind == "x")
    assert any(a.method == "contains" for a in all_rows(Alias) if a.kind == "supplier")


def test_ai_column_mapping_is_validated():
    from backend.agents.ingest_mapping import TARGETS, validate_mapping

    system.run_full(label="seed")  # existing ids give the key shapes
    ok, why = validate_mapping(TARGETS["suppliers"], {"site_id": "supplier_id", "name": "name"},
                               [{"site_id": "DC-HAM", "name": "Hamburg"}])
    assert not ok and "look like" in why
    ok, why = validate_mapping(TARGETS["packout"], {"Day": "pack_date", "Hours": "units_packed", "Lines": "product_id"},
                               [{"Day": "Saturday", "Hours": "16", "Lines": "L1"}])
    assert not ok
    ok, _ = validate_mapping(TARGETS["packout"], {"date": "pack_date", "units": "units_packed", "p": "product_id"},
                             [{"date": "2026-11-02", "units": "400", "p": "FG-GH6-CE"}])
    assert ok
