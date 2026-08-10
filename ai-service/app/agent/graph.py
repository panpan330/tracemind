from langgraph.graph import END, START, StateGraph

from app.agent.nodes import (
    collect_evidence, diagnose, execute_fix, human_approval, hypothesize,
    ingest, propose_fix, report, verify_recovery_node,
)
from app.agent.state import IncidentState


def _after_diagnose(state: IncidentState) -> str:
    """diagnose 后的分支:确认 -> propose_fix;证据不足预算耗尽 -> needs_human;否则继续收集。"""
    if state.get("confirmed_hypothesis_id") is not None:
        return "confirmed"
    if state.get("status") == "needs_human":
        return "needs_human"
    return "retry"


def _after_approval(state: IncidentState) -> str:
    """审批 resume 后的分支:approved -> execute_fix;rejected/expired -> report。"""
    approval = state.get("approval") or {}
    return "approved" if approval.get("status") == "approved" else "rejected"


def build_graph(checkpointer=None):
    g = StateGraph(IncidentState)
    g.add_node("ingest", ingest)
    g.add_node("hypothesize", hypothesize)
    g.add_node("collect_evidence", collect_evidence)
    g.add_node("diagnose", diagnose)
    g.add_node("propose_fix", propose_fix)
    g.add_node("human_approval", human_approval)
    g.add_node("execute_fix", execute_fix)
    g.add_node("verify_recovery", verify_recovery_node)
    g.add_node("report", report)

    g.add_edge(START, "ingest")
    g.add_edge("ingest", "hypothesize")
    g.add_edge("hypothesize", "collect_evidence")
    g.add_edge("collect_evidence", "diagnose")
    g.add_conditional_edges(
        "diagnose",
        _after_diagnose,
        {"confirmed": "propose_fix", "needs_human": END, "retry": "collect_evidence"},
    )
    g.add_edge("propose_fix", "human_approval")
    g.add_conditional_edges(
        "human_approval",
        _after_approval,
        {"approved": "execute_fix", "rejected": "report"},
    )
    g.add_edge("execute_fix", "verify_recovery")
    g.add_edge("verify_recovery", "report")
    g.add_edge("report", END)
    return g.compile(checkpointer=checkpointer)
