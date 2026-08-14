"""凭据隔离验收:只输出两个布尔,不 dump 容器完整环境(防验收过程泄密)。

本地脚本,ssh 到 VM 检查:
- ai-service 容器:不应有调查数据库凭据(READONLY_DB_URL)、MCP Server 认证配置(AUTH_CLIENTS_FILE)
- mcp-tools 容器:不应有 LLM key / fix_executor / session_terminator / 业务写账号
输出:{"aiServiceForbiddenCredentialsPresent": bool, "mcpToolsForbiddenCredentialsPresent": bool}
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _vm(command: str) -> str:
    r = subprocess.run([sys.executable, str(REPO / ".reasonix/tools/vm_ssh.py"), "run", command],
                       cwd=REPO, capture_output=True, text=True, timeout=120)
    return r.stdout


def _check(container: str, needles: list[str]) -> bool:
    env = _vm(f"docker exec {container} env")
    for n in needles:
        for line in env.splitlines():
            if line.startswith(n + "=") and line.split("=", 1)[1]:
                return True
    return False


def main() -> int:
    ai_forbidden = _check("tracemind-ai", [
        "TRACEMIND_PROMETHEUS_URL",              # 观测调查凭据(标准 HTTP 模式禁入 AI,调查经 mcp-tools)
        "TRACEMIND_JAEGER_QUERY_ENDPOINT",
        "TRACEMIND_MCP_AUTH_CLIENTS_FILE",       # MCP Server 认证配置(禁入 AI)
    ])
    mt_forbidden = _check("tracemind-mcp-tools", [
        "TRACEMIND_LLM_API_KEY", "TRACEMIND_CHAT_API_KEY",        # LLM key
        "TRACEMIND_FIX_EXECUTOR_DB_URL", "TRACEMIND_SESSION_TERMINATOR_DB_URL",
        "TRACEMIND_DB_APP_BUSINESS_PASSWORD",                     # 业务写账号
    ])
    out = {"aiServiceForbiddenCredentialsPresent": ai_forbidden,
           "mcpToolsForbiddenCredentialsPresent": mt_forbidden}
    print(json.dumps(out, ensure_ascii=False))
    return 0 if not (ai_forbidden or mt_forbidden) else 1


if __name__ == "__main__":
    raise SystemExit(main())
