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
