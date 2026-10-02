import json
import logging
import time

logger = logging.getLogger(__name__)

_time = time  # 混合循环内部使用,避免与函数参数名冲突

from langgraph.types import interrupt

from app.agent.llm import get_llm
from app.capabilities import registry as capability_registry
from app.agent.state import IncidentState
from app.repositories import approval_repo, evidence_repo, hypothesis_repo
from app.repositories import event_repo, incident_repo, postmortem_repo, proposal_repo
from app.services import fix_service
from app.mcp.client import get_mcp_client
from app.tools.execute import execute_tool

from app.replay.snapshot import ReplaySnapshotFactory
from app.replay.writer import ReplayWriter, STEP_TYPES

_snapshot_factory = ReplaySnapshotFactory()
_writer_registry: dict[tuple[int, int], ReplayWriter] = {}


def replay_writer_for(incident_id: int, run_id: int) -> ReplayWriter:
    """(incident_id, run_id) → writer(测试可 monkeypatch)。"""
    return _writer_registry.setdefault((incident_id, run_id), ReplayWriter(incident_id, run_id))


def _snap(state: dict) -> dict:
    return _snapshot_factory.snapshot(state)


def _replay(state: dict, step_type: str, phase: str, *, logical_step_id: str,
            state_before: dict | None = None, state_after: dict | None = None,
            decision: dict | None = None, operation: dict | None = None,
            source_refs: dict | None = None, outcome: str | None = None,
            round_no: int | None = None, attempt_no: int = 1) -> None:
    """回放快照写入(防御:无 run_id 或写入失败不阻塞业务;失败仅告警)。"""
    import logging
    run_id = state.get("run_id")
    if not run_id:
        return
    try:
        writer = replay_writer_for(state["incident_id"], run_id)
        writer.write(step_type, phase, logical_step_id=logical_step_id,
                     attempt_no=attempt_no, round_no=round_no,
                     state_before=state_before, state_after=state_after,
                     decision=decision, operation=operation,
                     source_refs=source_refs, step_outcome=outcome)
    except Exception as e:  # 回放写入失败不阻塞调查;完整性检查标记 partial
        logging.getLogger("replay").warning("replay step 写入失败: %s", e)


def _tool_call_info_from(state: dict, name: str) -> tuple[str | None, int | None]:
    """查 tool_call 审计取最近一次该工具的 transport 与 id(回放溯源)。"""
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from app.db.engine import get_control_engine
    from app.db.models import ToolCall
    try:
        with Session(get_control_engine()) as s:
            rows = list(s.scalars(select(ToolCall).where(
                ToolCall.incident_id == state.get("incident_id"),
                ToolCall.tool_name == name).order_by(ToolCall.id.desc()).limit(1)).all())
        if rows:
            return rows[0].transport, rows[0].id
    except Exception:
        pass
    return None, None


def _decision_summary(name: str, state: dict) -> str:
    """结构化决策摘要(可审计的外部依据,非思维链)。"""
    gate = {k: v for k, v in (state.get("evidence_gate") or {}).items() if v}
    return (f"选择 {name} 补充缺失证据;当前已满足: {sorted(gate) or '无'}")


def _replay_node(step_type: str):
    """节点级回放包裹:进入捕获 before,返回捕获 after(before/after 快照由 _snap 生成)。"""
    import functools

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(state, *args, **kwargs):
            lid = (f"ls-{step_type.lower()}-"
                   f"{state.get('run_id') or state.get('incident_id')}")
            _replay(state, step_type, "started", logical_step_id=lid,
                    state_before=_snap(state),
                    source_refs={"businessKey": f"{step_type}:{state.get('incident_id')}"})
            out = fn(state, *args, **kwargs)
            merged = {**state, **(out if isinstance(out, dict) else {})}
            outcome = _node_outcome(step_type, merged)
            _replay(state, step_type, "completed", logical_step_id=lid,
                    state_after=_snap(merged), outcome=outcome)
            return out
        return wrapper
    return deco


def _node_outcome(step_type: str, merged: dict) -> str:
    if step_type == "DIAGNOSIS_EVALUATED":
        return "confirmed" if merged.get("confirmed_hypothesis_id") else (
            merged.get("termination_reason") or "evaluated")
    if step_type == "FIX_PROPOSED":
        return "proposal_created" if merged.get("fix_proposal") else "evaluated"
    if step_type == "REPORT_GENERATED":
        return "reported" if (merged.get("report") or merged.get("postmortem")) else "failed"
    return "succeeded"


DEFAULT_MAX_ROUNDS = 5
DEFAULT_MAX_TOOL_CALLS = 25

# 证据未齐且预算未耗尽时,每轮等待时间(让故障负载在观测窗口产生数据)
EVIDENCE_RETRY_SLEEP_SECONDS = 2


def _call_tool(state: IncidentState, tool: str, **kwargs) -> dict:
    # 上下文(incident_id/agent_run_id)由调用方注入;kwargs 中出现的一律剔除(防伪造)
    kwargs.pop("incident_id", None)
    kwargs.pop("agent_run_id", None)
    incident_id = state.get("incident_id", 0)
    agent_run_id = state.get("run_id", 0)
    from app.mcp.contract import TOOL_NAMES
    if tool in TOOL_NAMES:
        # 五个调查工具:完全走 MCP
        result = get_mcp_client().call_tool(tool, incident_id=incident_id,
                                            agent_run_id=agent_run_id, **kwargs)
    else:
        # 确定性安全控制节点(verify_recovery):内部直接调用,审计 internal_control
        result = execute_tool(tool, incident_id=incident_id, agent_run_id=agent_run_id,
                              transport="internal_control", **kwargs)
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
    # V1.3:双 policy 终止条件(设计 §4.4)——已可判定根因或需转人工时停止收集;
    # 仅 E1~E5 齐不代表收集完成(锁证据可能仍未知)
    pol = state.get("policy") or {}
    facts_dict = state.get("facts") or {}
    _root_cause, _reason = capability_registry.decide_root_cause(
        pol, capability_registry.evaluate_exclusions(facts_dict))
    if _root_cause or _reason:
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
    if len(eligible) == 1:
        # 确定性兜底:唯一可选项不依赖 LLM 自觉——真实模型可能在多轮里反复选已采集工具
        # (SCN-001 暴露:get_query_plan 唯一 eligible 时 LLM 仍可能返回重复调用 → duplicate_tool_call)
        calls = [{"id": "deterministic-single", "name": next(iter(eligible)),
                  "arguments": {}}]
    else:
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
    except ArgumentResolutionError as e:
        inv = (state.get("consecutive_invalid_count") or 0) + 1
        if inv >= MAX_CONSECUTIVE_INVALID:
            return {**out, "status": "needs_human", "termination_reason": "argument_resolution_failed",
                    "consecutive_invalid_count": inv}
        return {**out, "consecutive_invalid_count": inv}

    guard = DuplicateGuard()
    for rec in state.get("tool_calls_record") or []:
        guard.seed(rec)
    unique_hist = sorted({(r.get("tool_name"), str(r.get("arguments"))[:30]) for r in (state.get("tool_calls_record") or [])})
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

    # V1.5 回放:EVIDENCE_COLLECTION started(每轮一个逻辑步骤)
    replay_lid = f"ls-ev-{state.get('run_id') or state['incident_id']}-r{decision}"
    _replay(state, "EVIDENCE_COLLECTION", "started", logical_step_id=replay_lid,
            round_no=decision, state_before=_snap(state),
            decision={"eligibleTools": sorted(eligible), "selectedTool": name,
                      "decisionSummary": _decision_summary(name, state),
                      "validationResult": "accepted"},
            source_refs={"businessKey": f"evidence:{state['incident_id']}:{decision}"})
    _replay_state_before = _snap(state)
    result = tools(state, name, resolved)
    out["tool_execution_count"] = exec_count
    record = {"tool_name": name, "arguments": resolved}
    _last_tool_transport, _last_tool_call_id = _tool_call_info_from(state, name)
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
        # V1.3:每次工具返回后重算共享 Fact 与双 Policy(设计 §4.1/4.2)
        # 注意:必须基于全部已收集证据(含历史轮次),否则 policy 永远 unknown
        all_evidence = list(state.get("evidence") or []) + list(new_evidence)
        ev_map = {str(e.get("key") or e.get("id")).lower():
                  {"content": e.get("content"), "passed": e.get("passed")}
                  for e in all_evidence}
        new_facts = capability_registry.extract_facts(ev_map)
        new_policy = capability_registry.evaluate_policies(new_facts)
        out["facts"] = new_facts
        out["policy"] = new_policy
        transport, tool_call_id = _tool_call_info_from(state, name)
        _replay(state, "EVIDENCE_COLLECTION", "completed", logical_step_id=replay_lid,
                round_no=decision, state_after=_snap({**state, **out}),
                outcome="succeeded",
                operation={"toolName": name, "resolvedParameters": resolved,
                           "transport": transport or "unknown",
                           "resultStatus": "success"},
                source_refs={"toolCallId": tool_call_id,
                             "evidenceIds": [e.get("id") for e in new_evidence]})
        return {**out, "evidence": new_evidence, "evidence_gate": new_gate,
                "tool_calls_record": [record], "consecutive_no_progress_count": 0}

    # 工具成功但无证据(或执行失败):不记录到 tool_calls_record(允许后续重采),
    # 连续无进展达阈值才转人工
    noop = (state.get("consecutive_no_progress_count") or 0) + 1
    # get_trace 无可用 trace(OTel batch 导出延迟/锁超时 trace 未完成)是暂态:
    # 轮间等待导出后重采(execution 预算兜底),不累计 no_progress
    if name == "get_trace":
        noop = 0
        _time.sleep(EVIDENCE_RETRY_SLEEP_SECONDS)
    elif name == "list_expensive_query_digests":
        # digest 增量全 0 是暂态(故障负载尚未进入 performance_schema),等待重采
        noop = 0
        _time.sleep(EVIDENCE_RETRY_SLEEP_SECONDS)
    elif name == "get_lock_waiters":
        # 锁场景:锁等待未达阈值是暂态(等待累积中),等待重采
        noop = 0
        _time.sleep(EVIDENCE_RETRY_SLEEP_SECONDS)
    elif name == "get_transaction_details":
        # 锁场景:阻塞事务年龄未达阈值是暂态(累积中),等待重采
        noop = 0
        _time.sleep(EVIDENCE_RETRY_SLEEP_SECONDS)
    elif noop >= MAX_CONSECUTIVE_NO_PROGRESS and name != "get_service_metrics":
        return {**out, "status": "needs_human", "termination_reason": "no_progress",
                "consecutive_no_progress_count": noop}
    # get_service_metrics 空窗口是暂态(注入清空观测后负载尚未进入窗口):
    # 不计数 no_progress,轮间等待窗口产生数据后重采(execution 预算兜底)
    if name == "get_service_metrics":
        noop = 0
        _time.sleep(EVIDENCE_RETRY_SLEEP_SECONDS)
    transport, tool_call_id = _tool_call_info_from(state, name)
    _replay(state, "EVIDENCE_COLLECTION", "completed", logical_step_id=replay_lid,
            round_no=decision, state_after=_snap({**state, **out}),
            outcome="no_progress" if noop > 0 else "succeeded",
            operation={"toolName": name, "resolvedParameters": resolved,
                       "transport": transport or "unknown",
                       "resultStatus": "success" if result.get("ok") else "error"},
            source_refs={"toolCallId": tool_call_id,
                         "evidenceIds": [e.get("id") for e in (result.get("evidence") or [])]})
    return {**out, "consecutive_no_progress_count": noop}


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


def _execute_with_evidence(state: dict, name: str, args: dict) -> dict:
    """执行工具 + 单工具证据判定;返回 {"ok": bool, "evidence": [..]}。"""
    result = _call_tool(state, name, **args)
    evaluator = capability_registry.evaluator_for(name)
    if evaluator is None:
        return {"ok": False, "evidence": [], "error": f"无评估器 {name}"}
    evidence = evaluator(result, state)
    if not result.get("success"):
        return {"ok": False, "evidence": evidence, "error": result.get("error_message", "tool_failed")}
    return {"ok": True, "evidence": evidence}


@_replay_node("DIAGNOSIS_EVALUATED")
def diagnose(state: IncidentState) -> dict:
    """V1.3:按双 Policy 四分支判定(设计 §4.4)。"""
    if state.get("status") == "needs_human":
        # collect_evidence 已决定转人工(预算/无效决策/去重/超时),保留并补发事件
        _emit_status(state)
        incident_repo.update_state(state["incident_id"], status="needs_human",
                                   termination_reason=state.get("termination_reason"))
        return state
    facts_dict = state.get("facts") or {}
    pol = state.get("policy") or capability_registry.evaluate_policies(facts_dict)
    exclusions = capability_registry.evaluate_exclusions(facts_dict)
    root_cause, reason = capability_registry.decide_root_cause(pol, exclusions)
    if root_cause:
        state["confirmed_hypothesis_id"] = "h1"
        state["root_cause_code"] = root_cause
        state["status"] = "investigating"
        state["termination_reason"] = None
        for h in state.get("hypotheses", []):
            hypothesis_repo.upsert_hypothesis(state["incident_id"],
                                              h.get("description", ""), "confirmed")
        return state
    if reason:
        state["status"] = "needs_human"
        state["termination_reason"] = reason
        _emit_status(state)
        incident_repo.update_state(state["incident_id"], status="needs_human",
                                   termination_reason=reason)
        return state
    # 继续收集:预算耗尽才转 needs_human
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
    lid = f"ls-ingest-{state.get('run_id') or state['incident_id']}"
    _replay(state, "INCIDENT_INGESTED", "started", logical_step_id=lid,
            state_before=_snap(state),
            source_refs={"businessKey": f"ingest:{state['incident_id']}"})
    state.setdefault("investigation_round", 0)
    state.setdefault("max_investigation_rounds", DEFAULT_MAX_ROUNDS)
    state.setdefault("tool_call_count", 0)
    state.setdefault("max_tool_calls", DEFAULT_MAX_TOOL_CALLS)
    state["status"] = "investigating"
    _emit_status(state)
    _replay(state, "INCIDENT_INGESTED", "completed", logical_step_id=lid,
            state_after=_snap(state), outcome="succeeded")
    return state


@_replay_node("HYPOTHESES_GENERATED")
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


@_replay_node("FIX_PROPOSED")
def propose_fix(state: IncidentState) -> dict:
    """根因确认后生成修复提案并落库,同时创建待审批记录,状态进入 awaiting_approval。
    V1.1:提案完全确定性(fix_registry.build_proposal),零 LLM 调用。"""
    from app.agent.fix_registry import build_proposal
    try:
        fix = build_proposal(state)
    except ValueError as exc:
        # V2.0-A fail closed:未知/缺失根因不创建提案与审批,转人工(零写路径)
        state["status"] = "needs_human"
        state["termination_reason"] = "unknown_root_cause"
        state["error"] = str(exc)
        _emit_status(state)
        _replay(state, "FIX_PROPOSED", "failed",
                logical_step_id=f"ls-fixp-{state['incident_id']}",
                state_after=_snap(state), outcome="failed",
                decision={"rejectionRule": str(exc)},
                source_refs={"businessKey": f"fix:{state['incident_id']}"})
        return state
    proposal = proposal_repo.create_proposal(
        incident_id=state["incident_id"],
        action_type=fix["action_type"],
        risk_level=fix["risk_level"],
        parameters=fix["parameters"],
        parameters_hash=fix["parameters_hash"],
        reason=fix.get("reason"),
        blocking_relation_hash=fix.get("blocking_relation_hash"),
    )
    approval = approval_repo.create_approval(
        incident_id=state["incident_id"],
        fix_proposal_id=proposal.id,
        action_type=fix["action_type"],
        parameters_hash=fix["parameters_hash"],
        agent_run_id=state.get("run_id"),
    )
    state["fix_proposal"] = {
        "fix_proposal_id": proposal.id,
        "action_type": fix["action_type"],
        "risk_level": fix["risk_level"],
        "parameters": fix["parameters"],
        "parameters_hash": fix["parameters_hash"],
        "blocking_relation_hash": fix.get("blocking_relation_hash"),
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


@_replay_node("REPORT_GENERATED")
def report(state: IncidentState, llm=None) -> dict:
    """终态复盘:调用 LLM 用已落库事实生成报告并写 postmortem 表。
    V1.1:报告阶段失败不推翻已恢复状态 — report.status=failed + degraded 标记。"""
    from app.agent.llm import ModelDegradedError, get_llm
    llm = llm if llm is not None else get_llm()
    try:
        result = llm.write_report(state)
        content = {"status": "ready", **result}
        postmortem_repo.create_postmortem(incident_id=state["incident_id"], content=content)
        from app.agent.memory import record_case
        record_case(state)   # 仅 recovered 沉淀;失败不阻塞诊断
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
    """审批挂起:interrupt 等待决策;resume 后按决策分流(记录由 propose_fix 预创建)。
    V2.0-A:fail-closed 路径(无提案/未知根因)不经审批,直接交由路由进入 report。"""
    proposal = state.get("fix_proposal") or {}
    approval = state.get("approval")
    if state.get("status") != "awaiting_approval" or not approval:
        return state

    # V1.5 回放:APPROVAL_REQUESTED(进入审批挂起)
    run_id = state.get("run_id")
    lid = f"ls-req-{state['incident_id']}"
    _replay(state, "APPROVAL_REQUESTED", "completed", logical_step_id=lid,
            state_before=_snap(state), outcome="requested",
            decision={"actionType": proposal.get("action_type"),
                      "riskLevel": proposal.get("risk_level")},
            source_refs={"approval_id": approval.get("approval_id"),
                         "fix_proposal_id": proposal.get("fix_proposal_id"),
                         "businessKey": f"request:{state['incident_id']}"})

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
    """执行审批后的预定义修复(唯一业务写路径)。按 action_type 分发:
    CREATE_INVENTORY_INDEX → fix_service(六项校验);TERMINATE_BLOCKING_SESSION → session_terminator(8 项重查)。"""
    proposal = state.get("fix_proposal") or {}
    approval = state.get("approval") or {}
    state["status"] = "executing"
    # V1.5 回放:FIX_EXECUTED started(两段式,KILL 流程 started → 抢占 → completed/failed)
    replay_lid = f"ls-fix-{state['incident_id']}"
    _replay(state, "FIX_EXECUTED", "started", logical_step_id=replay_lid,
            state_before=_snap(state),
            decision={"actionType": proposal.get("action_type"),
                      "approvalId": approval.get("approval_id"),
                      "fixProposalId": proposal.get("fix_proposal_id")},
            source_refs={"approval_id": approval.get("approval_id"),
                         "fix_proposal_id": proposal.get("fix_proposal_id"),
                         "businessKey": f"fix:{state['incident_id']}"})
    if proposal.get("action_type") not in ("CREATE_INVENTORY_INDEX",
                                           "TERMINATE_BLOCKING_SESSION"):
        # V2.0-A fail closed:未知动作不得进入任何写执行器(原 else 分支默认走索引创建,已删除)
        state["status"] = "failed"
        state["error"] = f"unknown_action_type: {proposal.get('action_type')!r}"
        _emit_status(state)
        _replay(state, "FIX_EXECUTED", "failed", logical_step_id=replay_lid,
                state_after=_snap(state), outcome="failed",
                operation={"actionType": proposal.get("action_type"),
                           "rejectionRule": state["error"]})
        return state
    if proposal.get("action_type") == "TERMINATE_BLOCKING_SESSION":
        from app.services import session_terminator as st
        result = st.execute(proposal, approval,
                            incident_id=state["incident_id"],
                            baseline=state.get("healthy_baseline_ref"),
                            agent_run_id=state.get("run_id") or 0)
        if result["execution_result"] == "executed":
            fix_status = "succeeded"
        elif result["execution_result"] in ("already_resolved", "already_executed"):
            fix_status = "no_op"   # 安全无操作(事务已结束/幂等)
        elif result["execution_result"] == "rejected_preflight_self_healed":
            # V2.1-C:Preflight 拒绝(当前已自愈)→ 转人工,不做任何写操作
            fix_status = "failed"
            state["status"] = "needs_human"
            state["termination_reason"] = result.get("preflight_reason") or \
                "PREFLIGHT_ALREADY_RECOVERED"
        elif result["execution_result"] in ("target_changed", "evidence_stale",
                                            "rejected_not_approved", "rejected_expired",
                                            "rejected_forbidden_account",
                                            "rejected_system_thread", "invalid_target"):
            fix_status = "failed"
        else:
            fix_status = "failed"
        state["fix_execution"] = {
            "status": fix_status,
            "execution_result": result["execution_result"],
            "actual_processlist_id": result.get("actual_processlist_id"),
            "idempotency_key": proposal.get("parameters_hash"),
            # V2.1-C:完成时刻(锁恢复验证的信号时刻来源;应用侧 UTC)
            "created_at": _time.strftime("%Y-%m-%dT%H:%M:%S", _time.gmtime()),
        }
        # 审计落库:fix_execution 表(Task 9 落库;此处 stub 兼容测试)
        _record_fix_execution(state, proposal, approval, fix_status, result)
        _replay(state, "FIX_EXECUTED",
                "completed" if fix_status in ("succeeded", "no_op") else "failed",
                logical_step_id=replay_lid, state_after=_snap(state),
                outcome=fix_status,
                operation={"actionType": proposal.get("action_type"),
                           "killAttempted": bool(result.get("actual_processlist_id")),
                           "actualProcesslistId": result.get("actual_processlist_id"),
                           "executionResult": result["execution_result"]},
                source_refs={"fix_execution_id": state["fix_execution"].get("fix_execution_id")})
        return state
    try:
        result = fix_service.execute_fix(
            incident_id=state["incident_id"],
            fix_proposal_id=proposal.get("fix_proposal_id"),
            approval_id=approval.get("approval_id"),
        )
    except ValueError as exc:
        # V2.1-C:Preflight 拒绝(已自愈/前置条件变化)→ 转人工,不标记为执行失败
        if str(exc).startswith("PREFLIGHT_"):
            state["status"] = "needs_human"
            state["termination_reason"] = str(exc)
        else:
            state["status"] = "failed"
        state["error"] = str(exc)
        _emit_status(state)
        _replay(state, "FIX_EXECUTED", "failed", logical_step_id=replay_lid,
                state_after=_snap(state), outcome="failed",
                operation={"actionType": proposal.get("action_type"),
                           "rejectionRule": str(exc)})
        return state
    state["fix_execution"] = {
        "fix_execution_id": result.get("fix_execution_id"),
        "status": result.get("status"),
    }
    state["status"] = "executing"
    _replay(state, "FIX_EXECUTED", "completed", logical_step_id=replay_lid,
            state_after=_snap(state), outcome=result.get("status", "succeeded"),
            operation={"actionType": proposal.get("action_type"),
                       "resultStatus": result.get("status")},
            source_refs={"fix_execution_id": result.get("fix_execution_id")})
    return state


def _record_fix_execution(state: IncidentState, proposal: dict, approval: dict,
                          fix_status: str, result: dict) -> None:
    """fix_execution 审计落库(KILL 审计,V2.1-D 修复)。

    - 幂等键经 repo.build_idempotency_key 绑定 approval 维度(裸 parameters_hash
      会跨 Incident 冲突,曾导致审计静默丢失);
    - 写失败不再静默:落 audit_write_failed 事件(坐席时间线可见)+ 日志;
      审计失败绝不阻塞/重试处置动作本身(KILL 只执行一次,见 session_terminator);
    - duplicate(同幂等键重复审计)为正常幂等拦截,记日志即可。"""
    from app.repositories import fix_execution_repo
    try:
        out = fix_execution_repo.create_execution(
            incident_id=state["incident_id"],
            fix_proposal_id=proposal.get("fix_proposal_id"),
            approval_id=approval.get("approval_id"),
            idempotency_key=fix_execution_repo.build_idempotency_key(
                incident_id=state["incident_id"],
                fix_proposal_id=proposal.get("fix_proposal_id"),
                approval_id=approval.get("approval_id"),
                parameters_hash=proposal.get("parameters_hash")),
            blocking_relation_hash=proposal.get("blocking_relation_hash") or "",
            status=fix_status,
            execution_result=result.get("execution_result"),
            kill_attempted=bool(result.get("kill_attempted")),
            actual_processlist_id=result.get("actual_processlist_id"))
        if out.get("status") == "duplicate":
            logger.info("fix_execution 重复审计被幂等拦截(incident %s)",
                        state.get("incident_id"))
    except Exception as exc:  # noqa: BLE001  审计失败不阻塞处置,但必须可见
        logger.warning("fix_execution 审计写入失败(incident %s): %s",
                       state.get("incident_id"), exc)
        try:
            event_repo.append_event(state["incident_id"], "audit_write_failed",
                                    {"audit": "fix_execution",
                                     "error": str(exc)[:200]})
        except Exception:  # noqa: BLE001
            logger.exception("audit_write_failed 事件写入失败(incident %s)",
                             state.get("incident_id"))


@_replay_node("REFLECTION_EVALUATED")
def reflect(state: IncidentState) -> dict:
    """V1.10 反思节点:修复失败后调用 LLM 复盘证据链,生成修正策略。
    LLM 不可用/结构化输出失败 → needs_human(reflection_llm_unavailable),不阻塞、不崩溃。"""
    from app.agent.llm import ModelDegradedError
    llm = get_llm()
    try:
        data = llm.reflect(state)
    except Exception as exc:  # noqa: BLE001 反思失败降级
        logger.warning("反思失败(降级 needs_human): %s", exc)
        state["status"] = "needs_human"
        state["termination_reason"] = "reflection_llm_unavailable"
        _emit_status(state)
        return state
    entry = {
        "attempt_no": (state.get("reflection_count") or 0) + 1,
        "reason": data.get("root_cause_revisit", ""),
        "new_hypothesis": data.get("new_hypothesis", ""),
        "strategy_change": data.get("adjust_strategy", ""),
    }
    state["reflection_log"] = [*(state.get("reflection_log") or []), entry]
    state["reflection_count"] = (state.get("reflection_count") or 0) + 1
    return state


def resolved_recheck(state: IncidentState) -> dict:
    """V2.1-C:告警 resolved 复核(确定性;每轮证据评估后进入)。

    语义(方案 §V2.1 条目 9):
    - alert_status 非 RESOLVED → 直通(零额外开销);
    - 已执行写动作(FixExecution 存在)→ 不宣告自愈(写后恢复由恢复验证判定);
    - 未写动作:取**信号之后**的新鲜 HTTP P95(与告警同口径),已恢复 → SELF_RECOVERED;
      仍异常 → 继续调查;无法判定(信号后无流量/观测不可用)→ 不宣告自愈。
    决策与依据写 Replay(ALERT_RESOLVED_RECHECK)与 SSE(run.self_recovered)。
    """
    from datetime import datetime, timezone

    from app.services import recovery_signal

    incident_id = state["incident_id"]
    status = _incident_alert_status(incident_id)
    if status != "RESOLVED":
        return {}
    if _has_write_execution(incident_id):
        return {"resolved_recheck": {"status": "write_already_executed"}}
    signal_at = _alert_resolved_at(incident_id) or \
        datetime.now(timezone.utc).replace(tzinfo=None)
    signal = recovery_signal.measure_post_signal_p95(
        state.get("service_ref") or "", state.get("affected_operation_ref"),
        signal_at, baseline=state.get("healthy_baseline_ref") or None)
    detail = signal.as_dict()
    if signal.status == recovery_signal.STATUS_RECOVERED:
        detail["status"] = "self_recovered"
        _replay(state, "ALERT_RESOLVED_RECHECK", "completed",
                logical_step_id=f"ls-resolved-{incident_id}",
                state_after=_snap(state),
                decision={"alertStatus": "RESOLVED", "recoverySignal": detail,
                          "thresholdSource": signal.source},
                outcome="self_recovered")
        event_repo.append_event(incident_id, "run.self_recovered",
                                {"run_id": state.get("run_id"),
                                 "sampleCount": signal.sample_count,
                                 "thresholdSource": signal.source,
                                 "windowSeconds": signal.window_seconds})
        return {"status": "self_recovered", "termination_reason": None,
                "resolved_recheck": detail, "recovery": detail}
    detail["status"] = ("still_degraded"
                        if signal.status == recovery_signal.STATUS_NOT_RECOVERED
                        else "recheck_inconclusive")
    _replay(state, "ALERT_RESOLVED_RECHECK", "completed",
            logical_step_id=f"ls-resolved-{incident_id}",
            state_after=_snap(state),
            decision={"alertStatus": "RESOLVED", "recoverySignal": detail},
            outcome=detail["status"])
    return {"resolved_recheck": detail}


def _incident_alert_status(incident_id: int) -> str | None:
    """动态信号:告警生命周期状态(不参与冻结上下文,仅用于自愈复核)。"""
    from sqlalchemy import text as _text
    from sqlalchemy.orm import Session as _Session

    from app.db.engine import get_control_engine as _engine
    with _Session(_engine()) as s:
        return s.execute(_text("SELECT alert_status FROM incident WHERE id=:i"),
                         {"i": incident_id}).scalar()


def _alert_resolved_at(incident_id: int):
    """信号时刻 = 关联实例最近 resolved_at(自愈判定窗口的起点)。"""
    from sqlalchemy import text as _text
    from sqlalchemy.orm import Session as _Session

    from app.db.engine import get_control_engine as _engine
    with _Session(_engine()) as s:
        return s.execute(_text(
            "SELECT MAX(ai.resolved_at) FROM incident_alert ia "
            "JOIN alert_instance ai ON ai.alert_instance_key = ia.alert_instance_key "
            "WHERE ia.incident_id = :i"), {"i": incident_id}).scalar()


def _has_write_execution(incident_id: int) -> bool:
    """该 Incident 是否已有写动作执行记录(有则不再宣告自愈)。"""
    from sqlalchemy import text as _text
    from sqlalchemy.orm import Session as _Session

    from app.db.engine import get_control_engine as _engine
    with _Session(_engine()) as s:
        n = s.execute(_text("SELECT COUNT(*) FROM fix_execution "
                            "WHERE incident_id=:i AND status IN ('succeeded','no_op')"),
                      {"i": incident_id}).scalar()
    return bool(n)


@_replay_node("RECOVERY_VERIFIED")
def verify_recovery_node(state: IncidentState) -> dict:
    """恢复验证。按根因分发:恢复策略由 Capability Registry 提供
    (锁根因 → 目标范围六项验证;其他 → verify_recovery 工具路径)。"""
    verifier = capability_registry.recovery_verifier_for(state.get("root_cause_code") or "")
    if verifier is not None:
        return verifier(state)
    fix_execution_id = (state.get("fix_execution") or {}).get("fix_execution_id")
    if not fix_execution_id:
        state["status"] = "failed"
        state["error"] = "missing fix_execution_id"
        return state
    result = _call_tool(state, "verify_recovery",
                        incident_id=state["incident_id"],
                        fix_execution_id=fix_execution_id)
    data = result.get("data") or {}
    if result["success"] and data.get("status") == "recovered":
        state["recovery"] = data
        state["status"] = "recovered"
    elif data.get("status") == "INCONCLUSIVE":
        # V2.1-C:无法判定(信号后无流量/观测不可用)→ 转人工,不宣告恢复也不宣告失败
        state["recovery"] = data
        state["status"] = "needs_human"
        state["termination_reason"] = "recovery_inconclusive"
    else:
        state["recovery"] = data or {"status": "not_recovered"}
        state["status"] = "needs_human"
        state["termination_reason"] = "recovery_failed"
    _emit_status(state)
    return state
