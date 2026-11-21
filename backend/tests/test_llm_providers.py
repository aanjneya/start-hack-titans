"""Claude or OpenAI behind the same llm.complete/extract (provider switch), tested without network."""
from types import SimpleNamespace

from pydantic import BaseModel

from backend.core import config, llm


class Out(BaseModel):
    qty: int


def _settings(monkeypatch, **env):
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "LLM_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MOCK_MODE", "false")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    config.reset_settings()
    llm.reset_clients()


def test_provider_auto_and_explicit(monkeypatch):
    _settings(monkeypatch, OPENAI_API_KEY="sk-test")
    if not config.get_settings().anthropic_api_key:  # a real key in .env would win in "auto"
        assert llm.provider() == "openai"
    _settings(monkeypatch, OPENAI_API_KEY="sk-test", LLM_PROVIDER="openai", ANTHROPIC_API_KEY="x")
    assert llm.provider() == "openai"
    _settings(monkeypatch, ANTHROPIC_API_KEY="x", LLM_PROVIDER="anthropic")
    assert llm.provider() == "anthropic"


def test_openai_extract_with_images_and_pdf(monkeypatch, tmp_path):
    _settings(monkeypatch, OPENAI_API_KEY="sk-test", LLM_PROVIDER="openai", OPENAI_MODEL="gpt-test")
    img, pdf = tmp_path / "scan.png", tmp_path / "note.pdf"
    img.write_bytes(b"\x89PNGfake")
    pdf.write_bytes(b"%PDF-1.4 fake")
    seen = {}

    def parse(**kwargs):
        seen.update(kwargs)
        msg = SimpleNamespace(parsed=Out(qty=7), refusal=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")], model="gpt-test",
                               usage=SimpleNamespace(prompt_tokens=10, completion_tokens=3))

    llm._clients["openai"] = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))
    out = llm.extract("read", Out, agent="t", task="probe", files=[img, pdf], system="be exact")
    assert out.qty == 7 and seen["model"] == "gpt-test" and seen["response_format"] is Out
    parts = seen["messages"][1]["content"]
    assert [p["type"] for p in parts] == ["image_url", "file", "text"]
    assert parts[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert parts[1]["file"]["file_data"].startswith("data:application/pdf;base64,")
    assert seen["messages"][0] == {"role": "system", "content": "be exact"}


def test_openai_errors_become_llmerror(monkeypatch):
    _settings(monkeypatch, OPENAI_API_KEY="sk-test", LLM_PROVIDER="openai")

    def boom(**kwargs):
        raise RuntimeError("quota exceeded")

    llm._clients["openai"] = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=boom, create=boom)))
    for call in (lambda: llm.extract("x", Out, agent="t", task="p"), lambda: llm.complete("x", agent="t", task="p")):
        try:
            call()
            raise AssertionError("expected LLMError")
        except llm.LLMError:
            pass
