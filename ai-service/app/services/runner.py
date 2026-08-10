"""LangGraph 执行管理:全局持久化 checkpointer + 审批恢复。

后台 asyncio.Task 管理与启动 checkpoint 恢复在 Task 3.6 补充。
"""
import os

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from app.config import settings

_saver: AsyncSqliteSaver | None = None


async def get_saver() -> AsyncSqliteSaver:
    global _saver
    if _saver is None:
        path = settings.checkpoint_path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        _saver = await AsyncSqliteSaver.from_conn_string(path)
    return _saver


async def resume_investigation(thread_id: str, resume_value: dict) -> None:
    """用同一 thread_id 恢复挂起的图(interrupt 处继续)。"""
    from app.agent.graph import build_graph
    saver = await get_saver()
    graph = build_graph(checkpointer=saver)
    await graph.ainvoke(
        Command(resume=resume_value),
        config={"thread_id": thread_id, "recursion_limit": 100},
    )
