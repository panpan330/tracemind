"""V1.4 操作注册表:affected_operation_ref → Prometheus/Jaeger 查询参数。
非根因上下文:表示"哪个业务接口发生异常",不代表 scenario/root_cause/Policy/修复动作。

V2.0-A 可观测性契约冻结(与 Java 控制器逐项对照;变更需同步 java/ 侧映射):
- Micrometer `service` 公共标签:management.metrics.tags.service(order/inventory application.yml);
- `uri` 标签为 Spring MVC 模板路径(含路径变量,如 /api/orders/{orderId}/check-stock);
- Prometheus 抓取 /actuator/prometheus:9081/9082(observability/prometheus.yml);
- Hikari 指标名(Micrometer 默认,供 V2.4 SCN-003 使用):
  hikaricp_connections_active / _idle / _pending / _max
  hikaricp_connections_timeout_total(Micrometer 3.x 若无 timeout 计数,以受控业务错误计数补充)。
"""
import re

OPERATION_REFS = ("ORDER_CREATE", "INVENTORY_LOOKUP", "INVENTORY_RESERVATION")

# operation → Jaeger http.route 模板(低基数模板路径)
OPERATION_TO_ROUTE = {
    "ORDER_CREATE": "/api/orders/{orderId}/check-stock",
    "INVENTORY_LOOKUP": "/api/inventory",
    "INVENTORY_RESERVATION": "/api/inventory",
}

# operation → Prometheus uri 标签真实模板(与 @PostMapping/@RequestMapping 一致)
OPERATION_TO_URI = {
    "ORDER_CREATE": "/api/orders/{orderId}/check-stock",
    "INVENTORY_LOOKUP": "/api/inventory",
    "INVENTORY_RESERVATION": "/api/inventory",
}

SERVICE_TO_OPERATIONS = {
    "order-service": ("ORDER_CREATE",),
    "inventory-service": ("INVENTORY_LOOKUP", "INVENTORY_RESERVATION"),
}

# 已知违例修正记录:V1.x 曾把 ORDER_CREATE 映射为 "/api/orders/check-stock",
# 与真实端点 @PostMapping("/{orderId}/check-stock") 不符(真实 uri 标签含路径变量)。


def uri_regex_for_service(service_ref: str) -> str:
    """service 下全部 operation 的 uri 模板 → 锚定 PromQL 正则(花括号转义)。
    未知 service 抛错(fail closed):禁止 uri=~\".+\" 通配掩盖映射错误。"""
    ops = SERVICE_TO_OPERATIONS.get(service_ref)
    if not ops:
        raise ValueError(f"METRICS_RESULT_INVALID: 未注册 service_ref={service_ref!r}"
                         "(operation registry 无映射,禁止通配 uri)")
    parts = []
    for op in ops:
        template = OPERATION_TO_URI[op]
        parts.append(_literal_regex(template))
    return "^(" + "|".join(parts) + ")$"


def _literal_regex(template: str) -> str:
    """模板路径 → 字面匹配正则(**无反斜杠**形式,V2.1-C live 修复)。

    2026-09-29 live 实测矩阵(Prometheus 2.55):
    - 模板经 re.escape 产生的 `\\{`/`\\-` 拼进 PromQL 字符串:单反斜杠 → 400
      (PromQL/RE2 拒绝 `\\{` 转义);双反斜杠 → 200 但**匹配不到任何序列**;
    - 字符类 `[{}]`/`[-]` 与不转义字面量均正确匹配。
    因此统一用字符类转义特殊字符:对 RE2 与 Python re 都无歧义、无反斜杠,
    也就不存在 PromQL 字符串字面量的二次转义问题。"""
    out = []
    for ch in template:
        if ch.isalnum() or ch in "/:_":
            out.append(ch)
        else:
            out.append("[" + ch + "]")
    return "".join(out)


def uri_regex_for_operation(service_ref: str, operation_ref: str | None) -> str:
    """operation 级 uri 正则(V2.1-C 恢复信号:与告警规则同服务**同操作**)。

    已知 operation → 该模板的锚定正则;operation 缺失/未注册 → 回退 service 级
    全部 operation 正则(仍禁止通配 uri,不掩盖映射错误)。"""
    if operation_ref:
        template = OPERATION_TO_URI.get(operation_ref)
        if template is not None and operation_ref in SERVICE_TO_OPERATIONS.get(
                service_ref, ()):
            return "^(" + _literal_regex(template) + ")$"
    return uri_regex_for_service(service_ref)


def uri_templates_for_service(service_ref: str) -> tuple[str, ...]:
    ops = SERVICE_TO_OPERATIONS.get(service_ref)
    if not ops:
        raise ValueError(f"METRICS_RESULT_INVALID: 未注册 service_ref={service_ref!r}")
    return tuple(OPERATION_TO_URI[op] for op in ops)
