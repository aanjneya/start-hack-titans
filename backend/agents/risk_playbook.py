"""Build product risk scenarios, supplier checks, backup-source examples and suggested safeguards."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlmodel import select

from ..core import policy
from ..core.models import AgentLog, OutboxMessage
from ..core.store import session
from .planner_queue import buyer_queue, digest
from .reconciler_rules import CheckContext, build_context
from .risk_baseline import current_risk_scores, recorded_risks, saved_signals
from .risk_breakers import evaluate_circuit_breakers
from .risk_recourse import build_upgrade_plans
from .risk_scoring import rate_products
from .risk_scout import build_second_source_scouts
from .risk_signals import ExternalSignal

OPEN_OUTBOX_STATUSES = {"draft", "approved", "sent"}

_PROVIDER_LABELS = {
    "polymarket": "Polymarket",
    "weather": "Open-Meteo weather",
    "regulations": "Trade regulation watch",
    "gnews": "Google News RSS",
    "portal_news": "Portal News Alert",
    "telemetry": "GPS/AIS Telemetry",
}

_LOCALIZED_GREETING = {
    "PL": ("Dzień dobry", "W związku z bieżącym alertem", "prosimy o pilne potwierdzenie statusu wysyłki", "i aktualnego terminu dostawy."),
    "KO": ("안녕하세요", "실시간 외부 리스크 알림에 따라", "선적 상태를 긴급히 확인해 주시기 바랍니다", "변경된 납품 예정일이 있으면 알려 주십시오."),
    "ZH": ("您好", "根据以下风险信息", "请确认清关或发运是否受到影响", "如预计交付时间有变，请告知新的时间。"),
    "DE": ("Guten Tag", "Aufgrund einer aktuellen externen Risikomeldung", "bitten wir um kurze Bestätigung des aktuellen Versandstatus", "und um den aktuellen Liefertermin."),
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


def build_preemptive_alerts(ctx: CheckContext, signals: list[ExternalSignal], outbox_rows, handled: dict[str, int]) -> list[dict[str, Any]]:
    """Country signals warrant a supplier check. Only a matching route or shipment identifies a delivery."""
    open_by_sup = {}
    for message in outbox_rows:
        if message.status in OPEN_OUTBOX_STATUSES and message.decision_id is None:
            open_by_sup[message.supplier_id] = max(open_by_sup.get(message.supplier_id, 0), message.id)
    active_shipments = defaultdict(list)
    closed = {"delivered", "received", "cancelled", "canceled", "closed"}
    completed_shipments = {sh.shipment_id for sh in ctx.shipments if sh.status.casefold() in closed}
    for sh in ctx.shipments:
        if sh.supplier_id and sh.shipment_id not in completed_shipments:
            active_shipments[sh.supplier_id].append(sh)
    active_orders = defaultdict(list)
    for po in ctx.pos:
        if po.supplier_id and not po.actual_delivery_date and po.status.casefold() not in closed:
            active_orders[po.supplier_id].append(po)

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
            shipments = active_shipments[sup_id]
            orders = active_orders[sup_id]
            if not shipments and not orders:
                continue
            matched = set()
            if sig.shipment_ids:
                matched = {sh.shipment_id for sh in shipments if sh.shipment_id in sig.shipment_ids}
                if not matched:
                    continue
                scope = "Shipment " + ", ".join(sorted(matched))
            elif sig.ports:
                ports = {p.upper() for p in sig.ports}
                matched_ports = set()
                for sh in shipments:
                    route = {s.upper() for s in (sh.origin, sh.destination, sh.via) if s}
                    if shared := ports.intersection(route):
                        matched.add(sh.shipment_id)
                        matched_ports.update(shared)
                if not matched:
                    continue
                scope = "Route via " + ", ".join(sorted(matched_ports))
            elif sup_cc in sig_countries:
                scope = "Country watch · " + sup_cc
            else:
                continue

            seen_suppliers.add(sup_id)
            ship_id = next(iter(matched)) if len(matched) == 1 else ""
            shipment_pos = set(ctx.shipment_pos.get(ship_id, []))
            shipment_parts = set(ctx.shipment_mats.get(ship_id, []))
            lines = [po for po in orders if po.po_number in shipment_pos
                     and (not shipment_parts or po.material_id in shipment_parts)]
            numbers = {po.po_number for po in lines}
            po_num = next(iter(numbers)) if len(numbers) == 1 else ""
            part_id = lines[0].material_id if len(lines) == 1 else ""
            lang = _infer_language(sup_cc, getattr(sup, "language", ""))
            prov_label = _PROVIDER_LABELS.get(sig.provider, sig.provider)
            ref_str = " / ".join(x for x in (f"Shipment {ship_id}" if ship_id else "", f"PO {po_num}" if po_num else "", part_id) if x) or "your open deliveries"

            lead_h = 72 if sig.provider in ("regulations", "polymarket") else 48 if sig.provider == "weather" else 24

            origin = ("Offline example" if not sig.live else "Customer record" if sig.provider in {"telemetry", "portal_news"}
                      else "Public feed at collection")
            alert_id = "pre-" + digest([sup_id, sig.provider, origin, sig.source_url,
                                        (sig.market or {}).get("question_id") or sig.title,
                                        scope, sorted(matched), po_num, part_id])[:24]
            question = (
                f"{origin} ({prov_label}): \"{sig.title}\". "
                f"Regarding {ref_str}, could you confirm whether this affects dispatch or customs clearance, "
                "and share any revised delivery time?"
            )
            draft_en = f"Hello {sup.name} team,\n\n{question}\n\nThank you,\nProcurement Operations"
            if lang in _LOCALIZED_GREETING:
                greet, intro, ask_part, closing = _LOCALIZED_GREETING[lang]
                draft_local = (
                    f"{greet} ({sup.name}),\n\n"
                    f"{intro} ({origin} · {prov_label}: \"{sig.title}\"), "
                    f"{ask_part} ({ref_str}) {closing}\n\n"
                    f"---\nEnglish summary: {draft_en.splitlines()[2]}"
                )
            else:
                draft_local = draft_en

            if alert_id in handled:
                status = "dispatched"
            elif sup_id in open_by_sup:
                status = "blocked_anti_spam"
            else:
                status = "ready"

            alerts.append({
                "alert_id": alert_id,
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
                "source_scope": scope,
                "source_origin": origin,
                "lead_time_advantage_hours": lead_h,
                "supplier_question": question,
                "draft_subject": f"Pre-Emptive Corridor Check: {ref_str} ({prov_label})",
                "draft_body": draft_local,
                "draft_body_en": draft_en,
                "status": status,
                "outbox_id": handled.get(alert_id) or open_by_sup.get(sup_id),
                "evidence": [sig.as_evidence().model_dump()],
            })

    return alerts[:6]


def current_supplier_checks(db, ctx, signals):
    outbox = list(db.exec(select(OutboxMessage)))
    handled = {log.data["alert_id"]: log.data["outbox_id"] for log in db.exec(select(AgentLog).where(
        AgentLog.agent == "human", AgentLog.action == "preemptive_chase"))
        if log.data.get("alert_id") and log.data.get("outbox_id") and log.data.get("delivery") != "failed"}
    return build_preemptive_alerts(ctx, signals, outbox, handled)


def build_risk_playbook() -> dict[str, Any]:
    """Preview actions against the published ratings, without refreshing or changing feed history."""
    ctx = build_context()
    with session() as db:
        risks = recorded_risks(ctx, current_risk_scores(db))
        signals = saved_signals(db)
        queue = buyer_queue(db)
        preemptive_alerts = current_supplier_checks(db, ctx, signals)

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
