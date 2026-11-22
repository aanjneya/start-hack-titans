"""FastAPI app. Run from the repo root:  uvicorn backend.app.main:app --reload --port 8000

Every response uses the ApiResponse envelope from API_CONTRACT.ts: {ok, data, error?, mocked}.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel
from sqlalchemy import or_, text
from sqlmodel import func, select

from ..agents.planner_history import decision_history
from ..agents.planner_queue import buyer_queue, current_option, selection_version
from ..agents.planner_delivery import DeliveryAdvice, SupplierQuote, already_requested

from .. import system
from ..core import events, policy, trace
from ..core.replay import ReplayUnavailable
from ..core.config import get_settings
from ..core.models import (
    AgentLog, Conflict, DecisionCard, DropRecord, FieldChange, Forecast, InboxMessage, Issue, OutboxMessage, RiskScore,
    utcnow,
)
from ..core.store import query, session


def ok(data: Any) -> dict:
    return {"ok": True, "data": data, "mocked": get_settings().mock_mode}


def _watch_loop(stop) -> None:
    minutes = get_settings().watch_interval_min
    while not stop.wait(minutes * 60):
        try:
            system.watch_once()
        except Exception as exc:  # keep the timer alive
            trace.log("watcher", "error", f"timer: {type(exc).__name__}: {exc}")


def _risk_loop(stop) -> None:
    while not stop.wait(get_settings().risk_refresh_interval_min * 60):
        try:
            system.refresh_risk("risk_timer")
        except Exception as exc:
            trace.log("risk", "error", f"refresh timer: {type(exc).__name__}: {exc}")


def _chaser():
    system.bus()
    return next(a for a in system._agents if a.name == "chaser")


def _chaser_poll_loop(stop) -> None:
    minutes = get_settings().chaser_poll_min
    while not stop.wait(minutes * 60):
        try:
            with system._lock:
                _chaser().poll_inbox()
                system.bus().run_until_idle()
        except Exception as exc:  # keep the timer alive
            trace.log("chaser", "error", f"poll timer: {type(exc).__name__}: {exc}")


@asynccontextmanager
async def lifespan(_: FastAPI):
    import threading

    with system._lock:
        trace.new_run_id()
        system.bus().run_until_idle()  # resume committed decisions after every agent has subscribed
    stop = threading.Event()
    s = get_settings()
    if s.watch_interval_min > 0 and not s.mock_mode:
        threading.Thread(target=_watch_loop, args=(stop,), daemon=True, name="watcher").start()
    if s.risk_refresh_interval_min > 0 and not s.mock_mode:
        threading.Thread(target=_risk_loop, args=(stop,), daemon=True, name="risk-refresh").start()
    if s.chaser_poll_min > 0 and s.chaser_send and not s.mock_mode:
        threading.Thread(target=_chaser_poll_loop, args=(stop,), daemon=True, name="chaser-poll").start()
    yield
    stop.set()


app = FastAPI(title="start-hack-titans backend", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1200, compresslevel=4)
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
    with session() as db:
        last = db.exec(select(DropRecord).order_by(DropRecord.id.desc())).first()
        activity = db.exec(select(AgentLog.id, AgentLog.ts).order_by(AgentLog.id.desc()).limit(1)).first()
        reading = db.exec(select(AgentLog).where(AgentLog.agent == "risk",
            AgentLog.action.in_(["risk_rated", "risk_refresh_incomplete", "signal_check"])).order_by(AgentLog.id.desc()).limit(1)).first()
    version = f"{activity.id}/{activity.ts.isoformat()}" if activity else "empty"
    if last:
        version += f"/{last.run_id}/{last.status}"
    return ok({
        "activity_version": version,
        "lastRiskCheck": (dict(at=reading.ts, run_id=reading.run_id, trigger=reading.data.get("trigger"),
            grades_changed=reading.data.get("grade_changes"), suppliers=reading.data.get("suppliers"),
            sources=reading.data.get("external_signals", {}), unavailable=reading.data.get("unavailable", {}),
            failures=reading.data.get("failures", []))
            if reading else None),
        "replayEnabled": bool(s.replay_db_path and s.mock_mode),
        "lastDrop": (dict(status=last.status, mode=last.mode, label=last.label, run_id=last.run_id, failures=last.failures or []) if last else None),
        "status": "degraded" if (last and last.status == "partial") or (reading and
                                 (reading.data.get("unavailable") or reading.data.get("failures"))) else "ok",
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
        raise HTTPException(409, "Use a new dedicated replay database to start a fresh demo.")
    if req.added is None and not req.changed and not req.removed:
        return ok(system.run_full(label=req.label or "full run"))
    return ok(system.process_drop(added=req.added or [], changed=req.changed, removed=req.removed,
                                  label=req.label, mode="manual"))


@app.post("/api/replay")
def replay(upto: Optional[int] = None) -> dict:
    from ..core.replay import replay as do_replay

    try:
        return ok(do_replay(upto=upto))
    except ReplayUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/replay/step")
def replay_step() -> dict:
    try:
        return ok(system.replay_step())
    except ReplayUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/watch/check")
def watch_check() -> dict:
    """Live portal check now (Watcher). Does nothing in MOCK mode."""
    return ok(system.watch_once())


@app.post("/api/chaser/poll")
def chaser_poll() -> dict:
    """Read new supplier replies from the shared mailbox now (only on the CHASER_SEND instance)."""
    with system._lock:
        result = _chaser().poll_inbox()
        result["steps"] = system.bus().run_until_idle()
    return ok(result)


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
def forecast(current: bool = False) -> dict:
    if current:
        newest = select(func.max(Forecast.id)).group_by(Forecast.product_id)
        with session() as db:
            summaries = []
            for f in db.exec(select(Forecast).where(Forecast.id.in_(newest)).order_by(Forecast.id.desc())):
                row = f.model_dump(mode="json", exclude={"details"})
                row["details"] = {k: f.details[k] for k in ("as_of", "working_hours", "limiting_part_name") if k in f.details}
                summaries.append(row)
            return ok(summaries)
    return ok(_rows(Forecast, order=Forecast.id.desc(), limit=50))


@app.get("/api/decisions")
def decisions(status: Optional[str] = None, for_buyer: bool = False) -> dict:
    where = DecisionCard.status == status if status else None
    if for_buyer:
        relevant = or_(DecisionCard.status != "outdated", DecisionCard.decided_at.is_not(None),
                       DecisionCard.options[0]["followup_for"].as_integer().is_not(None))
        where = relevant if where is None else where & relevant
        with session() as db:
            rows = db.exec(select(DecisionCard).where(where).order_by(DecisionCard.id.desc())).all()
            return ok([r.model_dump(mode="json") for r in rows])
    return ok(_rows(DecisionCard, order=DecisionCard.id.desc(), where=where))


@app.get("/api/issues/{issue_id}")
def get_issue(issue_id: int) -> dict:
    with session() as s:
        issue = s.get(Issue, issue_id)
        if not issue:
            raise HTTPException(404, "Finding not found.")
        return ok(issue.model_dump(mode="json"))


@app.get("/api/decisions/{decision_id}")
def get_decision(decision_id: int) -> dict:
    with session() as s:
        card = s.get(DecisionCard, decision_id)
        if not card:
            raise HTTPException(404, "Decision not found.")
        return ok(card.model_dump(mode="json"))


def _decision_selection(db, decision_id, option):
    card = db.get(DecisionCard, decision_id)
    if not card:
        raise HTTPException(404, "Decision not found.")
    if option is None:
        return card, card.chosen_option if card.chosen_option is not None else card.recommended
    if not 0 <= option < len(card.options):
        raise HTTPException(422, "Choose a valid option.")
    if card.chosen_option is not None and option != card.chosen_option:
        raise HTTPException(409, "This decision authorized another option.")
    return card, option


@app.get("/api/decisions/{decision_id}/history")
def get_decision_history(decision_id: int, option: Optional[int] = None) -> dict:
    with session() as db:
        card, index = _decision_selection(db, decision_id, option)
        return ok(decision_history(db, card, index))


@app.get("/api/decisions/{decision_id}/evidence/{index}")
def get_decision_source(decision_id: int, index: int) -> dict:
    from ..agents.planner_evidence import read_source

    with session() as db:
        card = db.get(DecisionCard, decision_id)
        if not card or not 0 <= index < len(card.evidence):
            raise HTTPException(404, "Citation not found on this decision.")
        return ok(read_source(db, card.evidence[index]))


@app.get("/api/decisions/{decision_id}/sample-window")
def get_sample_window(decision_id: int, requested_on: date, option: Optional[int] = None) -> dict:
    from ..agents.planner_regulatory import project_sample_date

    with session() as db:
        card, index = _decision_selection(db, decision_id, option)
        window = (card.options[index].get("exposure") or {}).get("sample_window") if index is not None else None
    if not window:
        raise HTTPException(409, "This option has no sourced sample interval and dated delivery to compare.")
    try:
        return ok(project_sample_date(window, requested_on))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@app.get("/api/buyer-queue")
def get_buyer_queue() -> dict:
    with session() as s:
        return ok(buyer_queue(s))


class DecideRequest(BaseModel):
    version: Optional[str] = None
    by: str = "unknown"
    approvals: list[str] = []
    option: Optional[int] = None  # index into options; default = recommended


class ChangedDecision(Exception):
    """Release the approval transaction before refreshing the Planner's stored cards."""


def _decide(decision_id: int, status: str, req: DecideRequest) -> DecisionCard:
    with session() as s:
        s.execute(text("BEGIN IMMEDIATE"))  # serialize status check and claim on SQLite
        card = s.get(DecisionCard, decision_id)
        if card is None:
            raise HTTPException(404, f"decision {decision_id} not found")
        if card.status != "proposed":
            raise HTTPException(409, f"decision {decision_id} is already {card.status}")
        selected = req.option if req.option is not None else card.recommended
        if selected is None or not 0 <= selected < len(card.options):
            raise HTTPException(422, "Choose a valid option.")
        option = card.options[selected]
        if req.version is not None and req.version != selection_version(card, selected, option):
            raise HTTPException(409, "The option changed. Refresh the buyer queue.")
        if status == "rejected" and selected != card.recommended:
            raise HTTPException(422, "Decline the recommended option to reject this decision.")
        if (card.created_by == "planner" and (option.get("product_id") or option.get("horizon") == "future")
                and current_option(s, card, selected) is None):
            raise ChangedDecision
        if any(not a.get("person") for a in option.get("assignments", [])):
            raise HTTPException(409, "A named signer must be assigned from the effective org chart.")
        if status == "rejected" and option.get("approvers"):
            if req.by.strip().casefold() not in {name.casefold() for name in option["approvers"]}:
                raise HTTPException(403, "Only an assigned signer can decline this decision.")
        if status == "approved":
            if not option.get("available", True):
                raise HTTPException(409, "The evidence does not support executing this option.")
            if card.created_by == "planner" and already_requested(s, option, card):
                raise HTTPException(409, "This action was already authorized on a related decision.")
            if option.get("approvers"):
                required = {name.strip().casefold() for name in option["approvers"]}
                signers = {name.strip().casefold() for name in [req.by, *req.approvals]}
                if req.by.strip().casefold() not in required or signers != required:
                    raise HTTPException(403, "The chosen option requires " + " + ".join(option["approvers"]))
                card.approver = " + ".join(option["approvers"])
                card.needs_approval = option.get("needs_approval", True)
        card.status, card.decided_by, card.decided_at = status, " + ".join([req.by, *req.approvals]), utcnow()
        card.chosen_option = selected if status == "approved" else None
        s.add(card)
        s.commit()
        s.refresh(card)
    return card


def _current_decision(decision_id: int, status: str, req: DecideRequest):
    try:
        return _decide(decision_id, status, req)
    except ChangedDecision:
        system.emit(events.PLANNER_RECHECK, {}, source="approval_recheck")
        raise HTTPException(409, "The evidence or approval route changed. Review the refreshed buyer queue.") from None


@app.post("/api/decisions/{decision_id}/approve")
def approve(decision_id: int, req: DecideRequest | None = None) -> dict:
    req = req or DecideRequest()
    card = _current_decision(decision_id, "approved", req)
    trace.log("human", "approve", f"decision {decision_id} by {req.by}", data={"decision_id": decision_id})
    result = system.emit(events.DECISION_APPROVED,
                         {"decision_id": decision_id, "option": card.chosen_option, "by": req.by})
    with session() as s:
        current = s.get(DecisionCard, decision_id)
        return ok({"decision": current.model_dump(mode="json"), **result})


@app.post("/api/decisions/{decision_id}/reject")
def reject(decision_id: int, req: DecideRequest | None = None) -> dict:
    req = req or DecideRequest()
    card = _current_decision(decision_id, "rejected", req)
    trace.log("human", "reject", f"decision {decision_id} by {req.by}", data={"decision_id": decision_id})
    result = system.emit(events.DECISION_REJECTED, {"decision_id": decision_id, "by": req.by})
    return ok({"decision": card.model_dump(mode="json"), **result})


@app.get("/api/trace")
def get_trace(limit: int = 200, agent: Optional[str] = None, run_id: Optional[str] = None,
              trace_id: Optional[int] = None) -> dict:
    if trace_id is not None:
        with session() as s:
            entry = s.get(AgentLog, trace_id)
            return ok([entry.model_dump(mode="json")] if entry and (not agent or entry.agent == agent)
                      and (not run_id or entry.run_id == run_id) else [])
    return ok([e.model_dump(mode="json") for e in trace.recent(limit=limit, agent=agent, run_id=run_id)])


@app.get("/api/buyer-activity")
def buyer_activity() -> dict:
    with session() as db:
        columns = (AgentLog.id, AgentLog.run_id, AgentLog.ts, AgentLog.agent, AgentLog.action, AgentLog.detail)
        fields = ("decision_id", "inbox_id", "outbox_id", "event")
        refs = [AgentLog.data[k].as_integer().label(k) for k in fields[:3]]
        refs.append(AgentLog.data["event"].as_string().label("event"))
        rows = db.exec(select(*columns, *refs).order_by(AgentLog.id.desc()).limit(400)).mappings()
        return ok([dict(id=r["id"], run_id=r["run_id"], ts=r["ts"], agent=r["agent"], action=r["action"],
                        detail=r["detail"], data={k: r[k] for k in fields if r[k] is not None}) for r in rows])


@app.get("/api/risk")
def risk(current: bool = False) -> dict:
    if current:
        latest_ids = select(func.max(RiskScore.id)).group_by(RiskScore.entity_type, RiskScore.entity_id)
        with session() as db:
            rows = db.exec(select(RiskScore).where(RiskScore.id.in_(latest_ids)).order_by(
                           RiskScore.grade.desc(), RiskScore.entity_type, RiskScore.entity_id)).all()
            return ok([r.model_dump(mode="json") for r in rows])
    return ok(_rows(RiskScore, order=RiskScore.grade.desc()))


@app.post("/api/risk/refresh")
def risk_refresh() -> dict:
    return ok(system.refresh_risk())


@app.get("/api/outbox")
def outbox() -> dict:
    return ok(_rows(OutboxMessage, order=OutboxMessage.id.desc()))


@app.get("/api/changes")
def changes(table: Optional[str] = None, limit: int = 500) -> dict:
    """Change log: every filled/corrected value (old -> new, source, agent, reason)."""
    where = FieldChange.table_name == table if table else None
    return ok(_rows(FieldChange, order=FieldChange.id.desc(), where=where, limit=limit))


@app.get("/api/inbox")
def inbox(with_forecast: bool = False) -> dict:
    rows = _rows(InboxMessage, order=InboxMessage.id.desc())
    if with_forecast:
        from ..agents.planner_outcomes import confirmation_forecasts

        with session() as db:
            outcomes = confirmation_forecasts(db, {r["id"] for r in rows if r["result"].get("confirmed_delivery")})
        for r in rows:
            if r["id"] in outcomes:
                r["forecast_at_confirmation"] = outcomes[r["id"]]
    return ok(rows)


class ReplyRequest(BaseModel):
    delivery: DeliveryAdvice | None = None
    quote: SupplierQuote | None = None
    supplier_id: str
    body: str
    issue_id: Optional[int] = None
    outbox_id: Optional[int] = None
    from_email: str = ""
    subject: str = ""
    language: str = ""


@app.post("/api/inbox")
def post_reply(req: ReplyRequest) -> dict:
    """Feed a supplier reply into the loop (demo / manual test): inbox -> Ingest -> Reconciler."""
    if req.delivery and req.quote:
        raise HTTPException(422, "Submit the quote and the confirmed delivery as separate replies.")
    return ok(system.receive_reply(**req.model_dump(exclude={"delivery", "quote"}),
                                   delivery=req.delivery, quote=req.quote, source="api"))


@app.get("/api/setup")
def customer_setup() -> dict:
    s = policy.setup()
    return ok({
        "customer": s.customer, "demo_buyer": s.demo_buyer, "site": s.site,
        "timezone": s.timezone, "currency": s.currency,
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


# The clean data the Watcher + Ingest built (read-only; the UI shows it with SAP field names).
_TRUTH_TABLES = {
    "purchase_orders": "PurchaseOrder", "order_confirmations": "OrderConfirmation", "goods_receipts": "GoodsReceipt",
    "stock": "StockLot", "shipments": "Shipment", "materials": "Material", "bom": "BomLine", "products": "Product",
    "suppliers": "Supplier", "supplier_part_map": "SupplierPartMap", "engineering_changes": "EngineeringChange",
    "packout": "Packout", "launch_status": "LaunchStatus",
}


def _truth_model(table: str):
    from ..core import models

    if table not in _TRUTH_TABLES:
        raise HTTPException(404, f"unknown table {table!r}; choose one of {sorted(_TRUTH_TABLES)}")
    return getattr(models, _TRUTH_TABLES[table])


@app.get("/api/truth")
def truth_tables() -> dict:
    """Data tables with their row counts."""
    from sqlalchemy import func

    with session() as s:
        return ok([{"table": t, "rows": s.exec(select(func.count()).select_from(_truth_model(t))).one()}
                   for t in _TRUTH_TABLES])


@app.get("/api/truth/{table}")
def truth_rows(table: str, limit: int = 1000, q: Optional[str] = None) -> dict:
    """Rows of one data table (each with its source file). q = text filter over all fields."""
    model = _truth_model(table)
    rows = _rows(model, order=model.id, limit=5000)
    if q:
        needle = q.casefold()
        rows = [r for r in rows if any(needle in str(v).casefold() for v in r.values())]
    rows = rows[:max(1, min(limit, 5000))]
    # `source` is where a row first came from; also say which file changed it last (evidence for the shown values)
    with session() as s:
        last = {c.row_id: c.source for c in s.exec(select(FieldChange).where(FieldChange.table_name == table)
                                                   .order_by(FieldChange.id)).all()}
    for r in rows:
        r["last_source"] = last.get(r.get("id"), "")
    return ok(rows)
