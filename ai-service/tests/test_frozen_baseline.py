"""V2.0-B closure:冻结基线绑定 —— 评估/恢复不回读可变 Incident、不猜最近 Run。

回归矩阵:
1. 健康基线:E1 判定用 RunContextSnapshot 冻结值(Incident 行被改不影响);
2. digest 基线:按 agent_run_id 精确取 Run 自己的 baseline(多 Run 不串);
3. fail closed:agent_run_id 缺失/无效/跨 Incident → RUN_CONTEXT_UNRESOLVED,
   且绝不发起"最近 Run"查询;
4. Runner 初始状态携带两个基线引用。
"""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.capabilities.mysql_missing_index.capability import evaluate_metrics
from app.db.engine import get_control_engine
from app.db.models import Incident
from app.repositories import incident_repo, run_repo
from app.services import runner
from app.services.recovery_service import _healthy_baseline_for
from app.services import slow_query_service
from app.tools_core.errors import ToolBusinessError


def _make_incident_with_baseline(healthy: dict | None):
    """V2.1-C:健康基线权威来源 = baseline_metrics_json(仅 quality='OK' 有效);
    healthy_metrics_baseline 旧列(故障态实时值)已停止读取。"""
    inc = incident_repo.create_incident(
        f"基线冻结-{uuid.uuid4().hex[:6]}", None, "high", "inventory-service",
        affected_service_ref="inventory-service",
        affected_operation_ref="INVENTORY_LOOKUP")
    if healthy is not None:
        import json
        with Session(get_control_engine()) as s:
            s.execute(text("UPDATE incident SET baseline_metrics_json=:h, "
                           "baseline_quality='OK' WHERE id=:i"),
                      {"h": json.dumps(healthy), "i": inc.id})
            s.commit()
    return inc


def _mutate_incident_baseline(inc_id: int, healthy: dict):
    """模拟恢复窗口期内 Incident 健康基线被修改(改权威来源列)。"""
    import json
    with Session(get_control_engine()) as s:
        s.execute(text("UPDATE incident SET baseline_metrics_json=:h, "
                       "baseline_quality='OK' WHERE id=:i"),
                  {"h": json.dumps(healthy), "i": inc_id})
        s.commit()


# ---------- 1) E1 健康基线来自冻结 RunContext ----------

def test_evaluate_metrics_uses_frozen_healthy_baseline():
    """冻结 healthy p95=200 → P95 150 判定未降级(passed=False);
    若回读被改为 1 的 Incident 行则误判 recovered。"""
    inc = _make_incident_with_baseline({"p95_ms": 200})
    run = run_repo.create_run(inc.id, baseline={"d": {"count": 0}})
    _mutate_incident_baseline(inc.id, {"p95_ms": 1})

    state = {"incident_id": inc.id, "run_id": run.id,
             "healthy_baseline_ref": {"p95_ms": 200}}
    ev = evaluate_metrics({"success": True, "data": {"p95Ms": 150}}, state)
    assert ev[0]["id"] == "E1" and ev[0]["passed"] is False


def test_runner_initial_state_carries_baseline_refs():
    inc = _make_incident_with_baseline({"p95_ms": 120})
    baseline = {"digest-x": {"count": 7, "rows_examined": 9}}
    run = run_repo.create_run(inc.id, baseline=baseline)
    initial = runner._initial_state_from_run(run)
    assert initial["healthy_baseline_ref"] == {"p95_ms": 120}
    assert initial["baseline_ref"] == baseline


# ---------- 2) digest 基线按精确 Run 绑定 ----------

def test_digest_baseline_uses_bound_run_not_latest():
    """同 Incident 两个不同 digest 基线的 Run:旧 Run 调查仍用自己的 baseline。"""
    inc = _make_incident_with_baseline(None)
    baseline_a = {"SELECT d": {"count": 100, "total_latency_us": 0, "rows_examined": 0}}
    baseline_b = {"SELECT d": {"count": 5, "total_latency_us": 0, "rows_examined": 0}}
    run_a = run_repo.create_run(inc.id, baseline=baseline_a)
    run_repo.create_run(inc.id, baseline=baseline_b)   # 更新的 Run(list_runs[0])

    current = {"SELECT d": {"count": 105, "total_latency_us": 0, "rows_examined": 0}}
    orig_fetch = slow_query_service._fetch_current_digests
    slow_query_service._fetch_current_digests = lambda: current
    try:
        out = slow_query_service.list_expensive_digests(inc.id, agent_run_id=run_a.id)
    finally:
        slow_query_service._fetch_current_digests = orig_fetch
    top = next(d for d in out if d["digest"].startswith("SELECT d"))
    assert top["count_delta"] == 5        # 105 - 100(自己 Run 的基线),不是 105 - 5


# ---------- 3) fail closed:禁止"最近 Run"猜测 ----------

@pytest.mark.parametrize("agent_run_id", [0, 999_999_999])
def test_digest_missing_or_invalid_run_fails_closed(agent_run_id, monkeypatch):
    inc = _make_incident_with_baseline(None)
    _make_incident_with_baseline(None)  # 存在其他 Run,也不允许猜

    def _no_query():
        raise AssertionError("fail closed 路径不得查询 performance_schema")

    monkeypatch.setattr(slow_query_service, "_fetch_current_digests", _no_query)
    with pytest.raises(ToolBusinessError) as ei:
        slow_query_service.list_expensive_digests(inc.id, agent_run_id=agent_run_id)
    assert ei.value.code == "RUN_CONTEXT_UNRESOLVED"


def test_digest_run_bound_to_other_incident_fails_closed(monkeypatch):
    inc_a = _make_incident_with_baseline(None)
    inc_b = _make_incident_with_baseline(None)
    run_of_b = run_repo.create_run(inc_b.id)

    def _no_query():
        raise AssertionError("fail closed 路径不得查询 performance_schema")

    monkeypatch.setattr(slow_query_service, "_fetch_current_digests", _no_query)
    with pytest.raises(ToolBusinessError) as ei:
        slow_query_service.list_expensive_digests(inc_a.id, agent_run_id=run_of_b.id)
    assert ei.value.code == "RUN_CONTEXT_UNRESOLVED"


# ---------- 4) 恢复验证健康基线同样走冻结快照 ----------

def test_recovery_healthy_baseline_frozen_and_fail_closed():
    inc = _make_incident_with_baseline({"p95_ms": 200})
    run = run_repo.create_run(inc.id)
    _mutate_incident_baseline(inc.id, {"p95_ms": 1})
    assert _healthy_baseline_for(inc.id, run.id) == {"p95_ms": 200}   # 冻结值
    with pytest.raises(ToolBusinessError) as ei:
        _healthy_baseline_for(inc.id, 999_999_999)                    # 无效 Run
    assert ei.value.code == "RUN_CONTEXT_UNRESOLVED"
    with pytest.raises(ToolBusinessError) as ei:
        _healthy_baseline_for(inc.id, 0)                              # 缺失
    assert ei.value.code == "RUN_CONTEXT_UNRESOLVED"
