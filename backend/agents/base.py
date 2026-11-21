"""Base class every agent extends. Gives logging, event emitting and issue helpers for free."""
from __future__ import annotations

import hashlib
from typing import Any, Callable, ClassVar, Iterable

from sqlmodel import select

from ..core import events, trace
from ..core.coordinator import Coordinator, Event
from ..core.models import Evidence, Issue, utcnow
from ..core.store import session


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
        self.bus.emit(event_name, payload or {}, source=self.name)

    def raise_issue(self, *, type: str, kind: str, summary: str, key: str, severity: int = 3,
                    evidence: Iterable[Evidence | dict] = (), **fields: Any) -> Issue:
        """Create or update an issue (deduped by `key`) and emit issue.raised.

        Re-running a check after a new drop updates the same issue instead of duplicating it;
        a resolved issue that shows up again is marked 'reopened'.
        """
        fingerprint = hashlib.sha1(f"{type}|{key}".encode()).hexdigest()[:16]
        ev = [e.model_dump() if isinstance(e, Evidence) else dict(e) for e in evidence]
        with session() as s:
            issue = s.exec(select(Issue).where(Issue.fingerprint == fingerprint)).first()
            if issue is None:
                issue = Issue(fingerprint=fingerprint, type=type, kind=kind, severity=severity,
                              summary=summary, evidence=ev, raised_by=self.name, **fields)
                action = "raise_issue"
            else:
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


#: registry filled by @register; system.py builds one instance of each
REGISTRY: list[type[Agent]] = []


def register(cls: type[Agent]) -> type[Agent]:
    REGISTRY.append(cls)
    return cls
