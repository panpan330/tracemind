"""LLM 封装:FakeLLM(确定性,默认)+ openai_compatible(占位,标注 V1.1 接入真实模型)。

FakeLLM 不调用网络,返回场景内建的确定性假设/提案/报告,保证 CI 与无密钥环境
可完整跑通 LangGraph 闭环。openai_compatible 分支保留接口,按 settings.llm_mode 切换。
"""
import hashlib
import json

from app.config import settings

FIX_ACTION = "CREATE_INVENTORY_INDEX"
FIX_PARAMETERS = {
    "index_name": "idx_sku_warehouse",
    "table": "inventory",
    "columns": ["sku_id", "warehouse_id"],
}


def _sha256(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class FakeLLM:
    """确定性占位实现:不调网络,返回场景内建假设/提案/报告。"""

    def hypothesize(self, state: dict) -> list[dict]:
        return [{
            "id": "h1",
            "description": "缺少联合索引 idx_sku_warehouse(sku_id, warehouse_id) 导致慢查询",
            "status": "proposed",
        }]

    def propose_fix(self, state: dict) -> dict:
        parameters = dict(FIX_PARAMETERS)
        return {
            "action_type": FIX_ACTION,
            "risk_level": "medium",
            "parameters": parameters,
            "parameters_hash": _sha256(parameters),
            "reason": "E1~E5 证据齐备:慢查询、trace 耗时集中于数据库阶段、全表扫描、联合索引缺失",
        }

    def write_report(self, state: dict) -> dict:
        """只使用 state 中已落库事实拼装 markdown 复盘报告,不引入模型臆测。"""
        evidence_lines = []
        for ev in state.get("evidence") or []:
            mark = "✅" if ev.get("passed") else "❌"
            evidence_lines.append(f"- {mark} {ev['id']} ({ev.get('source')}): {json.dumps(ev.get('content'), ensure_ascii=False)}")
        recovery = state.get("recovery") or {}
        fix_execution = state.get("fix_execution") or {}
        content = (
            "# 复盘报告\n\n"
            f"## 根因\n缺少联合索引 {FIX_PARAMETERS['index_name']} 导致慢查询\n\n"
            "## 证据链\n" + ("\n".join(evidence_lines) if evidence_lines else "(无)") + "\n\n"
            "## 修复执行\n"
            f"- action: {FIX_ACTION}\n"
            f"- 执行状态: {fix_execution.get('status', 'unknown')}\n\n"
            "## 恢复验证\n"
            f"- 结果: {recovery.get('status', 'unknown')}\n"
            f"- 修复后 P95: {recovery.get('latency_p95_after', 'n/a')} ms\n"
        )
        return {"content": content, "root_cause": "缺少联合索引"}


class OpenAICompatibleLLM:
    """V1.1 接入真实模型(结构化输出 + 工具绑定);本任务仅保证接口与切换位存在。"""

    def __init__(self) -> None:
        raise NotImplementedError("openai_compatible 模式在 V1.1 接入,当前请使用 fake 模式")


def get_llm():
    if settings.llm_mode == "openai_compatible":
        return OpenAICompatibleLLM()
    return FakeLLM()
