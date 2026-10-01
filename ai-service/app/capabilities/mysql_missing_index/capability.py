"""mysql_missing_index Capability:缺索引场景的诊断知识(V2.0-B 自 nodes/facts/policies 原文迁入)。

拥有:E1~E5 证据评估器、F_* Fact 抽取、SCN-001 诊断 Policy、排他条件(x_index_normal)。
不负责修复执行,不接触写账号;LLM 不参与判定。
"""
import logging

from app.capabilities.codes import ROOT_CAUSE_INDEX
from app.capabilities.base import (DiagnosticCapability, RECOVERY_VERIFY_TOOL)
from app.capabilities.mysql_blocking_transaction.capability import (
    LOCK_WAIT_THRESHOLD_MS)
from app.repositories import incident_repo

logger = logging.getLogger(__name__)

# evidence 键 → Fact 键(一一映射,值为评估器 passed 判定)
_FACT_MAP = {
    "e1": "F_ENDPOINT_DEGRADED",
    "e2": "F_DB_STAGE_DOMINANT",
    "e3": "F_TARGET_QUERY_EXPENSIVE",
    "e4": "F_PLAN_FULL_SCAN",
    "e5": "F_INDEX_MISSING",
}


def evaluate_metrics(result: dict, state: dict) -> list[dict]:
    data = result.get("data") or {}
    p95 = data.get("p95Ms")
    if p95 is None:
        # 窗口内无观测样本(如注入清空观测后负载尚未进入窗口):
        # 不产出证据,视为"尚未采集",允许 planner 后续轮次重采
        return []
    # V2.0-B closure:健康基线来自冻结 RunContext(runner 注入 healthy_baseline_ref),
    # 不回读可变 Incident 行(恢复窗口期内基线可能被修改)
    health = state.get("healthy_baseline_ref") or {}
    base_p95 = (health or {}).get("p95_ms")
    if p95 is not None and base_p95 is not None:
        e1 = p95 > int(base_p95) * 1.2
    else:
        # V2.1-C:无合格基线 → 显式 SLO(校准回填,不再用 100ms 旧常量 —— 缺索引
        # 故障实测 P95 ≈ 40ms,100ms 会让 E1 误判 False 而否定根因)
        from app.config import settings
        e1 = p95 is not None and p95 > settings.slo_p95_ms
    content: dict = {"p95Ms": p95,
                     "sourceBackend": data.get("sourceBackend"),
                     "observationQueryId": data.get("observationQueryId"),
                     "windowStart": data.get("windowStart"),
                     "windowEnd": data.get("windowEnd"),
                     "latestSampleAt": data.get("latestSampleAt")}
    if data.get("representativeSlowTraceId"):
        content["representativeSlowTraceId"] = data["representativeSlowTraceId"]
    return [{"id": "E1", "key": "e1", "source": "get_service_metrics",
             "content": content, "passed": e1}]


def evaluate_trace(result: dict, state: dict) -> list[dict]:
    """V1.4:TraceNormalizer 输出结构(dbDominanceRatio);无法归一化不产 E2。"""
    data = result.get("data") or {}
    backend = data.get("sourceBackend")
    if backend not in ("jaeger", "fixture"):
        return []
    passed = bool(data.get("dbDominanceRatio") is not None
                  and (data.get("dbDominanceRatio") or 0) >= 0.5
                  and data.get("inventoryServerDurationMs"))
    return [{"id": "E2", "key": "e2", "source": "get_trace",
             "content": data, "passed": passed}]


def evaluate_digests(result: dict, state: dict) -> list[dict]:
    # V2.1-B closure:无基线 → E3 unknown(passed=None),禁止确认根因
    if result.get("error_code") == "BASELINE_INSUFFICIENT":
        return [{"id": "E3", "key": "e3", "source": "list_expensive_query_digests",
                 "content": {"baseline_insufficient": True}, "passed": None}]
    digests = (result.get("data") or []) if result.get("success") else []
    top = digests[0] if digests else {}
    op = state.get("affected_operation_ref") or ""
    # 锁阻塞签名(live 验收实测):1205 超时语句 rows_examined=0,但锁等待耗时计入
    # SUM_TIMER_WAIT(每次 +10s)。只认 rows_examined 会把锁场景判成"暂态空增量"
    # 无限重采,直至 decision_budget_exhausted。阈值与锁等待判定同源。
    top_latency = max(digests, key=lambda d: d.get("total_latency_us_delta") or 0,
                      default={})
    max_latency_ms = (top_latency.get("total_latency_us_delta") or 0) / 1000
    if not result.get("success") or (top.get("rows_examined_delta", 0) <= 0
                                     and max_latency_ms < LOCK_WAIT_THRESHOLD_MS):
        # 锁场景(INVENTORY_RESERVATION):无慢查询增量是确定性否定(锁阻塞不产生慢查询),
        # 产 E3=False 证据,继续采集 L1/L2
        if op == "INVENTORY_RESERVATION":
            return [{"id": "E3", "key": "e3", "source": "list_expensive_query_digests",
                     "content": {"top": top, "query_ref": "INVENTORY_LOOKUP"}, "passed": False}]
        # 慢查询场景:增量 0 是暂态(故障负载尚未进入 performance_schema),触发重采
        # (真实后端验收暴露:digest 采集早于负载 → 增量 0 被误判为确定性否定)
        return []
    expensive = (top.get("rows_examined_delta", 0) > 1000
                 or max_latency_ms >= LOCK_WAIT_THRESHOLD_MS)
    # 锁等待签名下 top(按 rows_examined 排序)可能是 0 增量行,审计内容取耗时增量最大者
    content_top = top_latency if max_latency_ms >= LOCK_WAIT_THRESHOLD_MS else top
    # 单场景:高扫描行数或锁等待耗时的 digest 即目标查询(系统内只有 INVENTORY_LOOKUP 一个慢查询场景)
    return [{"id": "E3", "key": "e3", "source": "list_expensive_query_digests",
             "content": {"top": content_top, "query_ref": "INVENTORY_LOOKUP",
                         "max_latency_ms": round(max_latency_ms, 1)},
             "passed": expensive}]


def evaluate_plan(result: dict, state: dict) -> list[dict]:
    plan = (result.get("data") or {}).get("explain") if result.get("success") else None
    access_type = None
    try:
        access_type = plan["query_block"]["table"].get("access_type") if plan else None
    except (KeyError, TypeError, AttributeError):
        access_type = None
    e4 = result.get("success") and access_type == "ALL"
    return [{"id": "E4", "key": "e4", "source": "get_query_plan",
             "content": {"access_type": access_type}, "passed": e4}]


def evaluate_index(result: dict, state: dict) -> list[dict]:
    names = [i["index_name"] for i in ((result.get("data") or {}).get("indexes") or [])]
    e5 = result.get("success") and "idx_sku_warehouse" not in names
    return [{"id": "E5", "key": "e5", "source": "get_index_info",
             "content": {"indexes": names}, "passed": e5}]


class MissingIndexCapability(DiagnosticCapability):
    code = "mysql_missing_index"
    policy_key = "scn001"          # state["policy"] 兼容键(冻结)
    version = "1.0"
    root_cause_code = ROOT_CAUSE_INDEX
    required_fact_codes = ("F_ENDPOINT_DEGRADED", "F_DB_STAGE_DOMINANT",
                           "F_TARGET_QUERY_EXPENSIVE", "F_PLAN_FULL_SCAN", "F_INDEX_MISSING")
    exclusion_key = "x_index_normal"
    recovery_strategy = RECOVERY_VERIFY_TOOL
    tool_names = ("get_service_metrics", "get_trace", "list_expensive_query_digests",
                  "get_query_plan", "get_index_info")

    def __init__(self):
        self._evaluators = {
            "get_service_metrics": evaluate_metrics,
            "get_trace": evaluate_trace,
            "list_expensive_query_digests": evaluate_digests,
            "get_query_plan": evaluate_plan,
            "get_index_info": evaluate_index,
        }

    def extract_facts(self, evidence_map: dict) -> dict[str, bool]:
        out: dict[str, bool] = {}
        for ev_key, fact in _FACT_MAP.items():
            ev = evidence_map.get(ev_key)
            if ev is not None:
                passed = ev.get("passed")
                if passed is None:
                    continue   # V2.1-B closure:passed=None = unknown,不输出 Fact
                out[fact] = bool(passed)
        return out

    def exclusion(self, facts: dict[str, bool]) -> bool:
        """索引正向证据确定性缺失:索引已存在且执行计划未退化。"""
        return (facts.get("F_INDEX_MISSING") is False
                and facts.get("F_PLAN_FULL_SCAN") is False)

    def initial_hypothesis(self) -> dict | None:
        return {"id": "h1", "status": "proposed",
                "description": "缺少联合索引 idx_sku_warehouse(sku_id, warehouse_id) 导致慢查询"}
