"""LaunchPlanner agent: STUB. Owner: see .brain/tasks/5-launch-planner.md (claim it there).

The stub only logs and keeps the event chain flowing so the whole loop runs end to end.
Replace the TODO bodies; keep the class name, `name` and subscriptions (or update them in one PR).
"""
from __future__ import annotations

from ..core import events
from ..core.coordinator import Event
from ..core.models import IssueKind  # noqa: F401
from .base import Agent, register


@register
class LaunchPlanner(Agent):
    name = "planner"
    subscriptions = {events.TRUTH_UPDATED: "on_truth_updated", events.ISSUE_RAISED: "on_issue", events.DECISION_APPROVED: "on_decision_approved", events.DECISION_REJECTED: "on_decision_rejected"}

    def on_truth_updated(self, event: Event) -> None:
        """TODO T5.1-T5.3, T5.9: recompute Forecast rows per product; emit forecast.updated."""
        self.log("todo", "stub: forecast not implemented yet")

    def on_issue(self, event: Event) -> None:
        if event.payload.get("kind") != IssueKind.COPIES_AT_RISK:
            return
        # TODO T5.4-T5.7: build a DecisionCard (options with cost_eur vs copies_gained, approver).
        self.log("todo", f"stub: would build a decision card for issue {event.payload.get('issue_id')}")

    def on_decision_approved(self, event: Event) -> None:
        """TODO T5.8: execute the chosen option (e.g. emit email.requested), recompute, close the loop."""
        self.log("todo", f"stub: would execute decision {event.payload.get('decision_id')}")

    def on_decision_rejected(self, event: Event) -> None:
        self.log("todo", f"stub: decision {event.payload.get('decision_id')} rejected")
