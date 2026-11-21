"""SQLite truth store: engine, sessions, views and small helpers shared by all agents.

New facts: add_all(). Changing existing values: update_row() (logs every change). Current values per key
across several sources: latest_by().
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Sequence, TypeVar

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine, select

from . import models  # noqa: F401  (registers all tables on SQLModel.metadata)
from .config import get_settings

T = TypeVar("T", bound=SQLModel)

_engine: Engine | None = None

# One row per PO line with how far it got: ordered -> confirmed -> shipped -> received.
# Uses every stored row (history included); agents pick the latest by ingested_at where needed.
PART_JOURNEY_VIEW = """
CREATE VIEW IF NOT EXISTS part_journey AS
SELECT
  po.po_number, po.po_line, po.supplier_id, po.material_id,
  po.quantity AS qty_ordered, po.promised_date, po.status AS po_status, po.source AS po_source,
  oc.quantity AS qty_confirmed, oc.delivery_date AS confirmed_delivery, oc.revision AS confirmed_revision,
  s.shipment_id, s.mode AS shipment_mode, s.eta AS shipment_eta, s.status AS shipment_status,
  s.updated_at AS shipment_updated_at, s.source AS shipment_source,
  gr.gr_number, gr.qty_received, gr.gr_date,
  CASE
    WHEN gr.gr_number IS NOT NULL THEN 'received'
    WHEN s.shipment_id IS NOT NULL THEN 'shipped'
    WHEN oc.id IS NOT NULL THEN 'confirmed'
    ELSE 'ordered'
  END AS last_step
FROM purchase_orders po
LEFT JOIN order_confirmations oc
  ON oc.po_number = po.po_number AND (oc.po_line IS NULL OR oc.po_line = po.po_line)
LEFT JOIN shipment_po sp ON sp.po_number = po.po_number
LEFT JOIN shipments s ON s.shipment_id = sp.shipment_id
LEFT JOIN goods_receipts gr
  ON gr.po_number = po.po_number AND (gr.po_line IS NULL OR gr.po_line = po.po_line)
"""


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        path = get_settings().db_path
        path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    return _engine


def init_db() -> None:
    engine = get_engine()
    SQLModel.metadata.create_all(engine)
    _add_missing_columns(engine)
    with engine.begin() as conn:
        conn.execute(text(PART_JOURNEY_VIEW))


def _add_missing_columns(engine: Engine) -> None:
    """Tiny auto-migration: when a shared model gains a column, add it to existing local DBs."""
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            existing = {r[1] for r in conn.execute(text(f'PRAGMA table_info("{table.name}")'))}
            for col in table.columns:
                if col.name not in existing:
                    conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}"'))


def reset_db() -> None:
    """Drop and recreate everything (tests, replay from scratch)."""
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DROP VIEW IF EXISTS part_journey"))
    SQLModel.metadata.drop_all(engine)
    init_db()


def dispose_engine() -> None:
    """For tests: close the engine so a new DB_PATH takes effect."""
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None


@contextmanager
def session() -> Iterator[Session]:
    with Session(get_engine(), expire_on_commit=False) as s:
        yield s


def add_all(rows: Iterable[SQLModel]) -> list[SQLModel]:
    """Insert new rows (new facts). To change existing rows use update_row(). Returns rows with ids."""
    rows = list(rows)
    with session() as s:
        s.add_all(rows)
        s.commit()
        for row in rows:
            s.refresh(row)
    return rows


def all_rows(model: type[T]) -> Sequence[T]:
    with session() as s:
        return s.exec(select(model)).all()


def latest_by(rows: Iterable[T], *key_fields: str) -> list[T]:
    """Current truth helper: keep the newest row (by ingested_at, then id) per key.

    Example: latest_by(all_rows(StockLot), "material_id", "batch")
    """
    best: dict[tuple, T] = {}
    for row in rows:
        key = tuple(getattr(row, f) for f in key_fields)
        stamp = (getattr(row, "ingested_at", None), getattr(row, "id", 0) or 0)
        current = best.get(key)
        if current is None or stamp >= (getattr(current, "ingested_at", None), getattr(current, "id", 0) or 0):
            best[key] = row
    return list(best.values())


def query(sql: str, **params) -> list[dict]:
    """Run raw SQL (e.g. on the part_journey view) and return dict rows."""
    with get_engine().connect() as conn:
        result = conn.execute(text(sql), params)
        return [dict(r._mapping) for r in result]


# ---------------------------------------------------------------------------
# Updating the truth (team decision Sat 19:30): rows can change, every change is logged.
# ---------------------------------------------------------------------------
_EMPTY = (None, "")


@dataclass
class UpdateResult:
    table: str
    row_id: int
    changes: list = field(default_factory=list)  # list[FieldChange]
    conflicts: list = field(default_factory=list)  # list[Conflict]

    @property
    def changed(self) -> bool:
        return bool(self.changes)

    def as_payload(self) -> dict:
        """Shape used in truth.updated events: which row and which fields changed."""
        return {"table": self.table, "row_id": self.row_id, "fields": [c.field for c in self.changes],
                "conflicts": [c.id for c in self.conflicts]}


def _as_text(value) -> str | None:
    """Comparable text. Aware datetimes compare in UTC (SQLite keeps them as naive UTC)."""
    from datetime import datetime, timezone

    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return value.replace(tzinfo=None).isoformat(sep=" ")
    return str(value)


def _coerce(model: type[SQLModel], field_name: str, value):
    """'2026-11-22' -> date, '15000' -> int etc., using the model's type hints."""
    from pydantic import TypeAdapter

    info = model.model_fields.get(field_name)
    if info is None:
        raise ValueError(f"{model.__name__} has no field {field_name!r}")
    if value in _EMPTY:
        return value
    return TypeAdapter(info.annotation).validate_python(value)


def update_row(
    model: type[T],
    row_id: int,
    values: dict,
    *,
    source: str,
    agent: str,
    reason: str = "",
    confidence: float | None = None,
    on_conflict: str = "record",
    issue_id: int | None = None,
    entity_key: str = "",
) -> UpdateResult:
    """Change fields of one truth-store row and log every change in `field_changes`.

    - Empty field -> filled (change_type "fill").
    - Field already has a different value:
        on_conflict="record"    (default) keep the old value, add a `conflicts` row for the Reconciler
        on_conflict="overwrite" replace it (change_type "overwrite"), e.g. a confirmed correction
        on_conflict="skip"      leave it, log nothing
        on_conflict="newest"    replace it AND record the conflict (newest source wins, disagreement stays visible)
    - Same value -> nothing happens (so re-processing the same reply never loops).
    Returns UpdateResult; emit truth.updated only if result.changed (see Agent.update_row).
    """
    from .models import Conflict, FieldChange, utcnow
    from .trace import current_run_id

    if on_conflict not in {"record", "overwrite", "skip", "newest"}:
        raise ValueError(f"on_conflict must be record|overwrite|skip|newest, not {on_conflict!r}")
    table = model.__tablename__
    result = UpdateResult(table=table, row_id=row_id)
    with session() as s:
        row = s.get(model, row_id)
        if row is None:
            raise KeyError(f"{table} row {row_id} not found")
        key = entity_key or str(row_id)
        for field_name, raw in values.items():
            new = _coerce(model, field_name, raw)
            old = getattr(row, field_name)
            if _as_text(old) == _as_text(new):
                continue
            if old in _EMPTY:
                change_type = "fill"
            elif on_conflict in {"overwrite", "newest"}:
                change_type = "overwrite"
                if on_conflict == "newest":
                    result.conflicts.append(Conflict(
                        entity=table, entity_id=key, field=field_name,
                        value_a=_as_text(old) or "", source_a=getattr(row, "source", ""),
                        value_b=_as_text(new) or "", source_b=source, status="auto_resolved",
                    ))
            elif on_conflict == "record":
                result.conflicts.append(Conflict(
                    entity=table, entity_id=key, field=field_name,
                    value_a=_as_text(old) or "", source_a=getattr(row, "source", ""),
                    value_b=_as_text(new) or "", source_b=source,
                ))
                continue
            else:
                continue
            setattr(row, field_name, new)
            result.changes.append(FieldChange(
                run_id=current_run_id(), table_name=table, row_id=row_id, entity_key=key,
                field=field_name, old_value=_as_text(old), new_value=_as_text(new),
                change_type=change_type, source=source, agent=agent, reason=reason,
                confidence=confidence, issue_id=issue_id,
            ))
        if result.changes and hasattr(row, "updated_at"):
            row.updated_at = utcnow()
        s.add(row)
        s.add_all(result.changes + result.conflicts)
        s.commit()
        for obj in result.changes + result.conflicts:
            s.refresh(obj)
    return result


def find_rows(model: type[T], **filters) -> Sequence[T]:
    """Simple equality lookup, e.g. find_rows(OrderConfirmation, po_number="4500318745")."""
    with session() as s:
        stmt = select(model)
        for name, value in filters.items():
            stmt = stmt.where(getattr(model, name) == value)
        return s.exec(stmt).all()
