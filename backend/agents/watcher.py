"""Watcher agent: STUB. Owner: see .brain/tasks/1-watcher.md (claim it there).

The stub only logs and keeps the event chain flowing so the whole loop runs end to end.
Replace the TODO bodies; keep the class name, `name` and subscriptions (or update them in one PR).
"""
from __future__ import annotations

from ..core import events
from ..core.coordinator import Event
from ..core.models import IssueKind  # noqa: F401
from .base import Agent, register


@register
class Watcher(Agent):
    name = "watcher"
    subscriptions = {}

    # No subscriptions: the Watcher is triggered by a timer (T1.5), POST /api/run or the replay.
    def check(self) -> dict:
        """TODO T1.1-T1.4: diff the portal file list (SHA-256), download changed files and call
        system.process_drop(added=..., changed=..., removed=..., label=..., data_root=...)."""
        self.log("todo", "Watcher.check not implemented yet (see .brain/tasks/1-watcher.md)")
        return {}
