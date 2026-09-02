"""V2.0-A:LangGraph checkpoint 配置契约(真实 SqliteSaver 集成测试)。

钉死三个事实(升级方案 V2.0 实施内容第 7 条):
1. thread_id 必须位于 configurable 层级(LangGraph 1.x 文档契约);
2. 扁平顶层 thread_id 依赖 langchain-core ensure_config 的归一化,与 canonical
   命中同一 checkpoint 命名空间(兼容性事实,不得作为生产写法依据);
3. 进程重启(新 saver 实例、同一 checkpoint 文件)后同一 thread_id 可用
   Command(resume=...) 继续,thread 与 agent_run 一一对应。
"""
import sqlite3
from typing import Annotated, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.services.runner import _graph_config


class ContractState(TypedDict, total=False):
    v: int
    log: Annotated[list[str], lambda a, b: (a or []) + (b or [])]


def _build_graph(saver: SqliteSaver):
    def step1(state):
        return {"log": ["step1"]}

    def approval(state):
        decision = interrupt({"type": "approval_request"})
        return {"log": [f"approved:{decision['decision']}"]}

    g = StateGraph(ContractState)
    g.add_node("step1", step1)
    g.add_node("approval", approval)
    g.add_edge(START, "step1")
    g.add_edge("step1", "approval")
    g.add_edge("approval", END)
    return g.compile(checkpointer=saver)


def _saver(tmp_path):
    return SqliteSaver(sqlite3.connect(str(tmp_path / "ckpt.sqlite"),
                                       check_same_thread=False))


def test_configurable_thread_id_is_the_documented_contract(tmp_path):
    """canonical 配置产生可恢复 checkpoint;新 saver(模拟重启)可 resume。"""
    graph = _build_graph(_saver(tmp_path))
    cfg = _graph_config("contract-thread-1")
    assert cfg == {"configurable": {"thread_id": "contract-thread-1"},
                   "recursion_limit": 100}
    first = graph.invoke({"v": 0}, cfg)
    assert first["__interrupt__"]  # 停在审批

    # 模拟进程重启:全新 saver 实例 + 同一 checkpoint 文件
    graph2 = _build_graph(_saver(tmp_path))
    second = graph2.invoke(Command(resume={"decision": "approved"}),
                           _graph_config("contract-thread-1"))
    assert second["status"] if False else "approved:approved" in second["log"]


def test_flat_thread_id_hits_same_namespace_via_normalization(tmp_path):
    """兼容性事实:扁平写法经 ensure_config 归一化进 configurable(同命名空间),
    但生产代码一律使用 canonical configurable(runner._graph_config)。"""
    saver = _saver(tmp_path)
    graph = _build_graph(saver)
    graph.invoke({"v": 0}, {"thread_id": "flat-t", "recursion_limit": 50})
    # 扁平写入的 checkpoint 能以 canonical 方式列出并恢复
    ckpts = list(saver.list({"configurable": {"thread_id": "flat-t"}}))
    assert ckpts
    graph2 = _build_graph(_saver(tmp_path))
    second = graph2.invoke(Command(resume={"decision": "approved"}),
                           _graph_config("flat-t"))
    assert "approved:approved" in second["log"]


def test_thread_id_persisted_in_checkpoint_metadata(tmp_path):
    """checkpoint 元数据中的 thread_id 与传入 thread 一致(thread ↔ run 一一对应的存储基础)。"""
    saver = _saver(tmp_path)
    graph = _build_graph(saver)
    graph.invoke({"v": 0}, _graph_config("meta-t"))
    ckpts = list(saver.list({"configurable": {"thread_id": "meta-t"}}))
    assert ckpts
    for ck in ckpts:
        assert ck.config["configurable"]["thread_id"] == "meta-t"
