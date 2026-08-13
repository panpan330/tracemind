"""TraceMind 唯一数据库迁移入口。用法:
  python scripts/db/migrate.py [--migrations DIR] [--dry-run]
  python scripts/db/migrate.py repair --migration-id N --checksum SHA --reason "..." [--migrations DIR]
  python scripts/db/migrate.py --provision [--migrations DIR]
环境:TRACEMIND_MIGRATE_DB_URL(必填);账号密码走 TRACEMIND_DB_*_PASSWORD 系列。
"""
import argparse
import hashlib
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pymysql

DEFAULT_MIGRATIONS = Path(__file__).parent / "migrations"

# 当前连接配置(供 _apply 的 CLI 分支复用)
_conn_cfg: dict = {}

# (用户名, 密码环境变量, 角色) —— 与 002_users_roles.sql 的 Role 对应
ACCOUNTS = [
    ("tracemind_control_app", "TRACEMIND_DB_CONTROL_APP_PASSWORD", "role_control_app"),
    ("app_business", "TRACEMIND_DB_APP_BUSINESS_PASSWORD", "role_app_business"),
    ("ai_investigator", "TRACEMIND_DB_AI_INVESTIGATOR_PASSWORD", "role_ai_investigator"),
    ("fix_executor", "TRACEMIND_DB_FIX_EXECUTOR_PASSWORD", "role_fix_executor"),
    ("session_terminator", "TRACEMIND_DB_SESSION_TERMINATOR_PASSWORD", "role_session_terminator"),
]


def _parse_url(url: str) -> dict:
    """mysql+pymysql://user:pass@host:port/db → dict(密码不含 @ 时成立)。"""
    rest = url.split("://", 1)[1]
    cred, host = rest.rsplit("@", 1)
    user, _, pwd = cred.partition(":")
    if "/" in host:
        host, _, db = host.partition("/")
    else:
        db = ""
    port = 3306
    if ":" in host:
        host, _, port_s = host.partition(":")
        port = int(port_s)
    return {"user": user, "password": pwd, "host": host, "port": port, "db": db}


def _connect(url: str) -> pymysql.connections.Connection:
    global _conn_cfg
    _conn_cfg = _parse_url(url)
    cfg = _conn_cfg
    # MULTI_STATEMENTS:迁移文件含 USE + 多条 DDL,一次 execute 需启用
    return pymysql.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                           password=cfg["password"], database=cfg["db"] or None,
                           charset="utf8mb4", autocommit=False,
                           client_flag=pymysql.constants.CLIENT.MULTI_STATEMENTS)


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ensure_table(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            migration_id INT AUTO_INCREMENT PRIMARY KEY,
            filename VARCHAR(255) NOT NULL UNIQUE,
            checksum_sha256 CHAR(64) NOT NULL,
            status ENUM('started','applied','failed') NOT NULL,
            started_at DATETIME NOT NULL,
            applied_at DATETIME NULL,
            execution_ms INT NULL,
            error_code VARCHAR(255) NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)


def _sorted_migrations(migrations_dir: Path) -> list[Path]:
    files = list(migrations_dir.glob("*.sql"))
    files.sort(key=lambda p: (int(p.name.split("_", 1)[0]), p.name))
    return files


def _apply(cur, conn, path: Path) -> None:
    """执行迁移文件:全部走 mysql CLI(独立进程)。
    原因:pymysql 多语句 'USE db; DDL' 会挂起;Windows 下 subprocess stdin 管道喂 SQL
    给 mysql 也会挂起 → 用 --execute="SOURCE <abs_path>"(CLI 原生读文件,不经 stdin)。
    CLI 独立进程,天然支持多语句文件(USE/PREPARE/EXECUTE),不污染连接状态。"""
    import shutil
    exe = shutil.which("mysql")
    if not exe:
        raise RuntimeError("需要 mysql CLI(迁移执行器依赖)")
    cfg = _conn_cfg
    env = dict(os.environ, MYSQL_PWD=cfg["password"])
    cmd = [exe, "--host", cfg["host"], "--port", str(cfg["port"]),
           "--user", cfg["user"], "--default-character-set=utf8mb4",
           "--execute", f"SOURCE {path.resolve()}"]
    if cfg.get("db"):
        cmd.append(cfg["db"])
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"mysql CLI 执行失败: {r.stderr[:500]}")


def run_migrations(conn, migrations_dir: Path, dry_run: bool = False) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT GET_LOCK('tracemind_migrations', 60)")
        if cur.fetchone()[0] != 1:
            print("FATAL: 无法获取迁移 Advisory Lock", file=sys.stderr)
            return 4
        try:
            _ensure_table(cur)
            cur.execute("SELECT filename, checksum_sha256, status FROM schema_migrations")
            applied = {f: (c, s) for f, c, s in cur.fetchall()}
            for path in _sorted_migrations(migrations_dir):
                name, ck = path.name, _checksum(path)
                if name in applied:
                    old_ck, status = applied[name]
                    if old_ck != ck:
                        print(f"FATAL: {name} checksum 变更({old_ck} → {ck}),拒绝执行",
                              file=sys.stderr)
                        return 2
                    if status == "failed":
                        print(f"FATAL: {name} 处于 failed 状态,需 repair", file=sys.stderr)
                        return 3
                    print(f"SKIP  {name}(已 applied)")
                    continue
                if dry_run:
                    print(f"PLAN  {name}(dry-run)")
                    continue
                cur.execute(
                    "INSERT INTO schema_migrations (filename, checksum_sha256, status, started_at) "
                    "VALUES (%s, %s, 'started', NOW())", (name, ck))
                conn.commit()
                t0 = time.time()
                try:
                    _apply(cur, conn, path)
                    cur.execute(
                        "UPDATE schema_migrations SET status='applied', applied_at=NOW(), "
                        "execution_ms=%s WHERE filename=%s",
                        (int((time.time() - t0) * 1000), name))
                    conn.commit()
                    print(f"APPLY {name}")
                except Exception as e:  # noqa: BLE001
                    conn.rollback()
                    cur.execute(
                        "UPDATE schema_migrations SET status='failed', error_code=%s "
                        "WHERE filename=%s", (str(e)[:250], name))
                    conn.commit()
                    print(f"FAIL  {name}: {e}", file=sys.stderr)
                    return 3
            return 0
        finally:
            cur.execute("SELECT RELEASE_LOCK('tracemind_migrations')")


def _init_databases(conn) -> int:
    """执行 scripts/db/create_databases.sql(建库,连接可不带库名)。走 mysql CLI
    --execute="SOURCE <abs>"(pymysql 多语句建库挂起,stdin 管道在 Windows 也挂起)。"""
    path = Path(__file__).parent / "create_databases.sql"
    if not path.exists():
        print(f"FATAL: {path} 不存在", file=sys.stderr)
        return 1
    import shutil
    exe = shutil.which("mysql")
    if not exe:
        print("FATAL: 需要 mysql CLI", file=sys.stderr)
        return 1
    cfg = _conn_cfg
    env = dict(os.environ, MYSQL_PWD=cfg["password"])
    r = subprocess.run([exe, "--host", cfg["host"], "--port", str(cfg["port"]),
                        "--user", cfg["user"], "--default-character-set=utf8mb4",
                        "--execute", f"SOURCE {path.resolve()}"],
                       capture_output=True, text=True, env=env)
    if r.returncode != 0:
        print(f"FATAL: mysql CLI 建库失败: {r.stderr[:500]}", file=sys.stderr)
        return 1
    print("INIT-DB create_databases.sql OK")
    return 0


def run_provision(conn) -> int:
    """账号 Provisioning:密码只来自环境变量,参数化,不拼进 SQL 文件。幂等。
    自包含:确保角色存在(不依赖迁移文件已执行),再建用户/绑定角色/授权。"""
    # (角色, 授权语句列表) —— 与 003_users_roles.sql 保持同步(迁移文件对角色做相同授权)
    ROLE_GRANTS = {
        "role_control_app": [
            "GRANT SELECT, INSERT, UPDATE, DELETE ON tracemind_control.* TO 'role_control_app'",
            "GRANT CREATE TEMPORARY TABLES ON tracemind_control.* TO 'role_control_app'",
        ],
        "role_app_business": [
            "GRANT SELECT, INSERT, UPDATE, DELETE ON tracemind_business.* TO 'role_app_business'",
            "GRANT INDEX ON tracemind_business.* TO 'role_app_business'",
            "GRANT SELECT, INSERT, UPDATE, DELETE ON tracemind_business_test.* TO 'role_app_business'",
        ],
        "role_ai_investigator": [
            "GRANT SELECT ON tracemind_business.* TO 'role_ai_investigator'",
            "GRANT SELECT ON tracemind_business_test.* TO 'role_ai_investigator'",
            "GRANT SELECT ON performance_schema.* TO 'role_ai_investigator'",
            "GRANT PROCESS ON *.* TO 'role_ai_investigator'",
        ],
        "role_fix_executor": [
            "GRANT INDEX ON tracemind_business.* TO 'role_fix_executor'",
        ],
        "role_session_terminator": [
            "GRANT SELECT ON performance_schema.* TO 'role_session_terminator'",
            "GRANT PROCESS ON *.* TO 'role_session_terminator'",
            "GRANT CONNECTION_ADMIN ON *.* TO 'role_session_terminator'",
        ],
    }
    with conn.cursor() as cur:
        # 1) 确保角色存在 + 授权
        for role, grants in ROLE_GRANTS.items():
            cur.execute("CREATE ROLE IF NOT EXISTS %s", (role,))
            for g in grants:
                cur.execute(g)
            conn.commit()
        # 2) 创建/更新账号 + 绑定角色
        for user, pwd_env, role in ACCOUNTS:
            pwd = os.environ.get(pwd_env)
            if not pwd:
                print(f"FATAL: 缺 {pwd_env}", file=sys.stderr)
                return 1
            cur.execute("CREATE USER IF NOT EXISTS %s@'%%' IDENTIFIED BY %s", (user, pwd))
            cur.execute("ALTER USER %s@'%%' IDENTIFIED BY %s", (user, pwd))
            cur.execute("GRANT %s TO %s@'%%'", (role, user))
            cur.execute("SET DEFAULT ROLE ALL TO %s@'%%'", (user,))
            conn.commit()
            print(f"PROVISION {user}(role={role})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="TraceMind 迁移器")
    sub = ap.add_subparsers(dest="cmd")
    rep = sub.add_parser("repair")
    rep.add_argument("--migration-id", required=True)
    rep.add_argument("--checksum", required=True)
    rep.add_argument("--reason", required=True)
    ap.add_argument("--migrations", default=str(DEFAULT_MIGRATIONS))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--provision", action="store_true")
    ap.add_argument("--init-db", action="store_true",
                    help="先执行 create_databases.sql 建库(与连接同 host,不依赖连接库存在)")
    args = ap.parse_args()

    url = os.environ.get("TRACEMIND_MIGRATE_DB_URL")
    if not url:
        print("FATAL: 需设置 TRACEMIND_MIGRATE_DB_URL", file=sys.stderr)
        return 1
    conn = _connect(url)
    try:
        if args.init_db:
            rc = _init_databases(conn)
            if rc != 0:
                return rc
        # 确保 pymysql 有默认库(URL 无库名时切控制库,供 schema_migrations 访问)
        with conn.cursor() as cur:
            cur.execute("SELECT DATABASE()")
            if not cur.fetchone()[0]:
                cur.execute("USE tracemind_control")
                conn.commit()
        if args.provision:
            return run_provision(conn)
        if args.cmd == "repair":
            # 显式 repair:按 migration_id 定位文件,重置为 started 重新跑
            with conn.cursor() as cur:
                cur.execute("SELECT filename FROM schema_migrations WHERE migration_id=%s",
                            (args.migration_id,))
                row = cur.fetchone()
                if not row:
                    print(f"FATAL: migration_id {args.migration_id} 不存在", file=sys.stderr)
                    return 1
                cur.execute(
                    "UPDATE schema_migrations SET status='started', error_code=%s WHERE migration_id=%s",
                    (f"repair:{args.reason}", args.migration_id))
                conn.commit()
                print(f"REPAIR {row[0]}({args.reason}) 已重置为 started")
            return run_migrations(conn, Path(args.migrations), args.dry_run)
        return run_migrations(conn, Path(args.migrations), args.dry_run)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
