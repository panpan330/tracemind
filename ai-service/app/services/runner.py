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
"""
import asyncio
import logging
import os
import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from app.config import settings
from app.repositories import incident_repo, run_repo
from app.services.run_context import RunContextInvalid, RunContextMissing, load_snapshot

logger = logging.getLogger(__name__)

_saver: SqliteSaver | None = None
_tasks: dict[int, asyncio.Task] = {}


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
    }


def _fail_closed(run, detail: str) -> None:
    """fail closed:短码入库存 termination_reason(列宽 64),详情只进日志。"""
    logger.error("run %s fail closed: %s", getattr(run, "id", "?"), detail)
    run_repo.update_run_status(run.id, "failed")
    incident_repo.update_status(run.incident_id, "needs_human",
                                termination_reason="context_snapshot_invalid")


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
        initial = _initial_state_from_run(run)
        initial["status"] = "created"
    except (RunContextMissing, RunContextInvalid) as exc:
        _fail_closed(run, str(exc))
        return
    task = asyncio.create_task(_run_graph(incident_id, run_id, thread_id, initial))
    _tasks[run_id] = task
    task.add_done_callback(lambda _t: _tasks.pop(run_id, None))


async def resume_investigation(thread_id: str, resume_value: dict) -> None:
    """用同一 thread_id 恢复挂起的图(interrupt 处继续)。
    V1.5:恢复前校验版本,不一致(部署新版本后恢复旧 Run)停止原 Run 进入 version_mismatch。
    V2.0-A:expected_policy_bundle_version 在 Run 创建事务冻结,校验对未完成 Run 真正生效。"""
    from app.agent.graph import build_graph
    from app.replay.versions import POLICY_BUNDLE_VERSION

    run = run_repo.get_run_by_thread(thread_id)
    if run is not None and run.expected_policy_bundle_version \
            and run.expected_policy_bundle_version != POLICY_BUNDLE_VERSION:
        run_repo.update_run_status(run.id, "failed")
        incident_repo.update_status(run.incident_id, "needs_human",
                                    termination_reason="version_mismatch")
        logger.warning("run %s 版本不匹配(expected=%s, current=%s) → version_mismatch",
                       run.id, run.expected_policy_bundle_version, POLICY_BUNDLE_VERSION)
        return
    graph = build_graph(checkpointer=get_saver())
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
    V2.0-A:初始上下文只来自冻结快照;缺失/不完整 fail closed,不猜测、不补默认。"""
    pending = run_repo.list_pending_runs()
    for run in pending:
        try:
            initial = _initial_state_from_run(run)
            initial["status"] = run.status
        except (RunContextMissing, RunContextInvalid) as exc:
            _fail_closed(run, str(exc))
            continue
        task = asyncio.create_task(
            _run_graph(run.incident_id, run.id, run.thread_id, initial))
        _tasks[run.id] = task
        task.add_done_callback(lambda _t: _tasks.pop(run.id, None))
    if pending:
        logger.info("recovered %d pending run(s)", len(pending))
