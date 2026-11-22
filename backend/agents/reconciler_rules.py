"""Deterministic reconciliation & gate rules for Agent 3 (Reconciler & Gate).

Every check reads canonical truth-store rows (never hardcoded values from the data) and emits
CandidateFinding objects that conform to FINDING FORMAT v1 (.brain/DATA_FORMAT.md).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from ..core.models import (
    BomLine,
    Conflict,
    Document,
    EngineeringChange,
    FieldChange,
    Evidence,
    GoodsReceipt,
    IssueKind,
    LaunchStatus,
    Material,
    OrderConfirmation,
    Product,
    PurchaseOrder,
    Severity,
    Shipment,
    ShipmentMaterial,
    ShipmentPO,
    StockLot,
    Supplier,
    SupplierPartMap,
)
from .planner_counts import fitted_components
from ..core.store import all_rows, latest_by, query

# Thresholds (configurable defaults — never domain values from Soony's data)
STALE_FEED_HOURS = 18.0
QTY_JUMP_RATIO = 1.5  # >= 50% jump over ordered qty triggers human review
PRICE_DIFF_RATIO = 0.05  # >= 5% price discrepancy triggers review
CLOSED_PO_STATUSES = {"delivered", "closed", "received", "completed", "cancelled", "canceled", "gl", "lo"}
ACTIVE_SHIPMENT_STATUSES = {"booked", "in_transit", "arrived_port", "in_delivery", "delayed", "on_hold"}


@dataclass
class CandidateFinding:
    """Output of a single deterministic rule before Agent.raise_issue persists it."""

    type: str
    kind: str
    severity: int
    key: str
    summary: str
    reason: str = ""
    subject_type: str = ""
    subject_id: str = ""
    part_id: str = ""
    supplier_id: str = ""
    shipment_id: str = ""
    po_number: str = ""
    product_id: str = ""
    impact: dict[str, Any] = field(default_factory=dict)
    suggested_actions: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    confidence: Optional[float] = 0.95


@dataclass
class CandidateConflict:
    """Disagreement between two sources to persist in the `conflicts` table."""

    entity: str
    entity_id: str
    field: str
    value_a: str
    source_a: str
    value_b: str
    source_b: str


@dataclass
class CheckContext:
    """Snapshot of the current truth store built once per reconciliation pass."""

    products: dict[str, Product]
    launch_products: dict[str, Product]
    launch_status: dict[str, LaunchStatus]
    launch_deadline: Optional[date]
    reference_now: datetime
    shipment_feed_now: Optional[datetime]
    materials: dict[str, Material]
    bom_by_parent: dict[str, list[BomLine]]
    bom_by_child: dict[str, list[BomLine]]
    launch_parts: set[str]
    suppliers: dict[str, Supplier]
    spm_by_part: dict[str, list[SupplierPartMap]]
    ecns: list[EngineeringChange]
    pos: list[PurchaseOrder]
    po_by_key: dict[tuple[str, Optional[int]], PurchaseOrder]
    pos_by_number: dict[str, list[PurchaseOrder]]
    confirmations: list[OrderConfirmation]
    ocs_by_po: dict[str, list[OrderConfirmation]]
    shipments: list[Shipment]
    shipment_pos: dict[str, list[str]]
    shipment_mats: dict[str, list[str]]
    goods_receipts: list[GoodsReceipt]
    gr_by_number: dict[str, GoodsReceipt]
    stock: list[StockLot]
    documents: list[Document]
    existing_conflicts: list[Conflict]
    embedded: dict[str, str] = field(default_factory=dict)
    risk_by_part: dict[str, int] = field(default_factory=dict)
    risk_by_supplier: dict[str, int] = field(default_factory=dict)


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _truncate(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build_context(risk_by_part: dict[str, int] | None = None,
                  risk_by_supplier: dict[str, int] | None = None) -> CheckContext:
    """Load current truth from SQLite once for a reconciliation pass."""
    products_list = latest_by(all_rows(Product), "product_id")
    products = {p.product_id: p for p in products_list}

    ls_list = latest_by(all_rows(LaunchStatus), "product_id")
    launch_status = {ls.product_id: ls for ls in ls_list}

    # Determine launch-active products from launch_status, or products with upcoming/latest launch_date
    if launch_status:
        launch_products = {pid: products[pid] for pid in launch_status if pid in products}
        for pid in launch_status:
            launch_products.setdefault(pid, Product(product_id=pid, name=launch_status[pid].edition or pid))
    else:
        dated = [p for p in products_list if p.launch_date is not None]
        max_launch = max((p.launch_date for p in dated), default=None)
        if max_launch is not None:
            # Products launching within 30 days of the latest launch date
            launch_products = {
                p.product_id: p for p in dated if abs((max_launch - p.launch_date).days) <= 30
            }
        else:
            launch_products = dict(products)

    launch_dates = [p.launch_date for p in launch_products.values() if p.launch_date]
    launch_deadline = min(launch_dates) if launch_dates else None

    materials = {m.component_id: m for m in latest_by(all_rows(Material), "component_id")}

    bom_lines = latest_by(all_rows(BomLine), "parent_id", "child_id")
    bom_by_parent: dict[str, list[BomLine]] = defaultdict(list)
    bom_by_child: dict[str, list[BomLine]] = defaultdict(list)
    for b in bom_lines:
        bom_by_parent[b.parent_id].append(b)
        bom_by_child[b.child_id].append(b)

    launch_parts: set[str] = set()
    for pid in launch_products:
        for b in bom_by_parent.get(pid, []):
            launch_parts.add(b.child_id)

    suppliers = {s.supplier_id: s for s in latest_by(all_rows(Supplier), "supplier_id")}

    spm_by_part: dict[str, list[SupplierPartMap]] = defaultdict(list)
    for sp in latest_by(all_rows(SupplierPartMap), "supplier_id", "component_id"):
        spm_by_part[sp.component_id].append(sp)

    ecns = latest_by(all_rows(EngineeringChange), "change_id")

    pos = latest_by(all_rows(PurchaseOrder), "po_number", "po_line")
    po_by_key: dict[tuple[str, Optional[int]], PurchaseOrder] = {}
    pos_by_number: dict[str, list[PurchaseOrder]] = defaultdict(list)
    for po in pos:
        po_by_key[(po.po_number, po.po_line)] = po
        pos_by_number[po.po_number].append(po)

    confirmations = latest_by(all_rows(OrderConfirmation), "confirmation_id", "po_line", "material_id")
    ocs_by_po: dict[str, list[OrderConfirmation]] = defaultdict(list)
    for oc in confirmations:
        if oc.po_number:
            ocs_by_po[oc.po_number].append(oc)

    shipments = latest_by(all_rows(Shipment), "shipment_id", "source")

    shipment_pos: dict[str, list[str]] = defaultdict(list)
    for sp in all_rows(ShipmentPO):
        if sp.po_number not in shipment_pos[sp.shipment_id]:
            shipment_pos[sp.shipment_id].append(sp.po_number)

    shipment_mats: dict[str, list[str]] = defaultdict(list)
    for sm in all_rows(ShipmentMaterial):
        if sm.material_id not in shipment_mats[sm.shipment_id]:
            shipment_mats[sm.shipment_id].append(sm.material_id)

    goods_receipts = latest_by(all_rows(GoodsReceipt), "gr_number")
    gr_by_number = {g.gr_number: g for g in goods_receipts}

    stock = latest_by(all_rows(StockLot), "site", "material_id", "batch")
    embedded = {}
    for part, assembly, _ in fitted_components(all_rows(SupplierPartMap), all_rows(FieldChange)):
        consumers = [b.parent_id for b in bom_by_child[part.component_id] if b.parent_id in launch_products]
        if consumers and all(any(b.child_id == assembly for b in bom_by_parent[pid]) for pid in consumers):
            embedded[part.component_id] = assembly
    documents = latest_by(all_rows(Document), "path")
    existing_conflicts = list(all_rows(Conflict))

    # Derive reference clock for feed-staleness from the shipment export itself (max shipment updated_at),
    # and calendar reference date from the newest drop/status/ECN date so replays are deterministic.
    ship_timestamps = [_as_utc(sh.updated_at) for sh in shipments if sh.updated_at is not None]
    drop_timestamps: list[datetime] = []
    for ls in launch_status.values():
        if ls.as_of:
            drop_timestamps.append(_as_utc(ls.as_of))  # type: ignore[arg-type]
    for doc in documents:
        if doc.published_at:
            drop_timestamps.append(_as_utc(doc.published_at))  # type: ignore[arg-type]
    for ecn in ecns:
        if ecn.effective_from:
            drop_timestamps.append(datetime(ecn.effective_from.year, ecn.effective_from.month,
                                            ecn.effective_from.day, 12, 0, tzinfo=timezone.utc))
    if drop_timestamps or ship_timestamps:
        reference_now = max(drop_timestamps + [t for t in ship_timestamps if t is not None])
    elif launch_deadline:
        reference_now = datetime(launch_deadline.year, launch_deadline.month,
                                 launch_deadline.day, 12, 0, tzinfo=timezone.utc) - timedelta(days=1)
    else:
        reference_now = datetime.now(timezone.utc)

    shipment_feed_now = max((t for t in ship_timestamps if t is not None), default=None)

    return CheckContext(
        products=products,
        launch_products=launch_products,
        launch_status=launch_status,
        launch_deadline=launch_deadline,
        reference_now=reference_now,
        shipment_feed_now=shipment_feed_now,
        materials=materials,
        bom_by_parent=bom_by_parent,
        bom_by_child=bom_by_child,
        launch_parts=launch_parts,
        suppliers=suppliers,
        spm_by_part=spm_by_part,
        ecns=ecns,
        pos=pos,
        po_by_key=po_by_key,
        pos_by_number=pos_by_number,
        confirmations=confirmations,
        ocs_by_po=ocs_by_po,
        shipments=shipments,
        shipment_pos=shipment_pos,
        shipment_mats=shipment_mats,
        goods_receipts=goods_receipts,
        gr_by_number=gr_by_number,
        stock=stock, embedded=embedded,
        documents=documents,
        existing_conflicts=existing_conflicts,
        risk_by_part=risk_by_part or {},
        risk_by_supplier=risk_by_supplier or {},
    )


# ---------------------------------------------------------------------------
# Impact calculator (from LaunchStatus, BomLine, StockLot — never guessed)
# ---------------------------------------------------------------------------
def _part_products(ctx: CheckContext, part_id: str) -> list[tuple[str, float]]:
    """Return [(product_id, qty_per)] for launch-active products using `part_id`."""
    out = []
    for b in ctx.bom_by_child.get(part_id, []):
        if b.parent_id in ctx.launch_products:
            out.append((b.parent_id, float(b.qty_per or 1)))
    return out


def _due_by_iso(ctx: CheckContext) -> Optional[str]:
    if ctx.launch_deadline:
        return f"{ctx.launch_deadline.isoformat()}T20:00:00+01:00"
    return None


def _usable_stock_for_part(ctx: CheckContext, part_id: str, excluded_batches: set[str] | None = None) -> float:
    excluded = excluded_batches or set()
    by_site = defaultdict(list)
    for row in ctx.stock:
        if row.material_id == part_id:
            by_site[row.site].append(row)
    total = 0.0
    for rows in by_site.values():
        latest = max((r.snapshot_date or date.min for r in rows))
        current = [r for r in rows if (r.snapshot_date or date.min) == latest]
        balances = [r for r in current if not r.batch]
        eligible = balances or [r for r in current if r.batch not in excluded]
        total += sum(float(r.quantity or 0) for r in eligible)
    return total


def compute_part_impact(
    ctx: CheckContext,
    part_id: str,
    *,
    usable_qty_override: Optional[float] = None,
    blocked_qty: float = 0.0,
    note: str = "",
) -> tuple[str, dict[str, Any]]:
    """Calculate Impact dict (`product_id`, `copies_at_risk`, `hours_buffer`, `due_by`, `note`) from data."""
    consumers = _part_products(ctx, part_id)
    if part_id in ctx.embedded:
        return "", {"copies_at_risk": 0, "note": f"Fitted into {ctx.embedded[part_id]} by the supplier."}
    if not consumers:
        return "", {"due_by": _due_by_iso(ctx), "note": note}

    total_stock = usable_qty_override if usable_qty_override is not None else max(
        0.0, _usable_stock_for_part(ctx, part_id) - blocked_qty
    )
    # Sum demand across all launch-active products using this part (Collector's first if multiple)
    consumers_sorted = sorted(consumers, key=lambda x: (0 if "CE" in x[0] else 1, x[0]))
    primary_product = consumers_sorted[0][0]
    total_parts_needed = 0.0
    total_rate_parts_per_hour = 0.0
    primary_still_to_pack: Optional[int] = None

    for pid, qty_per in consumers_sorted:
        ls = ctx.launch_status.get(pid)
        if ls and ls.still_to_pack is not None:
            need = ls.still_to_pack * qty_per
            if ls.rate_per_hour and ls.rate_per_hour > 0:
                total_rate_parts_per_hour += ls.rate_per_hour * qty_per
            if pid == primary_product:
                primary_still_to_pack = ls.still_to_pack
        else:
            prod = ctx.launch_products.get(pid)
            plan_qty = (prod.day_one_qty or prod.weekly_plan_qty) if prod else None
            need = (plan_qty or 0) * qty_per
            if pid == primary_product and plan_qty is not None:
                primary_still_to_pack = plan_qty
        total_parts_needed += need

    primary_qty_per = consumers_sorted[0][1]
    copies_at_risk: Optional[int] = None
    hours_buffer: Optional[float] = None

    if total_parts_needed > 0:
        shortfall_parts = max(0.0, total_parts_needed - total_stock)
        if shortfall_parts > 0:
            copies_at_risk = int(round(shortfall_parts / primary_qty_per))
        elif blocked_qty > 0 and primary_still_to_pack is not None:
            copies_at_risk = 0
        if total_rate_parts_per_hour > 0:
            # Combined burn rate across all active lines consuming this part
            hours_buffer = round(total_stock / total_rate_parts_per_hour, 1)

    impact: dict[str, Any] = {
        "product_id": primary_product,
        "copies_at_risk": copies_at_risk,
        "hours_buffer": hours_buffer,
        "due_by": _due_by_iso(ctx),
        "note": note,
    }
    return primary_product, {k: v for k, v in impact.items() if v is not None and v != ""}


def _is_erp_shipment(sh: Shipment) -> bool:
    src = (sh.source or "").lower()
    return "/erp/" in f"/{src}" or src.endswith(".csv")


def _is_tracker_shipment(sh: Shipment) -> bool:
    src = (sh.source or "").lower()
    return "tracker" in src or ".xlsx" in src


def _shipment_parts(ctx: CheckContext, shipment_id: str) -> list[str]:
    parts = list(ctx.shipment_mats.get(shipment_id, []))
    if not parts:
        for po_num in ctx.shipment_pos.get(shipment_id, []):
            for po in ctx.pos_by_number.get(po_num, []):
                if po.material_id and po.material_id not in parts:
                    parts.append(po.material_id)
    return parts


def _shipment_qty(ctx: CheckContext, shipment_id: str, part_id: str = "") -> float:
    total = 0.0
    for po_num in ctx.shipment_pos.get(shipment_id, []):
        for po in ctx.pos_by_number.get(po_num, []):
            if not part_id or po.material_id == part_id:
                total += float(po.quantity or 0)
    return total


def _is_open_po(po: PurchaseOrder) -> bool:
    return (po.status or "").strip().lower() not in CLOSED_PO_STATUSES


# ---------------------------------------------------------------------------
# T3.1: ERP shipment list vs logistics tracker
# ---------------------------------------------------------------------------
def check_erp_vs_tracker(ctx: CheckContext) -> tuple[list[CandidateFinding], list[CandidateConflict]]:
    findings: list[CandidateFinding] = []
    conflicts: list[CandidateConflict] = []

    by_id: dict[str, list[Shipment]] = defaultdict(list)
    for sh in ctx.shipments:
        by_id[sh.shipment_id].append(sh)

    late_missing: list[Shipment] = []
    urgent_cutoff = (ctx.launch_deadline + timedelta(days=3)) if ctx.launch_deadline else None

    for sid, rows in sorted(by_id.items()):
        erp = next((r for r in rows if _is_erp_shipment(r)), None)
        trk = next((r for r in rows if _is_tracker_shipment(r)), None)
        parts = _shipment_parts(ctx, sid)
        launch_hit = [p for p in parts if p in ctx.launch_parts]
        primary_part = launch_hit[0] if launch_hit else (parts[0] if parts else "")
        po_list = ctx.shipment_pos.get(sid, [])
        matching = {po.po_number for number in po_list for po in ctx.pos_by_number.get(number, [])
                    if po.material_id == primary_part}
        primary_po = next(iter(matching)) if len(matching) == 1 else ""

        if erp and trk:
            diffs: list[str] = []
            if (erp.status or "") != (trk.status or ""):
                diffs.append(f"status ERP '{erp.status}' vs tracker '{trk.status}'")
                conflicts.append(CandidateConflict(
                    entity="shipment", entity_id=sid, field="status",
                    value_a=erp.status or "", source_a=erp.source,
                    value_b=trk.status or "", source_b=trk.source,
                ))
            if erp.eta and trk.eta and erp.eta != trk.eta:
                diffs.append(f"ETA ERP {erp.eta} vs tracker {trk.eta}")
                conflicts.append(CandidateConflict(
                    entity="shipment", entity_id=sid, field="eta",
                    value_a=str(erp.eta), source_a=erp.source,
                    value_b=str(trk.eta), source_b=trk.source,
                ))
            if diffs:
                qty = int(round(_shipment_qty(ctx, sid, primary_part)))
                at_port = (erp.status == "arrived_port")
                is_launch_relevant = bool(launch_hit)
                product_id, impact = compute_part_impact(
                    ctx, primary_part,
                    note=f"{sid} ({qty:,} pcs {primary_part}) — {'; '.join(diffs)}" if qty else "; ".join(diffs),
                ) if primary_part else ("", {})
                if at_port and is_launch_relevant:
                    kind = IssueKind.COPIES_AT_RISK
                    severity = Severity.HIGH
                    summary = _truncate(
                        f"{sid} ({primary_part}) already at port in ERP but tracker says '{trk.status}'"
                    )
                    actions = [
                        {
                            "type": "expedite",
                            "label": f"Dispatch last-mile pickup for {sid} ({erp.note or 'at port'})",
                            "target": sid,
                            "owner": "planner",
                            "copies_gained": min(qty, impact.get("copies_at_risk") or qty) if qty else None,
                        },
                        {
                            "type": "email",
                            "label": f"Confirm terminal release for {sid} with {erp.carrier_id or 'carrier'}",
                            "target": erp.supplier_id or sid,
                            "owner": "chaser",
                        },
                    ]
                else:
                    kind = IssueKind.MISSING_INFO
                    severity = Severity.MEDIUM if is_launch_relevant else Severity.LOW
                    summary = _truncate(f"{sid} ({primary_part}): ERP vs tracker mismatch ({'; '.join(diffs)})")
                    actions = [
                        {
                            "type": "email",
                            "label": f"Verify live status of {sid} with carrier {erp.carrier_id or ''}".strip(),
                            "target": erp.supplier_id or sid,
                            "owner": "chaser",
                        }
                    ]
                findings.append(CandidateFinding(
                    type="tracker_mismatch",
                    kind=kind,
                    severity=severity,
                    key=sid,
                    summary=summary,
                    reason=(
                        f"ERP ({erp.source}) and logistics tracker ({trk.source}) disagree on {sid}: "
                        f"{'; '.join(diffs)}."
                        + (f" ERP note: '{erp.note}'." if erp.note else "")
                        + (f" Tracker note: '{trk.note}'." if trk.note else "")
                    ),
                    subject_type="shipment",
                    subject_id=sid,
                    part_id=primary_part,
                    supplier_id=erp.supplier_id or trk.supplier_id,
                    shipment_id=sid,
                    po_number=primary_po,
                    product_id=product_id,
                    impact=impact,
                    suggested_actions=[{k: v for k, v in a.items() if v is not None} for a in actions],
                    evidence=[
                        Evidence(path=erp.source, locator=sid, quote=f"status={erp.status}, eta={erp.eta}, note={erp.note}"),
                        Evidence(path=trk.source, locator=sid, quote=f"status={trk.status}, eta={trk.eta}, note={trk.note}"),
                    ],
                ))

        elif erp and not trk and (erp.status or "").lower() in ACTIVE_SHIPMENT_STATUSES:
            is_urgent = (
                (erp.mode or "").lower() in {"air", "road"}
                or (urgent_cutoff is not None and erp.eta is not None and erp.eta <= urgent_cutoff)
            )
            if is_urgent and launch_hit:
                qty = int(round(_shipment_qty(ctx, sid, primary_part)))
                product_id, impact = compute_part_impact(
                    ctx, primary_part,
                    note=f"Urgent {erp.mode} shipment {sid} (ETA {erp.eta}, {qty:,} pcs) missing from tracker",
                )
                findings.append(CandidateFinding(
                    type="missing_from_tracker",
                    kind=IssueKind.MISSING_INFO if primary_po else IssueKind.HUMAN_CHECK,
                    severity=Severity.MEDIUM,
                    key=sid,
                    summary=_truncate(
                        f"Rush {erp.mode} shipment {sid} ({primary_part}, ETA {erp.eta}) missing from logistics tracker"
                    ),
                    reason=(
                        f"ERP lists active {erp.mode} shipment {sid} ({erp.status}, ETA {erp.eta}, "
                        f"PO {', '.join(po_list) or 'n/a'}) for launch part {primary_part}, "
                        f"but it is absent from the shared logistics tracker."
                    ),
                    subject_type="shipment",
                    subject_id=sid,
                    part_id=primary_part,
                    supplier_id=erp.supplier_id,
                    shipment_id=sid,
                    po_number=primary_po,
                    product_id=product_id,
                    impact=impact,
                    suggested_actions=[
                        {
                            "type": "email" if primary_po else "human_review",
                            "label": f"Request live tracking & departure confirmation for {sid} ({erp.reference or erp.mode})",
                            "target": erp.supplier_id or sid,
                            "owner": "chaser" if primary_po else "human",
                        }
                    ],
                    evidence=[
                        Evidence(path=erp.source, locator=sid, quote=f"mode={erp.mode}, status={erp.status}, eta={erp.eta}"),
                    ],
                ))
            else:
                late_missing.append(erp)

    if late_missing:
        ids = [s.shipment_id for s in late_missing]
        sample_list = ", ".join(ids[:5]) + (f" (+{len(ids) - 5} more)" if len(ids) > 5 else "")
        findings.append(CandidateFinding(
            type="missing_from_tracker",
            kind=IssueKind.MISSING_INFO,
            severity=Severity.LOW,
            key="late_or_nonlaunch_batch",
            summary=_truncate(
                f"{len(late_missing)} non-rush ERP shipments missing from logistics tracker ({sample_list})"
            ),
            reason=(
                f"{len(late_missing)} open ERP shipments ({', '.join(ids)}) do not appear in the logistics "
                f"tracker spreadsheet. Because their ETAs are post-launch or non-GH6, they are grouped for cleanup."
            ),
            subject_type="shipment",
            subject_id=ids[0],
            shipment_id=ids[0],
            suggested_actions=[
                {
                    "type": "other",
                    "label": f"Sync {len(late_missing)} open ERP shipments into the logistics tracker",
                    "target": "logistics_tracker",
                    "owner": "human",
                }
            ],
            evidence=[
                Evidence(path=s.source, locator=s.shipment_id, quote=f"mode={s.mode}, eta={s.eta}, status={s.status}")
                for s in late_missing[:10]
            ],
        ))

    return findings, conflicts


# ---------------------------------------------------------------------------
# T3.2: Silent tracking feeds (updated_at stopped moving)
# ---------------------------------------------------------------------------
def check_stale_feeds(ctx: CheckContext, stale_hours: float = STALE_FEED_HOURS) -> list[CandidateFinding]:
    findings: list[CandidateFinding] = []
    # Use the shipment feed's own clock (max updated_at across all shipments) so a later news/ECN drop
    # does not falsely mark every normal shipment in an older ERP export as silent.
    feed_clock = ctx.shipment_feed_now or ctx.reference_now
    for sh in ctx.shipments:
        if (sh.status or "").lower() not in ACTIVE_SHIPMENT_STATUSES:
            continue
        upd = _as_utc(sh.updated_at)
        if upd is None:
            continue
        age_hours = (feed_clock - upd).total_seconds() / 3600.0
        if age_hours < stale_hours:
            continue
        parts = _shipment_parts(ctx, sh.shipment_id)
        launch_hit = [p for p in parts if p in ctx.launch_parts]
        if not launch_hit:
            continue
        # Focus on shipments that matter near launch (arriving within 10 days of launch, or air/road/delayed)
        near_launch = (
            (sh.mode or "").lower() in {"air", "road"}
            or (sh.status or "").lower() in {"delayed", "arrived_port", "in_delivery"}
            or (ctx.launch_deadline is not None and sh.eta is not None
                and sh.eta <= ctx.launch_deadline + timedelta(days=10))
        )
        if not near_launch:
            continue
        primary_part = launch_hit[0]
        po_list = ctx.shipment_pos.get(sh.shipment_id, [])
        product_id, impact = compute_part_impact(
            ctx, primary_part,
            note=f"Tracking silent for {age_hours:.1f}h (last update {upd.isoformat()})",
        )
        reason = (
            f"Shipment {sh.shipment_id} ({sh.mode}, carrier {sh.carrier_id or 'unknown'}, ETA {sh.eta}) "
            f"last reported at {upd.strftime('%Y-%m-%d %H:%M UTC')} — {age_hours:.1f}h before the latest "
            f"feed timestamp ({feed_clock.strftime('%Y-%m-%d %H:%M UTC')}), exceeding the {stale_hours:.0f}h threshold."
        )
        findings.append(CandidateFinding(
            type="stale_feed",
            kind=IssueKind.MISSING_INFO,
            severity=Severity.MEDIUM if age_hours < 36 else Severity.HIGH,
            key=sh.shipment_id,
            summary=_truncate(
                f"Tracking feed silent for {age_hours:.0f}h on {sh.shipment_id} ({primary_part}, {sh.status})"
            ),
            reason=reason,
            subject_type="shipment",
            subject_id=sh.shipment_id,
            part_id=primary_part,
            supplier_id=sh.supplier_id,
            shipment_id=sh.shipment_id,
            po_number=po_list[0] if po_list else "",
            product_id=product_id,
            impact=impact,
            suggested_actions=[
                {
                    "type": "email",
                    "label": f"Chase {sh.carrier_id or sh.supplier_id or 'carrier'} for live position of {sh.shipment_id}",
                    "target": sh.supplier_id or sh.shipment_id,
                    "owner": "chaser",
                }
            ],
            evidence=[
                Evidence(path=sh.source, locator=sh.shipment_id,
                         quote=f"updated_at={sh.updated_at}, status={sh.status}, eta={sh.eta}"),
            ],
        ))
    return findings


# ---------------------------------------------------------------------------
# T3.3: Order vs supplier confirmation (quantity, date, price, short-shipment advice)
# ---------------------------------------------------------------------------
def check_po_vs_confirmation(ctx: CheckContext) -> tuple[list[CandidateFinding], list[CandidateConflict]]:
    findings: list[CandidateFinding] = []
    conflicts: list[CandidateConflict] = []

    for oc in ctx.confirmations:
        po = ctx.po_by_key.get((oc.po_number, oc.po_line))
        if po is None:
            # Fallback match by po_number + material_id
            po = next((p for p in ctx.pos_by_number.get(oc.po_number, []) if p.material_id == oc.material_id), None)
        if po is None or not _is_open_po(po):
            continue
        if po.material_id not in ctx.launch_parts:
            continue

        diffs: list[str] = []
        copies_at_risk_trigger = False

        # Quantity mismatch
        if po.quantity is not None and oc.quantity is not None and abs(po.quantity - oc.quantity) > 0.5:
            diffs.append(f"qty ordered {int(po.quantity):,} vs confirmed {int(oc.quantity):,}")
            conflicts.append(CandidateConflict(
                entity="purchase_order", entity_id=f"{po.po_number}/{po.po_line}", field="quantity",
                value_a=str(po.quantity), source_a=po.source,
                value_b=str(oc.quantity), source_b=oc.source,
            ))
            if oc.quantity < po.quantity:
                copies_at_risk_trigger = True

        # Delivery date slip
        ref_date = po.promised_date or po.requested_date
        if ref_date and oc.delivery_date and oc.delivery_date > ref_date:
            slip_days = (oc.delivery_date - ref_date).days
            diffs.append(f"date slipped +{slip_days}d ({ref_date} → {oc.delivery_date})")
            conflicts.append(CandidateConflict(
                entity="purchase_order", entity_id=f"{po.po_number}/{po.po_line}", field="delivery_date",
                value_a=str(ref_date), source_a=po.source,
                value_b=str(oc.delivery_date), source_b=oc.source,
            ))
            if ctx.launch_deadline and oc.delivery_date > ctx.launch_deadline and ref_date <= ctx.launch_deadline + timedelta(days=30):
                copies_at_risk_trigger = True

        # Price change > 5%
        if po.unit_price and oc.unit_price and po.currency == oc.currency:
            ratio = abs(oc.unit_price - po.unit_price) / max(po.unit_price, 1e-6)
            if ratio >= PRICE_DIFF_RATIO:
                diffs.append(f"price {po.unit_price} vs {oc.unit_price} {po.currency}")
                conflicts.append(CandidateConflict(
                    entity="purchase_order", entity_id=f"{po.po_number}/{po.po_line}", field="unit_price",
                    value_a=f"{po.unit_price} {po.currency}", source_a=po.source,
                    value_b=f"{oc.unit_price} {oc.currency}", source_b=oc.source,
                ))

        if diffs:
            key = f"{po.po_number}/{po.po_line}"
            product_id, impact = compute_part_impact(
                ctx, po.material_id,
                note=f"PO {key} ({po.supplier_id}): {'; '.join(diffs)}",
            )
            findings.append(CandidateFinding(
                type="po_confirmation_mismatch",
                kind=IssueKind.COPIES_AT_RISK if copies_at_risk_trigger else IssueKind.MISSING_INFO,
                severity=Severity.HIGH if copies_at_risk_trigger else Severity.MEDIUM,
                key=key,
                summary=_truncate(
                    f"PO {key} ({po.material_id}) disagrees with confirmation {oc.confirmation_id}: {'; '.join(diffs)}"
                ),
                reason=(
                    f"Supplier confirmation {oc.confirmation_id} ({oc.supplier_id}) does not match "
                    f"ERP PO {key} for {po.material_id}: {'; '.join(diffs)}."
                ),
                subject_type="po",
                subject_id=po.po_number,
                part_id=po.material_id,
                supplier_id=po.supplier_id or oc.supplier_id,
                po_number=po.po_number,
                product_id=product_id,
                impact=impact,
                suggested_actions=[
                    {
                        "type": "email",
                        "label": f"Clarify PO {key} discrepancy ({diffs[0]}) with {po.supplier_id}",
                        "target": po.supplier_id,
                        "owner": "chaser",
                    }
                ],
                evidence=[
                    Evidence(path=po.source, locator=f"PO {key}",
                             quote=f"qty={po.quantity}, promised={po.promised_date}, price={po.unit_price}"),
                    Evidence(path=oc.source, locator=oc.confirmation_id or f"PO {key}",
                             quote=f"qty={oc.quantity}, delivery={oc.delivery_date}, price={oc.unit_price}"),
                ],
            ))

    # Also catch shipping advices reporting short shipments / carrier offloads (e.g. Hwaseong AWB 020-48113256)
    for doc in ctx.documents:
        if doc.kind != "shipping_advice":
            continue
        doc_facts = (doc.extracted or {}).get("facts") or {}
        signals = set(doc_facts.get("signals") or [])
        if "short_shipment" not in signals:
            continue
        parts = [p for p in (doc_facts.get("parts") or []) if p in ctx.launch_parts]
        part_id = parts[0] if parts else (doc_facts.get("parts") or [""])[0]
        po_nums = doc_facts.get("po_numbers") or []
        po_num = po_nums[0] if po_nums else ""
        # Find related shipment by PO
        rel_ship = next((sid for sid, pnums in ctx.shipment_pos.items() if po_num and po_num in pnums), "")
        qtys = sorted(doc_facts.get("quantities") or [])
        balance_qty = qtys[0] if qtys else None
        product_id, impact = compute_part_impact(
            ctx, part_id,
            note=f"Short shipment on PO {po_num} ({doc.path}); balance offloaded past launch",
        ) if part_id else ("", {})
        if balance_qty and product_id:
            # Check if balance arrives after launch
            dates = sorted(doc_facts.get("dates") or [])
            late_dates = [d for d in dates if ctx.launch_deadline and d > ctx.launch_deadline.isoformat()]
            if late_dates:
                impact["note"] = f"Short shipment: {balance_qty:,} pcs offloaded, next flight arrives {late_dates[-1]}"
        findings.append(CandidateFinding(
            type="short_shipment",
            kind=IssueKind.COPIES_AT_RISK,
            severity=Severity.HIGH,
            key=f"{po_num or doc.path}|{part_id}",
            summary=_truncate(
                f"Short shipment on PO {po_num} ({part_id}): "
                + (f"{balance_qty:,} pcs offloaded past launch" if balance_qty else "partial quantity offloaded")
            ),
            reason=(
                f"Shipping advice ({doc.path}) from {doc.supplier_id or 'supplier'} reports a short shipment "
                f"for {part_id} on PO {po_num}. The offloaded balance will miss the launch packing window."
            ),
            subject_type="shipment" if rel_ship else "po",
            subject_id=rel_ship or po_num,
            part_id=part_id,
            supplier_id=doc.supplier_id,
            shipment_id=rel_ship,
            po_number=po_num,
            product_id=product_id,
            impact=impact,
            suggested_actions=[
                {
                    "type": "expedite",
                    "label": f"Rebook offloaded {part_id} balance onto earlier air freight or reallocate stock",
                    "target": doc.supplier_id or part_id,
                    "owner": "planner",
                    "copies_gained": balance_qty,
                },
                {
                    "type": "email",
                    "label": f"Ask {doc.supplier_id} for exact AWB & earliest flight for offloaded balance",
                    "target": doc.supplier_id,
                    "owner": "chaser",
                },
            ],
            evidence=[
                Evidence(path=doc.path, locator="shipping_advice", quote=_truncate(doc.text, 180)),
            ],
        ))

    return findings, conflicts


# ---------------------------------------------------------------------------
# T3.4: Revision gate (ECN-2026-118 rev B blocked; ECN-2026-119 rev A use-up allowed)
# ---------------------------------------------------------------------------
def _infer_batch_revision(ctx: CheckContext, lot: StockLot) -> tuple[str, str, str]:
    """Trace a StockLot's revision via StockLot.revision -> GoodsReceipt -> OrderConfirmation.

    Returns (revision, supplier_id, evidence_source).
    """
    if lot.revision:
        return lot.revision.upper(), "", lot.source
    gr = ctx.gr_by_number.get(lot.batch)
    if gr is None:
        return "", "", lot.source
    ocs = [oc for oc in ctx.ocs_by_po.get(gr.po_number, []) if oc.material_id == lot.material_id and oc.revision]
    if ocs:
        return ocs[-1].revision.upper(), gr.supplier_id, ocs[-1].source
    return "", gr.supplier_id, gr.source


def check_revision_gate(ctx: CheckContext) -> list[CandidateFinding]:
    findings: list[CandidateFinding] = []

    for ecn in ctx.ecns:
        if not ecn.child_id or not ecn.rev_old or not ecn.rev_new:
            continue
        if ecn.use_up_old_rev is True:
            # Explicitly permitted (e.g. ECN-2026-119 CMP-GAM-009 rev A -> B: use up rev A)
            continue
        if ecn.use_up_old_rev is not False:
            continue

        part_id = ecn.child_id
        forbidden_rev = ecn.rev_old.upper()
        required_rev = ecn.rev_new.upper()

        # 1. Inspect warehouse stock lots for forbidden revision
        blocked_lots: list[tuple[StockLot, str, str]] = []  # (lot, supplier_id, ev_src)
        usable_lots: list[StockLot] = []
        unknown_lots: list[StockLot] = []
        for lot in ctx.stock:
            if lot.material_id != part_id or not (lot.quantity or 0):
                continue
            rev, sup_id, ev_src = _infer_batch_revision(ctx, lot)
            if rev == forbidden_rev:
                blocked_lots.append((lot, sup_id, ev_src))
            elif rev == required_rev:
                usable_lots.append(lot)
            else:
                unknown_lots.append(lot)

        blocked_stock_qty = sum(float(l.quantity or 0) for l, _, _ in blocked_lots)
        usable_stock_qty = sum(float(l.quantity or 0) for l in usable_lots)

        # 2. Inspect open inbound confirmations/POs for this part
        blocked_ocs: list[OrderConfirmation] = []
        unconfirmed_rev_pos: list[tuple[PurchaseOrder, Optional[OrderConfirmation]]] = []
        for po in ctx.pos:
            if po.material_id != part_id or not _is_open_po(po):
                continue
            ocs = [oc for oc in ctx.ocs_by_po.get(po.po_number, [])
                   if oc.material_id == part_id and (oc.po_line is None or oc.po_line == po.po_line)]
            oc = ocs[-1] if ocs else None
            if oc and (oc.revision or "").upper() == forbidden_rev:
                blocked_ocs.append(oc)
            elif oc is None or not (oc.revision or "").strip():
                unconfirmed_rev_pos.append((po, oc))

        blocked_inbound_qty = sum(float(oc.quantity or 0) for oc in blocked_ocs)
        if blocked_stock_qty > 0 or blocked_inbound_qty > 0:
            blocked_sups = sorted({sup for _, sup, _ in blocked_lots if sup} | {oc.supplier_id for oc in blocked_ocs if oc.supplier_id})
            primary_sup = blocked_sups[0] if blocked_sups else ""
            # Find any rush offer supplier for this part (e.g. Karton Nowak SUP-0028)
            offer_doc = next(
                (d for d in ctx.documents if d.kind == "offer" and d.supplier_id in {sp.supplier_id for sp in ctx.spm_by_part.get(part_id, [])}),
                None,
            )
            product_id, impact = compute_part_impact(
                ctx, part_id,
                usable_qty_override=usable_stock_qty,
                note=(
                    f"{int(blocked_stock_qty):,} pcs stock & {int(blocked_inbound_qty):,} pcs open orders "
                    f"on forbidden rev {forbidden_rev}; {int(usable_stock_qty):,} pcs rev {required_rev} usable"
                ),
            )
            copies_short = impact.get("copies_at_risk")
            if copies_short and copies_short > 0:
                headline = f"{part_id} rev {forbidden_rev} blocked ({ecn.change_id}): {copies_short:,} copies short"
                severity = Severity.LINE_STOP
            else:
                headline = (
                    f"{part_id} rev {forbidden_rev} blocked ({ecn.change_id}): "
                    f"{int(blocked_stock_qty):,} stock + {int(blocked_inbound_qty):,} inbound unusable"
                )
                severity = Severity.HIGH

            actions: list[dict[str, Any]] = [
                {
                    "type": "block_stock",
                    "label": f"Quarantine {int(blocked_stock_qty):,} pcs rev {forbidden_rev} stock "
                             f"({', '.join(l.batch for l, _, _ in blocked_lots)})",
                    "target": part_id,
                    "owner": "human",
                }
            ]
            if blocked_ocs:
                actions.append({
                    "type": "email",
                    "label": f"Stop rev {forbidden_rev} production & switch open POs to rev {required_rev} with {primary_sup}",
                    "target": primary_sup,
                    "owner": "chaser",
                })
            if offer_doc:
                actions.append({
                    "type": "expedite",
                    "label": f"Evaluate rush rev {required_rev} run via {offer_doc.supplier_id} ({offer_doc.path.rsplit('/', 1)[-1]})",
                    "target": offer_doc.supplier_id,
                    "owner": "planner",
                })

            ev = [
                Evidence(path=ecn.source, locator=ecn.change_id,
                         quote=f"{part_id} rev {forbidden_rev}->{required_rev}, use_up=N: {ecn.reason}"),
            ]
            for lot, _, ev_src in blocked_lots[:5]:
                ev.append(Evidence(path=ev_src or lot.source, locator=f"batch {lot.batch}",
                                   quote=f"{int(lot.quantity or 0):,} pcs confirmed as rev {forbidden_rev}"))
            for oc in blocked_ocs[:5]:
                ev.append(Evidence(path=oc.source, locator=f"{oc.confirmation_id}/PO {oc.po_number}",
                                   quote=f"{int(oc.quantity or 0):,} pcs confirmed rev {forbidden_rev}"))

            findings.append(CandidateFinding(
                type="rev_blocked",
                kind=IssueKind.COPIES_AT_RISK,
                severity=severity,
                key=f"{part_id}/{forbidden_rev}",
                summary=_truncate(headline),
                reason=(
                    f"{ecn.change_id} makes rev {required_rev} mandatory for {part_id} ({ecn.reason}). "
                    f"{int(blocked_stock_qty):,} pcs in DC-HAM stock ({', '.join(l.batch for l, _, _ in blocked_lots) or 'none'}) "
                    f"and {len(blocked_ocs)} open confirmation(s) ({int(blocked_inbound_qty):,} pcs) are rev {forbidden_rev} "
                    f"and cannot be packed; usable rev {required_rev} stock is {int(usable_stock_qty):,} pcs."
                ),
                subject_type="part",
                subject_id=part_id,
                part_id=part_id,
                supplier_id=primary_sup,
                product_id=product_id or ecn.parent_id,
                impact=impact,
                suggested_actions=actions,
                evidence=ev,
            ))

        # 3. Any open PO/confirmation for this part where revision is STILL missing -> Chaser asks supplier
        for po, oc in unconfirmed_rev_pos:
            key = f"{po.po_number}/{po.po_line}/{part_id}"
            rel_ship = next((sid for sid, pnums in ctx.shipment_pos.items() if po.po_number in pnums), "")
            product_id, impact = compute_part_impact(
                ctx, part_id,
                note=f"Open PO {po.po_number}/{po.po_line} ({int(po.quantity or 0):,} pcs) has no confirmed revision after {ecn.change_id}",
            )
            findings.append(CandidateFinding(
                type="missing_revision",
                kind=IssueKind.MISSING_INFO,
                severity=Severity.HIGH,
                key=key,
                summary=_truncate(
                    f"Confirm revision {required_rev} on PO {po.po_number}/{po.po_line} ({part_id}) with {po.supplier_id}"
                ),
                reason=(
                    f"{ecn.change_id} forbids rev {forbidden_rev} for {part_id}. Open PO {po.po_number}/{po.po_line} "
                    f"({int(po.quantity or 0):,} pcs from {po.supplier_id}) has no confirmed drawing revision on file."
                ),
                subject_type="po",
                subject_id=po.po_number,
                part_id=part_id,
                supplier_id=po.supplier_id,
                shipment_id=rel_ship,
                po_number=po.po_number,
                product_id=product_id or ecn.parent_id,
                impact=impact,
                suggested_actions=[
                    {
                        "type": "email",
                        "label": f"Ask {po.supplier_id} whether PO {po.po_number} is produced to rev {required_rev}",
                        "target": po.supplier_id,
                        "owner": "chaser",
                    }
                ],
                evidence=[
                    Evidence(path=ecn.source, locator=ecn.change_id, quote=f"rev {required_rev} mandatory"),
                    Evidence(path=oc.source if oc else po.source, locator=f"PO {po.po_number}/{po.po_line}",
                             quote="revision blank"),
                ],
            ))

    return findings


# ---------------------------------------------------------------------------
# T3.5: Stuck PO approvals, expired supplier qualification, quality inspection & downtime
# ---------------------------------------------------------------------------
def check_approvals_and_supplier_health(ctx: CheckContext) -> list[CandidateFinding]:
    findings: list[CandidateFinding] = []
    ref_date = ctx.reference_now.date()

    # 1. POs stuck awaiting_approval
    stuck_by_sup_part: dict[tuple[str, str], list[PurchaseOrder]] = defaultdict(list)
    for po in ctx.pos:
        if (po.status or "").strip().lower() in {"awaiting_approval", "pending_approval"}:
            stuck_by_sup_part[(po.supplier_id, po.material_id)].append(po)

    for (sup_id, part_id), po_list in sorted(stuck_by_sup_part.items()):
        total_qty = int(round(sum(float(p.quantity or 0) for p in po_list)))
        po_nums = sorted({p.po_number for p in po_list})
        sup = ctx.suppliers.get(sup_id)
        sup_name = sup.name if sup else sup_id
        product_id, impact = compute_part_impact(
            ctx, part_id,
            note=f"{len(po_list)} PO(s) stuck awaiting_approval ({total_qty:,} pcs)",
        ) if part_id else ("", {})
        findings.append(CandidateFinding(
            type="po_stuck_approval",
            kind=IssueKind.COPIES_AT_RISK if part_id in ctx.launch_parts else IssueKind.HUMAN_CHECK,
            severity=Severity.HIGH if part_id in ctx.launch_parts else Severity.MEDIUM,
            key=f"{sup_id}|{part_id}",
            summary=_truncate(
                f"{len(po_list)} PO(s) for {part_id} at {sup_name} ({total_qty:,} pcs) stuck awaiting approval"
            ),
            reason=(
                f"Purchase order(s) {', '.join(po_nums)} for {part_id} ({total_qty:,} pcs from {sup_name}) "
                f"remain in status 'awaiting_approval' and cannot ship until approved."
            ),
            subject_type="po",
            subject_id=po_nums[0],
            part_id=part_id,
            supplier_id=sup_id,
            po_number=po_nums[0],
            product_id=product_id,
            impact=impact,
            suggested_actions=[
                {
                    "type": "human_review",
                    "label": f"Approve or re-route stuck POs ({', '.join(po_nums)}) for {part_id}",
                    "target": sup_id,
                    "owner": "planner",
                }
            ],
            evidence=[
                Evidence(path=p.source, locator=f"PO {p.po_number}/{p.po_line}",
                         quote=f"status={p.status}, qty={p.quantity}, promised={p.promised_date}")
                for p in po_list
            ],
        ))

    # 2. Expired supplier qualification or watch/at_risk status
    for sup_id, sup in sorted(ctx.suppliers.items()):
        expired = sup.qualification_valid_until is not None and sup.qualification_valid_until < ref_date
        bad_status = (sup.status or "").strip().lower() in {"watch", "suspended", "blocked"}
        if not (expired or bad_status):
            continue
        sup_parts = [sp.component_id for part_list in ctx.spm_by_part.values()
                     for sp in part_list if sp.supplier_id == sup_id and sp.component_id in ctx.launch_parts]
        part_id = sup_parts[0] if sup_parts else ""
        product_id, impact = compute_part_impact(
            ctx, part_id,
            note=f"Supplier {sup_id} ({sup.name}) qualification={sup.qualification_status}, valid_until={sup.qualification_valid_until}",
        ) if part_id else ("", {})
        details = []
        if expired:
            details.append(f"conditional release expired {sup.qualification_valid_until}")
        if bad_status:
            details.append(f"status is '{sup.status}'")
        findings.append(CandidateFinding(
            type="supplier_qualification_expired",
            kind=IssueKind.COPIES_AT_RISK if part_id else IssueKind.HUMAN_CHECK,
            severity=Severity.HIGH if expired else Severity.MEDIUM,
            key=sup_id,
            summary=_truncate(f"{sup.name} ({sup_id}, {part_id or 'supplier'}): {'; '.join(details)}"),
            reason=(
                f"Supplier {sup.name} ({sup_id}) has {'; '.join(details)}. "
                f"Per Quality release policy, parts from unapproved suppliers cannot be packed without a signed concession."
            ),
            subject_type="supplier",
            subject_id=sup_id,
            part_id=part_id,
            supplier_id=sup_id,
            product_id=product_id,
            impact=impact,
            suggested_actions=[
                {
                    "type": "human_review",
                    "label": f"Request Quality extension or concession for {sup.name} ({part_id})",
                    "target": sup_id,
                    "owner": "planner",
                },
                {
                    "type": "email",
                    "label": f"Confirm quality corrective status with {sup.name}",
                    "target": sup_id,
                    "owner": "chaser",
                },
            ],
            evidence=[
                Evidence(path=sup.source, locator=sup_id,
                         quote=f"status={sup.status}, qualification={sup.qualification_status}, valid_until={sup.qualification_valid_until}"),
            ],
        ))

    # 3. Failed incoming quality inspection reports (e.g. AQL failure on lot booked unrestricted)
    for doc in ctx.documents:
        if doc.kind != "inspection":
            continue
        doc_facts = (doc.extracted or {}).get("facts") or {}
        text_low = (doc.text or "").lower()
        if not any(k in text_low for k in ("does not meet aql", "blocking requested", "reject", "concession")):
            continue
        parts = [p for p in (doc_facts.get("parts") or []) if p in ctx.launch_parts]
        part_id = parts[0] if parts else (doc_facts.get("parts") or [""])[0]
        po_num = (doc_facts.get("po_numbers") or [""])[0]
        # Extract lot/GR number from filename or text
        lot = next((l for l in ctx.stock if l.batch and l.batch in doc.path), None)
        lot_qty = int(lot.quantity) if (lot and lot.quantity) else 0
        gr = ctx.gr_by_number.get(lot.batch) if lot else None
        sup_id = doc.supplier_id or (gr.supplier_id if gr else "")
        product_id, impact = compute_part_impact(
            ctx, part_id,
            blocked_qty=float(lot_qty),
            note=f"Lot {lot.batch if lot else 'n/a'} ({lot_qty:,} pcs) failed AQL inspection; booked unrestricted",
        ) if part_id else ("", {})
        findings.append(CandidateFinding(
            type="quality_hold_failed_aql",
            kind=IssueKind.COPIES_AT_RISK,
            severity=Severity.HIGH,
            key=f"{part_id}|{lot.batch if lot else doc.path}",
            summary=_truncate(
                f"{part_id} lot {lot.batch if lot else ''} ({lot_qty:,} pcs) failed AQL inspection but sits unrestricted in stock"
            ),
            reason=(
                f"Incoming inspection report ({doc.path}) rejected {part_id} lot {lot.batch if lot else 'n/a'} "
                f"({lot_qty:,} pcs from {sup_id or 'supplier'}), yet receiving booked it as unrestricted stock. "
                f"Nothing may ship without Quality release or signed concession."
            ),
            subject_type="part",
            subject_id=part_id,
            part_id=part_id,
            supplier_id=sup_id,
            po_number=po_num,
            product_id=product_id,
            impact=impact,
            suggested_actions=[
                {
                    "type": "block_stock",
                    "label": f"Quarantine {part_id} lot {lot.batch if lot else ''} ({lot_qty:,} pcs) pending Quality concession",
                    "target": part_id,
                    "owner": "human",
                },
                {
                    "type": "human_review",
                    "label": f"Decide concession vs replacement for {lot_qty:,} pcs of {part_id}",
                    "target": part_id,
                    "owner": "planner",
                },
            ],
            evidence=[
                Evidence(path=doc.path, locator=lot.batch if lot else "inspection", quote=_truncate(doc.text, 180)),
            ],
        ))

    # 4. Supplier downtime notices during launch window
    for doc in ctx.documents:
        if doc.kind != "notice":
            continue
        doc_facts = (doc.extracted or {}).get("facts") or {}
        signals = set(doc_facts.get("signals") or [])
        if "downtime" not in signals or not doc.supplier_id:
            continue
        sup = ctx.suppliers.get(doc.supplier_id)
        sup_name = sup.name if sup else doc.supplier_id
        sup_parts = sorted({sp.component_id for plist in ctx.spm_by_part.values()
                            for sp in plist if sp.supplier_id == doc.supplier_id and sp.component_id in ctx.launch_parts})
        primary_part = sup_parts[0] if sup_parts else ""
        product_id, impact = compute_part_impact(
            ctx, primary_part,
            note=f"{sup_name} planned downtime limits rush capacity during launch weekend",
        ) if primary_part else ("", {})
        findings.append(CandidateFinding(
            type="supplier_downtime",
            kind=IssueKind.COPIES_AT_RISK,
            severity=Severity.HIGH,
            key=f"{doc.supplier_id}|downtime",
            summary=_truncate(
                f"{sup_name} ({doc.supplier_id}) planned downtime limits rush capacity for {', '.join(sup_parts[:3]) or 'parts'}"
            ),
            reason=(
                f"Downtime notice ({doc.path}) from {sup_name} restricts main press / bindery capacity over "
                f"launch weekend (rush print capped on digital line). Impacts rush recovery options for {', '.join(sup_parts)}."
            ),
            subject_type="supplier",
            subject_id=doc.supplier_id,
            part_id=primary_part,
            supplier_id=doc.supplier_id,
            product_id=product_id,
            impact=impact,
            suggested_actions=[
                {
                    "type": "email",
                    "label": f"Confirm available digital/express slot & revision capability with {sup_name}",
                    "target": doc.supplier_id,
                    "owner": "chaser",
                },
                {
                    "type": "expedite",
                    "label": f"Book available express capacity at {sup_name} before cutoff",
                    "target": doc.supplier_id,
                    "owner": "planner",
                },
            ],
            evidence=[
                Evidence(path=doc.path, locator="downtime_notice", quote=_truncate(doc.text, 180)),
            ],
        ))

    return findings


# ---------------------------------------------------------------------------
# T3.6: Single source + zero stock / export control / bad news
# ---------------------------------------------------------------------------
def check_single_source_and_news(ctx: CheckContext) -> list[CandidateFinding]:
    findings: list[CandidateFinding] = []

    export_news = [
        d for d in ctx.documents
        if d.kind in {"news", "notice"} and (
            "export_control" in ((d.extracted or {}).get("facts", {}).get("signals") or [])
            or "export" in (d.text or "").lower()
        )
    ]

    for part_id in sorted(ctx.launch_parts):
        sp_rows = ctx.spm_by_part.get(part_id, [])
        if len(sp_rows) != 1:
            continue
        sp = sp_rows[0]
        sup = ctx.suppliers.get(sp.supplier_id)
        sup_name = sup.name if sup else sp.supplier_id
        stock_qty = _usable_stock_for_part(ctx, part_id)
        open_pos = [po for po in ctx.pos if po.material_id == part_id and _is_open_po(po)]
        mat = ctx.materials.get(part_id)
        mat_desc = mat.description if mat else part_id

        # Match export control news or supplier notices affecting this part/supplier
        part_note_low = (sp.note or "").lower()
        mat_desc_low = mat_desc.lower()
        matched_docs: list[Document] = []
        for doc in export_news:
            txt_low = (doc.text or "").lower()
            if doc.supplier_id and doc.supplier_id == sp.supplier_id:
                matched_docs.append(doc)
            elif any(tok in txt_low and tok in (mat_desc_low + " " + part_note_low)
                     for tok in ("magnet", "ndfeb", "rare-earth")):
                matched_docs.append(doc)

        has_export_risk = bool(matched_docs) or any(
            k in part_note_low for k in ("cannot export", "export", "suspended", "+42")
        )
        zero_stock_no_po = (stock_qty <= 0 and not open_pos)

        if part_id in ctx.embedded and not has_export_risk:
            continue
        if not (zero_stock_no_po or has_export_risk):
            continue

        product_id, impact = compute_part_impact(
            ctx, part_id,
            usable_qty_override=stock_qty,
            note=f"Single source {sup_name} ({sp.supplier_id}); stock={int(stock_qty):,}; open POs={len(open_pos)}"
                 + (f"; {sp.note}" if sp.note else ""),
        )
        copies_at_risk = impact.get("copies_at_risk")
        if zero_stock_no_po and has_export_risk:
            severity = Severity.LINE_STOP
            ftype = "single_source_export_ban"
            summary = _truncate(
                f"{part_id} ({mat_desc}): 0 stock, no PO, single-source {sup_name} hit by export controls"
            )
            reason = (
                f"{part_id} is single-sourced via {sup_name} ({sp.supplier_id}) with 0 DC-HAM stock and no open POs. "
                f"Export-control news/notices suspend magnet sub-supplier exports (4-8 weeks licence delay). "
                f"Verify whether magnets are already pre-assembled inside {sup_name}'s Collector's boxes."
            )
            actions = [
                {
                    "type": "email",
                    "label": f"Confirm with {sup_name} if {part_id} is pre-installed in November Collector's boxes",
                    "target": sp.supplier_id,
                    "owner": "chaser",
                },
                {
                    "type": "second_source",
                    "label": f"Qualify non-CN second source or alternate closure for {part_id}",
                    "target": part_id,
                    "owner": "planner",
                },
            ]
        elif zero_stock_no_po:
            severity = Severity.HIGH
            ftype = "single_source_no_stock"
            summary = _truncate(
                f"{part_id} ({mat_desc}): 0 stock, no open PO, single source {sup_name}"
            )
            reason = (
                f"{part_id} is required in the BOM for {product_id} (single supplier {sup_name}), "
                f"but DC-HAM shows 0 stock and 0 open purchase orders."
            )
            actions = [
                {
                    "type": "email",
                    "label": f"Verify with {sup_name} how {part_id} is supplied (pre-assembled or separate)",
                    "target": sp.supplier_id,
                    "owner": "chaser",
                },
                {
                    "type": "second_source",
                    "label": f"Establish second source for single-source part {part_id}",
                    "target": part_id,
                    "owner": "planner",
                },
            ]
        else:
            severity = Severity.HIGH if (copies_at_risk and copies_at_risk > 0) else Severity.MEDIUM
            ftype = "single_source_export_risk"
            summary = _truncate(
                f"{part_id} ({mat_desc}): single source {sup_name} affected by export-licence lead-time jump ({sp.lead_time_days}d)"
            )
            reason = (
                f"{part_id} is 100% single-sourced at {sup_name} ({sp.supplier_id}). "
                f"Rare-earth export controls extend post-November lead time to {sp.lead_time_days or 'extended'} days "
                f"({sp.note or 'see supply notice'})."
            )
            actions = [
                {
                    "type": "second_source",
                    "label": f"Qualify second source or alternative closure for {part_id} (post-launch replenishment)",
                    "target": part_id,
                    "owner": "planner",
                },
                {
                    "type": "safety_stock",
                    "label": f"Confirm November magnet coverage with {sup_name} for {part_id}",
                    "target": sp.supplier_id,
                    "owner": "chaser",
                },
            ]

        if part_id in ctx.embedded:
            severity = Severity.HIGH
            summary = f"{part_id} export restrictions threaten future {ctx.embedded[part_id]} supply"
            reason = (f"The supplier fits {part_id} into {ctx.embedded[part_id]}. Finished boxes already at Hamburg "
                      "can be packed. Qualify an alternative for future box orders.")
            actions = [a for a in actions if a["type"] == "second_source"]

        ev = [
            Evidence(path=sp.source, locator=f"{sp.supplier_id}/{part_id}",
                     quote=f"share={sp.share}, lead_time={sp.lead_time_days}d, note={sp.note}"),
        ]
        for doc in matched_docs[:3]:
            ev.append(Evidence(path=doc.path, locator=doc.kind, quote=_truncate(doc.text, 160)))

        findings.append(CandidateFinding(
            type=ftype,
            kind=IssueKind.HUMAN_CHECK if part_id in ctx.embedded else IssueKind.COPIES_AT_RISK,
            severity=severity,
            key=f"{part_id}|{sp.supplier_id}",
            summary=summary,
            reason=reason,
            subject_type="part",
            subject_id=part_id,
            part_id=part_id,
            supplier_id=sp.supplier_id,
            product_id=product_id,
            impact=impact,
            suggested_actions=actions,
            evidence=ev,
        ))

    return findings


# ---------------------------------------------------------------------------
# T3.7: Bank details / IBAN changed vs contract/master -> always HUMAN_CHECK
# ---------------------------------------------------------------------------
def check_bank_details(ctx: CheckContext) -> tuple[list[CandidateFinding], list[CandidateConflict]]:
    findings: list[CandidateFinding] = []
    conflicts: list[CandidateConflict] = []

    # Build baseline official IBAN per supplier (Supplier.bank_iban or contract PDF facts)
    baseline_iban: dict[str, tuple[str, str]] = {}  # supplier_id -> (iban, source)
    for sup_id, sup in ctx.suppliers.items():
        if sup.bank_iban:
            baseline_iban[sup_id] = (sup.bank_iban.replace(" ", "").upper(), sup.source)
    for doc in ctx.documents:
        if doc.kind == "contract" and doc.supplier_id and doc.supplier_id not in baseline_iban:
            ibans = ((doc.extracted or {}).get("facts") or {}).get("ibans") or []
            if ibans:
                baseline_iban[doc.supplier_id] = (ibans[0].replace(" ", "").upper(), doc.path)

    # Check order confirmations for IBAN changes
    for oc in ctx.confirmations:
        if not oc.bank_iban or not oc.supplier_id:
            continue
        new_iban = oc.bank_iban.replace(" ", "").upper()
        base = baseline_iban.get(oc.supplier_id)
        if base and base[0] != new_iban:
            conflicts.append(CandidateConflict(
                entity="supplier", entity_id=oc.supplier_id, field="bank_iban",
                value_a=base[0], source_a=base[1], value_b=new_iban, source_b=oc.source,
            ))
            findings.append(CandidateFinding(
                type="bank_change",
                kind=IssueKind.HUMAN_CHECK,
                severity=Severity.LINE_STOP,
                key=f"{oc.supplier_id}|{new_iban}",
                summary=_truncate(
                    f"IBAN changed for {oc.supplier_id} on {oc.confirmation_id}: verify before payment (fraud check)"
                ),
                reason=(
                    f"Confirmation {oc.confirmation_id} lists IBAN {new_iban}, which differs from master/contract "
                    f"IBAN {base[0]}. Never auto-accept bank detail changes — requires out-of-band human verification."
                ),
                subject_type="supplier",
                subject_id=oc.supplier_id,
                supplier_id=oc.supplier_id,
                po_number=oc.po_number,
                suggested_actions=[
                    {
                        "type": "human_review",
                        "label": f"Call {oc.supplier_id} on verified phone number to validate IBAN change before paying",
                        "target": oc.supplier_id,
                        "owner": "human",
                    }
                ],
                evidence=[
                    Evidence(path=base[1], locator=oc.supplier_id, quote=f"contract/master IBAN {base[0]}"),
                    Evidence(path=oc.source, locator=oc.confirmation_id, quote=f"confirmation IBAN {new_iban}"),
                ],
            ))

    # Check non-contract documents (notices, replies, etc.) for bank_change signals or different IBANs
    for doc in ctx.documents:
        if doc.kind in {"contract", "order_confirmation", "policy", "org_chart", "memo"}:
            continue
        doc_facts = (doc.extracted or {}).get("facts") or {}
        signals = set(doc_facts.get("signals") or [])
        ibans = [i.replace(" ", "").upper() for i in (doc_facts.get("ibans") or [])]
        sup_id = doc.supplier_id
        base = baseline_iban.get(sup_id) if sup_id else None
        iban_mismatch = bool(base and ibans and any(i != base[0] for i in ibans))
        if "bank_change" not in signals and not iban_mismatch:
            continue
        shown_iban = next((i for i in ibans if not base or i != base[0]), ibans[0] if ibans else "new account")
        if base and iban_mismatch:
            conflicts.append(CandidateConflict(
                entity="supplier", entity_id=sup_id, field="bank_iban",
                value_a=base[0], source_a=base[1], value_b=shown_iban, source_b=doc.path,
            ))
        findings.append(CandidateFinding(
            type="bank_change",
            kind=IssueKind.HUMAN_CHECK,
            severity=Severity.LINE_STOP,
            key=f"{sup_id or doc.path}|{shown_iban}",
            summary=_truncate(
                f"Bank details change notice from {sup_id or doc.path} ({shown_iban}): mandatory human verification"
            ),
            reason=(
                f"Document {doc.path} requests or announces a bank account / IBAN change"
                + (f" ({base[0]} → {shown_iban})" if base and iban_mismatch else "")
                + ". Routed straight to human finance/procurement check to prevent invoice fraud."
            ),
            subject_type="supplier" if sup_id else "document",
            subject_id=sup_id or doc.path,
            supplier_id=sup_id,
            suggested_actions=[
                {
                    "type": "human_review",
                    "label": f"Verify bank detail change in {doc.path.rsplit('/', 1)[-1]} via callback before updating SAP",
                    "target": sup_id or doc.path,
                    "owner": "human",
                }
            ],
            evidence=[
                Evidence(path=doc.path, locator="bank_change", quote=_truncate(doc.text, 180)),
            ] + ([Evidence(path=base[1], locator=sup_id, quote=f"baseline IBAN {base[0]}")] if base else []),
        ))

    return findings, conflicts


# ---------------------------------------------------------------------------
# T3.8: Quantity jumps vs order/history (e.g. 8,000 -> 80,000 or 690 -> 790) -> HUMAN_CHECK
# ---------------------------------------------------------------------------
def check_quantity_jumps(ctx: CheckContext, jump_ratio: float = QTY_JUMP_RATIO) -> tuple[list[CandidateFinding], list[CandidateConflict]]:
    findings: list[CandidateFinding] = []
    conflicts: list[CandidateConflict] = []

    # 1. GoodsReceipt over-delivery / extreme quantity jump vs ordered (e.g. GR 5000413065: 8,000 ordered vs 80,000 received)
    for gr in ctx.goods_receipts:
        if not gr.qty_ordered or not gr.qty_received or gr.qty_ordered <= 0:
            continue
        ratio = gr.qty_received / gr.qty_ordered
        if ratio < jump_ratio:
            continue
        # Only recent / launch-relevant receipts (within 60 days of reference date or launch parts)
        if gr.material_id not in ctx.launch_parts:
            continue
        if gr.gr_date and (ctx.reference_now.date() - gr.gr_date).days > 60:
            continue
        conflicts.append(CandidateConflict(
            entity="goods_receipt", entity_id=gr.gr_number, field="qty_received",
            value_a=str(gr.qty_ordered), source_a=gr.source,
            value_b=str(gr.qty_received), source_b=gr.source,
        ))
        findings.append(CandidateFinding(
            type="quantity_jump",
            kind=IssueKind.HUMAN_CHECK,
            severity=Severity.HIGH,
            key=f"GR/{gr.gr_number}",
            summary=_truncate(
                f"Quantity jump on GR {gr.gr_number} ({gr.material_id}): {int(gr.qty_received):,} received vs {int(gr.qty_ordered):,} ordered ({ratio:.1f}x)"
            ),
            reason=(
                f"Goods receipt {gr.gr_number} (PO {gr.po_number}, {gr.supplier_id}) booked {int(gr.qty_received):,} pcs "
                f"against an order of {int(gr.qty_ordered):,} pcs ({ratio:.1f}x jump; remark: '{gr.remark or 'none'}'). "
                f"Likely decimal/zero entry error or unapproved over-delivery — requires human stock count check."
            ),
            subject_type="part",
            subject_id=gr.material_id,
            part_id=gr.material_id,
            supplier_id=gr.supplier_id,
            po_number=gr.po_number,
            suggested_actions=[
                {
                    "type": "human_review",
                    "label": f"Physically verify DC-HAM batch {gr.gr_number} ({gr.material_id}: {int(gr.qty_received):,} vs {int(gr.qty_ordered):,})",
                    "target": gr.material_id,
                    "owner": "human",
                }
            ],
            evidence=[
                Evidence(path=gr.source, locator=f"GR {gr.gr_number}",
                         quote=f"PO {gr.po_number}, qty_ordered={gr.qty_ordered}, qty_received={gr.qty_received}, remark={gr.remark}"),
            ],
        ))

    # 2. OrderConfirmation quantity jump vs PO (e.g. 690 -> 790) or OCR scan quantity mismatch
    for oc in ctx.confirmations:
        po = ctx.po_by_key.get((oc.po_number, oc.po_line))
        if po is None or not po.quantity or not oc.quantity or po.quantity <= 0:
            continue
        pct_change = abs(oc.quantity - po.quantity) / po.quantity
        if oc.quantity > po.quantity and (oc.quantity / po.quantity >= 1.10 or pct_change >= 0.10):
            key = f"OC/{oc.po_number}/{oc.po_line}"
            conflicts.append(CandidateConflict(
                entity="order_confirmation", entity_id=f"{oc.po_number}/{oc.po_line}", field="quantity",
                value_a=str(po.quantity), source_a=po.source,
                value_b=str(oc.quantity), source_b=oc.source,
            ))
            findings.append(CandidateFinding(
                type="quantity_jump",
                kind=IssueKind.HUMAN_CHECK,
                severity=Severity.HIGH,
                key=key,
                summary=_truncate(
                    f"Quantity jump on confirmation {oc.confirmation_id} ({oc.material_id}): {int(po.quantity):,} → {int(oc.quantity):,}"
                ),
                reason=(
                    f"Supplier confirmation {oc.confirmation_id} ({oc.supplier_id}) jumped quantity from "
                    f"{int(po.quantity):,} to {int(oc.quantity):,} (+{pct_change * 100:.0f}%) vs PO {oc.po_number}/{oc.po_line}. "
                    f"Quantity jumps are never auto-accepted and require human review."
                ),
                subject_type="po",
                subject_id=oc.po_number,
                part_id=oc.material_id,
                supplier_id=oc.supplier_id,
                po_number=oc.po_number,
                suggested_actions=[
                    {
                        "type": "human_review",
                        "label": f"Review quantity jump ({int(po.quantity):,} → {int(oc.quantity):,}) on PO {oc.po_number} before accepting",
                        "target": oc.po_number,
                        "owner": "human",
                    }
                ],
                evidence=[
                    Evidence(path=po.source, locator=f"PO {po.po_number}/{po.po_line}", quote=f"qty={po.quantity}"),
                    Evidence(path=oc.source, locator=oc.confirmation_id, quote=f"qty={oc.quantity}"),
                ],
            ))

    # 3. OCR scan cross-check mismatches stored by Ingest in Document.extracted["checks"]
    for doc in ctx.documents:
        checks = (doc.extracted or {}).get("checks")
        if not isinstance(checks, dict):
            continue
        results = checks.get("results") or {}
        failed_qty = [k for k, v in results.items() if k.startswith("qty_") and v is False]
        if not failed_qty:
            continue
        notes = checks.get("notes") or []
        parts = checks.get("parts") or []
        part_id = parts[0] if parts else ""
        findings.append(CandidateFinding(
            type="quantity_jump",
            kind=IssueKind.HUMAN_CHECK,
            severity=Severity.HIGH,
            key=f"SCAN/{doc.path}",
            summary=_truncate(f"Delivery scan {doc.path.rsplit('/', 1)[-1]} disagrees with booked goods receipt"),
            reason=(
                f"OCR cross-check of {doc.path} found a quantity mismatch against the booked goods receipt: "
                + "; ".join(notes[:3])
            ),
            subject_type="document",
            subject_id=doc.path,
            part_id=part_id,
            supplier_id=doc.supplier_id,
            suggested_actions=[
                {
                    "type": "human_review",
                    "label": f"Reconcile physical scan {doc.path.rsplit('/', 1)[-1]} against ERP goods receipt",
                    "target": part_id or doc.path,
                    "owner": "human",
                }
            ],
            evidence=[
                Evidence(path=doc.path, locator="ocr_check", quote="; ".join(notes[:3])),
            ],
        ))

    return findings, conflicts


# ---------------------------------------------------------------------------
# T3.9: part_journey view — empty steps (ordered, never confirmed) & late launch arrivals
# ---------------------------------------------------------------------------
def check_part_journey(ctx: CheckContext) -> list[CandidateFinding]:
    findings: list[CandidateFinding] = []
    rows = query("SELECT * FROM part_journey")
    if not rows:
        return findings

    # Deduplicate part_journey rows per (po_number, po_line) — step precedence: received > shipped > confirmed > ordered
    step_rank = {"ordered": 0, "confirmed": 1, "shipped": 2, "received": 3}
    best_by_po: dict[tuple[str, Any], dict] = {}
    for r in rows:
        key = (r["po_number"], r["po_line"])
        cur = best_by_po.get(key)
        if cur is None or step_rank.get(r["last_step"], 0) > step_rank.get(cur["last_step"], 0):
            best_by_po[key] = r

    # 1. Open launch-part POs stuck at 'ordered' (never confirmed, excluding awaiting_approval handled by T3.5)
    for (po_num, po_line), r in sorted(best_by_po.items()):
        po_status = (r.get("po_status") or "").strip().lower()
        part_id = r.get("material_id") or ""
        if po_status in CLOSED_PO_STATUSES or po_status in {"awaiting_approval", "pending_approval"}:
            continue
        if part_id not in ctx.launch_parts:
            continue
        if r.get("last_step") == "ordered":
            sup_id = r.get("supplier_id") or ""
            qty = int(round(float(r.get("qty_ordered") or 0)))
            product_id, impact = compute_part_impact(
                ctx, part_id,
                note=f"Open PO {po_num}/{po_line} ({qty:,} pcs) has no supplier confirmation yet",
            )
            findings.append(CandidateFinding(
                type="unconfirmed_po",
                kind=IssueKind.MISSING_INFO,
                severity=Severity.MEDIUM,
                key=f"{po_num}/{po_line}",
                summary=_truncate(
                    f"PO {po_num}/{po_line} ({part_id}, {qty:,} pcs) has no supplier confirmation from {sup_id}"
                ),
                reason=(
                    f"In part_journey, open PO {po_num}/{po_line} for {part_id} ({qty:,} pcs, promised {r.get('promised_date')}) "
                    f"is still at step 'ordered' with no OrderConfirmation or Shipment on file."
                ),
                subject_type="po",
                subject_id=po_num,
                part_id=part_id,
                supplier_id=sup_id,
                po_number=po_num,
                product_id=product_id,
                impact=impact,
                suggested_actions=[
                    {
                        "type": "email",
                        "label": f"Ask {sup_id} to confirm PO {po_num}/{po_line} ({part_id}, {qty:,} pcs)",
                        "target": sup_id,
                        "owner": "chaser",
                    }
                ],
                evidence=[
                    Evidence(path=r.get("po_source") or "part_journey", locator=f"PO {po_num}/{po_line}",
                             quote=f"last_step=ordered, qty={qty}, promised={r.get('promised_date')}"),
                ],
            ))

    # 2. Critical launch parts whose ONLY open inbound shipments arrive AFTER launch deadline (e.g. CMP-GAM-010 in Dec)
    if ctx.launch_deadline:
        shipments_by_part: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            part_id = r.get("material_id") or ""
            po_status = (r.get("po_status") or "").strip().lower()
            if part_id not in ctx.launch_parts or po_status in CLOSED_PO_STATUSES:
                continue
            if r.get("shipment_id") and r.get("shipment_eta"):
                shipments_by_part[part_id].append(r)

        for part_id, srows in sorted(shipments_by_part.items()):
            mat = ctx.materials.get(part_id)
            # Deduplicate by shipment_id
            by_sid = {r["shipment_id"]: r for r in srows}
            etas = sorted(str(r["shipment_eta"])[:10] for r in by_sid.values() if r.get("shipment_eta"))
            if not etas:
                continue
            earliest_eta = etas[0]
            if earliest_eta <= ctx.launch_deadline.isoformat():
                continue  # At least one shipment arrives on/before launch
            # Flag if the part is marked critical OR usable stock has a shortfall / low cover
            usable = _usable_stock_for_part(ctx, part_id)
            product_id, impact = compute_part_impact(ctx, part_id, usable_qty_override=usable)
            shortfall = impact.get("copies_at_risk") or 0
            is_critical = bool(mat and mat.critical)
            if not (is_critical or shortfall > 0):
                continue
            earliest_row = min(by_sid.values(), key=lambda r: str(r["shipment_eta"]))
            sup_id = earliest_row.get("supplier_id") or ""
            sid = earliest_row["shipment_id"]
            mode = earliest_row.get("shipment_mode") or "sea"
            impact["note"] = (
                f"Earliest open shipment {sid} ({mode}) arrives {earliest_eta} — after launch {ctx.launch_deadline}"
            )
            findings.append(CandidateFinding(
                type="shipment_late_for_launch",
                kind=IssueKind.COPIES_AT_RISK,
                severity=Severity.HIGH if (is_critical or shortfall > 0) else Severity.MEDIUM,
                key=f"{part_id}|late_inbound",
                summary=_truncate(
                    f"Critical part {part_id}: no inbound arrives before launch (earliest {sid} {mode} ETA {earliest_eta})"
                ),
                reason=(
                    f"All {len(by_sid)} open inbound shipment(s) for {part_id} arrive after the "
                    f"{ctx.launch_deadline} launch deadline (earliest: {sid} via {mode}, ETA {earliest_eta}). "
                    f"Any stock loss or quality hold cannot be replenished in time without expediting."
                ),
                subject_type="part",
                subject_id=part_id,
                part_id=part_id,
                supplier_id=sup_id,
                shipment_id=sid,
                po_number=earliest_row.get("po_number") or "",
                product_id=product_id,
                impact=impact,
                suggested_actions=[
                    {
                        "type": "expedite",
                        "label": f"Evaluate air-freight split for {part_id} (earliest sea {sid} ETA {earliest_eta})",
                        "target": sup_id or part_id,
                        "owner": "planner",
                    }
                ],
                evidence=[
                    Evidence(path=r.get("shipment_source") or "part_journey", locator=r["shipment_id"],
                             quote=f"mode={r.get('shipment_mode')}, eta={r.get('shipment_eta')}, status={r.get('shipment_status')}")
                    for r in list(by_sid.values())[:5]
                ],
            ))

    return findings


# ---------------------------------------------------------------------------
# Entry point: run all rules and prioritize by risk score
# ---------------------------------------------------------------------------
def run_all_checks(
    ctx: CheckContext,
    *,
    stale_hours: float = STALE_FEED_HOURS,
    jump_ratio: float = QTY_JUMP_RATIO,
) -> tuple[list[CandidateFinding], list[CandidateConflict]]:
    findings: list[CandidateFinding] = []
    conflicts: list[CandidateConflict] = []

    f1, c1 = check_erp_vs_tracker(ctx)
    findings.extend(f1)
    conflicts.extend(c1)

    findings.extend(check_stale_feeds(ctx, stale_hours=stale_hours))

    f3, c3 = check_po_vs_confirmation(ctx)
    findings.extend(f3)
    conflicts.extend(c3)

    findings.extend(check_revision_gate(ctx))
    findings.extend(check_approvals_and_supplier_health(ctx))
    findings.extend(check_single_source_and_news(ctx))

    f7, c7 = check_bank_details(ctx)
    findings.extend(f7)
    conflicts.extend(c7)

    f8, c8 = check_quantity_jumps(ctx, jump_ratio=jump_ratio)
    findings.extend(f8)
    conflicts.extend(c8)

    findings.extend(check_part_journey(ctx))

    # Prioritize by risk rating (from RiskRater if present) then severity descending
    def _priority(f: CandidateFinding) -> tuple[int, int, str]:
        part_risk = ctx.risk_by_part.get(f.part_id, 0)
        sup_risk = ctx.risk_by_supplier.get(f.supplier_id, 0)
        return (-max(part_risk, sup_risk), -f.severity, f"{f.type}|{f.key}")

    findings.sort(key=_priority)
    return findings, conflicts
