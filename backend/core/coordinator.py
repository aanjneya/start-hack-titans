"""Coordinator: a tiny in-process event bus that routes hand-offs between agents.

- Agents subscribe to event names (core/events.py) and emit new events.
- Events are processed in order (FIFO) until nothing is left (`run_until_idle`).
- Every handled event and every error is written to the agent log, so the trace shows the chain.
- A failing agent never stops the others.
"""
from __future__ import annotations

import traceback
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from . import trace


@dataclass
class Event:
    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = ""  # agent that emitted it


Handler = Callable[[Event], None]


@dataclass
class _Subscription:
    agent: str
    handler: Handler


class Coordinator:
    def __init__(self, max_steps: int = 10_000) -> None:
        self._subs: dict[str, list[_Subscription]] = {}
        self._queue: deque[Event] = deque()
        self.max_steps = max_steps
        self.handled = 0

    def subscribe(self, event_name: str, agent: str, handler: Handler) -> None:
        self._subs.setdefault(event_name, []).append(_Subscription(agent, handler))

    def subscribers(self, event_name: str) -> list[str]:
        return [s.agent for s in self._subs.get(event_name, [])]

    def emit(self, name: str, payload: dict[str, Any] | None = None, source: str = "") -> None:
        self._queue.append(Event(name, payload or {}, source))

    def pending(self) -> int:
        return len(self._queue)

    def run_until_idle(self) -> int:
        """Process queued events (and the ones they emit). Returns how many handler calls ran."""
        steps = 0
        while self._queue:
            if steps >= self.max_steps:
                trace.log("coordinator", "error", f"stopped after {steps} steps (possible event loop)")
                self._queue.clear()
                break
            event = self._queue.popleft()
            subs = self._subs.get(event.name, [])
            if not subs:
                trace.log("coordinator", "unhandled", event.name, data={"source": event.source})
            for sub in subs:
                steps += 1
                try:
                    sub.handler(event)
                except Exception as exc:  # one agent failing must not stop the loop
                    trace.log(
                        sub.agent,
                        "error",
                        f"{type(exc).__name__} while handling {event.name}: {exc}",
                        data={"event": event.name, "traceback": traceback.format_exc()[-2000:]},
                    )
        self.handled += steps
        return steps
