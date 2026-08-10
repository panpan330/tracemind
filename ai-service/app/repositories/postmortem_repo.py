from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import Postmortem


def create_postmortem(incident_id: int, content: dict) -> Postmortem:
    with Session(get_control_engine()) as session:
        pm = Postmortem(incident_id=incident_id, content=content)
        session.add(pm)
        session.commit()
        session.refresh(pm)
        return pm
