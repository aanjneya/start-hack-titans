"""Connect a confirmed reply to the first forecast that actually used its evidence."""
from collections import defaultdict

from sqlmodel import select

from ..core.models import AgentLog, Forecast


def confirmation_forecasts(db, inbox_ids):
    if not inbox_ids:
        return {}
    inbox_field = AgentLog.data["inbox_id"].as_integer()
    accepted = select(AgentLog).where(AgentLog.agent == "ingest", AgentLog.action == "confirmed_delivery")
    run_ids = db.exec(select(AgentLog.run_id).where(AgentLog.agent == "ingest", AgentLog.action == "confirmed_delivery",
                      inbox_field.in_(inbox_ids)).distinct()).all()
    if not run_ids:
        return {}
    # Include sibling confirmations processed in the same run. A joint change cannot be credited twice.
    confirmations = defaultdict(dict)
    for log in db.exec(accepted.where(AgentLog.run_id.in_(run_ids)).order_by(AgentLog.id)):
        confirmations[log.run_id][f"inbox/{log.data['inbox_id']}"] = (log.data["inbox_id"], log.id)
    projections = db.exec(select(AgentLog).where(AgentLog.agent == "planner", AgentLog.action == "forecast",
                          AgentLog.run_id.in_(run_ids)).order_by(AgentLog.id)).all()
    results = defaultdict(list)
    seen = set()
    for log in projections:
        for f in db.exec(select(Forecast).where(Forecast.id.in_(log.data.get("forecast_ids", []))).order_by(Forecast.id)):
            matches = {}
            for line in f.details.get("inbound", []):
                for evidence in line.get("evidence", []):
                    hit = confirmations[log.run_id].get(evidence.get("path"))
                    if hit and hit[1] < log.id and (hit[0], f.product_id) not in seen:
                        matches[hit[0]] = line
            seen.update((i, f.product_id) for i in matches)
            if not matches.keys() & inbox_ids:
                continue
            prior = db.exec(select(Forecast.projected_packed).where(Forecast.product_id == f.product_id,
                            Forecast.id < f.id).order_by(Forecast.id.desc()).limit(1)).first()
            delta = f.projected_packed - prior if prior is not None and f.projected_packed is not None else None
            for i in matches.keys() & inbox_ids:
                results[i].append(dict(product_id=f.product_id, edition=f.edition, forecast_id=f.id,
                    projected_packed=f.projected_packed, shortfall=f.shortfall, change_from_previous=delta,
                    joint_confirmations=len(matches), dock_released=matches[i].get("dock_released"),
                    recorded_at=log.ts.isoformat(), trace_id=log.id, run_id=log.run_id))
    return results
