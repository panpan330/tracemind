"""五只读工具 OpenAI function schema(execute_fix/verify_recovery 永不在此)。"""
TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": "get_service_metrics",
        "description": "查询服务接口指标(P95/QPS/错误率/代表慢请求 traceId)",
        "parameters": {"type": "object", "properties": {
            "service_ref": {"type": "string", "enum": ["order-service", "inventory-service"]},
            "window_seconds": {"type": "integer", "minimum": 30, "maximum": 1800}},
            "required": ["service_ref"]}}},
    {"type": "function", "function": {"name": "get_trace",
        "description": "按 traceId 查询调用链各阶段耗时分布",
        "parameters": {"type": "object", "properties": {
            "trace_ref": {"type": "string", "enum": ["REPRESENTATIVE_SLOW_TRACE"]}},
            "required": ["trace_ref"]}}},
    {"type": "function", "function": {"name": "list_expensive_query_digests",
        "description": "列出窗口内高代价 SQL 摘要(相对 Incident 基线的增量)",
        "parameters": {"type": "object", "properties": {
            "window_seconds": {"type": "integer", "minimum": 60, "maximum": 1800}},
            "required": []}}},
    {"type": "function", "function": {"name": "get_query_plan",
        "description": "获取目标查询执行计划(EXPLAIN FORMAT=JSON)",
        "parameters": {"type": "object", "properties": {
            "query_ref": {"type": "string", "enum": ["INVENTORY_LOOKUP"]},
            "sample_parameters": {"type": "object"}},
            "required": ["query_ref"]}}},
    {"type": "function", "function": {"name": "get_index_info",
        "description": "查询目标表索引元数据(information_schema)",
        "parameters": {"type": "object", "properties": {
            "table_ref": {"type": "string", "enum": ["inventory"]}},
            "required": ["table_ref"]}}},
    {"type": "function", "function": {"name": "get_lock_waiters",
        "description": "查询目标库存记录的锁等待关系(仅用于锁阻塞调查)",
        "parameters": {"type": "object", "properties": {
            "scope_ref": {"type": "string", "enum": ["INVENTORY_RESERVATION"]}},
            "required": ["scope_ref"]}}},
    {"type": "function", "function": {"name": "get_transaction_details",
        "description": "查询已观测阻塞事务的详情(需先调用 get_lock_waiters 获得引用)",
        "parameters": {"type": "object", "properties": {
            "transaction_ref": {"type": "string", "enum": ["OBSERVED_BLOCKER"]}},
            "required": ["transaction_ref"]}}},
]

ALLOWED_TOOLS = {t["function"]["name"] for t in TOOL_SCHEMAS}
