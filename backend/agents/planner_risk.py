"""Project the Risk Rater’s retained grades and source provenance onto buyer decisions."""
from urllib.parse import urlsplit

from sqlmodel import select

from ..core import models as m
from ..core.store import session


def source_kind(item, documents, matched_doc=None):
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
    doc = matched_doc or documents.get(path)
    if doc:
        if doc.kind == "news":
            return "News coverage"
        if doc.supplier_id:
            return "Supplier evidence"
    return item.get("source_kind", "Unknown provenance")


EXTERNAL_FACTORS = {"trade_news", "external_politics", "weather", "telemetry", "regulatory_change"}


def buyer_risk(nodes, documents):
    """Read the Rater's own grades for the supplier and the entities linked by customer records."""
    grades = []
    score = previous = first = None
    with session() as db:
        for tier, entity_id, name in nodes:
            query = select(m.RiskScore).where(m.RiskScore.entity_type == tier, m.RiskScore.entity_id == entity_id)
            latest = db.exec(query.order_by(m.RiskScore.id.desc()).limit(1)).first()
            if not latest or not latest.rating:
                continue
            last = db.exec(query.where(m.RiskScore.grade != latest.grade).order_by(m.RiskScore.id.desc()).limit(1)).first()
            reason = next((r["text"] for r in latest.reasons if r.get("text")), "")
            grades.append(dict(tier=tier, id=entity_id, name=name or entity_id, rating=latest.rating,
                               previous=last.rating if last else None, reason=reason))
            if tier == "supplier":
                score, previous = latest, last
                if previous:
                    # Compare the records either side of the transition, even if contributions changed afterwards.
                    first = db.exec(query.where(m.RiskScore.id > previous.id).order_by(m.RiskScore.id).limit(1)).one()
    if not score:
        return dict(supplier_rating=None, signals=[], other_drivers=[], last_grade_change=None, rating_path=grades)
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
            proof = dict(item, source_kind=source_kind(item, documents, doc))
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
    return dict(supplier_rating=score.rating, signals=signals, other_drivers=other, last_grade_change=transition,
                rating_path=grades)
