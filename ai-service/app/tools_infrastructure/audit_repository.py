"""MCP Server 侧审计写入(tool_call_attempt),用 mcp_tool_auditor 最小权限账号。
V2.0-A:时间一律应用侧生成 naive UTC(不再用 DB NOW(),规避服务器时区漂移)。"""
from datetime import datetime, timezone
from typing import Optional

from app.tools_core.ports import (ToolAuditPort, ToolAuditPersistFailed,
                                  ToolAuditUnavailable)


def _utcnow() -> "datetime":
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
                    "VALUES (:tc, :an, :mrid, :iid, :rid, :p, 'mcp_streamable_http', 'started', :now)"
                ), {"tc": ctx.tool_call_id, "an": attempt_no, "mrid": mcp_request_id,
                    "iid": ctx.incident_id, "rid": ctx.agent_run_id, "p": ctx.purpose,
                    "now": _utcnow()})
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
                    "latency_ms=:l, result_hash=:rh, completed_at=:now WHERE id=:pk"
                ), {"o": outcome, "ec": error_code, "rb": retryable, "l": latency_ms,
                    "rh": self._hash(result or {}), "pk": attempt_pk, "now": _utcnow()})
                conn.commit()
        except Exception as e:  # noqa: BLE001
            raise ToolAuditPersistFailed(str(e)) from e

    def write_observation_query(self, ctx, tool_name: str, params: dict,
                                result: dict, latency_ms: int) -> None:
        from sqlalchemy import text
        with self._engine_factory().connect() as conn:
            # 列名以 004_control_schema.observation_query 为准(queried_at;原 created_at 列不存在)
            conn.execute(text(
                "INSERT INTO observation_query (incident_id, agent_run_id, "
                "observation_query_id, backend, query_template_id, normalized_params_json, "
                "status, duration_ms, result_hash, queried_at) "
                "VALUES (:iid, :rid, :obs, 'mcp', :tn, :params, 'ok', :l, :rh, :now)"
            ), {"iid": ctx.incident_id, "rid": ctx.agent_run_id,
                "obs": f"mcp-{_utcnow().strftime('%Y%m%d%H%M%S%f')}",
                "tn": tool_name, "params": self._hash(params), "l": latency_ms,
                "rh": self._hash(result or {}), "now": _utcnow()})
            conn.commit()

    @staticmethod
    def _hash(obj: dict) -> str:
        import hashlib, json
        return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:16]
