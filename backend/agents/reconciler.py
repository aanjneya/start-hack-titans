"""Reconciler & Gate agent (owner: aanjneya, .brain/tasks/3-reconciler-gate.md).

Inspects the canonical truth store after every drop or supplier reply, detects contradictions,
missing steps, revision blocks, quality/approval traps, fraud/quantity anomalies, and raises
deduplicated Findings (FINDING FORMAT v1, .brain/DATA_FORMAT.md) routed to Chaser, Launch Planner,
or human review. Never writes supply-chain data itself (only Ingest does).
"""
from __future__ import annotations

from sqlmodel import select

from ..core import events
from ..core.coordinator import Event
from ..core.models import Conflict, Issue, RiskScore, utcnow
from ..core.store import add_all, all_rows, latest_by, session
from .base import Agent, register
from .reconciler_rules import CandidateConflict, build_context, run_all_checks


@register
class ReconcilerGate(Agent):
    name = "reconciler"
    subscriptions = {
        events.TRUTH_UPDATED: "on_truth_updated",
        events.RISK_UPDATED: "on_risk_updated",
    }

    def on_truth_updated(self, event: Event) -> None:
        """T3.0-T3.11: run all checks on current truth, record conflicts, emit Findings, resolve fixed issues."""
        payload = event.payload or {}
        reply_issue_ids = set(payload.get("issue_ids") or [])
        changes = payload.get("changes") or []

        risk_by_part, risk_by_supplier = self._load_risk_scores()
        ctx = build_context(risk_by_part=risk_by_part, risk_by_supplier=risk_by_supplier)
        findings, conflicts = run_all_checks(ctx)

        new_conflicts = self._sync_conflicts(conflicts)

        active_fingerprints: set[str] = set()
        for f in findings:
            issue = self.raise_issue(
                type=f.type,
                kind=f.kind,
                severity=f.severity,
                key=f.key,
                summary=f.summary,
                reason=f.reason,
                subject_type=f.subject_type,
                subject_id=f.subject_id,
                part_id=f.part_id,
                supplier_id=f.supplier_id,
                shipment_id=f.shipment_id,
                po_number=f.po_number,
                product_id=f.product_id,
                impact=f.impact,
                suggested_actions=f.suggested_actions,
                evidence=f.evidence,
                confidence=f.confidence,
            )
            active_fingerprints.add(issue.fingerprint)

        # T3.10 & T3.11: close any previously open reconciler finding that no longer triggers
        resolved_count = self._resolve_cleared_issues(active_fingerprints, reply_issue_ids, bool(changes))

        self.log(
            "reconciled",
            f"{len(findings)} active findings ({new_conflicts} new conflicts, {resolved_count} resolved)",
            data={
                "findings": len(findings),
                "new_conflicts": new_conflicts,
                "resolved": resolved_count,
                "reply_issue_ids": sorted(reply_issue_ids),
            },
        )

    def on_risk_updated(self, event: Event) -> None:
        """Re-prioritise / escalate severity when Risk Rater assigns high risk grades (5-6)."""
        risk_by_part, risk_by_supplier = self._load_risk_scores()
        if not (risk_by_part or risk_by_supplier):
            return
        escalated = 0
        with session() as s:
            open_issues = s.exec(
                select(Issue).where(Issue.raised_by == self.name, Issue.status != "resolved")
            ).all()
            for issue in open_issues:
                grade = max(
                    risk_by_part.get(issue.part_id, 0),
                    risk_by_supplier.get(issue.supplier_id, 0),
                )
                if (grade >= 6 and issue.severity < 5
                        and (issue.impact or {}).get("copies_at_risk") != 0):
                    issue.severity = 5
                    issue.updated_at = utcnow()
                    s.add(issue)
                    escalated += 1
                elif grade == 5 and issue.severity < 4:
                    issue.severity = 4
                    issue.updated_at = utcnow()
                    s.add(issue)
                    escalated += 1
            if escalated:
                s.commit()
        self.log("risk_reprioritized", f"checked open findings against risk grades ({escalated} escalated)",
                 data={"escalated": escalated})

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _load_risk_scores() -> tuple[dict[str, int], dict[str, int]]:
        latest = latest_by(all_rows(RiskScore), "entity_type", "entity_id")
        by_part = {r.entity_id: r.grade for r in latest if r.entity_type == "part"}
        by_supplier = {r.entity_id: r.grade for r in latest if r.entity_type == "supplier"}
        return by_part, by_supplier

    @staticmethod
    def _sync_conflicts(candidates: list[CandidateConflict]) -> int:
        """Insert new conflicts and resolve open reconciler conflicts that disappeared."""
        existing = list(all_rows(Conflict))
        existing_keys = {
            (c.entity, c.entity_id, c.field, c.value_a, c.value_b): c for c in existing
        }
        active_triples = {(c.entity, c.entity_id, c.field) for c in candidates}

        to_add: list[Conflict] = []
        for cand in candidates:
            k = (cand.entity, cand.entity_id, cand.field, cand.value_a, cand.value_b)
            if k not in existing_keys:
                to_add.append(Conflict(
                    entity=cand.entity,
                    entity_id=cand.entity_id,
                    field=cand.field,
                    value_a=cand.value_a,
                    source_a=cand.source_a,
                    value_b=cand.value_b,
                    source_b=cand.source_b,
                    status="open",
                ))
                existing_keys[k] = to_add[-1]

        if to_add:
            add_all(to_add)

        # Auto-resolve shipment/PO conflicts we previously opened if they no longer disagree
        reconciler_entities = {"shipment", "purchase_order", "goods_receipt", "order_confirmation"}
        with session() as s:
            for c in s.exec(select(Conflict).where(Conflict.status == "open")).all():
                if c.entity in reconciler_entities and (c.entity, c.entity_id, c.field) not in active_triples:
                    c.status = "resolved"
                    c.resolved_at = utcnow()
                    s.add(c)
            s.commit()

        return len(to_add)

    def _resolve_cleared_issues(
        self,
        active_fingerprints: set[str],
        reply_issue_ids: set[int],
        had_changes: bool,
    ) -> int:
        resolved = 0
        with session() as s:
            open_issues = s.exec(
                select(Issue).where(Issue.raised_by == self.name, Issue.status != "resolved")
            ).all()
        for issue in open_issues:
            if issue.fingerprint not in active_fingerprints:
                reason = (
                    "Resolved after supplier reply updated truth store"
                    if (issue.id in reply_issue_ids or had_changes)
                    else "Resolved by updated portal data"
                )
                self.resolve_issue(issue.id, reason)
                resolved += 1
        return resolved
