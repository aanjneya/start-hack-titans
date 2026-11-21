"""Decision-linked delivery advice. Ingest owns accepted facts; Chaser owns the mock outbox."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field
from sqlmodel import select

from ..core import trace
from ..core.models import DecisionCard, InboxMessage, Issue, OrderConfirmation, OutboxMessage, Supplier, utcnow
from ..core.store import session
from .ingest import IngestNormalizer


class DeliveryAdvice(BaseModel):
    po_number: str
    po_line: int = Field(ge=1)
    shipment_id: str
    revision: str
    quantity: int = Field(ge=0)
    arrival: AwareDatetime
    supersedes_inbox_id: int | None = None


class SupplierQuote(DeliveryAdvice):
    mode: Literal["expedite", "air", "pickup"]
    incremental_cost_eur: float = Field(ge=0, allow_inf_nan=False)
    valid_until: AwareDatetime


def already_requested(s, actions):
    """A newer version of a card may still describe an already-authorized physical action."""
    if not actions:
        return False
    cards = s.exec(select(DecisionCard).where(DecisionCard.created_by == "planner",
                   DecisionCard.status.in_(["approved", "executed"]))).all()
    return any(c.chosen_option is not None and c.options[c.chosen_option].get("actions") == actions for c in cards)


def mock_outbox(event):
    """Offline transport for Planner requests. A native Chaser's delivery takes precedence."""
    p = event.payload
    if not p.get("decision_id") or not p.get("ask"):
        return
    with session() as s:
        existing = s.exec(select(OutboxMessage).where(OutboxMessage.decision_id == p["decision_id"])).first()
        if existing:
            return
        supplier = s.exec(select(Supplier).where(Supplier.supplier_id == p["supplier_id"])).first()
        msg = OutboxMessage(supplier_id=p["supplier_id"], issue_id=p["issue_id"], decision_id=p["decision_id"],
                            subject="[MOCK] " + p["ask"], body=p["ask"], body_en=p["ask"], language="en",
                            to_email=supplier.contact_email if supplier else "", status="sent", sent_at=utcnow())
        s.add(msg)
        s.commit()
    trace.log("chaser", "mock_dispatch", p["ask"], data={"decision_id": p["decision_id"], "mocked": True})


class PlanningReplyIngest(IngestNormalizer):
    """Use the same Ingest subscription for API and Chaser-originated supplier replies."""

    def on_reply(self, event):
        with session() as s:
            msg = s.get(InboxMessage, event.payload["inbox_id"])
        if not msg or msg.processed:
            return
        pending = msg.result or {}
        delivery = DeliveryAdvice.model_validate(pending["delivery"]) if pending.get("delivery") else None
        quote = SupplierQuote.model_validate(pending["quote"]) if pending.get("quote") else None
        if not ingest_reply(msg, delivery, self, quote=quote):
            super().on_reply(event)


def _text_confirmation(body):
    # Without an explicit positive, unambiguous answer keep the raw message for review.
    if not re.search(r"\bconfirm(?:ed)?\b", body, re.I) or re.search(
            r"\b(?:not|unconfirmed|estimate|tentative|quote|if|pending|cancelled)\b", body, re.I):
        return None
    po_lines = re.findall(r"\bPO\s+(\d+)/(\d+)\b", body, re.I)
    shipments = re.findall(r"\bSHP-[\w-]+", body)
    units = re.findall(r"\b([\d,]+) (?:pcs|units|pieces)\b", body, re.I)
    rev = re.findall(r"\brev(?:ision)?[. ]+([A-Z])\b", body, re.I)
    times = re.findall(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})", body)
    if not all(len(x) == 1 for x in (po_lines, shipments, units, rev, times)):
        return None
    try:
        return DeliveryAdvice(po_number=po_lines[0][0], po_line=int(po_lines[0][1]), shipment_id=shipments[0],
                              revision=rev[0].upper(), quantity=int(units[0].replace(",", "")), arrival=times[0])
    except ValueError:
        return None


def ingest_reply(msg, delivery, agent, *, quote=None):
    """Return False for ordinary replies so the original Ingest handler still handles them."""
    with session() as s:
        out = s.get(OutboxMessage, msg.outbox_id) if msg.outbox_id else None
        card = s.get(DecisionCard, out.decision_id) if out and out.decision_id else None
        issue = s.get(Issue, msg.issue_id) if msg.issue_id else None
        line = None
        if card and card.created_by == "planner" and card.chosen_option is not None:
            actions = card.options[card.chosen_option].get("actions", [])
            line = next((a for a in actions if a.get("type") == "email"), None)
        if delivery is None and quote is None and not line and not (issue and issue.raised_by == "planner"):
            # The generic extractor can fill every matching confirmation. Quarantine ambiguous replies.
            if not issue or not (issue.part_id or issue.po_number):
                return False
            query = select(OrderConfirmation).where(OrderConfirmation.supplier_id == msg.supplier_id)
            if issue.part_id:
                query = query.where(OrderConfirmation.material_id == issue.part_id)
            if issue.po_number:
                query = query.where(OrderConfirmation.po_number == issue.po_number)
            if len(s.exec(query).all()) <= 1:
                return False
        advice = delivery or quote or (_text_confirmation(msg.body) if line else None)
        fact = advice.model_dump(mode="json") if advice else {}
        field = "supplier_quote" if quote else "confirmed_delivery"
        reason = ""
        if not line or not out or msg.supplier_id != out.supplier_id or msg.issue_id not in (None, out.issue_id):
            reason = "A unique approved request, supplier and issue must match."
        elif not fact or any(fact[k] != line.get(k) for k in ("po_number", "po_line", "shipment_id")):
            reason = "Confirm quantity, revision and site arrival against the requested shipment and PO line."
        else:
            confirmations = s.exec(select(OrderConfirmation).where(
                OrderConfirmation.supplier_id == msg.supplier_id, OrderConfirmation.po_number == fact["po_number"],
                OrderConfirmation.po_line == fact["po_line"], OrderConfirmation.material_id == line["part"])).all()
            if len(confirmations) != 1:
                reason = "Order-line evidence is ambiguous."
            elif (confirmations[0].quantity is None or fact["quantity"] > confirmations[0].quantity
                  or (confirmations[0].revision and fact["revision"] != confirmations[0].revision)):
                reason = "Supplier advice conflicts with the confirmed order quantity or revision."
        if not reason:
            prior = []
            for row in s.exec(select(InboxMessage).where(InboxMessage.supplier_id == msg.supplier_id)
                              .order_by(InboxMessage.id.desc())).all():
                old = (row.result or {}).get(field)
                if old and all(old[k] == fact[k] for k in ("po_number", "po_line", "shipment_id")):
                    prior.append((row.id, old))
                    break
            if prior:
                old_id, old = prior[0]
                if all(old.get(k) == v for k, v in fact.items() if k != "supersedes_inbox_id"):
                    reason = f"Duplicate of inbox/{old_id}"
                elif fact.get("supersedes_inbox_id") != old_id:
                    reason = f"Conflicting advice requires an explicit correction to inbox/{old_id}."
        target = s.get(InboxMessage, msg.id)
        target.processed = True
        if reason:
            target.result = {**(target.result or {}), "review": reason}
        else:
            target.issue_id = out.issue_id
            target.result = {field: fact}
            out.status = "replied"
            s.add(out)
        s.add(target)
        s.commit()
    trace.log("ingest", "delivery_review" if reason else field, reason or "Bound supplier advice",
              [{"path": f"inbox/{msg.id}", "locator": "supplier delivery advice"}], data={"inbox_id": msg.id})
    if not reason:
        agent.emit_truth_updated(tables=["inbox"], issue_ids=[out.issue_id])
    return True
