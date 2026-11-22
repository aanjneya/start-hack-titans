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

RECORD_IDS = ("decision_id", "inbox_id", "outbox_id")


@dataclass
class Event:
    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = ""  # agent that emitted it
    target: str = ""  # retry a failed subscriber without repeating successful siblings
    cause: dict = field(default_factory=dict)


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

    def emit(self, name: str, payload: dict[str, Any] | None = None, source: str = "", *, target: str = "") -> None:
        parent = trace.event_context.get()
        cause = {key: parent[key] for key in RECORD_IDS if key in parent}
        self._queue.append(Event(name, payload or {}, source, target, cause))

    def pending(self) -> int:
        return len(self._queue)

    def run_until_idle(self, *, record_retries: bool = False) -> int:
        """Process queued events (and the ones they emit). Returns how many handler calls ran."""
        steps = 0
        while self._queue:
            if steps >= self.max_steps:
                trace.log("coordinator", "error", f"stopped after {steps} steps (possible event loop)",
                          data={"event": self._queue[0].name})
                self._queue.clear()
                break
            event = self._queue.popleft()
            subs = self._subs.get(event.name, [])
            if event.target:
                subs = [sub for sub in subs if sub.agent == event.target]
                if not subs:
                    trace.log(event.target, "error", "Replay subscriber unavailable", data={"event": event.name})
                    continue
            if not subs:
                trace.log("coordinator", "unhandled", event.name, data={"source": event.source})
            for sub in subs:
                steps += 1
                context = {"event": event.name, **event.cause}
                context.update({key: event.payload[key] for key in RECORD_IDS
                                if event.payload.get(key) is not None})
                if record_retries:
                    context["retry"] = dict(name=event.name, payload=event.payload, source=event.source, target=sub.agent)
                token = trace.event_context.set(context)
                try:
                    sub.handler(event)
                except Exception as exc:  # one agent failing must not stop the loop
                    trace.log(
                        sub.agent,
                        "error",
                        f"{type(exc).__name__} while handling {event.name}: {exc}",
                        data={"event": event.name, "traceback": traceback.format_exc()[-2000:]},
                    )
                finally:
                    trace.event_context.reset(token)
        self.handled += steps
        return steps
