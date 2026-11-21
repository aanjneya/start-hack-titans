"""Customer setup: approval matrix + autonomy switch (office hours Sat 18:00).

The numbers live in backend/setup/<customer>.json (CUSTOMER_SETUP, default soony), never in code,
so onboarding another company = a new setup file.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .config import BACKEND_ROOT, get_settings


@dataclass(frozen=True)
class ApprovalRule:
    below_eur: Optional[float]  # None = no upper limit
    approvers: tuple[str, ...]


@dataclass(frozen=True)
class CustomerSetup:
    customer: str
    autonomy_mode: str  # "control" | "auto"
    auto_execute_below_eur: float
    approval_matrix: tuple[ApprovalRule, ...]
    demo_buyer: str = ""


def _setup_path() -> Path:
    name = get_settings().customer_setup
    path = Path(name)
    return path if path.suffix == ".json" else BACKEND_ROOT / "setup" / f"{name}.json"


@lru_cache(maxsize=4)
def _load(path: str) -> CustomerSetup:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    rules = tuple(
        ApprovalRule(r.get("below_eur"), tuple(r.get("approvers", [])))
        for r in raw.get("approval_matrix", [])
    )
    rules = tuple(sorted(rules, key=lambda r: float("inf") if r.below_eur is None else r.below_eur))
    autonomy = raw.get("autonomy", {})
    return CustomerSetup(
        demo_buyer=raw.get("demo_buyer", ""),
        customer=raw.get("customer", ""),
        autonomy_mode=autonomy.get("mode", "control"),
        auto_execute_below_eur=float(autonomy.get("auto_execute_below_eur") or 0),
        approval_matrix=rules,
    )


def setup() -> CustomerSetup:
    return _load(str(_setup_path()))


def approvers_for(amount_eur: float) -> list[str]:
    """Who must approve an action costing `amount_eur` (first rule whose limit is above the amount)."""
    for rule in setup().approval_matrix:
        if rule.below_eur is None or amount_eur < rule.below_eur:
            return list(rule.approvers)
    return []


def approver_for(amount_eur: float) -> str:
    """Approvers as one display string, e.g. 'CPO + CFO'."""
    return " + ".join(approvers_for(amount_eur))


def needs_approval(amount_eur: float) -> bool:
    """False only in 'auto' mode for actions cheaper than auto_execute_below_eur."""
    s = setup()
    return not (s.autonomy_mode == "auto" and amount_eur < s.auto_execute_below_eur)


def reset_cache() -> None:
    _load.cache_clear()
