import json

from app.agent.llm import get_llm
from app.agent.rules import EVIDENCE_TOOL_MAP, evaluate_evidence_gate
from app.agent.state import IncidentState
from app.repositories import postmortem_repo, proposal_repo
from app.tools.execute import execute_tool

# 固定探测参数(INVENTORY_LOOKUP 白名单模板)
PROBE_PARAMS = {"skuId": 42, "warehouseId": 7}
DEFAULT_MAX_ROUNDS = 3
DEFAULT_MAX_TOOL_CALLS = 12

HEALTHY_BASELINE_P95 = 10  # 健康态基线(ms),用于恢复判定;M4 改为 Incident 记录的真实基线


def _call_tool(state: IncidentState, tool: str, **kwargs) -> dict:
    kwargs.pop("incident_id", None)  # incident_id 由 execute_tool 统一注入/审计
    result = execute_tool(tool, incident_id=state.get("incident_id"), **kwargs)
    state["tool_call_count"] = state.get("tool_call_count", 0) + 1
    return result


def _append_evidence(state: IncidentState, key: str, source: str, content: dict,
                     passed: bool) -> None:
    evidence = state.setdefault("evidence", [])
    evidence.append({
        "id": key,  # E1/E2/... 作为证据 id 去重
        "source": source,
        "content": content,
        "passed": passed,
    })


def collect_evidence(state: IncidentState) -> dict:
    """调查预算内依次调用五个只读调查工具,产出 E1~E5 判定与 evidence。"""
    state["investigation_round"] = state.get("investigation_round", 0) + 1
    status = {}

    # E1: 目标服务 P95 相对健康基线异常
    r1 = _call_tool(state, "get_service_metrics",
                    service_ref=state["service_ref"], window_seconds=300)
    p95 = (r1.get("data") or {}).get("p95Ms")
    e1 = r1["success"] and p95 is not None and p95 > HEALTHY_BASELINE_P95
    _append_evidence(state, "E1", "get_service_metrics", {"p95Ms": p95}, e1)
    status["E1"] = e1
    slow_trace = (r1.get("data") or {}).get("representativeSlowTraceId")

    # E2: 代表性慢请求耗时位于 inventory 数据库阶段
    e2 = False
    if slow_trace:
        r2 = _call_tool(state, "get_trace", trace_id=slow_trace)
        if r2["success"]:
            inv = (r2.get("data") or {}).get("inventory_service") or []
            db_stage = next((x for x in inv if x.get("stage") == "database"), None)
            total_stage = next((x for x in inv if x.get("stage") == "total"), None)
            if db_stage and total_stage:
                e2 = db_stage.get("durationMs", 0) >= total_stage.get("durationMs", 1) * 0.5
            _append_evidence(state, "E2", "get_trace", {"stages": inv}, e2)
    if not e2:
        _append_evidence(state, "E2", "get_trace", {"detail": "no slow trace"}, e2)
    status["E2"] = e2

    # E3: 目标 SQL 扫描行数/耗时异常(digest 增量)
    r3 = _call_tool(state, "list_expensive_query_digests", incident_id=state["incident_id"])
    digests = (r3.get("data") or []) if r3["success"] else []
    top = digests[0] if digests else {}
    e3 = r3["success"] and top.get("rows_examined_delta", 0) > 1000
    _append_evidence(state, "E3", "list_expensive_query_digests", {"top": top}, e3)
    status["E3"] = e3

    # E4: 执行计划全表扫描或未命中目标索引
    r4 = _call_tool(state, "get_query_plan", query_ref="INVENTORY_LOOKUP",
                    sample_parameters=PROBE_PARAMS)
    plan = (r4.get("data") or {}).get("explain") if r4["success"] else None
    access_type = None
    try:
        access_type = plan["query_block"]["table"].get("access_type") if plan else None
    except (KeyError, TypeError, AttributeError):
        access_type = None
    e4 = r4["success"] and access_type == "ALL"
    _append_evidence(state, "E4", "get_query_plan", {"access_type": access_type}, e4)
    status["E4"] = e4

    # E5: 索引元数据确认联合索引缺失
    r5 = _call_tool(state, "get_index_info", table_ref="inventory")
    names = [i["index_name"] for i in ((r5.get("data") or {}).get("indexes") or [])]
    e5 = r5["success"] and "idx_sku_warehouse" not in names
    _append_evidence(state, "E5", "get_index_info", {"indexes": names}, e5)
    status["E5"] = e5

    state["evidence_gate"] = status  # type: ignore[assignment]

    # 预算耗尽判断
    if (state.get("investigation_round", 0) >= state.get("max_investigation_rounds", DEFAULT_MAX_ROUNDS)
            or state.get("tool_call_count", 0) >= state.get("max_tool_calls", DEFAULT_MAX_TOOL_CALLS)):
        state["termination_reason"] = "evidence_budget_exhausted"
    return state


def diagnose(state: IncidentState) -> dict:
    """汇总证据 E1~E5,规则闸门决定是否确认根因。"""
    gate = state.get("evidence_gate") or {}
    if evaluate_evidence_gate(gate):
        state["confirmed_hypothesis_id"] = "h1"
        state["status"] = "investigating"
        state["termination_reason"] = None
    else:
        # 证据不足:预算耗尽 -> needs_human;否则回到 collect_evidence(条件边)
        if state.get("termination_reason") == "evidence_budget_exhausted":
            state["status"] = "needs_human"
        else:
            state["status"] = "investigating"  # 继续循环
    return state


def ingest(state: IncidentState) -> dict:
    """初始化调查预算与状态(Incident 已在 API 层创建)。"""
    state.setdefault("investigation_round", 0)
    state.setdefault("max_investigation_rounds", DEFAULT_MAX_ROUNDS)
    state.setdefault("tool_call_count", 0)
    state.setdefault("max_tool_calls", DEFAULT_MAX_TOOL_CALLS)
    state["status"] = "investigating"
    return state


def hypothesize(state: IncidentState) -> dict:
    """调用 LLM 生成初始假设列表,写入 hypotheses 并进入调查。"""
    llm = get_llm()
    hyps = llm.hypothesize(state)
    state["hypotheses"] = hyps
    state["status"] = "investigating"
    return state


def propose_fix(state: IncidentState) -> dict:
    """根因确认后生成修复提案并落库,状态进入 awaiting_approval。"""
    llm = get_llm()
    fix = llm.propose_fix(state)
    proposal = proposal_repo.create_proposal(
        incident_id=state["incident_id"],
        action_type=fix["action_type"],
        risk_level=fix["risk_level"],
        parameters=fix["parameters"],
        parameters_hash=fix["parameters_hash"],
        reason=fix.get("reason"),
    )
    state["fix_proposal"] = {
        "fix_proposal_id": proposal.id,
        "action_type": fix["action_type"],
        "risk_level": fix["risk_level"],
        "parameters": fix["parameters"],
        "parameters_hash": fix["parameters_hash"],
        "reason": fix.get("reason"),
    }
    state["status"] = "awaiting_approval"
    return state


def report(state: IncidentState) -> dict:
    """终态复盘:调用 LLM 用已落库事实生成报告并写 postmortem 表。"""
    llm = get_llm()
    content = llm.write_report(state)
    postmortem_repo.create_postmortem(incident_id=state["incident_id"], content=content)
    state["report"] = content
    # status 保持前序终态(recovered / needs_human / rejected 等)
    return state


def verify_recovery_node(state: IncidentState) -> dict:
    """调用 verify_recovery 工具并按规则判定恢复(骨架,完整三批探测在 Task 3.4 后接)。"""
    fix_execution_id = (state.get("fix_execution") or {}).get("fix_execution_id")
    if not fix_execution_id:
        state["status"] = "failed"
        state["error"] = "missing fix_execution_id"
        return state
    result = _call_tool(state, "verify_recovery",
                        incident_id=state["incident_id"],
                        fix_execution_id=fix_execution_id)
    if result["success"] and result["data"].get("status") == "recovered":
        state["recovery"] = result["data"]
        state["status"] = "recovered"
    else:
        state["recovery"] = result.get("data") or {"status": "not_recovered"}
        state["status"] = "needs_human"
        state["termination_reason"] = "recovery_failed"
    return state
