"""retrieval_record 审计写入(control 库)。"""
from sqlalchemy import text

from app.db.engine import get_control_engine

# 惰性获取:offline_eval profile 下模块导入不触 DB(运行时 insert 才调,
# 此时 engine 层会抛 DATABASE_ACCESS_DISABLED 或按 profile 返回)。
# 不缓存模块级 engine,避免导入期副作用 + 测试可替换。


def insert(*, incident_id: int, run_id: int, node: str, query_text_hash: str,
           collection_alias: str, collection_version: str, embedding_model: str,
           dimensions: int, candidate_top_k: int, final_chunk_ids: str, scores: str,
           latency_ms: int, status: str, error_code: str, degraded: bool) -> None:
    control_engine = get_control_engine()
    with control_engine.begin() as conn:
        conn.execute(
            text("INSERT INTO retrieval_record (incident_id, agent_run_id, node, query_text_hash, "
                 "collection_alias, collection_version, embedding_model, embedding_dimensions, "
                 "candidate_top_k, final_chunk_ids, scores, latency_ms, status, error_code, degraded) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"),
            (incident_id, run_id, node, query_text_hash, collection_alias,
             collection_version, embedding_model, dimensions, candidate_top_k,
             final_chunk_ids, scores, latency_ms, status, error_code, int(degraded)),
        )


def list_retrievals_by_run(agent_run_id: int) -> list[dict]:
    from sqlalchemy import text
    control_engine = get_control_engine()
    with control_engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT node, final_chunk_ids, scores, latency_ms, status, degraded, id "
            "FROM retrieval_record WHERE agent_run_id = :r ORDER BY id"), {"r": agent_run_id})
        return [dict(row._mapping) for row in rows]
