"""One shared Claude helper for all agents.

- `complete(...)` -> text answer. `extract(...)` -> validated Pydantic object (structured output).
- MOCK_MODE=true (default): no network. Agents register deterministic mock answers with
  `register_mock(task, fn)`; unregistered tasks return None so callers can fall back to rules.
- Every call is written to the agent log (model, task, tokens, stop reason).
- Model defaults to claude-opus-5-5 (ANTHROPIC_MODEL); effort from LLM_EFFORT (default medium).
- `complete` uses server-side refusal fallbacks (beta) so a policy decline is retried on a fallback model.
"""
from __future__ import annotations

from typing import Any, Callable, Optional, TypeVar

from pydantic import BaseModel

from . import trace
from .config import get_settings

M = TypeVar("M", bound=BaseModel)

_MOCKS: dict[str, Callable[..., Any]] = {}
_client = None

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(RuntimeError):
    pass


def register_mock(task: str, fn: Callable[..., Any]) -> None:
    """fn(prompt=..., system=..., schema=...) -> str (complete) or schema instance/dict (extract)."""
    _MOCKS[task] = fn


def is_mock() -> bool:
    return get_settings().mock_mode


def _get_client():
    global _client
    if _client is None:
        import anthropic  # imported lazily so MOCK mode works without the SDK configured

        _client = anthropic.Anthropic()
    return _client


def _usage(resp) -> dict:
    usage = getattr(resp, "usage", None)
    return {
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "stop_reason": getattr(resp, "stop_reason", None),
        "model": getattr(resp, "model", None),
    }


def complete(
    prompt: str,
    *,
    agent: str,
    task: str,
    system: str = "",
    effort: Optional[str] = None,
    max_tokens: Optional[int] = None,
) -> Optional[str]:
    """Ask Claude for a text answer. Returns None in MOCK mode when no mock is registered."""
    settings = get_settings()
    if settings.mock_mode:
        fn = _MOCKS.get(task)
        answer = fn(prompt=prompt, system=system, schema=None) if fn else None
        trace.log(agent, "llm_call", f"[mock] {task}", data={"task": task, "mocked": True, "has_mock": fn is not None})
        return answer

    import anthropic

    client = _get_client()
    kwargs: dict[str, Any] = dict(
        model=settings.anthropic_model,
        max_tokens=max_tokens or settings.llm_max_tokens,
        messages=[{"role": "user", "content": prompt}],
        output_config={"effort": effort or settings.llm_effort},
        betas=[FALLBACK_BETA],
        fallbacks="default",
    )
    if system:
        kwargs["system"] = system
    try:
        resp = client.beta.messages.create(**kwargs)
    except anthropic.APIStatusError as exc:
        trace.log(agent, "error", f"llm {task}: {exc.status_code} {exc.message}", data={"task": task})
        raise LLMError(str(exc)) from exc
    except anthropic.APIConnectionError as exc:
        trace.log(agent, "error", f"llm {task}: connection error", data={"task": task})
        raise LLMError(str(exc)) from exc

    trace.log(agent, "llm_call", task, data={"task": task, **_usage(resp)})
    if resp.stop_reason == "refusal":
        return None
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


def extract(
    prompt: str,
    schema: type[M],
    *,
    agent: str,
    task: str,
    system: str = "",
    effort: Optional[str] = None,
    max_tokens: Optional[int] = None,
) -> Optional[M]:
    """Ask Claude for structured data validated against `schema` (Pydantic). None if mocked/refused."""
    settings = get_settings()
    if settings.mock_mode:
        fn = _MOCKS.get(task)
        result = fn(prompt=prompt, system=system, schema=schema) if fn else None
        if isinstance(result, dict):
            result = schema.model_validate(result)
        trace.log(agent, "llm_call", f"[mock] {task}", data={"task": task, "mocked": True, "has_mock": fn is not None})
        return result

    import anthropic

    client = _get_client()
    kwargs: dict[str, Any] = dict(
        model=settings.anthropic_model,
        max_tokens=max_tokens or settings.llm_max_tokens,
        messages=[{"role": "user", "content": prompt}],
        output_config={"effort": effort or settings.llm_effort},
        output_format=schema,
    )
    if system:
        kwargs["system"] = system
    try:
        resp = client.messages.parse(**kwargs)
    except anthropic.APIStatusError as exc:
        trace.log(agent, "error", f"llm {task}: {exc.status_code} {exc.message}", data={"task": task})
        raise LLMError(str(exc)) from exc
    except anthropic.APIConnectionError as exc:
        trace.log(agent, "error", f"llm {task}: connection error", data={"task": task})
        raise LLMError(str(exc)) from exc

    trace.log(agent, "llm_call", task, data={"task": task, **_usage(resp)})
    if resp.stop_reason == "refusal":
        return None
    return resp.parsed_output
