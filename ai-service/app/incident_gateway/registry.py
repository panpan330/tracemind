"""V2.1-B:服务端映射注册表 —— alertname/service/operation 组合校验、
environment 白名单、severity 服务端映射(不信任外部声明)。

demo 阶段仅一条规则;V2.4 增加场景时在此扩展(单点)。"""
import hashlib
import json
from dataclasses import dataclass

ENVIRONMENTS = frozenset({"demo"})

# 服务端权威映射:labels 声明的 service/operation 必须与规则一致
ALERT_RULES = {
    "OrderOperationP95High": {"service": "order-service", "operation": "ORDER_CREATE"},
}

SEVERITY_MAP = {"warning": "medium", "critical": "high"}
DEFAULT_SEVERITY = "high"


@dataclass(frozen=True)
class ResolvedAlert:
    alertname: str
    environment: str
    service: str
    operation: str
    severity: str


def resolve_alert(labels: dict[str, str]) -> ResolvedAlert | None:
    """服务端映射:任一校验失败返回 None(调用方按 incomplete_labels 语义仅归档)。"""
    alertname = labels.get("alertname")
    rule = ALERT_RULES.get(alertname)
    if rule is None:
        return None
    environment = labels.get("environment")
    if environment not in ENVIRONMENTS:
        return None
    service = labels.get("service")
    operation = labels.get("operation")
    if service != rule["service"] or operation != rule["operation"]:
        return None
    return ResolvedAlert(alertname=alertname, environment=environment,
                         service=service, operation=operation,
                         severity=SEVERITY_MAP.get(labels.get("severity", ""),
                                                   DEFAULT_SEVERITY))


def group_key_hash(source: str, alertname: str, environment: str,
                   service: str, operation: str) -> str:
    """聚合键:结构化数据(canonical_json)的 SHA-256,定长 64,无分隔符拼接。"""
    payload = json.dumps({
        "source": source, "alertname": alertname, "environment": environment,
        "service": service, "operation": operation,
    }, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
