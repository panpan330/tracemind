"""V2.1-D:fix_execution 审计落库回归矩阵(KILL 审计缺口修复)。

live 缺陷:004/006 均为 CREATE TABLE IF NOT EXISTS,004 旧结构先建导致 006 新列
静默缺失 → create_execution INSERT 一直引用不存在列,被 _record_fix_execution
裸 except 吞掉,KILL 审计从未落库。

1. INSERT 成功且关键字段正确(真实 MySQL);
2. 幂等键不跨 Incident/Proposal 冲突(同 parameters_hash、不同 approval);
3. 同幂等键重复审计不产生第二行(duplicate 语义,不抛异常);
4. 审计写入失败不静默:落 audit_write_failed 事件,不抛、不触发 KILL 重试。
"""
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.repositories import fix_execution_repo
from app.repositories import incident_repo


@pytest.fixture()
def cleanup():
    inc_ids = []

    def register(inc_id):
        inc_ids.append(inc_id)

    yield register
    with Session(get_control_engine()) as s:
        for inc in inc_ids:
            s.execute(text("DELETE FROM fix_execution WHERE incident_id=:i"),
                      {"i": inc})
            s.execute(text("DELETE FROM incident_event WHERE incident_id=:i"),
                      {"i": inc})
        s.commit()


def _mk_incident(cleanup) -> int:
    inc = incident_repo.create_incident(
        f"审计-{__import__('uuid').uuid4().hex[:6]}", None, "high",
        "inventory-service")
    cleanup(inc.id)
    return inc.id


def test_create_execution_persists_kill_audit(cleanup):
    """复现 live 缺陷:INSERT 引用的列必须真实存在,审计行落库且关键字段正确。"""
    inc = _mk_incident(cleanup)
    out = fix_execution_repo.create_execution(
        incident_id=inc, fix_proposal_id=42, approval_id=1001,
        idempotency_key=fix_execution_repo.build_idempotency_key(
            incident_id=inc, fix_proposal_id=42, approval_id=1001,
            parameters_hash="h1"),
        blocking_relation_hash="abc123", status="succeeded",
        execution_result="executed", kill_attempted=True,
        actual_processlist_id=777)
    assert out["status"] == "succeeded"
    with Session(get_control_engine()) as s:
        row = s.execute(text(
            "SELECT incident_id, fix_proposal_id, approval_id, idempotency_key, "
            "blocking_relation_hash, status, execution_result, kill_attempted, "
            "actual_processlist_id, started_at, finished_at FROM fix_execution "
            "WHERE incident_id=:i"), {"i": inc}).mappings().one()
    assert row["fix_proposal_id"] == 42
    assert row["approval_id"] == 1001
    assert row["idempotency_key"].startswith("appr:1001")
    assert row["blocking_relation_hash"] == "abc123"
    assert row["status"] == "succeeded"
    assert row["execution_result"] == "executed"
    assert row["kill_attempted"] == 1
    assert row["actual_processlist_id"] == 777
    assert row["started_at"] is not None and row["finished_at"] is not None


def test_idempotency_key_does_not_collide_across_incidents(cleanup):
    """两个 Incident 用相同 parameters_hash(不同 proposal/approval)→ 审计都落库。"""
    inc_a = _mk_incident(cleanup)
    inc_b = _mk_incident(cleanup)
    for inc, appr in ((inc_a, 2001), (inc_b, 2002)):
        fix_execution_repo.create_execution(
            incident_id=inc, fix_proposal_id=None, approval_id=appr,
            idempotency_key=fix_execution_repo.build_idempotency_key(
                incident_id=inc, fix_proposal_id=None, approval_id=appr,
                parameters_hash="SAME_PARAMS"),
            blocking_relation_hash="", status="succeeded",
            execution_result="already_resolved", kill_attempted=False,
            actual_processlist_id=None)
    with Session(get_control_engine()) as s:
        n = s.execute(text("SELECT COUNT(*) FROM fix_execution "
                           "WHERE incident_id IN (:a, :b)"),
                      {"a": inc_a, "b": inc_b}).scalar()
    assert n == 2


def test_same_idempotency_key_is_duplicate_not_second_row(cleanup):
    """同 approval 重复审计:标记 duplicate,不产生第二行,不抛异常。"""
    inc = _mk_incident(cleanup)
    key = fix_execution_repo.build_idempotency_key(
        incident_id=inc, fix_proposal_id=9, approval_id=3001,
        parameters_hash="h9")
    first = fix_execution_repo.create_execution(
        incident_id=inc, fix_proposal_id=9, approval_id=3001,
        idempotency_key=key, blocking_relation_hash="", status="succeeded",
        execution_result="executed", kill_attempted=True,
        actual_processlist_id=55)
    second = fix_execution_repo.create_execution(
        incident_id=inc, fix_proposal_id=9, approval_id=3001,
        idempotency_key=key, blocking_relation_hash="", status="succeeded",
        execution_result="executed", kill_attempted=True,
        actual_processlist_id=55)
    assert first["status"] == "succeeded"
    assert second["status"] == "duplicate"
    with Session(get_control_engine()) as s:
        n = s.execute(text("SELECT COUNT(*) FROM fix_execution "
                           "WHERE idempotency_key=:k"), {"k": key}).scalar()
    assert n == 1


def test_record_fix_execution_failure_is_visible_not_silent(monkeypatch, cleanup):
    """审计写入失败:落 audit_write_failed 事件(坐席可见),不抛、绝不触发 KILL。"""
    from app.agent import nodes as agent_nodes
    from app.repositories import event_repo, fix_execution_repo

    inc = _mk_incident(cleanup)
    events = []

    def boom(**kwargs):
        raise RuntimeError("simulated audit write failure")

    monkeypatch.setattr(fix_execution_repo, "create_execution", boom)
    monkeypatch.setattr(event_repo, "append_event",
                        lambda iid, et, payload=None: events.append((iid, et, payload)))
    agent_nodes._record_fix_execution(
        {"incident_id": inc}, {"fix_proposal_id": 1, "parameters_hash": "h"},
        {"approval_id": 7}, "succeeded", {"execution_result": "executed"})
    assert len(events) == 1
    iid, et, payload = events[0]
    assert iid == inc and et == "audit_write_failed"
    assert "simulated audit write failure" in (payload or {}).get("error", "")


def test_record_fix_execution_uses_approval_scoped_key(monkeypatch, cleanup):
    """_record_fix_execution 生成的幂等键必须带 approval 维度(跨 Incident 不冲突)。"""
    from app.agent import nodes as agent_nodes
    from app.repositories import fix_execution_repo

    inc = _mk_incident(cleanup)
    captured = {}
    real = fix_execution_repo.create_execution

    def spy(**kwargs):
        captured["idempotency_key"] = kwargs["idempotency_key"]
        return real(**kwargs)

    monkeypatch.setattr(fix_execution_repo, "create_execution", spy)
    agent_nodes._record_fix_execution(
        {"incident_id": inc}, {"fix_proposal_id": 3, "parameters_hash": "SAME"},
        {"approval_id": 4002}, "succeeded", {"execution_result": "executed"})
    assert captured["idempotency_key"] == "appr:4002"
