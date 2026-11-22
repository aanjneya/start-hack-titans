"""Buyer-facing history from the decisions, evidence and trace rows for one selected option."""
from sqlalchemy import or_
from sqlmodel import select

from ..core.models import AgentLog, DecisionCard, Forecast, InboxMessage, Issue, OutboxMessage


def _quote_thread(db, option):
    """Follow the accepted quote back to the inquiry that requested it."""
    quote_id = next((a["quote_inbox_id"] for a in option.get("actions", []) if a.get("quote_inbox_id")), None)
    quote = db.get(InboxMessage, quote_id) if quote_id else None
    mail = db.get(OutboxMessage, quote.outbox_id) if quote and quote.outbox_id else None
    return quote, mail


def _forecast_events(db, rows):
    """A forecast belongs here only when the reply run recalculated it and the snapshot cites that reply."""
    accepted = {}
    for row in rows:
        if row.action == "confirmed_delivery" and row.run_id:
            accepted.setdefault(row.run_id, set()).add(f"inbox/{row.data['inbox_id']}")
    if not accepted:
        return []
    result = []
    logs = db.exec(select(AgentLog).where(AgentLog.agent == "planner", AgentLog.action == "forecast",
                                         AgentLog.run_id.in_(accepted))).all()
    for log in logs:
        for forecast in db.exec(select(Forecast).where(Forecast.id.in_(log.data.get("forecast_ids", [])))):
            paths = {e.get("path") for item in forecast.details.get("inbound", []) for e in item.get("evidence", [])}
            if not paths.intersection(accepted[log.run_id]):
                continue
            if forecast.projected_packed is None or forecast.shortfall is None:
                detail = f"{forecast.edition} is awaiting sufficient evidence for a projection."
            else:
                previous = db.exec(select(Forecast).where(Forecast.product_id == forecast.product_id,
                                   Forecast.id < forecast.id).order_by(Forecast.id.desc()).limit(1)).first()
                detail = f"{forecast.projected_packed:,} projected by close, {forecast.shortfall:,} short."
                if previous and previous.projected_packed is not None:
                    detail += f" Change from the preceding projection: {forecast.projected_packed - previous.projected_packed:+,}."
            result.append(_event(log.ts, "Planner", f"{forecast.edition} forecast recalculated", detail,
                                 "forecast", forecast.id, log))
    return result


def _event(stamp, actor, title, detail, record_type, record_id, log=None):
    event = dict(at=stamp.isoformat(), actor=actor, title=title, detail=detail,
                 record=dict(type=record_type, id=record_id))
    if log:
        event.update(trace_id=log.id, run_id=log.run_id)
    return event


def decision_history(db, card, index):
    option = card.options[index] if index is not None else {}
    quote, inquiry = _quote_thread(db, option)
    own_mail = db.exec(select(OutboxMessage).where(OutboxMessage.decision_id == card.id)).all()
    mails = {m.id: m for m in own_mail}
    replies = {m.id: m for m in db.exec(select(InboxMessage).where(InboxMessage.outbox_id.in_(mails)))}
    cards = {card.id: card}
    if quote and inquiry:
        mails[inquiry.id], replies[quote.id] = inquiry, quote
        prior = db.get(DecisionCard, inquiry.decision_id) if inquiry.decision_id else None
        if prior:
            cards[prior.id] = prior
    issue_ids = {issue_id for c in cards.values() for issue_id in c.issue_ids}
    findings = {i.id: i for i in db.exec(select(Issue).where(Issue.id.in_(issue_ids)))}
    reviews = []
    if card.chosen_option == index and any(a.get("type") == "human_review" for a in option.get("actions", [])):
        reviews = [i for i in db.exec(select(Issue).where(Issue.type == "recovery_review", Issue.raised_by == "planner"))
                   if any(e.get("path") == f"decisions/{card.id}" for e in i.evidence)]
    issue_ids.update(i.id for i in reviews)
    rows = db.exec(select(AgentLog).where(or_(
        AgentLog.data["decision_id"].as_integer().in_(cards),
        AgentLog.data["outbox_id"].as_integer().in_(mails),
        AgentLog.data["inbox_id"].as_integer().in_(replies),
        (AgentLog.action == "raise_issue") & AgentLog.data["issue_id"].as_integer().in_(issue_ids),
    )).order_by(AgentLog.id)).all()
    # Use the first attributable record for each milestone, even when a handler was retried.
    traces = {}
    for row in rows:
        for key in ("issue_id", "decision_id", "outbox_id", "inbox_id"):
            if row.data.get(key):
                traces.setdefault((key, row.data[key], row.action), row)

    history = []
    if (option.get("exposure") or {}).get("last_grade_change"):
        supplier = next((a["supplier_id"] for a in option.get("actions", []) if a.get("supplier_id")), None)
        rating = db.exec(select(AgentLog).where(AgentLog.agent == "risk", AgentLog.action == "grade_changed",
                         AgentLog.data["entity_type"].as_string() == "supplier",
                         AgentLog.data["entity_id"].as_string() == supplier, AgentLog.ts <= card.created_at)
                         .order_by(AgentLog.id.desc()).limit(1)).first()
        if rating:
            history.append(_event(rating.ts, "Risk Rater", "Supplier grade changed", rating.detail,
                                  "decision", card.id, rating))
    for issue in findings.values():
        log = traces.get(("issue_id", issue.id, "raise_issue"))
        history.append(_event(issue.created_at, issue.raised_by, "Finding opened",
                              log.detail if log else issue.summary, "issue", issue.id, log))
    for c in cards.values():
        proposed = traces.get(("decision_id", c.id, "decision_created"))
        history.append(_event(c.created_at, c.created_by, "Decision proposed", c.summary, "decision", c.id, proposed))
        if c.decided_at:
            declined = c.status == "rejected"
            decision = traces.get(("decision_id", c.id, "reject" if declined else "approve"))
            chosen = c.options[c.chosen_option] if c.chosen_option is not None else {}
            history.append(_event(c.decided_at, c.decided_by, "Decision declined" if declined else "Action authorized",
                                  chosen.get("label") or c.title, "decision", c.id, decision))
    for review in reviews:
        history.append(_event(review.created_at, "Planner", "Internal review opened",
                              f"{card.approver} · {review.impact.get('note') or review.summary}", "issue", review.id,
                              traces.get(("issue_id", review.id, "raise_issue"))))
    for mail in mails.values():
        lifecycle = {"mock_send": "Supplier request sent in simulation", "send_email": "Supplier request sent",
                     "draft_email": "Supplier request drafted", "error": "Supplier dispatch failed"}
        log = next((traces[("outbox_id", mail.id, a)] for a in lifecycle if ("outbox_id", mail.id, a) in traces), None)
        title = lifecycle[log.action] if log else f"Supplier request recorded · {mail.status}"
        history.append(_event(mail.sent_at or mail.created_at, "Chaser", title, mail.subject, "outbox", mail.id, log))
    for reply in replies.values():
        result = reply.result or {}
        if result.get("supplier_quote") or result.get("confirmed_delivery"):
            quoted = bool(result.get("supplier_quote"))
            key = "supplier_quote" if quoted else "confirmed_delivery"
            fact = result[key]
            title = "Supplier quote matched" if quoted else "Delivery confirmation matched"
            detail = f"PO {fact['po_number']}/{fact['po_line']} · {fact['shipment_id']} · {fact['quantity']:,} units."
            if quoted:
                detail += f" Offered fee EUR {fact['incremental_cost_eur']:,.2f}. Approval is a separate step."
        elif result.get("review"):
            key, detail = "delivery_review", result["review"]
            title = "Duplicate reply retained" if detail.startswith("Duplicate") else "Reply held for review"
        else:
            key, title, detail = "reply_received", "Supplier reply received", reply.subject
        log = traces.get(("inbox_id", reply.id, key))
        history.append(_event(log.ts if log else reply.received_at, "Ingest" if key != "reply_received" else "Inbox",
                              title, detail, "inbox", reply.id, log))
    history.extend(_forecast_events(db, rows))
    history.sort(key=lambda e: e["at"])
    return dict(decision_id=card.id, events=history)
