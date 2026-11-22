"""Convert a documented future-supply finding into an internal, non-spending review."""
from datetime import date
from decimal import Decimal
import re
from types import SimpleNamespace
from urllib.parse import urlsplit

from sqlmodel import select

from ..core import models as m, policy
from ..core.store import all_rows, find_rows, latest_by, session
from .planner_counts import fitted_components
from .planner_inputs import evidence
from .planner_options import resolve_approvers


def relevant(issue):
    if issue.raised_by == "planner":
        return False
    actions = issue.suggested_actions or []
    if issue.kind != m.IssueKind.HUMAN_CHECK:
        # The Risk Rater routes high supplier grades as copies_at_risk even for
        # a country-level regulatory watch. Preserve its explicit compliance task.
        return (issue.raised_by == "risk" and issue.subject_type == "supplier"
                and issue.kind == m.IssueKind.COPIES_AT_RISK
                and any(e.get("locator", "").startswith("regulations:") for e in issue.evidence)
                and any(a.get("type") == "human_review" for a in actions))
    return any(a.get("type") == "second_source" or
               (a.get("type") == "human_review" and
                ("compliance" in a.get("label", "").lower() or any(
                    e.get("locator", "").startswith("regulations:") for e in issue.evidence)))
               for a in actions)


def source_kind(item, documents):
    path = item["path"]
    quote = item.get("quote", "").lower()
    if "offline" in quote or item.get("offline") is True:
        return "Offline example"
    host = (urlsplit(path).hostname or "").lower()
    if host:
        for domain in ("federalregister.gov", "eur-lex.europa.eu", "wto.org"):
            if host == domain or host.endswith("." + domain):
                return "Official publication"
        if host == "polymarket.com" or host.endswith(".polymarket.com"):
            return "Market odds"
        if host == "news.google.com" or item.get("locator", "").startswith(("gnews:", "gdelt:")):
            return "News coverage"
    doc = documents.get(path)
    if doc:
        if doc.kind == "news":
            return "News coverage"
        if doc.supplier_id:
            return "Supplier evidence"
    return item.get("source_kind", "Unknown provenance")


EXTERNAL_FACTORS = {"trade_news", "external_politics", "weather", "telemetry", "regulatory_change"}


def supplier_signals(supplier_id, documents):
    """Use only drivers retained on this supplier's latest rating in this database."""
    with session() as db:
        query = select(m.RiskScore).where(m.RiskScore.entity_type == "supplier",
                                          m.RiskScore.entity_id == supplier_id).order_by(m.RiskScore.id.desc())
        score = db.exec(query.limit(1)).first()
        if not score:
            return dict(supplier_rating=None, signals=[], other_drivers=[], last_grade_change=None)
        previous = db.exec(query.where(m.RiskScore.grade != score.grade).limit(1)).first()
        # Compare the two records either side of the grade transition, even
        # when later evidence has changed contributions within the same grade.
        first = db.exec(select(m.RiskScore).where(m.RiskScore.entity_type == "supplier",
                        m.RiskScore.entity_id == supplier_id, m.RiskScore.id > previous.id
                        ).order_by(m.RiskScore.id).limit(1)).one() if previous else None
    signals = []
    for reason in score.reasons:
        factor = reason.get("factor")
        if factor not in EXTERNAL_FACTORS:
            continue
        sources = []
        for item in reason.get("evidence", []):
            path = item.get("path", "")
            if not path:
                continue
            quote = item.get("quote", "")
            doc = documents.get(path)
            if not doc and quote:
                # A news feed can contain multiple dated stories in the same file.
                doc = next((d for d in documents.values() if d.path.startswith(path + "#")
                            and quote.strip() in d.text), None)
            proof = dict(item, source_kind=source_kind(item, documents))
            origin = ("Offline example" if proof["source_kind"] == "Offline example" else
                      "Portal record" if doc or not urlsplit(path).scheme else
                      "Live at collection" if "live " in quote.lower() else "Collection mode unknown")
            sources.append(dict(evidence=proof, origin=origin, title=doc.title if doc else "",
                                published=doc.published_at.date().isoformat() if doc and doc.published_at else None))
        if sources:
            signals.append(dict(factor=factor, text=reason["text"], points=reason["points"], sources=sources))
    retained = {s["factor"] for s in signals}
    breakdown = next((reason["breakdown"] for reason in score.reasons if "breakdown" in reason), {})
    other = [dict(factor=k, points=v) for k, v in sorted(breakdown.items())
             if k in EXTERNAL_FACTORS - retained and v > 0]
    transition = None
    if previous and first:
        old = next((r["breakdown"] for r in previous.reasons if "breakdown" in r), {})
        new = next((r["breakdown"] for r in first.reasons if "breakdown" in r), {})
        transition = dict(previous=previous.rating, current=first.rating,
                          drivers=[dict(factor=k, before=old[k], after=new[k]) for k in sorted(old.keys() & new.keys())])
    return dict(supplier_rating=score.rating, signals=signals, other_drivers=other, last_grade_change=transition)


def order_value(orders):
    """Whole value of identified lines in their contract currency; no inferred FX or loss."""
    currencies = {o.currency for o in orders}
    if (len(currencies) != 1 or not orders[0].currency or
            any(o.quantity is None or o.quantity < 0 or o.unit_price is None or o.unit_price < 0 for o in orders)):
        return None
    amount = sum(Decimal(str(o.quantity)) * Decimal(str(o.unit_price)) for o in orders)
    return {"amount": float(amount), "currency": orders[0].currency}


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
    documents = {d.path: d for d in all_rows(m.Document)}
    proof = list(issue.evidence or [])
    part_refs = {target, mapping.supplier_ref if mapping else ""} - {""}
    for alias in find_rows(m.Alias, kind="part", canonical_id=target):
        origin = documents.get(alias.source)
        if (supplier and origin and origin.supplier_id == supplier.supplier_id and alias.kind == "part"
                and alias.canonical_id == target and (alias.confirmed or alias.confidence == 1)):
            part_refs.add(alias.alias)
            proof.append(dict(evidence(alias.source, alias.alias, f"{alias.alias} = {target}"), source_kind="Supplier evidence"))
    notice_lines = []
    for doc in documents.values():
        if not supplier or doc.kind != "notice" or doc.supplier_id != supplier.supplier_id:
            continue
        # Bind timing to the same sentence as the article. A notice may cover several unrelated lines.
        for sentence in re.split(r"(?<=[.!?])\s+", " ".join(doc.text.split())):
            if any(re.search(r"(?<![\w-])" + re.escape(ref) + r"(?![\w-])", sentence, re.I) for ref in part_refs):
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
    label = f"Qualify a backup source for {name}" if qualify else f"Check regulatory exposure for {supplier.name if supplier else name}"
    sample_offers = []
    if qualify:
        for path in sorted({d.path for d, _ in notice_lines}):
            sentences = re.split(r"(?<=[.!?])\s+", " ".join(documents[path].text.split()))
            for line in sentences:
                if re.search(r"\bcan be sampled within \d+ days?\b", line, re.I):
                    sample_offers.append(dict(text=line, source=path))
                    proof.append(dict(evidence(path, "supplier sample terms", line), source_kind="Supplier evidence"))
    facts.append(f"Applies to deliveries after {cutoff.isoformat()}." if cutoff else "Effective date unknown.")
    if sample_offers:
        brief = "Check the supplier’s sample terms with Engineering and Quality. Record fit, release criteria and lead time before committing spend."
    elif qualify:
        brief = "Identify a qualified alternative and compare capacity, lead time and terms with the incumbent supplier."
    else:
        brief = "Ask the supplier to confirm the affected article, effective date and matching open order lines."
    facts.extend(uncertainty)
    if not qualify:
        facts.append("Verify the notice against a supplier and order line before taking sourcing action.")
    as_of = plan.as_of if plan else issue.created_at
    dated = as_of.date()
    pos = []
    if mapping and cutoff:
        with session() as db:
            orders = db.exec(select(m.PurchaseOrder).where(m.PurchaseOrder.supplier_id == mapping.supplier_id,
                             m.PurchaseOrder.material_id == target, m.PurchaseOrder.promised_date > cutoff)
                             .order_by(m.PurchaseOrder.po_number, m.PurchaseOrder.po_line)).all()
        # Quantities represent whole ordered lines. Partial receipts do not establish a remaining balance.
        pos = [po for po in orders if (po.status or "").lower() in {"open", "awaiting_approval", "pending_approval"}
               and (not po.order_date or po.order_date <= dated)
               and (not po.actual_delivery_date or po.actual_delivery_date > dated)]
    for po in pos:
        proof.append(dict(evidence(po.source, f"{po.po_number}/{po.po_line}", f"Promised {po.promised_date}, quantity {po.quantity}, unit price {po.unit_price} {po.currency}"), source_kind="Order record"))
    if pos:
        facts.append("Open order lines in that window include " + ", ".join(f"{p.po_number}/{p.po_line}" for p in pos) + ".")
    bom = [b for b in find_rows(m.BomLine, child_id=target) if source
           and (not b.valid_from or b.valid_from <= dated) and (not b.valid_to or b.valid_to >= dated)]
    products = {p.product_id: p.name for p in all_rows(m.Product)}
    pids = sorted({b.parent_id for b in bom if b.parent_id in products})
    roles = policy.approvers_for(0)
    owners = resolve_approvers(roles, material.category if material else "", SimpleNamespace(as_of=as_of),
                               documents.values(), buyer=pos[0].buyer if pos else policy.setup().demo_buyer)
    reference = f"{'qualification' if qualify else 'applicability'}/{issue.supplier_id}/{target or issue.subject_id}"
    for b in bom:
        if b.parent_id in products and b.source:
            proof.append(dict(evidence(b.source, f"{b.parent_id}/{target}", "Effective product BOM"), source_kind="Product BOM"))
    proof = [dict(e, source_kind=source_kind(e, documents)) for e in proof]
    exposure = dict(supplier=(supplier.name or supplier.supplier_id) if supplier else issue.supplier_id,
                    component=component, assembly=assembly or None, share=source.share if source and not disputed_share else None,
                    lead_days=mapping.lead_time_days if mapping else None, previous_lead_days=previous,
                    uncertainties=uncertainty, sample_offers=sample_offers, order_value=order_value(pos),
                    effective_after=cutoff.isoformat() if cutoff else None, **supplier_signals(issue.supplier_id, documents),
                    orders=[dict(po=o.po_number, line=o.po_line, promised=o.promised_date.isoformat(),
                                 quantity=o.quantity) for o in pos])
    return dict(exposure=exposure, review_brief=brief, horizon="future", button_label=action, label=label, description="future supply review",
                edition=", ".join(products[p] or p for p in pids) or "Supplier review", product_id=pids[0] if pids else "",
                product_ids=pids, part=target, currency=policy.setup().currency, cost_eur=None, approval_value_eur=0,
                copies_gained=None, remaining_shortfall=None, approvers=owners, approval_roles=roles,
                available=bool(owners), needs_approval=True, risk_note=" ".join(facts),
                requirements=(["Technical fit of the offered sample", "Qualification scope and supplier terms"] if sample_offers
                              else ["Qualification scope and supplier terms"] if qualify else ["Supplier or order-line confirmation"]),
                evidence=proof, actions=[dict(type="human_review", reference=reference, supplier_id=issue.supplier_id,
                    part=target, product_id=pids[0] if pids else "", copies_at_risk=None)])
