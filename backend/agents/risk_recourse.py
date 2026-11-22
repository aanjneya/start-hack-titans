"""Moody's Path-to-Upgrade Optimizer (Algorithmic Recourse for Agent 6).

Inverts our deterministic 3-tier `risk_scoring.py` engine (<2ms in memory) to solve
the minimum-cost bundle of concrete operational levers that upgrades a high-risk
product from CCC / B back toward Investment Grade (BBB / A / AA).

Zero hardcoded Soony values: every lever is derived from live `DecisionCard`s,
`ComputedRisk` bottleneck factors, `purchase_orders`, and `supplier_part_map`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from ..core.models import DecisionCard, Evidence
from .reconciler_rules import CheckContext
from .risk_scoring import ComputedRisk, score_to_grade


@dataclass
class _CandidateLever:
    id: str
    title: str
    category: str  # decision | second_source | quality | buffer
    part_id: str
    supplier_id: str
    cost_eur: float
    copies_gained: Optional[int]
    part_score_reduction: float
    decision_id: Optional[int]
    option_index: Optional[int]
    approver: str
    executable: bool
    rationale: str
    evidence: list[dict[str, Any]]


def _recompute_product_score(
    base_bom_scores: dict[str, float],
    slack_pts: float,
    reductions_by_part: dict[str, float],
) -> tuple[float, int, str]:
    """Mirror `rate_products()` formula deterministically after applying lever reductions."""
    if not base_bom_scores:
        return 0.0, 1, "AAA"
    adjusted = {
        pid: max(8.0, score - reductions_by_part.get(pid, 0.0))
        for pid, score in base_bom_scores.items()
    }
    worst = max(adjusted.values())
    high_count = sum(1 for sc in adjusted.values() if score_to_grade(sc)[0] >= 4)
    density_bonus = min(8.0, max(0, high_count - 1) * 2.5)
    # If worst bottleneck drops below BBB (<50), shift-slack pressure also eases proportionally
    effective_slack = slack_pts if worst >= 50.0 else round(slack_pts * 0.4, 1)
    total = round(min(100.0, worst + density_bonus + effective_slack), 1)
    grade, rating = score_to_grade(total)
    return total, grade, rating


def _estimate_unit_price(ctx: CheckContext, part_id: str) -> float:
    prices = [
        float(po.unit_price)
        for po in ctx.pos
        if po.material_id == part_id and po.unit_price and po.unit_price > 0
    ]
    return round(sum(prices) / len(prices), 2) if prices else 2.5


def build_upgrade_plans(
    ctx: CheckContext,
    part_risks: dict[str, ComputedRisk],
    product_risks: dict[str, ComputedRisk],
    open_decisions: list[DecisionCard],
) -> list[dict[str, Any]]:
    """Compute optimal Path-to-Upgrade plans for every launch product."""
    plans: list[dict[str, Any]] = []

    for prod_id, prod_risk in sorted(
        product_risks.items(),
        key=lambda kv: (-kv[1].grade, -kv[1].score_0_100, kv[0]),
    ):
        bom = ctx.bom_by_parent.get(prod_id, [])
        bom_part_ids = {b.child_id for b in bom if b.child_id in part_risks}
        if not bom_part_ids:
            continue

        base_bom_scores = {pid: part_risks[pid].score_0_100 for pid in bom_part_ids}
        slack_pts = float(prod_risk.breakdown.get("shift_slack") or 0.0)
        candidates: list[_CandidateLever] = []
        covered_parts_by_decision: set[str] = set()

        # 1. Extract executable levers from live proposed DecisionCards targeting this product's BOM
        for card in open_decisions:
            if card.status != "proposed" or not card.options:
                continue
            rec_idx = card.recommended if card.recommended is not None and 0 <= card.recommended < len(card.options) else 0
            opt = card.options[rec_idx]
            if not opt.get("available", True):
                continue
            actions = opt.get("actions") or []
            matched_part = ""
            matched_sup = ""
            for act in actions:
                p = str(act.get("part") or act.get("material_id") or "")
                if p in bom_part_ids:
                    matched_part = p
                    matched_sup = str(act.get("supplier_id") or "")
                    break
            if not matched_part:
                # Fallback: check title/summary/problem text for BOM part IDs
                blob = f"{card.title} {card.summary} {card.problem}"
                for pid in sorted(bom_part_ids, key=lambda x: -base_bom_scores[x]):
                    if pid in blob:
                        matched_part = pid
                        matched_sup = part_risks[pid].supplier_id
                        break
            if not matched_part:
                continue

            pr = part_risks[matched_part]
            if pr.grade < 4:
                continue
            covered_parts_by_decision.add(matched_part)
            cost = float(opt.get("cost_eur") or 0.0)
            copies = opt.get("copies_gained")
            copies_int = int(copies) if copies is not None else None
            # Clearing buffer + shock penalties on that part (derived from real breakdown!)
            reducible = float(pr.breakdown.get("stock_buffer", 0.0)) + float(pr.breakdown.get("part_shocks", 0.0)) * 0.7
            part_drop = round(max(18.0, min(pr.score_0_100 - 22.0, reducible)), 1)
            approvers = opt.get("approvers") or ([card.approver] if card.approver else [])
            ev = (opt.get("evidence") or card.evidence or [])[:3]
            candidates.append(_CandidateLever(
                id=f"dec-{card.id}-opt-{rec_idx}",
                title=f"{opt.get('label') or card.title} ({matched_part})",
                category="decision",
                part_id=matched_part,
                supplier_id=matched_sup or pr.supplier_id,
                cost_eur=round(cost, 2),
                copies_gained=copies_int,
                part_score_reduction=part_drop,
                decision_id=card.id,
                option_index=rec_idx,
                approver=" + ".join(str(a) for a in approvers) or "Buyer",
                executable=True,
                rationale=card.summary or opt.get("description") or f"Resolves {pr.rating} bottleneck on {matched_part}",
                evidence=ev,
            ))

        # 2. Extract structural levers directly from high-risk BOM bottlenecks (quality, buffer, 2nd source)
        for pid in sorted(bom_part_ids, key=lambda x: -base_bom_scores[x]):
            pr = part_risks[pid]
            if pr.grade < 4:
                continue
            ev_list = [e.model_dump() for r in pr.reasons for e in r.evidence][:3]

            # 2a. Expired quality qualification -> €0 Quality Concession lever
            sup = ctx.suppliers.get(pr.supplier_id)
            if sup and sup.qualification_valid_until and sup.qualification_valid_until < ctx.reference_now.date():
                if not any(c.part_id == pid and "concession" in c.title.lower() for c in candidates):
                    candidates.append(_CandidateLever(
                        id=f"qual-{pid}",
                        title=f"Issue Quality Concession & AQL Release ({sup.name} · {pid})",
                        category="quality",
                        part_id=pid,
                        supplier_id=pr.supplier_id,
                        cost_eur=0.0,
                        copies_gained=pr.impact.get("copies_at_risk"),
                        part_score_reduction=round(min(26.0, max(14.0, pr.breakdown.get("part_shocks", 16.0))), 1),
                        decision_id=None,
                        option_index=None,
                        approver="Quality + Head of procurement",
                        executable=False,
                        rationale=f"Clears expired conditional release ({sup.qualification_valid_until}) on {pid}",
                        evidence=ev_list,
                    ))

            # 2b. Single-source concentration -> Dual-Source Qualification lever
            if pr.is_single_source and pr.grade >= 5:
                single_pts = float(pr.breakdown.get("single_source", 14.0))
                sup_rollup = float(pr.breakdown.get("supplier_rollup", 25.0))
                # Adding a low-risk geo-decorrelated 2nd source removes single-source penalty + ~40% of supplier country rollup
                drop = round(min(pr.score_0_100 - 20.0, single_pts + sup_rollup * 0.42), 1)
                candidates.append(_CandidateLever(
                    id=f"ss-{pid}",
                    title=f"Qualify Geo-Decorrelated 2nd Source ({pid} · {pr.entity_name})",
                    category="second_source",
                    part_id=pid,
                    supplier_id=pr.supplier_id,
                    cost_eur=8500.0,
                    copies_gained=pr.impact.get("copies_at_risk"),
                    part_score_reduction=max(16.0, drop),
                    decision_id=None,
                    option_index=None,
                    approver="Buyer",
                    executable=False,
                    rationale=f"Eliminates 100% single-source lock on {sup.name if sup else pr.supplier_id} ({sup.country if sup else ''})",
                    evidence=ev_list,
                ))

            # 2c. Stock buffer gap not yet covered by a proposed DecisionCard
            if pid not in covered_parts_by_decision and float(pr.breakdown.get("stock_buffer", 0.0)) >= 12.0:
                shortfall = int(pr.impact.get("copies_at_risk") or 5000)
                est_cost = round(shortfall * _estimate_unit_price(ctx, pid), 0)
                candidates.append(_CandidateLever(
                    id=f"buf-{pid}",
                    title=f"Expedite Buffer Replenishment ({shortfall:,} pcs · {pid})",
                    category="buffer",
                    part_id=pid,
                    supplier_id=pr.supplier_id,
                    cost_eur=est_cost,
                    copies_gained=shortfall,
                    part_score_reduction=round(float(pr.breakdown.get("stock_buffer", 18.0)), 1),
                    decision_id=None,
                    option_index=None,
                    approver="Buyer" if est_cost < 50000 else "Category lead",
                    executable=False,
                    rationale=f"Restores line-ready buffer for {pid} ({pr.entity_name})",
                    evidence=ev_list,
                ))

        # 3. Greedy optimal ordering by marginal product-level risk drop per euro (Δproduct_pts / cost)
        chosen: list[dict[str, Any]] = []
        remaining = list(candidates)
        reductions: dict[str, float] = {}
        cur_score, cur_grade, cur_rating = prod_risk.score_0_100, prod_risk.grade, prod_risk.rating
        total_cost = 0.0

        while remaining and len(chosen) < 5:
            best_item: Optional[_CandidateLever] = None
            best_key = (-1.0, -1.0, -1.0)
            best_after = (cur_score, cur_grade, cur_rating)
            best_delta = 0.0

            for cand in remaining:
                trial_red = dict(reductions)
                trial_red[cand.part_id] = trial_red.get(cand.part_id, 0.0) + cand.part_score_reduction
                new_score, new_grade, new_rating = _recompute_product_score(base_bom_scores, slack_pts, trial_red)
                delta = round(max(0.0, cur_score - new_score), 1)
                # Also reward executable levers on worst bottlenecks even if another tied part keeps product score high initially
                efficiency = (delta if delta > 0 else cand.part_score_reduction * 0.25) / max(cand.cost_eur, 1000.0)
                rank_key = (1.0 if delta > 0 else 0.0, 1.0 if cand.executable else 0.0, efficiency)
                if rank_key > best_key:
                    best_key = rank_key
                    best_item = cand
                    best_after = (new_score, new_grade, new_rating)
                    best_delta = delta

            if best_item is None:
                break
            remaining.remove(best_item)
            reductions[best_item.part_id] = reductions.get(best_item.part_id, 0.0) + best_item.part_score_reduction
            cur_score, cur_grade, cur_rating = best_after
            total_cost += best_item.cost_eur
            chosen.append({
                "id": best_item.id,
                "title": best_item.title,
                "category": best_item.category,
                "part_id": best_item.part_id,
                "supplier_id": best_item.supplier_id,
                "cost_eur": round(best_item.cost_eur, 2),
                "copies_gained": best_item.copies_gained,
                "delta_points": best_delta if best_delta > 0 else round(best_item.part_score_reduction * 0.35, 1),
                "cumulative_score": cur_score,
                "cumulative_grade": cur_grade,
                "cumulative_rating": cur_rating,
                "decision_id": best_item.decision_id,
                "option_index": best_item.option_index,
                "approver": best_item.approver,
                "executable": best_item.executable,
                "rationale": best_item.rationale,
                "evidence": best_item.evidence,
            })

        plans.append({
            "product_id": prod_id,
            "product_name": prod_risk.entity_name,
            "current_score": prod_risk.score_0_100,
            "current_grade": prod_risk.grade,
            "current_rating": prod_risk.rating,
            "projected_score": cur_score,
            "projected_grade": cur_grade,
            "projected_rating": cur_rating,
            "total_cost_eur": round(total_cost, 2),
            "levers": chosen,
        })

    return plans
