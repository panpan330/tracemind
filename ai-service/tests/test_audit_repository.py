# ai-service/tests/test_audit_repository.py
from app.tools_core.context import ClientInvocationContext
from app.tools_core.ports import ToolAuditUnavailable
from app.tools_infrastructure.audit_repository import MySqlToolAuditPort


class MemAudit:  # 测试替身,验证两段式语义(不连库)
    def __init__(self):
        self.started = []
        self.finished = []
        self.fail_started = False

    def write_attempt_started(self, ctx, attempt_no, mcp_request_id):
        if self.fail_started:
            raise ToolAuditUnavailable("audit db down")
        self.started.append((ctx.tool_call_id, attempt_no, mcp_request_id))
        return len(self.started)

    def write_attempt_finished(self, attempt_pk, outcome, result=None,
                               error_code=None, retryable=None, latency_ms=0):
        self.finished.append((attempt_pk, outcome))


def test_two_phase_audit():
    a = MemAudit()
    ctx = ClientInvocationContext(1, 1, "tc-1", "investigation")
    pk = a.write_attempt_started(ctx, 1, "m-1")
    a.write_attempt_finished(pk, "completed", result={"success": True})
    assert len(a.started) == 1 and a.finished[0] == (1, "completed")


def test_started_failure_is_fatal():
    a = MemAudit()
    a.fail_started = True
    ctx = ClientInvocationContext(1, 1, "tc-1", "investigation")
    try:
        a.write_attempt_started(ctx, 1, "m-1")
        assert False, "应抛 ToolAuditUnavailable"
    except ToolAuditUnavailable:
        pass


def test_mysql_port_class_exists():
    assert MySqlToolAuditPort is not None
