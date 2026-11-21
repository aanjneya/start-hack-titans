"""IngestNormalizer agent: STUB. Owner: see .brain/tasks/2-ingest-normalizer.md (claim it there).

The stub only logs and keeps the event chain flowing so the whole loop runs end to end.
Replace the TODO bodies; keep the class name, `name` and subscriptions (or update them in one PR).
"""
from __future__ import annotations

from ..core import events
from ..core.coordinator import Event
from ..core.models import IssueKind  # noqa: F401
from .base import Agent, register


@register
class IngestNormalizer(Agent):
    name = "ingest"
    subscriptions = {events.FILES_CHANGED: "on_files_changed", events.REPLY_RECEIVED: "on_reply"}

    def on_files_changed(self, event: Event) -> None:
        """TODO T2.1-T2.10: read each added/changed file under event.payload["data_root"] into the
        truth-store tables (never overwrite; source + ingested_at on every row)."""
        p = event.payload
        files = list(p.get("added", [])) + list(p.get("changed", []))
        self.log("todo", f"stub: would ingest {len(files)} files ({len(p.get('removed', []))} removed)")
        # Keep the loop flowing so downstream agents run on every drop.
        self.emit(events.TRUTH_UPDATED, {"files": files, "data_root": p.get("data_root"), "tables": []})

    def on_reply(self, event: Event) -> None:
        """TODO T2.19: apply a supplier reply (you are the ONLY writer of supply-chain data).

        1. Load InboxMessage(event.payload["inbox_id"]) (+ the issue it answers, if any).
        2. Extract the answer with llm.extract(...) (quote + confidence), find the row(s) it is about.
        3. self.update_row(Model, row_id, {field: value}, source=f"inbox/{msg.id}", issue_id=..., reason=...)
           -> fills empty fields, records a conflict if a different value is already there.
        4. Mark the inbox message processed, then self.emit_truth_updated([results], issue_ids=[...])
           (it only emits if something changed, so the Reconciler re-checks exactly once).
        """
        self.log("todo", f"stub: reply {event.payload.get('inbox_id')} not applied yet")
