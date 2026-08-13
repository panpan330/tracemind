"""TraceMind 唯一数据库迁移入口。用法:
  python scripts/db/migrate.py [--migrations DIR] [--dry-run]
  python scripts/db/migrate.py repair --migration-id N --checksum SHA --reason "..." [--migrations DIR]
  python scripts/db/migrate.py --provision [--migrations DIR]
环境:TRACEMIND_MIGRATE_DB_URL(必填);账号密码走 TRACEMIND_DB_*_PASSWORD 系列。
"""
import argparse
import hashlib
import os
import sys
import time
from pathlib import Path

import pymysql

DEFAULT_MIGRATIONS = Path(__file__).parent / "migrations"

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
    cfg = _parse_url(url)
    return pymysql.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                           password=cfg["password"], database=cfg["db"] or None,
                           charset="utf8mb4", autocommit=False)


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


def _apply(cur, path: Path) -> None:
    """整文件执行:05/06 迁移用 PREPARE/EXECUTE 多语句,pymysql 一次 execute 可处理。
    不 split(';') —— 避免注释/DELIMITER 误切。"""
    sql = path.read_text(encoding="utf-8")
    cur.execute(sql)


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
                    _apply(cur, path)
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


def run_provision(conn) -> int:
    """账号 Provisioning:密码只来自环境变量,参数化,不拼进 SQL 文件。幂等。"""
    with conn.cursor() as cur:
        for user, pwd_env, role in ACCOUNTS:
            pwd = os.environ.get(pwd_env)
            if not pwd:
                print(f"FATAL: 缺 {pwd_env}", file=sys.stderr)
                return 1
            cur.execute("CREATE USER IF NOT EXISTS %s@'%%' IDENTIFIED BY %s", (user, pwd))
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
    args = ap.parse_args()

    url = os.environ.get("TRACEMIND_MIGRATE_DB_URL")
    if not url:
        print("FATAL: 需设置 TRACEMIND_MIGRATE_DB_URL", file=sys.stderr)
        return 1
    conn = _connect(url)
    try:
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
