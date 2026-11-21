"""Agent log (AI traceability): every agent action ends up in the `agent_log` table."""
from __future__ import annotations

import contextvars
import uuid
from typing import Any, Iterable

from sqlmodel import select

from .models import AgentLog, Evidence
from .store import session

_run_id: contextvars.ContextVar[str] = contextvars.ContextVar("run_id", default="")


def new_run_id() -> str:
    run_id = uuid.uuid4().hex[:12]
    _run_id.set(run_id)
    return run_id


def current_run_id() -> str:
    return _run_id.get()


def set_run_id(run_id: str) -> None:
    _run_id.set(run_id)


def _evidence_dicts(evidence: Iterable[Evidence | dict] | None) -> list[dict]:
    out: list[dict] = []
    for item in evidence or []:
        out.append(item.model_dump() if isinstance(item, Evidence) else dict(item))
    return out


def log(
    agent: str,
    action: str,
    detail: str = "",
    evidence: Iterable[Evidence | dict] | None = None,
    data: dict[str, Any] | None = None,
) -> AgentLog:
    entry = AgentLog(
        run_id=current_run_id(),
        agent=agent,
        action=action,
        detail=detail,
        evidence=_evidence_dicts(evidence),
        data=data or {},
    )
    with session() as s:
        s.add(entry)
        s.commit()
        s.refresh(entry)
    return entry


def recent(limit: int = 200, agent: str | None = None, run_id: str | None = None) -> list[AgentLog]:
    with session() as s:
        stmt = select(AgentLog).order_by(AgentLog.id.desc()).limit(limit)
        if agent:
            stmt = stmt.where(AgentLog.agent == agent)
        if run_id:
            stmt = stmt.where(AgentLog.run_id == run_id)
        return list(s.exec(stmt).all())
