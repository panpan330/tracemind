from app.agent.state import IncidentState


def noop(state: IncidentState) -> dict:
    return state
