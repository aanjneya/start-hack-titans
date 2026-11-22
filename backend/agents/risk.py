"""Risk Rater agent ("Moody's for parts, suppliers & products", owner: aanjneya, .brain/tasks/6-risk-rater.md).

Fuses internal truth-store fundamentals & internal politics with live external signals
(Polymarket prediction odds, Open-Meteo weather/marine on exact port coordinates, portal + GDELT
political/trade news, and GPS/AIS telemetry) to produce deterministic 1..6 (AAA..CCC) ratings:
  Tier 1: Supplier  ->  Tier 2: Part  ->  Tier 3: Product (worst-link bottleneck rollup)

Every high risk (grade >= 5) emits a Finding Format v1 issue (.brain/DATA_FORMAT.md) with concrete
suggested actions for the Launch Planner ("I need the risk, but I want the solution"), plus a
grouped post-launch second-source candidates list (T6.5).
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Optional

from sqlmodel import select

from ..core import events
from ..core.coordinator import Event
from ..core.models import Evidence, Issue, IssueKind, RiskScore, Severity
from ..core.store import add_all, all_rows, latest_by, session
from .base import Agent, register
from .reconciler_rules import build_context
from .risk_breakers import evaluate_circuit_breakers
from .risk_scoring import ComputedRisk, rate_parts, rate_products, rate_suppliers
from .risk_signals import DEFAULT_PROVIDERS, SignalProvider, collect_external_signals


def _truncate(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


@register
class RiskRater(Agent):
    name = "risk"
    subscriptions = {events.TRUTH_UPDATED: "on_truth_updated", events.RISK_REFRESH_REQUESTED: "on_truth_updated"}

    #: Pluggable external providers (overridable in tests)
    providers: tuple[SignalProvider, ...] = DEFAULT_PROVIDERS

    def on_truth_updated(self, event: Event) -> None:
        """T6.1-T6.7: fetch external signals, rate Supplier -> Part -> Product, log changes, raise Findings."""
        payload = event.payload or {}
        data_root_str = payload.get("data_root")
        data_root: Optional[Path] = Path(data_root_str) if data_root_str else None

        ctx = build_context()
        if not (ctx.suppliers or ctx.materials):
            if event.name == events.RISK_REFRESH_REQUESTED:
                self.log("risk_rated", "Waiting for customer supplier or material records.",
                         data={"trigger": event.name, "external_signals": {}, "grade_changes": 0, "suppliers": 0})
            return

        with session() as s:
            open_issues = list(
                s.exec(select(Issue).where(Issue.status != "resolved", Issue.raised_by != self.name)).all()
            )

        countries = {(sup.country or "").upper() for sup in ctx.suppliers.values() if sup.country}
        signals, provider_counts, unavailable = collect_external_signals(
            countries=countries,
            shipments=ctx.shipments,
            documents=ctx.documents,
            reference_now=ctx.reference_now,
            data_root=data_root,
            providers=self.providers,
        )

        # Previous grades (for T6.4 grade-change audit logging)
        prev_scores = {
            (r.entity_type, r.entity_id): r
            for r in latest_by(all_rows(RiskScore), "entity_type", "entity_id")
        }
        for name, failure in unavailable.items():
            self.log("error", f"{name} unavailable: {failure['reason']}", data={
                "event": event.name, "provider": name, "last_known_at": failure["last_known_at"]})
        missing_baseline = any(f["last_known_at"] is None for f in unavailable.values())
        no_fresh_source = len(unavailable) == len(self.providers) and bool(unavailable)
        if prev_scores and (missing_baseline or (event.name == events.RISK_REFRESH_REQUESTED and no_fresh_source)):
            # A missing baseline could silently remove an existing contributor. With a baseline,
            # a healthy feed may still move the grade while another retains its last known input.
            self.log("risk_refresh_incomplete", "Previous grades retained until the missing source returns.",
                     data={"trigger": event.name, "unavailable": unavailable, "external_signals": provider_counts})
            return

        # 3-tier bottom-up scoring
        supplier_risks = rate_suppliers(ctx, open_issues, signals)
        part_risks = rate_parts(ctx, supplier_risks, open_issues, signals)
        product_risks = rate_products(ctx, part_risks)

        all_computed: list[ComputedRisk] = (
            list(supplier_risks.values())
            + list(part_risks.values())
            + list(product_risks.values())
        )

        # Persist RiskScore rows + detect grade changes
        to_insert: list[RiskScore] = []
        grade_changes: list[str] = []
        for comp in all_computed:
            reasons_payload = [r.as_dict() for r in comp.reasons]
            # Attach score & factor breakdown on first reason metadata so UI/judges can inspect exact math
            if reasons_payload:
                reasons_payload[0]["score_0_100"] = comp.score_0_100
                reasons_payload[0]["breakdown"] = comp.breakdown
                if comp.score_basis:
                    reasons_payload[0]["score_basis"] = comp.score_basis
                if comp.signal_inputs:
                    reasons_payload[0]["signal_inputs"] = comp.signal_inputs
                if comp.shipment_observations:
                    reasons_payload[0]["shipment_observations"] = comp.shipment_observations

            prev = prev_scores.get((comp.entity_type, comp.entity_id))
            if prev is None or prev.grade != comp.grade or prev.reasons != reasons_payload:
                to_insert.append(RiskScore(
                    entity_type=comp.entity_type,
                    entity_id=comp.entity_id,
                    grade=comp.grade,
                    rating=comp.rating,
                    reasons=reasons_payload,
                ))
            if prev is not None and prev.grade != comp.grade:
                delta = (
                    f"{comp.entity_name} ({comp.entity_id}): "
                    f"{prev.grade} ({prev.rating}) → {comp.grade} ({comp.rating}) — {comp.reasons[0].text}"
                )
                grade_changes.append(delta)
                self.log(
                    "grade_changed",
                    delta,
                    evidence=comp.reasons[0].evidence[:2],
                    data={
                        "entity_type": comp.entity_type,
                        "entity_id": comp.entity_id,
                        "old_grade": prev.grade,
                        "new_grade": comp.grade,
                        "score_0_100": comp.score_0_100,
                    },
                )

        if to_insert:
            add_all(to_insert)

        # T6.6 & T6.7: Raise Findings for every part or supplier with grade >= 5 (and auto-resolve if improved)
        active_fingerprints: set[str] = set()
        high_risk_entities = [
            c for c in list(supplier_risks.values()) + list(part_risks.values())
            if c.grade >= 5
        ]
        for comp in sorted(high_risk_entities, key=lambda x: (-x.grade, -x.score_0_100, x.entity_id)):
            # Keep a citation for each driver before filling additional evidence from the strongest one.
            top_ev = ([r.evidence[0] for r in comp.reasons if r.evidence]
                      + [e for r in comp.reasons for e in r.evidence[1:]])[:5]
            if not top_ev:
                top_ev = [Evidence(path="risk_engine", locator=comp.entity_id, quote=comp.reasons[0].text)]
            actions = comp.suggested_actions or [
                {
                    "type": "second_source" if comp.is_single_source else "safety_stock",
                    "label": f"Mitigate {comp.rating} risk on {comp.entity_name}",
                    "target": comp.entity_id,
                    "owner": "planner",
                }
            ]
            linked = next((item for item in comp.signal_inputs.get("regulatory_change", [])
                           if item.get("material_matches")), None)
            if linked:
                citation = Evidence(**linked["evidence"])
                if citation not in top_ev:
                    top_ev.append(citation)
                if not any(a.get("type") == "human_review" and "compliance" in a.get("label", "").lower() for a in actions):
                    actions = [*actions, dict(type="human_review", label="Verify the publication against the matched supplier articles.",
                                              target=comp.entity_id, owner="planner")]
            reason_lines = " | ".join(f"{r.text} (+{r.points:.0f}pts)" for r in comp.reasons[:3])
            embedded = comp.entity_type == "part" and comp.part_id in ctx.embedded
            verify_exposure = embedded or (comp.entity_type == "supplier" and not comp.impact.get("copies_at_risk"))
            issue = self.raise_issue(
                type="high_risk_rating",
                kind=IssueKind.HUMAN_CHECK if verify_exposure else IssueKind.COPIES_AT_RISK,
                severity=Severity.LINE_STOP if comp.grade >= 6 and not verify_exposure else Severity.HIGH,
                key=f"{comp.entity_type}/{comp.entity_id}",
                summary=_truncate(
                    f"Risk {comp.rating} ({comp.grade}/6) on {comp.entity_name}: {comp.reasons[0].text}"
                ),
                reason=f"Risk index {comp.score_0_100:.0f}/100 ({comp.rating}). Top drivers: {reason_lines}",
                subject_type=comp.entity_type,
                subject_id=comp.entity_id,
                part_id=comp.part_id,
                supplier_id=comp.supplier_id,
                product_id=comp.product_id,
                impact=comp.impact,
                suggested_actions=actions,
                evidence=top_ev,
                confidence=0.9,
            )
            active_fingerprints.add(issue.fingerprint)

        # T6.5: Single-source candidates for post-launch second-source qualification (CPO memo)
        single_source_parts = sorted(
            [c for c in part_risks.values() if c.is_single_source],
            key=lambda x: (-x.grade, -x.score_0_100, x.entity_id),
        )
        if single_source_parts:
            part_labels = [f"{p.entity_id} ({p.rating}, {p.supplier_id})" for p in single_source_parts]
            ss_ev = [
                e for p in single_source_parts[:6]
                for r in p.reasons if r.factor == "single_source"
                for e in r.evidence
            ][:8]
            instruction = next(((d, sentence.strip()) for d in ctx.documents if d.kind == "memo"
                for sentence in re.split(r"(?<=[.!?])\s+|\n", d.text or "")
                if re.search(r"\bsecond source for every part that has one supplier\b", sentence, re.I)), None)
            if instruction:
                doc, sentence = instruction
                ss_ev.insert(0, Evidence(path=doc.path, locator="sourcing priorities", quote=sentence))
            issue = self.raise_issue(
                type="second_source_candidates",
                kind=IssueKind.COPIES_AT_RISK,
                severity=Severity.MEDIUM,
                key="launch_single_source_list",
                summary=_truncate(
                    f"{len(single_source_parts)} single-source launch parts need 2nd sources: "
                    + ", ".join(p.entity_id for p in single_source_parts[:5])
                ),
                reason=((instruction[1] + " ") if instruction else "The sourcing records identify one supplier per component. ")
                       + f"Ranked by risk: {'; '.join(part_labels)}.",
                subject_type="part",
                subject_id=single_source_parts[0].entity_id,
                part_id=single_source_parts[0].entity_id,
                supplier_id=single_source_parts[0].supplier_id,
                product_id=single_source_parts[0].product_id,
                suggested_actions=[
                    {
                        "type": "second_source",
                        "label": f"Start second-source qualification for {p.entity_id} (current sole supplier: {p.supplier_id}, {p.rating})",
                        "target": p.entity_id,
                        "owner": "planner",
                    }
                    for p in single_source_parts[:4]
                ],
                evidence=ss_ev,
                confidence=0.95,
            )
            active_fingerprints.add(issue.fingerprint)

        # Auto-resolve any previous Risk Rater findings whose grade dropped below 5
        resolved = self._resolve_cleared(active_fingerprints)

        # Log tripped Risk-Triggered Circuit Breakers (Grade 5/6) for AI Observability
        breakers = evaluate_circuit_breakers(ctx, supplier_risks, product_risks)
        if breakers:
            summary_b = ", ".join(f"{b['entity_id']} ({b['rating']})" for b in breakers[:4])
            self.log(
                "circuit_breaker_tripped",
                f"{len(breakers)} circuit breaker(s) active: {summary_b}",
                data={"count": len(breakers), "entities": [b["entity_id"] for b in breakers]},
            )

        self.log(
            "risk_rated",
            f"rated {len(supplier_risks)} suppliers, {len(part_risks)} parts, {len(product_risks)} products "
            f"({len(high_risk_entities)} grade>=5, {len(grade_changes)} grade changes, {resolved} resolved)",
            data={
                "suppliers": len(supplier_risks),
                "parts": len(part_risks),
                "products": len(product_risks),
                "high_risk": len(high_risk_entities),
                "grade_changes": len(grade_changes),
                "external_signals": provider_counts,
                "unavailable": unavailable, "trigger": event.name,
            },
        )

        # Notify Reconciler & Planner that fresh risk ratings are in `risk_scores`
        self.emit(
            events.RISK_UPDATED,
            {
                "suppliers": {k: {"grade": v.grade, "rating": v.rating, "score": v.score_0_100}
                              for k, v in supplier_risks.items()},
                "parts": {k: {"grade": v.grade, "rating": v.rating, "score": v.score_0_100}
                          for k, v in part_risks.items()},
                "products": {k: {"grade": v.grade, "rating": v.rating, "score": v.score_0_100}
                             for k, v in product_risks.items()},
                "grade_changes": grade_changes,
                "external_signals": provider_counts,
            },
        )

    def _resolve_cleared(self, active_fingerprints: set[str]) -> int:
        resolved = 0
        with session() as s:
            open_own = s.exec(
                select(Issue).where(Issue.raised_by == self.name, Issue.status != "resolved")
            ).all()
        for issue in open_own:
            if issue.fingerprint not in active_fingerprints:
                self.resolve_issue(issue.id, "Risk grade improved below alert threshold")
                resolved += 1
        return resolved
