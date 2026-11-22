"""Buyer-facing projection over immutable decision versions. The decision endpoint owns execution."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import re

from sqlmodel import select

from ..core import policy
from ..core.models import DecisionCard, Issue
from ..core.parsing import parse_datetime
from .planner_options import paid_window_open


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def physical_key(option, card=None):
    """Resolve stored pre-queue options too. Ignore generated copy, signer and projected gains."""
    actions = option.get("actions") or []
    if len(actions) != 1:
        return None
    action = actions[0]
    kind = option.get("description", "")
    part = action.get("part")
    reference = action.get("reference")
    if option.get("followup_for") and action.get("purpose") == "verify_sample_terms":
        return ("verify_sample_terms", option["followup_for"], action.get("supplier_id"), action.get("component"), part)
    if option.get("horizon") == "future" and reference:
        # Old reviews named only the ordered assembly. The evidence still identifies the embedded component.
        scope = option.get("exposure") or {}
        return (kind, reference.split("/")[0], action.get("supplier_id"), part, scope.get("component") or "")
    if action["type"] == "human_review" and kind != "port pickup":
        if kind == "supplier stock offer":
            supplier = action.get("supplier_id")
            if not supplier:  # Original stored offers used the supplier's document path.
                match = re.match(r"suppliers/(SUP-[^_/]+)", reference or "")
                supplier = match[1] if match else None
            return (kind, supplier, reference, part)
        if reference:
            return (kind, reference, part)
        return ("review", action.get("product_id"), part)
    line = action
    if kind == "port pickup" and not action.get("po_line") and card:
        lines = [a for o in card.options for a in o.get("actions", [])
                 if a.get("shipment_id") == reference and a.get("part") == part and a.get("po_line")]
        if len({(a.get("po_number"), a["po_line"]) for a in lines}) == 1:
            line = lines[0]
    purpose = action.get("purpose")
    target = action.get("target")
    if not purpose:
        if kind == "port pickup":
            purpose = "terminal_pickup_review"
        elif action.get("quote_inbox_id"):
            purpose = "accept_" + kind
        elif kind == "information request":
            purpose = "confirm_supply"
        else:
            purpose = "request_quote_" + kind
    return (purpose, line.get("supplier_id"), line.get("po_number"), line.get("po_line"),
            line.get("shipment_id") or reference, part, target if purpose.startswith("request_quote_") else None)


def authorization_key(option, card=None):
    """Alternative paid services for one shipment line represent one freight commitment."""
    key = physical_key(option, card)
    if key and option["actions"][0].get("quote_inbox_id"):
        return ("accept_freight_service", *key[1:])
    return key


def selection_version(card, index, option):
    return digest([card.id, index, option])


def _instructions(option):
    action = option["actions"][0]
    kind = option["description"]
    if option.get("horizon") == "future":
        return option["risk_note"], option["requirements"]
    site = policy.setup().site or "receiving site"
    dock = [] if option.get("dock_released") else [f"Released {site} dock slot"]
    if kind == "port pickup":
        return "Open a Logistics review to price pickup and confirm terminal and dock release.", ["Terminal release", "Truck quote and arrival time", *dock]
    if kind == "supplier stock offer":
        return "Verify freight, arrival and dock release before placing a new PO.", [
            "Offer validity and freight cost", f"Timed arrival at {site}", "Released dock slot"]
    if kind == "quality concession review":
        return "Request a documented Quality concession for this lot.", ["Signed concession", "Recorded lot release"]
    if action["type"] == "human_review":
        return "Open an internal recovery review for the buyer.", ["A feasible recovery option and supporting evidence"]
    if action.get("quote_inbox_id"):
        return "Authorize the Chaser to accept this quote and request a confirmed delivery.", ["Supplier confirmation of line-ready delivery"]
    return action["ask"], ["Supplier response matched to the shipment and PO line", *dock]


def _rank(item):
    option = item["option"]
    gain = option.get("copies_gained") or 0
    latest = parse_datetime(item["decide_by"])
    as_of = parse_datetime(option.get("as_of"))
    actionable_gain = gain > 0 and (not latest or not as_of or latest >= as_of)
    order_time = latest.timestamp() if latest else float("inf")
    if option.get("horizon") == "future":
        action = option["actions"][0]
        qualified = (action.get("reference") or "").startswith("qualification/")
        scheduled = (o["promised"] for o in (option.get("exposure") or {}).get("orders", []) if o.get("promised"))
        return (True, 0 if qualified else 1, option.get("priority", 999), min(scheduled, default="9999-12-31"), -gain, item["key"])
    return (not actionable_gain, 2, option.get("priority", 999), order_time, -gain, item["key"])


def compare_freight(entries):
    """Compare executable priced services for the same physical line and edition."""
    quotes = {}
    for entry in entries:
        option = entry["option"]
        if (not option["actions"][0].get("quote_inbox_id") or option.get("cost_eur") is None
                or not (option.get("copies_gained") or 0) > 0):
            continue
        scope = (entry["edition"], authorization_key(option))
        quotes.setdefault(scope, []).append(entry)
    for group in quotes.values():
        options = [e["option"] for e in group]
        def merit(option):
            return (-option["copies_gained"], option["cost_eur"])
        best = min(merit(o) for o in options)
        for entry in group:
            o = entry["option"]
            entry["freight_comparison"] = dict(
                fee_per_additional_copy=o["cost_eur"] / o["copies_gained"], preferred=merit(o) == best,
                alternatives=[dict(label=a["label"], fee=a["cost_eur"], gain=a["copies_gained"])
                              for a in sorted(options, key=merit) if a is not o])


def buyer_queue(db):
    cards = db.exec(select(DecisionCard).where(DecisionCard.created_by == "planner")
                    .order_by(DecisionCard.id.desc())).all()
    authorized = [(c, c.options[c.chosen_option]) for c in cards
                  if c.status in ("approved", "executed") and c.chosen_option is not None]
    claimed = {authorization_key(option, card) for card, option in authorized}
    specific_decisions = {f"decisions/{card.id}" for card, opt in authorized
                          if any(a.get("type") == "human_review" and a.get("reference")
                                 for a in opt.get("actions", []))}
    reviews = db.exec(select(Issue).where(Issue.raised_by == "planner", Issue.type == "recovery_review",
                                         Issue.status != "resolved"))
    active_reviews = {(i.product_id, i.part_id) for i in reviews
                      if any(e.get("path") in specific_decisions for e in i.evidence)}
    groups = {}
    for card in cards:
        if card.status != "proposed":
            continue
        for index, option in enumerate(card.options):
            physical = physical_key(option, card)
            if (not physical or authorization_key(option, card) in claimed
                    or option.get("available") is False or not paid_window_open(option)):
                continue
            key = digest([physical, sorted(option.get("approvers", [])), sorted(option.get("approval_roles", []))])
            step, requirements = _instructions(option)
            source = dict(decision_id=card.id, option_index=index, issue_ids=card.issue_ids, related_review=False)
            item = dict(key=key, decision_id=card.id, option_index=index, version=selection_version(card, index, option),
                        edition=option.get("edition") or card.title.split(" · ")[0],
                        part=option["actions"][0].get("part"), option=option,
                        decide_by=option.get("latest_useful") or (card.decide_by.isoformat() if card.decide_by else None),
                        next_step=step, requirements=requirements, sources=[source], evidence=list(card.evidence))
            old = groups.get(key)
            if old:
                # The highest-priority edition is the representative. Never add overlapping gains.
                best, other = (item, old) if _rank(item) < _rank(old) else (old, item)
                best["sources"].extend(other["sources"])
                best["evidence"].extend(other["evidence"])
                item = best
            groups[key] = item
    entries = sorted(groups.values(), key=_rank)
    specific = {}
    for item in entries:
        if item["option"]["description"] != "human review" and item["option"].get("horizon") != "future":
            specific.setdefault((item["edition"], item["part"]), item)
    for item in entries:
        target = specific.get((item["edition"], item["part"]))
        if item["option"]["description"] != "human review":
            continue
        if target:
            target["sources"].extend(dict(s, related_review=True) for s in item["sources"])
            target["evidence"].extend(item["evidence"])
            del groups[item["key"]]
        elif (item["option"].get("product_id"), item["part"]) in active_reviews:
            del groups[item["key"]]
    def shipment_scope(entry):
        action = entry["option"]["actions"][0]
        return (entry["edition"], action.get("shipment_id"), action.get("po_number"), action.get("po_line"))

    counts = Counter(shipment_scope(e) for e in groups.values() if (e["option"].get("copies_gained") or 0) > 0)
    for entry in groups.values():
        scope = shipment_scope(entry)
        entry["shared_shipment"] = scope[1] if counts[scope] > 1 and entry["option"].get("copies_gained") else None
        entry["on_time_recovery"] = not _rank(entry)[0]
        entry["evidence"] = list({digest(e): e for e in entry["evidence"]}.values())
    compare_freight(groups.values())
    return sorted(groups.values(), key=_rank)


def current_option(db, card, index):
    """Recheck forecast, physical eligibility and policy using the same inputs as the Planner."""
    from .planner_forecast import simulate
    from .planner_inputs import load_plan
    from .planner_options import options_for

    old = card.options[index]
    issue = db.get(Issue, card.issue_ids[0]) if card.issue_ids else None
    if not issue or issue.status == "resolved":
        return None
    plan = load_plan()
    if old.get("followup_for"):
        from .planner_sample import sample_inquiry
        return old if sample_inquiry(db, issue, plan) == old else None
    if old.get("horizon") == "future":
        from .planner_regulatory import regulatory_option
        return old if regulatory_option(issue, plan) == old else None
    row = next((r for r in plan.editions if r.product_id == old.get("product_id")), None) if plan else None
    if row:
        options, _, _ = options_for(issue, plan, simulate(plan)[row.product_id], row)
        return next((o for o in options if o == old), None)
    return None
