"""Ingest & Normalizer agent (owner: daksh, .brain/tasks/2-ingest-normalizer.md).

Turns every file of a drop (CSV, Excel, JSON, PDF, Word, Markdown, scans; KO/ZH/PL/EN) into the truth store,
and applies supplier replies. It is the ONLY writer of supply-chain data.

- New rows are inserted; rows that already exist (same natural key) are UPDATED via update_row, so every change
  lands in the change log. Same file replaced -> its newer values win. Different source disagrees -> newest wins
  and the disagreement is kept in `conflicts` (Informatica-style survivorship, T2.17).
- Tabular files: column mapping learned once per header fingerprint (ingest_mapping.py).
- Documents: classify -> extract -> ground (ingest_docs.py). Order confirmations parsed by rules.
- Supplier names / vendor numbers / supplier part numbers -> our IDs via aliases (T2.7/T2.16).
- Nothing here contains values from the data; everything is read from the files.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import typing
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlmodel import select

from ..core import events, llm
from ..core.coordinator import Event
from ..core.models import (
    Alias, Document, InboxMessage, Issue, IssueKind, LaunchStatus, Material, OrderConfirmation, PurchaseOrder,
    Shipment, ShipmentMaterial, ShipmentPO, StockLot, Supplier, SupplierPartMap,
)
from ..core.parsing import (
    decode, flatten_json, norm_header, norm_name, office_text, parse_date, parse_datetime, parse_number,
    pdf_text, read_csv, safe_rel, xlsx_rows,
)
from ..core.store import UpdateResult, add_all, all_rows, session
from .base import Agent, register
from .ingest_docs import (
    classify, facts, language, llm_extract, ocr_scan, parse_order_confirmation, scan_info,
)
from .ingest_mapping import LIST_FIELDS, TARGETS, Mapping, map_columns

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".gif", ".webp"}
SUP_RE = re.compile(r"(SUP-\d{4})(?!\d)")

#: one standard shipment status list (T2.18); carrier/ERP/tracker words -> canonical status
#: Coordinates: a dot is always a decimal mark ('9.785' is a longitude, not 9,785).
DECIMAL_DOT_FIELDS = {"last_lat", "last_lon"}
#: Free-text fields: a second source adds its text instead of replacing the first one.
NOTE_FIELDS = {"note", "notes", "remark"}

STATUS_MAP = {
    "booked": "booked", "planned": "booked", "pending": "booked",
    "in_transit": "in_transit", "in transit": "in_transit", "on the way": "in_transit", "departed": "in_transit",
    "arrived_port": "arrived_port", "arrived port": "arrived_port", "discharged": "arrived_port",
    "in_delivery": "in_delivery", "out for delivery": "in_delivery", "on carriage": "in_delivery",
    "delivered": "delivered", "received": "delivered", "cancelled": "cancelled", "canceled": "cancelled",
    "delayed": "delayed", "on hold": "on_hold", "stuck": "on_hold",
}


def _priority(rel: str) -> tuple:
    """Master data first (so names/parts can be resolved), then ERP, then documents, then scans."""
    name = rel.rsplit("/", 1)[-1].lower()
    ext = Path(rel).suffix.lower()
    if name.startswith("profile"):
        rank = 0
    elif "/master/" in f"/{rel}":
        rank = 1
    elif name.startswith("components"):
        rank = 2
    elif "/erp/" in f"/{rel}":
        rank = 3
    elif ext in {".csv", ".xlsx", ".json"}:
        rank = 4
    elif ext in {".pdf", ".docx", ".md", ".txt"}:
        rank = 5
    else:
        rank = 6
    return (rank, rel)


def _supplier_of(rel: str) -> str:
    """Portal convention: suppliers/<SUP-####>_<Name>/... (from the API docs)."""
    m = SUP_RE.search(rel)
    return m.group(1) if m else ""


def _base_type(annotation) -> type:
    args = [a for a in typing.get_args(annotation) if a is not type(None)]
    return args[0] if args else annotation


@register
class IngestNormalizer(Agent):
    name = "ingest"
    subscriptions = {events.FILES_CHANGED: "on_files_changed", events.REPLY_RECEIVED: "on_reply"}

    # ------------------------------------------------------------------ drops
    def on_files_changed(self, event: Event) -> None:
        p = event.payload
        root = Path(p.get("data_root") or ".")
        files = sorted(set(p.get("added", [])) | set(p.get("changed", [])), key=_priority)
        self._results: list[UpdateResult] = []
        self._tables: set[str] = set()
        self._unmapped: list[str] = []
        self._scans: list[str] = []
        self._unresolved: set[str] = set()
        self._packed: list[tuple[dict, datetime, str]] = []  # applied after all files (launch status may come later)
        counts = {"rows_inserted": 0, "rows_updated": 0, "documents": 0}
        self._counts = counts
        for rel in files:
            try:
                self._ingest_file(root, rel)
            except Exception as exc:  # one bad file must never stop the drop
                self.log("error", f"{rel}: {type(exc).__name__}: {exc}", [{"path": rel}])
                self._unmapped.append(rel)
        for rel in p.get("removed", []):
            self.log("file_removed", f"{rel} was removed from the portal; its rows stay as history", [{"path": rel}])
        for counted, as_of, rel in sorted(self._packed, key=lambda x: x[1]):
            self._packed_to_launch_status(counted, as_of, rel)
        self._raise_summary_issues()
        self.log("drop_ingested", f"{len(files)} files: +{counts['rows_inserted']} rows, "
                 f"{counts['rows_updated']} rows updated, {counts['documents']} documents",
                 data={**counts, "tables": sorted(self._tables), "unmapped": len(self._unmapped)})
        self.emit_truth_updated(self._results, files=files, tables=sorted(self._tables), data_root=str(root))

    def _ingest_file(self, root: Path, rel: str) -> None:
        safe_rel(rel)
        path = root / rel
        if not path.is_file():
            self.log("missing_file", rel)
            return
        ext = path.suffix.lower()
        data = path.read_bytes()
        if ext == ".csv":
            headers, rows, delim = read_csv(data)
            self._ingest_table(rel, headers, rows, delim)
        elif ext == ".xlsx":
            self._ingest_workbook(rel, data)
        elif ext == ".json":
            self._ingest_json(rel, data)
        elif ext in IMAGE_EXT:
            self._store_scan(root, rel)
        elif ext == ".pdf":
            text = pdf_text(data)
            if not text.strip():
                self._store_scan(root, rel)
            else:
                self._ingest_document(rel, text)
        elif ext in {".docx", ".md", ".txt"}:
            text = office_text(data) if ext == ".docx" else decode(data)
            self._ingest_document(rel, text or "")
        else:
            self._unmapped.append(rel)
            self.log("unsupported", rel)

    # ------------------------------------------------------------ tables
    def _ingest_workbook(self, rel: str, data: bytes) -> None:
        for sheet, rows in xlsx_rows(data).items():
            rows = [[c.strip() for c in r] for r in rows]
            filled = [r for r in rows if any(r)]
            if not filled:
                continue
            title = filled[0][0] if len([c for c in filled[0] if c]) == 1 else ""
            header_idx = self._find_header(rows)
            if header_idx is None:
                self._store_document(f"{rel}#{sheet}", "\n".join(" | ".join(c for c in r if c) for r in filled),
                                     kind="note")
                continue
            headers = rows[header_idx]
            body, notes = [], []
            for r in rows[header_idx + 1:]:
                cells = [c for c in r if c]
                if not cells:
                    continue
                if len(cells) == 1 and len(headers) > 2:
                    notes.append(cells[0])
                    continue
                body.append({h: (r[i] if i < len(r) else "") for i, h in enumerate(headers) if h})
            self._ingest_table(f"{rel}#{sheet}", [h for h in headers if h], body, "", title=title)
            if notes or title:
                self._store_document(f"{rel}#{sheet}#notes", "\n".join([title] + notes).strip(), kind="note")

    @staticmethod
    def _find_header(rows: list[list[str]]) -> Optional[int]:
        best, best_i = 0.0, None
        for i, r in enumerate(rows[:8]):
            cells = [c for c in r if c]
            if len(cells) < 2:
                continue
            texty = sum(1 for c in cells if parse_number(c) is None) / len(cells)
            score = texty * len(cells)
            if score > best:
                best, best_i = score, i
        return best_i

    def _ingest_json(self, rel: str, data: bytes) -> None:
        obj = json.loads(decode(data))
        items = obj.get("items") if isinstance(obj, dict) else None
        if isinstance(items, list) and items and all(isinstance(i, dict) for i in items):
            for item in items:  # news-style feeds: one document per item
                text = "\n".join(str(item.get(k, "")) for k in ("headline", "title", "summary", "body") if item.get(k))
                self._store_document(f"{rel}#{item.get('id', len(text))}", text, kind="news",
                                     extra={"item": item},
                                     published_at=parse_datetime(item.get("published_at") or item.get("date")))
            return
        flat = flatten_json(obj)
        headers = list(flat)
        self._ingest_table(rel, headers, [{k: ("" if v is None else str(v)) for k, v in flat.items()}], "")

    def _ingest_table(self, rel: str, headers: list[str], rows: list[dict], delim: str, title: str = "") -> None:
        normed = {norm_header(h) for h in headers}
        if {"field", "new"} <= normed:
            self._apply_changeset(rel, headers, rows)
            return
        title_match = re.search(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2})?", title or "")
        mapping = map_columns(headers, rows, path=rel, agent=self.name, delimiter=delim,
                              has_title_date=bool(title_match))
        if not mapping.target:
            self._store_document(rel, "", kind="table", extra={"headers": headers, "rows": rows[:500], "title": title})
            self._unmapped.append(rel)
            return
        target = TARGETS[mapping.target]
        file_supplier = _supplier_of(rel)
        as_of = parse_datetime(re.search(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2})?", title).group(0)) if (
            title and re.search(r"\d{4}-\d{2}-\d{2}", title)) else None
        existing = self._existing(target, rel)
        styles = self._decimal_styles(mapping, rows)
        new_rows, links = [], []
        counted: dict[str, float] = {}  # finished goods in a timestamped stock sheet = packed copies
        for i, raw in enumerate(rows):
            values, lists = self._convert(target, mapping, raw, styles)
            if not any(v not in (None, "") for v in values.values()):
                continue
            if target.model is StockLot and as_of and values.get("material_id") and values.get("quantity") is not None:
                counted[str(values["material_id"])] = values["quantity"]
            if "supplier_id" in target.model.model_fields:
                sup = values.get("supplier_id")
                if not sup and file_supplier:
                    values["supplier_id"] = file_supplier
                elif sup:
                    values["supplier_id"] = self.resolve_supplier(str(sup), rel) or str(sup)
            if as_of and target.title_date_field and not values.get(target.title_date_field):
                field_type = _base_type(target.model.model_fields[target.title_date_field].annotation)
                values[target.title_date_field] = as_of.date() if field_type is date else as_of
            if target.model is Shipment and values.get("status"):
                values["status"] = STATUS_MAP.get(str(values["status"]).lower().strip(), values["status"])
            key = tuple(values.get(k) for k in target.key)
            if any(v in (None, "") for k, v in zip(target.key, key) if k in target.required):
                continue
            lookup = key + ((rel,) if target.key_includes_source else ())
            row_id = existing.get(lookup)
            if row_id is None:
                obj = target.model(**values, source=rel)
                new_rows.append(obj)
                existing[lookup] = -1  # avoid duplicates within the same file
            elif row_id > 0:
                same_source = existing.get(("__source__", row_id)) == rel
                present = {k: v for k, v in values.items() if v not in (None, "")}  # a missing cell never erases
                present = self._drop_stale(target.model, row_id, present, rel)
                if not same_source:
                    present = self._merge_notes(target.model, row_id, present)
                res = self.update_row(target.model, row_id, present, source=rel,
                                      reason=f"{'file updated' if same_source else 'other source'}: {rel} row {i + 1}",
                                      on_conflict="overwrite" if same_source else "newest",
                                      entity_key="/".join(str(k) for k in key))
                if res.changes or res.conflicts:
                    self._results.append(res)
                    self._counts["rows_updated"] += 1
            if lists:
                links.append((values.get("shipment_id"), lists))
        if new_rows:
            add_all(new_rows)
            self._counts["rows_inserted"] += len(new_rows)
        if links:
            self._store_links(links, rel)
        if counted:
            self._packed.append((counted, as_of, rel))
        self._tables.add(target.model.__tablename__)
        self.log("table_ingested", f"{rel} -> {target.model.__tablename__}: {len(rows)} rows "
                 f"({'saved mapping' if mapping.reused else mapping.method}, score {mapping.score:.2f})",
                 [{"path": rel}], data={"mapping": mapping.columns, "options": mapping.options})

    def _packed_to_launch_status(self, counted: dict[str, float], as_of: datetime, rel: str) -> None:
        """A timestamped count of finished goods ('Packed copies DC-HAM, 2026-11-21 20:50') is newer packing
        progress than the launch status sheet: update packed / still to pack, so every agent sees the same number.
        Only when the count is strictly newer (a date-only snapshot never beats a same-day status with a time)."""
        def utc(dt: datetime) -> datetime:
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)

        for ls in all_rows(LaunchStatus):
            qty = counted.get(ls.product_id)
            if qty is None or ls.day_one_demand is None or (ls.as_of and utc(ls.as_of) >= utc(as_of)):
                continue
            values = {"packed": int(qty), "still_to_pack": max(0, int(ls.day_one_demand - qty)), "as_of": as_of}
            res = self.update_row(LaunchStatus, ls.id, values, source=rel, on_conflict="overwrite",
                                  reason=f"packed copies counted {as_of:%Y-%m-%d %H:%M} ({rel})",
                                  entity_key=ls.product_id)
            if res.changes or res.conflicts:
                self._results.append(res)
                self._tables.add(LaunchStatus.__tablename__)

    @staticmethod
    def _merge_notes(model, row_id: int, values: dict) -> dict:
        """A second file's note is added to the first one, not swapped in: 'fitted by supplier into CMP-GAM-007'
        (components.csv) must survive 'sub-supplier cannot export' (components_delta.csv)."""
        notes = {f: v.strip() for f, v in values.items() if f in NOTE_FIELDS and isinstance(v, str) and v.strip()}
        if not notes:
            return values
        with session() as s:
            row = s.get(model, row_id)
        out = dict(values)
        for field, new in notes.items():
            old = str(getattr(row, field, "") or "").strip()
            if not old or old in new:
                continue  # nothing to keep, or the new text already contains the old one
            if new in old:
                out.pop(field)  # already part of the note
            else:
                out[field] = f"{old} | {new}"
        return out

    @staticmethod
    def _drop_stale(model, row_id: int, values: dict, rel: str) -> dict:
        """Survivorship across sources: if `rel` set a field before, was later overruled by another source and
        still sends the SAME value, it's old news -> skip it (otherwise reprocessing would flip values back)."""
        from ..core.models import FieldChange
        from ..core.store import _as_text

        with session() as s:
            row = s.get(model, row_id)
            hist = s.exec(select(FieldChange).where(FieldChange.table_name == model.__tablename__,
                                                    FieldChange.row_id == row_id).order_by(FieldChange.id)).all()
        if not hist:
            return values
        keep = {}
        for field, value in values.items():
            changes = [h for h in hist if h.field == field]
            if not changes:
                keep[field] = value
                continue
            last_by_source = {row.source: changes[0].old_value}  # value the first source had
            for h in changes:
                last_by_source[h.source] = h.new_value
            latest_setter = changes[-1].source
            if rel != latest_setter and rel in last_by_source and last_by_source[rel] == _as_text(value):
                continue  # this source already lost with exactly this value
            keep[field] = value
        return keep

    def _existing(self, target, rel: str) -> dict:
        out: dict = {}
        for row in all_rows(target.model):
            key = tuple(getattr(row, k) for k in target.key)
            if target.key_includes_source:
                key += (row.source,)
            out[key] = row.id
            out[("__source__", row.id)] = row.source
        return out

    @staticmethod
    def _decimal_styles(mapping: Mapping, rows: list[dict]) -> dict[str, bool]:
        """Per column: is the decimal mark a comma (True) or a dot (False)? Decided from the column's own values
        ('53.54', '6.11', '0.125' -> dot; '1,52', '1,5200' -> comma). Columns with only '42.900'-style values stay
        undecided, so the global heuristic (thousands) still applies to them."""
        out: dict[str, bool] = {}
        for col, field in mapping.columns.items():
            if field in DECIMAL_DOT_FIELDS:
                out[col] = False
                continue
            dot = comma = False
            for r in rows:
                s = str(r.get(col, "") or "").strip().replace(" ", "")
                dot |= bool(re.fullmatch(r"-?(\d+\.(\d{1,2}|\d{4,})|0\.\d+)", s))
                comma |= bool(re.fullmatch(r"-?(\d+,(\d{1,2}|\d{4,})|0,\d+)", s))
            if dot != comma:
                out[col] = comma
        return out

    def _convert(self, target, mapping: Mapping, raw: dict, styles: Optional[dict] = None) -> tuple[dict, dict]:
        values: dict[str, Any] = {}
        lists: dict[str, list[str]] = {}
        opts = mapping.options or {}
        for col, field in mapping.columns.items():
            val = raw.get(col, "")
            if field in LIST_FIELDS:
                parts = [x.strip() for x in re.split(r"[|,;]", str(val)) if x.strip()]
                if parts:
                    lists[field] = parts
                continue
            info = target.model.model_fields.get(field)
            if info is None:
                continue
            col_opts = {**opts, "decimal_comma": styles[col]} if styles and col in styles else opts
            values[field] = self._coerce(_base_type(info.annotation), val, col_opts, field)
        return values, lists

    @staticmethod
    def _coerce(kind, val, opts: dict, field: str):
        if val is None or (isinstance(val, str) and not val.strip()):
            return None if kind is not str else ""
        s = str(val).strip()
        if kind is bool:
            return s.lower() in {"y", "yes", "true", "1", "x", "ja"}
        if kind is date:
            return parse_date(s, opts.get("date_order", "ymd"))
        if kind is datetime:
            return parse_datetime(s)
        if kind is int:
            if field == "po_line" and re.fullmatch(r"0\d{4}", s):
                n = int(s)
                return n // 10 if n % 10 == 0 else n  # SAP item 00010 -> line 1
            n = parse_number(s, opts.get("decimal_comma"))
            return int(round(n)) if n is not None else None
        if kind is float:
            return parse_number(s, opts.get("decimal_comma"))
        return s

    def _store_links(self, links: list[tuple[str, dict]], rel: str) -> None:
        have = {(r.shipment_id, r.po_number) for r in all_rows(ShipmentPO)}
        have_m = {(r.shipment_id, r.material_id) for r in all_rows(ShipmentMaterial)}
        new = []
        for shipment_id, lists in links:
            for po in lists.get("po_numbers", []):
                if (shipment_id, po) not in have:
                    new.append(ShipmentPO(shipment_id=shipment_id, po_number=po, source=rel))
                    have.add((shipment_id, po))
            for m in lists.get("material_ids", []):
                if (shipment_id, m) not in have_m:
                    new.append(ShipmentMaterial(shipment_id=shipment_id, material_id=m, source=rel))
                    have_m.add((shipment_id, m))
        if new:
            add_all(new)

    def _apply_changeset(self, rel: str, headers: list[str], rows: list[dict]) -> None:
        """Files like `component_id,field,old,new,changed_by`: explicit updates of existing rows."""
        id_col = headers[0]
        target = next((t for t in TARGETS.values() if t.key == (norm_header(id_col),)), None)
        if target is None:
            self._store_document(rel, "", kind="table", extra={"headers": headers, "rows": rows})
            self._unmapped.append(rel)
            return
        col = {norm_header(h): h for h in headers}
        by_key = {getattr(r, target.key[0]): r for r in all_rows(target.model)}
        for i, raw in enumerate(rows):
            key, field = raw.get(id_col, ""), norm_header(raw.get(col["field"], ""))
            row = by_key.get(key)
            if row is None or field not in target.model.model_fields:
                self.log("changeset_skip", f"{rel} row {i + 1}: {key}.{field} not found", [{"path": rel}])
                continue
            old = raw.get(col.get("old", ""), "")
            who = raw.get(col.get("changed_by", ""), "")
            res = self.update_row(target.model, row.id, {field: raw.get(col["new"], "")}, source=rel,
                                  on_conflict="overwrite", entity_key=key,
                                  reason=f"change file{(' by ' + who) if who else ''} (old per file: {old})")
            if res.changes:
                self._results.append(res)
                self._counts["rows_updated"] += 1
        self._tables.add(target.model.__tablename__)

    # --------------------------------------------------------- documents
    def _ingest_document(self, rel: str, text: str) -> None:
        kind = classify(rel, text)
        supplier = _supplier_of(rel)
        if kind == "order_confirmation":
            parsed = parse_order_confirmation(text)
            if parsed:
                self._ingest_confirmation(rel, text, parsed, supplier)
                return
        sha = hashlib.sha256(text.encode()).hexdigest()
        extra = {"facts": facts(text), "sha256": sha}
        extracted = self._cached(rel, sha, "llm") or llm_extract(text, kind, rel, self.name)
        if extracted:
            extra["llm"] = extracted
        self._store_document(rel, text, kind=kind, supplier_id=supplier, extra=extra)

    def _ingest_confirmation(self, rel: str, text: str, parsed: dict, supplier: str) -> None:
        pos = {r.po_number for r in all_rows(PurchaseOrder)}
        existing = {(r.confirmation_id, r.po_line, r.material_id): r for r in all_rows(OrderConfirmation)}
        new = []
        for line in parsed["lines"]:
            material = line["material_id"]
            supplier_ref = ""
            if not material.startswith("CMP-"):
                supplier_ref = material
                material = self.resolve_part(supplier, material, line["description"], rel) or material
            po_known = parsed["po_number"] in pos
            confidence = round(0.6 + 0.2 * po_known + 0.2 * material.startswith("CMP-"), 2)
            values = {
                "confirmation_id": parsed["confirmation_id"], "po_number": parsed["po_number"],
                "po_line": line["po_line"], "supplier_id": supplier, "material_id": material,
                "quantity": line["quantity"], "delivery_date": parse_date(line["delivery_date"]),
                "revision": line["revision"], "unit_price": line["unit_price"], "currency": line["currency"],
                "bank_iban": parsed["bank_iban"],
                "note": ((f"supplier part {supplier_ref}; " if supplier_ref else "") + parsed["notes"])[:2000],
            }
            row = existing.get((parsed["confirmation_id"], line["po_line"], material))
            if row is None:
                new.append(OrderConfirmation(**values, source=rel, confidence=confidence))
            else:
                res = self.update_row(OrderConfirmation, row.id, values, source=rel, on_conflict="overwrite",
                                      reason="confirmation re-sent", confidence=confidence,
                                      entity_key=f"{parsed['confirmation_id']}/{line['po_line']}")
                if res.changes:
                    self._results.append(res)
                    self._counts["rows_updated"] += 1
        if new:
            add_all(new)
            self._counts["rows_inserted"] += len(new)
        self._tables.add("order_confirmations")
        self._store_document(rel, text, kind="order_confirmation", supplier_id=supplier,
                             extra={"parsed": {k: v for k, v in parsed.items() if k != "notes"},
                                    "notes": parsed["notes"], "facts": facts(text)})

    def _store_scan(self, root: Path, rel: str) -> None:
        """Scans: file-name facts always; Claude vision OCR in live mode, cross-checked against our data."""
        from .ingest_docs import ScanExtraction

        supplier = _supplier_of(rel)
        sha = hashlib.sha256((root / rel).read_bytes()).hexdigest()
        from .ingest_docs import OCR_VERSION

        extra: dict = {"from_file_name": scan_info(rel), "sha256": sha, "ocr_version": OCR_VERSION}
        ocr = None
        cached = self._cached(rel, sha, "ocr", version=OCR_VERSION)
        if cached:
            ocr = ScanExtraction.model_validate(cached)
        else:
            try:
                ocr = ocr_scan(root / rel, rel, self.name)
            except Exception as exc:  # OCR must never stop the drop
                self.log("error", f"OCR {rel}: {type(exc).__name__}: {exc}", [{"path": rel}])
        if ocr is None:
            self._scans.append(rel)
            extra["ocr"] = "pending (needs live Claude vision)"
            self._store_document(rel, "", kind="scan", supplier_id=supplier, extra=extra)
            return
        extra["ocr"] = ocr.model_dump()
        extra["checks"] = self._check_scan(ocr, extra["from_file_name"], rel)
        supplier = supplier or self.resolve_supplier(ocr.supplier_name, rel) or ""
        text = "\n".join(filter(None, [ocr.summary_en, ocr.remarks, ocr.handwritten_notes]))
        self._store_document(rel, text, kind=ocr.document_type or "scan", supplier_id=supplier, extra=extra)
        self.log("scan_read", f"{rel}: {ocr.document_type} {ocr.document_number}, {len(ocr.lines)} lines, "
                 f"checks {extra['checks']['passed']}/{extra['checks']['total']}", [{"path": rel}])

    def _check_scan(self, ocr, from_name: dict, rel: str) -> dict:
        """Validate the OCR against what we already know (Instabase-style): confidence = checks passed."""
        from ..core.models import GoodsReceipt

        results: dict[str, bool] = {}
        notes: list[str] = []
        if from_name.get("delivery_note"):
            results["number_matches_file_name"] = ocr.document_number.strip() == from_name["delivery_note"]
        pos = {p.po_number for p in all_rows(PurchaseOrder)}
        if ocr.po_numbers:
            results["po_known"] = all(p in pos for p in ocr.po_numbers)
        resolved = []
        supplier = _supplier_of(rel) or self.resolve_supplier(ocr.supplier_name, rel) or ""
        for line in ocr.lines:
            part = line.part_number.strip().upper()
            if not part.startswith("CMP-"):
                part = self.resolve_part(supplier, part, line.description, rel) or part
            resolved.append(part)
        if ocr.lines:
            results["parts_known"] = all(p.startswith("CMP-") for p in resolved)
        receipts = [g for g in all_rows(GoodsReceipt)
                    if ocr.document_number and g.delivery_note == ocr.document_number.strip()]
        if receipts and ocr.lines:
            paper: dict[str, float] = {}  # one delivery can list the same part on several lines -> sum per part
            hand: dict[str, float] = {}
            for line, part in zip(ocr.lines, resolved):
                paper[part] = paper.get(part, 0) + (line.quantity or 0)
                if line.quantity_corrected_by_hand and line.printed_quantity is not None:
                    hand[part] = line.printed_quantity
            for part, qty in paper.items():
                booked = sum(g.qty_received or 0 for g in receipts if g.material_id == part)
                ordered = sum(g.qty_ordered or 0 for g in receipts if g.material_id == part)
                ok = abs(booked - qty) < 0.5
                results[f"qty_{part}_matches_receipt"] = ok
                if part in hand:
                    notes.append(f"{part}: printed {hand[part]:,.0f}, corrected by hand to {qty:,.0f}, "
                                 f"goods receipt booked {booked:,.0f} (ordered {ordered:,.0f})")
                elif not ok:
                    notes.append(f"{part}: paper says {qty:,.0f}, goods receipt booked {booked:,.0f}")
        if ocr.handwritten_notes:
            notes.append(f"handwritten: {ocr.handwritten_notes}")
        passed = sum(results.values())
        return {"results": results, "passed": passed, "total": len(results), "parts": resolved,
                "confidence": round(passed / len(results), 2) if results else None, "notes": notes,
                "goods_receipts": [g.gr_number for g in receipts]}

    def _store_document(self, rel: str, text: str, *, kind: str, supplier_id: str = "", extra: dict | None = None,
                        published_at=None) -> None:
        extracted = extra or {}
        title = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")[:200]
        values = {"path": rel, "kind": kind, "supplier_id": supplier_id or "", "language": language(text),
                  "title": title or extracted.get("title", ""), "text": text, "extracted": extracted,
                  "published_at": published_at}
        if extracted.get("llm"):
            values["text_en"] = extracted["llm"].get("summary_en", "")
        with session() as s:
            row = s.exec(select(Document).where(Document.path == rel)).first()
        if row is None:
            add_all([Document(**values, source=rel.split("#")[0])])
        elif row.text != text or row.extracted != extracted:
            res = self.update_row(Document, row.id, {"text": text, "extracted": extracted, "kind": kind},
                                  source=rel, on_conflict="overwrite", reason="document replaced", entity_key=rel)
            if res.changes:
                self._results.append(res)
        self._counts["documents"] += 1
        self._tables.add("documents")

    @staticmethod
    def _cached(rel: str, sha: str, key: str, version: int | None = None):
        """Reuse an earlier AI result for an unchanged file (no repeat cost on reruns)."""
        with session() as s:
            row = s.exec(select(Document).where(Document.path == rel)).first()
        if version is not None and (not row or (row.extracted or {}).get(f"{key}_version") != version):
            return None
        if row and isinstance(row.extracted, dict) and row.extracted.get("sha256") == sha:
            value = row.extracted.get(key)
            if value and not (isinstance(value, str) and value.startswith("pending")):
                return value
        return None

    # ----------------------------------------------------------- aliases
    def resolve_supplier(self, value: str, source: str) -> Optional[str]:
        """'SUP-0028' | 'Karton Nowak' | 'Karton Nowak Sp. z o.o.' | vendor no '28' -> 'SUP-0028'."""
        v = value.strip()
        if SUP_RE.fullmatch(v):
            return v
        alias = norm_name(v)
        with session() as s:
            hit = s.exec(select(Alias).where(Alias.kind == "supplier", Alias.alias == alias)).first()
        if hit:
            return hit.canonical_id
        suppliers = all_rows(Supplier)
        candidates: dict[str, str] = {}
        for sup in suppliers:
            for name in (sup.name, sup.legal_name):
                if name:
                    candidates[norm_name(name)] = sup.supplier_id
            if sup.vendor_no is not None:
                candidates[str(sup.vendor_no)] = sup.supplier_id
            digits = re.sub(r"\D", "", sup.supplier_id).lstrip("0")
            candidates.setdefault(digits, sup.supplier_id)  # legacy vendor no = numeric part of the id
        contained = [c for c in candidates if len(c) >= 5 and not c.isdigit() and re.search(rf"\b{re.escape(c)}\b", alias)]
        if alias in candidates:
            canonical, method, conf = candidates[alias], "master_data", 1.0
        elif contained:  # e.g. "Karton Nowak Sp. z o.o., Poznań" -> "karton nowak poznań" contains "karton nowak"
            canonical, method, conf = candidates[max(contained, key=len)], "contains", 0.95
        else:
            close = difflib.get_close_matches(alias, list(candidates), n=1, cutoff=0.85)
            if not close:
                self._unresolved.add(f"supplier '{v}' ({source})")
                return None
            canonical, method = candidates[close[0]], "fuzzy"
            conf = round(difflib.SequenceMatcher(None, alias, close[0]).ratio(), 2)
        add_all([Alias(kind="supplier", alias=alias, canonical_id=canonical, confidence=conf, method=method,
                       source=source)])
        return canonical

    def resolve_part(self, supplier_id: str, supplier_part: str, description: str, source: str) -> Optional[str]:
        """Supplier's own part number -> our CMP id (saved alias, else description match among their parts)."""
        alias = supplier_part.strip().upper()
        with session() as s:
            hit = s.exec(select(Alias).where(Alias.kind == "part", Alias.alias == alias)).first()
        if hit:
            return hit.canonical_id
        their = {m.component_id for m in all_rows(SupplierPartMap) if m.supplier_id == supplier_id}
        materials = [m for m in all_rows(Material) if not their or m.component_id in their]
        target = norm_name(description)
        best, score = None, 0.0
        for m in materials:
            r = difflib.SequenceMatcher(None, target, norm_name(m.description)).ratio()
            if r > score:
                best, score = m.component_id, r
        if best is None or score < 0.6:
            self._unresolved.add(f"part '{supplier_part}' of {supplier_id} ({source})")
            return None
        add_all([Alias(kind="part", alias=alias, canonical_id=best, confidence=round(score, 2),
                       method="description_match", source=source)])
        self.log("alias_learned", f"{supplier_id} part {supplier_part} = {best} (description match {score:.2f})",
                 [{"path": source, "quote": description}])
        return best

    # ------------------------------------------------------------- issues
    def _raise_summary_issues(self) -> None:
        """Ingest's own findings, in FINDING FORMAT v1 (.brain/DATA_FORMAT.md)."""
        if self._scans:
            # count every scan still unread, not only this drop's (earlier drops' scans are still unread too)
            pending = [d.path for d in all_rows(Document)
                       if d.kind == "scan" and str((d.extracted or {}).get("ocr", "")).startswith("pending")]
            self._scans = sorted(set(pending) | set(self._scans))
            why = "offline mode" if llm.is_mock() else "Claude was not reachable"
            self.raise_issue(
                type="scans_unread", kind=IssueKind.HUMAN_CHECK, severity=2, key="scans",
                summary=f"{len(self._scans)} scanned delivery notes stored but not read yet ({why})",
                reason="Receiving books what is on the paper, so a scan can hide a short or damaged delivery. "
                       "File names already link each scan to its delivery-note number; the content needs OCR.",
                subject_type="drop", subject_id="scans",
                impact={"note": f"{len(self._scans)} scans; quantities on paper not yet compared with goods receipts"},
                suggested_actions=[{"type": "human_review", "owner": "human",
                                    "label": "Switch to live mode (MOCK_MODE=false + API key) so Claude reads the scans"}],
                evidence=[{"path": p} for p in self._scans[:20]], confidence=1.0)
        if self._unmapped:
            self.raise_issue(
                type="unmapped_files", kind=IssueKind.HUMAN_CHECK, severity=1, key="unmapped",
                summary=f"{len(self._unmapped)} files kept as plain tables/documents (no matching table yet)",
                reason="They are stored and readable by every agent, but not split into our tables "
                       "(e.g. routes, ports, price lists).",
                subject_type="drop", subject_id="unmapped",
                suggested_actions=[{"type": "human_review", "owner": "human",
                                    "label": "Check if any of these files matter for the launch"}],
                evidence=[{"path": p} for p in self._unmapped[:30]], confidence=1.0)
        if self._unresolved:
            self.raise_issue(
                type="unresolved_ids", kind=IssueKind.HUMAN_CHECK, severity=3, key="unresolved",
                summary=f"{len(self._unresolved)} supplier names or part numbers could not be matched to our IDs",
                reason="Rows with an unknown supplier or part can't be linked to orders, stock or the launch "
                       "forecast, so problems in them stay invisible to the other agents.",
                subject_type="drop", subject_id="unresolved",
                impact={"note": "; ".join(sorted(self._unresolved)[:5])},
                suggested_actions=[
                    {"type": "human_review", "owner": "human",
                     "label": "Confirm the right ID for each name/part number (saved as an alias, asked only once)"},
                    {"type": "email", "owner": "chaser",
                     "label": "Ask the supplier which of our part numbers they mean"}],
                evidence=[{"path": "", "quote": u} for u in sorted(self._unresolved)[:30]], confidence=0.9)

    # ------------------------------------------------------------ replies
    def on_reply(self, event: Event) -> None:
        """Apply a supplier reply (T2.19): fill fields of the rows the question was about."""
        with session() as s:
            msg = s.get(InboxMessage, event.payload["inbox_id"])
            issue = s.get(Issue, msg.issue_id) if msg and msg.issue_id else None
        if msg is None:
            return
        src = f"inbox/{msg.id}"
        found = facts(msg.body)
        extra = {"facts": found}
        extracted = llm_extract(msg.body, "reply", src, self.name)
        if extracted:
            extra["llm"] = extracted
        self._results, self._tables, self._counts = [], set(), {"rows_inserted": 0, "rows_updated": 0, "documents": 0}
        self._store_document(src, msg.body, kind="reply", supplier_id=msg.supplier_id, extra=extra)
        values: dict[str, Any] = {}
        if len(found.get("revisions", [])) == 1:
            values["revision"] = found["revisions"][0]
        future = [d for d in found.get("dates", []) if d >= msg.received_at.date().isoformat()]
        if len(future) == 1:
            values["delivery_date"] = future[0]
        if len(found.get("quantities", [])) == 1:
            values["quantity"] = found["quantities"][0]
        targets: list[OrderConfirmation] = []
        if issue and values:
            for oc in all_rows(OrderConfirmation):
                if issue.po_number and oc.po_number != issue.po_number:
                    continue
                if issue.part_id and oc.material_id != issue.part_id:
                    continue
                if not (issue.po_number or issue.part_id) or (msg.supplier_id and oc.supplier_id != msg.supplier_id):
                    continue
                targets.append(oc)
        for oc in targets:
            res = self.update_row(OrderConfirmation, oc.id, values, source=src, reason="supplier reply",
                                  issue_id=issue.id if issue else None, confidence=0.8,
                                  entity_key=f"{oc.confirmation_id}/{oc.po_line}")
            if res.changes or res.conflicts:
                self._results.append(res)
        with session() as s:
            m = s.get(InboxMessage, msg.id)
            m.processed = True
            m.result = {"fields": sorted(values), "rows": len(targets),
                        "changes": sum(len(r.changes) for r in self._results),
                        "conflicts": sum(len(r.conflicts) for r in self._results)}
            s.add(m)
            s.commit()
        self.emit_truth_updated(self._results, tables=sorted(self._tables | {"inbox"}),
                                issue_ids=[issue.id] if issue else [])
