from langgraph.graph import END, START, StateGraph

from app.agent.nodes import noop
from app.agent.state import IncidentState


def build_graph():
    g = StateGraph(IncidentState)
    g.add_node("noop", noop)
    g.add_edge(START, "noop")
    g.add_edge("noop", END)
    return g.compile()
