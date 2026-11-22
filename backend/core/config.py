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
    llm_provider: str
    anthropic_api_key: str
    anthropic_base_url: str
    openai_api_key: str
    openai_base_url: str
    openai_model: str
    openai_reasoning_effort: str
    llm_effort: str
    llm_max_tokens: int
    soony_base_url: str
    soony_api_token: str
    customer_setup: str
    watch_interval_min: float
    portal_mirror: Path
    replay_db_path: Path | None = None
    # Chaser mailbox (shared team mailbox; see .brain/tasks/4-chaser.md)
    mail_address: str = ""
    mail_password: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_security: str = "starttls"  # starttls | ssl
    imap_host: str = ""
    imap_port: int = 993
    chaser_send: bool = False  # True only on the ONE instance that really sends + polls the mailbox
    chaser_min_gap_min: float = 60.0  # minimum minutes between two mails to the same supplier
    chaser_escalate_h: float = 4.0  # no reply after N hours -> escalate to a buyer
    chaser_poll_min: float = 0.0  # poll the mailbox every N minutes inside the backend (0 = off)
    chaser_max_steps: int = 8  # max tool-use iterations of the Chaser agent per finding


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Environment variables win over .env; .env wins over defaults."""
    env = {**_load_dotenv(REPO_ROOT / ".env"), **os.environ}

    def get(key: str, default: str = "") -> str:
        return env.get(key, default) or default

    data_root = Path(get("DATA_ROOT", str(REPO_ROOT / "challenge" / "start-hack-data" / "data")))
    db_path = Path(get("DB_PATH", str(BACKEND_ROOT / "data" / "truth.db")))
    replay_path = get("REPLAY_DB_PATH")
    replay_db = (REPO_ROOT / replay_path).resolve() if replay_path else None
    live_db = (REPO_ROOT / db_path).resolve()
    if replay_db and (live_db == replay_db or
                      (live_db.exists() and replay_db.exists() and live_db.samefile(replay_db))):
        raise ValueError("REPLAY_DB_PATH must be separate from DB_PATH")
    return Settings(
        replay_db_path=replay_db,
        mock_mode=_flag(env.get("MOCK_MODE"), default=True),
        data_root=data_root if data_root.is_absolute() else REPO_ROOT / data_root,
        db_path=db_path if db_path.is_absolute() else REPO_ROOT / db_path,
        replay_dir=Path(get("REPLAY_DIR", str(BACKEND_ROOT / "data" / "replay"))),
        anthropic_model=get("ANTHROPIC_MODEL", "claude-opus-5-5"),
        llm_provider=get("LLM_PROVIDER", "auto").strip().lower(),
        anthropic_api_key=get("ANTHROPIC_API_KEY"),
        anthropic_base_url=_load_dotenv(REPO_ROOT / ".env").get("ANTHROPIC_BASE_URL", ""),  # .env only, see llm.py
        openai_api_key=get("OPENAI_API_KEY"),
        openai_base_url=get("OPENAI_BASE_URL"),
        openai_model=get("OPENAI_MODEL", "gpt-5.4-mini"),
        openai_reasoning_effort=get("OPENAI_REASONING_EFFORT"),
        llm_effort=get("LLM_EFFORT", "medium"),
        llm_max_tokens=int(get("LLM_MAX_TOKENS", "16000")),
        soony_base_url=get("SOONY_BASE_URL").rstrip("/"),
        soony_api_token=get("SOONY_API_TOKEN"),
        customer_setup=get("CUSTOMER_SETUP", "soony"),
        watch_interval_min=float(get("WATCH_INTERVAL_MIN", "0") or 0),
        portal_mirror=Path(get("PORTAL_MIRROR", str(BACKEND_ROOT / "data" / "portal"))),
        mail_address=get("MAIL_ADDRESS"),
        mail_password=get("MAIL_PASSWORD"),
        smtp_host=get("SMTP_HOST"),
        smtp_port=int(get("SMTP_PORT", "587") or 587),
        smtp_security=get("SMTP_SECURITY", "starttls").lower(),
        imap_host=get("IMAP_HOST"),
        imap_port=int(get("IMAP_PORT", "993") or 993),
        chaser_send=_flag(env.get("CHASER_SEND"), default=False),
        chaser_min_gap_min=float(get("CHASER_MIN_GAP_MIN", "60") or 60),
        chaser_escalate_h=float(get("CHASER_ESCALATE_H", "4") or 4),
        chaser_poll_min=float(get("CHASER_POLL_MIN", "0") or 0),
        chaser_max_steps=int(get("CHASER_MAX_STEPS", "8") or 8),
    )


def reset_settings() -> None:
    """For tests: forget cached settings so new env vars take effect."""
    get_settings.cache_clear()
