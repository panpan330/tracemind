"""处置安全测试(独立套件,不并入 eval_agent 根因准确率):Approval → Revalidation → Action Executor → Idempotency。
设计 V1.3 §8.1 处置安全测试。指标:负例错误终止会话率=0%、未经审批处置率=0%、重复处置率=0%、合法审批处置成功率=100%。"""
import pytest
from app.services import session_terminator as st


class FakeEngine:
    def __init__(self):
        self.killed: list[int] = []
        self.relations = {}
        self.resolved = False

    def query_blocking(self, processlist_id: int) -> dict | None:
        if self.resolved:
            return None
        return self.relations.get(processlist_id)

    def execute_kill(self, processlist_id: int) -> None:
        self.killed.append(processlist_id)


def _proposal(**kw):
    base = {"action_type": "TERMINATE_BLOCKING_SESSION", "parameters_hash": "h",
            "blocking_relation_hash": "rh",
            "parameters": {"processlist_id": 88, "blocking_transaction_id": 88}}
    base.update(kw)
    return base


def _approval(**kw):
    base = {"status": "approved", "expires_at": "2099-01-01T00:00:00Z", "parameters_hash": "h"}
    base.update(kw)
    return base


@pytest.fixture(autouse=True)
def clear_idem():
    st._executed_keys.clear()
    yield
    st._executed_keys.clear()


def test_valid_approval_kills_exactly_once():
    """合法路径:有效审批 + 关系一致 + 账号允许 + 证据未过期 → 实际 KILL 恰好一次。"""
    eng = FakeEngine()
    eng.relations[88] = {"transaction_id": 88, "account": "app_business",
                         "age_ms": 12000, "holds_lock": True, "is_system": False}
    r1 = st.execute(_proposal(), _approval(), eng)
    assert r1["kill_attempted"] is True and eng.killed == [88]
    r2 = st.execute(_proposal(), _approval(), eng)
    assert r2["kill_attempted"] is False and eng.killed == [88]  # 重复处置率 0


def test_no_unauthorized_kill():
    """未审批调用 → 拒绝,实际不 KILL。"""
    eng = FakeEngine()
    eng.relations[88] = {"transaction_id": 88, "account": "app_business",
                         "age_ms": 12000, "holds_lock": True, "is_system": False}
    r = st.execute(_proposal(), _approval(status="pending"), eng)
    assert r["execution_result"] == "rejected_not_approved"
    assert eng.killed == []


def test_no_negative_kill():
    """负例(已解决/复用/过期/禁止账号)→ 一律不 KILL(负例错误终止会话率 0%)。"""
    scenarios = [
        {"relations": {}, "resolved": True, "approval": _approval(), "expect": "already_resolved"},
        {"relations": {88: {"transaction_id": 999, "account": "app_business", "age_ms": 5,
                            "holds_lock": True, "is_system": False}},
         "resolved": False, "approval": _approval(), "expect": "target_changed"},
        {"relations": {88: {"transaction_id": 88, "account": "app_business", "age_ms": 12000,
                            "holds_lock": False, "is_system": False}},
         "resolved": False, "approval": _approval(), "expect": "evidence_stale"},
        {"relations": {88: {"transaction_id": 88, "account": "tracemind_control_app",
                            "age_ms": 12000, "holds_lock": True, "is_system": False}},
         "resolved": False, "approval": _approval(), "expect": "rejected_forbidden_account"},
        {"relations": {88: {"transaction_id": 88, "account": "app_business", "age_ms": 12000,
                            "holds_lock": True, "is_system": True}},
         "resolved": False, "approval": _approval(), "expect": "rejected_system_thread"},
        {"relations": {88: {"transaction_id": 88, "account": "app_business", "age_ms": 12000,
                            "holds_lock": True, "is_system": False}},
         "resolved": False, "approval": _approval(expires_at="2020-01-01T00:00:00Z"),
         "expect": "rejected_expired"},
    ]
    for s in scenarios:
        eng = FakeEngine()
        eng.relations = s["relations"]
        eng.resolved = s["resolved"]
        r = st.execute(_proposal(), s["approval"], eng)
        assert r["execution_result"] == s["expect"], s["expect"]
        assert eng.killed == []  # 负例绝不发 KILL
