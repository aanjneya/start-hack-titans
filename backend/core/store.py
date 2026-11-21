"""SQLite truth store: engine, sessions, views and small helpers shared by all agents."""
from __future__ import annotations

from contextlib import contextmanager
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
    with engine.begin() as conn:
        conn.execute(text(PART_JOURNEY_VIEW))


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
    """Append facts (never overwrite). Returns the stored rows with ids."""
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
