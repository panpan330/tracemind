from langgraph.graph import END, START, StateGraph

from app.agent.nodes import collect_evidence, diagnose
from app.agent.state import IncidentState


def _after_diagnose(state: IncidentState) -> str:
    """diagnose 后的分支:确认 -> 后续流程(当前到 END,Task 3.3 接 propose_fix)。"""
    if state.get("confirmed_hypothesis_id") is not None:
        return "confirmed"
    if state.get("status") == "needs_human":
        return "needs_human"
    return "retry"


def build_graph():
    g = StateGraph(IncidentState)
    g.add_node("collect_evidence", collect_evidence)
    g.add_node("diagnose", diagnose)
    g.add_edge(START, "collect_evidence")
    g.add_edge("collect_evidence", "diagnose")
    g.add_conditional_edges(
        "diagnose",
        _after_diagnose,
        {"confirmed": END, "needs_human": END, "retry": "collect_evidence"},
    )
    return g.compile()
