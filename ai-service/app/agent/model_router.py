"""V1.11 多模型路由:按节点任务难度选模型(纯配置驱动)。"""
from app.config import settings

NODE_MODEL_KEY = {
    "hypothesize": "hypothesize_model",
    "select_tool": "select_tool_model",
    "reflect": "reflect_model",
    "write_report": "report_model",
}


def route(node: str) -> str | None:
    """返回该节点应使用的模型;未配置/未知节点返回 None(调用方回落默认)。"""
    key = NODE_MODEL_KEY.get(node)
    if key is None:
        return None
    return getattr(settings, key, "") or None
