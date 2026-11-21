"""ReconcilerGate agent: STUB. Owner: see .brain/tasks/3-reconciler-gate.md (claim it there).

The stub only logs and keeps the event chain flowing so the whole loop runs end to end.
Replace the TODO bodies; keep the class name, `name` and subscriptions (or update them in one PR).
"""
from __future__ import annotations

from ..core import events
from ..core.coordinator import Event
from ..core.models import IssueKind  # noqa: F401
from .base import Agent, register


@register
class ReconcilerGate(Agent):
    name = "reconciler"
    subscriptions = {events.TRUTH_UPDATED: "on_truth_updated", events.RISK_UPDATED: "on_risk_updated"}

    def on_truth_updated(self, event: Event) -> None:
        """TODO T3.1-T3.10: run all checks; use self.raise_issue(type=..., kind=IssueKind..., key=...)."""
        self.log("todo", "stub: checks not implemented yet")

    def on_risk_updated(self, event: Event) -> None:
        self.log("todo", "stub: re-prioritise checks by risk")
