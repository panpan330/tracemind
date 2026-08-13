"""MCP Server 侧审计写入(tool_call_attempt),用 mcp_tool_auditor 最小权限账号。"""
from typing import Optional

from app.tools_core.ports import (ToolAuditPort, ToolAuditPersistFailed,
                                  ToolAuditUnavailable)


class MySqlToolAuditPort(ToolAuditPort):
    def __init__(self, engine_factory=None):
        self._engine_factory = engine_factory or self._default_engine

    def _default_engine(self):
        from sqlalchemy import create_engine
        from app.config.mcp import McpHttpServerSettings
        s = McpHttpServerSettings()
        url = s.mcp_audit_db_url or s.control_db_url
        return create_engine(url)

    def write_attempt_started(self, ctx, attempt_no: int, mcp_request_id: str) -> int:
        try:
            from sqlalchemy import text
            with self._engine_factory().connect() as conn:
                res = conn.execute(text(
                    "INSERT INTO tool_call_attempt (tool_call_id, attempt_no, mcp_request_id, "
                    "incident_id, agent_run_id, purpose, transport, outcome, started_at) "
                    "VALUES (:tc, :an, :mrid, :iid, :rid, :p, 'mcp_streamable_http', 'started', NOW())"
                ), {"tc": ctx.tool_call_id, "an": attempt_no, "mrid": mcp_request_id,
                    "iid": ctx.incident_id, "rid": ctx.agent_run_id, "p": ctx.purpose})
                conn.commit()
                return int(res.lastrowid)
        except Exception as e:  # noqa: BLE001
            raise ToolAuditUnavailable(str(e)) from e

    def write_attempt_finished(self, attempt_pk: int, outcome: str,
                               result: Optional[dict] = None, error_code: Optional[str] = None,
                               retryable: Optional[bool] = None, latency_ms: int = 0) -> None:
        try:
            from sqlalchemy import text
            with self._engine_factory().connect() as conn:
                conn.execute(text(
                    "UPDATE tool_call_attempt SET outcome=:o, error_code=:ec, retryable=:rb, "
                    "latency_ms=:l, result_hash=:rh, completed_at=NOW() WHERE id=:pk"
                ), {"o": outcome, "ec": error_code, "rb": retryable, "l": latency_ms,
                    "rh": self._hash(result or {}), "pk": attempt_pk})
                conn.commit()
        except Exception as e:  # noqa: BLE001
            raise ToolAuditPersistFailed(str(e)) from e

    def write_observation_query(self, ctx, tool_name: str, params: dict,
                                result: dict, latency_ms: int) -> None:
        from sqlalchemy import text
        with self._engine_factory().connect() as conn:
            conn.execute(text(
                "INSERT INTO observation_query (incident_id, agent_run_id, tool_name, "
                "params_hash, latency_ms, created_at) VALUES (:iid, :rid, :tn, :ph, :l, NOW())"
            ), {"iid": ctx.incident_id, "rid": ctx.agent_run_id, "tn": tool_name,
                "ph": self._hash(params), "l": latency_ms})
            conn.commit()

    @staticmethod
    def _hash(obj: dict) -> str:
        import hashlib, json
        return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:16]
