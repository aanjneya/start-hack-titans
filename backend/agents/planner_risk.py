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
    if item.get("locator", "").startswith("telemetry:"):
        return "Shipment telemetry"
    if item.get("locator", "").startswith("weather:"):
        return "Weather observation"
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


def signal_source(item, documents, observation):
    path = item.get("path", "")
    quote = item.get("quote", "")
    doc = documents.get(path)
    if not doc and quote:
        doc = next((d for d in documents.values() if d.path.startswith(path + "#") and quote.strip() in d.text), None)
    kind = source_kind(item, documents, doc)
    provider = observation.get("provider") or item.get("locator", "").partition(":")[0]
    if observation.get("live") is False or kind == "Offline example":
        origin = kind = "Offline example"
    elif provider == "telemetry":
        origin = "Customer shipment feed"
    elif doc or provider == "portal_news" or not urlsplit(path).scheme:
        origin = "Portal record"
    elif observation.get("live") is True or "live " in quote.lower():
        origin = "Live at collection"
    else:
        origin = "Collection mode unknown"
    return dict(evidence=dict(item, source_kind=kind), origin=origin, provider=provider,
                market=observation.get("market"),
                last_known_at=observation.get("last_known_at"),
                material_matches=observation.get("material_matches", []),
                title=observation.get("title") or (doc.title if doc else ""), scope=observation.get("scope", ""),
                observed_at=observation.get("observed_at"),
                published=observation.get("published") or (doc.published_at.date().isoformat() if doc and doc.published_at else None))


def retained_signals(score, documents):
    breakdown = next((r["breakdown"] for r in score.reasons if "breakdown" in r), {})
    observations = next((r["signal_inputs"] for r in score.reasons if "signal_inputs" in r), {})
    reasons = {r["factor"]: r for r in score.reasons if r.get("factor") in EXTERNAL_FACTORS}
    signals = []
    for factor in sorted(reasons.keys() | observations.keys()):
        reason = reasons.get(factor, {})
        inputs = observations.get(factor, [])
        if inputs:
            sources = [signal_source(o["evidence"], documents, o) for o in inputs]
            text = inputs[0]["summary"]
        else:
            sources = [signal_source(e, documents, {}) for e in reason.get("evidence", []) if e.get("path")]
            text = reason.get("text", "")
        if sources:
            signals.append(dict(factor=factor, text=text, points=breakdown.get(factor, reason.get("points", 0)), sources=sources))
    retained = {s["factor"] for s in signals}
    other = [dict(factor=k, points=v) for k, v in sorted(breakdown.items()) if k in EXTERNAL_FACTORS - retained and v > 0]
    return sorted(signals, key=lambda s: -s["points"]), other


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
    signals, other = retained_signals(score, documents)
    transition = None
    if previous and first:
        old = next((r["breakdown"] for r in previous.reasons if "breakdown" in r), {})
        new = next((r["breakdown"] for r in first.reasons if "breakdown" in r), {})
        transition = dict(previous=previous.rating, current=first.rating,
                          drivers=[dict(factor=k, before=old[k], after=new[k]) for k in sorted(old.keys() & new.keys())])
        before = market_readings(previous)
        after = market_readings(first)
        for signal in signals:
            for source in signal["sources"]:
                market = source.get("market")
                key = market_key(source)
                if key in before and key in after and market and market["yes_pct"] == after[key] != before[key]:
                    source["market"] = dict(market, previous_yes_pct=before[key])
    return dict(supplier_rating=score.rating, signals=signals, other_drivers=other, last_grade_change=transition,
                score_basis=next((r["score_basis"] for r in score.reasons if r.get("score_basis")), None),
                rating_path=grades)


def market_key(source):
    market = source.get("market") or {}
    # A live instrument and a replay example must never form a time series together.
    identity = market.get("question_id")
    if not identity and source.get("origin") == "Offline example":
        identity = (source["evidence"]["path"], source["title"])
    return (source.get("origin"), identity) if identity else None


def market_readings(score):
    readings = {}
    for reason in score.reasons:
        for obs in reason.get("signal_inputs", {}).get("external_politics", []):
            if not obs.get("market") or obs.get("provider") != "polymarket":
                continue
            source = signal_source(obs["evidence"], {}, obs)
            key = market_key(source)
            if key:
                readings[key] = obs["market"]["yes_pct"]
    return readings


def same_review_option(first, second):
    """Refreshing the same instrument at the same price leaves its existing review actionable."""
    if first == second:
        return True
    if not first or not second or not first.get("exposure") or not second.get("exposure"):
        return False
    def authorization_terms(option):
        exposure = option["exposure"]
        signals = []
        for group in exposure.get("signals", []):
            sources = []
            for source in group["sources"]:
                if source.get("provider") == "polymarket" and (source.get("market") or {}).get("question_id"):
                    source = {k: v for k, v in source.items() if k != "observed_at"}
                sources.append(source)
            signals.append(dict(group, sources=sources))
        return dict(option, exposure=dict(exposure, signals=signals))
    return authorization_terms(first) == authorization_terms(second)
