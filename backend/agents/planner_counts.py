"""Read site-level book counts separately from the physical lots that establish availability."""
import re
from collections import defaultdict

from ..core.parsing import parse_number


def site_counts(notes, documents, lots, site):
    """Support both raw workbook tables and stock rows already normalized by Ingest."""
    tables = {doc.path: doc for doc in documents if doc.kind == "table"}
    mapped = defaultdict(list)
    for lot in lots:
        if lot.site == site and not lot.batch:
            mapped[lot.source].append(lot)
    for note in notes:
        title = note.text.splitlines()[0] if note.text else ""
        sheet = note.path.removesuffix("#notes")
        table = tables.get(sheet)
        counts = {lot.material_id: lot.quantity for lot in mapped[sheet]}
        if table:
            counts.update({r["Material"]: parse_number(r.get("Qty")) for r in table.extracted.get("rows", [])
                           if r.get("Site") == site and r.get("Material")})
        counts = {pid: int(qty) for pid, qty in counts.items()
                  if qty is not None and qty >= 0 and float(qty).is_integer()}
        if counts:
            yield title, note, counts


def receiving_corrections(documents, receipts):
    """Only correct a unique physical receipt. Positive differences never create new stock."""
    reductions = {}
    for doc in documents:
        checks = doc.extracted.get("checks", {})
        ocr = doc.extracted.get("ocr")
        if not isinstance(ocr, dict):
            continue
        for part in set(checks.get("parts", [])):
            rows = [r for r in receipts if r.gr_number in checks.get("goods_receipts", [])
                    and r.material_id == part and r.delivery_note == ocr.get("document_number")]
            lines = [line for line, material in zip(ocr.get("lines", []), checks["parts"]) if material == part]
            if len(rows) != 1 or not lines or not any(l.get("quantity_corrected_by_hand") for l in lines):
                continue
            if any(l.get("quantity") is None for l in lines):
                continue
            row = rows[0]
            physical = sum(l["quantity"] for l in lines)
            if row.qty_received and 0 <= physical < row.qty_received:
                reductions[row.gr_number] = (row.qty_received - physical, doc.path, "Hand-corrected delivery note")
    for row in receipts:
        mismatch = re.search(r"labelled ([\w-]+) holds ([\w-]+).*?\(([\d,]+) pcs\)", row.remark or "", re.I)
        if mismatch and mismatch[1] == row.material_id and mismatch[2] != row.material_id:
            qty = int(mismatch[3].replace(",", ""))
            if row.qty_received and 0 < qty <= row.qty_received:
                reductions[row.gr_number] = (qty, row.source, row.remark)
    return reductions


def fitted_components(parts, history):
    """Supplier notes identify components that arrive inside a finished assembly."""
    for part in parts:
        previous = [c.old_value for c in history if c.table_name == "supplier_part_map"
                    and c.row_id == part.id and c.field == "note"]
        for note in [part.note, *previous]:
            fitted = re.search(r"fitted by supplier into ([\w-]+)", note or "", re.I)
            if fitted:
                yield part, fitted[1], note
                break
