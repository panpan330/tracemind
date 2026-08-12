"""LangGraph 执行管理:全局持久化 checkpointer + asyncio.Task 后台执行 + 启动恢复。

- start_investigation:创建后台任务跑图,thread_id 固定为 agent_run.thread_id。
- resume_investigation:审批/过期扫描用 Command(resume=...) 恢复。
- recover_pending_runs:启动时扫描未完成任务,从 checkpoint 继续。

checkpointer 使用同步 SqliteSaver(aiosqlite/AsyncSqliteSaver 在 Windows 测试环境
存在偶发死锁),图调用经 asyncio.to_thread 在线程池执行,避免阻塞事件循环。
"""
import asyncio
import logging
import os
import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from app.config import settings
from app.repositories import incident_repo, run_repo

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


from app.replay.versions import POLICY_BUNDLE_VERSION
from app.replay.writer import ReplayWriter


def _finalize_run(incident_id: int, run_id: int, status: str,
                  termination_reason: str | None = None) -> None:
    """Run 收尾:冻结版本 + 写 RUN_TERMINATED 回放步骤。"""
    try:
        run_repo.freeze_run_versions(run_id, POLICY_BUNDLE_VERSION)
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
            {"thread_id": thread_id, "recursion_limit": 100},
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
    inc = incident_repo.get_incident(incident_id)
    initial = {
        "incident_id": incident_id,
        "run_id": run_id,
        "thread_id": thread_id,
        "severity": inc.severity if inc else "medium",
        "service_ref": inc.service_ref if inc else "inventory-service",
        "status": "created",
    }
    task = asyncio.create_task(_run_graph(incident_id, run_id, thread_id, initial))
    _tasks[run_id] = task
    task.add_done_callback(lambda _t: _tasks.pop(run_id, None))


async def resume_investigation(thread_id: str, resume_value: dict) -> None:
    """用同一 thread_id 恢复挂起的图(interrupt 处继续)。
    V1.5:恢复前校验版本,不一致(部署新版本后恢复旧 Run)停止原 Run 进入 version_mismatch。"""
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
        {"thread_id": thread_id, "recursion_limit": 100},
    )
    run = run_repo.get_run_by_thread(thread_id)
    if run is not None:
        status = result.get("status") or "finished"
        run_repo.update_run_status(run.id, status)
        incident_repo.update_status(run.incident_id, status)
        _finalize_run(run.incident_id, run.id, status, result.get("termination_reason"))
        logger.info("graph resumed thread=%s status=%s", thread_id, status)


async def recover_pending_runs() -> None:
    """启动时从 checkpoint 恢复未完成任务(interrupt 处重新挂起等待审批)。"""
    pending = run_repo.list_pending_runs()
    for run in pending:
        inc = incident_repo.get_incident(run.incident_id)
        initial = {
            "incident_id": run.incident_id,
            "run_id": run.id,
            "thread_id": run.thread_id,
            "severity": inc.severity if inc else "medium",
            "service_ref": inc.service_ref if inc else "inventory-service",
            "status": run.status,
        }
        task = asyncio.create_task(
            _run_graph(run.incident_id, run.id, run.thread_id, initial))
        _tasks[run.id] = task
        task.add_done_callback(lambda _t: _tasks.pop(run.id, None))
    if pending:
        logger.info("recovered %d pending run(s)", len(pending))
