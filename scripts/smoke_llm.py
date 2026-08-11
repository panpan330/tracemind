"""真实模型冒烟:断言 provider/model/degraded/structured_output_valid/Tool Calling,禁止假通过。
用法: cd ai-service && uv run python ../scripts/smoke_llm.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 让 `uv run python ../scripts/smoke_llm.py` 能 import ai-service 的 app 包
sys.path.insert(0, str(ROOT / "ai-service"))

from app.agent.llm_client import LLMClient  # noqa: E402
from app.config import settings  # noqa: E402


def main() -> int:
    if not (settings.chat_api_key_resolved and settings.chat_base_url_resolved):
        print("FAIL: Chat Provider 未配置", file=sys.stderr)
        return 1
    model = settings.eval_chat_model or settings.chat_model_resolved
    if not settings.eval_chat_model:
        print("FAIL: TRACEMIND_EVAL_CHAT_MODEL 必填(评测固定快照,不得用会漂移的别名)", file=sys.stderr)
        return 1
    client = LLMClient(model=model)
    # 1) Structured Output
    data = client.chat_json([{"role": "user", "content": '输出 JSON:{"ok": true}'}], max_tokens=200)
    so_ok = data is not None and data.get("ok") is True
    # 2) Tool Calling
    r = client.chat([{"role": "system", "content": "必须调用工具"},
                     {"role": "user", "content": "查询 inventory-service 的服务指标"}],
                    tools=[{"type": "function", "function": {
                        "name": "get_service_metrics", "description": "服务指标",
                        "parameters": {"type": "object", "properties": {
                            "service_ref": {"type": "string"}}, "required": ["service_ref"]}}}],
                    max_tokens=300)
    tc_ok = r is not None and bool(r.tool_calls)
    print(f"provider={settings.chat_provider} model={model} "
          f"degraded={not (so_ok and tc_ok)} "
          f"structured_output_valid={so_ok} tool_calling={tc_ok}")
    return 0 if (so_ok and tc_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
