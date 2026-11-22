"""Launch Planner and Decision Copilot. Only new evidence changes the baseline."""
from __future__ import annotations

from sqlalchemy import text, update
from sqlmodel import select

from ..core import events, llm, trace
from ..core.coordinator import Event
from ..core.models import DecisionCard, Forecast, Issue, IssueKind, OutboxMessage, RiskScore, SupplierPartMap, utcnow
from ..core.parsing import parse_datetime
from ..core.store import all_rows, session
from .base import Agent, register
from .planner_delivery import already_requested, review_key
from .planner_forecast import simulate
from .planner_inputs import load_plan
from .planner_options import options_for

llm.register_mock("planner.wording", lambda prompt, **_: prompt.splitlines()[0])
DISPATCH_CHECK = "planner.dispatch.checked"


@register
class LaunchPlanner(Agent):
    name = "planner"
    subscriptions = {events.TRUTH_UPDATED: "on_truth_updated", events.ISSUE_RAISED: "on_issue",
                     events.RISK_UPDATED: "on_risk", events.DECISION_APPROVED: "on_decision_approved",
                     events.DECISION_REJECTED: "on_decision_rejected", DISPATCH_CHECK: "on_dispatch_checked"}

    def __init__(self, bus):
        super().__init__(bus)
        self._refreshed = {}
        # Restore approvals committed immediately before a process interruption.
        outdated_baseline = False
        for card in all_rows(DecisionCard):
            if card.created_by != self.name:
                continue
            if card.status == "proposed" and card.recommended == 0 and card.options:
                baseline = card.options[0]
                if baseline.get("description") == "baseline" and (baseline.get("remaining_shortfall") or 0) > 0:
                    outdated_baseline = True
            if card.status == "proposed" and card.options and "product_id" not in card.options[0]:
                outdated_baseline = True
            if card.status == "approved":
                self.emit(events.DECISION_APPROVED, {"decision_id": card.id, "option": card.chosen_option})

        # Existing installations may already have a stale recommendation without another data drop.
        if outdated_baseline:
            plan = load_plan()
            if plan:
                self._refresh(plan, simulate(plan))

    def on_truth_updated(self, event: Event) -> None:
        plan = load_plan()
        if not plan:
            self.log("awaiting_inputs", "Waiting for a dated edition snapshot, launch date and shift calendar.")
            return
        forecasts = simulate(plan)
        changed = []
        with session() as s:
            for pid, forecast in forecasts.items():
                previous = s.exec(select(Forecast).where(Forecast.product_id == pid).order_by(Forecast.id.desc())).first()
                ignore = {"id", "computed_at", "deadline"}  # Compare instants separately from JSON payloads.
                if previous and previous.model_dump(exclude=ignore) == forecast.model_dump(exclude=ignore) and (
                    parse_datetime(previous.deadline) == forecast.deadline
                ):
                    continue
                s.add(forecast)
                s.flush()
                changed.append(forecast.id)
            s.commit()
        if changed:
            self.log("forecast", "Recomputed line-ready packing capacity", plan.evidence, {"forecast_ids": changed})
            self.emit(events.FORECAST_UPDATED, {"forecast_ids": changed})
        self._forecast_findings(plan, forecasts)
        self._refresh(plan, forecasts)

    def _forecast_findings(self, plan, forecasts):
        """Report projected shortages in Finding v1; prefer an existing Reconciler finding."""
        active = set()
        current = list(all_rows(Issue))
        for row in plan.editions:
            pid = row.product_id
            f = forecasts[pid]
            if not f.shortfall:
                continue
            parts = {x["part"] for x in f.details["lots"] if x["status"] == "revision blocked"
                     and plan.stock[x["part"]] < row.still_to_pack * plan.bom[pid][x["part"]]}
            if f.limiting_part:
                parts.add(f.limiting_part)
            for part in sorted(parts):
                if any(i.kind == IssueKind.COPIES_AT_RISK and i.raised_by != self.name and i.part_id == part
                       and i.product_id in ("", pid) and i.status != "resolved" for i in current):
                    continue
                proof = [e for x in f.details["lots"] if x["part"] == part for e in x["evidence"]]
                nearby = [x for x in f.details["inbound"] if x["part"] == part and x["eta"]
                          and x["eta"][:10] <= plan.deadline.date().isoformat() and not x["revision_blocked"]]
                inbound = min(nearby, key=lambda x: (x["eta"], x["shipment_id"])) if nearby else {}
                msg = f"{row.edition} projects {f.shortfall:,} copies short; verify coverage for {part}."
                issue = self.raise_issue(type="forecast_exposure", key=f"{pid}/{part}", kind=IssueKind.COPIES_AT_RISK,
                                         severity=4, summary=msg, part_id=part, product_id=pid,
                                         supplier_id=inbound.get("supplier_id", ""), po_number=inbound.get("po_number", ""),
                                         shipment_id=inbound.get("shipment_id", ""),
                                         reason="The edition shortfall includes constraints from other parts.",
                                         impact={"product_id": pid, "copies_at_risk": f.shortfall},
                                         suggested_actions=[{"type": "email" if inbound else "human_review",
                                                             "owner": "chaser" if inbound else "human",
                                                             "label": "Verify line-ready availability"}],
                                         evidence=proof + inbound.get("evidence", []) or plan.evidence)
                active.add(issue.id)
        for issue in current:
            if issue.raised_by == self.name and issue.type == "recovery_review" and issue.status != "resolved":
                current_forecast = forecasts.get(issue.product_id)
                if current_forecast and current_forecast.shortfall == 0:
                    self.resolve_issue(issue.id, "Current evidence covers the edition's outstanding demand.")
            if issue.raised_by == self.name and issue.type == "forecast_exposure" and issue.id not in active:
                self.resolve_issue(issue.id, "The current projection supersedes this exposure.")

    def on_risk(self, event):
        plan = load_plan()
        if plan:
            self._refresh(plan, simulate(plan))

    def _refresh(self, plan, forecasts):
        issues = list(all_rows(Issue))
        self._refreshed = {}
        for issue in issues:
            if issue.kind == IssueKind.COPIES_AT_RISK and issue.status != "resolved":
                self._card(issue, plan, forecasts)
                self._refreshed[issue.id] = (trace.current_run_id(), issue.updated_at)
        closed = {i.id for i in issues if i.status == "resolved"}
        with session() as s:
            for card in s.exec(select(DecisionCard).where(DecisionCard.status == "proposed")).all():
                if closed.intersection(card.issue_ids):
                    card.status = "outdated"
                    s.add(card)
            s.commit()

    def on_issue(self, event):
        if event.payload.get("kind") != IssueKind.COPIES_AT_RISK:
            return
        with session() as s:
            issue = s.get(Issue, event.payload["issue_id"])
        if issue and self._refreshed.get(issue.id) == (trace.current_run_id(), issue.updated_at):
            return
        plan = load_plan()
        if issue and issue.status != "resolved" and plan:
            self._card(issue, plan, simulate(plan))

    def _card(self, issue, plan, forecasts):
        product = issue.product_id or (issue.impact or {}).get("product_id")
        parts = {issue.part_id} if issue.part_id else {p.component_id for p in all_rows(SupplierPartMap)
                                                       if issue.supplier_id and p.supplier_id == issue.supplier_id}
        products = [r for r in plan.editions if (r.product_id == product if product else
                    bool(parts.intersection(plan.bom.get(r.product_id, {}))))]
        for row in products:
            f = forecasts[row.product_id]
            options, recommended, due = options_for(issue, plan, f, row)
            selected = options[recommended]
            grades = {r.entity_id: r.grade for r in sorted(all_rows(RiskScore), key=lambda r: r.id)
                      if r.entity_id in {issue.supplier_id, issue.part_id}}
            reasoning = f"{issue.reason} Projected total: {f.projected_packed}; shortfall: {f.shortfall}. " + selected["risk_note"]
            reported = (issue.impact or {}).get("copies_at_risk")
            if reported is not None:
                reasoning += f" The originating finding estimates {reported} copies exposed."
            if grades:
                reasoning += " Risk grades: " + ", ".join(f"{key} {grades[key]}/6" for key in sorted(grades)) + "."
            title = f"{row.edition} · {issue.type.replace('_', ' ')} · {issue.part_id or issue.subject_id}"
            proof = issue.evidence + plan.evidence + [e for o in options for e in o["evidence"]]
            proof = list({(e["path"], e.get("locator", "")): e for e in proof}.values())
            with session() as s:
                previous = next((c for c in s.exec(select(DecisionCard).where(DecisionCard.created_by == self.name)
                                .order_by(DecisionCard.id.desc())) if issue.id in c.issue_ids and c.title == title), None)
                if previous and previous.status == "rejected" and previous.recommended is not None:
                    old = previous.options[previous.recommended]
                    if (previous.evidence == proof and previous.reasoning == reasoning and
                            all(old.get(k) == selected.get(k) for k in ("actions", "cost_eur", "copies_gained"))):
                        continue
                if (previous and previous.status != "outdated" and previous.options == options
                        and previous.reasoning == reasoning and previous.evidence == proof
                        and previous.recommended == recommended and parse_datetime(previous.decide_by) == due):
                    continue
                headline = f"{row.edition}: {f.shortfall} copies short. {selected['label']}."
                alternate = f"{selected['label']}. {row.edition} projects a shortfall of {f.shortfall} copies."
                wording = [headline, alternate]
                try:
                    choice = llm.complete("\n".join(wording), system="Return the clearest of these two headlines verbatim.",
                                          agent=self.name, task="planner.wording")
                    summary = choice if choice in wording else headline
                except llm.LLMError:
                    summary = headline
                card = DecisionCard(issue_ids=[issue.id], title=title, summary=summary,
                                    problem=issue.summary, reasoning=reasoning, evidence=proof, options=options,
                                    recommended=recommended, approver=" + ".join(selected["approvers"]),
                                    approver_reason=f"Customer approval matrix on €{selected['approval_value_eur']} "
                                                    f"for the selected action.",
                                    needs_approval=selected["needs_approval"], decide_by=due, created_by=self.name)
                # Wording logs through a separate connection. Claim only after that call returns.
                s.execute(text("BEGIN IMMEDIATE"))
                if previous:
                    s.execute(update(DecisionCard).where(
                        DecisionCard.id == previous.id, DecisionCard.status == "proposed").values(status="outdated"))
                auto = (not card.needs_approval and selected["available"] and bool(selected["actions"])
                        and not already_requested(s, selected, card))
                if auto:
                    card.status, card.chosen_option, card.decided_by, card.decided_at = "approved", recommended, "policy", utcnow()
                s.add(card)
                s.commit()
                s.refresh(card)
            self.log("decision_created", summary, proof, {"decision_id": card.id, "issue_id": issue.id})
            self.emit(events.DECISION_CREATED, {"decision_id": card.id})
            if auto:
                self.emit(events.DECISION_APPROVED, {"decision_id": card.id, "option": recommended, "by": "policy"})

    def on_decision_approved(self, event):
        with session() as s:
            card = s.get(DecisionCard, event.payload["decision_id"])
            if not card or card.created_by != self.name or card.status != "approved":
                return
            selected = card.options[card.chosen_option]
            if not selected["actions"]:
                card.status = "executed"
                s.add(card)
                s.commit()
                return
            exists = s.exec(select(OutboxMessage).where(OutboxMessage.decision_id == card.id)).first()
        review = next((a for a in selected["actions"] if a["type"] == "human_review"), None)
        if review:
            self.raise_issue(type="recovery_review", key=review_key(review),
                             kind=IssueKind.HUMAN_CHECK, severity=4, summary=selected["label"][:160],
                             product_id=review["product_id"], part_id=review["part"],
                             reason=f"{card.approver}: {selected['risk_note']}",
                             impact={"product_id": review["product_id"], "copies_at_risk": review["copies_at_risk"],
                                     "due_by": card.decide_by.isoformat() if card.decide_by else None},
                             evidence=[{"path": f"decisions/{card.id}", "locator": f"approved option {card.chosen_option}"},
                                       *selected["evidence"]])
            with session() as s:
                dispatched = s.get(DecisionCard, card.id)
                dispatched.status = "executed"
                s.add(dispatched)
                s.commit()
            return
        if not exists:
            for action in selected["actions"]:
                if action["type"] == "email":
                    self.emit(events.EMAIL_REQUESTED, {**action, "issue_id": card.issue_ids[0], "decision_id": card.id})
            self.log("handoff", "Requested Chaser dispatch; awaiting supplier evidence.", data={"decision_id": card.id})
        self.emit(DISPATCH_CHECK, {"decision_id": card.id})

    def on_dispatch_checked(self, event):
        with session() as s:
            card = s.get(DecisionCard, event.payload["decision_id"])
            sent = s.exec(select(OutboxMessage).where(OutboxMessage.decision_id == card.id,
                          OutboxMessage.status.in_(["sent", "replied"]))).first()
            if sent and card.status == "approved":
                card.status = "executed"
                s.add(card)
                s.commit()

    def on_decision_rejected(self, event):
        self.log("decision_rejected", "Closed without dispatch.", data={"decision_id": event.payload["decision_id"]})
