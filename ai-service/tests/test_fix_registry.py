"""FixRegistry 确定性映射单测。"""
from app.agent.fix_registry import FixRegistry, build_proposal


def test_resolve_missing_index():
    fix = FixRegistry.resolve("MISSING_INVENTORY_INDEX")
    assert fix.action_type == "CREATE_INVENTORY_INDEX"
    assert fix.index_name == "idx_sku_warehouse"
    assert fix.columns == ["sku_id", "warehouse_id"]
    assert fix.risk_level == "medium"


def test_resolve_unknown_raises():
    import pytest
    with pytest.raises(KeyError):
        FixRegistry.resolve("DROP_EVERYTHING")


def test_build_proposal_deterministic():
    p1 = build_proposal({"description": "x"})
    p2 = build_proposal({"description": "x"})
    assert p1["action_type"] == "CREATE_INVENTORY_INDEX"
    assert p1["parameters_hash"] == p2["parameters_hash"]
    assert "E1~E5" in p1["reason"]          # 模板说明,不调 LLM
    assert p1["risk_level"] == "medium"
