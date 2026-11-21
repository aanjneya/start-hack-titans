"""Read site-level book counts separately from the physical lots that establish availability."""
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
