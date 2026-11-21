"""One shared AI helper for all agents: Claude (default) or OpenAI, same two functions.

- `complete(...)` -> text answer. `extract(...)` -> validated Pydantic object (structured output).
  `extract(..., files=[...])` also sends images/PDFs (e.g. scanned delivery notes for OCR).
- MOCK_MODE=true (default): no network. Agents register deterministic mock answers with
  `register_mock(task, fn)`; unregistered tasks return None so callers can fall back to rules.
- Provider (LLM_PROVIDER): "anthropic" | "openai" | "auto" (default: Claude if ANTHROPIC_API_KEY is set,
  else OpenAI if OPENAI_API_KEY is set). Keys are read from .env / environment.
  Claude: model ANTHROPIC_MODEL (default claude-opus-5-5), effort LLM_EFFORT, server-side refusal fallbacks.
  OpenAI: model OPENAI_MODEL (default gpt-5.4-mini), optional OPENAI_REASONING_EFFORT.
- Every call is written to the agent log (provider, model, task, tokens). Any failure raises LLMError,
  which callers treat as "no answer" (the drop never fails because the AI is down).
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, TypeVar

from pydantic import BaseModel

from . import trace
from .config import get_settings

M = TypeVar("M", bound=BaseModel)

_MOCKS: dict[str, Callable[..., Any]] = {}
_clients: dict[str, Any] = {}

FALLBACK_BETA = "server-side-fallback-2026-07-01"
_MEDIA = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
          ".webp": "image/webp", ".pdf": "application/pdf"}


class LLMError(RuntimeError):
    pass


def register_mock(task: str, fn: Callable[..., Any]) -> None:
    """fn(prompt=..., system=..., schema=..., files=[...]) -> str (complete) or schema instance/dict (extract).
    Accept **kwargs in your mock so new arguments never break it."""
    _MOCKS[task] = fn


def is_mock() -> bool:
    return get_settings().mock_mode


def provider() -> str:
    s = get_settings()
    if s.llm_provider in {"anthropic", "openai"}:
        return s.llm_provider
    if s.anthropic_api_key:
        return "anthropic"
    if s.openai_api_key:
        return "openai"
    return "anthropic"


def reset_clients() -> None:
    _clients.clear()


def _anthropic_client():
    if "anthropic" not in _clients:
        import anthropic

        s = get_settings()
        # explicit key + base URL from .env, so a shell's ANTHROPIC_BASE_URL can't redirect calls
        _clients["anthropic"] = anthropic.Anthropic(api_key=s.anthropic_api_key or None,
                                                    base_url=s.anthropic_base_url or "https://api.anthropic.com")
    return _clients["anthropic"]


def _openai_client():
    if "openai" not in _clients:
        from openai import OpenAI

        s = get_settings()
        _clients["openai"] = OpenAI(api_key=s.openai_api_key or None, base_url=s.openai_base_url or None)
    return _clients["openai"]


def _b64(path: Path) -> tuple[str, str]:
    media = _MEDIA.get(Path(path).suffix.lower())
    if media is None:
        raise ValueError(f"unsupported file type for the AI: {path}")
    return media, base64.standard_b64encode(Path(path).read_bytes()).decode()


def _content(prompt: str, files: Sequence[Path] = ()) -> list[dict] | str:
    """Claude message content: file blocks (images/PDFs, base64) followed by the text."""
    if not files:
        return prompt
    blocks: list[dict] = []
    for f in files:
        media, data = _b64(f)
        kind = "document" if media == "application/pdf" else "image"
        blocks.append({"type": kind, "source": {"type": "base64", "media_type": media, "data": data}})
    blocks.append({"type": "text", "text": prompt})
    return blocks


def _openai_content(prompt: str, files: Sequence[Path] = ()) -> list[dict] | str:
    """OpenAI chat content: image_url / file parts (data URLs) followed by the text."""
    if not files:
        return prompt
    parts: list[dict] = []
    for f in files:
        media, data = _b64(f)
        url = f"data:{media};base64,{data}"
        if media == "application/pdf":
            parts.append({"type": "file", "file": {"filename": Path(f).name, "file_data": url}})
        else:
            parts.append({"type": "image_url", "image_url": {"url": url}})
    parts.append({"type": "text", "text": prompt})
    return parts


def _fail(agent: str, task: str, exc: Exception) -> LLMError:
    status = getattr(exc, "status_code", None)
    detail = f"{status} " if status else ""
    trace.log(agent, "error", f"llm {task}: {detail}{type(exc).__name__}: {str(exc)[:200]}",
              data={"task": task, "provider": provider()})
    return LLMError(str(exc))


def _mocked(task: str, agent: str, **kwargs):
    fn = _MOCKS.get(task)
    result = fn(**kwargs) if fn else None
    trace.log(agent, "llm_call", f"[mock] {task}", data={"task": task, "mocked": True, "has_mock": fn is not None})
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def complete(prompt: str, *, agent: str, task: str, system: str = "", effort: Optional[str] = None,
             max_tokens: Optional[int] = None) -> Optional[str]:
    """Text answer. Returns None in MOCK mode when no mock is registered, or on a refusal."""
    s = get_settings()
    if s.mock_mode:
        return _mocked(task, agent, prompt=prompt, system=system, schema=None)
    if provider() == "openai":
        return _openai_complete(prompt, agent=agent, task=task, system=system, max_tokens=max_tokens)
    client = _anthropic_client()
    kwargs: dict[str, Any] = dict(
        model=s.anthropic_model, max_tokens=max_tokens or s.llm_max_tokens,
        messages=[{"role": "user", "content": prompt}], output_config={"effort": effort or s.llm_effort},
        betas=[FALLBACK_BETA], fallbacks="default",
    )
    if system:
        kwargs["system"] = system
    try:
        resp = client.beta.messages.create(**kwargs)
    except Exception as exc:
        raise _fail(agent, task, exc) from exc
    trace.log(agent, "llm_call", task, data={"task": task, "provider": "anthropic", "model": resp.model,
                                             "input_tokens": resp.usage.input_tokens,
                                             "output_tokens": resp.usage.output_tokens,
                                             "stop_reason": resp.stop_reason})
    if resp.stop_reason == "refusal":
        return None
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


def extract(prompt: str, schema: type[M], *, agent: str, task: str, system: str = "", effort: Optional[str] = None,
            max_tokens: Optional[int] = None, files: Sequence[Path] = ()) -> Optional[M]:
    """Structured data validated against `schema` (Pydantic). None if mocked without a mock, or refused.

    `files`: images (jpg/png/...) or PDFs the model should read, e.g. scanned delivery notes (OCR).
    """
    s = get_settings()
    if s.mock_mode:
        result = _mocked(task, agent, prompt=prompt, system=system, schema=schema, files=list(files))
        return schema.model_validate(result) if isinstance(result, dict) else result
    if provider() == "openai":
        return _openai_extract(prompt, schema, agent=agent, task=task, system=system, max_tokens=max_tokens,
                               files=files)
    client = _anthropic_client()
    kwargs: dict[str, Any] = dict(
        model=s.anthropic_model, max_tokens=max_tokens or s.llm_max_tokens,
        messages=[{"role": "user", "content": _content(prompt, files)}],
        output_config={"effort": effort or s.llm_effort}, output_format=schema,
    )
    if system:
        kwargs["system"] = system
    try:
        resp = client.messages.parse(**kwargs)
    except Exception as exc:
        raise _fail(agent, task, exc) from exc
    trace.log(agent, "llm_call", task, data={"task": task, "provider": "anthropic", "model": resp.model,
                                             "files": [Path(f).name for f in files],
                                             "input_tokens": resp.usage.input_tokens,
                                             "output_tokens": resp.usage.output_tokens,
                                             "stop_reason": resp.stop_reason})
    if resp.stop_reason == "refusal":
        return None
    return resp.parsed_output


# ---------------------------------------------------------------------------
# OpenAI (Chat Completions; structured output via chat.completions.parse)
# ---------------------------------------------------------------------------
def _openai_messages(prompt: str, system: str, files: Sequence[Path]) -> list[dict]:
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": _openai_content(prompt, files)})
    return messages


def _openai_kwargs(max_tokens: Optional[int]) -> dict:
    s = get_settings()
    kwargs: dict[str, Any] = {"model": s.openai_model, "max_completion_tokens": max_tokens or s.llm_max_tokens}
    if s.openai_reasoning_effort:
        kwargs["reasoning_effort"] = s.openai_reasoning_effort
    return kwargs


def _openai_log(agent: str, task: str, resp, files: Sequence[Path] = ()) -> None:
    usage = getattr(resp, "usage", None)
    trace.log(agent, "llm_call", task, data={
        "task": task, "provider": "openai", "model": getattr(resp, "model", None),
        "files": [Path(f).name for f in files],
        "input_tokens": getattr(usage, "prompt_tokens", None),
        "output_tokens": getattr(usage, "completion_tokens", None),
        "finish_reason": resp.choices[0].finish_reason if getattr(resp, "choices", None) else None,
    })


def _openai_complete(prompt: str, *, agent: str, task: str, system: str, max_tokens: Optional[int]) -> Optional[str]:
    try:
        resp = _openai_client().chat.completions.create(messages=_openai_messages(prompt, system, ()),
                                                        **_openai_kwargs(max_tokens))
    except Exception as exc:
        raise _fail(agent, task, exc) from exc
    _openai_log(agent, task, resp)
    msg = resp.choices[0].message
    return None if getattr(msg, "refusal", None) else msg.content


def _openai_extract(prompt: str, schema: type[M], *, agent: str, task: str, system: str,
                    max_tokens: Optional[int], files: Sequence[Path]) -> Optional[M]:
    try:
        resp = _openai_client().chat.completions.parse(messages=_openai_messages(prompt, system, files),
                                                       response_format=schema, **_openai_kwargs(max_tokens))
    except Exception as exc:
        raise _fail(agent, task, exc) from exc
    _openai_log(agent, task, resp, files)
    msg = resp.choices[0].message
    if getattr(msg, "refusal", None):
        return None
    return msg.parsed
