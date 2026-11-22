from backend.agents.reconciler_rules import build_context
from backend.agents.risk_playbook import build_preemptive_alerts
from backend.agents.risk_signals import ExternalSignal
from backend.core.models import PurchaseOrder, Shipment, Supplier


def test_early_checks_use_signal_provenance_and_the_matching_shipment_only():
    ctx = build_context()
    ctx.suppliers = {key: Supplier(supplier_id=key, name=key, country="CA") for key in ("One", "Two", "Idle")}
    ctx.shipments = [
        Shipment(shipment_id="other", supplier_id="One"),
        Shipment(shipment_id="target", supplier_id="One", origin="CAVAN"),
        Shipment(shipment_id="unrelated", supplier_id="Two"),
    ]
    ctx.pos = [PurchaseOrder(po_number=no, po_line=1, material_id=part, supplier_id="One")
               for no, part in [("outside", "A"), ("linked", "B"), ("split", "C")]]
    ctx.shipment_pos = {"target": ["linked", "split"]}
    ctx.shipment_mats = {"target": ["B"]}
    observed = ExternalSignal(provider="telemetry", category="transport", title="Tracking late",
                              summary="Tracker shows a delay", score_0_100=87, live=True,
                              countries=["CA"], shipment_ids=["target"], source_url="tracking.csv")
    rows = build_preemptive_alerts(ctx, [observed], [], {})
    assert [(a["supplier_id"], a["shipment_id"], a["po_number"], a["part_id"]) for a in rows] == [
        ("One", "target", "linked", "B")]
    assert rows[0]["source_origin"] == "Customer record"
    assert rows[0]["evidence"] == [observed.as_evidence().model_dump()]

    # If the shipment could carry two PO lines, do not invent an association.
    ctx.shipment_mats = {}
    ambiguous, = build_preemptive_alerts(ctx, [observed], [], {})
    assert ambiguous["shipment_id"] == "target"
    assert ambiguous["po_number"] == ambiguous["part_id"] == ""
    assert ambiguous["alert_id"] != rows[0]["alert_id"]

    market = ExternalSignal(provider="polymarket", category="external_politics", title="Canadian export question",
                            summary="Example price", score_0_100=70, countries=["CA"], live=False)
    alerts = build_preemptive_alerts(ctx, [market], [], {})
    assert {a["supplier_id"] for a in alerts} == {"One", "Two"}
    assert all((a["source_scope"], a["source_origin"], a["shipment_id"], a["po_number"], a["part_id"])
               == ("Country watch · CA", "Offline example", "", "", "") for a in alerts)
    assert all(a["provider"] == "Polymarket" and "Offline example (Polymarket)" in a["draft_body_en"] for a in alerts)

    new_market = ExternalSignal(provider="polymarket", category="external_politics", title="Another Canadian question",
                                summary="Another example", score_0_100=72, countries=["CA"])
    previous = {a["alert_id"]: 9 for a in alerts}
    assert all(a["status"] == "ready" for a in build_preemptive_alerts(ctx, [new_market], [], previous))
