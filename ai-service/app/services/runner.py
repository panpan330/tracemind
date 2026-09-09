"""LangGraph 执行管理:全局持久化 checkpointer + asyncio.Task 后台执行 + 启动恢复。

- start_investigation:创建后台任务跑图,thread_id 固定为 agent_run.thread_id。
- resume_investigation:审批/过期扫描用 Command(resume=...) 恢复。
- recover_pending_runs:启动时扫描未完成任务,从 checkpoint 继续。

checkpointer 使用同步 SqliteSaver(aiosqlite/AsyncSqliteSaver 在 Windows 测试环境
存在偶发死锁),图调用经 asyncio.to_thread 在线程池执行,避免阻塞事件循环。

V2.0-A:
- 初始上下文只来自 Run 创建事务冻结的 RunContextSnapshot,不从当前 Incident 行
  重新推导(快照缺失/不完整 → fail closed 转人工,不用默认 service/operation 补齐)。
- 版本冻结前移至 Run 创建事务;resume 时校验生效(旧实现收尾补写导致校验空操作)。
- checkpoint config 采用 LangGraph 文档契约 {"configurable": {"thread_id": ...}}
  (见 tests/test_checkpoint_contract.py 真实 SqliteSaver 集成测试)。

V2.0-A closure:
- 启动/恢复/重启恢复统一走 validate_run_for_resume:快照存在且合法、schema_version
  受支持、incident/agent_run/thread/namespace 与 Run 一致、Capability/Policy/Prompt/
  Tool 四类冻结版本与当前可执行版本一致;任一失败禁止恢复并转 needs_human。
"""
import asyncio
import logging
import os
import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from app.config import settings
from app.repositories import incident_repo, run_repo
from app.services.run_context import (ResumeBlocked, RunContextInvalid,
                                      RunContextMissing, load_snapshot,
                                      validate_run_for_resume)

logger = logging.getLogger(__name__)

_saver: SqliteSaver | None = None
_tasks: dict[int, asyncio.Task] = {}
_execution_gate: asyncio.Semaphore | None = None
_execution_gate_loop: asyncio.AbstractEventLoop | None = None


def _effective_max_concurrent() -> int:
    """SQLite checkpointer(单实例声明)下强制为 1;换共享 checkpointer 后放开。"""
    if settings.checkpoint_path.endswith(".sqlite"):
        return 1
    return max(1, settings.max_concurrent_runs)


def execution_gate() -> asyncio.Semaphore:
    """统一图执行门:start_investigation/resume/recover 的真实 graph.invoke
    均经此门串行化(max_concurrent_runs 覆盖全部执行路径,不只 Dispatcher)。
    信号量按运行事件循环懒创建并缓存(生产为单一常驻循环;测试每用例
    新循环时自动重建,避免跨循环绑定错误)。"""
    global _execution_gate, _execution_gate_loop
    loop = asyncio.get_running_loop()
    if _execution_gate is None or _execution_gate_loop is not loop:
        _execution_gate = asyncio.Semaphore(_effective_max_concurrent())
        _execution_gate_loop = loop
    return _execution_gate


def pending_task_count() -> int:
    """Dispatcher 容量计算:当前存活图任务数(含恢复启动的任务)。"""
    return len(_tasks)


def pending_task_count() -> int:
    """Dispatcher 容量计算:当前存活图任务数(含恢复启动的任务)。"""
    return len(_tasks)


def get_saver() -> SqliteSaver:
    global _saver
    if _saver is None:
        path = settings.checkpoint_path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        _saver = SqliteSaver(sqlite3.connect(path, check_same_thread=False))
    return _saver


def _graph_config(thread_id: str) -> dict:
    """LangGraph 1.x 文档契约:thread_id 必须位于 configurable 层级。"""
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 100}


from app.replay.versions import POLICY_BUNDLE_VERSION
from app.replay.writer import ReplayWriter


def _current_bundle_versions() -> dict:
    """当前可执行的四类 bundle 版本(运行时读模块属性,便于测试注入不匹配值)。"""
    import app.replay.versions as _versions
    from app.mcp.contract import MCP_TOOL_CONTRACT_VERSION
    return {
        "policy": _versions.POLICY_BUNDLE_VERSION,
        "capability": _versions.CAPABILITY_BUNDLE_VERSION,
        "prompt": _versions.PROMPT_BUNDLE_VERSION,
        "tool": MCP_TOOL_CONTRACT_VERSION,
    }


def _initial_state_from_run(run) -> dict:
    """从冻结快照构建初始状态(恢复与启动共用;禁止从 Incident 行重推导)。"""
    snap = load_snapshot(run)
    return {
        "incident_id": snap.incident_id,
        "run_id": snap.agent_run_id,
        "thread_id": getattr(run, "thread_id", snap.checkpoint.get("thread_id")),
        "severity": snap.severity,
        "service_ref": snap.service_ref,
        "affected_operation_ref": snap.affected_operation_ref,
        # V2.0-B closure:基线引用随 RunContext 冻结(评估/恢复不回读可变 Incident)
        "healthy_baseline_ref": snap.healthy_baseline_ref,
        "baseline_ref": snap.baseline_ref,
    }


def _fail_closed(run, reason: str, detail: str) -> None:
    """fail closed:短码入库存 termination_reason(列宽 64),详情只进日志。"""
    logger.error("run %s fail closed: %s (%s)", getattr(run, "id", "?"), reason, detail)
    run_repo.update_run_status(run.id, "failed")
    incident_repo.update_status(run.incident_id, "needs_human",
                                termination_reason=reason[:64])


def _finalize_run(incident_id: int, run_id: int, status: str,
                  termination_reason: str | None = None) -> None:
    """Run 收尾:补写版本(不覆盖已冻结值) + 写 RUN_TERMINATED 回放步骤。
    只在终态写入(审批挂起/执行中等中间状态不写,避免重复终止)。"""
    terminal = {"recovered", "failed", "needs_human", "rejected", "cancelled"}
    try:
        run_repo.freeze_run_versions(run_id, POLICY_BUNDLE_VERSION)
        if status not in terminal:
            return
        writer = ReplayWriter(incident_id, run_id)
        lid = f"ls-term-{run_id}"
        outcome = ("succeeded" if status == "recovered"
                   else "rejected" if status == "rejected"
                   else "needs_human" if status == "needs_human"
                   else "failed")
        writer.write("RUN_TERMINATED", "completed", logical_step_id=lid,
                     step_outcome=outcome,
                     source_refs={"businessKey": f"terminated:{run_id}"},
                     decision={"runStatus": status,
                               "terminationReason": termination_reason})
    except Exception:
        logger.exception("finalize_run failed incident=%s run=%s", incident_id, run_id)


async def _run_graph(incident_id: int, run_id: int, thread_id: str, initial: dict) -> None:
    from app.agent.graph import build_graph
    graph = build_graph(checkpointer=get_saver())
    try:
        async with execution_gate():
            result = await asyncio.to_thread(
                graph.invoke,
                initial,
                _graph_config(thread_id),
            )
    except Exception:
        logger.exception("graph run failed incident=%s run=%s", incident_id, run_id)
        run_repo.update_run_status(run_id, "failed")
        incident_repo.update_status(incident_id, "failed")
        return
    status = result.get("status") or "finished"
    run_repo.update_run_status(run_id, status)
    incident_repo.update_status(incident_id, status)
    _finalize_run(incident_id, run_id, status, result.get("termination_reason"))
    logger.info("graph finished incident=%s run=%s status=%s", incident_id, run_id, status)


async def start_investigation(incident_id: int, run_id: int, thread_id: str) -> None:
    run_repo.update_run_status(run_id, "investigating")
    incident_repo.update_status(incident_id, "investigating")
    run = run_repo.get_run(run_id)
    try:
        validate_run_for_resume(run, current_bundle_versions=_current_bundle_versions())
        initial = _initial_state_from_run(run)
        initial["status"] = "created"
    except (ResumeBlocked, RunContextMissing, RunContextInvalid) as exc:
        reason = getattr(exc, "reason", "context_snapshot_invalid")
        _fail_closed(run, reason, str(exc))
        return
    task = asyncio.create_task(_run_graph(incident_id, run_id, thread_id, initial))
    _tasks[run_id] = task
    task.add_done_callback(lambda _t: _tasks.pop(run_id, None))


async def resume_investigation(thread_id: str, resume_value: dict) -> None:
    """用同一 thread_id 恢复挂起的图(interrupt 处继续)。
    V2.0-A closure:恢复前统一校验(快照/绑定/四类冻结版本),任一失败禁止恢复、
    标记明确原因并转 needs_human,不调用图。"""
    from app.agent.graph import build_graph

    run = run_repo.get_run_by_thread(thread_id)
    if run is None:
        logger.error("resume_investigation: thread=%s 无对应 Run(fail closed)", thread_id)
        return
    try:
        validate_run_for_resume(run, current_bundle_versions=_current_bundle_versions())
    except (ResumeBlocked, RunContextMissing, RunContextInvalid) as exc:
        reason = getattr(exc, "reason", "context_snapshot_invalid")
        run_repo.update_run_status(run.id, "failed")
        incident_repo.update_status(run.incident_id, "needs_human",
                                    termination_reason=reason[:64])
        logger.warning("run %s 恢复被拒绝(%s): %s", run.id, reason, exc)
        return
    graph = build_graph(checkpointer=get_saver())
    async with execution_gate():
        result = await asyncio.to_thread(
            graph.invoke,
            Command(resume=resume_value),
            _graph_config(thread_id),
        )
    run = run_repo.get_run_by_thread(thread_id)
    if run is not None:
        status = result.get("status") or "finished"
        run_repo.update_run_status(run.id, status)
        incident_repo.update_status(run.incident_id, status)
        _finalize_run(run.incident_id, run.id, status, result.get("termination_reason"))
        logger.info("graph resumed thread=%s status=%s", thread_id, status)


async def recover_pending_runs() -> None:
    """启动时从 checkpoint 恢复未完成任务(interrupt 处重新挂起等待审批)。
    V2.0-A closure:初始上下文只来自冻结快照并统一校验;缺失/不完整/版本不匹配
    fail closed,不猜测、不补默认。"""
    pending = run_repo.list_pending_runs()
    for run in pending:
        try:
            validate_run_for_resume(run, current_bundle_versions=_current_bundle_versions())
            initial = _initial_state_from_run(run)
            initial["status"] = run.status
        except (ResumeBlocked, RunContextMissing, RunContextInvalid) as exc:
            reason = getattr(exc, "reason", "context_snapshot_invalid")
            _fail_closed(run, reason, str(exc))
            continue
        task = asyncio.create_task(
            _run_graph(run.incident_id, run.id, run.thread_id, initial))
        _tasks[run.id] = task
        task.add_done_callback(lambda _t, rid=run.id: _tasks.pop(rid, None))
    if pending:
        logger.info("recovered %d pending run(s)", len(pending))
