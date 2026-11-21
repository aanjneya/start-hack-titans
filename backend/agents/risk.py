"""RiskRater agent: STUB. Owner: see .brain/tasks/6-risk-rater.md (claim it there).

The stub only logs and keeps the event chain flowing so the whole loop runs end to end.
Replace the TODO bodies; keep the class name, `name` and subscriptions (or update them in one PR).
"""
from __future__ import annotations

from ..core import events
from ..core.coordinator import Event
from ..core.models import IssueKind  # noqa: F401
from .base import Agent, register


@register
class RiskRater(Agent):
    name = "risk"
    subscriptions = {events.TRUTH_UPDATED: "on_truth_updated"}

    def on_truth_updated(self, event: Event) -> None:
        """TODO T6.1-T6.5: compute RiskScore rows (grade 1-6 + top-3 reasons), then emit risk.updated."""
        self.log("todo", "stub: risk scores not implemented yet")
