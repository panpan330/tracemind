# ai-service/tests/test_tools_core_ports.py
from app.tools_core.ports import (IncidentRunPort, ToolAuditPort, MetricsPort,
                                  TracePort, DigestPort, PlanPort, IndexPort,
                                  LockPort, ToolAuditUnavailable,
                                  ToolAuditPersistFailed, RunContext)


def test_port_names():
    assert IncidentRunPort.__name__ == "IncidentRunPort"
    assert ToolAuditPort.__name__ == "ToolAuditPort"
    assert MetricsPort.__name__ == "MetricsPort"
    assert LockPort.__name__ == "LockPort"


def test_run_context_frozen():
    rc = RunContext(run_id=1, incident_id=1, status="running", service_ref="inventory-service")
    assert rc.run_id == 1 and rc.status == "running"
    try:
        rc.status = "done"
        assert False, "RunContext 应不可变"
    except Exception:
        pass


def test_audit_errors_are_exceptions():
    assert issubclass(ToolAuditUnavailable, Exception)
    assert issubclass(ToolAuditPersistFailed, Exception)
