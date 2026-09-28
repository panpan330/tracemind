from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import Incident


def create_incident(title: str, description: str | None, severity: str,
                    service_ref: str, observed_at: datetime | None = None,
                    affected_service_ref: str | None = None,
                    affected_operation_ref: str | None = None) -> Incident:
    with Session(get_control_engine()) as session:
        inc = Incident(title=title, description=description, severity=severity,
                       service_ref=service_ref, observed_at=observed_at, status="created",
                       affected_service_ref=affected_service_ref,
                       affected_operation_ref=affected_operation_ref)
        session.add(inc)
        session.commit()
        session.refresh(inc)
        return inc


def save_current_health_snapshot(incident_id: int, snapshot: dict | None) -> None:
    """V2.1-C:保存手动路径的**当前快照**(非健康基线)。

    旧实现把创建时的实时 P95 写入 healthy_metrics_baseline —— 告警场景下那就是
    故障态值,不得充当健康基线;该列停止写入/读取(保留,不清洗存量)。"""
    with Session(get_control_engine()) as session:
        inc = session.get(Incident, incident_id)
        if inc is None:
            return
        inc.current_health_snapshot_json = snapshot
        session.commit()


def get_incident(incident_id: int) -> Incident | None:
    with Session(get_control_engine()) as session:
        return session.get(Incident, incident_id)


def list_incidents() -> list[Incident]:
    with Session(get_control_engine()) as session:
        return list(session.scalars(select(Incident).order_by(Incident.id.desc())).all())


def update_status(incident_id: int, status: str,
                  termination_reason: str | None = None) -> None:
    with Session(get_control_engine()) as session:
        inc = session.get(Incident, incident_id)
        if inc is None:
            return
        inc.status = status
        if termination_reason:
            inc.termination_reason = termination_reason
        session.commit()


def update_state(incident_id: int, *, status: str | None = None,
                 termination_reason: str | None = None,
                 degraded: bool | None = None,
                 degradation_reasons: list[str] | None = None) -> None:
    """V1.1 状态属性部分更新(termination_reason / degraded 等)。"""
    with Session(get_control_engine()) as session:
        inc = session.get(Incident, incident_id)
        if inc is None:
            return
        if status is not None:
            inc.status = status
        if termination_reason is not None:
            inc.termination_reason = termination_reason
        if degraded is not None:
            inc.degraded = degraded
        if degradation_reasons is not None:
            inc.degradation_reasons = ",".join(degradation_reasons)
        session.commit()
