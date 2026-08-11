"""确定性降级组件:运行时不用 FakeLLM 伪装,用程序模板保证闭环不崩。"""
from app.agent.rules import EVIDENCE_TOOL_MAP  # {e1: get_service_metrics, ...} 现有

# E1~E5 顺序(确定性降级规划器按此顺序补证据)
EVIDENCE_ORDER = ["e1", "e2", "e3", "e4", "e5"]
EVIDENCE_TOOL = {
    "e1": "get_service_metrics",
    "e2": "get_trace",
    "e3": "list_expensive_query_digests",
    "e4": "get_query_plan",
    "e5": "get_index_info",
}


class TemplateHypothesisGenerator:
    def generate(self, state: dict) -> list[dict]:
        return [{"id": "h1", "status": "proposed",
                 "description": "缺少联合索引 idx_sku_warehouse(sku_id, warehouse_id) 导致慢查询"}]


class DeterministicEvidencePlanner:
    """按 E1→E5 顺序选第一个缺失证据对应的 eligible 工具;
    E2 缺失但无合法 trace_id 时回退 get_service_metrics(重新取代表性 trace)。"""

    def choose(self, state: dict, eligible_tools: set[str]) -> list[dict]:
        gate = state.get("evidence_gate") or {}
        trace_id = self._find_trace_id(state)
        for key in EVIDENCE_ORDER:
            if gate.get(key, False):
                continue
            if key == "e2" and not trace_id:
                # 无 trace_id:回退到 metrics 重新取代表性 trace
                if "get_service_metrics" in eligible_tools:
                    return [{"id": "de1", "name": "get_service_metrics",
                             "arguments": self._arguments_for("e1", "get_service_metrics", state)}]
                continue
            tool = EVIDENCE_TOOL[key]
            if tool not in eligible_tools:
                continue
            args = self._arguments_for(key, tool, state)
            if key == "e2":
                args["trace_id"] = trace_id
            return [{"id": f"d{key}", "name": tool, "arguments": args}]
        return []

    @staticmethod
    def _find_trace_id(state: dict) -> str:
        if state.get("trigger_trace_id"):
            return state["trigger_trace_id"]
        for ev in state.get("evidence") or []:
            content = ev.get("content") or {}
            tid = content.get("representative_slow_trace_id") or content.get("representativeTraceId")
            if tid:
                return tid
        return ""

    @staticmethod
    def _arguments_for(key: str, tool: str, state: dict) -> dict:
        if tool == "get_service_metrics":
            return {"service_ref": state.get("service_ref", "inventory-service"),
                    "window_seconds": 300}
        if tool == "get_trace":
            return {"trace_id": ""}
        if tool == "list_expensive_query_digests":
            return {"window_seconds": 300}
        if tool == "get_query_plan":
            return {"query_ref": "INVENTORY_LOOKUP", "sample_parameters": {}}
        if tool == "get_index_info":
            return {"table_ref": "inventory"}
        return {}


class TemplatePostmortemRenderer:
    def render(self, state: dict) -> dict:
        fix_execution = state.get("fix_execution") or {}
        recovery = state.get("recovery") or {}
        content = (
            "# 复盘报告\n\n"
            "## 根因\n缺少联合索引 idx_sku_warehouse(sku_id, warehouse_id) 导致慢查询\n\n"
            "## 修复执行\n"
            f"- action: CREATE_INVENTORY_INDEX\n"
            f"- 执行状态: {fix_execution.get('status', 'unknown')}\n\n"
            "## 恢复验证\n"
            f"- 结果: {recovery.get('status', 'unknown')}\n"
        )
        return {"content": content, "root_cause_summary": "缺少联合索引"}
