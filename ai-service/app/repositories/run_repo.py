import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import AgentRun


def create_run(incident_id: int, baseline: dict | None = None) -> AgentRun:
    with Session(get_control_engine()) as session:
        run = AgentRun(incident_id=incident_id, thread_id=f"run-{uuid.uuid4()}",
                       status="created", incident_digest_baseline=baseline)
        session.add(run)
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
    """Run 启动/收尾时冻结预期版本。"""
    with Session(get_control_engine()) as session:
        run = session.get(AgentRun, agent_run_id)
        if run is None:
            return
        run.expected_policy_bundle_version = policy_bundle_version
        session.commit()
