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


# 固定探测参数(INVENTORY_LOOKUP 白名单模板)
PROBE_PARAMS = {"skuId": 42, "warehouseId": 7}
DEFAULT_MAX_ROUNDS = 5
DEFAULT_MAX_TOOL_CALLS = 25

# 证据未齐且预算未耗尽时,每轮等待时间(让故障负载在观测窗口产生数据)
EVIDENCE_RETRY_SLEEP_SECONDS = 2

# 基线缺失时的宽松判定阈值(ms):仅当健康基线采集失败时使用
FALLBACK_E1_P95_MS = 100


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
    from app.agent import policies as policies_mod
    pol = state.get("policy") or {}
    facts_dict = state.get("facts") or {}
    _root_cause, _reason = policies_mod.decide_root_cause(
        pol, policies_mod.evaluate_exclusions(facts_dict))
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
        from app.agent import facts as facts_mod, policies as policies_mod
        all_evidence = list(state.get("evidence") or []) + list(new_evidence)
        ev_map = {str(e.get("key") or e.get("id")).lower():
                  {"content": e.get("content"), "passed": e.get("passed")}
                  for e in all_evidence}
        new_facts = facts_mod.evaluate_facts(ev_map)
        new_policy = policies_mod.evaluate_policies(new_facts)
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


def _evaluate_metrics(result: dict, state: dict) -> list[dict]:
    data = result.get("data") or {}
    p95 = data.get("p95Ms")
    if p95 is None:
        # 窗口内无观测样本(如注入清空观测后负载尚未进入窗口):
        # 不产出证据,视为"尚未采集",允许 planner 后续轮次重采
        return []
    inc = incident_repo.get_incident(state["incident_id"])
    health = (inc.healthy_metrics_baseline or {}) if inc else {}
    base_p95 = (health or {}).get("p95_ms")
    if p95 is not None and base_p95 is not None:
        e1 = p95 > int(base_p95) * 1.2
    else:
        e1 = p95 is not None and p95 > FALLBACK_E1_P95_MS
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


def _evaluate_trace(result: dict, state: dict) -> list[dict]:
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


def _evaluate_digests(result: dict, state: dict) -> list[dict]:
    digests = (result.get("data") or []) if result.get("success") else []
    top = digests[0] if digests else {}
    # 暂态:故障负载尚未进入 performance_schema(增量全 0)时不产证据,触发重采
    # (真实后端验收暴露:digest 采集早于负载 → 增量 0 被误判为确定性否定)
    if not result.get("success") or top.get("rows_examined_delta", 0) <= 0:
        return []
    e3 = top.get("rows_examined_delta", 0) > 1000
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


def _evaluate_lock_waiters(result: dict, state: dict) -> list[dict]:
    """L1:目标 inventory 记录上的锁等待(等待语句匹配库存预占,wait_duration ≥ 3s)。"""
    data = result.get("data") or {}
    waits = data.get("waits") or []
    target = [w for w in waits
              if w.get("object_schema") == "tracemind_business"
              and w.get("object_table") == "inventory"
              and w.get("waiting_query_ref") == "INVENTORY_RESERVATION"]
    passed = bool(target and any((w.get("wait_duration_ms") or 0) >= 3000 for w in target))
    return [{"id": "L1", "key": "l1", "source": "get_lock_waiters",
             "content": data, "passed": passed}]


def _evaluate_transaction_details(result: dict, state: dict) -> list[dict]:
    """L2:阻塞事务详情(复合匹配见 facts/policies;此处只判定存在长事务)。"""
    data = result.get("data") or {}
    passed = bool(data.get("transaction_id") and (data.get("age_ms") or 0) >= 5000)
    return [{"id": "L2", "key": "l2", "source": "get_transaction_details",
             "content": data, "passed": passed}]


_EVALUATORS = {
    "get_service_metrics": _evaluate_metrics,
    "get_trace": _evaluate_trace,
    "list_expensive_query_digests": _evaluate_digests,
    "get_query_plan": _evaluate_plan,
    "get_index_info": _evaluate_index,
    "get_lock_waiters": _evaluate_lock_waiters,
    "get_transaction_details": _evaluate_transaction_details,
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


@_replay_node("DIAGNOSIS_EVALUATED")
def diagnose(state: IncidentState) -> dict:
    """V1.3:按双 Policy 四分支判定(设计 §4.4)。"""
    if state.get("status") == "needs_human":
        # collect_evidence 已决定转人工(预算/无效决策/去重/超时),保留并补发事件
        _emit_status(state)
        incident_repo.update_state(state["incident_id"], status="needs_human",
                                   termination_reason=state.get("termination_reason"))
        return state
    from app.agent import policies as policies_mod
    facts_dict = state.get("facts") or {}
    pol = state.get("policy") or policies_mod.evaluate_policies(facts_dict)
    exclusions = policies_mod.evaluate_exclusions(facts_dict)
    root_cause, reason = policies_mod.decide_root_cause(pol, exclusions)
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
    fix = build_proposal(state)
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
    if proposal.get("action_type") == "TERMINATE_BLOCKING_SESSION":
        from app.services import session_terminator as st
        result = st.execute(proposal, approval)
        if result["execution_result"] == "executed":
            fix_status = "succeeded"
        elif result["execution_result"] in ("already_resolved", "already_executed"):
            fix_status = "no_op"   # 安全无操作(事务已结束/幂等)
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
    """fix_execution 审计落库(表在 Task 9;此处 try/except 保证不阻塞处置闭环)。"""
    try:
        from app.repositories import fix_execution_repo
        fix_execution_repo.create_execution(
            incident_id=state["incident_id"],
            fix_proposal_id=proposal.get("fix_proposal_id"),
            approval_id=approval.get("approval_id"),
            idempotency_key=proposal.get("parameters_hash"),
            blocking_relation_hash=proposal.get("blocking_relation_hash") or "",
            status=fix_status,
            execution_result=result.get("execution_result"),
            kill_attempted=bool(result.get("kill_attempted")),
            actual_processlist_id=result.get("actual_processlist_id"))
    except Exception:  # noqa: BLE001  审计失败不阻塞处置
        pass


@_replay_node("RECOVERY_VERIFIED")
def verify_recovery_node(state: IncidentState) -> dict:
    """恢复验证。按根因分发:锁根因 → 目标范围六项验证(设计 V1.3 §6);
    其他 → 原有 verify_recovery 工具路径。"""
    if state.get("root_cause_code") == "LONG_RUNNING_TRANSACTION_BLOCKING_INVENTORY_RESERVATION":
        return _verify_lock_recovery(state)
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


def _verify_lock_recovery(state: IncidentState) -> dict:
    """锁根因恢复验证(六项目标范围,设计 §6):
    轮询目标锁等待关系消失(≤60s)→ 连续三批库存预占探测 → recovered / needs_human(recovery_timeout)。"""
    import time
    from app.tools import lock_queries
    deadline = _time.time() + 60  # 轮询截止 N=60s
    target_gone = False
    while _time.time() < deadline:
        r = lock_queries.get_lock_waiters("tracemind_business", "inventory", 3000)
        waits = (r.get("data") or {}).get("waits") or []
        target = [w for w in waits
                  if w.get("object_schema") == "tracemind_business"
                  and w.get("object_table") == "inventory"
                  and w.get("waiting_query_ref") == "INVENTORY_RESERVATION"]
        if not target:
            target_gone = True
            break
        time.sleep(5)
    if not target_gone:
        state["recovery"] = {"status": "needs_human",
                             "termination_reason": "recovery_timeout"}
        state["status"] = "needs_human"
        _emit_status(state)
        return state
    # 目标关系已消失:连续三批库存预占探测(复用 order check-stock 探测逻辑)
    probes = _run_probe_batches(state, batches=3)
    ok = all(p.get("success") for p in probes)
    state["recovery"] = {"status": "recovered" if ok else "needs_human",
                         "probes": probes,
                         "termination_reason": None if ok else "recovery_probe_failed"}
    state["status"] = state["recovery"]["status"]
    _emit_status(state)
    return state


def _run_probe_batches(state: IncidentState, batches: int = 3) -> list[dict]:
    """三批固定探测请求(与健康基线采集相同参数),每批记录 success。"""
    import httpx
    probes = []
    order_url = _order_service_base()
    for _ in range(batches):
        try:
            resp = httpx.post(
                f"{order_url}/api/orders/1/check-stock",
                json={"skuId": 42, "warehouseId": 7, "quantity": 1}, timeout=10)
            probes.append({"success": resp.status_code < 500})
        except Exception:  # noqa: BLE001
            probes.append({"success": False})
    return probes


def _order_service_base() -> str:
    from app.config import settings
    return settings.order_service_url