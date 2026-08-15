"""V1.12 多模型路由:动态评分优先,静态配置回落。"""
from app.agent.model_scorer import ModelScorer
from app.config import settings

NODE_MODEL_KEY = {
    "hypothesize": "hypothesize_model",
    "select_tool": "select_tool_model",
    "reflect": "reflect_model",
    "write_report": "report_model",
}

NODE_CANDIDATES_KEY = {
    "hypothesize": "hypothesize_candidates",
    "select_tool": "select_tool_candidates",
    "reflect": "reflect_candidates",
    "write_report": "report_candidates",
}

scorer = ModelScorer()   # 模块级单例(进程内共享)


def _candidates(node: str) -> list[str]:
    key = NODE_CANDIDATES_KEY.get(node)
    if key is None:
        return []
    raw = getattr(settings, key, "") or ""
    return [m.strip() for m in raw.split(",") if m.strip()]


def _static_route(node: str) -> str | None:
    key = NODE_MODEL_KEY.get(node)
    if key is None:
        return None
    return getattr(settings, key, "") or None


def route(node: str) -> str | None:
    """动态路由:候选里选评分最高者;未启用/无候选/数据不足 → 回落静态配置。"""
    if settings.dynamic_routing:
        candidates = _candidates(node)
        if candidates:
            chosen = scorer.best(node, candidates, epsilon=settings.routing_epsilon)
            if chosen:
                return chosen
    return _static_route(node)
