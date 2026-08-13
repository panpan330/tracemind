"""敏感信息扫描(首次推送前必跑):工作区文件中的已知 Secret 模式。
模式:百炼 key / Bearer / 明文密码 / VM 口令 / 演示密钥。
分类:
  - BLOCK:真实凭据(sk- key / VM 口令 / 明文密码定义)→ 阻断
  - WARN:低敏感信息(内网 VM 地址 / 演示密钥)→ 提示人工确认
用法: python scripts/ci/scan_secrets.py [--strict]
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# (标签, 模式, 是否阻断)
PATTERNS = [
    ("百炼/OpenAI key", re.compile(r"sk-[A-Za-z0-9_\-]{12,}"), True),
    ("VM 口令", re.compile(r"panhangyu\w*"), True),
    ("明文密码定义", re.compile(r"IDENTIFIED BY ['\"][^'\"]+['\"]"), True),
    ("Bearer token", re.compile(r"(?i)authorization:\s*bearer\s+[A-Za-z0-9._\-]{8,}"), True),
    ("VM 内网地址", re.compile(r"192\.168\.\d+\.\d+"), False),
    ("演示密钥", re.compile(r"demo-secret-2026"), False),
]

# 允许清单:项目固定的非敏感固定值(演示 key / 测试假数据 / 扫描模式本身 / 开发默认账号密码)
ALLOWLIST = {
    "demo-secret-2026",  # demo 模式的固定密钥,非真实凭据
    "sk-1234567890abcd",  # redact_logs 测试假数据
    "sk-abcdef123456",  # redact_logs 测试假数据
    "panhangyu",  # 扫描规则模式本身(非口令泄露)
    "abc123def",  # redact_logs 测试假数据(Bearer)
    # 开发默认账号密码(与 .env.example / 迁移 002 一致,本地开发用,非生产凭据)
    "app_business_pwd", "control_app_pwd", "investigator_pwd", "fix_executor_pwd", "terminator_pwd",
}

SKIP_DIRS = {".git", "node_modules", ".venv", "target", "dist", "__pycache__",
             "reports", ".reasonix"}


def main() -> int:
    strict = "--strict" in sys.argv
    blocks: list[str] = []
    warns: list[str] = []
    for f in REPO.rglob("*"):
        if any(part in SKIP_DIRS for part in f.parts):
            continue
        if not f.is_file():
            continue
        if f.suffix not in {".py", ".sh", ".yml", ".yaml", ".sql", ".md",
                            ".json", ".ps1", ".ts", ".vue", ".txt"}:
            continue
        if f == Path(__file__).resolve():
            continue  # 扫描器自身包含模式定义字样,豁免
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            continue
        for label, pat, is_block in PATTERNS:
            for m in pat.finditer(text):
                val = m.group(0)
                # IDENTIFIED BY 'xxx' → 提取 xxx 与 allowlist 对比
                if label == "明文密码定义" and "'" in val:
                    val = val.split("'")[1] if val.count("'") >= 2 else val
                # Bearer xxx → 提取 token
                if label == "Bearer token" and val.lower().startswith("authorization:"):
                    parts = val.split()
                    if len(parts) >= 3:
                        val = parts[2]
                if val in ALLOWLIST:
                    continue
                entry = f"{f.relative_to(REPO)}: [{label}] {m.group(0)[:40]}"
                if is_block:
                    blocks.append(entry)
                else:
                    warns.append(entry)

    for w in warns:
        print(f"WARN {w}")
    if blocks:
        print("\n".join(blocks), file=sys.stderr)
        print(f"FATAL: {len(blocks)} 处真实凭据命中(阻断)", file=sys.stderr)
        return 1
    if strict and warns:
        print(f"FATAL(--strict): {len(warns)} 处低敏感 WARN,需人工确认", file=sys.stderr)
        return 1
    print(f"OK: 无真实凭据命中(WARN {len(warns)} 处,默认放行;--strict 阻断)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
