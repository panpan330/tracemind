"""observation_query 审计写入(control 库);只存归一化结果,不存原始观测响应。"""
import json

from sqlalchemy import text

from app.db.engine import get_control_engine

control_engine = get_control_engine()


def record_query(*, incident_id: int, agent_run_id: int, observation_query_id: str,
                 backend: str, query_template_id: str | None,
                 normalized_params: dict, window_start: str | None,
                 window_end: str | None, status: str, error_code: str | None,
                 duration_ms: int | None, result_hash: str | None,
                 trace_id: str | None, normalized_result: dict | list | None) -> None:
    with control_engine.begin() as conn:
        conn.execute(
            text("INSERT INTO observation_query (incident_id, agent_run_id, "
                 "observation_query_id, backend, query_template_id, "
                 "normalized_params_json, window_start, window_end, status, error_code, "
                 "duration_ms, result_hash, trace_id, normalized_result_json) "
                 "VALUES (:incident_id, :agent_run_id, :observation_query_id, :backend, "
                 ":query_template_id, :normalized_params_json, :window_start, :window_end, "
                 ":status, :error_code, :duration_ms, :result_hash, :trace_id, "
                 ":normalized_result_json)"),
            {
                "incident_id": incident_id, "agent_run_id": agent_run_id,
                "observation_query_id": observation_query_id, "backend": backend,
                "query_template_id": query_template_id,
                "normalized_params_json": (json.dumps(normalized_params, ensure_ascii=False)
                                           if normalized_params else None),
                "window_start": window_start, "window_end": window_end,
                "status": status, "error_code": error_code, "duration_ms": duration_ms,
                "result_hash": result_hash, "trace_id": trace_id,
                "normalized_result_json": (json.dumps(normalized_result, ensure_ascii=False)
                                           if normalized_result is not None else None),
            },
        )
