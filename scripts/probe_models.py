"""批量探测模型能力:Structured Output + Tool Calling(真实调用,耗少量额度)。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ai-service"))

from app.agent.llm_client import LLMClient  # noqa: E402
from app.config import settings  # noqa: E402

MODELS = [
    "qwen3.7-flash",
    "qwen3.7-max",
    "qwen3.8-max",
    "qwen3.6-plus",
    "qwen3.6-max-preview",
    "deepseek-v4-flash-0731",
    "kimi-k2.7-code",
    "glm-5.2",
]

TOOLS = [{"type": "function", "function": {
    "name": "get_service_metrics", "description": "服务指标",
    "parameters": {"type": "object", "properties": {
        "service_ref": {"type": "string"}}, "required": ["service_ref"]}}}]


def probe(model: str) -> tuple[bool, bool, str]:
    client = LLMClient(model=model)
    # 1) Structured Output
    so_ok = False
    try:
        data = client.chat_json([{"role": "user", "content": '输出 JSON:{"ok": true}'}],
                                max_tokens=200)
        so_ok = data is not None and data.get("ok") is True
    except Exception as exc:  # noqa: BLE001
        return False, False, f"SO 异常: {str(exc)[:80]}"
    # 2) Tool Calling
    tc_ok = False
    try:
        r = client.chat([{"role": "system", "content": "必须调用工具"},
                         {"role": "user", "content": "查询 inventory-service 的服务指标"}],
                        tools=TOOLS, max_tokens=300)
        tc_ok = r is not None and bool(r.tool_calls)
    except Exception as exc:  # noqa: BLE001
        return so_ok, False, f"TC 异常: {str(exc)[:80]}"
    return so_ok, tc_ok, ""


if __name__ == "__main__":
    print(f"base_url={settings.chat_base_url_resolved}")
    for m in MODELS:
        so, tc, err = probe(m)
        mark = "OK" if (so and tc) else "!!"
        print(f"{mark} {m:24s} structured_output={so} tool_calling={tc} {err}")
