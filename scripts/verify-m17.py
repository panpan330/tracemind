"""V1.7 三层验收编排(Local Fast / VM Smoke / VM Release)。

人工触发,内部自动执行全部步骤并生成 JSON 汇总:
  python scripts/verify-m17.py --tier fast
  python scripts/verify-m17.py --tier vm-smoke
  python scripts/verify-m17.py --tier release
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
AI = REPO / "ai-service"
REPORTS = REPO / "reports" / "generated" / "v1.7"


def run(cmd: list[str], cwd: Path, env: dict | None = None) -> tuple[int, str]:
    full_env = {**os.environ, **(env or {})}
    # Windows 下 npm 需 npm.cmd
    if os.name == "nt" and cmd and cmd[0] == "npm":
        cmd = ["npm.cmd", *cmd[1:]]
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=full_env)
    return r.returncode, ((r.stdout or "") + (r.stderr or ""))


# ---- Local Fast ----
def tier_fast() -> dict:
    summary = {"tier": "fast", "steps": {}}
    py = str(AI / ".venv/Scripts/python.exe")
    # 1) 后端全量 pytest
    code, out = run([py, "-m", "pytest", "tests/", "-q"], AI)
    summary["steps"]["ai_pytest"] = {"exit": code,
                                     "tail": (out.splitlines()[-3:] if out else [])}
    # 2) Vue typecheck + Replay Transport targeted test
    code, out = run(["npm", "run", "typecheck"], REPO / "web")
    summary["steps"]["vue_typecheck"] = {"exit": code}
    code, out = run(["npm", "run", "test", "--", "-t", "replay.*transport"], REPO / "web")
    summary["steps"]["vue_replay_transport"] = {"exit": code}
    # 3) 离线评测 N/N(动态发现;Windows 下用 cmd set 注入 env)
    eval_env = {"TRACEMIND_RUN_PROFILE": "offline_eval", "TRACEMIND_LLM_MODE": "fake",
                "TRACEMIND_EVAL_MODE": "true",
                "TRACEMIND_CONTROL_DB_URL":
                    "mysql+pymysql://tracemind_control_app:control_app_pwd@localhost:3306/tracemind_control"}
    code, out = run([py, "../scripts/eval_agent.py", "--mode", "offline",
                     "--llm", "fake", "--runs", "1"], AI, env=eval_env)
    summary["steps"]["offline_eval"] = {"exit": code,
                                        "tail": (out.splitlines()[-5:] if out else [])}
    summary["ok"] = all(s.get("exit") == 0 for s in summary["steps"].values())
    return summary


# ---- VM Smoke / Release(经 vm_ssh 在 VM 执行)----
def _vm(command: str) -> tuple[int, str]:
    import subprocess as sp
    r = sp.run([sys.executable, str(REPO / ".reasonix/tools/vm_ssh.py"), "run", command],
               cwd=REPO, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr)


def tier_vm_smoke() -> dict:
    summary = {"tier": "vm-smoke", "steps": {}}
    for name, cmd in [
        ("mcp_tools_health", "curl -sf http://127.0.0.1:8001/health/ready >/dev/null && echo OK"),
        ("ai_health", "curl -sf http://127.0.0.1:8000/api/health >/dev/null && echo OK"),
        ("no_stdio_spawn",
         "docker exec tracemind-ai sh -c 'ps aux | grep -c \"app.mcp.server_stdio\"' | tr -d ' '"),
    ]:
        code, out = _vm(cmd)
        summary["steps"][name] = {"exit": code, "out": out.strip()[:200]}
    # SCN-001/002 各一次闭环(复用 verify-m14 单轮,fake 或少量 real)
    code, out = _vm("python scripts/verify-m14.py --base http://192.168.88.10:8000 "
                    "--order http://192.168.88.10:8081 --rounds 1")
    summary["steps"]["scn_rounds"] = {"exit": code,
                                      "tail": (out.splitlines()[-5:] if out else [])}
    # 凭据隔离布尔(只输出两布尔,不 dump 完整 env)
    code, out = _vm("python scripts/check_credential_isolation.py")
    summary["credential_isolation"] = out.strip()
    summary["ok"] = all(s.get("exit") == 0 for s in summary["steps"].values())
    return summary


def tier_release() -> dict:
    summary = {"tier": "release", "steps": {}}
    # 真实模型验收:real_strict + Streamable HTTP(见计划 Task 21;耗时耗额度)
    code, out = _vm("python scripts/verify-m14.py --base http://192.168.88.10:8000 "
                    "--order http://192.168.88.10:8081 --rounds 1")
    summary["steps"]["release_rounds"] = {"exit": code,
                                          "tail": (out.splitlines()[-5:] if out else [])}
    code, out = _vm("python scripts/check_credential_isolation.py")
    summary["credential_isolation"] = out.strip()
    summary["ok"] = all(s.get("exit") == 0 for s in summary["steps"].values())
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tier", choices=["fast", "vm-smoke", "release"], default="fast")
    args = parser.parse_args()
    if args.tier == "fast":
        summary = tier_fast()
    elif args.tier == "vm-smoke":
        summary = tier_vm_smoke()
    else:
        summary = tier_release()
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "validation-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
