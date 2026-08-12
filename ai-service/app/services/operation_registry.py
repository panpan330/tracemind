"""V1.4 操作注册表:affected_operation_ref → Prometheus/Jaeger 查询参数。
非根因上下文:表示"哪个业务接口发生异常",不代表 scenario/root_cause/Policy/修复动作。"""

OPERATION_REFS = ("ORDER_CREATE", "INVENTORY_LOOKUP", "INVENTORY_RESERVATION")

# operation → Jaeger service / http.route 模板(低基数模板路径)
OPERATION_TO_ROUTE = {
    "ORDER_CREATE": "/api/orders/check-stock",
    "INVENTORY_LOOKUP": "/api/inventory",
    "INVENTORY_RESERVATION": "/api/inventory",
}

# operation → Prometheus uri 标签(低基数模板路径,非具体 ID 路径)
OPERATION_TO_URI = {
    "ORDER_CREATE": "/api/orders/check-stock",
    "INVENTORY_LOOKUP": "/api/inventory",
    "INVENTORY_RESERVATION": "/api/inventory",
}
