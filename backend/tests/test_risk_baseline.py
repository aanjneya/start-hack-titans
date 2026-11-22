from fastapi.testclient import TestClient

from backend import system
from backend.app.main import app
from backend.agents.risk_signals import DEFAULT_PROVIDERS
from backend.core.models import RiskFeedSnapshot, RiskScore
from backend.core.store import add_all, all_rows


def test_viewing_risk_scenarios_reuses_the_recorded_ratings_and_feed_history(monkeypatch):
    system.replay_step()

    calls = []

    def blocked(**kwargs):
        calls.append(kwargs)
        raise AssertionError("Viewing a scenario attempted a provider refresh")

    for provider in DEFAULT_PROVIDERS:
        monkeypatch.setattr(provider, "fetch", blocked)

    def feed_history():
        return {s.key: (s.collected_at, s.signals) for s in all_rows(RiskFeedSnapshot)}

    before = feed_history()
    assert before
    client = TestClient(app)
    scores = {r["entity_id"]: r for r in client.get("/api/risk?current=true").json()["data"]
              if r["entity_type"] == "product"}
    plans = client.get("/api/risk/playbook").json()["data"]["upgrade_plans"]
    assert {p["product_id"] for p in plans} == set(scores) and plans
    for plan in plans:
        row = scores[plan["product_id"]]
        assert (plan["current_score"], plan["current_rating"]) == (row["reasons"][0]["score_0_100"], row["rating"])
    assert not calls
    assert before == feed_history()

    # A historical row with a grade and no factor details cannot support a numerical what-if.
    product = plans[0]["product_id"]
    add_all([RiskScore(entity_type="product", entity_id=product, grade=3, rating="A",
                       reasons=[{"text": "Rating recorded without a point breakdown."}])])
    remaining = client.get("/api/risk/playbook").json()["data"]["upgrade_plans"]
    assert {p["product_id"] for p in remaining} == set(scores) - {product}
    assert not calls
    assert before == feed_history()


def test_current_market_history_uses_the_adjacent_supplier_publication_only():
    def observation(price=72, question="Q", live=True, outcome="Yes", **kwargs):
        return dict(provider="polymarket", evidence={"path": "https://polymarket.com/event/trade"},
                    title="Will export rules change?", live=live,
                    market=dict(yes_pct=price, question_id=question, disruption_outcome=outcome), **kwargs)

    def score(supplier, *observations):
        return RiskScore(entity_type="supplier", entity_id=supplier, grade=4, rating="BBB",
                         reasons=[dict(signal_inputs={"external_politics": list(observations)})])

    add_all([
        score("same-grade", observation(12)), score("same-grade", observation(18)), score("same-grade", observation(23)),
        score("same-price", observation(live=False, question=None)), score("same-price", observation(live=False, question=None)),
        score("wrong-instrument", observation(50, question="Q1")), score("wrong-instrument", observation(question="Q2")),
        score("new", observation()),
        score("reintroduced", observation()), score("reintroduced"), score("reintroduced", observation()),
        score("mode-change", observation(live=False)), score("mode-change", observation()),
        score("outcome-change", observation()), score("outcome-change", observation(outcome="No")),
        score("retained-before", observation(last_known_at="2026-11-21")), score("retained-before", observation()),
        score("retained-now", observation()), score("retained-now", observation(last_known_at="2026-11-21")),
        score("missing-price", observation(None)), score("missing-price", observation()),
        score("unknown-live-id", observation(question=None)), score("unknown-live-id", observation(question=None)),
    ])
    client = TestClient(app)
    rows = client.get("/api/risk?current=true").json()["data"]
    def markets(rows):
        return [(r["entity_id"], obs["market"]) for r in rows for obs in r["reasons"][0]["signal_inputs"]["external_politics"]]
    assert {supplier: m["previous_yes_pct"] for supplier, m in markets(rows) if "previous_yes_pct" in m} == {
        "same-grade": 18, "same-price": 72}
    assert len(rows) == 11
    history = client.get("/api/risk").json()["data"]
    assert all("previous_yes_pct" not in m for _, m in markets(history))
