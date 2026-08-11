import json
import logging
import time

logger = logging.getLogger(__name__)

_time = time  # 混合循环内部使用,避免与函数参数名冲突

from langgraph.types import interrupt

from app.agent.llm import get_llm
from app.agent.rules import EVIDENCE_TOOL_MAP, evaluate_evidence_gate
from app.agent.state import IncidentState
from app.repositories import approval_repo, evidence_repo, hypothesis_repo
from app.repositories import event_repo, incident_repo, postmortem_repo, proposal_repo
from app.services import fix_service
from app.mcp.client import get_mcp_client
from app.tools.execute import execute_tool

# 固定探测参数(INVENTORY_LOOKUP 白名单模板)
PROBE_PARAMS = {"skuId": 42, "warehouseId": 7}
DEFAULT_MAX_ROUNDS = 5
DEFAULT_MAX_TOOL_CALLS = 25

# 证据未齐且预算未耗尽时,每轮等待时间(让故障负载在观测窗口产生数据)
EVIDENCE_RETRY_SLEEP_SECONDS = 2

# 基线缺失时的宽松判定阈值(ms):仅当健康基线采集失败时使用
FALLBACK_E1_P95_MS = 100


def _call_tool(state: IncidentState, tool: str, **kwargs) -> dict:
    # 上下文(incident_id/agent_run_id)由 MCP Client 注入;kwargs 中出现的一律剔除(防伪造)
    kwargs.pop("incident_id", None)
    kwargs.pop("agent_run_id", None)
    result = get_mcp_client().call_tool(
        tool, incident_id=state.get("incident_id", 0),
        agent_run_id=state.get("run_id", 0), **kwargs)
    state["tool_call_count"] = state.get("tool_call_count", 0) + 1
    return result


def _emit_status(state: IncidentState) -> None:
    """状态变化事件落库(SSE 实时展示与审计)。"""
    event_repo.append_event(state["incident_id"], "status_changed",
                            {"status": state.get("status")})


def _emit_degradation(state: IncidentState, kind: str) -> None:
    """llm_degraded / rag_degraded / rag_recovered SSE 事件。"""
    event_repo.append_event(state["incident_id"], kind, {"run_id": state.get("run_id")})


def _append_evidence(state: IncidentState, key: str, source: str, content: dict,
                     passed: bool) -> None:
    evidence = state.setdefault("evidence", [])
    evidence.append({
        "id": key,  # E1/E2/... 作为证据 id 去重
        "source": source,
        "content": content,
        "passed": passed,
    })


def collect_evidence(state: IncidentState, llm=None, tools=None) -> dict:
    """混合循环:LLM 选工具(或确定性规划器)→ 程序校验/解析/去重/执行 → 更新闸门。
    返回增量 dict 由 LangGraph reducer 合并;llm/tools 以参数注入便于单测。"""
    from app.agent.determinism import DeterministicEvidencePlanner
    from app.agent.llm import ModelDegradedError, get_llm
    from app.agent.tool_calling import (MAX_CONSECUTIVE_INVALID, MAX_CONSECUTIVE_NO_PROGRESS,
                                        MAX_DECISION_ATTEMPTS, MAX_DURATION_SECONDS,
                                        MAX_TOOL_EXECUTIONS, ArgumentResolutionError,
                                        DuplicateGuard, compute_eligible_tools,
                                        resolve_arguments, validate_tool_call)

    llm = llm if llm is not None else get_llm()
    tools = tools if tools is not None else _execute_with_evidence
    planner = DeterministicEvidencePlanner()
    if getattr(llm, "degraded", False):
        _emit_degradation(state, "llm_degraded")

    gate = state.get("evidence_gate") or {}
    if evaluate_evidence_gate(gate):
        return {}

    now = _time.time()
    started = state.get("investigation_started_at") or now
    if now - started > MAX_DURATION_SECONDS:
        return {"status": "needs_human", "termination_reason": "investigation_timeout",
                "investigation_started_at": started}

    decision = (state.get("decision_attempt_count") or 0) + 1
    if decision > MAX_DECISION_ATTEMPTS:
        return {"status": "needs_human", "termination_reason": "decision_budget_exhausted",
                "decision_attempt_count": decision, "investigation_started_at": started}

    eligible = compute_eligible_tools(state)
    prompt = _build_collect_prompt(state, eligible)
    try:
        if hasattr(llm, "select_tool"):
            calls = llm.select_tool(state, prompt, eligible)
        else:
            # FakeLLM/确定性路径:规划器按 E1→E5 顺序补缺失证据(不伪装成模型)
            calls = planner.choose(state, eligible)
    except ModelDegradedError:
        # real_strict 模型失败:优雅转 needs_human,不让调查崩溃
        return {"status": "needs_human", "termination_reason": "llm_unavailable",
                "investigation_started_at": state.get("investigation_started_at")}

    out = {"decision_attempt_count": decision,
           "investigation_started_at": started,
           "consecutive_invalid_count": 0,
           "consecutive_no_progress_count": 0,
           "tool_execution_count": state.get("tool_execution_count") or 0}

    if not calls:
        noop = (state.get("consecutive_no_progress_count") or 0) + 1
        if noop >= MAX_CONSECUTIVE_NO_PROGRESS:
            return {**out, "status": "needs_human", "termination_reason": "no_progress",
                    "consecutive_no_progress_count": noop}
        return {**out, "consecutive_no_progress_count": noop}

    if len(calls) > 1:
        inv = (state.get("consecutive_invalid_count") or 0) + 1
        if inv >= MAX_CONSECUTIVE_INVALID:
            return {**out, "status": "needs_human", "termination_reason": "multi_tool_call_rejected",
                    "consecutive_invalid_count": inv}
        return {**out, "consecutive_invalid_count": inv}

    tc = calls[0]
    name, raw_args = tc.get("name", ""), tc.get("arguments", {}) or {}
    err = validate_tool_call(name, raw_args, eligible)
    if err:
        inv = (state.get("consecutive_invalid_count") or 0) + 1
        if inv >= MAX_CONSECUTIVE_INVALID:
            return {**out, "status": "needs_human", "termination_reason": "invalid_tool_decision",
                    "consecutive_invalid_count": inv}
        return {**out, "consecutive_invalid_count": inv}

    try:
        resolved = resolve_arguments(name, raw_args, state)
    except ArgumentResolutionError:
        inv = (state.get("consecutive_invalid_count") or 0) + 1
        if inv >= MAX_CONSECUTIVE_INVALID:
            return {**out, "status": "needs_human", "termination_reason": "argument_resolution_failed",
                    "consecutive_invalid_count": inv}
        return {**out, "consecutive_invalid_count": inv}

    guard = DuplicateGuard()
    for rec in state.get("tool_calls_record") or []:
        guard.seed(rec)
    dup, _ = guard.check(name, resolved)
    if dup:
        inv = (state.get("consecutive_invalid_count") or 0) + 1
        if inv >= MAX_CONSECUTIVE_INVALID:
            return {**out, "status": "needs_human", "termination_reason": "duplicate_tool_call",
                    "consecutive_invalid_count": inv}
        return {**out, "consecutive_invalid_count": inv}

    exec_count = (state.get("tool_execution_count") or 0) + 1
    if exec_count > MAX_TOOL_EXECUTIONS:
        return {**out, "status": "needs_human", "termination_reason": "execution_budget_exhausted",
                "tool_execution_count": exec_count}

    result = tools(state, name, resolved)
    out["tool_execution_count"] = exec_count
    record = {"tool_name": name, "arguments": resolved}
    if result.get("ok") and result.get("evidence"):
        new_evidence = result["evidence"]
        new_gate = dict(gate)
        for ev in new_evidence:
            new_gate[ev.get("id", "").upper()] = bool(ev.get("passed"))
        # 审计落库:证据写 evidence 表(按 source 幂等覆盖)
        for ev in new_evidence:
            evidence_repo.upsert_evidence(state["incident_id"], ev.get("id") or ev.get("key"),
                                          ev["source"], ev.get("content"),
                                          bool(ev.get("passed")))
        return {**out, "evidence": new_evidence, "evidence_gate": new_gate,
                "tool_calls_record": [record], "consecutive_no_progress_count": 0}

    # 工具成功但无证据(或执行失败):noop 计数
    noop = (state.get("consecutive_no_progress_count") or 0) + 1
    if noop >= MAX_CONSECUTIVE_NO_PROGRESS:
        return {**out, "status": "needs_human", "termination_reason": "no_progress",
                "consecutive_no_progress_count": noop}
    return {**out, "consecutive_no_progress_count": noop, "tool_calls_record": [record]}


def _build_collect_prompt(state: dict, eligible: set[str]) -> str:
    hyps = "\n".join(f"- [{h.get('status', '?')}] {h.get('description', '')}"
                     for h in state.get("hypotheses") or []) or "(无)"
    evidence = "\n".join(f"- {e.get('id', e.get('key'))}: passed={e.get('passed')}"
                         for e in state.get("evidence") or []) or "(无)"
    return (
        "你是故障调查 Agent。根据当前假设和已有证据,从可用工具中选择**一个**下一步要调用的工具。\n"
        "你必须选择一个可用工具并调用它;禁止输出文字解释或放弃调用。仅当证据已完全足够时才不做调用。\n"
        f"当前假设:\n{hyps}\n已有证据:\n{evidence}\n"
        f"可用工具(只能选这些):{', '.join(sorted(eligible))}\n"
        "只输出一个 tool_call。"
    )


def _evaluate_metrics(result: dict, state: dict) -> list[dict]:
    data = result.get("data") or {}
    p95 = data.get("p95Ms")
    inc = incident_repo.get_incident(state["incident_id"])
    health = (inc.healthy_metrics_baseline or {}) if inc else {}
    base_p95 = (health or {}).get("p95_ms")
    if p95 is not None and base_p95 is not None:
        e1 = p95 > int(base_p95) * 1.2
    else:
        e1 = p95 is not None and p95 > FALLBACK_E1_P95_MS
    content: dict = {"p95Ms": p95}
    if data.get("representativeSlowTraceId"):
        content["representativeSlowTraceId"] = data["representativeSlowTraceId"]
    return [{"id": "E1", "key": "e1", "source": "get_service_metrics",
             "content": content, "passed": e1}]


def _evaluate_trace(result: dict, state: dict) -> list[dict]:
    if not result.get("success"):
        return [{"id": "E2", "key": "e2", "source": "get_trace",
                 "content": {"detail": "no slow trace"}, "passed": False}]
    inv = (result.get("data") or {}).get("inventory_service") or []
    db_stage = next((x for x in inv if x.get("stage") == "database"), None)
    total_stage = next((x for x in inv if x.get("stage") == "total"), None)
    e2 = bool(db_stage and total_stage
              and db_stage.get("durationMs", 0) >= total_stage.get("durationMs", 1) * 0.5)
    return [{"id": "E2", "key": "e2", "source": "get_trace",
             "content": {"stages": inv}, "passed": e2}]


def _evaluate_digests(result: dict, state: dict) -> list[dict]:
    digests = (result.get("data") or []) if result.get("success") else []
    top = digests[0] if digests else {}
    e3 = result.get("success") and top.get("rows_examined_delta", 0) > 1000
    # 单场景:高扫描行数的 digest 即目标查询(系统内只有 INVENTORY_LOOKUP 一个慢查询场景)
    return [{"id": "E3", "key": "e3", "source": "list_expensive_query_digests",
             "content": {"top": top, "query_ref": "INVENTORY_LOOKUP"}, "passed": e3}]


def _evaluate_plan(result: dict, state: dict) -> list[dict]:
    plan = (result.get("data") or {}).get("explain") if result.get("success") else None
    access_type = None
    try:
        access_type = plan["query_block"]["table"].get("access_type") if plan else None
    except (KeyError, TypeError, AttributeError):
        access_type = None
    e4 = result.get("success") and access_type == "ALL"
    return [{"id": "E4", "key": "e4", "source": "get_query_plan",
             "content": {"access_type": access_type}, "passed": e4}]


def _evaluate_index(result: dict, state: dict) -> list[dict]:
    names = [i["index_name"] for i in ((result.get("data") or {}).get("indexes") or [])]
    e5 = result.get("success") and "idx_sku_warehouse" not in names
    return [{"id": "E5", "key": "e5", "source": "get_index_info",
             "content": {"indexes": names}, "passed": e5}]


_EVALUATORS = {
    "get_service_metrics": _evaluate_metrics,
    "get_trace": _evaluate_trace,
    "list_expensive_query_digests": _evaluate_digests,
    "get_query_plan": _evaluate_plan,
    "get_index_info": _evaluate_index,
}


def _execute_with_evidence(state: dict, name: str, args: dict) -> dict:
    """执行工具 + 单工具证据判定;返回 {"ok": bool, "evidence": [..]}。"""
    result = _call_tool(state, name, **args)
    evaluator = _EVALUATORS.get(name)
    if evaluator is None:
        return {"ok": False, "evidence": [], "error": f"无评估器 {name}"}
    evidence = evaluator(result, state)
    if not result.get("success"):
        return {"ok": False, "evidence": evidence, "error": result.get("error_message", "tool_failed")}
    return {"ok": True, "evidence": evidence}


def diagnose(state: IncidentState) -> dict:
    """汇总证据 E1~E5,规则闸门决定是否确认根因。"""
    if state.get("status") == "needs_human":
        # collect_evidence 已决定转人工(预算/无效决策/去重/超时),保留并补发事件
        _emit_status(state)
        incident_repo.update_state(state["incident_id"], status="needs_human",
                                   termination_reason=state.get("termination_reason"))
        return state
    gate = state.get("evidence_gate") or {}
    if evaluate_evidence_gate(gate):
        state["confirmed_hypothesis_id"] = "h1"
        state["status"] = "investigating"
        state["termination_reason"] = None
        for h in state.get("hypotheses", []):
            hypothesis_repo.upsert_hypothesis(state["incident_id"],
                                              h.get("description", ""), "confirmed")
    else:
        # 证据不足:预算耗尽 -> needs_human;否则回到 collect_evidence(条件边)
        if state.get("termination_reason") == "evidence_budget_exhausted":
            state["status"] = "needs_human"
            _emit_status(state)
            incident_repo.update_state(state["incident_id"], status="needs_human",
                                       termination_reason=state.get("termination_reason"))
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
    _emit_status(state)
    return state


def hypothesize(state: IncidentState) -> dict:
    """调用 LLM 生成初始假设列表,写入 hypotheses 并进入调查。
    real_strict 模型失败:优雅转 needs_human(llm_unavailable),不让调查崩溃。"""
    from app.agent.llm import ModelDegradedError
    llm = get_llm()
    try:
        hyps = llm.hypothesize(state)
    except ModelDegradedError:
        state["status"] = "needs_human"
        state["termination_reason"] = "llm_unavailable"
        _emit_status(state)
        return state
    state["hypotheses"] = hyps
    # 审计落库:假设写 hypothesis 表(幂等)
    for h in hyps:
        hypothesis_repo.upsert_hypothesis(state["incident_id"],
                                          h.get("description", ""), "proposed")
    state["status"] = "investigating"
    return state


def propose_fix(state: IncidentState) -> dict:
    """根因确认后生成修复提案并落库,同时创建待审批记录,状态进入 awaiting_approval。
    V1.1:提案完全确定性(fix_registry.build_proposal),零 LLM 调用。"""
    from app.agent.fix_registry import build_proposal
    fix = build_proposal(state)
    proposal = proposal_repo.create_proposal(
        incident_id=state["incident_id"],
        action_type=fix["action_type"],
        risk_level=fix["risk_level"],
        parameters=fix["parameters"],
        parameters_hash=fix["parameters_hash"],
        reason=fix.get("reason"),
    )
    approval = approval_repo.create_approval(
        incident_id=state["incident_id"],
        fix_proposal_id=proposal.id,
        action_type=fix["action_type"],
        parameters_hash=fix["parameters_hash"],
    )
    state["fix_proposal"] = {
        "fix_proposal_id": proposal.id,
        "action_type": fix["action_type"],
        "risk_level": fix["risk_level"],
        "parameters": fix["parameters"],
        "parameters_hash": fix["parameters_hash"],
        "reason": fix.get("reason"),
    }
    state["approval"] = {
        "approval_id": approval.id,
        "status": "pending",
        "fix_proposal_id": proposal.id,
    }
    state["status"] = "awaiting_approval"
    _emit_status(state)
    return state


def report(state: IncidentState, llm=None) -> dict:
    """终态复盘:调用 LLM 用已落库事实生成报告并写 postmortem 表。
    V1.1:报告阶段失败不推翻已恢复状态 — report.status=failed + degraded 标记。"""
    from app.agent.llm import ModelDegradedError, get_llm
    llm = llm if llm is not None else get_llm()
    try:
        result = llm.write_report(state)
        content = {"status": "ready", **result}
        postmortem_repo.create_postmortem(incident_id=state["incident_id"], content=content)
        event_repo.append_event(state["incident_id"], "incident_finished",
                                {"status": state.get("status")})
        state["report"] = content
        state["degraded"] = False
        incident_repo.update_state(state["incident_id"], degraded=False)
        return state
    except ModelDegradedError:
        # real_strict:报告阶段失败不推翻 recovered;标记 report.failed
        _emit_degradation(state, "llm_degraded")
        state["report"] = {"status": "failed", "content": ""}
        state["degraded"] = True
        state["degradation_reasons"] = [*(state.get("degradation_reasons") or []),
                                        "report_generation_failed"]
        incident_repo.update_state(state["incident_id"], degraded=True,
                                   degradation_reasons=state["degradation_reasons"])
        return state
    except Exception as exc:  # noqa: BLE001 兜底
        logger.warning("报告生成异常: %s", exc)
        state["report"] = {"status": "failed", "content": ""}
        state["degraded"] = True
        state["degradation_reasons"] = [*(state.get("degradation_reasons") or []),
                                        "report_generation_failed"]
        incident_repo.update_state(state["incident_id"], degraded=True,
                                   degradation_reasons=state["degradation_reasons"])
        return state


def human_approval(state: IncidentState) -> dict:
    """审批挂起:interrupt 等待决策;resume 后按决策分流(记录由 propose_fix 预创建)。"""
    proposal = state.get("fix_proposal") or {}
    approval = state["approval"]  # propose_fix 已创建

    decision = interrupt({
        "type": "approval_request",
        "approval_id": approval["approval_id"],
        "proposal": proposal,
    })

    # resume 分支:决策由 API/scanner 在恢复前已写入 approval 表
    if decision.get("decision") == "approved":
        approval["status"] = "approved"
        state["status"] = "executing"
    else:
        approval["status"] = decision.get("decision", "rejected")
        state["status"] = "rejected"
        state["termination_reason"] = decision.get("comment") or "rejected_by_approver"
    _emit_status(state)
    return state


def execute_fix(state: IncidentState) -> dict:
    """执行审批后的预定义修复(唯一业务写路径,含六项校验)。"""
    proposal = state.get("fix_proposal") or {}
    approval = state.get("approval") or {}
    state["status"] = "executing"
    try:
        result = fix_service.execute_fix(
            incident_id=state["incident_id"],
            fix_proposal_id=proposal.get("fix_proposal_id"),
            approval_id=approval.get("approval_id"),
        )
    except ValueError as exc:
        state["status"] = "failed"
        state["error"] = str(exc)
        _emit_status(state)
        return state
    state["fix_execution"] = {
        "fix_execution_id": result.get("fix_execution_id"),
        "status": result.get("status"),
    }
    state["status"] = "executing"
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
    _emit_status(state)
    return state
