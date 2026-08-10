from sqlalchemy import text

from app.db.engine import get_readonly_engine
from app.tools.schemas import QUERY_REF_WHITELIST

# 服务端固化 SQL 模板,LLM 永远无法提交完整 SQL
QUERY_REGISTRY = {
    "INVENTORY_LOOKUP": "SELECT id, sku_id, warehouse_id, quantity FROM inventory "
                        "WHERE sku_id = {skuId} AND warehouse_id = {warehouseId}",
}


def explain(query_ref: str, sample_parameters: dict) -> dict:
    """E4:白名单模板 + EXPLAIN FORMAT=JSON,返回执行计划。"""
    if query_ref not in QUERY_REF_WHITELIST:
        raise ValueError("UNKNOWN_QUERY_REF")
    sql = QUERY_REGISTRY[query_ref].format(**sample_parameters)  # 参数经 Pydantic 校验为 int
    explain_sql = f"EXPLAIN FORMAT=JSON {sql}"
    with get_readonly_engine().connect() as conn:
        row = conn.execute(text(explain_sql)).fetchone()
    explain_json = row[0] if row else None
    if isinstance(explain_json, str):
        import json
        explain_json = json.loads(explain_json)
    return {"query_ref": query_ref, "explain": explain_json}
