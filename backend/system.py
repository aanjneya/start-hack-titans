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
from .core.models import AgentLog, DropRecord, InboxMessage, Issue
from .core.store import init_db, reset_db, session

_bus: Coordinator | None = None
_agents: list[Agent] = []
_lock = threading.RLock()  # the timer thread and API calls must not run the loop at the same time


def bus() -> Coordinator:
    global _bus, _agents
    if _bus is None:
        init_db()
        _bus = Coordinator()
        _agents = [cls(_bus) for cls in REGISTRY]
        # A saved reply can outlive the process or the handler that was applying it.
        with session() as db:
            pending = db.exec(select(InboxMessage).where(InboxMessage.processed == False).order_by(InboxMessage.id)).all()
        for msg in pending:
            _bus.emit(events.REPLY_RECEIVED, {"inbox_id": msg.id, "supplier_id": msg.supplier_id,
                                             "issue_id": msg.issue_id}, source="inbox_recovery")
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
    git_commit: str = "",
    retries: list[dict] | None = None,
    watcher_run: str | None = None,
) -> dict:
    """Record a drop and push it through all agents until nothing is left to do."""
    with _lock:
        return _process_drop(added=added, changed=changed, removed=removed, label=label, data_root=data_root,
                             mode=mode, git_commit=git_commit, retries=retries, watcher_run=watcher_run)


def _complete_run(b, run_id, *, record_retries=False):
    steps = b.run_until_idle(record_retries=record_retries)
    with session() as db:
        errors = db.exec(select(AgentLog).where(AgentLog.run_id == run_id, AgentLog.action == "error")
                         .order_by(AgentLog.id)).all()
    failures = [dict(agent=e.agent, event=e.data.get("event", "handler"), trace_id=e.id) for e in errors]
    return dict(run_id=run_id, steps=steps, status="partial" if failures else "completed", failures=failures)


def _process_drop(*, added, changed, removed, label, data_root, mode, git_commit, retries, watcher_run) -> dict:
    b = bus()
    root = Path(data_root or get_settings().data_root)
    added, changed, removed = list(added), list(changed), list(removed)
    run_id = watcher_run or trace.new_run_id()
    with session() as s:
        rec = DropRecord(label=label, mode=mode, git_commit=git_commit, run_id=run_id, added=added, changed=changed, removed=removed)
        s.add(rec)
        s.commit()
        s.refresh(rec)
    trace.log("coordinator", "drop", f"{label or mode}: +{len(added)} ~{len(changed)} -{len(removed)}",
              data={"drop_id": rec.id, "data_root": str(root)})
    if retries:
        for retry in retries:
            payload = dict(retry["payload"])
            if "data_root" in payload:
                payload["data_root"] = str(root)
            if "drop_id" in payload:
                payload["drop_id"] = rec.id
            b.emit(retry["name"], payload, retry["source"], target=retry["target"])
    else:
        payload = {"added": added, "changed": changed, "removed": removed,
                   "label": label, "data_root": str(root), "drop_id": rec.id}
        if mode == "replay":
            next(a for a in _agents if a.name == "watcher").replay_manifest(payload)
        else:
            b.emit(events.FILES_CHANGED, payload, source="watcher" if watcher_run else "coordinator")
    result = _complete_run(b, run_id, record_retries=mode == "replay")
    with session() as s:
        saved = s.get(DropRecord, rec.id)
        saved.status, saved.failures = result["status"], result["failures"]
        s.add(saved)
        s.commit()
        open_issues = s.exec(select(func.count()).select_from(Issue).where(Issue.status != "resolved")).one()
    return {**result, "drop_id": rec.id, "label": label,
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
        return _complete_run(b, run_id)


def refresh_risk(source: str = "api") -> dict:
    with _lock:
        payload = {}
        if get_settings().mock_mode:
            # Replay must use the same port master as the materialized historical snapshot.
            with session() as db:
                last = db.exec(select(AgentLog).where(AgentLog.agent == "coordinator", AgentLog.action == "drop")
                               .order_by(AgentLog.id.desc()).limit(1)).first()
                if last and last.data.get("data_root"):
                    payload["data_root"] = last.data["data_root"]
        result = emit(events.RISK_REFRESH_REQUESTED, payload, source)
        with session() as db:
            reading = db.exec(select(AgentLog).where(AgentLog.run_id == result["run_id"], AgentLog.agent == "risk",
                    AgentLog.action.in_(["risk_rated", "risk_refresh_incomplete"])).order_by(AgentLog.id.desc()).limit(1)).first()
        trace.log("risk", "signal_check", f"Signal check {result['status']}.", data={
            **(reading.data if reading else {}), "trigger":events.RISK_REFRESH_REQUESTED, "failures":result["failures"]})
        return result


def store_reply(*, supplier_id: str, body: str, issue_id: int | None = None, outbox_id: int | None = None,
                from_email: str = "", subject: str = "", language: str = "", source: str = "chaser", transport_message_id=None, delivery=None, quote=None):
    """Save a supplier/carrier reply in the inbox (no event). Inside an agent, follow with
    self.emit(events.REPLY_RECEIVED, {"inbox_id": msg.id, "supplier_id": ..., "issue_id": ...})."""
    with session() as s:
        msg = InboxMessage(transport_message_id=transport_message_id, supplier_id=supplier_id, body=body, issue_id=issue_id, outbox_id=outbox_id,
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
        b = bus()
        msg = store_reply(source=source, delivery=delivery, quote=quote, **fields)
        b.emit(events.REPLY_RECEIVED, {"inbox_id": msg.id, "supplier_id": msg.supplier_id,
                                       "issue_id": msg.issue_id}, source=source)
        return {"inbox_id": msg.id, **_complete_run(b, run_id)}


def watch_once() -> dict:
    """One live portal check by the Watcher (no-op in MOCK mode)."""
    bus()
    watcher = next(a for a in _agents if a.name == "watcher")
    with _lock:
        return watcher.check()


def replay_step() -> dict:
    from .core.replay import advance
    return advance()
