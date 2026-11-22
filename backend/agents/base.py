"""Base class every agent extends. Gives logging, event emitting and issue helpers for free."""
from __future__ import annotations

import hashlib
from typing import Any, Callable, ClassVar, Iterable

from sqlmodel import select

from ..core import events, trace
from ..core.coordinator import Coordinator, Event
from ..core.models import Evidence, Finding, Issue, utcnow
from ..core import store
from ..core.store import UpdateResult, session


def issue_fingerprint(issue_type: str, key: str) -> str:
    return hashlib.sha1(f"{issue_type}|{key}".encode()).hexdigest()[:16]


class Agent:
    #: unique short name, shown in the agent log and UI
    name: ClassVar[str] = "agent"
    #: event name -> method name, e.g. {events.FILES_CHANGED: "on_files_changed"}
    subscriptions: ClassVar[dict[str, str]] = {}

    def __init__(self, bus: Coordinator) -> None:
        self.bus = bus
        for event_name, method in self.subscriptions.items():
            handler: Callable[[Event], None] = getattr(self, method)
            bus.subscribe(event_name, self.name, self._wrap(event_name, handler))

    def _wrap(self, event_name: str, handler: Callable[[Event], None]) -> Callable[[Event], None]:
        def run(event: Event) -> None:
            self.log(f"handle:{event_name}", f"from {event.source or 'api'}", data={"payload_keys": sorted(event.payload)})
            handler(event)

        return run

    # -- helpers -------------------------------------------------------------
    def log(self, action: str, detail: str = "", evidence: Iterable[Evidence | dict] | None = None,
            data: dict[str, Any] | None = None) -> None:
        trace.log(self.name, action, detail, evidence, data)

    def emit(self, event_name: str, payload: dict[str, Any] | None = None) -> None:
        """Every hand-off carries the same envelope: _meta = {from, run_id, ts, v} + the event's own keys."""
        body = dict(payload or {})
        body["_meta"] = {"from": self.name, "run_id": trace.current_run_id(), "ts": utcnow().isoformat(), "v": 1}
        self.bus.emit(event_name, body, source=self.name)

    def raise_issue(self, *, type: str, kind: str, summary: str, key: str, severity: int = 3,
                    evidence: Iterable[Evidence | dict] = (), **fields: Any) -> Issue:
        """Hand a problem on in the shared FINDING FORMAT v1 (.brain/DATA_FORMAT.md) and emit issue.raised.

        Fields (all validated by models.Finding): reason, subject_type/subject_id, part_id, supplier_id,
        shipment_id, po_number, product_id, impact={copies_at_risk, hours_buffer, due_by, cost_eur, ...},
        suggested_actions=[{type, label, target, owner, cost_eur, copies_gained}], confidence (0..1).
        Deduped by `key`: re-running a check updates the same finding; a resolved one that comes back is reopened.
        """
        f = Finding(type=type, kind=kind, summary=summary, severity=severity,
                    evidence=[e if isinstance(e, Evidence) else Evidence(**{"path": "", **dict(e)})
                              for e in evidence], **fields)
        fields = {k: v for k, v in f.model_dump(mode="json").items()
                  if k not in {"type", "kind", "summary", "severity", "evidence"}}
        fingerprint = issue_fingerprint(type, key)
        ev = [e.model_dump() for e in f.evidence]
        with session() as s:
            issue = s.exec(select(Issue).where(Issue.fingerprint == fingerprint)).first()
            if issue is None:
                issue = Issue(fingerprint=fingerprint, type=type, kind=kind, severity=severity,
                              summary=summary, evidence=ev, raised_by=self.name, **fields)
                action, notify = "raise_issue", True
            else:
                # only hand it on again if it came back or got worse (re-checks must not spam the Chaser)
                notify = issue.status == "resolved" or kind != issue.kind or severity > issue.severity
                issue.summary, issue.severity, issue.kind, issue.evidence = summary, severity, kind, ev
                for k, v in fields.items():
                    setattr(issue, k, v)
                if issue.status == "resolved":
                    issue.status = "reopened"
                issue.updated_at = utcnow()
                action = "update_issue"
            s.add(issue)
            s.commit()
            s.refresh(issue)
        self.log(action, summary, ev, data={"issue_id": issue.id, "type": type, "kind": kind, "severity": severity})
        if notify:
            self.emit(events.ISSUE_RAISED, {"issue_id": issue.id, "kind": kind})
        return issue

    def resolve_issue(self, issue_id: int, reason: str) -> None:
        with session() as s:
            issue = s.get(Issue, issue_id)
            if issue is None or issue.status == "resolved":
                return
            issue.status, issue.updated_at = "resolved", utcnow()
            s.add(issue)
            s.commit()
        self.log("resolve_issue", reason, data={"issue_id": issue_id})


    # -- updating the truth (only the Ingest agent writes supply-chain data) --------------
    def update_row(self, model, row_id: int, values: dict, *, source: str, reason: str = "",
                   confidence: float | None = None, on_conflict: str = "record",
                   issue_id: int | None = None, entity_key: str = "") -> UpdateResult:
        """Fill/correct fields of an existing row; every change goes to the change log + agent log."""
        result = store.update_row(model, row_id, values, source=source, agent=self.name, reason=reason,
                                  confidence=confidence, on_conflict=on_conflict, issue_id=issue_id,
                                  entity_key=entity_key)
        if result.changes or result.conflicts:
            detail = ", ".join(f"{c.field}: {c.old_value!r} -> {c.new_value!r}" for c in result.changes)
            if result.conflicts:
                detail += f" | {len(result.conflicts)} conflict(s) recorded"
            self.log("update_row", f"{result.table}#{row_id} {detail}", [{"path": source, "locator": reason}],
                     data={"table": result.table, "row_id": row_id, "issue_id": issue_id,
                           "changed": [c.field for c in result.changes],
                           "conflicts": [c.id for c in result.conflicts]})
        return result

    def emit_truth_updated(self, results: Iterable[UpdateResult] = (), *, files: Iterable[str] = (),
                           tables: Iterable[str] = (), data_root: str | None = None,
                           issue_ids: Iterable[int] = ()) -> bool:
        """Announce new/changed data. Does nothing if nothing changed (prevents endless loops)."""
        changes = [r.as_payload() for r in results if r.changes or r.conflicts]
        files, tables = list(files), list(tables)
        if not (changes or files or tables):
            self.log("no_change", "nothing new to announce")
            return False
        self.emit(events.TRUTH_UPDATED, {"changes": changes, "files": files, "tables": tables,
                                         "data_root": data_root, "issue_ids": list(issue_ids)})
        return True


#: registry filled by @register; system.py builds one instance of each
REGISTRY: list[type[Agent]] = []


def register(cls: type[Agent]) -> type[Agent]:
    REGISTRY.append(cls)
    return cls
