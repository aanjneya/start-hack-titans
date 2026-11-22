"""Convert a documented future-supply finding into an internal, non-spending review."""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
import re

from sqlmodel import select

from ..core import models as m, policy
from ..core.store import all_rows, find_rows, latest_by, session
from .planner_risk import buyer_risk, source_kind
from .planner_counts import fitted_components
from .planner_inputs import evidence
from .planner_people import approval_assignments, signer_names, supplier_release_gate


def relevant(issue):
    if issue.raised_by == "planner":
        return False
    actions = issue.suggested_actions or []
    if issue.raised_by == "risk" and issue.subject_type == "supplier" and issue.type == "high_risk_rating":
        evidence = [e.get("locator", "") for e in issue.evidence]
        if any(loc.startswith("regulations:") for loc in evidence) and any(a.get("type") == "human_review" for a in actions):
            return True
        return (any(loc.startswith(("polymarket:", "gnews:", "gdelt:", "portal_news:")) for loc in evidence)
                and any(a.get("type") in {"second_source", "safety_stock"} for a in actions))
    if issue.kind != m.IssueKind.HUMAN_CHECK:
        return False
    return any(a.get("type") == "second_source" or
               (a.get("type") == "human_review" and
                ("compliance" in a.get("label", "").lower() or any(
                    e.get("locator", "").startswith("regulations:") for e in issue.evidence)))
               for a in actions)


@dataclass
class OrderScope:
    lines: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    proof: list = field(default_factory=list)
    disputed_buyer: bool = False


def current_order_lines(supplier_id, dated):
    rows = find_rows(m.PurchaseOrder, supplier_id=supplier_id)
    grouped = defaultdict(list)
    for po in latest_by(rows, "po_number", "po_line", "source"):
        if not po.order_date or po.order_date <= dated:
            grouped[po.po_number, po.po_line].append(po)
    return grouped


def open_orders(supplier_id, dated, part=None, after=None):
    """Resolve representations of each physical line before testing the notice window."""
    result = OrderScope()
    grouped = current_order_lines(supplier_id, dated)

    def eligible(po):
        return ((po.status or "").lower() in {"open", "awaiting_approval", "pending_approval"}
                and (not part or po.material_id == part)
                and (not after or (po.promised_date and po.promised_date > after))
                and (not po.actual_delivery_date or po.actual_delivery_date > dated))

    for (number, line), copies in grouped.items():
        if not any(eligible(po) for po in copies):
            continue
        key = f"{number or '?'}/{line if line is not None else '?'}"
        merged, disputed = {}, []
        for name in ("status", "material_id", "plant", "promised_date", "actual_delivery_date", "quantity", "unit_price", "currency", "buyer"):
            values = {getattr(po, name) for po in copies if getattr(po, name) not in (None, "")}
            if name == "status":
                values = {v.lower() for v in values}
            if len(values) > 1:
                disputed.append(name)
            merged[name] = next(iter(values)) if len(values) == 1 else None
        if disputed or not number or line is None:
            why = ", ".join(s.replace("_", " ") for s in disputed) if disputed else "PO line identifier"
            result.notes.append(f"Verify {key} against the purchasing records ({why}).")
        if disputed or len(copies) > 1 or not number or line is None:
            for po in copies:
                fields = disputed or ("status", "promised_date", "quantity", "unit_price", "currency")
                quote = ", ".join(f"{f.replace('_', ' ')}: {getattr(po, f)}" for f in fields if getattr(po, f) is not None)
                result.proof.append(dict(evidence(po.source, key, quote), source_kind="Order record"))
        if not number or line is None or set(disputed) & {"status", "material_id", "plant", "promised_date", "actual_delivery_date"}:
            continue
        result.disputed_buyer |= "buyer" in disputed
        record = copies[0].model_copy(update=merged)
        if eligible(record):
            result.lines.append(record)
    result.lines.sort(key=lambda po: (po.promised_date or date.max, po.po_number, po.po_line))
    return result


def delivery_history(supplier_id, part, site, as_of):
    """Compare the same supplier, ordered material and receiving site using completed, dated lines."""
    if not site:
        return None
    observed = []
    for (po, line), rows in current_order_lines(supplier_id, as_of).items():
        if not po or line is None or not all(r.material_id == part and r.plant == site for r in rows):
            continue
        terms = {(r.promised_date, r.actual_delivery_date) for r in rows}
        completed = {"delivered", "received", "fully_received", "closed", "complete", "completed"}
        if len(terms) != 1 or any((r.status or "").strip().lower().replace(" ", "_") not in completed for r in rows):
            continue
        promise, arrived = terms.pop()
        if not promise or not arrived or arrived > as_of or any(r.order_date and min(promise, arrived) < r.order_date for r in rows):
            continue
        observed.append(dict(po=po, line=line, promised=promise.isoformat(), arrived=arrived.isoformat(),
                             days_from_promise=(arrived - promise).days, source=rows[0].source))
    if not observed:
        return None
    observed.sort(key=lambda r: (r["arrived"], r["po"], r["line"]), reverse=True)
    return dict(material=part, site=site, count=len(observed), on_time=sum(r["days_from_promise"] <= 0 for r in observed),
                first=observed[-1]["arrived"], last=observed[0]["arrived"], recent=observed[:5])


def order_value(orders):
    """Whole value of identified lines in their contract currency; no inferred FX or loss."""
    currencies = {o.currency for o in orders}
    if (len(currencies) != 1 or not orders[0].currency or
            any(o.quantity is None or o.quantity < 0 or o.unit_price is None or o.unit_price < 0 for o in orders)):
        return None
    amount = sum(Decimal(str(o.quantity)) * Decimal(str(o.unit_price)) for o in orders)
    return {"amount": float(amount), "currency": orders[0].currency}


def sample_window(offers, as_of, orders):
    """A sample can inform qualification. Only compare an unambiguous quoted window to a dated open line."""
    durations = {offer.get("days") for offer in offers}
    if len(durations) != 1 or None in durations or not orders:
        return None
    first_delivery = min(po.promised_date for po in orders)
    if first_delivery < as_of:
        return None
    window = dict(as_of=as_of.isoformat(), sample_days=durations.pop(), first_delivery=first_delivery.isoformat(),
                  source=offers[0]["source"])
    return project_sample_date(window, as_of)


def project_sample_date(window, requested_on):
    """Apply the recorded offer to a date the buyer is considering, without modifying the decision."""
    if requested_on < date.fromisoformat(window["as_of"]):
        raise ValueError("Choose a date on or after this decision’s evidence snapshot.")
    try:
        sample_by = requested_on + timedelta(days=window["sample_days"])
    except OverflowError:
        raise ValueError("That date is outside the supported calendar.") from None
    first = date.fromisoformat(window["first_delivery"])
    return dict(window, requested_on=requested_on.isoformat(), sample_by=sample_by.isoformat(),
                days_between=(first - sample_by).days,
                request_by_for_delivery=(first - timedelta(days=window["sample_days"])).isoformat())


def mentions(text, identifiers):
    return any(re.search(r"(?<![\w-])" + re.escape(ref) + r"(?![\w-])", text, re.I)
               for ref in identifiers if ref)


def scoped_samples(documents, targets, other_articles, component_name):
    """Attribute a supplier's sample terms to this article or an explicitly related alternative."""
    subject_words = {w.rstrip("s") for w in re.findall(r"[a-z]{4,}", (component_name or "").lower())}
    subject_words -= {"part", "unit", "supplier", "component", "assembly", "with", "without", "finished"}
    offers = []
    for doc in documents:
        text = " ".join(doc.text.split())
        several_articles = mentions(text, other_articles)
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            terms = re.search(r"\bcan be sampled within (\d{1,4}) (calendar |business |working )?days?\b",
                              sentence, re.I)
            if not terms or mentions(sentence, other_articles):
                continue
            named = mentions(sentence, targets)
            sample_words = {w.rstrip("s") for w in re.findall(r"[a-z]{4,}", sentence.lower())}
            related = (not several_articles and bool(subject_words & sample_words)
                       and re.search(r"\b(?:alternative|replacement)\b", sentence, re.I))
            if named or related:
                offer = dict(text=sentence, source=doc.path)
                if not terms[2] or terms[2].lower() == "calendar ":
                    offer["days"] = int(terms[1])
                offers.append(offer)
    return offers


def regulatory_option(issue, plan=None):
    if not relevant(issue) or issue.status == "resolved":
        return None
    supplier = next(iter(find_rows(m.Supplier, supplier_id=issue.supplier_id)), None)
    component = issue.part_id
    mappings = latest_by(find_rows(m.SupplierPartMap, supplier_id=issue.supplier_id), "component_id") if supplier else []
    allocations = latest_by(find_rows(m.SupplierPartMap, component_id=component), "supplier_id") if component else []
    by_part = {s.component_id: s for s in mappings}
    source = by_part.get(component)
    with session() as db:
        changes = db.exec(select(m.FieldChange).where(m.FieldChange.table_name == "supplier_part_map",
                          m.FieldChange.row_id.in_([row.id for row in mappings])).order_by(m.FieldChange.id.desc())).all()
    assemblies = {a for row, a, _ in fitted_components(mappings, changes) if row.component_id == component}
    assembly = next(iter(assemblies)) if len(assemblies) == 1 else ""
    target = assembly or component
    mapping = by_part.get(target)
    material = next(iter(find_rows(m.Material, component_id=target)), None)
    name = material.description if material and material.description else target or "this component"
    subject = next(iter(find_rows(m.Material, component_id=component)), None) if assembly else material
    component_name = subject.description if subject and subject.description else component
    documents = {d.path: d for d in all_rows(m.Document)}
    proof = list(issue.evidence or [])
    part_refs = {target, mapping.supplier_ref if mapping else ""} - {""}
    other_refs = {ref for p in mappings if p.component_id not in {component, target}
                  for ref in (p.component_id, p.supplier_ref) if ref}
    for alias in find_rows(m.Alias, kind="part"):
        origin = documents.get(alias.source)
        if supplier and origin and origin.supplier_id == supplier.supplier_id and (alias.confirmed or alias.confidence == 1):
            if alias.canonical_id == target:
                part_refs.add(alias.alias)
                proof.append(dict(evidence(alias.source, alias.alias, f"{alias.alias} = {target}"), source_kind="Supplier evidence"))
            elif alias.canonical_id != component:
                other_refs.add(alias.alias)
    notice_lines = []
    for doc in documents.values():
        if not supplier or doc.kind != "notice" or doc.supplier_id != supplier.supplier_id:
            continue
        # Bind timing to the same sentence as the article. A notice may cover several unrelated lines.
        for sentence in re.split(r"(?<=[.!?])\s+", " ".join(doc.text.split())):
            if mentions(sentence, part_refs):
                notice_lines.append((doc, sentence))
                proof.append(dict(evidence(doc.path, "supplier article", sentence), source_kind="Supplier evidence"))
    history = [c for c in changes if mapping and c.table_name == "supplier_part_map" and c.row_id == mapping.id
               and c.field == "lead_time_days" and c.new_value == str(mapping.lead_time_days)]
    delta = history[0] if history else None
    previous = int(delta.old_value) if delta and (delta.old_value or "").isdigit() else None
    longer = previous is not None and mapping.lead_time_days > previous
    facts = []
    for row in {r.id: r for r in [source, mapping] if r}.values():
        proof.append(dict(evidence(row.source, f"{row.supplier_id}/{row.component_id}", row.note), source_kind="Supplier evidence"))
    if assembly:
        facts.append(f"{component} is fitted into {assembly} by the supplier.")
    if longer:
        facts.append(f"Lead time increased from {previous} to {mapping.lead_time_days} days (+{mapping.lead_time_days - previous}).")
        proof.append(dict(evidence(delta.source, delta.entity_key, facts[-1]), source_kind="Supplier evidence"))
    elif mapping and mapping.lead_time_days is not None:
        facts.append(f"Recorded lead time is {mapping.lead_time_days} days.")
    cutoffs = set()
    for notice, sentence in notice_lines:
        match = re.search(r"\bdelivery after (\d{4}-\d{2}-\d{2})\b", sentence, re.I)
        if match:
            try:
                cutoffs.add(date.fromisoformat(match[1]))
            except ValueError:
                continue
            proof.append(dict(evidence(notice.path, "delivery window", sentence), source_kind="Supplier evidence"))
    cutoff = next(iter(cutoffs)) if len(cutoffs) == 1 else None
    uncertainty = []
    disputed_share = source and source.share == 1 and any(
        r.supplier_id != source.supplier_id and (r.share is None or r.share > 0) for r in allocations)
    if disputed_share:
        uncertainty.append("Sourcing records also list another supplier for this component. Verify the allocation.")
        proof.extend(dict(evidence(r.source, f"{r.supplier_id}/{component}", f"Recorded share {r.share}"),
                          source_kind="Supplier evidence") for r in allocations if r.supplier_id != source.supplier_id)
    if source and source.share is not None and not disputed_share:
        facts.append(f"{supplier.name or supplier.supplier_id} supplies {source.share:.0%} of {component}.")
    if len(cutoffs) > 1:
        uncertainty.append("Supplier evidence gives different effective dates. Confirm the delivery window.")
    excluded = any(re.search(r"\b(?:unaffected|exempt|not affected|no longer (?:restricted|affected))\b", line, re.I)
                   for _, line in notice_lines)
    if excluded:
        uncertainty.append("A linked notice excludes this article. Confirm which terms apply.")
        cutoff = None
    restricted = any(re.search(r"\b(?:licen[cs]e|restricted|suspended|affected|move out|delayed)\b", line, re.I)
                     for _, line in notice_lines)
    qualify = bool(source and mapping and source.share == 1 and not uncertainty and (restricted or longer)
                   and any(a.get("type") == "second_source" for a in issue.suggested_actions))
    action = "Start qualification review" if qualify else "Check applicability"
    subject_name = (supplier.name or supplier.supplier_id) if supplier else issue.supplier_id or name
    label = f"Check supply exposure for {subject_name}"
    if qualify:
        label = f"Qualify a backup source for {component_name or name}"
    notices = [documents[path] for path in sorted({d.path for d, _ in notice_lines})]
    sample_offers = scoped_samples(notices, part_refs | {component}, other_refs, component_name) if qualify else []
    proof.extend(dict(evidence(o["source"], "supplier sample terms", o["text"]), source_kind="Supplier evidence")
                 for o in sample_offers)
    facts.append(f"Applies to deliveries after {cutoff.isoformat()}." if cutoff else "Effective date unknown.")
    if sample_offers:
        brief = ("Ask Engineering and Quality to assess the incumbent’s sample terms. "
                 "In parallel, identify an independent supplier and verify qualification, capacity and lead time.")
    elif qualify:
        brief = "Identify a qualified alternative and compare capacity, lead time and terms with the incumbent supplier."
    else:
        brief = "Ask the supplier to confirm the affected article, effective date and matching open order lines."
    facts.extend(uncertainty)
    if not qualify:
        facts.append("Verify the notice against a supplier and order line before taking sourcing action.")
    as_of = plan.as_of if plan else issue.created_at
    dated = as_of.date()
    # These are scheduled PO quantities. Partial receipts do not establish a remaining balance.
    scope = OrderScope()
    if supplier and ((mapping and cutoff) or (not qualify and not cutoff)):
        scope = open_orders(supplier.supplier_id, dated, part=target or None, after=cutoff)
    pos = scope.lines if cutoff else []
    verify = scope.lines if not cutoff else []
    if verify and not target:
        # Country-level alerts allow a general check. An article match narrows it to the supplier's relevant lines.
        rated = buyer_risk([("supplier", supplier.supplier_id, supplier.name)], documents)
        matches = [m for factor in rated["signals"] if factor["factor"] == "regulatory_change"
                   for signal in factor["sources"] for m in signal.get("material_matches", [])]
        if matches:
            relevant_parts = {m["part"] for m in matches}
            verify = [po for po in verify if po.material_id in relevant_parts]
            proof.extend(dict(e, source_kind="Customer sourcing record") for m in matches for e in m["evidence"])
    proof.extend(scope.proof)
    uncertainty.extend(scope.notes)
    facts.extend(scope.notes)
    for po in verify[:3]:
        proof.append(dict(evidence(po.source, f"{po.po_number}/{po.po_line}",
                                   f"Open PO for {po.material_id}. Applicability still needs confirmation."),
                          source_kind="Order record"))
    for po in pos:
        key = f"{po.po_number}/{po.po_line}"
        if not any(e["locator"] == key for e in scope.proof):
            proof.append(dict(evidence(po.source, key, f"Promised {po.promised_date}, quantity {po.quantity}, unit price {po.unit_price} {po.currency}"), source_kind="Order record"))
    if pos:
        facts.append("Open order lines in that window include " + ", ".join(f"{p.po_number}/{p.po_line}" for p in pos) + ".")
    sites = {p.plant for p in pos}
    history = delivery_history(supplier.supplier_id, target, next(iter(sites)), dated) if len(sites) == 1 else None
    for line in (history or {}).get("recent", []):
        proof.append(dict(evidence(line["source"], f"{line['po']}/{line['line']}",
            f"Promised {line['promised']}, delivered {line['arrived']}."), source_kind="Completed order"))
    bom = [b for b in find_rows(m.BomLine, child_id=target) if source
           and (not b.valid_from or b.valid_from <= dated) and (not b.valid_to or b.valid_to >= dated)]
    products = {p.product_id: p.name for p in all_rows(m.Product)}
    pids = sorted({b.parent_id for b in bom if b.parent_id in products})
    nodes = [("supplier", issue.supplier_id, subject_name)] if issue.supplier_id else []
    if source and component:
        nodes.append(("part", component, component_name))
        if assembly:
            nodes.append(("part", assembly, name))
        nodes.extend(("product", pid, products[pid]) for pid in pids)
    roles = policy.approvers_for(0)
    buyers = {po.buyer for po in (pos or verify) if po.buyer}
    buyer = next(iter(buyers), "") if len(buyers) <= 1 and not scope.disputed_buyer else None
    assignments = approval_assignments(roles, material.category if material else "", dated,
                                       documents.values(), buyer=buyer)
    owners = signer_names(assignments)
    gate = supplier_release_gate(documents.values(), as_of, material.category if material else "") if qualify else None
    if gate:
        proof.append(dict(gate["evidence"], source_kind="Customer policy"))
        brief += f" Before serial deliveries, obtain {gate['assessment']} and the written releases in the customer policy."
    for path in dict.fromkeys(a["org_source"] for a in assignments if a["org_source"]):
        proof.append(dict(evidence(path, "effective approval assignment"), source_kind="Customer org chart"))
    reference = f"{'qualification' if qualify else 'applicability'}/{issue.supplier_id}/{target or issue.subject_id}"
    if component and assembly:
        reference += f"/{component}"
    for b in bom:
        if b.parent_id in products and b.source:
            proof.append(dict(evidence(b.source, f"{b.parent_id}/{target}", "Effective product BOM"), source_kind="Product BOM"))
    proof = [dict(e, source_kind=source_kind(e, documents)) for e in proof]
    exposure = dict(supplier=(supplier.name or supplier.supplier_id) if supplier else issue.supplier_id,
                    component=component, component_name=component_name, assembly=assembly or None, assembly_name=name if assembly else "",
                    share=source.share if source and not disputed_share else None,
                    lead_days=mapping.lead_time_days if mapping else None, previous_lead_days=previous,
                    uncertainties=uncertainty, qualification_gate=gate, sample_offers=sample_offers, sample_window=sample_window(sample_offers, dated, pos),
                    order_value=order_value(pos),
                    delivery_history=history,
                    packing_closes_before_notice=(plan.deadline.date().isoformat() if plan and cutoff
                        and plan.deadline.date() <= cutoff and assembly else None),
                    effective_after=cutoff.isoformat() if cutoff else None, **buyer_risk(nodes, documents),
                    verification_orders=[dict(po=o.po_number, line=o.po_line, material=o.material_id,
                                              promised=o.promised_date.isoformat() if o.promised_date else None)
                                         for o in verify[:3]],
                    orders=[dict(po=o.po_number, line=o.po_line, status=o.status, promised=o.promised_date.isoformat(),
                                 quantity=o.quantity) for o in pos])
    return dict(exposure=exposure, review_brief=brief, horizon="future", button_label=action, label=label, description="future supply review",
                edition=", ".join(products[p] or p for p in pids) or "Supplier review", product_id=pids[0] if pids else "",
                product_ids=pids, part=target, currency=policy.setup().currency, cost_eur=None, approval_value_eur=0,
                copies_gained=None, remaining_shortfall=None, approvers=owners, approval_roles=roles, assignments=assignments,
                available=bool(owners), needs_approval=True, risk_note=" ".join(facts),
                requirements=(["Technical fit of the offered sample", "Qualification scope and supplier terms"] if sample_offers
                              else ["Qualification scope and supplier terms"] if qualify else ["Supplier or order-line confirmation"]),
                evidence=proof, actions=[dict(type="human_review", reference=reference, supplier_id=issue.supplier_id,
                    part=target, product_id=pids[0] if pids else "", copies_at_risk=None)])
