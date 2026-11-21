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
import re
from datetime import date, datetime
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
    title_date_field: str = ""  # field that may come from a date in the sheet title ("Stock … 2026-11-21")


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
        uom="unit", safety_stock="", weeks_of_cover="cover", last_count="counted"),
        title_date_field="snapshot_date"),
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
        still_to_pack="still to pack", rate_per_hour="rate", line_status=""), title_date_field="as_of"),
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


def _score(headers: list[str], target: Target, has_title_date: bool = False) -> tuple[float, dict[str, str]]:
    cols: dict[str, str] = {}
    used: set[str] = set()
    normed = {h: norm_header(h) for h in headers if h}
    for fname, names in target.synonyms.items():  # exact names first
        for h, n in normed.items():
            if h not in cols and fname not in used and n in names:
                cols[h] = fname
                used.add(fname)
    satisfied = used | ({target.title_date_field} if has_title_date and target.title_date_field else set())
    if not all(r in satisfied for r in target.required):
        return 0.0, cols
    return len(cols) / max(len(normed), 1), cols


def _options(headers: list[str], rows: list[dict], cols: dict[str, str], delimiter: str = "") -> dict:
    date_cols = [h for h, f in cols.items() if "date" in f or f in {"etd", "eta", "valid_from", "valid_to", "last_count",
                                                                   "effective_from", "qualification_valid_until"}]
    sample = [r.get(h) for r in rows[:200] for h in date_cols]
    return {"decimal_comma": True if delimiter == ";" else None, "date_order": detect_date_order(sample)}


def _shape(value: str) -> str:
    return re.sub(r"[A-Za-z]", "A", re.sub(r"\d", "9", str(value).strip()))


def validate_mapping(target: Target, cols: dict[str, str], rows: list[dict]) -> tuple[bool, str]:
    """Guard against AI mappings that would pollute the truth (T2.15): required fields present, sample values
    convert to the field types, and key values look like the ids already in that table."""
    mapped = set(cols.values())
    missing = [f for f in target.required if f not in mapped]
    if missing:
        return False, f"missing required {missing}"
    from typing import get_args

    from ..core.parsing import parse_date, parse_number

    bad = total = 0
    for row in rows[:20]:
        for header, field in cols.items():
            info = target.model.model_fields.get(field)
            val = str(row.get(header, "") or "").strip()
            if info is None or not val:
                continue
            kinds = [a for a in get_args(info.annotation) if a is not type(None)] or [info.annotation]
            kind = kinds[0]
            total += 1
            if kind is date or kind is datetime:
                bad += parse_date(val) is None
            elif kind in (int, float):
                bad += parse_number(val) is None
    if total and bad / total > 0.2:
        return False, f"{bad}/{total} sample values don't fit the field types"
    with session() as s:
        existing = s.exec(select(getattr(target.model, target.key[0])).limit(50)).all()
    key_col = next((h for h, f in cols.items() if f == target.key[0]), None)
    if existing and key_col:
        shapes = {_shape(v) for v in existing if v}
        sample = [str(r.get(key_col, "")) for r in rows[:20] if r.get(key_col)]
        if sample and sum(_shape(v) in shapes for v in sample) / len(sample) < 0.5:
            return False, f"key values {sample[:2]} don't look like existing {target.key[0]}s"
    return True, ""


class ColumnPair(BaseModel):
    header: str
    field: str


class LLMMapping(BaseModel):
    target_table: str
    columns: list[ColumnPair]  # list (not dict) so both Claude and OpenAI strict schemas accept it


def map_columns(headers: list[str], rows: list[dict], *, path: str, agent: str, delimiter: str = "",
                has_title_date: bool = False) -> Mapping:
    fp = fingerprint(headers + (["<title-date>"] if has_title_date else []))
    with session() as s:
        saved = s.exec(select(ColumnMapping).where(ColumnMapping.fingerprint == fp)).first()
    if saved:
        return Mapping(saved.target_table or None, dict(saved.mapping), dict(saved.options), saved.method,
                       saved.score or 0, fp, reused=True)

    best: tuple[float, str, dict] = (0.0, "", {})
    for name, target in TARGETS.items():
        sc, cols = _score(headers, target, has_title_date)
        if sc > best[0]:
            best = (sc, name, cols)
    score, name, cols = best
    method = "glossary"
    if score < 0.5:  # weak: ask Claude (None offline -> unmapped)
        prompt = (
            f"Does {path} contain rows of one of our target tables? Only map it if each row really IS such a record "
            "(e.g. a purchase order line, a shipment); reference lists like ports, routes, sites or price lists are "
            "NOT shipments or suppliers -> answer target_table ''. Never map a column to a field of another meaning."
            f"\nHeaders: {headers}\nSample rows: "
            f"{json.dumps(rows[:5], ensure_ascii=False, default=str)}\nTarget tables and fields: "
            + json.dumps({n: list(t.synonyms) for n, t in TARGETS.items()})
            + "\nReturn target_table (or '' if none fits) and columns as [{header, field}] using only listed fields."
        )
        try:
            answer = llm.extract(prompt, LLMMapping, agent=agent, task="ingest.map_columns")
        except llm.LLMError:
            answer = None  # Claude unavailable: keep the file as a plain table
        name, cols, method = "", {}, "unmapped"
        if answer and answer.target_table in TARGETS:
            cand = {p.header: p.field for p in answer.columns
                    if p.header in headers and p.field in TARGETS[answer.target_table].synonyms}
            ok, why = validate_mapping(TARGETS[answer.target_table], cand, rows)
            if ok:
                name, cols, method = answer.target_table, cand, "llm"
                score = len(cols) / max(len(headers), 1)
            else:
                method = f"llm_rejected: {why}"
    mapping = Mapping(name or None, cols, _options(headers, rows, cols, delimiter) if name else {}, method, score, fp)
    with session() as s:
        s.add(ColumnMapping(fingerprint=fp, target_table=name, mapping=cols, options=mapping.options,
                            method=method, score=score, example_path=path))
        s.commit()
    return mapping
