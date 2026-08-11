"""retrieval_record 审计写入(control 库)。"""
from app.db.engine import get_control_engine

# 模块级 engine:懒创建(create_engine 不真连),测试可 monkeypatch 替换
control_engine = get_control_engine()


def insert(*, incident_id: int, run_id: int, node: str, query_text_hash: str,
           collection_alias: str, collection_version: str, embedding_model: str,
           dimensions: int, candidate_top_k: int, final_chunk_ids: str, scores: str,
           latency_ms: int, status: str, error_code: str, degraded: bool) -> None:
    with control_engine.begin() as conn:
        conn.execute(
            "INSERT INTO retrieval_record (incident_id, agent_run_id, node, query_text_hash, "
            "collection_alias, collection_version, embedding_model, embedding_dimensions, "
            "candidate_top_k, final_chunk_ids, scores, latency_ms, status, error_code, degraded) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (incident_id, run_id, node, query_text_hash, collection_alias,
             collection_version, embedding_model, dimensions, candidate_top_k,
             final_chunk_ids, scores, latency_ms, status, error_code, int(degraded)),
        )
