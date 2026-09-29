from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import IncidentEvent


def _next_sequence(session: Session, incident_id: int) -> int:
    """下一事件序号(**锁定读**:当前读 + 对最新行加 next-key 锁)。

    必须用锁定读而不能用普通一致性读:并发聚合事务写同一 Incident 的生命周期
    事件时,一致性读会看到旧快照 → 两个事务算出相同 sequence → 撞
    uq_incident_seq 唯一键、整个聚合交付失败。锁定读串行化分配,且对"尚无事件
    的新 Incident"通过间隙锁同样生效。"""
    current = session.execute(
        select(IncidentEvent.sequence)
        .filter(IncidentEvent.incident_id == incident_id)
        .order_by(IncidentEvent.sequence.desc())
        .limit(1)
        .with_for_update()).scalar()
    return int(current or 0) + 1


def append_event(incident_id: int, event_type: str, payload: dict | None = None) -> IncidentEvent:
    with Session(get_control_engine()) as session:
        event = IncidentEvent(incident_id=incident_id,
                              sequence=_next_sequence(session, incident_id),
                              event_type=event_type, payload=payload)
        session.add(event)
        session.commit()
        session.refresh(event)
        return event


def append_event_in_session(session: Session, incident_id: int, event_type: str,
                            payload: dict | None = None) -> None:
    """V2.1-C:在**调用方事务内**追加事件(不提交、不另开会话)。

    告警生命周期事件(created_from_alert/alert_merged/alert_resolved)与聚合事务
    同源:要么事件与聚合一起可见,要么一起回滚,杜绝"事件与聚合不同源"。"""
    session.add(IncidentEvent(incident_id=incident_id,
                              sequence=_next_sequence(session, incident_id),
                              event_type=event_type, payload=payload))


def list_events(incident_id: int, after_sequence: int = 0) -> list[IncidentEvent]:
    with Session(get_control_engine()) as session:
        return list(session.scalars(
            select(IncidentEvent)
            .filter(IncidentEvent.incident_id == incident_id,
                    IncidentEvent.sequence > after_sequence)
            .order_by(IncidentEvent.sequence.asc())).all())
