"""Ground decision options in the same projection used by the baseline."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from ..core import models as m, policy
from ..core.config import get_settings
from ..core.parsing import parse_datetime
from ..core.store import all_rows
from .planner_forecast import simulate
from .planner_inputs import evidence
from .planner_people import approval_assignments, resolve_approvers, signer_names


def approval_clock(snapshot):
    """Replay evaluates the decision at the recorded instant; live buyers act at the current time."""
    return snapshot if get_settings().mock_mode else max(snapshot, m.utcnow())


def paid_window_open(option):
    if not any(a.get("quote_inbox_id") for a in option.get("actions", [])):
        return True
    snapshot = parse_datetime(option.get("as_of"))
    latest = parse_datetime(option.get("latest_useful"))
    return bool(snapshot and latest and approval_clock(snapshot) <= latest)


def air_freight_limit(documents):
    for doc in documents:
        if doc.kind == "policy":
            match = re.search(r"air freight up to EUR ([\d,]+) per calendar month", doc.text, re.I)
            if match:
                return float(match[1].replace(",", ""))
    return None


def approval_roles(value, documents, *, air=False, monthly_spend=None, freight_eur=None):
    roles = policy.approvers_for(value) if value is not None else []
    policy_docs = [d for d in documents if d.kind == "policy"]
    threshold = air_freight_limit(policy_docs) if air else None
    if threshold is not None:
        charge = freight_eur if freight_eur is not None else value
        within_limit = monthly_spend is not None and charge is not None and monthly_spend + charge <= threshold
        role = "Category lead" if within_limit else "CPO"
        roles = list(dict.fromkeys([*roles, role]))
    return roles


def approvers(value, category, plan, documents, *, buyer="", **conditions):
    roles = approval_roles(value, documents, **conditions)
    return resolve_approvers(roles, category, plan, documents, buyer)


def amended_po_basis(orders, fee):
    """Preserve the priced lines behind the authorization. Ambiguous or unconverted values stay unknown."""
    if len({(o.po_number, o.supplier_id) for o in orders}) != 1:
        return None
    lines = {}
    for o in orders:
        if o.po_line is None or o.currency != "EUR" or o.quantity is None or o.unit_price is None:
            return None
        qty, unit = Decimal(str(o.quantity)), Decimal(str(o.unit_price))
        if not qty.is_finite() or not unit.is_finite() or qty < 0 or unit < 0:
            return None
        terms = (o.material_id, qty, unit)
        if o.po_line in lines and lines[o.po_line] != terms:
            return None
        lines[o.po_line] = terms
    value = sum(qty * unit for _, qty, unit in lines.values())
    return dict(po=orders[0].po_number, order_eur=float(value), fee_eur=fee, total_eur=float(value + Decimal(str(fee))),
                lines=[dict(line=n, material=part, quantity=float(qty), unit_price=float(unit), value_eur=float(qty * unit))
                       for n, (part, qty, unit) in sorted(lines.items())])


def order_buyer(orders, part, supplier, po, line=None):
    buyers = {o.buyer for o in orders if o.buyer and o.material_id == part and o.po_number == po
              and (not supplier or o.supplier_id == supplier) and (line is None or o.po_line == line)}
    if len(buyers) > 1:
        return None
    return next(iter(buyers), "")


def useful_until(plan, row, quantity):
    if not row.rate_per_hour or not quantity:
        return plan.deadline
    hours = quantity / row.rate_per_hour
    for start, end in reversed(plan.shifts):
        start, end = max(start, plan.as_of), min(end, plan.deadline)
        available = max(0, (end - start).total_seconds() / 3600)
        if available >= hours:
            return end - timedelta(hours=hours)
        hours -= available
    return plan.as_of


def rush_terms(offer, notices, now):
    text = " ".join(offer.text.split())
    duration = re.search(r"(\d+) h from print-ready file", text, re.I)
    drive = re.search(r"about (\d+) h driving", text, re.I)
    percent = re.search(r"Annex A price \+ (\d+) %", text, re.I)
    cap = re.search(r"up to ([\d,]+) pcs", text, re.I)
    if not all((duration, drive, percent, cap)):
        return None
    hours, drive_hours = int(duration[1]), int(drive[1])
    limit = int(cap[1].replace(",", ""))
    proof = [evidence(offer.path, "page 1, item 1 and transport", duration[0])]
    for notice in notices:
        if notice.supplier_id != offer.supplier_id or "downtime" not in notice.extracted.get("facts", {}).get("signals", []):
            continue
        period = re.findall(r"\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}", notice.text)
        rate = re.search(r"do ([\d ]+) szt\..*?na\s+(\d+) h", notice.text, re.S)
        if rate and len(period) >= 2:
            start, end = [datetime.strptime(t, "%d.%m.%Y %H:%M").replace(tzinfo=now.tzinfo) for t in period[:2]]
            if now < end and now + timedelta(hours=hours) > start:
                limit = min(limit, int(rate[1].replace(" ", "")) * hours // int(rate[2]))
                proof.append(evidence(notice.path, "page 1, digital line", " ".join(rate[0].split())))
    return limit, hours + drive_hours, float(percent[1]), proof


def options_for(issue, plan, forecast, row):
    pid = row.product_id
    supplier_parts = {p.component_id for p in all_rows(m.SupplierPartMap) if issue.supplier_id
                      and p.supplier_id == issue.supplier_id and p.component_id in plan.bom.get(pid, {})}
    part = issue.part_id or forecast.limiting_part
    if not issue.part_id and supplier_parts and part not in supplier_parts:
        part = sorted(supplier_parts)[0]
    docs = list(all_rows(m.Document))
    material = next((r for r in all_rows(m.Material) if r.component_id == part), None)
    category = material.category if material else ""
    purchase_orders = all_rows(m.PurchaseOrder)
    choices = []
    due = parse_datetime((issue.impact or {}).get("due_by")) or plan.deadline
    priority = next(i for i, edition in enumerate(plan.editions) if edition.product_id == pid)
    def add(label, kind, cost, gained, note, *, actions=(), value=None, available=True, proof=(), air=False, required_roles=None):
        monthly = None
        if air:
            period = plan.as_of.strftime("%Y-%m")
            amounts = [r.get("month_to_date_eur") for d in docs if not d.path.startswith("suppliers/")
                       for r in d.extracted.get("rows", []) if r.get("category") == category
                       and r.get("month") == period and r.get("month_to_date_eur") is not None]
            if len(amounts) == 1:
                from ..core.parsing import parse_number
                monthly = parse_number(amounts[0])
        roles = required_roles or approval_roles(value, docs, air=air, monthly_spend=monthly, freight_eur=cost)
        action = actions[0] if actions else {}
        buyer = order_buyer(purchase_orders, part, action.get("supplier_id") or issue.supplier_id,
                            action.get("po_number") or issue.po_number, action.get("po_line"))
        assignments = approval_assignments(roles, category, plan.as_of.date(), docs, buyer)
        signers = signer_names(assignments)
        policy_proof = [evidence(d.path, "page 1, approval routing") for d in docs if d.kind == "policy"]
        for path in dict.fromkeys(a["org_source"] for a in assignments if a["org_source"]):
            policy_proof.append(evidence(path, "effective approval assignment"))

        latest = min(useful_until(plan, row, gained), due)
        choices.append(dict(product_id=pid, edition=row.edition, priority=priority, part=part,
                            as_of=plan.as_of.isoformat(), latest_useful=latest.isoformat(), label=label, description=kind, cost_eur=cost, copies_gained=gained,
                            remaining_shortfall=None if gained is None or forecast.shortfall is None else max(0, forecast.shortfall - gained),
                            actions=list(actions), risk_note=note, evidence=[*proof, *policy_proof],
                            approvers=signers, approval_roles=roles, assignments=assignments, approval_value_eur=value,
                            needs_approval=(value is None or policy.needs_approval(value)
                                            or (air and air_freight_limit(docs) is not None)), available=available))
        shipment = actions[0].get("shipment_id") if actions else None
        if pid in plan.launch_priorities and actions:
            priority_source = plan.launch_priorities[pid]
            choices[-1]["launch_priority"] = priority_source
            choices[-1]["evidence"].append(priority_source["evidence"])
        checks = plan.shipment_checks.get(shipment)
        if checks:
            choices[-1]["shipment_checks"] = checks
            choices[-1]["evidence"].extend(evidence(r["source"], shipment, r["value"])
                                           for check in checks for r in check["readings"])
    add("Keep the current plan", "baseline", 0, 0, "Retain released stock and confirmed line-ready deliveries.", value=0)
    targets = {a.get("target") for a in issue.suggested_actions or [] if a.get("target")}
    if issue.supplier_id:
        targets.add(issue.supplier_id)
    inbound = [x for x in plan.inbound if x["part"] == part and not x["revision_blocked"]
               and (not issue.supplier_id or issue.type == "rev_blocked" or x["supplier_id"] in targets)
               and (not issue.shipment_id or x["shipment_id"] == issue.shipment_id)
               and (not issue.po_number or x["po_number"] == issue.po_number)]
    inbound.sort(key=lambda x: (x["eta"] is None, x["eta"] or "", x["shipment_id"]))
    candidate = inbound[0] if inbound else None
    recommended = 0
    decide_by = plan.deadline
    if candidate:
        c = candidate
        key = (c["supplier_id"], c["po_number"], c["po_line"], c["shipment_id"])
        action = {k: c[k] for k in ("supplier_id", "po_number", "po_line", "shipment_id", "part")}
        action.update(type="email", purpose="confirm_supply", ask=f"Confirm released revision, quantity and timed site delivery for PO "
                                        f"{c['po_number']}/{c['po_line']} on {c['shipment_id']}. If earlier delivery is possible, quote the additional fee, arrival and offer expiry.")
        earliest = max(plan.as_of, parse_datetime(c["eta"]) or plan.as_of)
        possible = simulate(plan, {key: (earliest, part, c["quantity"])})[pid]
        gain = possible.projected_packed - forecast.projected_packed if forecast.projected_packed is not None else None
        add("Confirm existing supply", "information request", 0, gain,
            "Conditional ceiling at the earliest evidenced date, subject to a released dock. Asking creates no supply.",
            actions=[action], value=0, proof=c["evidence"])
        choices[-1]["dock_released"] = c["dock_released"]
        if forecast.shortfall and earliest < plan.deadline and not c["eligible"] and not any(e["path"].startswith("inbox/") for e in c["evidence"]):
            recommended = 1
        if gain:
            decide_by = useful_until(plan, row, gain)
    if candidate:
        # A quote is a conditional scenario. Only a later delivery confirmation enters plan.arrivals.
        latest_quotes = {}
        for msg in sorted(all_rows(m.InboxMessage), key=lambda r: r.id):
            quote = (msg.result or {}).get("supplier_quote")
            if (quote and msg.supplier_id == candidate["supplier_id"] and
                    all(quote[k] == candidate[k] for k in ("po_number", "po_line", "shipment_id"))):
                latest_quotes[quote["mode"]] = (msg, quote)
        for msg, quote in latest_quotes.values():
            arrival = parse_datetime(quote["arrival"])
            dock = parse_datetime(candidate["dock_at"])
            usable_at = max(arrival, dock) if dock else arrival
            qty = min(candidate["quantity"], quote["quantity"])
            alternative = simulate(plan, {key: (usable_at, part, qty)})[pid]
            gain = alternative.projected_packed - forecast.projected_packed if forecast.projected_packed is not None else None
            orders = [o for o in purchase_orders if o.po_number == quote["po_number"] and o.supplier_id == msg.supplier_id]
            extra = quote["incremental_cost_eur"]
            basis = amended_po_basis(orders, extra)
            gross = basis["total_eur"] if basis else None
            expiry = parse_datetime(quote["valid_until"])
            purchase_by = min(expiry, arrival, useful_until(plan, row, gain), due)
            execute = bool(gain and gain > 0 and gross is not None and dock and approval_clock(plan.as_of) <= purchase_by)
            order = dict(action, purpose="accept_" + quote["mode"], ask=f"Accept quoted {quote['mode']} service for PO {quote['po_number']}/{quote['po_line']}, "
                                     f"{qty:g} units, extra EUR {extra:.2f}. Confirm site arrival.",
                         quote_inbox_id=msg.id)
            add(f"Accept {quote['mode']} quote", quote["mode"], extra, gain,
                "Conditional on supplier confirmation. Requires a valid quote, released dock and priced PO.",
                actions=[order], value=gross, available=execute, air=quote["mode"] == "air",
                proof=candidate["evidence"] + [evidence(f"inbox/{msg.id}", "supplier quote")])
            choices[-1]["latest_useful"] = purchase_by.isoformat()
            if basis:
                choices[-1]["approval_basis"] = basis
            if execute:
                selected_gain = choices[recommended]["copies_gained"]
                if selected_gain:
                    better = gain > selected_gain or (gain == selected_gain and extra < (choices[recommended]["cost_eur"] or 0))
                else:
                    better = True
                if choices[recommended]["description"] == "information request" or better:
                    recommended = len(choices) - 1
                    decide_by = purchase_by
    # An offer is tied to a supplier and material, independently of any in-transit shipment.
    suppliers = {p.supplier_id for p in all_rows(m.SupplierPartMap) if p.component_id == part}
    suppliers.update(c["supplier_id"] for c in inbound)
    if issue.supplier_id:
        suppliers.add(issue.supplier_id)
    for offer in docs:
        if offer.kind != "offer" or offer.supplier_id not in suppliers or not material:
            continue
        text = " ".join(offer.text.split())
        item = re.search(re.escape(part) + r" .*?finished-goods store.*?/ ([\d,]+) pcs .*?loaded within (\d+) h", text, re.I)
        truck = re.search(r"about (\d+) h driving", text, re.I)
        if item and truck and forecast.shortfall and forecast.limiting_part == part:
            qty = int(item[1].replace(",", ""))
            lead = timedelta(hours=int(item[2]) + int(truck[1]))
            at = plan.as_of + lead
            modeled = simulate(plan, {("supplier overrun", offer.path): (at, part, qty)})[pid]
            gain = modeled.projected_packed - forecast.projected_packed
            price = next((p for p in all_rows(m.SupplierPartMap) if p.supplier_id == offer.supplier_id
                          and p.component_id == part and p.currency == "EUR"), None)
            cost = round(qty * price.unit_price, 2) if price and price.unit_price is not None else None
            proof = [evidence(offer.path, "page 1, finished-goods offer and transport", item[0])]
            if price:
                proof.append(evidence(price.source, part, f"EUR {price.unit_price} per unit"))
            note = (f"{qty:,} finished units offered. Earliest modeled arrival {at.isoformat()}. "
                    "Buyer must confirm offer validity, freight, dock release and a new PO before ordering.")
            review = {"type": "human_review", "product_id": pid, "part": part,
                      "reference": offer.path, "supplier_id": offer.supplier_id, "copies_at_risk": forecast.shortfall}
            add(f"Verify the offer for {qty:,} units of {material.description or part}", "supplier stock offer", cost, gain, note,
                actions=[review], value=0, proof=proof)
            choices[-1]["needs_approval"] = True
            choices[-1]["latest_useful"] = min(useful_until(plan, row, gain) - lead, due).isoformat()
            if gain > 0:
                recommended = len(choices) - 1
                decide_by = min(decide_by, useful_until(plan, row, gain) - lead)
        # Only match the items covered by the rush-print paragraph (the art book is a separate offer).
        scope = re.search(r"agreement:\s*(.*?)\s+do\s+\d", " ".join(offer.text.split()), re.I)
        if not scope or not any(word.rstrip("s") in material.description.lower()
                                for word in re.findall(r"[a-z]+", scope[1].lower()) if len(word) > 3):
            continue
        terms = rush_terms(offer, docs, plan.as_of)
        if not terms:
            continue
        qty, lead, premium, proof = terms
        price = next((p for p in all_rows(m.SupplierPartMap) if p.supplier_id == offer.supplier_id
                      and p.component_id == part and p.currency == "EUR"), None)
        cost = round(qty * price.unit_price * (1 + premium / 100), 2) if price and price.unit_price is not None else None
        if price:
            proof.append(evidence(price.source, part, "EUR unit price; transport excluded"))
        at = plan.as_of + timedelta(hours=lead)
        rerun = simulate(plan, {("new print", offer.id): (at, part, qty)})[pid]
        gain = rerun.projected_packed - forecast.projected_packed if forecast.projected_packed is not None else None
        add("Order a fresh rush print", "expedite", cost, gain,
            f"Up to {qty:,} units; earliest site arrival {at.isoformat()}. Freight and dock release require confirmation.",
            value=cost, available=False, proof=proof)
    if issue.type == "quality_hold_failed_aql":
        for lot in plan.lots:
            if lot["part"] != part or lot["status"] != "Quality hold":
                continue
            inspection = next((d for d in docs if d.kind == "inspection" and lot["batch"] in d.text
                               and "concession signed by Supplier Quality and the Category Lead"
                               in " ".join(d.text.split())), None)
            if not inspection:
                continue
            outcome = simulate(plan, {("concession", lot["batch"]): (plan.as_of, part, lot["quantity"])})[pid]
            gain = outcome.projected_packed - forecast.projected_packed if forecast.projected_packed is not None else None
            review = {"type": "human_review", "product_id": pid, "part": part,
                      "reference": lot["batch"], "copies_at_risk": forecast.shortfall}
            add(f"Request a documented concession for lot {lot['batch']}", "quality concession review", 0, gain,
                "Supplier Quality and the responsible category lead must sign. PP-07 routes an exception for "
                "a vacant seat to the head of procurement. The lot stays on hold pending a signed release.",
                actions=[review], value=0, proof=lot["evidence"], required_roles=["Supplier Quality Lead", "Category lead"])
            choices[-1]["needs_approval"] = True
            if gain and gain > 0:
                recommended = len(choices) - 1
    for a in issue.suggested_actions or []:
        kind = a.get("type")
        if candidate and kind == "expedite" and "pick" in a["label"].lower() and a.get("target") == candidate["shipment_id"]:
            c = candidate
            key = (c["supplier_id"], c["po_number"], c["po_line"], c["shipment_id"])
            earliest = max(plan.as_of, parse_datetime(c["eta"]) or plan.as_of)
            modeled = simulate(plan, {key: (earliest, part, c["quantity"])})[pid]
            gain = modeled.projected_packed - forecast.projected_packed if forecast.projected_packed is not None else None
            review = {"type": "human_review", "product_id": pid, "part": part,
                      "reference": c["shipment_id"], "shipment_id": c["shipment_id"],
                      "supplier_id": c["supplier_id"], "po_number": c["po_number"], "po_line": c["po_line"], "copies_at_risk": forecast.shortfall}
            add(f"Arrange terminal pickup for {c['shipment_id']}", "port pickup", None, gain,
                "Conditional ceiling if cases reach the line immediately. Logistics must confirm terminal release, "
                "a truck quote and an authorized dock slot before dispatch.", actions=[review], value=0,
                proof=c["evidence"] + issue.evidence)
            choices[-1]["needs_approval"] = True
            choices[-1]["dock_released"] = c["dock_released"]
            if gain and forecast.limiting_part == part:
                recommended = len(choices) - 1
            continue
        if kind == "email" and candidate or kind == "expedite" and any(c["description"] == kind for c in choices):
            continue
        if kind in {"block_stock", "human_review"}:
            if not candidate:
                add(a["label"], "human review", 0, None, "Check the lot or delivery with the responsible site owner.",
                    value=0, available=False, proof=issue.evidence)
            continue
        if kind == "reallocate":
            other_order = [row] + [r for r in plan.editions if r.product_id != pid]
            projected = simulate(plan, priority=other_order)
            gain = projected[pid].projected_packed - forecast.projected_packed if forecast.projected_packed is not None else None
            baseline = simulate(plan)
            effects = [f"{r.edition} {projected[r.product_id].shortfall - baseline[r.product_id].shortfall:+d} short"
                       for r in plan.editions if projected[r.product_id].shortfall is not None
                       and baseline[r.product_id].shortfall is not None]
            add(a["label"], kind, 0, gain, "Changing priority requires customer authorization. " + ", ".join(effects),
                value=0, available=False)
        elif kind == "use_old_revision":
            rules = [e for e in all_rows(m.EngineeringChange) if e.child_id == part and e.parent_id == pid
                     and e.effective_from and e.effective_from <= plan.as_of.date()]
            rule = max(rules, key=lambda e: (e.effective_from, e.id), default=None)
            permitted = rule and rule.use_up_old_rev is True
            note = ("Released old-revision units are already included in the baseline." if permitted else
                    "No effective engineering release permits these units.")
            add(a["label"], kind, 0, 0, note, value=0, available=False,
                proof=[evidence(rule.source, rule.change_id, rule.reason)] if rule else [])
        else:
            action = None
            if candidate and kind in {"email", "expedite", "other"}:
                action = dict(choices[1]["actions"][0], purpose="request_quote_" + kind, target=a.get("target"), ask=f"Obtain a quote and timed site delivery for: {a['label']}")
            add(a["label"], kind, a.get("cost_eur"), None, "Awaiting evidence for quantity, arrival, and release.",
                actions=[action] if action else [], value=0 if action else a.get("cost_eur"),
                available=bool(action), air="air" in a["label"].lower() and not action)
    if recommended == 0 and forecast.shortfall is not None and forecast.shortfall > 0:
        blocker = forecast.limiting_part
        target = blocker or f"{row.edition} packing capacity"
        review = {"type": "human_review", "part": blocker, "product_id": pid,
                  "copies_at_risk": forecast.shortfall}
        add(f"Review recovery options for {target}", "human review", 0, 0,
            f"Ask the buyer to verify {target} and obtain a feasible recovery plan before the deadline. "
            "The forecast stays unchanged until supporting evidence arrives.",
            actions=[review], value=0, proof=forecast.details["evidence"])
        recommended = len(choices) - 1
        choices[recommended]["needs_approval"] = True
    due = parse_datetime((issue.impact or {}).get("due_by"))
    return choices, recommended, min(decide_by, due) if due else decide_by
