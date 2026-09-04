"""V2.0-A final closure:Trace 调查上下文解析 —— 只信任冻结 RunContextSnapshot。

Incident 行本身可变(恢复窗口期内 service/operation/observed_at 可能被更新),
因此 trace 的调查上下文必须来自 agent_run_id 对应 Run 的冻结快照,并以
incident_id 做归属校验;Run 缺失、绑定不一致或快照非法一律 fail closed,
禁止回读当前 Incident 代替。
"""
from app.tools_core.errors import ToolBusinessError


def resolve_frozen_trace_context(incident_id: int, agent_run_id: int) -> dict:
    """按 agent_run_id 读取 RunContextSnapshot,构建 trace 调查上下文(fail closed)。

    返回结构与 trace_service 期望的 incident dict 一致:
    {"id", "affected_service_ref", "affected_operation_ref", "observed_at"}。
    """
    from app.repositories import run_repo
    from app.services.run_context import (RunContextInvalid, RunContextMissing,
                                          load_snapshot)

    if not agent_run_id:
        raise ToolBusinessError(
            "RUN_CONTEXT_UNRESOLVED",
            "trace 调查需要 agent_run_id(禁止回读可变 Incident 行)", retryable=False)
    run = run_repo.get_run(agent_run_id)
    if run is None or run.incident_id != incident_id:
        raise ToolBusinessError(
            "RUN_CONTEXT_UNRESOLVED",
            f"agent_run {agent_run_id} 缺失或不属于 incident {incident_id}",
            retryable=False)
    try:
        snap = load_snapshot(run)
    except (RunContextMissing, RunContextInvalid) as exc:
        raise ToolBusinessError("RUN_CONTEXT_UNRESOLVED", str(exc), retryable=False)
    return {
        "id": incident_id,
        "affected_service_ref": snap.affected_service_ref,
        "affected_operation_ref": snap.affected_operation_ref,
        "observed_at": snap.observed_at,
    }
