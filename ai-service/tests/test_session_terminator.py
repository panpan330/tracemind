import pytest
from app.services import session_terminator as st


class FakeEngine:
    """内存版执行引擎:记录 KILL 调用,可按场景返回重查结果。"""
    def __init__(self):
        self.killed: list[int] = []
        self.relations = {}   # processlist_id -> 当前事务信息(模拟重查)
        self.resolved = False

    def query_blocking(self, processlist_id: int) -> dict | None:
        if self.resolved:
            return None
        return self.relations.get(processlist_id)

    def execute_kill(self, processlist_id: int) -> None:
        self.killed.append(processlist_id)


def _proposal(**kw):
    base = {
        "action_type": "TERMINATE_BLOCKING_SESSION",
        "parameters_hash": "h", "blocking_relation_hash": "rh",
        "parameters": {"processlist_id": 88, "blocking_transaction_id": 88,
                       "blocking_lock_ref": "lr2", "locked_schema": "tracemind_business",
                       "locked_table": "inventory", "locked_index": "idx_sku_warehouse",
                       "waiting_transaction_id": 100,
                       "waiting_query_ref": "INVENTORY_RESERVATION"},
        "status": "pending",
    }
    base.update(kw)
    return base


def _approval(**kw):
    base = {"status": "approved", "expires_at": "2099-01-01T00:00:00Z",
            "parameters_hash": "h"}
    base.update(kw)
    return base


@pytest.fixture(autouse=True)
def clear_idem():
    st._executed_keys.clear()
    yield
    st._executed_keys.clear()


def test_valid_kill_exactly_once():
    eng = FakeEngine()
    eng.relations[88] = {"transaction_id": 88, "account": "app_business",
                         "age_ms": 12000, "holds_lock": True, "is_system": False}
    p = _proposal()
    r1 = st.execute(p, _approval(), eng)
    assert r1["kill_attempted"] is True and r1["execution_result"] == "executed"
    # 重复执行:幂等,不再 KILL
    r2 = st.execute(p, _approval(), eng)
    assert r2["kill_attempted"] is False and r2["execution_result"] == "already_executed"


def test_already_resolved():
    eng = FakeEngine()
    eng.resolved = True
    r = st.execute(_proposal(), _approval(), eng)
    assert r["execution_result"] == "already_resolved" and r["kill_attempted"] is False


def test_target_changed_when_processlist_reused():
    eng = FakeEngine()
    # 原事务消失,processlist 已属另一事务
    eng.relations[88] = {"transaction_id": 999, "account": "app_business",
                         "age_ms": 5, "holds_lock": True, "is_system": False}
    r = st.execute(_proposal(), _approval(), eng)
    assert r["execution_result"] == "target_changed" and r["kill_attempted"] is False


def test_evidence_stale_when_lock_ref_changed():
    eng = FakeEngine()
    eng.relations[88] = {"transaction_id": 88, "account": "app_business",
                         "age_ms": 12000, "holds_lock": False, "is_system": False}
    r = st.execute(_proposal(), _approval(), eng)
    assert r["execution_result"] == "evidence_stale" and r["kill_attempted"] is False


def test_reject_forbidden_account():
    eng = FakeEngine()
    eng.relations[88] = {"transaction_id": 88, "account": "tracemind_control_app",
                         "age_ms": 12000, "holds_lock": True, "is_system": False}
    r = st.execute(_proposal(), _approval(), eng)
    assert r["execution_result"] == "rejected_forbidden_account"


def test_reject_not_approved():
    r = st.execute(_proposal(), _approval(status="pending"), FakeEngine())
    assert r["execution_result"] == "rejected_not_approved"


def test_reject_system_thread():
    eng = FakeEngine()
    eng.relations[88] = {"transaction_id": 88, "account": "app_business",
                         "age_ms": 12000, "holds_lock": True, "is_system": True}
    r = st.execute(_proposal(), _approval(), eng)
    assert r["execution_result"] == "rejected_system_thread" and r["kill_attempted"] is False


def test_reject_invalid_processlist():
    eng = FakeEngine()
    p = _proposal()
    p["parameters"]["processlist_id"] = -1
    r = st.execute(p, _approval(), eng)
    assert r["execution_result"] == "invalid_target" and r["kill_attempted"] is False
