"""Wires everything together: one Coordinator + one instance of every registered agent.

Use these functions from the API, the replay, tests or a REPL:
    from backend import system
    system.reset(); system.run_full()          # ingest the whole current portal mirror
    system.process_drop(added=[...], changed=[...], label="2026-11-21T16-08")
"""
from __future__ import annotations

import threading
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
_lock = threading.RLock()  # the timer thread and API calls must not run the loop at the same time
_replay_pos = 0


def bus() -> Coordinator:
    global _bus, _agents
    if _bus is None:
        init_db()
        _bus = Coordinator()
        _agents = [cls(_bus) for cls in REGISTRY]
        if get_settings().mock_mode:
            from .agents.planner_delivery import mock_outbox
            _bus.subscribe(events.EMAIL_REQUESTED, "chaser", mock_outbox)
    return _bus


def agent_names() -> list[str]:
    bus()
    return [a.name for a in _agents]


def reset() -> None:
    """Empty the truth store and rebuild the agents (fresh replay)."""
    global _bus, _replay_pos
    _replay_pos = 0
    reset_db()
    _bus = None
    bus()


def list_data_files(root: Path) -> list[str]:
    # always forward slashes (also on Windows): every agent and the DB use portal-style paths "soony/erp/…"
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and not p.name.startswith("."))


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
    with _lock:
        return _process_drop(added=added, changed=changed, removed=removed, label=label, data_root=data_root,
                             mode=mode)


def _process_drop(*, added, changed, removed, label, data_root, mode) -> dict:
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
    with _lock:
        run_id = trace.new_run_id()
        b = bus()
        b.emit(event_name, payload, source=source)
        return {"run_id": run_id, "steps": b.run_until_idle()}


def store_reply(*, supplier_id: str, body: str, issue_id: int | None = None, outbox_id: int | None = None,
                from_email: str = "", subject: str = "", language: str = "", source: str = "chaser", delivery=None, quote=None):
    """Save a supplier/carrier reply in the inbox (no event). Inside an agent, follow with
    self.emit(events.REPLY_RECEIVED, {"inbox_id": msg.id, "supplier_id": ..., "issue_id": ...})."""
    from .core.models import InboxMessage

    with session() as s:
        msg = InboxMessage(supplier_id=supplier_id, body=body, issue_id=issue_id, outbox_id=outbox_id,
                           from_email=from_email, subject=subject, language=language,
                           result={k: value.model_dump(mode="json") for k, value in
                                   (("delivery", delivery), ("quote", quote)) if value is not None})
        s.add(msg)
        s.commit()
        s.refresh(msg)
    trace.log(source, "reply_received", f"{supplier_id}: {subject or body[:80]}",
              data={"inbox_id": msg.id, "issue_id": issue_id})
    return msg


def receive_reply(*, source: str = "api", delivery=None, quote=None, **fields) -> dict:
    """Outside the agents (API, demo, tests): store the reply and run the loop
    (inbox -> Ingest applies it -> truth.updated -> Reconciler re-checks)."""
    with _lock:
        run_id = trace.new_run_id()
        msg = store_reply(source=source, delivery=delivery, quote=quote, **fields)
        b = bus()
        b.emit(events.REPLY_RECEIVED, {"inbox_id": msg.id, "supplier_id": msg.supplier_id,
                                       "issue_id": msg.issue_id}, source=source)
        return {"inbox_id": msg.id, "run_id": run_id, "steps": b.run_until_idle()}


def watch_once() -> dict:
    """One live portal check by the Watcher (no-op in MOCK mode)."""
    bus()
    watcher = next(a for a in _agents if a.name == "watcher")
    with _lock:
        return watcher.check()


def replay_step() -> dict:
    """Demo: send the NEXT real drop from git history through the agents ("a new drop arrives").
    First call starts from an empty store with the baseline drop."""
    global _replay_pos
    from .core.replay import list_drops, materialize

    with _lock:
        drops = list_drops()
        if _replay_pos == 0:
            reset()
        if _replay_pos >= len(drops):
            return {"done": True, "drops": len(drops)}
        drop = drops[_replay_pos]
        _replay_pos += 1
        root = materialize(drop)
        result = process_drop(added=drop.added, changed=drop.changed, removed=drop.removed, label=drop.label,
                              data_root=root, mode="replay")
        return {**result, "step": _replay_pos, "of": len(drops)}
