"""V2.1-B:数据库租约 Dispatcher —— 领取 queued Run 并启动调查。

- 每轮领取前计算剩余容量:runner.max_concurrent_runs() − runner 存活任务数
  (含 recover_pending_runs 恢复的任务);上限唯一来源在 runner(按实际
  checkpointer 类型判定,当前 SqliteSaver 恒为 1);
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


def _owner() -> str:
    return f"dispatcher-{os.getpid()}-{uuid.uuid4().hex[:8]}"


async def dispatch_once() -> int:
    """领取并启动至多(剩余容量)个 queued Run;返回启动数。

    V2.1-C 两段式:alertmanager Run 在启动图前先做**事务外**基线采集
    (Prometheus 历史窗口 + digest 快照),再以**短 CAS 事务**单次封存并调度;
    采集异常/CAS 失败都不启动图,消耗本轮容量,Run 留待重领(重试条件=封存 CAS)。
    容量上限取 runner.max_concurrent_runs()(唯一来源:按实际 checkpointer 类型
    判定,当前 SqliteSaver 恒为 1),与图执行门同源。"""
    from app.services import baseline_capture, runner

    capacity = runner.max_concurrent_runs() - runner.pending_task_count()
    started = 0
    owner = _owner()
    while capacity > 0:
        run = run_repo.claim_queued_run(owner, settings.dispatch_lease_seconds, _now())
        if run is None:
            break
        if run.trigger_source == "alertmanager":
            already_sealed = run.baseline_capture_status in ("OK", "INSUFFICIENT")
            if already_sealed:
                # 此前 DISPATCHED 后启动失败回退、图从未启动:复用已封存基线,
                # 不重采、不重写(封存不可改写)
                capture = None
            else:
                try:
                    capture = baseline_capture.capture_run_baselines(run)  # 事务外
                except Exception:  # noqa: BLE001 采集层兜底失效也不阻断调度循环
                    logger.exception("run %s 基线采集异常,按 CAPTURE_FAILED 处理",
                                     run.id)
                    run_repo.record_capture_failed(run.id, owner, _now())
                    capacity -= 1
                    continue
                if capture.status == baseline_capture.STATUS_CAPTURE_FAILED:
                    logger.warning("run %s 基线采集失败,回退 READY 待重领", run.id)
                    run_repo.record_capture_failed(run.id, owner, _now())
                    capacity -= 1
                    continue
            if not run_repo.seal_run_baselines_and_dispatch(run.id, owner, _now(),
                                                            capture=capture):
                logger.warning("run %s 基线封存 CAS 失败(租约/状态漂移),不启动", run.id)
                capacity -= 1
                continue
            logger.info("dispatcher 封存基线并调度 run %s (incident=%s, quality=%s)",
                        run.id, run.incident_id,
                        capture.status if capture else "REUSED_SEALED")
        else:
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
