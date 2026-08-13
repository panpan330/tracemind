"""session_terminator.execute 单测:注入 FakeEngine,覆盖全部 8 项语义检查 + 三结果 + 幂等(不触 DB)。

覆盖 app/services/session_terminator.py 的 execute/_to_positive_int 全分支。
"""
import pytest

from app.services import session_terminator as st


class FakeEngine:
    """最小引擎:query_blocking 返回预设 dict|None,execute_kill 记录。"""

    def __init__(self, blocking=None):
        self.blocking = blocking
        self.killed: list[int] = []
        self.queries = 0

    def query_blocking(self, processlist_id: int):
        self.queries += 1
        return self.blocking

    def execute_kill(self, processlist_id: int) -> None:
        self.killed.append(processlist_id)


def _approved():
    return {"status": "approved", "expires_at": None}


def _proposal(pid=42, tx="tx-1", phash="h1"):
    return {"parameters": {"processlist_id": pid, "blocking_transaction_id": tx},
            "parameters_hash": phash}


@pytest.fixture(autouse=True)
def _clear_idem():
    st._executed_keys.clear()
    yield
    st._executed_keys.clear()


def test_rejected_not_approved():
    eng = FakeEngine()
    r = st.execute(_proposal(), {"status": "pending", "expires_at": None}, engine=eng)
    assert r == {"execution_result": "rejected_not_approved", "kill_attempted": False}
    assert eng.queries == 0


def test_rejected_expired():
    eng = FakeEngine()
    r = st.execute(_proposal(), {"status": "approved", "expires_at": "2000-01-01T00:00:00+00:00"},
                   engine=eng)
    assert r == {"execution_result": "rejected_expired", "kill_attempted": False}
    assert eng.queries == 0


def test_invalid_target_non_numeric():
    eng = FakeEngine()
    r = st.execute(_proposal(pid="42; DROP TABLE x"), _approved(), engine=eng)
    assert r == {"execution_result": "invalid_target", "kill_attempted": False}


def test_invalid_target_non_positive():
    eng = FakeEngine()
    r = st.execute(_proposal(pid=0), _approved(), engine=eng)
    assert r == {"execution_result": "invalid_target", "kill_attempted": False}
    r = st.execute(_proposal(pid=-5), _approved(), engine=eng)
    assert r["execution_result"] == "invalid_target"


def test_already_resolved_blocking_none():
    eng = FakeEngine(blocking=None)
    r = st.execute(_proposal(), _approved(), engine=eng)
    assert r == {"execution_result": "already_resolved", "kill_attempted": False}
    assert eng.killed == []


def test_target_changed_tx_mismatch():
    eng = FakeEngine(blocking={"transaction_id": "tx-OTHER", "holds_lock": True,
                               "account": "app_business", "is_system": False})
    r = st.execute(_proposal(tx="tx-1"), _approved(), engine=eng)
    assert r == {"execution_result": "target_changed", "kill_attempted": False}
    assert eng.killed == []


def test_evidence_stale_no_lock():
    eng = FakeEngine(blocking={"transaction_id": "tx-1", "holds_lock": False,
                               "account": "app_business", "is_system": False})
    r = st.execute(_proposal(), _approved(), engine=eng)
    assert r == {"execution_result": "evidence_stale", "kill_attempted": False}
    assert eng.killed == []


def test_rejected_forbidden_account():
    eng = FakeEngine(blocking={"transaction_id": "tx-1", "holds_lock": True,
                               "account": "root", "is_system": False})
    r = st.execute(_proposal(), _approved(), engine=eng)
    assert r == {"execution_result": "rejected_forbidden_account", "kill_attempted": False}
    # 不在 ALLOWED 的账号同样拒绝
    eng2 = FakeEngine(blocking={"transaction_id": "tx-1", "holds_lock": True,
                                "account": "some_app", "is_system": False})
    r2 = st.execute(_proposal(), _approved(), engine=eng2)
    assert r2["execution_result"] == "rejected_forbidden_account"


def test_system_account_rejected_via_forbidden():
    """system user/event_scheduler 已在 FORBIDDEN_ACCOUNTS,execute 先按 forbidden 拒绝;
    is_system 分支为防御冗余(FORBIDDEN 已覆盖,实际不可达)。"""
    eng = FakeEngine(blocking={"transaction_id": "tx-1", "holds_lock": True,
                               "account": "system user", "is_system": True})
    r = st.execute(_proposal(), _approved(), engine=eng)
    assert r == {"execution_result": "rejected_forbidden_account", "kill_attempted": False}
    assert eng.killed == []


def test_executed_success():
    eng = FakeEngine(blocking={"transaction_id": "tx-1", "holds_lock": True,
                               "account": "app_business", "is_system": False})
    r = st.execute(_proposal(), _approved(), engine=eng)
    assert r == {"execution_result": "executed", "kill_attempted": True,
                 "actual_processlist_id": 42}
    assert eng.killed == [42]


def test_already_executed_idempotent():
    eng = FakeEngine(blocking={"transaction_id": "tx-1", "holds_lock": True,
                               "account": "app_business", "is_system": False})
    assert st.execute(_proposal(), _approved(), engine=eng)["execution_result"] == "executed"
    # 同参数再次执行 → 进程内幂等拒绝,不重复 KILL
    r = st.execute(_proposal(), _approved(), engine=eng)
    assert r == {"execution_result": "already_executed", "kill_attempted": False}
    assert eng.killed == [42]


def test_to_positive_int():
    assert st._to_positive_int(42) == 42
    assert st._to_positive_int("7") == 7
    assert st._to_positive_int("abc") is None
    assert st._to_positive_int(None) is None
    assert st._to_positive_int(0) is None
    assert st._to_positive_int(-3) is None


def test_get_terminator_engine_offline_eval_disabled(monkeypatch):
    """offline_eval 下 get_terminator_engine 抛 DATABASE_ACCESS_DISABLED。"""
    from app.config import settings, DATABASE_ACCESS_DISABLED
    monkeypatch.setattr(settings, "run_profile", "offline_eval")
    monkeypatch.setattr(st, "_engine", None)
    with pytest.raises(DATABASE_ACCESS_DISABLED):
        st.get_terminator_engine()


def test_get_terminator_engine_non_local_missing_url(monkeypatch):
    """非 local 且缺 URL → 禁止回退只读引擎。"""
    from app.config import settings
    monkeypatch.setattr(settings, "run_profile", "ci_db")
    monkeypatch.setattr(settings, "session_terminator_db_url", "")
    monkeypatch.setattr(st, "_engine", None)
    with pytest.raises(ValueError, match="禁止回退只读引擎"):
        st.get_terminator_engine()
