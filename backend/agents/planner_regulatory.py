"""Convert a documented future-supply finding into an internal, non-spending review."""
from datetime import date
import re
from types import SimpleNamespace
from urllib.parse import urlsplit

from ..core import models as m, policy
from ..core.store import all_rows
from .planner_counts import fitted_components
from .planner_inputs import evidence
from .planner_options import resolve_approvers


def relevant(issue):
    if issue.kind != m.IssueKind.HUMAN_CHECK or issue.raised_by == "planner":
        return False
    actions = issue.suggested_actions or []
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


def regulatory_option(issue, plan=None):
    if not relevant(issue) or issue.status == "resolved":
        return None
    suppliers = {s.supplier_id: s for s in all_rows(m.Supplier)}
    supplier = suppliers.get(issue.supplier_id)
    component = issue.part_id
    mappings = [s for s in all_rows(m.SupplierPartMap) if supplier and s.supplier_id == supplier.supplier_id]
    by_part = {s.component_id: s for s in mappings}
    source = by_part.get(component)
    changes = sorted(all_rows(m.FieldChange), key=lambda c: c.id, reverse=True)
    assemblies = {a for row, a, _ in fitted_components(mappings, changes) if row.component_id == component}
    assembly = next(iter(assemblies)) if len(assemblies) == 1 else ""
    target = assembly or component
    mapping = by_part.get(target)
    materials = {r.component_id: r for r in all_rows(m.Material)}
    material = materials.get(target)
    name = material.description if material and material.description else target or "this component"
    documents = {d.path: d for d in all_rows(m.Document)}
    proof = list(issue.evidence or [])
    refs = {e["path"] for e in proof}
    part_refs = {target, mapping.supplier_ref if mapping else ""} - {""}
    for alias in all_rows(m.Alias):
        origin = documents.get(alias.source)
        if (supplier and origin and origin.supplier_id == supplier.supplier_id and alias.kind == "part"
                and alias.canonical_id == target and (alias.confirmed or alias.confidence == 1)):
            part_refs.add(alias.alias)
            proof.append(dict(evidence(alias.source, alias.alias, f"{alias.alias} = {target}"), source_kind="Supplier evidence"))
    notice_lines = []
    for doc in documents.values():
        if not supplier or doc.kind != "notice" or doc.supplier_id != supplier.supplier_id or doc.path not in refs:
            continue
        # Bind timing to the same sentence as the article. A notice may cover several unrelated lines.
        for sentence in re.split(r"(?<=[.!?])\s+", " ".join(doc.text.split())):
            if any(re.search(r"(?<![\w-])" + re.escape(ref) + r"(?![\w-])", sentence, re.I) for ref in part_refs):
                notice_lines.append((doc, sentence))
    history = [c for c in changes if mapping and c.table_name == "supplier_part_map" and c.row_id == mapping.id
               and c.field == "lead_time_days" and c.new_value == str(mapping.lead_time_days)]
    delta = history[0] if history else None
    previous = int(delta.old_value) if delta and (delta.old_value or "").isdigit() else None
    longer = previous is not None and mapping.lead_time_days > previous
    qualify = bool(source and mapping and source.share == 1 and (notice_lines or longer) and
                   any(a.get("type") == "second_source" for a in issue.suggested_actions))
    action = "Start qualification review" if qualify else "Check applicability"
    label = f"Qualify a backup source for {name}" if qualify else f"Check regulatory exposure for {supplier.name if supplier else name}"
    facts = []
    for row in {r.id: r for r in [source, mapping] if r}.values():
        proof.append(dict(evidence(row.source, f"{row.supplier_id}/{row.component_id}", row.note), source_kind="Supplier evidence"))
    if source and source.share is not None:
        facts.append(f"{supplier.name or supplier.supplier_id} supplies {source.share:.0%} of {component}.")
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
    facts.append(f"Applies to deliveries after {cutoff.isoformat()}." if cutoff else "Effective date unknown.")
    if not qualify:
        facts.append("Verify the notice against a supplier and order line before taking sourcing action.")
    pos = sorted((o for o in all_rows(m.PurchaseOrder) if mapping and o.supplier_id == mapping.supplier_id
                  and o.material_id == target and (o.status or "").lower() in {"open", "awaiting_approval", "pending_approval"}
                  and cutoff and o.promised_date and o.promised_date > cutoff), key=lambda o: (o.po_number, o.po_line or 0))
    for po in pos:
        proof.append(dict(evidence(po.source, f"{po.po_number}/{po.po_line}", f"Promised {po.promised_date}"), source_kind="Order record"))
    if pos:
        facts.append("Open order lines in that window include " + ", ".join(f"{p.po_number}/{p.po_line}" for p in pos) + ".")
    as_of = plan.as_of if plan else issue.created_at
    pids = sorted({b.parent_id for b in all_rows(m.BomLine) if source and b.child_id == target
                   and (not b.valid_from or b.valid_from <= as_of.date()) and (not b.valid_to or b.valid_to >= as_of.date())})
    products = {p.product_id: p.name for p in all_rows(m.Product)}
    pids = [p for p in pids if p in products]
    roles = policy.approvers_for(0)
    owners = resolve_approvers(roles, material.category if material else "", SimpleNamespace(as_of=as_of),
                               documents.values(), buyer=pos[0].buyer if pos else policy.setup().demo_buyer)
    if roles == ["Buyer"] and owners == roles and policy.setup().demo_buyer:
        owners = [policy.setup().demo_buyer]
    reference = f"{'qualification' if qualify else 'applicability'}/{issue.supplier_id}/{target or issue.subject_id}"
    proof = [dict(e, source_kind=source_kind(e, documents)) for e in proof]
    return dict(horizon="future", button_label=action, label=label, description="future supply review",
                edition=", ".join(products[p] or p for p in pids) or "Supplier review", product_id=pids[0] if pids else "",
                product_ids=pids, part=target, currency=policy.setup().currency, cost_eur=None, approval_value_eur=0,
                copies_gained=None, remaining_shortfall=None, approvers=owners, approval_roles=roles,
                available=bool(owners), needs_approval=True, risk_note=" ".join(facts),
                requirements=["Qualification scope and supplier terms"] if qualify else ["Supplier or order-line confirmation"],
                evidence=proof, actions=[dict(type="human_review", reference=reference, supplier_id=issue.supplier_id,
                    part=target, product_id=pids[0] if pids else "", copies_at_risk=None)])
