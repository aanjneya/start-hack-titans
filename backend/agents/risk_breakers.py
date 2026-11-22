"""Risk-Triggered Circuit Breakers & Auto-Policy Shift (Agent 6).

Reads customer-configured circuit-breaker thresholds from `backend/setup/<customer>.json`
(with safe defaults when omitted) and computes automatic governance actions whenever
a supplier or product downgrades to Grade 5 (B) or Grade 6 (CCC):
  - Grade 5 (B):   Control mode lock (no unattended auto-POs) + 100% Dock AQL QC
  - Grade 6 (CCC): Auto-PO frozen + Dual-source share auto-shifted to healthy alternate
                   + Approval escalated +1 tier
"""
from __future__ import annotations

import json
from typing import Any

from ..core import policy
from .reconciler_rules import CheckContext
from .risk_scoring import ComputedRisk

DEFAULT_BREAKERS: dict[str, dict[str, Any]] = {
    "grade_5": {
        "force_human_approval": True,
        "freeze_new_auto_pos": False,
        "qc_inspection": "100%_AQL",
        "share_cap_pct": 50,
        "escalate_approver_tiers": 0,
        "label": "Grade B (5/6) — Control Lock + 100% Dock QC",
    },
    "grade_6": {
        "force_human_approval": True,
        "freeze_new_auto_pos": True,
        "qc_inspection": "100%_AQL_QUARANTINE",
        "share_cap_pct": 0,
        "escalate_approver_tiers": 1,
        "label": "Grade CCC (6/6) — Auto-PO Frozen + Share Shift + Executive Escalation",
    },
}


def _breaker_config() -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(policy._setup_path().read_text(encoding="utf-8"))
        cfg = raw.get("risk_circuit_breakers")
        if isinstance(cfg, dict):
            return {
                "grade_5": {**DEFAULT_BREAKERS["grade_5"], **(cfg.get("grade_5") or {})},
                "grade_6": {**DEFAULT_BREAKERS["grade_6"], **(cfg.get("grade_6") or {})},
            }
    except Exception:
        pass
    return DEFAULT_BREAKERS


def evaluate_circuit_breakers(
    ctx: CheckContext,
    supplier_risks: dict[str, ComputedRisk],
    product_risks: dict[str, ComputedRisk],
) -> list[dict[str, Any]]:
    """Compute active Risk-Triggered Circuit Breakers for all grade >= 5 suppliers & products."""
    cfg = _breaker_config()
    actions: list[dict[str, Any]] = []

    # Pre-index parts by supplier for automatic dual-source share shifting
    parts_by_sup: dict[str, list[str]] = {}
    for part_id, sp_rows in ctx.spm_by_part.items():
        for sp in sp_rows:
            if sp.supplier_id:
                parts_by_sup.setdefault(sp.supplier_id, []).append(part_id)

    targets: list[ComputedRisk] = sorted(
        [c for c in list(product_risks.values()) + list(supplier_risks.values()) if c.grade >= 5],
        key=lambda x: (-x.grade, 0 if x.entity_type == "product" else 1, -x.score_0_100, x.entity_id),
    )

    for comp in targets:
        rule = cfg["grade_6"] if comp.grade >= 6 else cfg["grade_5"]
        rules_triggered: list[str] = []
        if rule.get("force_human_approval"):
            rules_triggered.append("Control Mode Locked (Human Gate Required)")
        if rule.get("freeze_new_auto_pos"):
            rules_triggered.append("Unattended Auto-POs Frozen")
        if rule.get("qc_inspection"):
            rules_triggered.append(f"Dock QC: {rule['qc_inspection'].replace('_', ' ')}")
        if rule.get("share_cap_pct") is not None and comp.entity_type == "supplier":
            rules_triggered.append(f"Volume Share Capped ≤ {rule['share_cap_pct']}%")
        if int(rule.get("escalate_approver_tiers") or 0) > 0:
            rules_triggered.append(f"Approver Fast-Tracked +{rule['escalate_approver_tiers']} Tier")

        # Dual-source share shift when a supplier trips Grade 6 (or Grade 5 with healthier alternate)
        share_shifts: list[dict[str, str]] = []
        if comp.entity_type == "supplier":
            for part_id in sorted(set(parts_by_sup.get(comp.entity_id, []))):
                sp_rows = ctx.spm_by_part.get(part_id, [])
                if len(sp_rows) < 2:
                    continue
                alternates = [
                    sp for sp in sp_rows
                    if sp.supplier_id != comp.entity_id
                    and supplier_risks.get(sp.supplier_id)
                    and supplier_risks[sp.supplier_id].grade < comp.grade
                ]
                if alternates:
                    best_alt = min(alternates, key=lambda sp: supplier_risks[sp.supplier_id].score_0_100)
                    alt_sup = ctx.suppliers.get(best_alt.supplier_id)
                    alt_name = alt_sup.name if alt_sup else best_alt.supplier_id
                    cap = int(rule.get("share_cap_pct") or 0)
                    alt_share = 100 - cap
                    share_shifts.append({
                        "part_id": part_id,
                        "from_supplier": comp.entity_name,
                        "to_supplier": alt_name,
                        "new_split": f"{cap}% / {alt_share}%",
                    })
                    rules_triggered.append(f"Auto-Shift {part_id} → {alt_name} ({alt_share}%)")

        top_ev = [e.model_dump() for r in comp.reasons for e in r.evidence][:4]
        actions.append({
            "entity_type": comp.entity_type,
            "entity_id": comp.entity_id,
            "entity_name": comp.entity_name,
            "grade": comp.grade,
            "rating": comp.rating,
            "score_0_100": comp.score_0_100,
            "label": str(rule.get("label") or f"Grade {comp.rating} Circuit Breaker"),
            "force_human_approval": bool(rule.get("force_human_approval", True)),
            "freeze_new_auto_pos": bool(rule.get("freeze_new_auto_pos", False)),
            "qc_inspection": str(rule.get("qc_inspection") or ""),
            "share_cap_pct": rule.get("share_cap_pct"),
            "escalate_approver_tiers": int(rule.get("escalate_approver_tiers") or 0),
            "share_shifts": share_shifts,
            "rules_triggered": rules_triggered,
            "reason": comp.reasons[0].text if comp.reasons else f"Rated {comp.rating} ({comp.score_0_100:.0f}/100)",
            "evidence": top_ev,
        })

    return actions
