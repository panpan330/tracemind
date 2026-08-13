"""日志脱敏单元测试:各类 Secret 被掩码;无 secrets 时拒绝。"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def run_redact(src: Path, dst: Path, secrets: list[str] | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if secrets is not None:
        env["TRACEMIND_REDACT_SECRETS"] = "|".join(secrets)
    else:
        env.pop("TRACEMIND_REDACT_SECRETS", None)
    # 脚本绝对路径(测试可能从任意 cwd 运行)
    script = Path(__file__).resolve().parent / "redact_logs.py"
    return subprocess.run([sys.executable, str(script), str(src), str(dst)],
                          capture_output=True, text=True, env=env)


def test_redacts_bearer_and_sk(tmp_path):
    src = tmp_path / "raw"; dst = tmp_path / "s"
    src.mkdir()
    (src / "a.log").write_text(
        "Authorization: Bearer abc123def\nkey: sk-1234567890abcd\n", encoding="utf-8")
    r = run_redact(src, dst, ["abc123def", "sk-1234567890abcd"])
    assert r.returncode == 0, r.stderr
    out = (dst / "a.log").read_text(encoding="utf-8")
    assert "sk-1234567890abcd" not in out
    assert "Bearer [REDACTED]" in out  # Bearer 前缀保留


def test_redacts_mysql_url(tmp_path):
    src = tmp_path / "raw"; dst = tmp_path / "s"
    src.mkdir()
    (src / "c.log").write_text(
        "mysql+pymysql://tracemind_control_app:secretpwd@h:3306/db", encoding="utf-8")
    r = run_redact(src, dst, ["secretpwd"])
    assert r.returncode == 0
    out = (dst / "c.log").read_text(encoding="utf-8")
    assert "secretpwd" not in out
    assert "tracemind_control_app" in out  # 用户名保留


def test_redacts_json_nested(tmp_path):
    src = tmp_path / "raw"; dst = tmp_path / "s"
    src.mkdir()
    (src / "j.json").write_text(
        '{"body": {"api_key": "KEY123", "token": "TOK456"}}', encoding="utf-8")
    r = run_redact(src, dst, ["KEY123", "TOK456"])
    assert r.returncode == 0
    out = (dst / "j.json").read_text(encoding="utf-8")
    assert "KEY123" not in out and "TOK456" not in out


def test_redacts_env_style_password(tmp_path):
    src = tmp_path / "raw"; dst = tmp_path / "s"
    src.mkdir()
    (src / "e.log").write_text("TRACEMIND_CHAT_API_KEY=sk-abcdef123456\n", encoding="utf-8")
    r = run_redact(src, dst, [])
    # 无 secrets → 拒绝(安全兜底)
    assert r.returncode != 0
    # 有 secrets 时 env 风格密码也应脱敏
    r2 = run_redact(src, dst, ["sk-abcdef123456"])
    assert r2.returncode == 0
    assert "sk-abcdef123456" not in (dst / "e.log").read_text(encoding="utf-8")


def test_missing_secrets_rejected(tmp_path):
    src = tmp_path / "raw"; dst = tmp_path / "s"
    src.mkdir()
    (src / "x.log").write_text("sk-abc123\n", encoding="utf-8")
    r = run_redact(src, dst, None)
    assert r.returncode != 0
    assert not dst.exists() or not any(dst.iterdir())  # 不产出可上传文件
