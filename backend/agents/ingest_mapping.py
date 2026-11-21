"""Column mapping for tabular files (CSV / Excel sheets / flattened JSON) – T2.8, T2.11, T2.12.

How a header row becomes a target table + {column -> field} mapping:
1. Fingerprint the normalized headers. Known fingerprint -> reuse the saved mapping (no AI, same result every drop).
2. New fingerprint -> score every target table with the glossary below (field names, English synonyms, legacy SAP
   codes). Good match -> save it (method "glossary").
3. Weak match -> ask Claude (headers + sample rows + target fields). Offline/MOCK -> None -> stored as a plain table
   document and flagged for a human, never guessed.

The glossary is domain vocabulary (what columns are usually called), not values from the data.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Optional

from pydantic import BaseModel
from sqlmodel import select

from ..core import llm
from ..core.models import (
    BomLine, ColumnMapping, EngineeringChange, GoodsReceipt, LaunchStatus, Material, Packout, Product,
    PurchaseOrder, Shipment, StockLot, Supplier, SupplierPartMap,
)
from ..core.parsing import detect_date_order, norm_header
from ..core.store import session


@dataclass
class Target:
    model: type
    key: tuple[str, ...]  # natural key for upserts
    required: tuple[str, ...]  # must be present to choose this table
    synonyms: dict[str, list[str]]  # field -> normalized header names
    key_includes_source: bool = False  # e.g. shipments: ERP and tracker rows are kept side by side


def _syn(**kw: str) -> dict[str, list[str]]:
    return {f: [norm_header(x) for x in ([f] + [s.strip() for s in v.split("|") if s.strip()])] for f, v in kw.items()}


TARGETS: dict[str, Target] = {
    "purchase_orders": Target(PurchaseOrder, ("po_number", "po_line"), ("po_number", "material_id", "quantity"), _syn(
        po_number="EBELN|PO|PO number|order number|purchase order", po_line="EBELP|item|line|position",
        supplier_id="LIFNR|vendor|supplier|vendor number", material_id="MATNR|material|part|component",
        plant="WERKS|site", quantity="MENGE|qty|order quantity", unit_price="NETPR|price|net price",
        currency="WAERS|cur", order_date="BEDAT|order date|po date", requested_date="EINDT|requested|delivery date",
        promised_date="ZZCONF|confirmed date|promised", actual_delivery_date="WEDAT|goods receipt date|delivered on",
        status="ZZSTAT|state", buyer="ERNAM|created by|purchaser", incoterm="incoterms", note="remark|comment")),
    "shipments": Target(Shipment, ("shipment_id",), ("shipment_id", "eta"), _syn(
        shipment_id="shipment|shipment no", carrier_id="carrier|lsp", mode="transport mode",
        reference="container_awb|container|awb|tracking", supplier_id="supplier|vendor", route_id="route",
        origin="from|pol|origin port", destination="to|pod", via="transhipment", dest_site="site",
        etd="departure", eta="arrival|expected arrival", status="state", last_lat="lat", last_lon="lon",
        updated_at="last update|updated", note="comment|remark", po_numbers="pos|po|purchase orders",
        material_ids="materials|parts"), key_includes_source=True),
    "goods_receipts": Target(GoodsReceipt, ("gr_number",), ("gr_number", "qty_received"), _syn(
        gr_number="goods receipt|gr", gr_date="receipt date", po_number="po", po_line="item",
        supplier_id="supplier", material_id="material|part", plant="site", qty_ordered="ordered",
        qty_received="received|qty", delivery_note="delivery note|dn", inspector="", remark="comment|note")),
    "stock": Target(StockLot, ("material_id", "batch", "snapshot_date"), ("material_id", "quantity", "snapshot_date"), _syn(
        snapshot_date="date|as of", site="plant", material_id="material|part", batch="lot", quantity="qty|stock",
        uom="unit", safety_stock="", weeks_of_cover="cover", last_count="counted")),
    "packout": Target(Packout, ("pack_date", "product_id", "line"), ("product_id", "units_packed"), _syn(
        pack_date="date", site="", product_id="product", line="", units_packed="packed|units")),
    "materials": Target(Material, ("component_id",), ("component_id", "description", "drawing_rev"), _syn(
        component_id="material|part", description="", category="", uom="", abc_class="abc",
        critical="", drawing_rev="revision|rev")),
    "bom": Target(BomLine, ("parent_id", "child_id"), ("parent_id", "child_id", "qty_per"), _syn(
        parent_id="parent|product", child_id="child|component", qty_per="quantity per|qty", valid_from="",
        valid_to="")),
    "products": Target(Product, ("product_id",), ("product_id", "name", "launch_date"), _syn(
        product_id="", name="", product_line="", lifecycle="", launch_date="", weekly_plan_qty="plan_weekly_units|weekly plan",
        day_one_qty="day one demand", std_cost_eur="standard cost", valid_from="")),
    "engineering_changes": Target(EngineeringChange, ("change_id",), ("change_id", "rev_new"), _syn(
        change_id="ecn", parent_id="", child_id="", action="", rev_old="", rev_new="", use_up_old_rev="use up",
        effective_from="", reason="")),
    "supplier_part_map": Target(SupplierPartMap, ("supplier_id", "component_id"), ("component_id", "share"), _syn(
        supplier_id="", component_id="material", supplier_ref="supplier part|their part", share="",
        primary="", lead_time_days="lead time|lead_time_days_new", moq="", unit_price="price", currency="",
        note="")),
    "suppliers": Target(Supplier, ("supplier_id",), ("supplier_id", "name"), _syn(
        supplier_id="supplier.supplierid|supplierid", vendor_no="supplier.vendorno|vendorno|lifnr",
        name="supplier.name", legal_name="supplier.legalname|legalname", category="supplier.category",
        country="supplier.address.country", city="supplier.address.city", currency="supplier.terms.currency",
        payment_terms="supplier.terms.payment|payment", incoterm="supplier.terms.incoterm",
        status="supplier.status", qualification_status="supplier.qualification.status",
        qualification_valid_until="supplier.qualification.validuntil", contact_name="supplier.contact.name",
        contact_email="supplier.contact.email", language="preferred_language|supplier.contact.language",
        notes="supplier.notes")),
    "launch_status": Target(LaunchStatus, ("product_id",), ("product_id", "still_to_pack", "rate_per_hour"), _syn(
        product_id="product", edition="name", day_one_demand="day one demand", packed="",
        still_to_pack="still to pack", rate_per_hour="rate", line_status="")),
}

#: Fields whose values are lists of ids inside one cell (split + stored in link tables).
LIST_FIELDS = {"po_numbers", "material_ids"}


def fingerprint(headers: list[str]) -> str:
    return hashlib.sha1("|".join(sorted(norm_header(h) for h in headers if h)).encode()).hexdigest()[:16]


@dataclass
class Mapping:
    target: Optional[str]
    columns: dict[str, str] = field(default_factory=dict)  # source header -> field
    options: dict = field(default_factory=dict)
    method: str = ""
    score: float = 0.0
    fingerprint: str = ""
    reused: bool = False


def _score(headers: list[str], target: Target) -> tuple[float, dict[str, str]]:
    cols: dict[str, str] = {}
    used: set[str] = set()
    normed = {h: norm_header(h) for h in headers if h}
    for fname, names in target.synonyms.items():  # exact names first
        for h, n in normed.items():
            if h not in cols and fname not in used and n in names:
                cols[h] = fname
                used.add(fname)
    if not all(r in used for r in target.required):
        return 0.0, cols
    return len(cols) / max(len(normed), 1), cols


def _options(headers: list[str], rows: list[dict], cols: dict[str, str], delimiter: str = "") -> dict:
    date_cols = [h for h, f in cols.items() if "date" in f or f in {"etd", "eta", "valid_from", "valid_to", "last_count",
                                                                   "effective_from", "qualification_valid_until"}]
    sample = [r.get(h) for r in rows[:200] for h in date_cols]
    return {"decimal_comma": True if delimiter == ";" else None, "date_order": detect_date_order(sample)}


class ColumnPair(BaseModel):
    header: str
    field: str


class LLMMapping(BaseModel):
    target_table: str
    columns: list[ColumnPair]  # list (not dict) so both Claude and OpenAI strict schemas accept it


def map_columns(headers: list[str], rows: list[dict], *, path: str, agent: str, delimiter: str = "") -> Mapping:
    fp = fingerprint(headers)
    with session() as s:
        saved = s.exec(select(ColumnMapping).where(ColumnMapping.fingerprint == fp)).first()
    if saved:
        return Mapping(saved.target_table or None, dict(saved.mapping), dict(saved.options), saved.method,
                       saved.score or 0, fp, reused=True)

    best: tuple[float, str, dict] = (0.0, "", {})
    for name, target in TARGETS.items():
        sc, cols = _score(headers, target)
        if sc > best[0]:
            best = (sc, name, cols)
    score, name, cols = best
    method = "glossary"
    if score < 0.5:  # weak: ask Claude (None offline -> unmapped)
        prompt = (
            f"Map the columns of {path} to one target table.\nHeaders: {headers}\nSample rows: "
            f"{json.dumps(rows[:5], ensure_ascii=False, default=str)}\nTarget tables and fields: "
            + json.dumps({n: list(t.synonyms) for n, t in TARGETS.items()})
            + "\nReturn target_table (or '' if none fits) and columns as [{header, field}] using only listed fields."
        )
        try:
            answer = llm.extract(prompt, LLMMapping, agent=agent, task="ingest.map_columns")
        except llm.LLMError:
            answer = None  # Claude unavailable: keep the file as a plain table
        if answer and answer.target_table in TARGETS:
            name, method = answer.target_table, "llm"
            cols = {p.header: p.field for p in answer.columns if p.header in headers}
            score = len(cols) / max(len(headers), 1)
        else:
            name, cols, method = "", {}, "unmapped"
    mapping = Mapping(name or None, cols, _options(headers, rows, cols, delimiter) if name else {}, method, score, fp)
    with session() as s:
        s.add(ColumnMapping(fingerprint=fp, target_table=name, mapping=cols, options=mapping.options,
                            method=method, score=score, example_path=path))
        s.commit()
    return mapping
