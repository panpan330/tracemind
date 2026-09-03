import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import AgentRun, Incident


def create_run(incident_id: int, baseline: dict | None = None) -> AgentRun:
    """创建 Run 并在同一事务内冻结 RunContextSnapshot 与 bundle 版本(V2.0-A 第 6/8 条)。

    - checkpoint_thread_id 与 thread_id 一一对应(唯一约束 009),namespace 空串 = 默认。
    - 版本冻结前移至创建事务:开始执行后不得改变(_finalize_run 不再覆盖)。
    - V2.0-A closure:始终在本事务内 SELECT ... FOR UPDATE 重读 Incident(调用方传入的
      detached 对象不作为冻结来源),消除"读取 Incident → 创建 Run"窗口期内的行变化。
    - incident 行缺失或 service_ref 缺失 → ValueError(fail closed,禁止默认上下文)。
    """
    from app.mcp.contract import MCP_TOOL_CONTRACT_VERSION
    from app.replay.versions import (CAPABILITY_BUNDLE_VERSION, POLICY_BUNDLE_VERSION,
                                     PROMPT_BUNDLE_VERSION)
    from app.services.run_context import build_snapshot

    with Session(get_control_engine()) as session:
        # FOR UPDATE:冻结期间锁 Incident 行,并发更新在 Run 创建提交后才能进行
        inc = session.scalars(
            select(Incident).where(Incident.id == incident_id).with_for_update()).first()
        if inc is None:
            raise ValueError(f"incident {incident_id} not found(禁止无上下文创建 Run)")
        thread_id = f"run-{uuid.uuid4()}"
        run = AgentRun(
            incident_id=incident_id, thread_id=thread_id, status="created",
            incident_digest_baseline=baseline,
            checkpoint_thread_id=thread_id, checkpoint_namespace="",
            expected_policy_bundle_version=POLICY_BUNDLE_VERSION,
            capability_bundle_version=CAPABILITY_BUNDLE_VERSION,
            prompt_bundle_version=PROMPT_BUNDLE_VERSION,
            tool_bundle_version=MCP_TOOL_CONTRACT_VERSION,
        )
        session.add(run)
        session.flush()  # 取 run.id,与快照写入同一事务
        run.run_context_snapshot_json = build_snapshot(
            inc, run.id, thread_id, baseline,
            bundle_versions={
                "capability": CAPABILITY_BUNDLE_VERSION,
                "policy": POLICY_BUNDLE_VERSION,
                "prompt": PROMPT_BUNDLE_VERSION,
                "tool": MCP_TOOL_CONTRACT_VERSION,
            })
        session.commit()
        session.refresh(run)
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


def update_run_status(run_id: int, status: str) -> None:
    from app.db.models import utcnow
    terminal = {"recovered", "failed", "needs_human", "rejected"}
    with Session(get_control_engine()) as session:
        run = session.get(AgentRun, run_id)
        if run is None:
            return
        run.status = status
        if status in terminal:
            run.finished_at = utcnow()
        session.commit()


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
