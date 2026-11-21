"""FastAPI app. Run from the repo root:  uvicorn backend.app.main:app --reload --port 8000

Every response uses the ApiResponse envelope from API_CONTRACT.ts: {ok, data, error?, mocked}.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlmodel import select

from .. import system
from ..core import events, policy, trace
from ..core.config import get_settings
from ..core.models import (
    Conflict, DecisionCard, DropRecord, Forecast, Issue, OutboxMessage, RiskScore, utcnow,
)
from ..core.store import query, session


def ok(data: Any) -> dict:
    return {"ok": True, "data": data, "mocked": get_settings().mock_mode}


@asynccontextmanager
async def lifespan(_: FastAPI):
    system.bus()  # creates tables + registers agents
    yield


app = FastAPI(title="start-hack-titans backend", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _rows(model, *, order=None, where=None, limit: int = 500) -> list[dict]:
    with session() as s:
        stmt = select(model)
        if where is not None:
            stmt = stmt.where(where)
        if order is not None:
            stmt = stmt.order_by(order)
        return [r.model_dump(mode="json") for r in s.exec(stmt.limit(limit)).all()]


@app.get("/api/health")
def health() -> dict:
    s = get_settings()
    return ok({
        "status": "ok",
        "mockMode": s.mock_mode,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dataRoot": str(s.data_root),
        "agents": system.agent_names(),
    })


class RunRequest(BaseModel):
    added: Optional[list[str]] = None
    changed: list[str] = []
    removed: list[str] = []
    label: str = ""
    reset: bool = False


@app.post("/api/run")
def run(req: RunRequest | None = None) -> dict:
    """No body: reprocess the whole current data folder. With file lists: process just that drop."""
    req = req or RunRequest()
    if req.reset:
        system.reset()
    if req.added is None and not req.changed and not req.removed:
        return ok(system.run_full(label=req.label or "full run"))
    return ok(system.process_drop(added=req.added or [], changed=req.changed, removed=req.removed,
                                  label=req.label, mode="manual"))


@app.post("/api/replay")
def replay(upto: Optional[int] = None) -> dict:
    from ..core.replay import replay as do_replay

    return ok(do_replay(upto=upto))


@app.get("/api/drops")
def drops() -> dict:
    return ok(_rows(DropRecord, order=DropRecord.id.desc()))


@app.get("/api/issues")
def issues(status: Optional[str] = None) -> dict:
    where = Issue.status == status if status else None
    return ok(_rows(Issue, order=Issue.severity.desc(), where=where))


@app.get("/api/conflicts")
def conflicts() -> dict:
    return ok(_rows(Conflict, order=Conflict.id.desc()))


@app.get("/api/forecast")
def forecast() -> dict:
    return ok(_rows(Forecast, order=Forecast.id.desc(), limit=50))


@app.get("/api/decisions")
def decisions(status: Optional[str] = None) -> dict:
    where = DecisionCard.status == status if status else None
    return ok(_rows(DecisionCard, order=DecisionCard.id.desc(), where=where))


class DecideRequest(BaseModel):
    by: str = "unknown"
    option: Optional[int] = None  # index into options; default = recommended


def _decide(decision_id: int, status: str, req: DecideRequest) -> DecisionCard:
    with session() as s:
        card = s.get(DecisionCard, decision_id)
        if card is None:
            raise HTTPException(404, f"decision {decision_id} not found")
        if card.status not in {"proposed", "outdated"}:
            raise HTTPException(409, f"decision {decision_id} is already {card.status}")
        card.status, card.decided_by, card.decided_at = status, req.by, utcnow()
        card.chosen_option = req.option if req.option is not None else card.recommended
        s.add(card)
        s.commit()
        s.refresh(card)
    return card


@app.post("/api/decisions/{decision_id}/approve")
def approve(decision_id: int, req: DecideRequest | None = None) -> dict:
    req = req or DecideRequest()
    card = _decide(decision_id, "approved", req)
    trace.log("human", "approve", f"decision {decision_id} by {req.by}", data={"decision_id": decision_id})
    result = system.emit(events.DECISION_APPROVED,
                         {"decision_id": decision_id, "option": card.chosen_option, "by": req.by})
    return ok({"decision": card.model_dump(mode="json"), **result})


@app.post("/api/decisions/{decision_id}/reject")
def reject(decision_id: int, req: DecideRequest | None = None) -> dict:
    req = req or DecideRequest()
    card = _decide(decision_id, "rejected", req)
    trace.log("human", "reject", f"decision {decision_id} by {req.by}", data={"decision_id": decision_id})
    result = system.emit(events.DECISION_REJECTED, {"decision_id": decision_id, "by": req.by})
    return ok({"decision": card.model_dump(mode="json"), **result})


@app.get("/api/trace")
def get_trace(limit: int = 200, agent: Optional[str] = None, run_id: Optional[str] = None) -> dict:
    return ok([e.model_dump(mode="json") for e in trace.recent(limit=limit, agent=agent, run_id=run_id)])


@app.get("/api/risk")
def risk() -> dict:
    return ok(_rows(RiskScore, order=RiskScore.grade.desc()))


@app.get("/api/outbox")
def outbox() -> dict:
    return ok(_rows(OutboxMessage, order=OutboxMessage.id.desc()))


@app.get("/api/setup")
def customer_setup() -> dict:
    s = policy.setup()
    return ok({
        "customer": s.customer,
        "autonomy": {"mode": s.autonomy_mode, "auto_execute_below_eur": s.auto_execute_below_eur},
        "approval_matrix": [{"below_eur": r.below_eur, "approvers": list(r.approvers)} for r in s.approval_matrix],
    })


@app.get("/api/part-journey")
def part_journey(material_id: Optional[str] = None, limit: int = 500) -> dict:
    sql = "SELECT * FROM part_journey"
    params: dict[str, Any] = {"limit": limit}
    if material_id:
        sql += " WHERE material_id = :material_id"
        params["material_id"] = material_id
    return ok(query(sql + " LIMIT :limit", **params))
