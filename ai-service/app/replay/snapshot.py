"""ReplaySnapshotFactory:IncidentState → 规范脱敏快照(白名单/深拷贝/确定性排序/Canonical JSON)。
snapshot_hash = SHA-256(canonical_json):只证明一致性,不声称防篡改。"""
import copy
import hashlib
import json

# 白名单:只保留回放展示需要的字段(排除内部字段/大型工具返回/未脱敏参数)
_SNAPSHOT_WHITELIST = ("hypotheses", "facts", "policy", "evidence_gate",
                       "root_cause_code", "confirmed_hypothesis_id", "status",
                       "termination_reason", "severity", "service_ref")


def _deterministic_sort(obj):
    """递归确定性排序:dict 按键;list 元素按 canonical JSON 排序(可排序时)。"""
    if isinstance(obj, dict):
        return {k: _deterministic_sort(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        items = [_deterministic_sort(v) for v in obj]
        try:
            return sorted(items, key=lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False))
        except TypeError:
            return items
    return obj


def canonical_json(obj: dict) -> str:
    return json.dumps(_deterministic_sort(obj), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def snapshot_hash(obj: dict) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


class ReplaySnapshotFactory:
    def snapshot(self, state: dict) -> dict:
        src = copy.deepcopy({k: state.get(k) for k in _SNAPSHOT_WHITELIST if k in state})
        hyps = sorted(src.get("hypotheses") or [], key=lambda h: str(h.get("id")))
        facts = dict(sorted((src.get("facts") or {}).items()))
        policies = dict(sorted((src.get("policy") or {}).items()))
        return {
            "hypotheses": hyps,
            "facts": facts,
            "diagnostic_policies": policies,
            "exclusion_conditions": _exclusion_conditions(facts),
            "confirmed_root_cause": (src.get("root_cause_code")
                                     or src.get("confirmed_hypothesis_id")),
            "incident_status": src.get("status"),
            "pending_approval": None,
            "recovery_status": None,
        }


def _exclusion_conditions(facts: dict) -> dict:
    """安全排他条件(Facts 推导):与根因正向证据分区展示。"""
    return {
        "X_INDEX_NORMAL": facts.get("F_INDEX_MISSING") is False,
        "X_NO_TARGET_LOCK_WAIT": facts.get("F_TARGET_LOCK_WAIT") is False,
    }
