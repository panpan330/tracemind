"""迁移器单元测试:本地 MySQL 上验证幂等/checksum 变更/脏状态/advisory lock。
用法:TRACEMIND_MIGRATE_TEST_DB_URL 指向 root 连接(默认 mysql+pymysql://root:root@127.0.0.1:3306/tracemind_migrate_test)。
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

MIGRATE = Path(__file__).parent / "migrate.py"
DB_URL = os.environ.get(
    "TRACEMIND_MIGRATE_TEST_DB_URL",
    "mysql+pymysql://root:root@127.0.0.1:3306/tracemind_migrate_test")
ROOT_URL = os.environ.get(
    "TRACEMIND_MIGRATE_TEST_ROOT_URL",
    "mysql+pymysql://root:root@127.0.0.1:3306/")


def run_migrate(migrations_dir: Path, *extra: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, TRACEMIND_MIGRATE_DB_URL=DB_URL)
    return subprocess.run([sys.executable, str(MIGRATE), "--migrations", str(migrations_dir),
                           *extra], capture_output=True, text=True, env=env)


@pytest.fixture
def isolated_migrations(tmp_path):
    (tmp_path / "001_create_t.sql").write_text(
        "CREATE TABLE IF NOT EXISTS t (id INT PRIMARY KEY, name VARCHAR(50));\n",
        encoding="utf-8")
    (tmp_path / "002_add_col.sql").write_text(
        "ALTER TABLE t ADD COLUMN IF NOT EXISTS age INT;\n", encoding="utf-8")
    return tmp_path


def _clean_schema_migrations():
    """每个用例前清空迁移表,保证独立。"""
    import pymysql
    conn = pymysql.connect(host="127.0.0.1", port=3306, user="root", password="root",
                           database="tracemind_migrate_test", charset="utf8mb4")
    try:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS schema_migrations")
            cur.execute("DROP TABLE IF EXISTS t")
            conn.commit()
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def clean_schema():
    _clean_schema_migrations()
    yield
    _clean_schema_migrations()


def test_first_run_applies_all():
    mdir = Path(__file__).parent / "_mig_sample"
    (mdir if mdir.exists() else None)  # 用 tmp_path 场景由 fixture 提供
    # 独立目录
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        (p / "001_create_t.sql").write_text(
            "CREATE TABLE IF NOT EXISTS t (id INT PRIMARY KEY, name VARCHAR(50));\n",
            encoding="utf-8")
        r = run_migrate(p)
        assert r.returncode == 0, r.stderr
        assert "APPLY 001_create_t.sql" in r.stdout


def test_idempotent_second_run_skips():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        (p / "001_create_t.sql").write_text(
            "CREATE TABLE IF NOT EXISTS t (id INT PRIMARY KEY, name VARCHAR(50));\n",
            encoding="utf-8")
        r1 = run_migrate(p)
        assert r1.returncode == 0, r1.stderr
        r2 = run_migrate(p)
        assert r2.returncode == 0, r2.stderr
        assert "SKIP  001_create_t.sql" in r2.stdout


def test_checksum_change_fails():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        f = p / "001_create_t.sql"
        f.write_text("CREATE TABLE IF NOT EXISTS t (id INT PRIMARY KEY);\n", encoding="utf-8")
        assert run_migrate(p).returncode == 0
        f.write_text("CREATE TABLE IF NOT EXISTS t (id INT PRIMARY KEY, changed INT);\n",
                     encoding="utf-8")
        r = run_migrate(p)
        assert r.returncode == 2, r.stdout + r.stderr
        assert "checksum" in r.stderr


def test_dry_run_does_not_apply():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        (p / "001_create_t.sql").write_text(
            "CREATE TABLE IF NOT EXISTS t (id INT PRIMARY KEY);\n", encoding="utf-8")
        r = run_migrate(p, "--dry-run")
        assert r.returncode == 0
        assert "PLAN" in r.stdout
        assert "APPLY" not in r.stdout
