import pytest

from backend.agents.reconciler_rules import build_context
from backend.agents.risk_recourse import build_upgrade_plans
from backend.agents.risk_scoring import ComputedRisk, ReasonItem, rate_products
from backend.core.models import BomLine, Product


@pytest.mark.parametrize("listed_fee", [None, 0, 500])
def test_tied_bottlenecks_and_duplicate_buyer_actions(listed_fee):
    ctx = build_context()
    ctx.launch_products = {"game": Product(product_id="game", name="Game")}
    ctx.bom_by_parent = {"game": [BomLine(parent_id="game", child_id=part) for part in ("case", "book")]}
    parts = {part: ComputedRisk("part", part, part, 80, 6, "CCC",
                                [ReasonItem("stock_buffer", 20, "Low stock")],
                                {"supplier_risk": 35, "part_shocks": 25, "stock_buffer": 20}, [])
             for part in ("case", "book")}
    products = rate_products(ctx, parts)

    def pickup(part, **kwargs):
        option = dict(product_id="game", description="port pickup", actions=[{"part": part}],
                      cost_eur=listed_fee, copies_gained=100, approvers=["Buyer"], label="Pickup")
        return dict(key="same-shipment", part=part, decision_id=5, option_index=1, option={**option, **kwargs})

    queue = [pickup("case", actions=[]), pickup("book", actions=[{}, {}]),
             pickup("case", product_id="other"), pickup("case"), pickup("book")]
    plan, = build_upgrade_plans(ctx, parts, products, queue)
    steps = plan["levers"]
    assert len(steps) == 2
    assert [step["queue_key"] for step in steps] == ["same-shipment", None]
    assert steps[0]["limiting_part_after"] == "book"
    assert steps[0]["cumulative_score"] == plan["current_score"] == 82.5
    assert steps[0]["delta_points"] == 0
    assert steps[1]["cumulative_score"] == 62.5
    assert steps[1]["delta_points"] == 20
    assert steps[0]["cost_eur"] == listed_fee
    assert plan["total_cost_eur"] is None  # The second action has no price.
    assert steps[0]["approver"] == "Buyer"
    assert products["game"].score_0_100 == 82.5
