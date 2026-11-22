"""Tests for Agent 6 (Risk Rater): external signal providers, 3-tier rollup, 3-drop replay & Findings."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlmodel import select

from backend import system
from backend.agents import risk_signals
from backend.agents.risk import RiskRater
from backend.agents.risk_signals import (
    ExternalSignal,
    OpenMeteoWeatherProvider,
    PoliticalNewsProvider,
    PolymarketProvider,
    RegulationChangeProvider,
    clear_cache,
)
from backend.core import config, events
from backend.core.coordinator import Coordinator
from backend.core.models import (
    AgentLog,
    BomLine,
    Issue,
    IssueKind,
    LaunchStatus,
    Material,
    Product,
    RiskScore,
    Severity,
    Shipment,
    StockLot,
    Supplier,
    SupplierPartMap,
)
from backend.core.replay import list_drops
from backend.core.store import add_all, all_rows, latest_by, session, update_row


def _risk_scores() -> dict[tuple[str, str], RiskScore]:
    return {
        (r.entity_type, r.entity_id): r
        for r in latest_by(all_rows(RiskScore), "entity_type", "entity_id")
    }


def _risk_errors() -> list[str]:
    with session() as s:
        return [
            e.detail
            for e in s.exec(
                select(AgentLog).where(AgentLog.agent == "risk", AgentLog.action == "error")
            ).all()
        ]


def _risk_issues(status: str | None = None) -> list[Issue]:
    with session() as s:
        stmt = select(Issue).where(Issue.raised_by == "risk")
        if status is not None:
            stmt = stmt.where(Issue.status == status)
        return list(s.exec(stmt).all())


# ---------------------------------------------------------------------------
# 1. Real 3-drop replay: grades jump accurately on 16:08 and 17:23 drops
# ---------------------------------------------------------------------------
def test_replay_real_drops_updates_grades_and_logs_changes():
    drops = list_drops()
    assert len(drops) >= 3

    # Drop 0 (14:15 baseline)
    system.replay_step()
    assert _risk_errors() == []
    s0 = _risk_scores()
    assert ("supplier", "SUP-0029") in s0
    assert ("part", "CMP-MAG-005") in s0
    assert ("product", "FG-GH6-CE") in s0 and ("product", "FG-GH6-STD") in s0
    packright_before = s0[("supplier", "SUP-0029")].grade
    mag_before = s0[("part", "CMP-MAG-005")].grade
    pune_before = s0[("supplier", "SUP-0018")].grade
    assert packright_before <= 3
    assert mag_before <= 4

    # Drop 1 (16:08 ECN-2026-118 + launch_status + CMP-GAM-010 critical)
    system.replay_step()
    assert _risk_errors() == []
    s1 = _risk_scores()
    assert s1[("part", "CMP-GAM-004")].grade > s0[("part", "CMP-GAM-004")].grade
    assert s1[("part", "CMP-GAM-010")].grade >= 4

    # Drop 2 (17:23 China magnet export ban + Pune Diecast watch/expired release)
    system.replay_step()
    assert _risk_errors() == []
    s2 = _risk_scores()

    # PackRight & CMP-MAG-005 jump to 6 (CCC); CMP-GAM-007 & Pune Diecast jump to >= 5 (B)
    assert s2[("supplier", "SUP-0029")].grade == 6 and s2[("supplier", "SUP-0029")].rating == "CCC"
    assert s2[("part", "CMP-MAG-005")].grade == 6 and s2[("part", "CMP-MAG-005")].rating == "CCC"
    assert s2[("part", "CMP-GAM-007")].grade >= 5
    assert s2[("supplier", "SUP-0018")].grade >= 5 and s2[("supplier", "SUP-0018")].grade > pune_before
    # Product tier: Collector's Edition rolls up worst bottleneck to 6 (CCC)
    assert s2[("product", "FG-GH6-CE")].grade == 6 and s2[("product", "FG-GH6-CE")].rating == "CCC"
    assert s2[("product", "FG-GH6-STD")].grade <= s2[("product", "FG-GH6-CE")].grade

    # T6.3: Top <= 3 explainable reasons + breakdown + evidence on every score
    for rs in s2.values():
        assert 1 <= rs.grade <= 6
        assert 1 <= len(rs.reasons) <= 3
        assert "score_0_100" in rs.reasons[0] and "breakdown" in rs.reasons[0]
        assert all(r.get("text") for r in rs.reasons)

    # T6.4: Grade changes recorded in agent_log
    with session() as s:
        changes = s.exec(
            select(AgentLog).where(AgentLog.agent == "risk", AgentLog.action == "grade_changed")
        ).all()
    assert any("SUP-0029" in c.detail for c in changes)
    assert any("CMP-MAG-005" in c.detail for c in changes)

    # T6.5, T6.6, T6.7: Grade >= 5 Findings + second_source_candidates Finding with actions & evidence
    open_risk = _risk_issues(status="open")
    types = {i.type for i in open_risk}
    assert {"high_risk_rating", "second_source_candidates"} <= types
    mag_finding = next(i for i in open_risk if i.type == "high_risk_rating" and i.part_id == "CMP-MAG-005")
    assert mag_finding.kind == IssueKind.HUMAN_CHECK and mag_finding.severity == Severity.HIGH
    assert mag_finding.suggested_actions and mag_finding.evidence
    ss_finding = next(i for i in open_risk if i.type == "second_source_candidates")
    assert "CMP-MAG-005" in ss_finding.summary and len(ss_finding.suggested_actions) >= 1


# ---------------------------------------------------------------------------
# 2. Live HTTP parsing for Polymarket Gamma API & Open-Meteo (mocked HTTP)
# ---------------------------------------------------------------------------
def test_live_polymarket_open_meteo_and_gnews_parse_real_shapes(monkeypatch):
    clear_cache()
    monkeypatch.setenv("MOCK_MODE", "false")
    config.reset_settings()

    def fake_get_json(url: str, params: dict[str, Any] | None = None):
        if "polymarket" in url:
            if (params or {}).get("tag_slug") == "geopolitics":
                return [
                    {
                        "slug": "strait-of-hormuz-traffic-returns-to-normal",
                        "markets": [{
                            "question": "Strait of Hormuz traffic returns to normal by December 31?",
                            "outcomes": '["Yes","No"]',
                            "outcomePrices": '["0.03", "0.97"]',
                            "closed": False,
                        }],
                    },
                    {
                        "slug": "will-china-invade-taiwan-before-2027",
                        "markets": [{
                            "question": "Will China invade Taiwan by end of 2026?",
                            "outcomes": ["No", "Yes"],
                            "outcomePrices": '["0.98", "0.02"]',
                            "closed": False,
                        }],
                    },
                    {
                        "slug": "us-china-rare-earth-tariff-escalation",
                        "markets": [{
                            "question": "Will China restrict rare earth exports to EU/US in 2026?",
                            "outcomes": ["Yes", "No"],
                            "outcomePrices": '["0.81", "0.19"]',
                            "closed": False,
                        }],
                    },
                ]
            return []
        if "open-meteo" in url:
            return {
                "current": {
                    "wind_speed_10m": 62.0,
                    "wind_gusts_10m": 88.0,
                    "precipitation": 18.0,
                    "weather_code": 95,
                }
            }
        return None

    def fake_get_text(url: str, params: dict[str, Any] | None = None):
        if "news.google.com" in url:
            return (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<rss version="2.0"><channel><item>'
                '<title>China tightens export controls on rare earth magnets - Reuters</title>'
                '<link>https://news.google.com/rss/articles/test123</link>'
                '<pubDate>Sat, 21 Nov 2026 18:00:00 GMT</pubDate>'
                '</item></channel></rss>'
            )
        return None

    monkeypatch.setattr(risk_signals, "_cached_get_json", fake_get_json)
    monkeypatch.setattr(risk_signals, "_cached_get_text", fake_get_text)

    now = datetime(2026, 11, 21, 13, 0, tzinfo=timezone.utc)
    ports = {"DEHAM": {"locode": "DEHAM", "name": "Hamburg", "country": "DE", "lat": 53.54, "lon": 9.98, "mode": "sea"}}
    ships = [Shipment(shipment_id="SHP-1", origin="CNYTN", destination="DEHAM", mode="sea", source="erp.csv")]

    poly_sigs = PolymarketProvider().fetch(countries={"CN"}, ports=ports, shipments=ships, documents=[], reference_now=now)
    by_title = {s.title: s for s in poly_sigs}
    # Relief question inverted: 3% Yes -> 97% disruption score
    assert by_title["Strait of Hormuz traffic returns to normal by December 31?"].score_0_100 == 97.0
    assert by_title["Strait of Hormuz traffic returns to normal by December 31?"].market["disruption_outcome"] == "No"
    # Disruption question NOT inverted: 81% Yes -> 81% risk score; 2% invasion filtered out (<10%)
    assert by_title["Will China restrict rare earth exports to EU/US in 2026?"].score_0_100 == 81.0
    assert "Will China invade Taiwan by end of 2026?" not in by_title
    assert all(s.live for s in poly_sigs)

    wx_sigs = OpenMeteoWeatherProvider().fetch(countries={"DE"}, ports=ports, shipments=ships, documents=[], reference_now=now)
    assert len(wx_sigs) == 1 and wx_sigs[0].live is True and wx_sigs[0].score_0_100 >= 80.0

    news_sigs = PoliticalNewsProvider().fetch(countries={"CN"}, ports=ports, shipments=ships, documents=[], reference_now=now)
    assert len(news_sigs) == 1 and news_sigs[0].provider == "gnews" and news_sigs[0].live is True
    assert news_sigs[0].score_0_100 == 65.0

    # Official regulatory feed (Federal Register + EUR-Lex)
    def fake_reg_json(url: str, params: dict[str, Any] | None = None):
        if "federalregister.gov" in url:
            return {
                "results": [
                    {
                        "title": "Addressing Remote Access to Advanced Semiconductors",
                        "type": "Rule",
                        "publication_date": "2026-11-19",
                        "html_url": "https://www.federalregister.gov/documents/2026/11/19/test",
                    },
                    {
                        "title": "Agency Information Collection Activities",
                        "type": "Notice",
                        "publication_date": "2026-11-20",
                        "html_url": "https://www.federalregister.gov/documents/2026/11/20/skip",
                    },
                ]
            }
        return fake_get_json(url, params)

    def fake_reg_text(url: str, params: dict[str, Any] | None = None):
        if "eur-lex.europa.eu" in url:
            return (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<rss version="2.0"><channel><item>'
                '<title>Council Regulation (EU) 2026/2568 on maritime sanctions and port access</title>'
                '<link>https://eur-lex.europa.eu/legal-content/AUTO/?uri=CELEX:32026R2568</link>'
                '</item></channel></rss>'
            )
        return fake_get_text(url, params)

    monkeypatch.setattr(risk_signals, "_cached_get_json", fake_reg_json)
    monkeypatch.setattr(risk_signals, "_cached_get_text", fake_reg_text)
    all_countries = {"CN", "KR", "IN", "PL", "DE", "US", "JP"}
    reg_sigs = RegulationChangeProvider().fetch(countries=all_countries, ports=ports, shipments=ships, documents=[], reference_now=now)
    assert any("federalregister.gov" in s.source_url for s in reg_sigs)
    assert any("eur-lex.europa.eu" in s.source_url for s in reg_sigs)
    assert all(s.live and s.category == "regulatory_change" for s in reg_sigs)
    assert next(s for s in reg_sigs if "federalregister.gov" in s.source_url).published == "2026-11-19"

    # Offline MOCK_MODE covers EVERY supplier country + any unseen future country (e.g. JP)
    monkeypatch.setenv("MOCK_MODE", "true")
    config.reset_settings()
    clear_cache()
    mock_reg = RegulationChangeProvider().fetch(countries=all_countries, ports=ports, shipments=ships, documents=[], reference_now=now)
    covered = {c for s in mock_reg for c in s.countries}
    assert all_countries <= covered


# ---------------------------------------------------------------------------
# 3. External signal shock shifts supplier -> part -> product grade & auto-resolves when cleared
# ---------------------------------------------------------------------------
def test_external_signal_drives_rollup_and_auto_resolves_when_risk_drops():
    add_all([
        Product(product_id="FG-TEST", name="Test Edition", launch_date=date(2026, 11, 22),
                day_one_qty=10000, source="products.csv"),
        LaunchStatus(product_id="FG-TEST", edition="Test",
                     as_of=datetime(2026, 11, 21, 13, 0, tzinfo=timezone.utc),
                     day_one_demand=10000, packed=0, still_to_pack=10000,
                     rate_per_hour=500.0, line_status="running", source="launch_status.xlsx"),
        Material(component_id="CMP-TST-001", description="Test Widget", critical=True,
                 drawing_rev="A", source="components.csv"),
        BomLine(parent_id="FG-TEST", child_id="CMP-TST-001", qty_per=1.0, source="bom.csv"),
        StockLot(material_id="CMP-TST-001", batch="B1", quantity=0, source="stock.csv"),
    ])
    sup = add_all([
        Supplier(supplier_id="SUP-9999", name="Risky Corp", country="CN", status="at risk",
                 qualification_status="conditional", qualification_valid_until=date(2026, 10, 1),
                 source="profile.json"),
    ])[0]
    add_all([
        SupplierPartMap(supplier_id="SUP-9999", component_id="CMP-TST-001", share=1.0,
                        primary=True, lead_time_days=95, note="export delay", source="components.csv"),
    ])

    class HighShockProvider:
        name = "shock"

        def fetch(self, **kw) -> list[ExternalSignal]:
            return [
                ExternalSignal(
                    provider="portal_news", category="trade_news",
                    title="Export ban hits CN widgets", score_0_100=95.0,
                    summary="Export ban hits CN widgets", countries=["CN"],
                    source_url="soony/news/ban.json", quote="ban in effect", live=True,
                ),
                ExternalSignal(
                    provider="polymarket", category="external_politics",
                    title="CN tariff escalation", score_0_100=85.0,
                    summary="Polymarket odds 85%: CN tariff escalation", countries=["CN"],
                    source_url="https://polymarket.com/event/cn-tariff", live=True,
                ),
            ]

    class CalmProvider:
        name = "calm"

        def fetch(self, **kw) -> list[ExternalSignal]:
            return []

    bus = Coordinator()
    agent = RiskRater(bus)
    agent.providers = (HighShockProvider(),)
    bus.emit(events.TRUTH_UPDATED, {"tables": ["suppliers"]}, source="test")
    bus.run_until_idle()

    scores = _risk_scores()
    assert scores[("supplier", "SUP-9999")].grade >= 5
    assert scores[("part", "CMP-TST-001")].grade >= 5
    assert scores[("product", "FG-TEST")].grade >= 5
    sup_issue = next(i for i in _risk_issues(status="open") if i.type == "high_risk_rating" and i.supplier_id == "SUP-9999")

    # Supplier resolves its qualification/status and external shock clears -> grade drops below 5 -> Finding auto-resolves
    update_row(Supplier, sup.id, {"status": "active", "qualification_valid_until": date(2027, 12, 31)},
               source="profile_fixed.json", agent="ingest", on_conflict="overwrite")
    agent.providers = (CalmProvider(),)
    bus.emit(events.TRUTH_UPDATED, {"tables": ["suppliers"]}, source="test")
    bus.run_until_idle()

    scores2 = _risk_scores()
    assert scores2[("supplier", "SUP-9999")].grade < 5
    with session() as s:
        refreshed = s.get(Issue, sup_issue.id)
    assert refreshed is not None and refreshed.status == "resolved"


def test_general_wto_headline_does_not_create_a_country_exposure(monkeypatch):
    from backend.agents import risk_signals
    xml = """<rss><channel>
      <item><title>WTO reviews global tariff monitoring</title><link>https://www.wto.org/general</link></item>
      <item><title>Japan changes export rules</title><link>https://www.wto.org/japan</link></item>
    </channel></rss>"""
    monkeypatch.setattr(risk_signals, "_cached_get_json", lambda *a, **k: None)
    monkeypatch.setattr(risk_signals, "_cached_get_text", lambda url, *a, **k: xml if url == risk_signals.WTO_RSS_URL else None)
    ok, signals = risk_signals.RegulationChangeProvider()._fetch_live({"DE", "JP", "CN", "VN"})
    assert ok
    assert {s.source_url: s.countries for s in signals} == {
        "https://www.wto.org/general": [], "https://www.wto.org/japan": ["JP"]}


def test_current_ratings_survive_more_than_one_page_of_older_alerts():
    from fastapi.testclient import TestClient
    from backend.app.main import app
    with session() as db:
        db.add_all([RiskScore(entity_type="supplier", entity_id="S", grade=6, rating="CCC") for _ in range(501)])
        db.add_all([RiskScore(entity_type="supplier", entity_id="S", grade=2, rating="AA"),
                    RiskScore(entity_type="part", entity_id="S", grade=4, rating="BBB")])
        db.commit()
    current = TestClient(app).get("/api/risk?current=true").json()["data"]
    assert [(r["entity_type"], r["entity_id"], r["rating"]) for r in current] == [
        ("part", "S", "BBB"), ("supplier", "S", "AA")]


def test_live_market_binds_named_outcome_country_and_active_question(monkeypatch):
    monkeypatch.setattr(risk_signals.llm, "is_mock", lambda: False)
    now = datetime(2026, 11, 21, tzinfo=timezone.utc)
    market = dict(question="Will Canada increase export tariffs?", outcomes='["No","Yes"]',
                  outcomePrices='["0.19","0.81"]', id="Q-7", endDate="2026-12-02T10:00:00Z")
    rows = [dict(slug="group", markets=[
        dict(market, question="Who won the football match?", outcomes=["Red", "Blue"]),
        dict(market, endDate="2026-11-20T10:00:00Z"), market, dict(market, id="later-threshold")])]
    monkeypatch.setattr(risk_signals, "_cached_get_json", lambda *a, **k: rows)
    def collect(countries):
        return PolymarketProvider().fetch(countries=countries, ports={}, shipments=[], documents=[], reference_now=now)
    signal, = collect({"CA", "CN"})
    assert signal.countries == ["CA"] and signal.score_0_100 == 81
    assert signal.market == dict(yes_pct=81.0, disruption_outcome="Yes", question_id="Q-7",
                                 closes_at=market["endDate"], scope_basis="Country named in the question")
    assert signal.rating_input("CA", set(), set())["market"] == signal.market
    assert collect({"CN"}) == []
    for invalid in [dict(market, outcomes=None), dict(market, outcomes=["No", "No"]),
                    dict(market, outcomePrices=None, bestBid=.9), dict(market, closed=True),
                    dict(market, outcomePrices='["0","nan"]')]:
        rows[0]["markets"] = [invalid]
        assert collect({"CA"}) == []
    rows.clear()
    assert not collect({"CA"})


def test_new_supplier_country_is_used_by_name_in_public_news_search(monkeypatch):
    queries = []
    def search(url, params=None):
        if params and "q" in params:
            queries.append(params["q"])
        return "<rss><channel/></rss>"
    monkeypatch.setattr(risk_signals, "_cached_get_text", search)
    monkeypatch.setattr(risk_signals, "_cached_get_json", lambda *a, **k: {})
    PoliticalNewsProvider()._fetch_google_news({"PT"})
    RegulationChangeProvider()._fetch_live({"PT"})
    assert len(queries) == 2 and all('"Portugal"' in q for q in queries)


def test_follow_exact_market_after_it_leaves_volume_ranking(monkeypatch):
    from dataclasses import asdict
    import pytest

    now = datetime(2026, 11, 21, tzinfo=timezone.utc)
    market = dict(question="Will Canada increase export tariffs?", outcomes=["No", "Yes"], outcomePrices=[.20, .80], id="m42")
    event = dict(slug="canadian-tariffs", markets=[market])
    listing, detail = [event], [event]
    def request(url, params=None):
        if params:
            return listing
        assert url.endswith("/events/slug/canadian-tariffs")
        return detail[0]
    monkeypatch.setattr(risk_signals, "_cached_get_json", request)
    provider = PolymarketProvider()
    watched = [asdict(s) for s in provider._fetch_live({"CA"}, now)]
    listing.clear()
    event["markets"] = [dict(market, id="different-contract", outcomePrices=[.99, .01]), dict(market, outcomePrices=[.11, .89])]
    reading, = provider._fetch_live({"CA"}, now, watched)
    assert reading.market["question_id"] == "m42" and reading.market["yes_pct"] == 89
    detail[0] = None
    with pytest.raises(risk_signals.RiskFeedUnavailable):
        provider._fetch_live({"CA"}, now, watched)
    detail[0] = dict(event, closed=True)
    assert provider._fetch_live({"CA"}, now, watched) == []


def test_risk_playbook_all_four_engines_on_real_drops():
    from fastapi.testclient import TestClient
    from backend.app.main import app
    from backend.core import trace

    # Replay 3 real drops (baseline + 16:08 + 17:23)
    for _ in range(3):
        system.replay_step()
    assert _risk_errors() == []

    client = TestClient(app)
    res = client.get("/api/risk/playbook")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    data = body["data"]

    # 1. Path-to-Upgrade Optimizer (CCC -> improved rating)
    plans = {p["product_id"]: p for p in data["upgrade_plans"]}
    assert "FG-GH6-CE" in plans
    ce = plans["FG-GH6-CE"]
    assert ce["levers"], "expected concrete upgrade levers for FG-GH6-CE"
    assert ce["projected_score"] < ce["current_score"]
    assert ce["projected_grade"] <= ce["current_grade"]

    # 2. Predictive Pre-Emptive Chasing (72h early external triggers)
    alerts = data["preemptive_alerts"]
    assert len(alerts) >= 1
    assert all(a["lead_time_advantage_hours"] >= 24 and a["draft_body"] and a["draft_body_en"] for a in alerts)
    assert all(a["status"] in ("ready", "dispatched", "blocked_anti_spam") for a in alerts)

    # 3. Geo-Decorrelated 2nd-Source Scout + Spec-Grounded RFQ Pack
    scouts = data["second_source_scouts"]
    assert len(scouts) >= 1
    for sc in scouts:
        assert len(sc["candidates"]) == 3
        assert all(c["country"] != sc["blocked_country"] for c in sc["candidates"])
        assert sc["projected_dual_score"] < sc["current_score"]
        assert sc["part_id"] in sc["rfq_body"]

    # 4. Risk-Triggered Circuit Breakers + trace audit
    breakers = data["circuit_breakers"]
    assert len(breakers) >= 1
    assert any(b["grade"] >= 5 and len(b["rules_triggered"]) >= 2 for b in breakers)
    logs = [e for e in trace.recent(limit=200) if e.agent == "risk" and e.action == "circuit_breaker_tripped"]
    assert logs, "expected circuit_breaker_tripped logged to agent_log"


def test_preemptive_chase_endpoint_dispatches_via_chaser():
    from fastapi.testclient import TestClient
    from backend.app.main import app
    from backend.core import trace
    from backend.core.models import OutboxMessage

    system.replay_step()
    client = TestClient(app)
    pb = client.get("/api/risk/playbook").json()["data"]
    assert pb["preemptive_alerts"]
    alert = pb["preemptive_alerts"][0]

    before_count = len(all_rows(OutboxMessage))
    resp = client.post("/api/risk/preemptive-chase", json={
        "alert_id": alert["alert_id"],
        "supplier_id": alert["supplier_id"],
        "shipment_id": alert["shipment_id"],
        "po_number": alert["po_number"],
        "part_id": alert["part_id"],
        "ask": alert["draft_body_en"],
    })
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert len(all_rows(OutboxMessage)) >= before_count
    logs = [e for e in trace.recent(limit=50) if e.agent == "human" and e.action == "preemptive_chase"]
    assert logs

