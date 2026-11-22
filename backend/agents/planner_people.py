"""Resolve approval roles against the org chart effective at the decision snapshot."""
from datetime import date
import re


def _fold(value):
    return (value or "").casefold().replace("’", "'").strip()


def _effective_policy(documents, on, topic):
    """A dated policy may supersede older terms even when its new wording is unrecognised."""
    candidates = []
    for doc in documents:
        if doc.kind != "policy" or not re.search(topic, doc.text, re.I):
            continue
        stamp = re.search(r"\beffective\s*:?\s*(\d{4}-\d{2}-\d{2})\b", doc.text, re.I)
        if stamp:
            try:
                effective = date.fromisoformat(stamp[1])
            except ValueError:
                continue
            if effective <= on:
                candidates.append((effective, doc))
    if candidates:
        newest = max(d for d, _ in candidates)
        active = [doc for d, doc in candidates if d == newest]
        if len({" ".join(doc.text.split()) for doc in active}) == 1:
            return newest, active[0]
    return None


def _effective_chart(documents, on):
    dated = []
    for doc in documents:
        if doc.kind != "org_chart":
            continue
        # A future appointee elsewhere on the page does not date the entire org chart.
        header = next((line for line in doc.text.splitlines() if line.strip()), "")
        stamp = re.search(r"\d{4}-\d{2}-\d{2}", header)
        if not stamp:
            continue
        try:
            effective = date.fromisoformat(stamp[0])
        except ValueError:
            continue
        if effective <= on:
            dated.append((effective, doc))
    if not dated:
        return None
    newest = max(d for d, _ in dated)
    active = [doc for d, doc in dated if d == newest]
    # A single content version is necessary to attribute a specific signer.
    return active[0] if len({doc.text.strip() for doc in active}) == 1 else None


def _candidates(chart, role, on, category, po_buyer):
    title = {"cpo": "chief procurement officer", "cfo": "chief financial officer"}.get(_fold(role), _fold(role))
    lines = [line.strip() for line in chart.text.splitlines() if line.strip()]
    candidates = []
    vacant = False
    for i, line in enumerate(lines[1:], 1):
        if not re.search(r"(?<!\w)" + re.escape(title) + r"(?!\w)", _fold(line)):
            continue
        if _fold(role) == "cfo" and "(" in line:
            person = re.search(r"(?:^|,\s*|and )([\wÀ-ž.-]+ [\wÀ-ž.-]+) \(Chief Financial Officer\)", line, re.I)
            if person:
                candidates.append(person[1])
            continue
        if "(" in line and _fold(role) != "buyer":
            continue
        raw = lines[i-1]
        person = raw.split(" (from")[0].strip()
        if _fold(role) == "buyer":
            if po_buyer and _fold(person) != _fold(po_buyer):
                continue
            if not po_buyer and category and _fold(category) not in _fold(line):
                continue
        if _fold(role) == "category lead" and (not category or _fold(category) not in _fold(line)):
            continue
        begins = re.search(r"\(from (\d{4}-\d\d-\d\d)\)", raw, re.I)
        if begins:
            try:
                starts = date.fromisoformat(begins[1])
            except ValueError:
                continue
            if starts > on:
                vacant = True
                continue
        if _fold(person) in {"tbd", "vacant", "open", "unassigned"}:
            vacant = True
        else:
            candidates.append(person)
    return sorted(set(candidates)), vacant


def approval_assignments(roles, category, on, documents, buyer=""):
    docs = list(documents)
    chart = _effective_chart(docs, on)
    result = []
    for role in roles:
        assignment = dict(required_role=role, signing_role=role, person=None, org_source=chart.path if chart else None,
                          basis="A named signer needs confirmation against the effective org chart.")
        if _fold(role) == "buyer" and buyer is None:
            assignment["basis"] = "The purchasing records name different buyers. Confirm who owns this action."
        elif chart:
            matches, vacancy = _candidates(chart, role, on, category, buyer)
            if len(matches) == 1:
                assignment.update(person=matches[0], basis="Named PO buyer and effective org chart." if
                                  _fold(role) == "buyer" and buyer else "Effective org chart for this role and category.")
            elif len(matches) > 1:
                assignment["basis"] = "Several people hold this role. Confirm the responsible signer."
            elif vacancy and _fold(role) == "category lead":
                current = _effective_policy(docs, on, r"\bexceptions\b")
                exception = current[1] if current else None
                heads, _ = _candidates(chart, "Head of Procurement", on, "", "")
                if exception and len(heads) == 1 and re.search(
                        r"\bExceptions must be (?:documented and )?approved by the Head of Procurement\b",
                        " ".join(exception.text.split()), re.I):
                    assignment.update(signing_role="Head of Procurement", person=heads[0], policy_source=exception.path,
                                      basis="The category seat is vacant on this date. The policy routes exceptions to the head of procurement.")
        result.append(assignment)
    return result


def signer_names(assignments):
    return list(dict.fromkeys(a["person"] or a["required_role"] for a in assignments))


def vacant_category_leads(documents, on, categories):
    """Quote only seats that are explicitly vacant or have a future start in the effective chart."""
    chart = _effective_chart(documents, on)
    vacancies = {}
    if chart:
        lines = [line.strip() for line in chart.text.splitlines() if line.strip()]
        for category in sorted(c for c in categories if c):
            matches, vacant = _candidates(chart, "Category lead", on, category, "")
            if vacant and not matches:
                seat = next((lines[i-1] + " · " + line for i, line in enumerate(lines) if i and
                    "category lead" in _fold(line) and _fold(category) in _fold(line)), None)
                if seat:
                    vacancies[category] = (chart.path, seat)
    return vacancies


def resolve_approvers(roles, category, plan, documents, buyer=""):
    return signer_names(approval_assignments(roles, category, plan.as_of.date(), documents, buyer))


def supplier_release_gate(documents, as_of, category):
    """Read a dated serial-release requirement only when the customer's policy binds an assessment and signers."""
    from .planner_inputs import evidence

    docs = list(documents)
    active = _effective_policy(docs, as_of.date(), r"\b(?:serial deliveries|serial production)\b")
    if not active:
        return None
    effective, doc = active
    terms = re.search(r"[^.]{0,100}\b(?:serial deliveries|serial production)\b[^.]{0,60}?"
                      r"after (?:a |an )?successful ([\w /-]+?) and written release by ([^.]+)\.",
                      " ".join(doc.text.split()), re.I)
    if not terms:
        return None
    assessment, signers = terms[1].strip(), terms[2].strip()
    roles = [re.sub(r"^(?:the\s+)?(?:responsible\s+)?", "", r.strip(), flags=re.I)
             for r in re.split(r",|\s+and\s+", signers, flags=re.I)]
    if not all(roles):
        return None
    routes = approval_assignments(roles, category, as_of.date(), docs)
    return dict(assessment=assessment, signers=routes, effective=effective.isoformat(),
                evidence=evidence(doc.path, "new supplier serial release", terms[0].strip()))
