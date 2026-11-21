"""Chaser agent: STUB. Owner: see .brain/tasks/4-chaser.md (claim it there).

The stub only logs and keeps the event chain flowing so the whole loop runs end to end.
Replace the TODO bodies; keep the class name, `name` and subscriptions (or update them in one PR).
"""
from __future__ import annotations

from ..core import events
from ..core.coordinator import Event
from ..core.models import IssueKind  # noqa: F401
from .base import Agent, register


@register
class Chaser(Agent):
    name = "chaser"
    subscriptions = {events.ISSUE_RAISED: "on_issue", events.EMAIL_REQUESTED: "on_email_requested"}

    def on_issue(self, event: Event) -> None:
        if event.payload.get("kind") != IssueKind.MISSING_INFO:
            return
        # TODO T4.1-T4.6: draft a rate-limited email in the supplier's language into the outbox.
        self.log("todo", f"stub: would chase for issue {event.payload.get('issue_id')}")

    # When a reply arrives (real mailbox or the mock supplier simulator), do NOT write data yourself:
    #   msg = system.store_reply(supplier_id=..., body=..., issue_id=..., outbox_id=...)
    #   self.emit(events.REPLY_RECEIVED, {"inbox_id": msg.id, "supplier_id": ..., "issue_id": ...})
    # Ingest applies it to the source of truth and the Reconciler re-checks. (T4.5)

    def on_email_requested(self, event: Event) -> None:
        self.log("todo", "stub: email requested by planner not sent yet")
