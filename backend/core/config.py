"""Settings for the backend, read from the environment and the repo-root .env file.

Nothing here is Soony-specific: every value that depends on the customer comes from env/.env
or from the data itself, never from code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"


def _load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _flag(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    mock_mode: bool
    data_root: Path
    db_path: Path
    replay_dir: Path
    anthropic_model: str
    llm_effort: str
    llm_max_tokens: int
    soony_base_url: str
    soony_api_token: str
    customer_setup: str
    watch_interval_min: float
    portal_mirror: Path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Environment variables win over .env; .env wins over defaults."""
    env = {**_load_dotenv(REPO_ROOT / ".env"), **os.environ}

    def get(key: str, default: str = "") -> str:
        return env.get(key, default) or default

    data_root = Path(get("DATA_ROOT", str(REPO_ROOT / "challenge" / "start-hack-data" / "data")))
    db_path = Path(get("DB_PATH", str(BACKEND_ROOT / "data" / "truth.db")))
    return Settings(
        mock_mode=_flag(env.get("MOCK_MODE"), default=True),
        data_root=data_root if data_root.is_absolute() else REPO_ROOT / data_root,
        db_path=db_path if db_path.is_absolute() else REPO_ROOT / db_path,
        replay_dir=Path(get("REPLAY_DIR", str(BACKEND_ROOT / "data" / "replay"))),
        anthropic_model=get("ANTHROPIC_MODEL", "claude-opus-5-5"),
        llm_effort=get("LLM_EFFORT", "medium"),
        llm_max_tokens=int(get("LLM_MAX_TOKENS", "16000")),
        soony_base_url=get("SOONY_BASE_URL").rstrip("/"),
        soony_api_token=get("SOONY_API_TOKEN"),
        customer_setup=get("CUSTOMER_SETUP", "soony"),
        watch_interval_min=float(get("WATCH_INTERVAL_MIN", "0") or 0),
        portal_mirror=Path(get("PORTAL_MIRROR", str(BACKEND_ROOT / "data" / "portal"))),
    )


def reset_settings() -> None:
    """For tests: forget cached settings so new env vars take effect."""
    get_settings.cache_clear()
