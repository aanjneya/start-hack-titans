"""Buyer-facing history from the decisions, evidence and trace rows for one selected option."""
from sqlalchemy import or_
from sqlmodel import select

from ..core.models import AgentLog, DecisionCard, InboxMessage, Issue, OutboxMessage
from .planner_outcomes import confirmation_forecasts


def _quote_thread(db, option):
    """Follow the accepted quote back to the inquiry that requested it."""
    quote_id = next((a["quote_inbox_id"] for a in option.get("actions", []) if a.get("quote_inbox_id")), None)
    quote = db.get(InboxMessage, quote_id) if quote_id else None
    mail = db.get(OutboxMessage, quote.outbox_id) if quote and quote.outbox_id else None
    return quote, mail


def _forecast_events(db, rows):
    ids = {row.data["inbox_id"] for row in rows if row.action == "confirmed_delivery" and row.data.get("inbox_id")}
    snapshots = confirmation_forecasts(db, ids)
    events = {}
    for outcomes in snapshots.values():
        for f in outcomes:
            if f["projected_packed"] is None or f["shortfall"] is None:
                detail = f"{f['edition']} is awaiting sufficient evidence for a projection."
            else:
                detail = f"{f['projected_packed']:,} projected by close, {f['shortfall']:,} short."
                if f["change_from_previous"] is not None:
                    detail += f" Change from the preceding projection: {f['change_from_previous']:+,}."
                if f["joint_confirmations"] > 1:
                    detail += f" This update incorporated {f['joint_confirmations']} confirmations together."
            events[f["forecast_id"]] = dict(at=f["recorded_at"], actor="Planner",
                title=f"{f['edition']} forecast recalculated", detail=detail,
                record=dict(type="forecast", id=f["forecast_id"]), trace_id=f["trace_id"], run_id=f["run_id"])
    return list(events.values())


def _event(stamp, actor, title, detail, record_type, record_id, log=None):
    event = dict(at=stamp.isoformat(), actor=actor, title=title, detail=detail,
                 record=dict(type=record_type, id=record_id))
    if log:
        event.update(trace_id=log.id, run_id=log.run_id)
    return event


def decision_history(db, card, index):
    option = card.options[index] if index is not None else {}
    quote, inquiry = _quote_thread(db, option)
    cards = {card.id: card}
    if option.get("followup_for"):
        parent = db.get(DecisionCard, option["followup_for"])
        if parent:
            cards[parent.id] = parent
    elif option.get("exposure"):
        # Draft follow-ups are shown on the review. History includes the selected correspondence.
        children = db.exec(select(DecisionCard).where(
            DecisionCard.options[0]["followup_for"].as_integer() == card.id,
            DecisionCard.decided_at.is_not(None))).all()
        cards.update({child.id: child for child in children})
    own_mail = db.exec(select(OutboxMessage).where(OutboxMessage.decision_id.in_(cards))).all()
    mails = {m.id: m for m in own_mail}
    replies = {m.id: m for m in db.exec(select(InboxMessage).where(InboxMessage.outbox_id.in_(mails)))}
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
    for log in rows:
        if log.action != "error":
            continue
        for key, group in (("inbox", replies), ("outbox", mails), ("decision", cards)):
            record_id = log.data.get(f"{key}_id")
            if record_id in group:
                history.append(_event(log.ts, log.agent, "Agent step needs review",
                                      f"{log.agent} reported an error on {log.data.get('event') or 'this action'}. "
                                      "Open the trace for the recorded error.", key, record_id, log))
                break
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
        expired = traces.get(("decision_id", c.id, "authorization_expired"))
        if expired:
            history.append(_event(expired.ts, "Planner", "Quote expired before dispatch", expired.detail, "decision", c.id, expired))
        changed = traces.get(("decision_id", c.id, "authorization_changed"))
        if changed:
            history.append(_event(changed.ts, "Planner", "Authorization needs new evidence", changed.detail, "decision", c.id, changed))
        withdrawn = traces.get(("decision_id", c.id, "review_withdrawn"))
        if withdrawn:
            history.append(_event(withdrawn.ts, "Planner", "Pending review withdrawn", withdrawn.detail, "decision", c.id, withdrawn))
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
        elif result.get("qualification_correspondence"):
            key, title = "qualification_correspondence", "Supplier response attached to review"
            detail = f"Review #{result[key]['review_id']} · Engineering and Quality assessment pending."
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
