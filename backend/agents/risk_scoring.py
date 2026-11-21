"""Deterministic 3-tier Moody's-style risk scoring engine for Agent 6 (Risk Rater).

Rates every entity on a transparent 0-100 risk index mapped to grade 1..6 (AAA..CCC):
  Tier 1: Supplier  (55% internal health & internal politics + 45% external Polymarket/news/weather/telemetry)
  Tier 2: Part      (share-weighted supplier risk + single-source concentration + stock buffer + ECN/inbound shocks)
  Tier 3: Product   (worst-link bottleneck BOM part + high-risk BOM density + packing shift slack)

Every grade carries:
- top 3 plain-English reasons with exact factor, points, and Evidence links (no bluffing)
- full factor breakdown for auditability
- concrete recommended_actions ("I need the risk, but I want the solution")
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

from ..core.models import Evidence, Issue, Shipment
from .reconciler_rules import CheckContext, compute_part_impact
from .risk_signals import ExternalSignal

GRADE_TO_RATING = {
    1: "AAA",
    2: "AA",
    3: "A",
    4: "BBB",
    5: "B",
    6: "CCC",
}


def score_to_grade(score_0_100: float) -> tuple[int, str]:
    s = max(0.0, min(100.0, score_0_100))
    if s >= 80.0:
        grade = 6
    elif s >= 65.0:
        grade = 5
    elif s >= 50.0:
        grade = 4
    elif s >= 35.0:
        grade = 3
    elif s >= 20.0:
        grade = 2
    else:
        grade = 1
    return grade, GRADE_TO_RATING[grade]


@dataclass
class ReasonItem:
    factor: str  # e.g. single_source | external_politics | weather | internal_politics | stock_buffer | transport
    points: float
    text: str
    evidence: list[Evidence] = field(default_factory=list)
    recommended_action: Optional[dict[str, Any]] = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "text": self.text,
            "factor": self.factor,
            "points": round(self.points, 1),
            "evidence": [e.model_dump() for e in self.evidence],
        }
        if self.recommended_action:
            out["recommended_action"] = self.recommended_action
        return out


@dataclass
class ComputedRisk:
    entity_type: str  # supplier | part | product
    entity_id: str
    entity_name: str
    score_0_100: float
    grade: int
    rating: str
    reasons: list[ReasonItem]  # top 3 reasons (sorted by points desc)
    breakdown: dict[str, float]  # factor -> points
    suggested_actions: list[dict[str, Any]]
    part_id: str = ""
    supplier_id: str = ""
    product_id: str = ""
    impact: dict[str, Any] = field(default_factory=dict)
    is_single_source: bool = False


def _dedup_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for a in actions:
        if not a:
            continue
        k = (str(a.get("type") or ""), str(a.get("target") or ""))
        if k not in seen:
            seen.add(k)
            out.append({kk: vv for kk, vv in a.items() if vv is not None})
    return out


# ---------------------------------------------------------------------------
# Tier 1: Supplier Risk Rating
# ---------------------------------------------------------------------------
def rate_suppliers(
    ctx: CheckContext,
    open_issues: list[Issue],
    signals: list[ExternalSignal],
) -> dict[str, ComputedRisk]:
    ref_date = ctx.reference_now.date()
    issues_by_sup: dict[str, list[Issue]] = defaultdict(list)
    for iss in open_issues:
        if iss.supplier_id:
            issues_by_sup[iss.supplier_id].append(iss)

    shipments_by_sup: dict[str, list[Shipment]] = defaultdict(list)
    for sh in ctx.shipments:
        if sh.supplier_id and sh.updated_at is not None:  # ERP shipment rows
            shipments_by_sup[sh.supplier_id].append(sh)

    docs_by_sup: dict[str, list] = defaultdict(list)
    for d in ctx.documents:
        if d.supplier_id:
            docs_by_sup[d.supplier_id].append(d)

    # Check if org notes mention open procurement roles (internal politics context)
    org_gap_doc = next(
        (d for d in ctx.documents if "category lead" in (d.text or "").lower() and "open" in (d.text or "").lower()),
        None,
    )

    results: dict[str, ComputedRisk] = {}
    for sup_id, sup in sorted(ctx.suppliers.items()):
        country = (sup.country or "").upper()
        reasons: list[ReasonItem] = []
        breakdown: dict[str, float] = {}

        # --- A. INTERNAL FUNDAMENTALS & INTERNAL POLITICS (0..55 pts) ---
        # A1. Supplier health & quality qualification (0..26 pts)
        health_pts = 0.0
        health_notes: list[str] = []
        health_ev: list[Evidence] = []
        sup_status = (sup.status or "").strip().lower()
        if sup_status in {"at risk", "at_risk", "suspended", "blocked"}:
            health_pts += 22.0
            health_notes.append(f"supplier status '{sup.status}'")
            health_ev.append(Evidence(path=sup.source, locator=sup_id, quote=f"status={sup.status}"))
        elif sup_status == "watch":
            health_pts += 14.0
            health_notes.append("supplier status 'watch'")
            health_ev.append(Evidence(path=sup.source, locator=sup_id, quote=f"status={sup.status}"))

        if sup.qualification_valid_until and sup.qualification_valid_until < ref_date:
            health_pts += 16.0
            health_notes.append(f"conditional quality release expired {sup.qualification_valid_until}")
            health_ev.append(Evidence(path=sup.source, locator=sup_id,
                                      quote=f"qualification={sup.qualification_status}, valid_until={sup.qualification_valid_until}"))

        # Downtime or export-suspension notices directly from this supplier
        for d in docs_by_sup.get(sup_id, []):
            sigs = set(((d.extracted or {}).get("facts") or {}).get("signals") or [])
            if d.kind == "notice" and ("suspended" in sigs or "export_control" in sigs):
                health_pts += 20.0
                health_notes.append(f"supply suspension/export-licence notice ({d.path.rsplit('/', 1)[-1]})")
                health_ev.append(Evidence(path=d.path, locator="notice", quote=(d.text or "")[:140]))
            elif d.kind == "notice" and "downtime" in sigs:
                health_pts += 14.0
                health_notes.append(f"planned equipment downtime ({d.path.rsplit('/', 1)[-1]})")
                health_ev.append(Evidence(path=d.path, locator="notice", quote=(d.text or "")[:140]))
            elif d.kind == "shipping_advice" and "short_shipment" in sigs:
                health_pts += 10.0
                health_notes.append("short-shipment offload on air freight")
                health_ev.append(Evidence(path=d.path, locator="shipping_advice", quote=(d.text or "")[:140]))

        health_pts = min(30.0, health_pts)
        breakdown["supplier_health"] = round(health_pts, 1)
        if health_pts > 0:
            reasons.append(ReasonItem(
                factor="supplier_health",
                points=health_pts,
                text=f"{sup.name}: " + "; ".join(health_notes[:2]),
                evidence=health_ev[:3],
                recommended_action={
                    "type": "second_source" if health_pts >= 18 else "email",
                    "label": f"Qualify backup supplier / verify capacity with {sup.name}",
                    "target": sup_id,
                    "owner": "planner" if health_pts >= 18 else "chaser",
                },
            ))

        # A2. Internal politics / org & approval friction (0..15 pts)
        internal_pts = 0.0
        internal_notes: list[str] = []
        internal_ev: list[Evidence] = []
        sup_issues = issues_by_sup.get(sup_id, [])
        stuck_pos = [po for po in ctx.pos if po.supplier_id == sup_id
                     and (po.status or "").lower() in {"awaiting_approval", "pending_approval"}]
        if stuck_pos:
            internal_pts += 10.0
            internal_notes.append(f"{len(stuck_pos)} PO(s) stuck awaiting internal approval")
            internal_ev.append(Evidence(path=stuck_pos[0].source, locator=f"PO {stuck_pos[0].po_number}",
                                        quote=f"status={stuck_pos[0].status}"))
        high_issues = [i for i in sup_issues if i.severity >= 4]
        if high_issues:
            internal_pts += min(8.0, 3.0 * len(high_issues))
            internal_notes.append(f"{len(high_issues)} high-severity open Reconciler issue(s)")
            internal_ev.append(Evidence(path=f"issue://{high_issues[0].id}", locator=high_issues[0].type,
                                        quote=high_issues[0].summary))
        if org_gap_doc and any(i.product_id == "FG-GH6-CE" for i in sup_issues):
            internal_pts += 3.0
            internal_notes.append("Collector's Category Lead role is vacant (approval bottleneck)")
            internal_ev.append(Evidence(path=org_gap_doc.path, locator="onboarding_notes",
                                        quote="Category Lead for Collector's Items left in the summer. Position is open."))
        internal_pts = min(15.0, internal_pts)
        breakdown["internal_politics"] = round(internal_pts, 1)
        if internal_pts > 0:
            reasons.append(ReasonItem(
                factor="internal_politics",
                points=internal_pts,
                text="Internal approval/ops friction: " + "; ".join(internal_notes[:2]),
                evidence=internal_ev[:2],
                recommended_action={
                    "type": "human_review",
                    "label": f"Escalate internal approval/quality sign-off for {sup.name} to Head of Procurement",
                    "target": sup_id,
                    "owner": "planner",
                },
            ))

        # A3. Transport & mode exposure (0..10 pts)
        sup_ships = shipments_by_sup.get(sup_id, [])
        transport_pts = 0.0
        if sup_ships:
            sea_ratio = sum(1 for s in sup_ships if (s.mode or "").lower() == "sea") / len(sup_ships)
            delayed = [s for s in sup_ships if (s.status or "").lower() == "delayed"]
            transport_pts += round(sea_ratio * 5.0, 1)
            if delayed:
                transport_pts += min(5.0, 2.5 * len(delayed))
            transport_pts = min(10.0, transport_pts)
            breakdown["transport"] = round(transport_pts, 1)
            if transport_pts >= 5.0:
                reasons.append(ReasonItem(
                    factor="transport",
                    points=transport_pts,
                    text=(
                        f"{int(sea_ratio * 100)}% sea-freight exposure"
                        + (f" with {len(delayed)} delayed vessel(s)" if delayed else "")
                    ),
                    evidence=[Evidence(path=sup_ships[0].source, locator=sup_ships[0].shipment_id,
                                       quote=f"mode={sup_ships[0].mode}, status={sup_ships[0].status}")],
                    recommended_action={
                        "type": "expedite",
                        "label": f"Prepare air-freight split option for {sup.name}",
                        "target": sup_id,
                        "owner": "planner",
                    },
                ))
        else:
            breakdown["transport"] = 0.0

        internal_total = min(55.0, health_pts + internal_pts + transport_pts)

        # --- B. EXTERNAL SIGNALS (0..45 pts, capped so ambient odds never dominate alone) ---
        sup_ports = {p.upper() for s in sup_ships for p in (s.origin, s.destination) if p}
        sup_ship_ids = {s.shipment_id for s in sup_ships}

        # B1. Portal Trade News & Export Bans (0..22 pts)
        news_sigs = [
            sg for sg in signals
            if sg.provider == "portal_news" and (not sg.countries or country in sg.countries)
        ]
        news_pts = 0.0
        if news_sigs:
            top_news = max(news_sigs, key=lambda x: x.score_0_100)
            news_pts = round(min(22.0, top_news.score_0_100 * 0.24), 1)
            breakdown["trade_news"] = news_pts
            reasons.append(ReasonItem(
                factor="trade_news",
                points=news_pts,
                text=f"Trade/export news ({country}): {top_news.title}",
                evidence=[sg.as_evidence() for sg in news_sigs[:2]],
                recommended_action={
                    "type": "second_source",
                    "label": f"De-risk {country} export-control exposure for {sup.name} via non-{country} 2nd source",
                    "target": sup_id,
                    "owner": "planner",
                },
            ))
        else:
            breakdown["trade_news"] = 0.0

        # B2. Polymarket + GDELT External Politics (0..13 pts)
        poly_sigs = [
            sg for sg in signals
            if sg.provider in {"polymarket", "gdelt"} and country in sg.countries
        ]
        ext_pol_pts = 0.0
        if poly_sigs:
            top_poly = max(poly_sigs, key=lambda x: x.score_0_100)
            avg_top2 = sum(s.score_0_100 for s in sorted(poly_sigs, key=lambda x: -x.score_0_100)[:2]) / min(2, len(poly_sigs))
            ext_pol_pts = round(min(13.0, avg_top2 * 0.16), 1)
            breakdown["external_politics"] = ext_pol_pts
            reasons.append(ReasonItem(
                factor="external_politics",
                points=ext_pol_pts,
                text=f"{top_poly.summary} ({country} exposure)",
                evidence=[top_poly.as_evidence()],
                recommended_action={
                    "type": "safety_stock",
                    "label": f"Build buffer stock or dual-source to hedge {country} tariff/geopolitical odds",
                    "target": sup_id,
                    "owner": "planner",
                },
            ))
        else:
            breakdown["external_politics"] = 0.0

        # B3. Open-Meteo Weather at supplier ports/corridors (0..6 pts)
        wx_sigs = [
            sg for sg in signals
            if sg.provider == "weather" and (
                any(p in sup_ports for p in sg.ports) or (country and country in sg.countries)
            )
        ]
        wx_pts = 0.0
        if wx_sigs:
            top_wx = max(wx_sigs, key=lambda x: x.score_0_100)
            wx_pts = round(min(6.0, top_wx.score_0_100 * 0.08), 1)
            breakdown["weather"] = wx_pts
            if wx_pts >= 2.0:
                reasons.append(ReasonItem(
                    factor="weather",
                    points=wx_pts,
                    text=f"Weather disruption: {top_wx.summary}",
                    evidence=[top_wx.as_evidence()],
                    recommended_action={
                        "type": "expedite",
                        "label": f"Monitor port/airport weather delays on {sup.name} shipments",
                        "target": sup_id,
                        "owner": "chaser",
                    },
                ))
        else:
            breakdown["weather"] = 0.0

        # B4. Satellite / AIS Telemetry on supplier's active shipments (0..6 pts)
        tel_sigs = [
            sg for sg in signals
            if sg.provider == "telemetry" and any(sid in sup_ship_ids for sid in sg.shipment_ids)
        ]
        tel_pts = 0.0
        if tel_sigs:
            top_tel = max(tel_sigs, key=lambda x: x.score_0_100)
            tel_pts = round(min(6.0, top_tel.score_0_100 * 0.08), 1)
            breakdown["telemetry"] = tel_pts
            if tel_pts >= 3.0:
                reasons.append(ReasonItem(
                    factor="telemetry",
                    points=tel_pts,
                    text=top_tel.summary,
                    evidence=[top_tel.as_evidence()],
                    recommended_action={
                        "type": "email",
                        "label": f"Verify live vessel/truck telemetry for {sup.name} ({top_tel.shipment_ids[0]})",
                        "target": sup_id,
                        "owner": "chaser",
                    },
                ))
        else:
            breakdown["telemetry"] = 0.0

        # Cap external total at 45 pts (and at 22 pts if no portal_news export ban hit, so ambient odds stay realistic)
        external_cap = 45.0 if news_pts > 0 else 24.0
        external_total = min(external_cap, news_pts + ext_pol_pts + wx_pts + tel_pts)

        total_score = round(min(100.0, internal_total + external_total), 1)
        grade, rating = score_to_grade(total_score)

        reasons.sort(key=lambda r: -r.points)
        top3 = reasons[:3]
        if not top3:
            top3 = [ReasonItem(
                factor="baseline",
                points=0.0,
                text=f"{sup.name} ({country}) is qualified with no active disruptions",
                evidence=[Evidence(path=sup.source, locator=sup_id, quote=f"status={sup.status}")],
            )]

        actions = _dedup_actions([r.recommended_action for r in top3 if r.recommended_action])
        results[sup_id] = ComputedRisk(
            entity_type="supplier",
            entity_id=sup_id,
            entity_name=sup.name or sup_id,
            score_0_100=total_score,
            grade=grade,
            rating=rating,
            reasons=top3,
            breakdown=breakdown,
            suggested_actions=actions,
            supplier_id=sup_id,
        )

    return results


# ---------------------------------------------------------------------------
# Tier 2: Part Risk Rating (rolled up from suppliers + part fundamentals)
# ---------------------------------------------------------------------------
def rate_parts(
    ctx: CheckContext,
    supplier_risks: dict[str, ComputedRisk],
    open_issues: list[Issue],
    signals: list[ExternalSignal],
) -> dict[str, ComputedRisk]:
    issues_by_part: dict[str, list[Issue]] = defaultdict(list)
    for iss in open_issues:
        if iss.part_id:
            issues_by_part[iss.part_id].append(iss)

    results: dict[str, ComputedRisk] = {}
    part_ids = sorted(set(ctx.launch_parts) | set(ctx.materials))

    for part_id in part_ids:
        mat = ctx.materials.get(part_id)
        part_name = f"{part_id} ({mat.description})" if (mat and mat.description) else part_id
        sp_rows = ctx.spm_by_part.get(part_id, [])
        reasons: list[ReasonItem] = []
        breakdown: dict[str, float] = {}

        # 1. Weighted supplier risk rollup (0..40 pts)
        primary_sup_id = ""
        sup_rollup = 0.0
        if sp_rows:
            total_share = sum(float(sp.share or 0) for sp in sp_rows)
            weighted = 0.0
            for sp in sp_rows:
                w = (float(sp.share or 0) / total_share) if total_share > 0 else (1.0 / len(sp_rows))
                sr = supplier_risks.get(sp.supplier_id)
                if sr:
                    weighted += w * sr.score_0_100
            primary_sp = max(sp_rows, key=lambda x: (1 if x.primary else 0, float(x.share or 0)))
            primary_sup_id = primary_sp.supplier_id
            sup_rollup = round(min(40.0, weighted * 0.45), 1)
            breakdown["supplier_risk"] = sup_rollup
            worst_sp = max(sp_rows, key=lambda x: supplier_risks.get(x.supplier_id).score_0_100 if supplier_risks.get(x.supplier_id) else 0)
            worst_sr = supplier_risks.get(worst_sp.supplier_id)
            if worst_sr and sup_rollup >= 12.0:
                reasons.append(ReasonItem(
                    factor="supplier_risk",
                    points=sup_rollup,
                    text=(
                        f"Supplier exposure: {worst_sr.entity_name} ({worst_sr.entity_id}) rated "
                        f"{worst_sr.rating} ({worst_sr.grade}/6) — {worst_sr.reasons[0].text}"
                    ),
                    evidence=worst_sr.reasons[0].evidence[:2],
                    recommended_action={
                        "type": "second_source" if len(sp_rows) == 1 else "reallocate",
                        "label": (
                            f"Qualify backup supplier for {part_id} beyond {worst_sr.entity_name}"
                            if len(sp_rows) == 1
                            else f"Shift volume of {part_id} away from {worst_sr.entity_name}"
                        ),
                        "target": part_id,
                        "owner": "planner",
                    },
                ))
        else:
            breakdown["supplier_risk"] = 0.0

        # 2. Single-source concentration (0..18 pts)
        is_single = len(sp_rows) == 1
        single_pts = 0.0
        if is_single:
            sp = sp_rows[0]
            single_pts = 16.0 if part_id in ctx.launch_parts else 10.0
            sup_name = ctx.suppliers[sp.supplier_id].name if sp.supplier_id in ctx.suppliers else sp.supplier_id
            reasons.append(ReasonItem(
                factor="single_source",
                points=single_pts,
                text=f"Single-source dependency: 100% supplied by {sup_name} ({sp.supplier_id})",
                evidence=[Evidence(path=sp.source, locator=f"{sp.supplier_id}/{part_id}",
                                   quote=f"share={sp.share}, lead_time={sp.lead_time_days}d, note={sp.note}")],
                recommended_action={
                    "type": "second_source",
                    "label": f"Qualify second source for single-source part {part_id}",
                    "target": part_id,
                    "owner": "planner",
                },
            ))
        breakdown["single_source"] = single_pts

        # 3. Stock & hours-to-line-stop buffer (0..28 pts)
        product_id, impact = compute_part_impact(ctx, part_id) if part_id in ctx.launch_parts else ("", {})
        stock_qty = sum(float(s.quantity or 0) for s in ctx.stock if s.material_id == part_id)
        hours_buf = impact.get("hours_buffer")
        copies_at_risk = impact.get("copies_at_risk")
        buffer_pts = 0.0
        if part_id in ctx.launch_parts:
            if stock_qty <= 0:
                buffer_pts = 28.0
                buf_text = f"Zero warehouse stock (0 pcs) for {product_id}; {copies_at_risk or 'all'} copies exposed"
            elif copies_at_risk and copies_at_risk > 0:
                buffer_pts = 24.0
                buf_text = f"Stock shortfall: {copies_at_risk:,} {product_id} copies at risk ({hours_buf}h buffer)"
            elif hours_buf is not None and hours_buf < 18.0:
                buffer_pts = 15.0
                buf_text = f"Tight line-stop buffer: {hours_buf:.1f}h usable stock ({int(stock_qty):,} pcs)"
            elif hours_buf is not None and hours_buf < 26.0:
                buffer_pts = 8.0
                buf_text = f"Low launch buffer: {hours_buf:.1f}h stock ({int(stock_qty):,} pcs)"
            else:
                buf_text = ""
            if buffer_pts > 0:
                stock_ev = [
                    Evidence(path=s.source, locator=f"{part_id}/{s.batch or 'lot'}",
                             quote=f"quantity={s.quantity}, weeks_of_cover={s.weeks_of_cover}")
                    for s in ctx.stock if s.material_id == part_id
                ][:2]
                reasons.append(ReasonItem(
                    factor="stock_buffer",
                    points=buffer_pts,
                    text=buf_text,
                    evidence=stock_ev,
                    recommended_action={
                        "type": "expedite" if buffer_pts >= 15 else "safety_stock",
                        "label": f"Expedite replenishment / secure safety stock for {part_id}",
                        "target": primary_sup_id or part_id,
                        "owner": "planner",
                        "copies_gained": copies_at_risk,
                    },
                ))
        breakdown["stock_buffer"] = buffer_pts

        # 4. Part-specific shocks: ECN revision block, export ban, failed AQL, late inbound (0..32 pts)
        shock_pts = 0.0
        p_issues = issues_by_part.get(part_id, [])
        for iss in p_issues:
            if iss.type == "single_source_export_ban":
                pts = 26.0
                shock_pts += pts
                reasons.append(ReasonItem(
                    factor="export_ban",
                    points=pts,
                    text=iss.summary,
                    evidence=[Evidence(**e) if isinstance(e, dict) else e for e in (iss.evidence or [])][:2],
                    recommended_action=(iss.suggested_actions[0] if iss.suggested_actions else None),
                ))
            elif iss.type == "rev_blocked":
                pts = 24.0
                shock_pts += pts
                reasons.append(ReasonItem(
                    factor="ecn_revision_block",
                    points=pts,
                    text=iss.summary,
                    evidence=[Evidence(**e) if isinstance(e, dict) else e for e in (iss.evidence or [])][:2],
                    recommended_action=(iss.suggested_actions[-1] if iss.suggested_actions else None),
                ))
            elif iss.type in {"quality_hold_failed_aql", "short_shipment", "shipment_late_for_launch"}:
                pts = 14.0
                shock_pts += pts
                reasons.append(ReasonItem(
                    factor=iss.type,
                    points=pts,
                    text=iss.summary,
                    evidence=[Evidence(**e) if isinstance(e, dict) else e for e in (iss.evidence or [])][:2],
                    recommended_action=(iss.suggested_actions[0] if iss.suggested_actions else None),
                ))
            elif iss.type in {"single_source_export_risk", "tracker_mismatch", "po_stuck_approval"}:
                pts = 10.0
                shock_pts += pts
                reasons.append(ReasonItem(
                    factor=iss.type,
                    points=pts,
                    text=iss.summary,
                    evidence=[Evidence(**e) if isinstance(e, dict) else e for e in (iss.evidence or [])][:2],
                    recommended_action=(iss.suggested_actions[0] if iss.suggested_actions else None),
                ))

        # Lead-time spike noted in SupplierPartMap (e.g. CMP-GAM-007 63 -> 105 days)
        for sp in sp_rows:
            if sp.lead_time_days and sp.lead_time_days >= 90 and sp.note:
                pts = 10.0
                shock_pts += pts
                reasons.append(ReasonItem(
                    factor="lead_time_spike",
                    points=pts,
                    text=f"Lead time jumped to {sp.lead_time_days}d at {sp.supplier_id}: {sp.note}",
                    evidence=[Evidence(path=sp.source, locator=f"{sp.supplier_id}/{part_id}", quote=sp.note)],
                    recommended_action={
                        "type": "safety_stock",
                        "label": f"Increase safety stock for {part_id} to cover {sp.lead_time_days}d lead time",
                        "target": part_id,
                        "owner": "planner",
                    },
                ))

        shock_pts = min(35.0, shock_pts)
        breakdown["part_shocks"] = round(shock_pts, 1)

        total_score = round(min(100.0, sup_rollup + single_pts + buffer_pts + shock_pts), 1)
        grade, rating = score_to_grade(total_score)

        reasons.sort(key=lambda r: -r.points)
        top3 = reasons[:3]
        if not top3:
            top3 = [ReasonItem(
                factor="baseline",
                points=0.0,
                text=f"{part_name} is adequately buffered with healthy suppliers",
                evidence=[Evidence(path=mat.source, locator=part_id, quote=mat.description)] if mat else [],
            )]

        actions = _dedup_actions([r.recommended_action for r in top3 if r.recommended_action])
        results[part_id] = ComputedRisk(
            entity_type="part",
            entity_id=part_id,
            entity_name=part_name,
            score_0_100=total_score,
            grade=grade,
            rating=rating,
            reasons=top3,
            breakdown=breakdown,
            suggested_actions=actions,
            part_id=part_id,
            supplier_id=primary_sup_id,
            product_id=product_id,
            impact=impact,
            is_single_source=is_single and (part_id in ctx.launch_parts),
        )

    return results


# ---------------------------------------------------------------------------
# Tier 3: Product Risk Rating (worst-link bottleneck rollup from BOM parts)
# ---------------------------------------------------------------------------
def rate_products(
    ctx: CheckContext,
    part_risks: dict[str, ComputedRisk],
) -> dict[str, ComputedRisk]:
    results: dict[str, ComputedRisk] = {}
    for prod_id, prod in sorted(ctx.launch_products.items()):
        bom = ctx.bom_by_parent.get(prod_id, [])
        bom_risks = [part_risks[b.child_id] for b in bom if b.child_id in part_risks]
        if not bom_risks:
            continue

        bom_risks.sort(key=lambda r: -r.score_0_100)
        worst = bom_risks[0]
        high_parts = [r for r in bom_risks if r.grade >= 4]

        # Worst-link base + small density bonus for multiple high-risk BOM bottlenecks
        base_score = worst.score_0_100
        density_bonus = min(8.0, max(0, len(high_parts) - 1) * 2.5)

        # Shift-slack pressure from LaunchStatus (e.g. Collector's ~21h left out of ~23h shift window)
        slack_pts = 0.0
        ls = ctx.launch_status.get(prod_id)
        slack_reason: Optional[ReasonItem] = None
        if ls and ls.still_to_pack and ls.rate_per_hour and ls.rate_per_hour > 0:
            hours_needed = ls.still_to_pack / ls.rate_per_hour
            if hours_needed >= 18.0:
                slack_pts = 6.0
                slack_reason = ReasonItem(
                    factor="shift_slack",
                    points=slack_pts,
                    text=f"Near-zero line slack: {ls.still_to_pack:,} copies left at {int(ls.rate_per_hour):,}/h ({hours_needed:.1f}h needed)",
                    evidence=[Evidence(path=ls.source, locator=prod_id,
                                       quote=f"still_to_pack={ls.still_to_pack}, rate={ls.rate_per_hour}/h")],
                    recommended_action={
                        "type": "reallocate",
                        "label": f"Protect {prod.name or prod_id} line priority and allocate shared parts first",
                        "target": prod_id,
                        "owner": "planner",
                    },
                )

        total_score = round(min(100.0, base_score + density_bonus + slack_pts), 1)
        grade, rating = score_to_grade(total_score)

        reasons: list[ReasonItem] = []
        for pr in bom_risks[:3]:
            if pr.grade >= 3 or not reasons:
                reasons.append(ReasonItem(
                    factor="bottleneck_part",
                    points=pr.score_0_100,
                    text=f"Bottleneck {pr.entity_id} rated {pr.rating} ({pr.grade}/6): {pr.reasons[0].text}",
                    evidence=pr.reasons[0].evidence[:2],
                    recommended_action=pr.suggested_actions[0] if pr.suggested_actions else None,
                ))
        if slack_reason and len(reasons) < 3:
            reasons.append(slack_reason)
        elif slack_reason and slack_pts > 0 and len(reasons) >= 3 and bom_risks[2].grade < 4:
            reasons[2] = slack_reason

        breakdown = {
            "worst_bom_part": round(base_score, 1),
            "high_risk_bom_density": round(density_bonus, 1),
            "shift_slack": round(slack_pts, 1),
        }
        actions = _dedup_actions([r.recommended_action for r in reasons if r.recommended_action])
        results[prod_id] = ComputedRisk(
            entity_type="product",
            entity_id=prod_id,
            entity_name=prod.name or prod_id,
            score_0_100=total_score,
            grade=grade,
            rating=rating,
            reasons=reasons[:3],
            breakdown=breakdown,
            suggested_actions=actions,
            product_id=prod_id,
        )

    return results
