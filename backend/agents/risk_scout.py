"""Autonomous Geo-Decorrelated 2nd-Source Scout + Spec-Grounded RFQ Pack (Agent 6).

When any single-source part hits high risk (grade >= 4), this module:
  1. Excludes the high-risk origin country (e.g. CN / IN)
  2. Evaluates alternative manufacturing corridors (DE, PL, CZ, JP, MX, VN) using our
     recorded country signals and illustrative corridor assumptions
  3. Shortlists 3 geo-decorrelated industrial candidates matched to the part's category
  4. Estimates the effect of removing the documented single-source dependency
  5. Auto-compiles a spec-grounded RFQ email pulling real part specs, BOM demand,
     drawing revision, and target pricing directly from SQLite.
"""
from __future__ import annotations

from typing import Any

from .reconciler_rules import CheckContext
from .risk_scoring import ComputedRisk, score_to_grade
from .risk_signals import ExternalSignal

SAFE_CANDIDATE_COUNTRIES = ("DE", "PL", "CZ", "JP", "MX", "VN")

# Domain-general industrial supplier archetype directory (used offline/MOCK and grounded by live corridor scores)
_ARCHETYPES: dict[str, list[dict[str, Any]]] = {
    "magnet": [
        {"name": "Vacuumschmelze Magnetics GmbH", "country": "DE", "city": "Hanau", "lead_time_days": 21, "qualification_weeks": 3, "certs": ["ISO 9001", "IATF 16949", "RoHS/REACH"], "why": "EU rare-earth-free & recycled NdFeB production; zero China MOFCOM export-control exposure"},
        {"name": "Shin-Etsu Precision Magnetics", "country": "JP", "city": "Takefu", "lead_time_days": 28, "qualification_weeks": 4, "certs": ["ISO 9001", "ISO 14001", "REACH"], "why": "Independent Japanese magnet supply chain with direct air/sea corridor to Hamburg"},
        {"name": "Bunting Magnetics Europe", "country": "PL", "city": "Wroclaw", "lead_time_days": 14, "qualification_weeks": 2, "certs": ["ISO 9001", "RoHS"], "why": "Nearshore EU overland truck (<9h to Hamburg); zero maritime chokepoints"},
    ],
    "collectible": [
        {"name": "Schleich Precision Casting & Molding GmbH", "country": "DE", "city": "Schwäbisch Gmünd", "lead_time_days": 18, "qualification_weeks": 3, "certs": ["ISO 9001", "EN 71", "CE"], "why": "EU-based high-detail resin/PVC collector production; 1-day truck to DC-HAM"},
        {"name": "Brno Fine Castings s.r.o.", "country": "CZ", "city": "Brno", "lead_time_days": 16, "qualification_weeks": 2, "certs": ["ISO 9001", "AQL 1.0"], "why": "Central European precision casting & hand-finishing; eliminates India/Suez sea-freight risk"},
        {"name": "Koto Collectibles Manufacturing Co.", "country": "JP", "city": "Osaka", "lead_time_days": 26, "qualification_weeks": 3, "certs": ["ISO 9001", "CE"], "why": "Tier-1 collector-edition figure specialist with proven AAA quality yield"},
    ],
    "packaging": [
        {"name": "Edelmann Packaging Group", "country": "DE", "city": "Heidenheim", "lead_time_days": 10, "qualification_weeks": 2, "certs": ["ISO 9001", "FSC", "ISO 14001"], "why": "German high-volume rigid-box & sleeve printing; 6h domestic overland to Hamburg"},
        {"name": "Model Opakowania Sp. z o.o.", "country": "PL", "city": "Biłgoraj", "lead_time_days": 12, "qualification_weeks": 2, "certs": ["ISO 9001", "FSC"], "why": "Dual-line EU offset & digital packaging capacity; immune to Asia sea-freight delays"},
        {"name": "THIMM Packaging Systems", "country": "CZ", "city": "Všetaty", "lead_time_days": 11, "qualification_weeks": 2, "certs": ["ISO 9001", "FSC", "ISO 50001"], "why": "Nearshore Czech rigid collector box specialist with automated magnet insertion"},
    ],
    "electronics": [
        {"name": "Kioxia Industrial Memory Systems", "country": "JP", "city": "Yokkaichi", "lead_time_days": 24, "qualification_weeks": 4, "certs": ["ISO 9001", "IATF 16949", "RoHS"], "why": "Japanese NAND/ROM wafer fab independent of Korean/US tariff corridors"},
        {"name": "Infineon / X-FAB European Packaging", "country": "DE", "city": "Dresden", "lead_time_days": 18, "qualification_weeks": 4, "certs": ["ISO 9001", "IATF 16949"], "why": "EU semiconductor back-end assembly & programming inside Schengen zone"},
        {"name": "Monterrey Micro-Assembly S.A.", "country": "MX", "city": "Monterrey", "lead_time_days": 22, "qualification_weeks": 3, "certs": ["ISO 9001", "RoHS"], "why": "Americas-corridor electronics assembly with direct transatlantic air-cargo links"},
    ],
}


def _pick_category(part_name: str, category: str) -> str:
    blob = f"{part_name} {category}".lower()
    if any(k in blob for k in ("magnet", "rare", "ndfeb", "neodymium")):
        return "magnet"
    if any(k in blob for k in ("statue", "figurine", "collectible", "diecast", "resin", "metal")):
        return "collectible"
    if any(k in blob for k in ("card", "chip", "memory", "semi", "pcb", "ic", "rom", "flash")):
        return "electronics"
    return "packaging"


def _corridor_score(country: str, signals: list[ExternalSignal]) -> tuple[float, str]:
    """Compute country corridor risk score (0..100) from live external signals."""
    cc = country.upper()
    matching = [s for s in signals if cc in (s.countries or ())]
    if not matching:
        # Clean corridor baseline
        base = 16.0 if cc in ("DE", "PL", "CZ", "JP") else 28.0
        return base, score_to_grade(base)[1]
    peak = max(s.score_0_100 for s in matching)
    avg = sum(s.score_0_100 for s in matching) / len(matching)
    score = round(min(95.0, 0.65 * peak + 0.35 * avg), 1)
    return score, score_to_grade(score)[1]


def build_second_source_scouts(
    ctx: CheckContext,
    part_risks: dict[str, ComputedRisk],
    existing_signals: list[ExternalSignal],
) -> list[dict[str, Any]]:
    """Build geo-decorrelated 2nd-source scouts + spec-grounded RFQ packs for high-risk single-source parts."""
    targets = sorted(
        [pr for pr in part_risks.values() if pr.is_single_source and pr.grade >= 4],
        key=lambda x: (-x.grade, -x.score_0_100, x.entity_id),
    )[:5]
    if not targets:
        return []

    # Scouting may read a supplier feed baseline. Only the Risk Rater refreshes that baseline.
    corridor_scores = {cc: _corridor_score(cc, existing_signals) for cc in SAFE_CANDIDATE_COUNTRIES}
    scouts: list[dict[str, Any]] = []

    for pr in targets:
        sup = ctx.suppliers.get(pr.supplier_id)
        mat = ctx.materials.get(pr.entity_id)
        blocked_cc = (sup.country or "").upper() if sup else ""
        cat_key = _pick_category(pr.entity_name, mat.category if mat else "")

        # Compute real unit price & volume from SQLite POs / BOM / LaunchStatus
        po_prices = [float(po.unit_price) for po in ctx.pos if po.material_id == pr.entity_id and po.unit_price]
        avg_price = round(sum(po_prices) / len(po_prices), 2) if po_prices else None
        est_qty = int(pr.impact.get("copies_at_risk") or 25000)
        drawing_rev = (mat.drawing_rev if mat and mat.drawing_rev else "latest")

        raw_candidates = [
            c for c in _ARCHETYPES[cat_key]
            if c["country"] != blocked_cc
        ]
        if len(raw_candidates) < 3:
            raw_candidates.extend(
                c for c in _ARCHETYPES["packaging"]
                if c["country"] != blocked_cc and c["name"] not in {x["name"] for x in raw_candidates}
            )

        candidates_out: list[dict[str, Any]] = []
        for idx, cand in enumerate(raw_candidates[:3]):
            c_score, c_rating = corridor_scores.get(cand["country"], (20.0, "AA"))
            price_mult = 1.04 + idx * 0.03
            candidates_out.append({
                "name": cand["name"],
                "country": cand["country"],
                "city": cand["city"],
                "corridor_rating": c_rating,
                "corridor_score": c_score,
                "lead_time_days": cand["lead_time_days"],
                "est_unit_price_eur": round(avg_price * price_mult, 2) if avg_price else None,
                "qualification_weeks": cand["qualification_weeks"],
                "certifications": cand["certs"],
                "why_decorrelated": cand["why"],
            })

        # No verified supplier grade exists for an illustrative candidate. Only the measured
        # single-source penalty clears. Apply the same 100-point ceiling as the part rater.
        projected = sum(pr.breakdown.values()) - pr.breakdown["single_source"]
        proj_score = round(min(100.0, max(0.0, projected)), 1)
        proj_grade, proj_rating = score_to_grade(proj_score)

        safe_corridors = sorted(
            [
                {"country": cc, "rating": rating, "score": sc}
                for cc, (sc, rating) in corridor_scores.items()
                if cc != blocked_cc
            ],
            key=lambda x: x["score"],
        )[:4]

        price_line = f"Target unit price: ~€{avg_price:.2f}/unit" if avg_price else "Target unit price: competitive market quote"
        rfq_subject = f"RFQ — Dual-Source Qualification: {pr.entity_id} ({pr.entity_name}, Rev {drawing_rev})"
        rfq_body = (
            f"Hello Sourcing Team,\n\n"
            f"We are qualifying a geo-decorrelated second source (30–50% volume allocation) for:\n"
            f"• Component: {pr.entity_id} — {pr.entity_name}\n"
            f"• Drawing Revision: {drawing_rev}\n"
            f"• Initial Volume: {est_qty:,} units (recurring quarterly call-offs)\n"
            f"• {price_line} (DDP Hamburg / Incoterms 2020)\n"
            f"• Compliance: ISO 9001 + RoHS/REACH + AQL 1.0 incoming quality gate\n\n"
            f"Please confirm tooling/qualification lead time, sample availability, and unit pricing by return email.\n\n"
            f"Best regards,\nProcurement Operations"
        )

        scouts.append({
            "part_id": pr.entity_id,
            "part_name": pr.entity_name,
            "current_supplier_id": pr.supplier_id,
            "current_supplier_name": sup.name if sup else pr.supplier_id,
            "blocked_country": blocked_cc or "N/A",
            "current_score": pr.score_0_100,
            "current_grade": pr.grade,
            "current_rating": pr.rating,
            "projected_dual_score": proj_score,
            "projected_dual_grade": proj_grade,
            "projected_dual_rating": proj_rating,
            "safe_corridors": safe_corridors,
            "candidates": candidates_out,
            "rfq_subject": rfq_subject,
            "rfq_body": rfq_body,
            "evidence": [e.model_dump() for r in pr.reasons for e in r.evidence][:3],
        })

    return scouts
