"""Product risk scenarios. Each step removes one measured part risk factor and rerates the product."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .reconciler_rules import CheckContext
from .risk_scoring import ComputedRisk, rate_products, score_to_grade


@dataclass
class ScenarioAction:
    part: str
    factor: str
    points: float
    category: str
    title: str
    assumption: str
    entry: dict | None


def _linked_actions(queue, product_id):
    """Keep the highest-ranked buyer action that addresses each scored problem."""
    linked = {}
    seen = set()
    for entry in queue:
        option = entry["option"]
        actions = option.get("actions") or []
        if option.get("product_id") != product_id or len(actions) != 1 or entry["key"] in seen:
            continue
        action = actions[0]
        part = action.get("part")
        if not part or part != entry.get("part"):
            continue
        kind = option.get("description")
        if (action.get("reference") or "").startswith("qualification/"):
            part = (option.get("exposure") or {}).get("component") or part
            factor = "single_source"
        elif kind == "quality concession review":
            factor = "quality_hold_failed_aql"
        elif kind in {"supplier stock offer", "port pickup"} or action.get("quote_inbox_id"):
            factor = "stock_buffer"
        else:
            continue
        linked.setdefault((part, factor), entry)
        seen.add(entry["key"])
    return linked


def _part_name(ctx, part):
    material = ctx.materials.get(part)
    return material.description if material and material.description else part


def _candidates(ctx, part, linked):
    name = _part_name(ctx, part.entity_id)
    cases = [
        ("single_source", part.breakdown.get("single_source", 0), "second_source",
         f"Put a second supplier in service for {name}",
         "Assumes a qualified second supplier starts supplying this part. Supplier health and delivery conditions stay at their current levels."),
        ("stock_buffer", part.breakdown.get("stock_buffer", 0), "buffer",
         f"Restore stock cover for {name}",
         "Assumes enough released stock reaches the line to clear this part’s entire stock shortfall and buffer warning."),
    ]
    # A concession can clear a documented hold once Quality releases the lot.
    # Other causes of the capped part-shock score must still apply.
    # The model retains its top three reasons, so the full uncapped sum of shocks
    # cannot be reconstructed when the cap binds.
    if part.breakdown.get("part_shocks", 0) < 35 and (part.entity_id, "quality_hold_failed_aql") in linked:
        hold = next((r for r in part.reasons if r.factor == "quality_hold_failed_aql"), None)
        if hold:
            cases.append((hold.factor, hold.points, "quality", f"Release the quality hold on {name}",
                          "Assumes Quality documents a concession and releases the affected lot."))
    return [
        ScenarioAction(part.entity_id, factor, points, category, title, assumption, linked.get((part.entity_id, factor)))
        for factor, points, category, title, assumption in cases if points > 0
    ]


def build_upgrade_plans(
    ctx: CheckContext,
    part_risks: dict[str, ComputedRisk],
    product_risks: dict[str, ComputedRisk],
    queue: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    plans = []
    for product in sorted(product_risks.values(), key=lambda r: (-r.score_0_100, r.entity_id)):
        product_id = product.entity_id
        part_ids = sorted({line.child_id for line in ctx.bom_by_parent.get(product_id, []) if line.child_id in part_risks})
        if not part_ids:
            continue
        linked = _linked_actions(queue, product_id)
        parts = {pid: part_risks[pid] for pid in part_ids}
        remaining = [action for part in parts.values() if part.grade >= 4 for action in _candidates(ctx, part, linked)]
        current = product
        # Each trial uses the production product rater, with just the selected factor removed.
        # Rebuild each part from its uncapped factors before applying the 100-point ceiling.
        local = replace(ctx, launch_products={product_id: ctx.launch_products[product_id]})
        removed = dict.fromkeys(part_ids, 0.0)
        chosen = []
        while remaining and len(chosen) < 5:
            trials = []
            for action in remaining:
                original = part_risks[action.part]
                raw = sum(original.breakdown.values()) - removed[action.part] - action.points
                score = round(max(0.0, min(100.0, raw)), 1)
                grade, rating = score_to_grade(score)
                changed_part = replace(original, score_0_100=score, grade=grade, rating=rating)
                after = rate_products(local, {**parts, action.part: changed_part})[product_id]
                # For tied products, prefer improving the highest-risk remaining part.
                rank = (after.score_0_100, -parts[action.part].score_0_100, -action.points,
                        action.entry is None, action.part, action.factor)
                trials.append((rank, action, changed_part, after))
            _, action, changed_part, after = min(trials, key=lambda trial: trial[0])
            removed[action.part] += action.points
            remaining.remove(action)
            parts[action.part] = changed_part
            limiter = max(parts, key=lambda pid: parts[pid].score_0_100)
            entry = action.entry or {}
            option = entry.get("option") or {}
            chosen.append({
                "id": f"{action.factor}/{action.part}", "category": action.category,
                "title": action.title, "rationale": action.assumption,
                "part_id": action.part, "supplier_id": changed_part.supplier_id,
                "delta_points": round(current.score_0_100 - after.score_0_100, 1),
                "cumulative_score": after.score_0_100, "cumulative_grade": after.grade,
                "cumulative_rating": after.rating, "limiting_part_after": _part_name(ctx, limiter),
                "queue_key": entry.get("key"), "decision_id": entry.get("decision_id"),
                "option_index": entry.get("option_index"), "decision_label": option.get("label"),
                "executable": bool(entry), "approver": " + ".join(option.get("approvers") or []),
                "cost_eur": option.get("cost_eur"), "copies_gained": option.get("copies_gained"),
                "evidence": entry.get("evidence") or [
                    e.model_dump() for reason in changed_part.reasons if reason.factor == action.factor for e in reason.evidence
                ],
            })
            current = after
        costs = [step["cost_eur"] for step in chosen]
        plans.append({
            "product_id": product_id, "product_name": product.entity_name,
            "current_score": product.score_0_100, "current_grade": product.grade, "current_rating": product.rating,
            "projected_score": current.score_0_100, "projected_grade": current.grade, "projected_rating": current.rating,
            "total_cost_eur": round(sum(costs), 2) if all(v is not None for v in costs) else None,
            "levers": chosen,
        })
    return plans
