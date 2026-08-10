from sqlalchemy import Engine, text

# 目标查询 digest 文本匹配(INVENTORY_LOOKUP 的规范化指纹片段)
TARGET_DIGEST_LIKE = "%inventory%sku_id%warehouse_id%"


def capture_digest_baseline(engine: Engine) -> dict:
    """从 performance_schema 采集目标查询的累计计数快照(Incident 创建时调用)。"""
    sql = text("""
        SELECT DIGEST_TEXT, COUNT_STAR, SUM_TIMER_WAIT, SUM_ROWS_EXAMINED
        FROM performance_schema.events_statements_summary_by_digest
        WHERE DIGEST_TEXT LIKE :pattern
    """)
    baseline: dict[str, dict] = {}
    with engine.connect() as conn:
        for row in conn.execute(sql, {"pattern": TARGET_DIGEST_LIKE}):
            baseline[row.DIGEST_TEXT] = {
                "count": int(row.COUNT_STAR),
                "total_latency_us": int(row.SUM_TIMER_WAIT) // 1000,
                "rows_examined": int(row.SUM_ROWS_EXAMINED),
            }
    return baseline
