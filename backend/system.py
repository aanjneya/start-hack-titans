"""Wires everything together: one Coordinator + one instance of every registered agent.

Use these functions from the API, the replay, tests or a REPL:
    from backend import system
    system.reset(); system.run_full()          # ingest the whole current portal mirror
    system.process_drop(added=[...], changed=[...], label="2026-11-21T16-08")
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from sqlmodel import func, select

from . import agents  # noqa: F401  (registers all agent classes)
from .agents.base import REGISTRY, Agent
from .core import events, trace
from .core.config import get_settings
from .core.coordinator import Coordinator
from .core.models import DropRecord, Issue
from .core.store import init_db, reset_db, session

_bus: Coordinator | None = None
_agents: list[Agent] = []


def bus() -> Coordinator:
    global _bus, _agents
    if _bus is None:
        init_db()
        _bus = Coordinator()
        _agents = [cls(_bus) for cls in REGISTRY]
    return _bus


def agent_names() -> list[str]:
    bus()
    return [a.name for a in _agents]


def reset() -> None:
    """Empty the truth store and rebuild the agents (fresh replay)."""
    global _bus
    reset_db()
    _bus = None
    bus()


def list_data_files(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and not p.name.startswith("."))


def process_drop(
    *,
    added: Iterable[str] = (),
    changed: Iterable[str] = (),
    removed: Iterable[str] = (),
    label: str = "",
    data_root: Path | None = None,
    mode: str = "manual",
) -> dict:
    """Record a drop and push it through all agents until nothing is left to do."""
    root = Path(data_root or get_settings().data_root)
    added, changed, removed = list(added), list(changed), list(removed)
    run_id = trace.new_run_id()
    with session() as s:
        rec = DropRecord(label=label, mode=mode, added=added, changed=changed, removed=removed)
        s.add(rec)
        s.commit()
        s.refresh(rec)
    trace.log("coordinator", "drop", f"{label or mode}: +{len(added)} ~{len(changed)} -{len(removed)}",
              data={"drop_id": rec.id, "data_root": str(root)})
    b = bus()
    b.emit(events.FILES_CHANGED, {"added": added, "changed": changed, "removed": removed,
                                  "label": label, "data_root": str(root), "drop_id": rec.id}, source="coordinator")
    steps = b.run_until_idle()
    with session() as s:
        open_issues = s.exec(select(func.count()).select_from(Issue).where(Issue.status != "resolved")).one()
    return {"run_id": run_id, "drop_id": rec.id, "label": label, "steps": steps,
            "added": len(added), "changed": len(changed), "removed": len(removed), "open_issues": open_issues}


def run_full(data_root: Path | None = None, label: str = "full run") -> dict:
    """Treat every file in the data folder as new (first load or 'reprocess everything')."""
    root = Path(data_root or get_settings().data_root)
    return process_drop(added=list_data_files(root), label=label, data_root=root, mode="manual")


def emit(event_name: str, payload: dict, source: str = "api") -> dict:
    """Emit one event (e.g. decision.approved from the UI) and process the consequences."""
    run_id = trace.new_run_id()
    b = bus()
    b.emit(event_name, payload, source=source)
    return {"run_id": run_id, "steps": b.run_until_idle()}
