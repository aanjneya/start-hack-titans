"""Separate, non-spending correspondence on an already assigned supplier qualification review."""
from sqlmodel import select

from ..core import models as m, trace
from .planner_regulatory import regulatory_option


def sample_inquiry(db, review, plan):
    if review.type != "recovery_review" or review.status == "resolved" or review.impact.get("horizon") != "future":
        return None
    parent_ids = {e["path"].removeprefix("decisions/") for e in review.evidence
                  if e.get("path", "").startswith("decisions/") and e.get("locator", "").startswith("approved option ")}
    if len(parent_ids) != 1:
        return None
    ref = parent_ids.pop()
    parent = db.get(m.DecisionCard, int(ref)) if ref.isdecimal() else None
    if not parent or parent.status != "executed" or parent.chosen_option is None or not parent.issue_ids:
        return None
    driver = db.get(m.Issue, parent.issue_ids[0])
    current = regulatory_option(driver, plan) if driver else None
    if not current or current["button_label"] != "Start qualification review":
        return None
    exposure = current["exposure"]
    offers = exposure.get("sample_offers") or []
    if not offers or not current["actions"][0].get("supplier_id"):
        return None
    original = parent.options[parent.chosen_option]
    if (exposure["component"] != original.get("exposure", {}).get("component") or
            current["actions"][0]["part"] != review.part_id):
        return None
    references = ", ".join(f"{o['po']}/{o['line']}" for o in exposure["orders"] if o["line"] is not None)
    ask = (f"For {exposure['component_name'] or exposure['component']} ({exposure['component']})"
           + (f" in assembly {exposure['assembly']}" if exposure['assembly'] else "")
           + (f", relating to PO lines {references}" if references else "")
           + f", your notice states: {offers[0]['text']} "
           "Please confirm the sample specification, availability date, freight, any charges and qualification documents. "
           "This inquiry requests information only.")
    sourcing = current["actions"][0]
    return dict(followup_for=parent.id, horizon="future", button_label="Send sample inquiry",
                label=f"Ask {exposure['supplier']} for sample terms", description="supplier sample inquiry",
                edition=current["edition"], product_id=current["product_id"], part=review.part_id,
                currency=current["currency"], cost_eur=0, approval_value_eur=0, copies_gained=None, remaining_shortfall=None,
                approvers=current["approvers"], approval_roles=current["approval_roles"], available=current["available"],
                needs_approval=True, requirements=["Supplier response", "Engineering and Quality assessment"],
                risk_note="Request written sample terms for the assigned review. This sends one inquiry and commits no spend.",
                evidence=[dict(path=f"decisions/{parent.id}", locator="authorized review"),
                          *[e for e in current["evidence"] if e.get("locator") == "supplier sample terms"]],
                actions=[dict(type="email", purpose="verify_sample_terms", reference=sourcing["reference"],
                              supplier_id=sourcing["supplier_id"], part=review.part_id,
                              component=exposure["component"], ask=ask)])


def ingest_sample_correspondence(db, msg, mail, card, *, structured):
    """Store correspondence on the exact thread. Engineering/Quality still decide what it establishes."""
    review = db.get(m.Issue, mail.issue_id) if mail.issue_id else None
    reason = None
    if (not review or review.type != "recovery_review" or msg.supplier_id != mail.supplier_id
            or msg.issue_id not in (None, mail.issue_id)):
        reason = "The reply must match the supplier and the assigned qualification review."
    elif mail.status not in {"sent", "replied", "escalated"}:
        reason = "This sample inquiry has no recorded dispatch."
    elif structured or not msg.body.strip():
        reason = "This inquiry collects written sample terms. A delivery or purchase quote needs its own order thread."
    else:
        previous = db.exec(select(m.InboxMessage).where(m.InboxMessage.outbox_id == mail.id,
                           m.InboxMessage.id < msg.id).order_by(m.InboxMessage.id)).all()
        duplicate = next((p for p in previous if p.result.get("qualification_correspondence")
                          and " ".join(p.body.split()) == " ".join(msg.body.split())), None)
        if duplicate:
            reason = f"Duplicate of inbox/{duplicate.id}"
    target = db.get(m.InboxMessage, msg.id)
    target.processed = True
    if reason:
        target.result = {"review": reason}
    else:
        target.issue_id = review.id
        target.result = {"qualification_correspondence": {"review_id": review.id, "decision_id": card.id}}
        mail.status = "replied"
        db.add(mail)
        review.evidence = [*review.evidence, dict(path=f"inbox/{msg.id}", locator="supplier correspondence",
                                                  quote=msg.body.strip()[:180])]
        review.updated_at = m.utcnow()
        db.add(review)
    db.add(target)
    db.commit()
    trace.log("ingest", "delivery_review" if reason else "qualification_correspondence",
              reason or "Attached the supplier response to the assigned qualification review.",
              [dict(path=f"inbox/{msg.id}", locator="supplier correspondence")], data={"inbox_id": msg.id,
              "outbox_id": mail.id, "decision_id": card.id, "issue_id": mail.issue_id})
    return True
