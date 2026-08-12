"""Tool Calling 混合循环核心:eligible 计算、校验、参数解析、去重、预算。"""
import hashlib
import json

from app.agent.tool_schemas import ALLOWED_TOOLS, TOOL_SCHEMAS

# ---- 预算(V1.3 定稿,设计 §5A;以 Fake/real_strict 评测校准为准)----
MAX_DECISION_ATTEMPTS = 14
MAX_TOOL_EXECUTIONS = 10
MAX_LOCK_EVIDENCE_REFRESH = 1
MAX_CONSECUTIVE_INVALID = 2
MAX_CONSECUTIVE_NO_PROGRESS = 2
MAX_DURATION_SECONDS = 180

EVIDENCE_TOOL = {
    "e1": "get_service_metrics",
    "e2": "get_trace",
    "e3": "list_expensive_query_digests",
    "e4": "get_query_plan",
    "e5": "get_index_info",
}


def compute_eligible_tools(state: dict) -> set[str]:
    """独立资格判断:每轮把所有满足条件的工具暴露给 LLM(不退化为固定顺序)。
    evidence_gate 兼容大写(E1)与小写(e1)两种写法。"""
    gate = state.get("evidence_gate") or {}

    def satisfied(key: str) -> bool:
        return bool(gate.get(key, gate.get(key.upper(), False)))

    evidence = {e.get("key"): e for e in state.get("evidence") or []}
    eligible: set[str] = set()
    if not satisfied("e1"):
        eligible.add("get_service_metrics")
    if not satisfied("e2"):
        content = (evidence.get("e1") or {}).get("content") or {}
        # V1.4:get_trace 由异常窗口驱动——E1 已采集后才可循;
        # 真实后端(prometheus/jaeger)需有效窗口,fixture 后端无需窗口。
        if content:
            backend = content.get("sourceBackend") or "fixture"
            needs_window = backend in ("prometheus", "jaeger")
            has_window = bool(content.get("windowStart") and content.get("windowEnd"))
            has_ctx = bool(state.get("affected_service_ref") or state.get("service_ref"))
            if (has_window or not needs_window) and has_ctx:
                eligible.add("get_trace")
    if not satisfied("e3"):
        eligible.add("list_expensive_query_digests")
    if not satisfied("e4"):
        content = (evidence.get("e3") or {}).get("content") or {}
        if content.get("query_ref") == "INVENTORY_LOOKUP":
            eligible.add("get_query_plan")
    if not satisfied("e5"):
        eligible.add("get_index_info")
    # 锁调查工具资格(V1.3,独立判断,不退化为固定顺序)
    if not satisfied("l1"):
        eligible.add("get_lock_waiters")
    if not satisfied("l2"):
        l1_ev = (evidence.get("l1") or {}).get("content") or {}
        lock_observed = any(
            w.get("object_schema") == "tracemind_business"
            and w.get("object_table") == "inventory"
            and w.get("waiting_query_ref") == "INVENTORY_RESERVATION"
            for w in (l1_ev.get("waits") or []))
        if lock_observed:
            eligible.add("get_transaction_details")
    return eligible


def _validate_enum(name: str, args: dict, schema: dict) -> str | None:
    """只校验白名单与边界;required 由 resolve_arguments 程序补充(LLM 只选工具)。"""
    props = schema["parameters"].get("properties", {})
    for k, v in args.items():
        spec = props.get(k)
        if spec and "enum" in spec and v not in spec["enum"]:
            return f"参数 {k} 不在白名单: {v}"
        if spec and "minimum" in spec and isinstance(v, (int, float)) and v < spec["minimum"]:
            return f"参数 {k} 过小: {v}"
    return None


def validate_tool_call(name: str, args: dict, eligible: set[str]) -> str | None:
    if name not in ALLOWED_TOOLS:
        return f"非法工具 {name}"
    if name not in eligible:
        return f"工具 {name} 当前不具备调用前置条件"
    schema = next(t["function"] for t in TOOL_SCHEMAS if t["function"]["name"] == name)
    return _validate_enum(name, args, schema)


class ArgumentResolutionError(Exception):
    pass


def resolve_arguments(name: str, raw_args: dict, state: dict) -> dict:
    """LLM 选工具,程序解析真实参数(参数来源见设计 §3.3)。"""
    evidence = {e.get("key"): e for e in state.get("evidence") or []}
    if name == "get_service_metrics":
        return {"service_ref": state.get("service_ref", "inventory-service"),
                "window_seconds": raw_args.get("window_seconds", 300)}
    if name == "get_trace":
        # V1.4 三层参数:模型只传抽象 trace_ref 或可信 trace_id;程序解析为搜索参数
        raw_trace_id = (raw_args or {}).get("trace_id")
        if raw_trace_id:
            return {"trace_id": raw_trace_id}
        return {
            "service_ref": state.get("affected_service_ref") or state.get("service_ref"),
            "operation_ref": state.get("affected_operation_ref") or "INVENTORY_LOOKUP",
            "window_start": state.get("observed_at") or "2026-08-12T00:00:00Z",
            "window_end": "2026-08-12T00:05:00Z",
            "strategy": "SLOWEST",
        }
    if name == "list_expensive_query_digests":
        return {"window_seconds": raw_args.get("window_seconds", 300)}
    if name == "get_query_plan":
        sp = raw_args.get("sample_parameters") or {}
        # 演示参数由程序固定(INVENTORY_LOOKUP 白名单模板),模型不得编造
        return {"query_ref": "INVENTORY_LOOKUP",
                "sample_parameters": {"skuId": sp.get("skuId", 42),
                                       "warehouseId": sp.get("warehouseId", 7)}}
    if name == "get_index_info":
        return {"table_ref": "inventory"}
    if name == "get_lock_waiters":
        return {"scope_ref": raw_args.get("scope_ref", "INVENTORY_RESERVATION")}
    if name == "get_transaction_details":
        # 受控引用:程序从当前 Incident/Run 的有效锁等待证据取 blocker_ref(LLM 不得编造)
        waits = ((evidence.get("l1") or {}).get("content") or {}).get("waits") or []
        target = [w for w in waits if w.get("object_schema") == "tracemind_business"
                  and w.get("object_table") == "inventory"
                  and w.get("waiting_query_ref") == "INVENTORY_RESERVATION"]
        if not target:
            raise ArgumentResolutionError("无有效 blocker_ref,无法调用 get_transaction_details")
        return {"transaction_ref": target[0].get("blocker_ref")}
    raise ArgumentResolutionError(f"未知工具 {name}")


class DuplicateGuard:
    """去重键 = tool_name | canonical_arguments_hash | phase | system_version。
    phase 取 state 的 investigation_phase(默认 investigating);system_version 默认 "1"。"""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def _key(self, name: str, args: dict) -> str:
        canonical = json.dumps(args, sort_keys=True, ensure_ascii=False)
        h = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
        return f"{name}|{h}|investigating|1"

    def check(self, name: str, args: dict) -> tuple[bool, str]:
        key = self._key(name, args)
        if key in self._seen:
            return True, key
        self._seen.add(key)
        return False, key

    def seed(self, record: dict) -> None:
        """用历史调用记录预置去重集合(供 collect_evidence 跨轮去重)。"""
        self._seen.add(self._key(record.get("tool_name", ""), record.get("arguments", {})))
