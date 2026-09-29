import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import AgentRun, Incident


def create_run(incident_id: int, baseline: dict | None = None,
               *, trigger_source: str = "manual", status: str = "created",
               active_run_key: str | None = None) -> AgentRun:
    """创建 Run 并在同一事务内冻结 RunContextSnapshot 与 bundle 版本(V2.0-A 第 6/8 条)。

    - checkpoint_thread_id 与 thread_id 一一对应(唯一约束 009),namespace 空串 = 默认。
    - 版本冻结前移至创建事务:开始执行后不得改变(_finalize_run 不再覆盖)。
    - V2.0-A closure:始终在本事务内 SELECT ... FOR UPDATE 重读 Incident(调用方传入的
      detached 对象不作为冻结来源),消除"读取 Incident → 创建 Run"窗口期内的行变化。
    - V2.1-B:trigger_source/active_run_key/dispatch_status 支持(手动=DISPATCHED;
      alertmanager queued=READY);实际插入由 _insert_run_in_session 完成(可复用调用方事务)。
    - incident 行缺失或 service_ref 缺失 → ValueError(fail closed,禁止默认上下文)。
    """
    with Session(get_control_engine()) as session:
        # FOR UPDATE:冻结期间锁 Incident 行,并发更新在 Run 创建提交后才能进行
        inc = session.scalars(
            select(Incident).where(Incident.id == incident_id).with_for_update()).first()
        if inc is None:
            raise ValueError(f"incident {incident_id} not found(禁止无上下文创建 Run)")
        run = _insert_run_in_session(
            session, inc, baseline=baseline, trigger_source=trigger_source,
            status=status, active_run_key=active_run_key)
        session.commit()
        session.refresh(run)
        return run


def _insert_run_in_session(session, incident: Incident, *, baseline: dict | None,
                           trigger_source: str, status: str,
                           active_run_key: str | None) -> AgentRun:
    """V2.1-B:在调用方事务内插入 Run 并冻结快照(不自行提交、不关闭 Session)。
    聚合事务(事件/投影/Incident/关联/Run)复用本方法保证原子性。"""
    from app.mcp.contract import MCP_TOOL_CONTRACT_VERSION
    from app.replay.versions import (CAPABILITY_BUNDLE_VERSION, POLICY_BUNDLE_VERSION,
                                     PROMPT_BUNDLE_VERSION)
    from app.services.run_context import build_snapshot

    thread_id = f"run-{uuid.uuid4()}"
    run = AgentRun(
        incident_id=incident.id, thread_id=thread_id, status=status,
        incident_digest_baseline=baseline,
        checkpoint_thread_id=thread_id, checkpoint_namespace="",
        expected_policy_bundle_version=POLICY_BUNDLE_VERSION,
        capability_bundle_version=CAPABILITY_BUNDLE_VERSION,
        prompt_bundle_version=PROMPT_BUNDLE_VERSION,
        tool_bundle_version=MCP_TOOL_CONTRACT_VERSION,
        trigger_source=trigger_source,
        active_run_key=active_run_key,
        dispatch_status="READY" if status == "queued" else "DISPATCHED",
    )
    session.add(run)
    session.flush()  # 取 run.id,与快照写入同一事务
    run.run_context_snapshot_json = build_snapshot(
        incident, run.id, thread_id, baseline,
        bundle_versions={
            "capability": CAPABILITY_BUNDLE_VERSION,
            "policy": POLICY_BUNDLE_VERSION,
            "prompt": PROMPT_BUNDLE_VERSION,
            "tool": MCP_TOOL_CONTRACT_VERSION,
        })
    return run


def get_run(run_id: int) -> AgentRun | None:
    with Session(get_control_engine()) as session:
        return session.get(AgentRun, run_id)


def get_run_by_thread(thread_id: str) -> AgentRun | None:
    with Session(get_control_engine()) as session:
        return session.scalars(
            select(AgentRun).filter(AgentRun.thread_id == thread_id).limit(1)).first()


def list_runs(incident_id: int) -> list[AgentRun]:
    with Session(get_control_engine()) as session:
        return list(session.scalars(
            select(AgentRun)
            .filter(AgentRun.incident_id == incident_id)
            .order_by(AgentRun.id.desc())).all())


TERMINAL_STATUSES = frozenset(
    {"recovered", "failed", "needs_human", "rejected", "cancelled",
     "self_recovered"})   # V2.1-C:告警 resolved 且指标已恢复、未执行写动作


def update_run_status(run_id: int, status: str) -> None:
    """运行状态更新;进入终态即释放 active_run_key 与租约(V2.1-B 集中清键,
    仅对能按 run_id 准确绑定的路径生效;审批绑定损坏不猜测清键)。

    V2.1-C:终态**提交后**以独立短事务尝试关闭 episode(T4,锁序与网关一致:
    incident_alert → incident,agent_run 只读)——覆盖"Run 终态 + 告警已 resolved"
    的组合;关闭失败不影响已提交终态(仅记日志,下一次终态/复核会收敛)。"""
    import logging

    from app.db.models import utcnow
    logger = logging.getLogger(__name__)
    incident_id = None
    with Session(get_control_engine()) as session:
        run = session.get(AgentRun, run_id)
        if run is None:
            return
        incident_id = run.incident_id
        run.status = status
        if status in TERMINAL_STATUSES:
            run.finished_at = utcnow()
            run.active_run_key = None
            run.lease_owner = None
            run.lease_until = None
        session.commit()
    if status in TERMINAL_STATUSES and incident_id is not None:
        from app.services.incident_lifecycle import close_incident_if_resolved
        try:
            close_incident_if_resolved(incident_id)
        except Exception:  # noqa: BLE001 关闭尝试失败不回滚已提交终态
            logger.exception("episode 关闭尝试失败 incident=%s", incident_id)


def list_pending_runs() -> list[AgentRun]:
    """未完成任务:进行中的 run(审批挂起视为未完成,由恢复流程重新挂起)。"""
    with Session(get_control_engine()) as session:
        return list(session.scalars(
            select(AgentRun)
            .filter(AgentRun.status.in_(("investigating", "executing", "verifying")))
            .order_by(AgentRun.id.asc())).all())


def allocate_replay_sequence(agent_run_id: int,
                             session: Session | None = None) -> int:
    """原子分配 replay sequence_no。
    传入 session 时与调用方共用同一事务(序号分配+记录插入必须同事务);
    否则自行 open 会话。"""
    owns = session is None
    s = session or Session(get_control_engine())
    try:
        r = s.get(AgentRun, agent_run_id)
        if r is None:
            raise ValueError("agent_run not found")
        r.next_replay_sequence += 1
        if owns:
            s.commit()
        return r.next_replay_sequence
    finally:
        if owns:
            s.close()


def freeze_run_versions(agent_run_id: int, policy_bundle_version: str) -> None:
    """V2.0-A:版本冻结已在 Run 创建事务完成。此处仅在字段为空时补写(旧数据防御),
    绝不覆盖已冻结值——运行中部署升级不得篡改 Run 的版本证据。"""
    with Session(get_control_engine()) as session:
        run = session.get(AgentRun, agent_run_id)
        if run is None:
            return
        if run.expected_policy_bundle_version is None:
            run.expected_policy_bundle_version = policy_bundle_version
        session.commit()


def claim_queued_run(owner: str, lease_seconds: int, now) -> AgentRun | None:
    """V2.1-B:CAS 领取一个 queued Run(租约)。
    READY 可领;过期 CLAIMED 可重领;未过期 CLAIMED/DISPATCHED 不可领。"""
    from datetime import timedelta

    from sqlalchemy import text as _text

    session = Session(get_control_engine(), expire_on_commit=False)
    try:
        with session.begin():
            row = session.execute(_text(
                "SELECT id FROM agent_run WHERE status='queued' "
                "AND dispatch_status IN ('READY','CLAIMED') "
                "AND (lease_until IS NULL OR lease_until < :now) "
                "ORDER BY id LIMIT 1 FOR UPDATE"), {"now": now}).scalar()
            if row is None:
                return None
            res = session.execute(_text(
                "UPDATE agent_run SET dispatch_status='CLAIMED', lease_owner=:o, "
                "lease_until=:lu, dispatch_attempts=dispatch_attempts+1 "
                "WHERE id=:id AND status='queued' "
                "AND dispatch_status IN ('READY','CLAIMED') "
                "AND (lease_until IS NULL OR lease_until < :now)"),
                {"o": owner, "lu": now + timedelta(seconds=lease_seconds),
                 "id": row, "now": now})
            if res.rowcount != 1:
                return None
            return session.get(AgentRun, row)
    finally:
        session.close()


def mark_dispatched(run_id: int, owner: str, now) -> bool:
    """V2.1-B:CLAIMED → DISPATCHED CAS(status=queued + owner 匹配 + 租约未过期)。
    租约过期后原 owner 不得启动图。"""
    from datetime import timedelta

    from sqlalchemy import text as _text

    with Session(get_control_engine()) as session:
        with session.begin():
            res = session.execute(_text(
                "UPDATE agent_run SET dispatch_status='DISPATCHED', status='investigating' "
                "WHERE id=:id AND status='queued' AND dispatch_status='CLAIMED' "
                "AND lease_owner=:o AND lease_until >= :now"),
                {"id": run_id, "o": owner, "now": now})
            return res.rowcount == 1


def revert_dispatch(run_id: int, owner: str) -> None:
    """V2.1-B closure:DISPATCHED 后启动失败 → 回退 queued/READY 并清租约,
    下一轮 Dispatcher 可安全重试(仅原 owner 可回退)。"""
    from datetime import timedelta

    from sqlalchemy import text as _text

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with Session(get_control_engine()) as session:
        with session.begin():
            session.execute(_text(
                "UPDATE agent_run SET dispatch_status='READY', status='queued', "
                "lease_owner=NULL, lease_until=NULL "
                "WHERE id=:id AND dispatch_status='DISPATCHED' AND lease_owner=:o "
                "AND lease_until >= :now"),
                {"id": run_id, "o": owner, "now": now - timedelta(seconds=1)})


def record_capture_failed(run_id: int, owner: str, now) -> bool:
    """V2.1-C:基线采集异常 → 记 CAPTURE_FAILED(未封存)并回退 CLAIMED→READY 清租约,
    下一轮按同一封存 CAS 重试。仅原 owner 可操作。"""
    from sqlalchemy import text as _text

    with Session(get_control_engine()) as session:
        with session.begin():
            res = session.execute(_text(
                "UPDATE agent_run SET baseline_capture_status='CAPTURE_FAILED', "
                "dispatch_status='READY', status='queued', "
                "lease_owner=NULL, lease_until=NULL "
                "WHERE id=:id AND dispatch_status='CLAIMED' AND lease_owner=:o "
                "AND status='queued'"),
                {"id": run_id, "o": owner})
            return res.rowcount == 1


def seal_run_baselines_and_dispatch(run_id: int, owner: str, now,
                                    *, capture: "BaselineCapture | None") -> bool:
    """V2.1-C 阶段 2:启动图前单次封存基线并调度(短 CAS 事务,单次往返)。

    事务内步骤(锁顺序与网关一致:先 incident 后 agent_run):
    ① SELECT incident ... FOR UPDATE(行锁,防与聚合事务交叠);
    ② UPDATE agent_run:原子校验(status=queued ∧ dispatch_status=CLAIMED ∧
       lease_owner=:owner ∧ lease_until>=:now ∧ 未封存)→ 写基线列 + 快照基线字段 +
       baseline_capture_status + dispatch_status='DISPATCHED' + status='investigating';
    ③ UPDATE incident:baseline_window_*/baseline_metrics_json/baseline_quality +
       auto_run_started_at(仅首次)。
    校验失败 → 整体回滚返回 False(调用方不启动图);外部采集必须在调用前完成
    (见 services/baseline_capture,capture 为其结果)。

    封存终值:OK / INSUFFICIENT(单次封存,不重采);
    CAPTURE_FAILED 未封存,可按同一 CAS 重试(重试条件 ≡ 封存条件)。

    capture=None:该 Run 已封存(此前 DISPATCHED 后启动失败回退、图从未启动)——
    **复用已封存基线,仅重新调度**,不重采、不重写(封存不可被改写)。
    """
    from sqlalchemy import JSON, bindparam
    from sqlalchemy import text as _text

    from app.services.baseline_capture import (STATUS_INSUFFICIENT, STATUS_OK,
                                               BaselineCapture)

    if capture is not None and (not isinstance(capture, BaselineCapture)
                                or capture.status not in (STATUS_OK,
                                                          STATUS_INSUFFICIENT)):
        return False
    with Session(get_control_engine()) as session:
        with session.begin():
            run = session.execute(_text(
                "SELECT incident_id, run_context_snapshot_json, "
                "baseline_capture_status FROM agent_run "
                "WHERE id=:id FOR UPDATE"), {"id": run_id}).fetchone()
            if run is None:
                return False
            incident_id = run.incident_id
            already_sealed = run.baseline_capture_status in (STATUS_OK,
                                                             STATUS_INSUFFICIENT)
            if capture is None and not already_sealed:
                return False          # 未封存却要求复用 → 调用方顺序错误
            session.execute(_text(
                "SELECT id FROM incident WHERE id=:iid FOR UPDATE"),
                {"iid": incident_id})
            import json as _json
            if already_sealed:
                snap = None           # 已封存:不读不改快照
            else:
                raw = run.run_context_snapshot_json
                snap = raw if isinstance(raw, dict) else (
                    _json.loads(raw) if isinstance(raw, str) else None)
                if not isinstance(snap, dict):
                    return False      # 快照缺失 fail closed,不调度
            if already_sealed:
                # 复用已封存基线:仅重做"领取→调度"CAS,不写任何基线字段
                res = session.execute(_text(
                    "UPDATE agent_run SET dispatch_status='DISPATCHED', "
                    "status='investigating' "
                    "WHERE id=:id AND status='queued' AND dispatch_status='CLAIMED' "
                    "AND lease_owner=:owner AND lease_until >= :now"),
                    {"id": run_id, "owner": owner, "now": now})
                if res.rowcount != 1:
                    return False
                session.execute(_text(
                    "UPDATE incident SET "
                    "auto_run_started_at=COALESCE(auto_run_started_at, :now) "
                    "WHERE id=:iid"), {"now": now, "iid": incident_id})
                return True
            healthy = (capture.healthy_metrics
                       if capture is not None and capture.status == STATUS_OK
                       else None)
            snap = {**snap,
                    "baseline_ref": capture.digest_baseline,
                    "healthy_baseline_ref": healthy,
                    "baseline_quality": capture.status,
                    "baseline_window": {
                        "start": capture.window_start.isoformat()
                        if capture.window_start else None,
                        "end": capture.window_end.isoformat()
                        if capture.window_end else None}}
            seal_stmt = _text(
                "UPDATE agent_run SET incident_digest_baseline=:digest, "
                "baseline_capture_status=:cs, run_context_snapshot_json=:snap, "
                "dispatch_status='DISPATCHED', status='investigating' "
                "WHERE id=:id AND status='queued' AND dispatch_status='CLAIMED' "
                "AND lease_owner=:owner AND lease_until >= :now "
                "AND (baseline_capture_status IS NULL "
                "OR baseline_capture_status='CAPTURE_FAILED')").bindparams(
                bindparam("digest", type_=JSON), bindparam("snap", type_=JSON))
            res = session.execute(seal_stmt,
                {"digest": capture.digest_baseline, "cs": capture.status,
                 "snap": snap, "id": run_id, "owner": owner, "now": now})
            if res.rowcount != 1:
                return False
            if healthy is None:
                # INSUFFICIENT:不写健康值(SQL NULL,不伪造基线)
                session.execute(_text(
                    "UPDATE incident SET baseline_window_start=:ws, "
                    "baseline_window_end=:we, baseline_metrics_json=NULL, "
                    "baseline_quality=:q, "
                    "auto_run_started_at=COALESCE(auto_run_started_at, :now) "
                    "WHERE id=:iid"),
                    {"ws": capture.window_start, "we": capture.window_end,
                     "q": capture.status, "now": now, "iid": incident_id})
            else:
                incident_stmt = _text(
                    "UPDATE incident SET baseline_window_start=:ws, "
                    "baseline_window_end=:we, baseline_metrics_json=:metrics, "
                    "baseline_quality=:q, "
                    "auto_run_started_at=COALESCE(auto_run_started_at, :now) "
                    "WHERE id=:iid").bindparams(bindparam("metrics", type_=JSON))
                session.execute(incident_stmt,
                    {"ws": capture.window_start, "we": capture.window_end,
                     "metrics": healthy, "q": capture.status,
                     "now": now, "iid": incident_id})
            return True
