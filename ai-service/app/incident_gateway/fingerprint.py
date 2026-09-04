"""V2.1-A:告警指纹/规范化 —— delivery_hash、alert_instance_key、标签边界。

- delivery_hash:规范化 JSON(排序键/紧凑分隔符)的 SHA-256,键序不敏感;
  禁止直接哈希受字段顺序影响的原始字节(方案 §V2.1-4)。
- alert_instance_key:source + external_fingerprint + startsAt;
  相同 fingerprint 只有更晚的 startsAt 才是新实例(由 key 天然区分)。
- normalize_labels:标签白名单(键)、值长度限制;白名单外键剥离,超长值记 violation。
"""
import hashlib
import json

# 标签白名单:demo 聚合键 + Prometheus 常见系统标签(白名单外键剥离,不入库)
LABEL_ALLOWLIST = frozenset({
    "alertname", "service", "operation", "environment", "severity",
    "instance", "job",
})
MAX_LABEL_VALUE_LEN = 128


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


def delivery_hash(normalized_alert: dict) -> str:
    return hashlib.sha256(canonical_json(normalized_alert).encode("utf-8")).hexdigest()


def alert_instance_key(source: str, fingerprint: str, starts_at_iso: str) -> str:
    return f"{source}:{fingerprint}:{starts_at_iso}"


def normalize_labels(labels: dict[str, str], *,
                     allowlist: frozenset = LABEL_ALLOWLIST,
                     max_value_len: int = MAX_LABEL_VALUE_LEN) -> tuple[dict, list[str]]:
    """保留白名单内键;超长值产生 violation(调用方 ignored 该告警)。"""
    kept: dict[str, str] = {}
    violations: list[str] = []
    for key, value in labels.items():
        if key not in allowlist:
            continue
        if len(value) > max_value_len:
            violations.append(f"label {key} 超长({len(value)} > {max_value_len})")
            continue
        kept[key] = value
    return kept, violations
