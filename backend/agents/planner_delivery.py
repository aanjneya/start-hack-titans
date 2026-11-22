"""Decision-linked delivery advice. Ingest owns accepted facts; Chaser owns the outbox."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field
from sqlmodel import select

from ..core import trace
from ..core.models import DecisionCard, Document, InboxMessage, Issue, OrderConfirmation, OutboxMessage, Shipment
from ..core.store import session
from .base import issue_fingerprint
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


def review_key(review):
    key = f"{review['product_id']}/{review['part']}"
    if review.get("reference"):
        key += f"/{review['reference']}"
    if review.get("po_line"):
        key += f"/{review['po_number']}/{review['po_line']}"
    return key


def already_requested(s, option, card=None):
    """A newer version of a card may still describe an already-authorized physical action."""
    from .planner_queue import authorization_key

    actions = option.get("actions", [])
    if not actions:
        return False
    if actions[0].get("type") == "human_review":
        review = actions[0]
        fingerprint = issue_fingerprint("recovery_review", review_key(review))
        if s.exec(select(Issue.id).where(Issue.fingerprint == fingerprint, Issue.raised_by == "planner",
                                         Issue.status != "resolved")).first() is not None:
            return True
    cards = s.exec(select(DecisionCard).where(DecisionCard.created_by == "planner",
                   DecisionCard.status.in_(["approved", "executed"]))).all()
    key = authorization_key(option, card)
    return any(c.chosen_option is not None and (
        authorization_key(c.options[c.chosen_option], c) == key if key else c.options[c.chosen_option].get("actions") == actions
    ) for c in cards)


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


def _delivery_sites(s, supplier, shipment_id):
    """A supplier naming a place must identify the destination in this customer's own site master."""
    sites = {site for site in s.exec(select(Shipment.dest_site).where(
        Shipment.supplier_id == supplier, Shipment.shipment_id == shipment_id)) if site}
    if len(sites) != 1:
        return set()
    site = sites.pop()
    names = {}
    for doc in s.exec(select(Document).where(Document.kind == "table")):
        for row in doc.extracted.get("rows", []):
            if code := row.get("site_id"):
                for name in (row.get("name"), row.get("city")):
                    if name:
                        names.setdefault(name.casefold(), set()).add(code)
    return {site} | {name for name, codes in names.items() if codes == {site}}


def _text_confirmation(body, shipment_ids, sites=()):
    # Without an explicit positive, unambiguous answer keep the raw message for review.
    body = re.split(r"(?im)^\s*(?:>|On .+wrote:|-----Original Message-----)", body, maxsplit=1)[0]
    if "?" in body or re.search(r"\b(?:not|cannot|unable|unconfirmed|estimate[d]?|tentative|if|pending|"
                                r"cancelled|canceled|unless|provisional|would|might|may|expected|no longer|subject to)\b|n['’]t\b", body, re.I):
        return None
    is_quote = bool(re.search(r"\b(?:quote|offer)\b", body, re.I))
    if not is_quote and not re.search(r"\b(?:confirmed|we confirm|I confirm)\b", body, re.I):
        return None
    identifier = r"[\w]+(?:[.:-][\w]+)*"
    po_lines = re.findall(r"\bPO\s+(" + identifier + r")(?:\s*/\s*|,?\s+(?:line|item)\s+)(\d+)\b", body, re.I)
    tokens = set(re.findall(identifier, body))
    shipments = tokens.intersection(shipment_ids)
    explicit = set(re.findall(r"\bshipment(?:\s+ID)?\s*[:#]?\s+(" + identifier + r")", body, re.I))
    explicit.update(t for t in tokens if t.startswith("SHP-"))
    if not explicit.issubset(shipments):
        return None
    units = re.findall(r"(?<![\d,.])\b((?:\d{1,3}(?:,\d{3})+|\d+)) (?:pcs|units|pieces)\b", body, re.I)
    rev = re.findall(r"\brev(?:ision)?[. :=]+(" + identifier + r")", body, re.I)
    timestamp = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})"
    times = re.findall(timestamp, body)
    if not all(len(x) == 1 for x in (po_lines, shipments, units, rev)):
        return None
    place = "|".join(re.escape(n) for n in sorted(sites, key=len, reverse=True))
    named_site = rf"(?:(?:at|in|to)\s+(?:{place})\s*,?\s+)?" if place else ""
    arrival = re.findall(r"\b(?:arrival|arrives?|delivery|ETA)\s*" + named_site
                         + r"(?:at\s+|on\s+|by\s+)?[:=]?\s*(" + timestamp + ")", body, re.I)
    # An explicit destination following the time must agree as well.
    following_place = re.search(timestamp + r"\s+(?:at|in|to)\s+(.+)", body, re.I)
    if following_place and (not place or not re.match(rf"(?:{place})(?=[.,;]|\s*$|\s+(?:on|at)\b)", following_place[1], re.I)):
        return None
    if len(arrival) != 1:
        return None
    fields = dict(po_number=po_lines[0][0], po_line=int(po_lines[0][1]), shipment_id=next(iter(shipments)),
                  revision=rev[0], quantity=int(units[0].replace(",", "")), arrival=arrival[0])
    try:
        if is_quote:
            expiry = re.findall(r"valid until\s*[:=]?\s*(" + timestamp + ")", body, re.I)
            modes = re.findall(r"\b(pickup|air|expedite)\b", body, re.I)
            prices = re.findall(r"\b(?:incremental cost|additional fee)\s*[:=]?\s*(?:EUR\s*|€)(\d+(?:\.\d{2})?)(?!\d|[.,]\d)", body, re.I)
            if len(times) == 2 and all(len(x) == 1 for x in (expiry, modes, prices)):
                return SupplierQuote(**fields, mode=modes[0].lower(), incremental_cost_eur=float(prices[0]),
                                     valid_until=expiry[0])
        elif len(times) == 1:
            return DeliveryAdvice(**fields)
    except ValueError:
        pass
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
        if line and line.get("purpose") == "verify_sample_terms":
            from .planner_sample import ingest_sample_correspondence
            return ingest_sample_correspondence(s, msg, out, card, structured=delivery is not None or quote is not None)
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
        advice = delivery or quote
        if advice is None and line:
            shipment_ids = set(s.exec(select(Shipment.shipment_id).where(Shipment.supplier_id == msg.supplier_id)))
            if line.get("shipment_id"):
                shipment_ids.add(line["shipment_id"])
            sites = _delivery_sites(s, msg.supplier_id, line.get("shipment_id"))
            advice = _text_confirmation(msg.body, shipment_ids, sites)
        quote = advice if isinstance(advice, SupplierQuote) else None
        fact = advice.model_dump(mode="json") if advice else {}
        field = "supplier_quote" if quote else "confirmed_delivery"
        reason = ""
        if not line or not out or msg.supplier_id != out.supplier_id or msg.issue_id not in (None, out.issue_id):
            reason = "A unique approved request, supplier and issue must match."
        elif card.status not in {"approved", "executed"} or out.status not in {"sent", "replied", "escalated"}:
            reason = "This order thread has no approved, dispatched supplier request."
        elif not fact or any(fact[k] != line.get(k) for k in ("po_number", "po_line", "shipment_id")):
            reason = "Confirm quantity, revision and site arrival against the requested shipment and PO line."
        else:
            confirmations = s.exec(select(OrderConfirmation).where(
                OrderConfirmation.supplier_id == msg.supplier_id, OrderConfirmation.po_number == fact["po_number"],
                OrderConfirmation.po_line == fact["po_line"], OrderConfirmation.material_id == line["part"])).all()
            if len(confirmations) != 1:
                reason = "Order-line evidence is ambiguous."
            elif (confirmations[0].quantity is None or fact["quantity"] > confirmations[0].quantity
                  or (confirmations[0].revision and fact["revision"].casefold() != confirmations[0].revision.casefold())):
                reason = "Supplier advice conflicts with the confirmed order quantity or revision."
            elif confirmations[0].revision:
                fact["revision"] = confirmations[0].revision
        if not reason:
            prior = []
            for row in s.exec(select(InboxMessage).where(InboxMessage.supplier_id == msg.supplier_id)
                              .order_by(InboxMessage.id.desc())).all():
                old = (row.result or {}).get(field)
                same_scope = old and all(old[k] == fact[k] for k in ("po_number", "po_line", "shipment_id"))
                if same_scope and (not quote or old.get("mode") == fact["mode"]):
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
