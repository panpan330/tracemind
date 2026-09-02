"""V2.0-A:干净克隆门禁(compose 结构 / bootstrap 产物 / 无未跟踪启动依赖)。

混合部署边界(docs/startup-boundary.md):compose 只承载 VM 基础设施 + CI 全栈,
主机应用不在 compose 内重构;本测试只钉住"干净克隆可校验、schema 有官方迁移器负责"。
"""
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _compose():
    return yaml.safe_load((ROOT / "compose.yml").read_text(encoding="utf-8"))


def test_compose_no_legacy_initdb_mount():
    text = (ROOT / "compose.yml").read_text(encoding="utf-8")
    assert "scripts/sql" not in text  # 该目录已废弃(commit a8f67f6),挂载会让 initdb 为空


def test_compose_mysql_is_utc():
    c = _compose()
    mysql = c["services"]["mysql"]
    assert mysql["environment"]["TZ"] == "UTC"
    assert "--default-time-zone=+00:00" in mysql["command"]
    text = (ROOT / "compose.yml").read_text(encoding="utf-8")
    assert "Asia/Shanghai" not in text


def test_compose_has_db_init_official_migrator():
    """schema 由官方迁移器负责(db-init 一次性服务),幂等、含 009。"""
    c = _compose()
    init = c["services"].get("db-init")
    assert init is not None
    cmd = " ".join(init["command"])
    assert "migrate.py --init-db" in cmd
    assert "--provision" in cmd
    assert init["restart"] == "no"
    # 应用服务必须等 db-init 完成后启动(schema 先行)
    for svc in ("seed", "ai-service", "mcp-tools"):
        deps = c["services"][svc]["depends_on"]
        assert deps["db-init"]["condition"] == "service_completed_successfully", svc


def test_compose_untracked_runtime_files_have_examples():
    """compose 引用的本地文件必须有入库 example + bootstrap 脚本可生成(不提交真实值)。"""
    for example in (".env.vm.example", "secrets/mcp_clients.example.json",
                    ".env.example"):
        assert (ROOT / example).is_file(), example
    for script in ("scripts/bootstrap-dev.ps1", "scripts/bootstrap-dev.sh",
                   "scripts/vm-infra-check.sh"):
        assert (ROOT / script).is_file(), script
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env.vm" in gitignore and "secrets/mcp_clients.json" in gitignore


def test_mcp_example_clients_file_shape():
    import json
    data = json.loads((ROOT / "secrets/mcp_clients.example.json").read_text(encoding="utf-8"))
    assert "__origins__" in data
    entries = [k for k in data if k != "__origins__"]
    assert len(entries) == 1 and entries[0].startswith("sha256:")
    entry = data[entries[0]]
    assert entry["subject"] == "ai-service" and entry["scopes"] == ["tools:investigate"]


def test_local_ignores_not_committed():
    """真实密钥文件绝不入库(双保险:.gitignore + 抽样检查)。"""
    for f in (".env.vm", ".env.local", "secrets/mcp_clients.json"):
        p = ROOT / f
        if p.is_file():
            assert ".git" not in str(p)  # 文件可以存在于本地,但必须被 ignore
            import subprocess
            r = subprocess.run(["git", "check-ignore", str(p)], cwd=ROOT,
                               capture_output=True, text=True)
            assert r.returncode == 0, f"{f} 未被 gitignore"
