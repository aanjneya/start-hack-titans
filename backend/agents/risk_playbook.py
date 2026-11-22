"""Unified Risk-to-Action Playbook Orchestrator (Agent 6).

Combines all 4 actionable engines into one deterministic, zero-hardcoding payload:
  1. Path-to-Upgrade Optimizer (`risk_recourse.py`)   — CCC -> A algorithmic recourse
  2. Predictive Pre-Emptive Chasing (below)           — live external feeds -> 72h early supplier check
  3. Geo-Decorrelated 2nd-Source Scout (`risk_scout.py`) — safe corridors + spec-grounded RFQ pack
  4. Risk-Triggered Circuit Breakers (`risk_breakers.py`) — auto-policy shift on Grade 5/6 downgrades
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlmodel import select

from ..core import policy
from ..core.models import OutboxMessage
from ..core.store import session
from .planner_queue import buyer_queue
from .reconciler_rules import CheckContext, build_context
from .risk_baseline import current_risk_scores, recorded_risks, saved_signals
from .risk_breakers import evaluate_circuit_breakers
from .risk_recourse import build_upgrade_plans
from .risk_scoring import ComputedRisk, rate_products
from .risk_scout import build_second_source_scouts
from .risk_signals import ExternalSignal

OPEN_OUTBOX_STATUSES = {"draft", "approved", "sent"}

_PROVIDER_LABELS = {
    "polymarket": "Polymarket Live Odds",
    "weather": "Open-Meteo Port Weather",
    "regulation": "Global Trade Regulations (BIS/EUR-Lex/WTO)",
    "gnews": "Google News RSS",
    "portal_news": "Portal News Alert",
    "telemetry": "GPS/AIS Telemetry",
}

_LOCALIZED_GREETING = {
    "PL": ("Dzień dobry", "W związku z bieżącym alertem", "prosimy o pilne potwierdzenie statusu wysyłki", "oraz czy potrzebna jest rezerwacja transportu lotniczego/ekspresowego zanim wzrosną stawki."),
    "KO": ("안녕하세요", "실시간 외부 리스크 알림에 따라", "선적 상태를 긴급히 확인해 주시기 바랍니다", "지연 발생 전 대체 항공/특송 예약이 필요한지 회신 부탁드립니다."),
    "ZH": ("您好", "鉴于最新外部风险预警", "请紧急确认该批次货物的清关及发运状态", "如需在运价上涨前提前锁定备选空运仓位，请尽快回复。"),
    "DE": ("Guten Tag", "Aufgrund einer aktuellen externen Risikomeldung", "bitten wir um kurze Bestätigung des aktuellen Versandstatus", "sowie ob wir vorsorglich Ersatzkapazität zu regulären Konditionen reservieren sollen."),
}


def _infer_language(country: str, lang_field: str) -> str:
    raw = (lang_field or "").strip().upper()
    if raw in ("PL", "POLISH"):
        return "PL"
    if raw in ("KO", "KR", "KOREAN"):
        return "KO"
    if raw in ("ZH", "CN", "CHINESE"):
        return "ZH"
    if raw in ("DE", "GERMAN"):
        return "DE"
    return {"PL": "PL", "KR": "KO", "CN": "ZH", "DE": "DE"}.get((country or "").upper(), "EN")


def build_preemptive_alerts(
    ctx: CheckContext,
    supplier_risks: dict[str, ComputedRisk],
    signals: list[ExternalSignal],
    outbox_rows: list[OutboxMessage],
) -> list[dict[str, Any]]:
    """Match high-severity live external world signals to active suppliers/shipments (72h early warning)."""
    open_by_sup = {m.supplier_id for m in outbox_rows if m.status in OPEN_OUTBOX_STATUSES and m.supplier_id}
    dispatched_alerts = {
        m.supplier_id
        for m in outbox_rows
        if m.supplier_id and "pre-emptive" in (m.subject or "").lower()
    }

    # Index active shipments & POs per supplier
    ship_by_sup: dict[str, Any] = {}
    for sh in ctx.shipments:
        if sh.supplier_id and (sh.status or "").lower() not in ("delivered", "received", "cancelled"):
            ship_by_sup.setdefault(sh.supplier_id, sh)

    po_by_sup: dict[str, Any] = {}
    for po in ctx.pos:
        if po.supplier_id and (po.status or "").lower() not in ("delivered", "cancelled", "closed"):
            po_by_sup.setdefault(po.supplier_id, po)

    high_signals = sorted(
        [s for s in signals if s.score_0_100 >= 55.0],
        key=lambda s: -s.score_0_100,
    )

    alerts: list[dict[str, Any]] = []
    seen_suppliers: set[str] = set()

    for sig in high_signals:
        sig_countries = {c.upper() for c in (sig.countries or ())}
        for sup_id, sup in sorted(ctx.suppliers.items()):
            if sup_id in seen_suppliers:
                continue
            sup_cc = (sup.country or "").upper()
            sh = ship_by_sup.get(sup_id)
            sig_ships = set(getattr(sig, "shipment_ids", ()) or ())
            matched_by_ship = bool(sig_ships and sh and sh.shipment_id in sig_ships)
            matched_by_cc = bool(sup_cc and sup_cc in sig_countries)
            if not (matched_by_ship or matched_by_cc):
                continue

            seen_suppliers.add(sup_id)
            po = po_by_sup.get(sup_id)
            ship_id = sh.shipment_id if sh else ""
            po_num = po.po_number if po else ""
            part_id = po.material_id if po else ""
            lang = _infer_language(sup_cc, getattr(sup, "language", ""))
            prov_label = _PROVIDER_LABELS.get(sig.provider, sig.provider)
            ref_str = " / ".join(x for x in (f"Shipment {ship_id}" if ship_id else "", f"PO {po_num}" if po_num else "", part_id) if x) or "active launch orders"

            lead_h = 72 if sig.provider in ("regulation", "polymarket") else 48 if sig.provider == "weather" else 24

            draft_en = (
                f"Hello {sup.name} team,\n\n"
                f"Our live monitoring ({prov_label}) flagged: \"{sig.title}\". "
                f"Regarding {ref_str}, could you please confirm whether dispatch/customs clearance is already on track, "
                f"or if we should pre-reserve backup express/air capacity today while standard rates are still available?\n\n"
                f"Thank you for a quick confirmation,\nProcurement Operations"
            )
            if lang in _LOCALIZED_GREETING:
                greet, intro, ask_part, closing = _LOCALIZED_GREETING[lang]
                draft_local = (
                    f"{greet} ({sup.name}),\n\n"
                    f"{intro} ({prov_label}: \"{sig.title}\"), "
                    f"{ask_part} ({ref_str}) {closing}\n\n"
                    f"---\nEnglish summary: {draft_en.splitlines()[2]}"
                )
            else:
                draft_local = draft_en

            if sup_id in dispatched_alerts:
                status = "dispatched"
            elif sup_id in open_by_sup:
                status = "blocked_anti_spam"
            else:
                status = "ready"

            ev = [sig.evidence.model_dump()] if getattr(sig, "evidence", None) else []
            alerts.append({
                "alert_id": f"pre-{sup_id}-{sig.provider}",
                "supplier_id": sup_id,
                "supplier_name": sup.name,
                "country": sup_cc,
                "language": lang,
                "shipment_id": ship_id,
                "po_number": po_num,
                "part_id": part_id,
                "provider": prov_label,
                "category": sig.category,
                "signal_title": sig.title,
                "signal_score": round(sig.score_0_100, 1),
                "lead_time_advantage_hours": lead_h,
                "draft_subject": f"Pre-Emptive Corridor Check: {ref_str} ({prov_label})",
                "draft_body": draft_local,
                "draft_body_en": draft_en,
                "status": status,
                "evidence": ev,
            })

    return alerts[:6]


def build_risk_playbook() -> dict[str, Any]:
    """Preview actions against the published ratings, without refreshing or changing feed history."""
    ctx = build_context()
    with session() as db:
        risks = recorded_risks(ctx, current_risk_scores(db))
        signals = saved_signals(db)
        queue = buyer_queue(db)
        outbox_rows = list(db.exec(select(OutboxMessage)))

    supplier_risks, part_risks, product_risks = risks["supplier"], risks["part"], risks["product"]
    # A changed BOM/shift or a legacy part without factors cannot reproduce the recorded grade.
    # Wait for the rater to publish a consistent baseline before showing a what-if for that product.
    rerated = rate_products(ctx, part_risks)
    comparable = {
        pid: product for pid, product in product_risks.items()
        if pid in rerated and rerated[pid].breakdown == product.breakdown
        and rerated[pid].score_0_100 == product.score_0_100
        and all(line.child_id in part_risks for line in ctx.bom_by_parent.get(pid, []))
    }
    upgrade_plans = build_upgrade_plans(ctx, part_risks, comparable, queue)
    preemptive_alerts = build_preemptive_alerts(ctx, supplier_risks, signals, outbox_rows)
    second_source_scouts = build_second_source_scouts(ctx, part_risks, signals)
    circuit_breakers = evaluate_circuit_breakers(ctx, supplier_risks, product_risks)

    return {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "autonomy_mode": policy.setup().autonomy_mode,
        "upgrade_plans": upgrade_plans,
        "preemptive_alerts": preemptive_alerts,
        "second_source_scouts": second_source_scouts,
        "circuit_breakers": circuit_breakers,
    }
