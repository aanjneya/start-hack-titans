"""Truth-store tables (PLAN.md §3b, Tim's schema) and shared result types.

Rules every agent follows (updated Sat 19:30, team-approved):
- Rows are the CURRENT truth and can be updated, but ONLY via `store.update_row(...)`, which writes
  every change (old -> new, source, agent, reason) to the `field_changes` log. Nothing is lost silently.
- One writer for supply-chain data: the Ingest agent (incl. supplier replies). Other agents write
  issues, conflicts, decisions, forecasts, risk scores, outbox/inbox.
- Every row keeps `source` (where it first came from), `ingested_at`, `updated_at`; extracted values carry `confidence`.
- Owners may ADD columns/tables for their agent; announce changes in .brain/DECISIONS.md.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel
from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Shared value types (not tables)
# ---------------------------------------------------------------------------
class Evidence(BaseModel):
    """Where a fact or finding comes from. `path` is relative to the data root."""

    path: str
    locator: str = ""  # e.g. "row 12", "page 2", "column eta"
    sha256: str = ""
    quote: str = ""  # short excerpt shown in the trace drawer


class DecisionOption(BaseModel):
    label: str
    description: str = ""
    cost_eur: Optional[float] = None
    copies_gained: Optional[int] = None
    actions: list[dict[str, Any]] = []  # e.g. {"type": "email", "supplier_id": ..., "issue_id": ...}
    risk_note: str = ""


class IssueKind:
    """Where the Reconciler routes an issue."""

    MISSING_INFO = "missing_info"  # -> Chaser
    COPIES_AT_RISK = "copies_at_risk"  # -> Launch Planner
    HUMAN_CHECK = "human_check"  # -> a person (bank details, big jumps, unmappable data)


class Provenance(SQLModel):
    source: str = Field(default="", index=True)  # file path relative to data root, or agent name
    ingested_at: datetime = Field(default_factory=utcnow)
    updated_at: Optional[datetime] = None  # set by store.update_row
    confidence: Optional[float] = None


def _json(default_factory=list):
    return Field(default_factory=default_factory, sa_column=Column(JSON))


# ---------------------------------------------------------------------------
# Master data
# ---------------------------------------------------------------------------
class Material(Provenance, table=True):
    __tablename__ = "materials"
    id: Optional[int] = Field(default=None, primary_key=True)
    component_id: str = Field(index=True)
    description: str = ""
    category: str = ""
    uom: str = ""
    abc_class: str = ""
    critical: Optional[bool] = None
    drawing_rev: str = ""


class BomLine(Provenance, table=True):
    __tablename__ = "bom"
    id: Optional[int] = Field(default=None, primary_key=True)
    parent_id: str = Field(index=True)
    child_id: str = Field(index=True)
    qty_per: float = 1
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None


class Product(Provenance, table=True):
    __tablename__ = "products"
    id: Optional[int] = Field(default=None, primary_key=True)
    product_id: str = Field(index=True)
    name: str = ""
    product_line: str = ""
    lifecycle: str = ""
    launch_date: Optional[date] = None
    weekly_plan_qty: Optional[int] = None
    day_one_qty: Optional[int] = None
    std_cost_eur: Optional[float] = None
    valid_from: Optional[date] = None


class Supplier(Provenance, table=True):
    __tablename__ = "suppliers"
    id: Optional[int] = Field(default=None, primary_key=True)
    supplier_id: str = Field(index=True)
    vendor_no: Optional[int] = None  # legacy SAP LIFNR
    name: str = ""
    legal_name: str = ""
    category: str = ""
    country: str = ""
    city: str = ""
    currency: str = ""
    payment_terms: str = ""
    incoterm: str = ""
    status: str = ""
    qualification_status: str = ""
    qualification_valid_until: Optional[date] = None
    contact_name: str = ""
    contact_email: str = ""
    language: str = ""
    bank_iban: str = ""
    notes: str = ""


class SupplierPartMap(Provenance, table=True):
    """Supplier's own part number / role for one of our components."""

    __tablename__ = "supplier_part_map"
    id: Optional[int] = Field(default=None, primary_key=True)
    supplier_id: str = Field(index=True)
    component_id: str = Field(index=True)
    supplier_ref: str = ""
    share: Optional[float] = None
    primary: Optional[bool] = None
    lead_time_days: Optional[int] = None
    moq: Optional[int] = None
    unit_price: Optional[float] = None
    currency: str = ""
    note: str = ""


class EngineeringChange(Provenance, table=True):
    __tablename__ = "engineering_changes"
    id: Optional[int] = Field(default=None, primary_key=True)
    change_id: str = Field(index=True)
    parent_id: str = ""
    child_id: str = Field(default="", index=True)
    action: str = ""
    rev_old: str = ""
    rev_new: str = ""
    use_up_old_rev: Optional[bool] = None
    effective_from: Optional[date] = None
    reason: str = ""


# ---------------------------------------------------------------------------
# Supply chain (one table per step: order -> confirm -> ship -> receive -> stock -> pack)
# ---------------------------------------------------------------------------
class PurchaseOrder(Provenance, table=True):
    __tablename__ = "purchase_orders"
    id: Optional[int] = Field(default=None, primary_key=True)
    po_number: str = Field(index=True)
    po_line: Optional[int] = None
    supplier_id: str = Field(default="", index=True)
    material_id: str = Field(default="", index=True)
    plant: str = ""
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    currency: str = ""
    order_date: Optional[date] = None
    requested_date: Optional[date] = None
    promised_date: Optional[date] = None
    actual_delivery_date: Optional[date] = None
    status: str = ""
    buyer: str = ""
    incoterm: str = ""
    note: str = ""


class OrderConfirmation(Provenance, table=True):
    __tablename__ = "order_confirmations"
    id: Optional[int] = Field(default=None, primary_key=True)
    confirmation_id: str = Field(default="", index=True)
    po_number: str = Field(default="", index=True)
    po_line: Optional[int] = None
    supplier_id: str = ""
    material_id: str = ""
    quantity: Optional[float] = None
    ship_date: Optional[date] = None
    delivery_date: Optional[date] = None
    revision: str = ""
    unit_price: Optional[float] = None
    currency: str = ""
    bank_iban: str = ""
    note: str = ""


class Shipment(Provenance, table=True):
    """One row per shipment per source (ERP csv and tracker xlsx both land here; compare by source)."""

    __tablename__ = "shipments"
    id: Optional[int] = Field(default=None, primary_key=True)
    shipment_id: str = Field(index=True)
    carrier_id: str = ""
    mode: str = ""
    reference: str = ""
    supplier_id: str = ""
    route_id: str = ""
    origin: str = ""
    destination: str = ""
    via: str = ""
    dest_site: str = ""
    etd: Optional[date] = None
    eta: Optional[date] = None
    status: str = ""
    last_lat: Optional[float] = None
    last_lon: Optional[float] = None
    updated_at: Optional[datetime] = None
    note: str = ""


class ShipmentPO(Provenance, table=True):
    __tablename__ = "shipment_po"
    id: Optional[int] = Field(default=None, primary_key=True)
    shipment_id: str = Field(index=True)
    po_number: str = Field(index=True)


class ShipmentMaterial(Provenance, table=True):
    __tablename__ = "shipment_material"
    id: Optional[int] = Field(default=None, primary_key=True)
    shipment_id: str = Field(index=True)
    material_id: str = Field(index=True)


class GoodsReceipt(Provenance, table=True):
    __tablename__ = "goods_receipts"
    id: Optional[int] = Field(default=None, primary_key=True)
    gr_number: str = Field(index=True)
    gr_date: Optional[date] = None
    po_number: str = Field(default="", index=True)
    po_line: Optional[int] = None
    supplier_id: str = ""
    material_id: str = Field(default="", index=True)
    plant: str = ""
    qty_ordered: Optional[float] = None
    qty_received: Optional[float] = None
    delivery_note: str = ""
    inspector: str = ""
    remark: str = ""


class StockLot(Provenance, table=True):
    __tablename__ = "stock"
    id: Optional[int] = Field(default=None, primary_key=True)
    snapshot_date: Optional[date] = None
    site: str = ""
    material_id: str = Field(index=True)
    batch: str = ""
    quantity: Optional[float] = None
    uom: str = ""
    safety_stock: Optional[float] = None
    weeks_of_cover: Optional[float] = None
    last_count: Optional[date] = None
    revision: str = ""  # filled when known (ECN gate needs it)


class Packout(Provenance, table=True):
    __tablename__ = "packout"
    id: Optional[int] = Field(default=None, primary_key=True)
    pack_date: Optional[date] = None  # CSV column "date"
    site: str = ""
    product_id: str = Field(index=True)
    line: str = ""
    units_packed: Optional[int] = None


class LaunchStatus(Provenance, table=True):
    """Rows of soony/context/launch_status.xlsx (or any customer's equivalent)."""

    __tablename__ = "launch_status"
    id: Optional[int] = Field(default=None, primary_key=True)
    as_of: Optional[datetime] = None
    product_id: str = Field(index=True)
    edition: str = ""
    day_one_demand: Optional[int] = None
    packed: Optional[int] = None
    still_to_pack: Optional[int] = None
    rate_per_hour: Optional[float] = None
    line_status: str = ""


class Document(Provenance, table=True):
    """Any unstructured input: notices, contracts, memos, news, scans, supplier replies."""

    __tablename__ = "documents"
    id: Optional[int] = Field(default=None, primary_key=True)
    path: str = Field(index=True)
    kind: str = Field(default="other", index=True)  # notice|contract|order_confirmation|memo|news|scan|reply|other
    supplier_id: str = Field(default="", index=True)
    language: str = ""
    title: str = ""
    text: str = ""  # original text (keep for evidence)
    text_en: str = ""  # English translation if different
    extracted: dict = _json(dict)  # structured facts pulled out by the Ingest agent
    published_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Truth & trace (agents' outputs)
# ---------------------------------------------------------------------------
class Conflict(SQLModel, table=True):
    __tablename__ = "conflicts"
    id: Optional[int] = Field(default=None, primary_key=True)
    entity: str = Field(index=True)  # e.g. "shipment"
    entity_id: str = Field(index=True)  # e.g. "SHP-2026-04276"
    field: str = ""
    value_a: str = ""
    source_a: str = ""
    value_b: str = ""
    source_b: str = ""
    status: str = Field(default="open", index=True)  # open -> asked -> resolved
    created_at: datetime = Field(default_factory=utcnow)
    resolved_at: Optional[datetime] = None


class Issue(SQLModel, table=True):
    __tablename__ = "issues"
    id: Optional[int] = Field(default=None, primary_key=True)
    fingerprint: str = Field(index=True)  # stable key so re-running checks updates, not duplicates
    type: str = Field(index=True)  # e.g. tracker_mismatch, stale_feed, rev_blocked, single_source
    kind: str = Field(default=IssueKind.HUMAN_CHECK, index=True)  # routing: see IssueKind
    severity: int = 3  # 1 info .. 5 line-stopper
    summary: str = ""
    part_id: str = ""
    supplier_id: str = ""
    shipment_id: str = ""
    po_number: str = ""
    evidence: list = _json(list)  # list[Evidence dict]
    status: str = Field(default="open", index=True)  # open|routed|asked|resolved|reopened
    raised_by: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class DecisionCard(SQLModel, table=True):
    __tablename__ = "decision_cards"
    id: Optional[int] = Field(default=None, primary_key=True)
    issue_ids: list = _json(list)
    title: str = ""
    summary: str = ""  # one line for the exec: risk -> recommended action
    problem: str = ""
    reasoning: str = ""  # full "why" shown on demand (office hours: exec summary + detail)
    evidence: list = _json(list)  # list[Evidence dict]
    options: list = _json(list)  # list[DecisionOption dict]
    recommended: Optional[int] = None  # index into options
    approver: str = ""  # from core/policy.approver_for(cost) (customer approval matrix)
    approver_reason: str = ""  # e.g. "€120k: Category lead (<€500k)"
    needs_approval: bool = True  # False = executed automatically (autonomy switch "auto")
    decide_by: Optional[datetime] = None
    status: str = Field(default="proposed", index=True)  # proposed|approved|rejected|executed|outdated
    chosen_option: Optional[int] = None
    decided_by: str = ""
    decided_at: Optional[datetime] = None
    created_by: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class Forecast(SQLModel, table=True):
    __tablename__ = "forecasts"
    id: Optional[int] = Field(default=None, primary_key=True)
    computed_at: datetime = Field(default_factory=utcnow)
    product_id: str = Field(index=True)
    edition: str = ""
    deadline: Optional[datetime] = None
    still_to_pack: Optional[int] = None
    projected_packed: Optional[int] = None
    shortfall: Optional[int] = None
    limiting_part: str = ""
    details: dict = _json(dict)


class RiskScore(SQLModel, table=True):
    __tablename__ = "risk_scores"
    id: Optional[int] = Field(default=None, primary_key=True)
    entity_type: str = Field(index=True)  # part|supplier
    entity_id: str = Field(index=True)
    grade: int = 3  # 1 safe .. 6 critical
    rating: str = ""  # AAA..CCC
    reasons: list = _json(list)  # [{"text": ..., "evidence": [...]}]
    computed_at: datetime = Field(default_factory=utcnow)


class ColumnMapping(SQLModel, table=True):
    """Learned once per file format (header fingerprint): which column means which field (T2.11)."""

    __tablename__ = "column_mappings"
    id: Optional[int] = Field(default=None, primary_key=True)
    fingerprint: str = Field(index=True)  # hash of the normalized header row
    target_table: str = ""
    mapping: dict = _json(dict)  # {source_column: field}
    options: dict = _json(dict)  # {"decimal_comma": bool, "date_order": "ymd|mdy|dmy", "list_sep": "|"}
    method: str = ""  # glossary | llm | manual
    score: Optional[float] = None
    example_path: str = ""
    confirmed: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class Alias(SQLModel, table=True):
    """Other names for our IDs: supplier names/vendor numbers, supplier part numbers (T2.7/T2.16)."""

    __tablename__ = "aliases"
    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str = Field(index=True)  # supplier | part | po
    alias: str = Field(index=True)  # normalized alias text
    canonical_id: str = Field(index=True)  # e.g. SUP-0028, CMP-GAM-007
    confidence: float = 1.0
    method: str = ""  # master_data | profile | fuzzy | llm | manual
    source: str = ""
    confirmed: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class PortalFile(SQLModel, table=True):
    """Watcher's manifest of the portal: what it last saw per file (live mode)."""

    __tablename__ = "portal_files"
    id: Optional[int] = Field(default=None, primary_key=True)
    path: str = Field(index=True)
    sha256: str = ""
    bytes: Optional[int] = None
    seen_at: datetime = Field(default_factory=utcnow)
    removed: bool = False


class FieldChange(SQLModel, table=True):
    """Change log: every update of a truth-store value (filled missing field, correction, supplier reply)."""

    __tablename__ = "field_changes"
    id: Optional[int] = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=utcnow, index=True)
    run_id: str = Field(default="", index=True)
    table_name: str = Field(index=True)
    row_id: int = Field(index=True)
    entity_key: str = Field(default="", index=True)  # e.g. "PO 4500318745/1", "SHP-2026-04276"
    field: str = ""
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    change_type: str = "fill"  # fill (was empty) | overwrite (correction)
    source: str = ""  # file/email the new value came from
    agent: str = ""
    reason: str = ""
    confidence: Optional[float] = None
    issue_id: Optional[int] = None  # issue this change answers, if any


class InboxMessage(SQLModel, table=True):
    """Supplier/carrier replies received by the Chaser. Ingest turns them into data updates."""

    __tablename__ = "inbox"
    id: Optional[int] = Field(default=None, primary_key=True)
    supplier_id: str = Field(default="", index=True)
    outbox_id: Optional[int] = None
    issue_id: Optional[int] = None
    from_email: str = ""
    subject: str = ""
    body: str = ""
    body_en: str = ""
    language: str = ""
    received_at: datetime = Field(default_factory=utcnow)
    processed: bool = Field(default=False, index=True)  # set by Ingest once applied
    result: dict = _json(dict)  # what Ingest did: {"changes": n, "conflicts": n, "note": ...}


class OutboxMessage(SQLModel, table=True):
    __tablename__ = "outbox"
    id: Optional[int] = Field(default=None, primary_key=True)
    supplier_id: str = Field(index=True)
    issue_id: Optional[int] = None
    decision_id: Optional[int] = None
    to_email: str = ""
    language: str = ""
    subject: str = ""
    body: str = ""
    body_en: str = ""
    status: str = Field(default="draft", index=True)  # draft|approved|sent|replied|failed
    created_at: datetime = Field(default_factory=utcnow)
    sent_at: Optional[datetime] = None


class DropRecord(SQLModel, table=True):
    """One portal check / replayed drop, as seen by the Watcher."""

    __tablename__ = "drops"
    id: Optional[int] = Field(default=None, primary_key=True)
    label: str = ""  # e.g. "2026-11-21T16-08" or a git commit
    mode: str = "live"  # live|replay|manual
    checked_at: datetime = Field(default_factory=utcnow)
    added: list = _json(list)
    changed: list = _json(list)
    removed: list = _json(list)


class AgentLog(SQLModel, table=True):
    """Every read/decision/action of every agent (AI traceability)."""

    __tablename__ = "agent_log"
    id: Optional[int] = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=utcnow, index=True)
    run_id: str = Field(default="", index=True)
    agent: str = Field(index=True)
    action: str = ""  # e.g. "handle:files.changed", "raise_issue", "llm_call", "error"
    detail: str = ""
    evidence: list = _json(list)
    data: dict = _json(dict)  # small structured payload (ids, counts, model, tokens)
