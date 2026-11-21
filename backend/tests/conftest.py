import os

import pytest


@pytest.fixture(autouse=True)
def fresh_backend(tmp_path, monkeypatch):
    """Every test gets its own empty SQLite DB, MOCK mode on, and fresh agents."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "truth.db"))
    monkeypatch.setenv("REPLAY_DIR", str(tmp_path / "replay"))
    monkeypatch.setenv("MOCK_MODE", "true")
    from backend.core import config, store
    from backend import system

    config.reset_settings()
    store.dispose_engine()
    system._bus = None
    system.reset()
    yield
    from backend.core import llm

    llm.reset_clients()
    store.dispose_engine()
    config.reset_settings()
    os.environ.pop("DB_PATH", None)
