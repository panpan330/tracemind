"""恢复验证(V2.1-C:统一 HTTP P95 恢复信号)。

判定信号 = 与告警同口径的 HTTP P95(recovery_signal 唯一实现,服务/操作取冻结
RunContext,窗口须完全位于修复完成时刻之后);阈值来源 = 合格基线 ×1.2 或显式 SLO;
两者都不可判定 → INCONCLUSIVE(既不宣告恢复也不宣告失败,转人工)。

直接 SQL 探针(_probe_p95_ms)降级为**独立佐证**:保留在返回结果与日志中,
不参与 recovered 判定(它测的是探针自身耗时,与告警口径不同)。
"""
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine, get_readonly_engine
from app.db.models import FixExecution, RecoveryCheck
from app.repositories import incident_repo
from app.services import recovery_signal

INDEX_PRESENT_SQL = text("""
    SELECT COUNT(*) FROM information_schema.statistics
    WHERE table_schema = DATABASE() AND table_name = 'inventory'
    AND index_name = 'idx_sku_warehouse'
""")

PROBE_BATCHES = 3
PROBE_PARAMS = {"skuId": 42, "warehouseId": 7}


def _probe_p95_ms() -> int | None:
    """执行一批固定探测请求,返回本批最大耗时(ms)。"""
    import time
    start = time.monotonic()
    with get_readonly_engine().connect() as conn:
        conn.execute(text(
            "SELECT id FROM inventory WHERE sku_id = :s AND warehouse_id = :w"
        ), {"s": PROBE_PARAMS["skuId"], "w": PROBE_PARAMS["warehouseId"]})
    return int((time.monotonic() - start) * 1000)


def _healthy_baseline_for(incident_id: int, agent_run_id: int):
    """V2.0-B closure:恢复验证的健康基线来自 agent_run_id 的冻结快照(fail closed),
    不回读可变 Incident 行。"""
    from app.repositories import run_repo
    from app.services.run_context import RunContextInvalid, RunContextMissing
    from app.services.run_context import load_snapshot
    from app.tools_core.errors import ToolBusinessError

    if not agent_run_id:
        raise ToolBusinessError(
            "RUN_CONTEXT_UNRESOLVED",
            "恢复验证需要 agent_run_id(禁止回读可变 Incident 行)", retryable=False)
    run = run_repo.get_run(agent_run_id)
    if run is None or run.incident_id != incident_id:
        raise ToolBusinessError(
            "RUN_CONTEXT_UNRESOLVED",
            f"agent_run {agent_run_id} 缺失或不属于 incident {incident_id}", retryable=False)
    try:
        return load_snapshot(run).healthy_baseline_ref
    except (RunContextMissing, RunContextInvalid) as exc:
        raise ToolBusinessError("RUN_CONTEXT_UNRESOLVED", str(exc), retryable=False)


def _index_checks(execution) -> tuple[bool, bool, int | None]:
    """SQL 层确定性检查(索引存在 + 计划使用目标索引 + 估算行数)。"""
    import json

    with get_readonly_engine().connect() as conn:
        index_present = conn.execute(INDEX_PRESENT_SQL).scalar_one() > 0
        row = conn.execute(text(
            "EXPLAIN FORMAT=JSON SELECT id FROM inventory "
            "WHERE sku_id = 42 AND warehouse_id = 7")).fetchone()
        plan = json.loads(row[0]) if row and isinstance(row[0], str) \
            else (row[0] if row else None)
    uses_index = False
    estimated_rows = None
    if plan:
        try:
            table = plan["query_block"]["table"]
            uses_index = table.get("access_type") in ("ref", "const") and \
                "idx_sku_warehouse" in str(table.get("possible_keys", ""))
            estimated_rows = table.get("rows")
        except (KeyError, TypeError):
            uses_index = False
    return index_present, uses_index, estimated_rows


def verify_recovery(incident_id: int, fix_execution_id: int, agent_run_id: int = 0) -> dict:
    """恢复验证(确定性规则,不让 LLM 决定)。

    V2.1-C 判定序:
    ① SQL 层:索引存在 + EXPLAIN 使用目标索引(确定性前置条件);
    ② 恢复信号:与告警同口径的 HTTP P95,窗口须完全位于**修复完成时刻**之后,
       阈值来源 = 冻结快照的合格基线 ×1.2 或显式 SLO;
    ③ recovered = ① 且 ②=recovered;②=not_recovered → not_recovered;
       ②=INCONCLUSIVE → INCONCLUSIVE(不宣告恢复、也不宣告失败,由节点转人工)。
    直接 SQL 探针仅作独立佐证(supporting_probe_p95_ms),不参与判定。
    """
    with Session(get_control_engine()) as session:
        execution = session.get(FixExecution, fix_execution_id)
        if execution is None or execution.incident_id != incident_id:
            raise ValueError("FIX_EXECUTION_NOT_FOUND")
        index_present, uses_index, estimated_rows = _index_checks(execution)
        baseline = _healthy_baseline_for(incident_id, agent_run_id)
        if not isinstance(baseline, dict):
            baseline = None
        signal_at = execution.created_at
        probe_p95 = max((_probe_p95_ms() or 0) for _ in range(PROBE_BATCHES))
        service_ref, operation_ref = _frozen_service_operation(incident_id, agent_run_id)
        signal = recovery_signal.measure_post_signal_p95(
            service_ref, operation_ref, signal_at, baseline=baseline)

        sql_ok = bool(index_present and uses_index)
        if signal.status == recovery_signal.STATUS_INCONCLUSIVE:
            status = "INCONCLUSIVE"
        elif not sql_ok:
            status = "not_recovered"
        else:
            status = ("recovered"
                      if signal.status == recovery_signal.STATUS_RECOVERED
                      else "not_recovered")
        check = RecoveryCheck(incident_id=incident_id, fix_execution_id=fix_execution_id,
                              index_present=index_present,
                              query_plan_uses_target_index=uses_index,
                              estimated_rows_after=estimated_rows,
                              latency_p95_after=(int(signal.p95_ms)
                                                 if signal.p95_ms is not None else None),
                              status=status)
        session.add(check)
        session.commit()
        session.refresh(check)
        return {"status": status, "index_present": index_present,
                "query_plan_uses_target_index": uses_index,
                "estimated_rows_after": estimated_rows,
                "latency_p95_after": check.latency_p95_after,
                "threshold_source": signal.source,
                "signal": signal.as_dict(),
                "supporting_probe_p95_ms": probe_p95}


def _frozen_service_operation(incident_id: int, agent_run_id: int) -> tuple[str, str | None]:
    """服务/操作取冻结 RunContextSnapshot(与告警服务端映射一致)。"""
    from app.repositories import run_repo
    from app.services.run_context import (RunContextInvalid, RunContextMissing,
                                          load_snapshot)
    from app.tools_core.errors import ToolBusinessError

    run = run_repo.get_run(agent_run_id) if agent_run_id else None
    if run is None or run.incident_id != incident_id:
        raise ToolBusinessError(
            "RUN_CONTEXT_UNRESOLVED",
            f"恢复信号需要 agent_run {agent_run_id} 的冻结上下文(禁止回读可变 Incident)",
            retryable=False)
    try:
        snap = load_snapshot(run)
    except (RunContextMissing, RunContextInvalid) as exc:
        raise ToolBusinessError("RUN_CONTEXT_UNRESOLVED", str(exc), retryable=False)
    return snap.service_ref, snap.affected_operation_ref
