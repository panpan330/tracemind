"""验证 --provision 能创建/更新 5 账号并绑定角色(幂等可重复),且迁移文件中无明文密码。
用法:TRACEMIND_MIGRATE_TEST_ROOT_URL 指向 root 连接(默认 mysql+pymysql://root:root@127.0.0.1:3306/)。
"""
import os
import re
import subprocess
import sys
from pathlib import Path

MIGRATE = Path(__file__).parent / "migrate.py"
ROOT_URL = os.environ.get(
    "TRACEMIND_MIGRATE_TEST_ROOT_URL",
    "mysql+pymysql://root:root@127.0.0.1:3306/")
MIG_DIR = Path(__file__).parent / "migrations"

# 测试输入:与本地 scripts/sql/02-users.sql 一致的原密码。
# 关键:provision 用这些值 ALTER USER 后,本地账号密码不变(不破坏本地开发环境);
# CI 环境(全新 MySQL)用各自 CI 密码,与本测试无冲突。
PROVISION_ENV = {
    "TRACEMIND_DB_CONTROL_APP_PASSWORD": "control_app_pwd",
    "TRACEMIND_DB_APP_BUSINESS_PASSWORD": "app_business_pwd",
    "TRACEMIND_DB_AI_INVESTIGATOR_PASSWORD": "investigator_pwd",
    "TRACEMIND_DB_FIX_EXECUTOR_PASSWORD": "fix_executor_pwd",
    "TRACEMIND_DB_SESSION_TERMINATOR_PASSWORD": "terminator_pwd",
}


def test_migration_files_have_no_passwords():
    for f in MIG_DIR.glob("*.sql"):
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"IDENTIFIED BY ['\"]", text, re.IGNORECASE), f"{f.name} 含明文密码"


def test_provision_creates_five_accounts():
    env = dict(os.environ, TRACEMIND_MIGRATE_DB_URL=ROOT_URL, **PROVISION_ENV)
    r1 = subprocess.run([sys.executable, str(MIGRATE), "--provision", "--migrations", str(MIG_DIR)],
                        capture_output=True, text=True, env=env)
    assert r1.returncode == 0, r1.stderr
    # 幂等:重复跑一次
    r2 = subprocess.run([sys.executable, str(MIGRATE), "--provision", "--migrations", str(MIG_DIR)],
                        capture_output=True, text=True, env=env)
    assert r2.returncode == 0, r2.stderr


def test_provision_requires_all_passwords():
    env = dict(os.environ, TRACEMIND_MIGRATE_DB_URL=ROOT_URL)  # 无任何 *_PASSWORD
    r = subprocess.run([sys.executable, str(MIGRATE), "--provision", "--migrations", str(MIG_DIR)],
                       capture_output=True, text=True, env=env)
    assert r.returncode != 0
    assert "FATAL" in r.stderr
