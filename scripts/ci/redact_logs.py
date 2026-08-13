"""日志脱敏:掩码所有已知 Secret 模式;失败时退出码非 0 且不产出可上传文件。
用法: python scripts/ci/redact_logs.py <src_dir> <dst_dir>
Secret 精确值经 TRACEMIND_REDACT_SECRETS(以 | 分隔)传入,不进命令行参数。
"""
import os
import re
import sys
from pathlib import Path

MASK = "[REDACTED]"

# 常见 Secret 模式(不含具体值;精确值由 TRACEMIND_REDACT_SECRETS 提供)
PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),                        # 百炼/OpenAI key
    re.compile(r"(?i)(authorization:\s*bearer\s+)[A-Za-z0-9._\-]+"),  # Bearer(保留前缀)
    re.compile(r"(://[^:/@\s]+:)[^@\s]+(@)"),            # URL 凭据 user:pass@(保留 user 和 @)
    re.compile(r'("(?:api_key|token|password|secret|apiKey)"\s*:\s*")[^"]*(")'),  # JSON 嵌套
    re.compile(r"(?i)(TRACEMIND_[A-Z_]*_(?:KEY|PASSWORD|SECRET)[A-Z_]*\s*=\s*)\S+"),
]


def _redact_patterns(text: str) -> str:
    for pat in PATTERNS:
        if pat.groups:
            text = pat.sub(lambda m: m.group(1) + MASK + (m.group(2) if len(m.groups()) > 1 else ""), text)
        else:
            text = pat.sub(MASK, text)
    return text


def load_secrets() -> list[str]:
    raw = os.environ.get("TRACEMIND_REDACT_SECRETS", "")
    return [s for s in raw.split("|") if s]


def redact(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, MASK)
    return _redact_patterns(text)


def main() -> int:
    if len(sys.argv) != 3:
        print("用法: redact_logs.py <src_dir> <dst_dir>", file=sys.stderr)
        return 2
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    if not src.is_dir():
        print(f"FATAL: {src} 不存在", file=sys.stderr)
        return 1
    secrets = load_secrets()
    if not secrets:
        print("FATAL: 无 TRACEMIND_REDACT_SECRETS,拒绝脱敏(避免未脱敏泄漏)", file=sys.stderr)
        return 1
    try:
        dst.mkdir(parents=True, exist_ok=True)
        for f in src.rglob("*"):
            if f.is_file():
                out = dst / f.relative_to(src)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(
                    redact(f.read_text(encoding="utf-8", errors="replace"), secrets),
                    encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"FATAL: 脱敏失败 {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
