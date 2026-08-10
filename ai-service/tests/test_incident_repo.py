from sqlalchemy import text

from app.db.engine import get_control_engine, get_readonly_engine
from app.repositories.incident_repo import create_incident
from app.repositories.run_repo import create_run
from app.services.baseline_service import capture_digest_baseline


def test_create_incident_and_run():
    incident = create_incident("test incident", "desc", "high", "inventory-service")
    assert incident.id is not None
    run = create_run(incident.id)
    assert run.thread_id.startswith("run-")
    # 清理
    with get_control_engine().begin() as conn:
        conn.execute(text("DELETE FROM agent_run WHERE id = :rid"), {"rid": run.id})
        conn.execute(text("DELETE FROM incident WHERE id = :iid"), {"iid": incident.id})


def test_capture_digest_baseline_returns_dict():
    baseline = capture_digest_baseline(get_readonly_engine())
    assert isinstance(baseline, dict)
