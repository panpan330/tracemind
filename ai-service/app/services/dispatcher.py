"""V2.1-B:数据库租约 Dispatcher —— 领取 queued Run 并启动调查。

- 每轮领取前计算剩余容量:effective_max − runner 存活任务数
  (含 recover_pending_runs 恢复的任务);SQLite checkpointer 下 effective_max 强制 1;
- 领取(CAS,含过期 CLAIMED 回收)→ DISPATCHED CAS(status=queued、owner、租约校验)
  → start_investigation(快照驱动);
- 测试环境默认关闭(dispatch_enabled),显式开启的测试直接调 loop 函数;
- 应用关闭:lifespan cancel + await,防残留后台任务。
"""
import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone

from app.config import settings
from app.repositories import run_repo

logger = logging.getLogger(__name__)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _effective_max_concurrent() -> int:
    """SQLite checkpointer(单实例声明)下强制为 1;换共享 checkpointer 后放开。"""
    if settings.checkpoint_path.endswith(".sqlite"):
        return 1
    return max(1, settings.max_concurrent_runs)


def _owner() -> str:
    return f"dispatcher-{os.getpid()}-{uuid.uuid4().hex[:8]}"


async def dispatch_once() -> int:
    """领取并启动至多(剩余容量)个 queued Run;返回启动数。"""
    from app.services import runner

    capacity = _effective_max_concurrent() - runner.pending_task_count()
    started = 0
    owner = _owner()
    while capacity > 0:
        run = run_repo.claim_queued_run(owner, settings.dispatch_lease_seconds, _now())
        if run is None:
            break
        if not run_repo.mark_dispatched(run.id, owner, _now()):
            continue     # 租约过期/被回收:换下一个(不计容量)
        logger.info("dispatcher 领取 queued run %s (incident=%s)",
                    run.id, run.incident_id)
        try:
            await runner.start_investigation(run.incident_id, run.id, run.thread_id)
        except Exception:  # noqa: BLE001
            # V2.1-B closure:DISPATCHED 后、任务注册前失败 → 安全重试
            # (回退 queued/READY 清租约,下一轮重新领取)。失败同样消耗本 tick
            # 容量,避免 start 持续失败时 while 循环无限领取/回退。
            logger.exception("启动失败,回退 queued(run=%s)", run.id)
            run_repo.revert_dispatch(run.id, owner)
            capacity -= 1
            continue
        started += 1
        capacity -= 1
    return started


async def dispatch_loop() -> None:
    """常驻调度循环;由 lifespan 在 dispatch_enabled 时启动,关闭时 cancel+await。"""
    while True:
        try:
            if settings.dispatch_enabled:
                await dispatch_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 单轮失败不终止调度
            logger.exception("dispatch loop error")
        await asyncio.sleep(settings.dispatch_interval_seconds)
