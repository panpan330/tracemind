"""model_call 审计写入(control 库)。"""
from sqlalchemy import text

from app.db.engine import get_control_engine

# 模块级 engine:懒创建(create_engine 不真连),测试可 monkeypatch 替换
# 惰性获取:函数内调用 get_control_engine()(offline_eval 下模块导入不触 DB)


def insert(*, incident_id: int, run_id: int, node: str, mode: str, provider: str,
           model: str, model_snapshot: str, prompt_version: str, prompt_hash: str,
           tool_schema_version: str, logical_call_id: str, attempts_json: str,
           finish_reason: str, structured_output_valid: bool, tool_call_count: int,
           provider_request_id: str, fallback_executor: str, input_snapshot_json: str,
           latency_ms: int, input_tokens: int | None, output_tokens: int | None,
           status: str, error_code: str, degraded: bool, git_commit_sha: str,
           knowledge_chunk_ids: str) -> None:
    control_engine = get_control_engine()
    with control_engine.begin() as conn:
        conn.execute(
            text("INSERT INTO model_call (incident_id, agent_run_id, node, mode, provider, model, "
                 "model_snapshot, prompt_version, prompt_hash, tool_schema_version, logical_call_id, "
                 "attempts_json, finish_reason, structured_output_valid, tool_call_count, "
                 "provider_request_id, fallback_executor, input_snapshot_json, latency_ms, "
                 "input_tokens, output_tokens, status, error_code, degraded, git_commit_sha, "
                 "knowledge_chunk_ids) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"),
            (incident_id, run_id, node, mode, provider, model, model_snapshot,
             prompt_version, prompt_hash, tool_schema_version, logical_call_id,
             attempts_json, finish_reason, int(structured_output_valid), tool_call_count,
             provider_request_id, fallback_executor, input_snapshot_json, latency_ms,
             input_tokens, output_tokens, status, error_code, int(degraded),
             git_commit_sha, knowledge_chunk_ids),
        )
