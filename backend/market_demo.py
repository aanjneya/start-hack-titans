"""A second customer's controlled market move, served by the normal buyer API."""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path


def serve(scratch: Path, port: int):
    setup = scratch / "customer.json"
    setup.write_text(json.dumps({
        "customer": "Pacific Assembly (simulation)", "demo_buyer": "Rei Ito", "site": "Vancouver",
        "timezone": "America/Vancouver", "currency": "CAD",
        "approval_matrix": [{"below_eur": None, "approvers": ["Buyer"]}],
    }))
    os.environ.update(MOCK_MODE="true", CHASER_SEND="false", CHASER_POLL_MIN="0",
                      WATCH_INTERVAL_MIN="0", RISK_REFRESH_INTERVAL_MIN="0", REPLAY_DB_PATH="",
                      DB_PATH=str(scratch / "market.db"), DATA_ROOT=str(scratch), CUSTOMER_SETUP=str(setup))
    import uvicorn
    from . import system
    from .agents.risk import RiskRater
    from .agents.risk_signals import PolymarketProvider, _market_signal
    from .app.main import app
    from .core import models as m, store

    class ExampleMarket(PolymarketProvider):
        yes = .20

        def fetch(self, *, countries, reference_now, **context):
            instrument = dict(id="demo-ca-tariff", question="Will Canada raise export tariffs?",
                              outcomes=["No", "Yes"], outcomePrices=[1 - self.yes, self.yes])
            reading = _market_signal(instrument, {"slug":"demo-canadian-tariffs"}, countries, reference_now)
            reading.live = False
            reading.source_url = "demo/market/ca-tariff"
            reading.quote = f"Controlled market simulation. Yes={self.yes:.0%}."
            return [reading]

    system.bus()
    source = ExampleMarket()
    risk = next(agent for agent in system._agents if isinstance(agent, RiskRater))
    risk.providers = (source,)
    # All company records and both prices belong to this isolated demonstration.
    store.add_all([
        m.Supplier(supplier_id="CA-N", name="Northshore Components", country="CA", status="at risk",
                   qualification_valid_until=date(2020, 1, 1), source="supplier-register"),
        m.PurchaseOrder(po_number="KIT-9", po_line=2, material_id="G2", supplier_id="CA-N", buyer="Rei Ito",
                        status="awaiting_approval", quantity=200, unit_price=10, currency="CAD",
                        promised_date=date(2027, 4, 1), source="open-orders"),
        m.Document(kind="org_chart", path="org-chart", text="Effective 2025-01-01\nRei Ito\nProcurement buyer"),
        *[m.Shipment(shipment_id=f"DEMO-{n}", supplier_id="CA-N", mode="sea", status="delayed",
                     updated_at=datetime(2026, 11, 21, tzinfo=timezone.utc), source="shipping") for n in (1, 2)],
        *[m.Issue(type="quality_check", raised_by="reconciler", supplier_id="CA-N", severity=4,
                  kind="human_check", fingerprint=f"qc-{n}") for n in (1, 2)],
    ])
    if system.refresh_risk()["status"] != "completed":
        raise RuntimeError("Unable to prepare the market simulation.")
    source.yes = .85
    uvicorn.run(app, host="127.0.0.1", port=port)
