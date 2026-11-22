"""Read the Risk Rater's published scores and last provider readings for the buyer screens."""
from __future__ import annotations

from sqlalchemy import and_, or_
from sqlmodel import func, select

from ..core import llm
from ..core.models import Evidence, RiskFeedSnapshot, RiskScore
from .planner_risk import market_readings, priced_markets
from .reconciler_rules import CheckContext, compute_part_impact
from .risk_scoring import ComputedRisk, ReasonItem
from .risk_signals import ExternalSignal


def current_risk_scores(db):
    newest = select(func.max(RiskScore.id)).group_by(RiskScore.entity_type, RiskScore.entity_id)
    return db.exec(select(RiskScore).where(RiskScore.id.in_(newest)).order_by(
        RiskScore.grade.desc(), RiskScore.entity_type, RiskScore.entity_id)).all()


def current_risk_response(db):
    """Fetch latest ratings plus just the preceding supplier publication in a single snapshot."""
    rank = func.row_number().over(partition_by=(RiskScore.entity_type, RiskScore.entity_id),
                                  order_by=RiskScore.id.desc()).label("rank")
    ranked = select(RiskScore.id, rank).subquery()
    rows = db.exec(select(RiskScore, ranked.c.rank).join(ranked, ranked.c.id == RiskScore.id)
                   .where(or_(ranked.c.rank == 1, and_(RiskScore.entity_type == "supplier", ranked.c.rank == 2)))
                   .order_by(RiskScore.grade.desc(), RiskScore.entity_type, RiskScore.entity_id)).all()
    previous = {row.entity_id: market_readings(row) for row, rank in rows if rank == 2}
    result = []
    for row, rank in rows:
        if rank != 1:
            continue
        data = row.model_dump(mode="json")
        if row.entity_type == "supplier" and row.entity_id in previous:
            for key, market in priced_markets(data["reasons"]):
                if key in previous[row.entity_id]:
                    market["previous_yes_pct"] = previous[row.entity_id][key]
        result.append(data)
    return result


def saved_signals(db):
    mode = "replay" if llm.is_mock() else "live"
    snapshots = db.exec(select(RiskFeedSnapshot).where(RiskFeedSnapshot.key.startswith(mode + "/")))
    return [ExternalSignal(**signal) for snapshot in snapshots for signal in snapshot.signals]


def recorded_risks(ctx: CheckContext, rows):
    by_tier = {"supplier": {}, "part": {}, "product": {}}
    for row in rows:
        if row.entity_type not in by_tier or not row.reasons:
            continue
        meta = row.reasons[0]
        score, breakdown = meta.get("score_0_100"), meta.get("breakdown")
        if not isinstance(score, (int, float)) or not isinstance(breakdown, dict) or not breakdown:
            continue  # Legacy grade-only rows cannot support a point-by-point scenario.
        reasons = [
            ReasonItem(factor=r.get("factor", ""), points=r.get("points") or 0, text=r["text"],
                       evidence=[Evidence(**e) for e in r.get("evidence") or []],
                       recommended_action=r.get("recommended_action"))
            for r in row.reasons if r.get("text")
        ]
        if not reasons:
            continue
        item = row.entity_id
        risk = ComputedRisk(entity_type=row.entity_type, entity_id=item, entity_name=item,
                            score_0_100=score, grade=row.grade, rating=row.rating, reasons=reasons,
                            breakdown=breakdown, suggested_actions=[r.recommended_action for r in reasons if r.recommended_action])
        if row.entity_type == "product":
            if item not in ctx.launch_products:
                continue
            risk.entity_name = ctx.launch_products[item].name or item
            risk.product_id = item
        elif row.entity_type == "supplier":
            if item not in ctx.suppliers:
                continue
            risk.entity_name = ctx.suppliers[item].name or item
            risk.supplier_id = item
        else:
            material = ctx.materials.get(item)
            risk.entity_name = material.description if material and material.description else item
            risk.part_id = item
            mappings = ctx.spm_by_part.get(item, [])
            if mappings:
                primary = max(mappings, key=lambda m: (bool(m.primary), m.share or 0))
                risk.supplier_id = primary.supplier_id
            if item in ctx.launch_parts:
                risk.is_single_source = len(mappings) == 1 and breakdown.get("single_source", 0) > 0
                risk.product_id, risk.impact = compute_part_impact(ctx, item)
        by_tier[row.entity_type][item] = risk
    return by_tier
