"""凭据隔离验收:只输出两个布尔,不 dump 容器完整环境(防验收过程泄密)。

检查:
- ai-service 容器:不应有调查数据库凭据(READONLY_DB_URL)、MCP Server 认证配置(AUTH_CLIENTS_FILE)
- mcp-tools 容器:不应有 LLM key / fix_executor / session_terminator / 业务写账号
输出:{"aiServiceForbiddenCredentialsPresent": bool, "mcpToolsForbiddenCredentialsPresent": bool}
"""
import json
import subprocess
import sys


def _docker_env(container: str) -> str:
    r = subprocess.run(["docker", "exec", container, "env"],
                       capture_output=True, text=True, timeout=30)
    return r.stdout


def _present(env: str, needles: list[str]) -> bool:
    for n in needles:
        for line in env.splitlines():
            if line.startswith(n + "=") and line.split("=", 1)[1]:
                return True
    return False


def main() -> int:
    ai_env = _docker_env("tracemind-ai")
    mt_env = _docker_env("tracemind-mcp-tools")
    ai_forbidden = _present(ai_env, [
        "TRACEMIND_READONLY_DB_URL",          # 调查数据库凭据(标准 HTTP 模式禁入 AI)
        "TRACEMIND_MCP_AUTH_CLIENTS_FILE",    # MCP Server 认证配置(禁入 AI)
    ])
    mt_forbidden = _present(mt_env, [
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
