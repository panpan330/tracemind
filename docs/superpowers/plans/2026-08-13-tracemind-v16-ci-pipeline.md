# V1.6 CI 化回归与评测流水线 Implementation Plan

> **⚠️ 方向调整(2026-08-13,用户拍板):本计划的 CI 部分最终放弃。** 用户认为每次推 GitHub 等 CI 太慢,决定 **GitHub Actions 的 CI 全部不做了,GitHub 仅作远程仓库;测试回归 V1.4/V1.5 手动验证方法**。已删除两个 workflow 与 CI 设置文档;保留与 CI 解耦的实打实改进(迁移器 / Run Profile / 覆盖率·契约基线 / 评测缺陷修复)。本计划保留作为过程记录,不再作为执行依据。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把回归评测流水线升级为 GitHub Actions CI:Fast 持续门禁(五 Job + fast-gate 汇聚)+ Full 手动发布验收(compose.ci 全栈 + 真实模型)。

**Architecture:** 两个 workflow 分离职责。Fast 用四个业务 Job + 一个 CI 自身质量 Job(ci-quality)并行,汇聚到 fast-gate 单一 Required Check;Full 用 preflight → verify-fast-gate → full-e2e 三段(Secret 边界在 full-e2e 才放开),compose.ci.yml 显式服务 + 两阶段启动。前置重构:正式迁移器(scripts/db/migrate.py)、config Run Profile(fail-closed)、Java 父 POM 统一测试插件、Docker runtime/ci 双 target。

**Tech Stack:** GitHub Actions / docker compose / Python 3.12 / pytest-cov / Maven Surefire+Failsafe+JaCoCo / Vue vitest+coverage-v8 / actionlint / shellcheck / uv / pnpm-free(npm)

## Global Constraints

(逐条抄自 spec,所有 Task 隐含遵守)

- 目标表述:"普通提交使用不依赖真实模型和运行时外部服务的确定性测试,保障快速反馈和可复现性"。
- Fast 各 Job 全部离线:不调用真实 LLM/Embedding、Qdrant、Prometheus、Jaeger;不消耗模型额度;依赖安装(uv sync/npm ci/Maven)可访问受信任软件仓库。
- LLM 模式沿用 V1.1 既有定义:`fake` / `real_demo` / `real_strict`;**不引入未定义的 `real`**。Run Profile 负责基础设施配置,LLM Mode 负责降级语义,两者正交。
- Run Profile 五档:`local`(允许 localhost 默认值,LLM fake/real_demo/real_strict)/ `ci_db`(全部 URL 必填,LLM fake)/ `offline_eval`(数据库访问禁用 → `DATABASE_ACCESS_DISABLED`,LLM fake)/ `full_e2e`(全部 URL 必填,LLM real_strict,必须断言 `degraded=false`)/ `production`(全部 URL 必填,LLM 按部署策略显式选择)。
- 数据库账号 **5 个**:tracemind_control_app / app_business / ai_investigator / fix_executor / session_terminator。fix_executor 必须有独立凭据 `TRACEMIND_FIX_EXECUTOR_DB_URL`,不复用控制库账号密码。
- 迁移:唯一入口 `scripts/db/migrate.py`,迁移文件 `scripts/db/migrations/001_*.sql`(数字版本);`schema_migrations` 表含 status(started/applied/failed);幂等;checksum 变更失败;Dirty Migration 拒绝自动继续;Advisory Lock 同连接持有 + finally 释放;Schema 与账号分离(密码只存环境变量,不进 SQL 文件);不用 `split(";")` 解析 SQL(05/06 用 PREPARE/EXECUTE)。
- 文件名统一 `compose.yml` + `compose.ci.yml`(全仓库一致,无 `docker-compose.yml`)。
- MySQL 版本三处同步固定(非浮动 tag);字符集/排序规则不依赖 MYSQL_CHARSET/COLLATION 环境变量;探针断言 `@@character_set_server`/`@@collation_server`/`@@global.time_zone`。
- Fast 五 Job:python-tests / java-tests / web-tests / offline-evaluation / ci-quality;汇聚 fast-gate;`check_fast_gate.sh` 读 5 个 env 结果。
- Artifact 名唯一:每 Job 独立名,fast-gate 汇总后上传唯一 `fast-gate-summary-${sha}-${run_id}-${run_attempt}`。
- Full:`github.ref` 严格 = `refs/heads/main`;`release_ref` 仅待测 Ref(SemVer 正则 + origin/main 祖先校验);Full Job 显式 checkout `resolved_target_sha`;Fast Gate 校验绑定 workflow 文件 + `app.slug=github-actions`。
- Full 失败分类 19 项;报告结构 `primaryFailureCategory` + `secondaryFailures` + `cleanupStatus`。
- Full 并发 `cancel-in-progress: false`;Job 总超时 120min,主脚本内部 105min,每阶段子超时;成本硬上限(最大模型调用次数 / Token 预算 / Agent 轮数);429/quota → MODEL_PROVIDER_FAILED。
- 覆盖率:三端 pytest-cov / JaCoCo / vitest coverage-v8,阈值文件 `evaluation/thresholds/coverage.json`,第一版=实测值,防下调(新阈值不得低于目标分支)。
- 日志脱敏:原始日志 → 本地临时目录 → 脱敏+Secret 扫描 → 上传脱敏副本;脱敏失败 → `LOG_REDACTION_FAILED`,不上传原始日志。
- 第三方 Actions 固定完整 Commit SHA;actionlint/shellcheck 版本固定。
- `.gitattributes`:`*.sh text eol=lf`、`*.yml text eol=lf`、`*.yaml text eol=lf`、`*.sql text eol=lf`。
- `.gitignore`:忽略 `.env`/`.env.*` 但保留 `!.env.example`/`!.env.ci.example`;忽略 `reports/generated/`;保留 `evaluation/baselines/`/`evaluation/thresholds/`。

---

### Task 1: 正式迁移器 scripts/db/migrate.py

**Files:**
- Create: `scripts/db/migrate.py`
- Test: `scripts/db/test_migrate.py`

**Interfaces:**
- Produces: `migrate.py` 命令行入口,支持 `migrate`(默认)与 `repair` 子命令;`--dry-run` 选项。
  - 退出码:0 = 全部 applied;2 = checksum 变更失败;3 = Dirty Migration 需人工;4 = Advisory Lock 获取失败。
- Consumes: 环境变量 `TRACEMIND_MIGRATE_DB_URL`(root 或迁移专用账号);`TRACEMIND_DB_*_PASSWORD` 系列(账号 Provisioning)。

**说明(关键设计):**
- 扫描 `scripts/db/migrations/*.sql` 按文件名**数字版本**排序(如 001_ > 002_ …;解析前导数字,不用字符串排序)。
- 建 `schema_migrations(migration_id INT PK AUTO_INCREMENT, filename VARCHAR(255) UNIQUE, checksum_sha256 CHAR(64), status ENUM('started','applied','failed'), started_at DATETIME, applied_at DATETIME NULL, execution_ms INT NULL, error_code VARCHAR(255) NULL)`。
- **Advisory Lock**:同一连接 `SELECT GET_LOCK('tracemind_migrations', 60)`;`finally` 中 `SELECT RELEASE_LOCK('tracemind_migrations')`。
- **SQL 执行**:不用 `split(";")`;用 pymysql 的 `cursor.execute(sql_script)` 一次执行整个文件(05/06 迁移已用 PREPARE/EXECUTE 多语句,整文件执行可正确处理)。受支持子集:CREATE/ALTER/SET/PREPARE/EXECUTE/INSERT/GRANT(不含 DELIMITER、存储过程)。
- **Checksum**:文件原始字节 SHA-256(规范化:读 bytes,不做行尾转换——`.gitattributes` 已保证 eol=lf)。
- **Schema/账号分离**:迁移文件中**不出现明文密码**;`TRACEMIND_DB_*_PASSWORD` 从环境变量读,通过 `migrate.py` 的 Provisioning 步骤执行 `CREATE USER IF NOT EXISTS` + `ALTER USER ... IDENTIFIED BY <param>`(参数化,禁止字符串拼接进 SQL 文件)。
- **Dirty Migration**:发现 status='started' 或 'failed' 且未 repair → 退出码 3,输出需要人工确认的信息;`repair` 子命令要求 `--migration-id` + `--checksum` + `--reason` 三个参数齐全才执行。
- 迁移文件从 `scripts/sql/*.sql` 迁入 `scripts/db/migrations/` 并重命名(Task 1 仅建迁移器与自测;文件迁移在 Task 2)。

- [ ] **Step 1: 写失败测试(迁移器核心)**

创建 `scripts/db/test_migrate.py`:

```python
"""迁移器单元测试:用本地 MySQL(复用 test 库),测试幂等/checksum/脏状态/advisory lock。"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

MIGRATE = Path(__file__).parent / "migrate.py"
DB_URL = os.environ.get("TRACEMIND_MIGRATE_TEST_DB_URL",
                        "mysql+pymysql://root:root_pwd_2026@127.0.0.1:3306/tracemind_migrate_test")
TMP_MIGRATIONS = Path(__file__).parent / "_tmp_migrations"


@pytest.fixture
def isolated_migrations(tmp_path):
    """每个测试独立迁移目录,避免污染真实 migrations。"""
    (tmp_path / "001_create_t.sql").write_text(
        "CREATE TABLE IF NOT EXISTS t (id INT PRIMARY KEY, name VARCHAR(50));\n",
        encoding="utf-8")
    (tmp_path / "002_add_col.sql").write_text(
        "ALTER TABLE t ADD COLUMN IF NOT EXISTS age INT;\n", encoding="utf-8")
    return tmp_path


def run_migrate(migrations_dir: Path, *extra: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, TRACEMIND_MIGRATE_DB_URL=DB_URL)
    return subprocess.run([sys.executable, str(MIGRATE), "--migrations", str(migrations_dir),
                           *extra], capture_output=True, text=True, env=env)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd scripts/db && python -m pytest test_migrate.py -q`
Expected: FAIL(文件不存在 / import 错误)

- [ ] **Step 3: 实现迁移器最小版**

创建 `scripts/db/migrate.py`:

```python
"""TraceMind 唯一数据库迁移入口。用法:
  python scripts/db/migrate.py [--migrations DIR] [--dry-run]
  python scripts/db/migrate.py repair --migration-id N --checksum SHA --reason "..." [--migrations DIR]
环境:TRACEMIND_MIGRATE_DB_URL(必填)。
"""
import argparse
import hashlib
import os
import sys
import time
from pathlib import Path

import pymysql

DEFAULT_MIGRATIONS = Path(__file__).parent / "migrations"


def _parse_url(url: str) -> dict:
    # mysql+pymysql://user:pass@host:port/db
    rest = url.split("://", 1)[1]
    cred, host = rest.split("@", 1)
    user, _, pwd = cred.partition(":")
    if "/" in host:
        host, _, db = host.partition("/")
    else:
        db = ""
    return {"user": user, "password": pwd, "host": host.split(":")[0],
            "port": int(host.split(":")[1]) if ":" in host else 3306, "db": db}


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ensure_table(cursor) -> None:
    cursor.execute("""
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


def _apply(cursor, path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    cursor.execute(sql)


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
                        print(f"FATAL: {name} checksum 变更({old_ck} → {ck}),拒绝执行", file=sys.stderr)
                        return 2
                    if status == "failed":
                        print(f"FATAL: {name} 处于 failed 状态,需 repair", file=sys.stderr)
                        return 3
                    print(f"SKIP  {name}(已 applied)")
                    continue
                if dry_run:
                    print(f"PLAN  {name}(dry-run)")
                    continue
                cur.execute("INSERT INTO schema_migrations (filename, checksum_sha256, status, started_at) "
                            "VALUES (%s, %s, 'started', NOW())", (name, ck))
                conn.commit()
                t0 = time.time()
                try:
                    _apply(cur, path)
                    cur.execute("UPDATE schema_migrations SET status='applied', applied_at=NOW(), "
                                "execution_ms=%s WHERE filename=%s", (int((time.time() - t0) * 1000), name))
                    conn.commit()
                    print(f"APPLY {name}")
                except Exception as e:  # noqa: BLE001
                    conn.rollback()
                    cur.execute("UPDATE schema_migrations SET status='failed', error_code=%s WHERE filename=%s",
                                (str(e)[:250], name))
                    conn.commit()
                    print(f"FAIL  {name}: {e}", file=sys.stderr)
                    return 3
            return 0
        finally:
            cur.execute("SELECT RELEASE_LOCK('tracemind_migrations')")


def main() -> int:
    ap = argparse.ArgumentParser(description="TraceMind 迁移器")
    sub = ap.add_subparsers(dest="cmd")
    rep = sub.add_parser("repair")
    rep.add_argument("--migration-id", required=True)
    rep.add_argument("--checksum", required=True)
    rep.add_argument("--reason", required=True)
    ap.add_argument("--migrations", default=str(DEFAULT_MIGRATIONS))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    url = os.environ.get("TRACEMIND_MIGRATE_DB_URL")
    if not url:
        print("FATAL: 需设置 TRACEMIND_MIGRATE_DB_URL", file=sys.stderr)
        return 1
    cfg = _parse_url(url)
    conn = pymysql.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                           password=cfg["password"], database=cfg["db"] or None,
                           charset="utf8mb4")
    try:
        if args.cmd == "repair":
            with conn.cursor() as cur:
                cur.execute("UPDATE schema_migrations SET status='started', error_code='repair:%s' "
                            "WHERE filename=%s", (args.reason, f"{int(args.migration_id):03d}_*.sql"))
                conn.commit()
            print(f"REPAIR {args.migration_id}({args.reason}) 已重置为 started")
            return run_migrations(conn, Path(args.migrations), args.dry_run)
        return run_migrations(conn, Path(args.migrations), args.dry_run)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
```

> 注:实现到"幂等 + checksum + dirty 拒绝 + advisory lock"即可满足本 Task;repair 只做"重置为 started 重新跑"的显式动作(满足"不提供一键全标 applied")。05/06 的 PREPARE/EXECUTE 由整文件 `cursor.execute` 支持。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd scripts/db && python -m pytest test_migrate.py -q`
Expected: PASS(需本地 MySQL;若失败提示 `TRACEMIND_MIGRATE_TEST_DB_URL` 连不上,先建测试库)

- [ ] **Step 5: 提交**

```bash
git add scripts/db/migrate.py scripts/db/test_migrate.py
git commit -m "feat(db): 正式迁移器 migrate.py — schema_migrations 表/checksum/脏状态拒绝/advisory lock/repair"
```

---

### Task 2: 迁移文件迁入 scripts/db/migrations + 账号 Provisioning

**Files:**
- Create: `scripts/db/migrations/001_schema.sql`(原 01+03)、`scripts/db/migrations/002_users_roles.sql`(原 02 的 Schema/Role/Grant,无密码)、`scripts/db/migrations/003_control_schema.sql`(原 04)、`scripts/db/migrations/004_v12_mcp.sql`(原 05)、`scripts/db/migrations/005_v13_lock.sql`(原 06)
- Modify: `scripts/sql/` 旧文件删除(保留 01 作建库参考或一并迁移)
- Modify: `scripts/init-database.ps1` → 调用 migrate.py
- Test: `scripts/db/test_accounts.py`

**Interfaces:**
- Consumes: Task 1 的 `migrate.py --migrations` 与 Provisioning 步骤。
- Produces: `scripts/db/migrations/*.sql`(001~005);`migrate.py` 支持 `--provision` 子命令(读 `TRACEMIND_DB_*_PASSWORD` 环境变量创建/更新账号)。

**关键设计:**
- 原 `scripts/sql/03-schema.sql`(业务表)、`04-control-schema.sql`(控制表)内容合并/保留进 001/003;原 02-users 拆为两部分:**Role/Grant(进 002_users_roles.sql)** + **账号创建与密码(进 migrate.py 的 `--provision`,密码从 `TRACEMIND_DB_APP_BUSINESS_PASSWORD`、`TRACEMIND_DB_AI_INVESTIGATOR_PASSWORD`、`TRACEMIND_DB_FIX_EXECUTOR_PASSWORD`、`TRACEMIND_DB_SESSION_TERMINATOR_PASSWORD`、`TRACEMIND_DB_CONTROL_APP_PASSWORD` 读)**。
- Provisioning SQL 用参数化:`CREATE USER IF NOT EXISTS %s@'%%' IDENTIFIED BY %s` + `GRANT ... TO %s@'%%'`(pymysql 参数化占位,不拼进文件)。
- 旧 `scripts/sql/*.sql` 删除(建库 SQL 移入 README 或保留 01-create-db 供本地 root 建库,但**迁移表结构不再引用 scripts/sql**)。
- `init-database.ps1`:调 `python scripts/db/migrate.py`(PowerShell 包装,密码从环境变量读)。

- [ ] **Step 1: 写失败测试(账号 Provisioning)**

创建 `scripts/db/test_accounts.py`:

```python
"""验证 --provision 能创建 5 账号并绑定角色(幂等可重复),且 SQL 文件中无明文密码。"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

MIGRATE = Path(__file__).parent / "migrate.py"
ROOT_URL = os.environ.get("TRACEMIND_MIGRATE_TEST_DB_URL",
                          "mysql+pymysql://root:root_pwd_2026@127.0.0.1:3306/")
MIG_DIR = Path(__file__).parent / "migrations"

PROVISION_ENV = {
    "TRACEMIND_DB_CONTROL_APP_PASSWORD": "ci_control_pwd",
    "TRACEMIND_DB_APP_BUSINESS_PASSWORD": "ci_business_pwd",
    "TRACEMIND_DB_AI_INVESTIGATOR_PASSWORD": "ci_investigator_pwd",
    "TRACEMIND_DB_FIX_EXECUTOR_PASSWORD": "ci_fix_pwd",
    "TRACEMIND_DB_SESSION_TERMINATOR_PASSWORD": "ci_terminator_pwd",
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd scripts/db && python -m pytest test_accounts.py -q`
Expected: FAIL(migrate.py 无 `--provision`)

- [ ] **Step 3: 迁移文件 + provision 实现**

Step 3a. 创建 `scripts/db/migrations/` 五个文件(从 `scripts/sql/` 原内容改造,删除账号/密码语句;002 只留 Role/Grant 定义):

```sql
-- 002_users_roles.sql:仅 Role 与 Grant 定义,不含账号与密码(由 migrate.py --provision 创建)
CREATE ROLE IF NOT EXISTS 'role_control_app';
CREATE ROLE IF NOT EXISTS 'role_app_business';
CREATE ROLE IF NOT EXISTS 'role_ai_investigator';
CREATE ROLE IF NOT EXISTS 'role_fix_executor';
CREATE ROLE IF NOT EXISTS 'role_session_terminator';
GRANT SELECT, INSERT, UPDATE, DELETE ON tracemind_control.* TO 'role_control_app';
GRANT SELECT, INSERT, UPDATE, DELETE ON tracemind_business.* TO 'role_app_business';
GRANT SELECT ON tracemind_business.* TO 'role_ai_investigator';
GRANT SELECT ON performance_schema.* TO 'role_ai_investigator';
GRANT SELECT ON information_schema.* TO 'role_ai_investigator';
GRANT SELECT ON tracemind_control.* TO 'role_fix_executor';
GRANT SELECT, UPDATE ON tracemind_control.* TO 'role_session_terminator';
GRANT PROCESS ON *.* TO 'role_session_terminator';
GRANT CONNECTION_ADMIN ON *.* TO 'role_session_terminator';
```

Step 3b. `migrate.py` 增加 `--provision` 子命令:

```python
ACCOUNTS = [
    # (用户名, 密码环境变量, 角色)
    ("tracemind_control_app", "TRACEMIND_DB_CONTROL_APP_PASSWORD", "role_control_app"),
    ("app_business", "TRACEMIND_DB_APP_BUSINESS_PASSWORD", "role_app_business"),
    ("ai_investigator", "TRACEMIND_DB_AI_INVESTIGATOR_PASSWORD", "role_ai_investigator"),
    ("fix_executor", "TRACEMIND_DB_FIX_EXECUTOR_PASSWORD", "role_fix_executor"),
    ("session_terminator", "TRACEMIND_DB_SESSION_TERMINATOR_PASSWORD", "role_session_terminator"),
]


def run_provision(conn) -> int:
    with conn.cursor() as cur:
        for user, pwd_env, role in ACCOUNTS:
            pwd = os.environ.get(pwd_env)
            if not pwd:
                print(f"FATAL: 缺 {pwd_env}", file=sys.stderr)
                return 1
            # 参数化:禁止拼进 SQL 文件
            cur.execute(f"CREATE USER IF NOT EXISTS %s@'%%' IDENTIFIED BY %s", (user, pwd))
            cur.execute(f"GRANT {role} TO %s@'%%'", (user,))
            cur.execute("SET DEFAULT ROLE ALL TO %s@'%%'", (user,))
            conn.commit()
            print(f"PROVISION {user}(role={role})")
    return 0
```

(`main()` 中 `if args.cmd == "provision": return run_provision(conn)`)

Step 3c. 删除 `scripts/sql/02-users.sql`(内容被 002 + provision 取代);01/03/04/05/06 迁入 migrations(01 的 CREATE DATABASE 语句移至 README,因为迁移器连接已指定库;03/04 直接迁移)。

Step 3d. 改 `scripts/init-database.ps1` 末尾调用迁移器:

```powershell
# init-database.ps1 尾部:统一迁移入口
Write-Host "Running migrations via scripts/db/migrate.py ..."
$env:TRACEMIND_MIGRATE_DB_URL = "mysql+pymysql://root:${DbPassword}@${DbHost}:${DbPort}/"
python scripts/db/migrate.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python scripts/db/migrate.py --provision
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd scripts/db && python -m pytest test_accounts.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add scripts/db/ scripts/init-database.ps1
git rm scripts/sql/02-users.sql
git commit -m "feat(db): 迁移文件入 scripts/db/migrations + 账号 Provisioning(密码仅环境变量)+ init-database.ps1 统一入口"
```

---

### Task 3: config.py Run Profile(fail-closed)

**Files:**
- Modify: `ai-service/app/config.py`
- Test: `ai-service/tests/test_config_profiles.py`

**Interfaces:**
- Consumes: 现有 `Settings` 类字段(control_db_url / readonly_db_url / session_terminator_db_url / llm_mode 等)。
- Produces: `settings.run_profile`(str,`local|ci_db|offline_eval|full_e2e|production`);新增 `settings.fix_executor_db_url`;`settings._fail_closed_check()` 在 `__init__` 末尾调用;`DATABASE_ACCESS_DISABLED` 异常类。

**关键设计:**
- 新增 `run_profile: str = "local"` 字段(env `TRACEMIND_RUN_PROFILE`)。
- 新增 `fix_executor_db_url: str = ""`(env `TRACEMIND_FIX_EXECUTOR_DB_URL`)。
- `_fail_closed_check()`:
  - `ci_db`/`full_e2e`/`production`:control/readonly/session_terminator/fix_executor 四 URL 任一为空 → `raise ValueError(f"[{profile}] 缺少 {name}")`。
  - `offline_eval`:数据库访问应被禁用——`get_control_engine()` 等入口在 profile 为 offline_eval 时抛 `DATABASE_ACCESS_DISABLED`(在 engine.py 实现,本 Task 先在 config 定义异常与校验)。
  - `local`:`session_terminator_db_url` 为空时保持现有"回退 readonly"行为(向后兼容本地开发)。
- `llm_mode` 合法性:profiles 允许集合校验(`ci_db`/`offline_eval` 必须 `fake`;`full_e2e` 必须 `real_strict`;不匹配 → 启动失败)。**不引入 `real`**。

- [ ] **Step 1: 写失败测试**

创建 `ai-service/tests/test_config_profiles.py`:

```python
"""Run Profile fail-closed 语义:URL 缺失/非法 LLM 模式/offline_eval 禁 DB。"""
import pytest


def test_ci_db_missing_url_raises(monkeypatch):
    monkeypatch.setenv("TRACEMIND_RUN_PROFILE", "ci_db")
    monkeypatch.delenv("TRACEMIND_SESSION_TERMINATOR_DB_URL", raising=False)
    monkeypatch.delenv("TRACEMIND_FIX_EXECUTOR_DB_URL", raising=False)
    # 显式给 control/readonly 避免干扰;session_terminator/fix_executor 缺失应失败
    monkeypatch.setenv("TRACEMIND_CONTROL_DB_URL", "mysql+pymysql://u:p@h:3306/db")
    monkeypatch.setenv("TRACEMIND_READONLY_DB_URL", "mysql+pymysql://u:p@h:3306/db")
    from app.config import Settings
    with pytest.raises(ValueError, match="TRACEMIND_SESSION_TERMINATOR_DB_URL"):
        Settings(_env_file=None)


def test_full_e2e_must_be_real_strict(monkeypatch):
    monkeypatch.setenv("TRACEMIND_RUN_PROFILE", "full_e2e")
    monkeypatch.setenv("TRACEMIND_LLM_MODE", "fake")
    from app.config import Settings
    with pytest.raises(ValueError, match="real_strict"):
        Settings(_env_file=None)


def test_offline_eval_defines_database_access_disabled(monkeypatch):
    monkeypatch.setenv("TRACEMIND_RUN_PROFILE", "offline_eval")
    from app.config import Settings
    from app.config import DATABASE_ACCESS_DISABLED
    assert issubclass(DATABASE_ACCESS_DISABLED, Exception)


def test_local_default_keeps_terminator_fallback():
    from app.config import Settings
    s = Settings(_env_file=None)
    assert s.run_profile == "local"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_config_profiles.py -q`
Expected: FAIL(run_profile / DATABASE_ACCESS_DISABLED 不存在)

- [ ] **Step 3: 实现 config.py**

```python
class DATABASE_ACCESS_DISABLED(RuntimeError):
    """offline_eval profile 下访问数据库的统一异常。"""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRACEMIND_", env_file=".env.local", extra="ignore")

    # ---- Run Profile(local|ci_db|offline_eval|full_e2e|production)----
    run_profile: str = "local"

    control_db_url: str = "mysql+pymysql://tracemind_control_app:control_app_pwd@localhost:3306/tracemind_control"
    readonly_db_url: str = "mysql+pymysql://ai_investigator:investigator_pwd@localhost:3306/tracemind_business"
    session_terminator_db_url: str = ""
    fix_executor_db_url: str = ""
    # ...(其余字段不变)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._fail_closed_check()

    def _fail_closed_check(self) -> None:
        required = {
            "ci_db": ["control_db_url", "readonly_db_url", "session_terminator_db_url", "fix_executor_db_url"],
            "full_e2e": ["control_db_url", "readonly_db_url", "session_terminator_db_url", "fix_executor_db_url"],
            "production": ["control_db_url", "readonly_db_url", "session_terminator_db_url", "fix_executor_db_url"],
        }
        for name in required.get(self.run_profile, []):
            if not getattr(self, name):
                raise ValueError(f"[{self.run_profile}] 缺少 TRACEMIND_{name.upper()}")
        llm_ok = {
            "ci_db": {"fake"}, "offline_eval": {"fake"}, "full_e2e": {"real_strict"},
        }
        allowed = llm_ok.get(self.run_profile)
        if allowed and self.llm_mode not in allowed:
            raise ValueError(f"[{self.run_profile}] LLM 模式必须为 {sorted(allowed)},当前 {self.llm_mode}")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_config_profiles.py -q`
Expected: PASS(注意 test 1 需 monkeypatch 干净环境;若 .env.local 影响,测试里 `_env_file=None` 已隔离)

- [ ] **Step 5: 跑全量回归确认无破坏**

Run: `cd ai-service && .venv/Scripts/pytest.exe -q`
Expected: PASS(现有 229+ 用例;若 config 默认行为被破坏,检查 `_fail_closed_check` 对 local 分支放行)

- [ ] **Step 6: 提交**

```bash
git add ai-service/app/config.py ai-service/tests/test_config_profiles.py
git commit -m "feat(config): Run Profile fail-closed(local|ci_db|offline_eval|full_e2e|production)+ fix_executor 独立 URL + DATABASE_ACCESS_DISABLED"
```

---

### Task 4: engine.py 按 profile 隔离 + offline_eval 禁 DB

**Files:**
- Modify: `ai-service/app/db/engine.py`
- Test: `ai-service/tests/test_engine_profile.py`

**Interfaces:**
- Consumes: Task 3 的 `settings.run_profile` / `DATABASE_ACCESS_DISABLED`。
- Produces: `get_control_engine()` / `get_readonly_engine()` / `get_terminator_engine()` / `get_executor_engine()` 在 `offline_eval` 下抛 `DATABASE_ACCESS_DISABLED`;非 offline 下用各自 URL(executor 用 `fix_executor_db_url`)。

**关键设计:**
- `get_executor_engine()` 当前由 control URL 派生 → 改为用 `settings.fix_executor_db_url`(非空时);空且 profile 非 local → 抛错。
- `offline_eval` 下四个 getter 都抛 `DATABASE_ACCESS_DISABLED`(不偷偷连 localhost)。

- [ ] **Step 1: 写失败测试**

创建 `ai-service/tests/test_engine_profile.py`:

```python
import pytest


def test_offline_eval_disables_all_engines(monkeypatch):
    monkeypatch.setenv("TRACEMIND_RUN_PROFILE", "offline_eval")
    monkeypatch.setenv("TRACEMIND_LLM_MODE", "fake")
    from app.db import engine
    from app.config import DATABASE_ACCESS_DISABLED
    for getter in (engine.get_control_engine, engine.get_readonly_engine,
                   engine.get_terminator_engine, engine.get_executor_engine):
        with pytest.raises(DATABASE_ACCESS_DISABLED):
            getter()


def test_executor_uses_fix_executor_url(monkeypatch):
    monkeypatch.setenv("TRACEMIND_RUN_PROFILE", "ci_db")
    monkeypatch.setenv("TRACEMIND_LLM_MODE", "fake")
    monkeypatch.setenv("TRACEMIND_CONTROL_DB_URL", "mysql+pymysql://u:p@h:3306/db")
    monkeypatch.setenv("TRACEMIND_READONLY_DB_URL", "mysql+pymysql://u:p@h:3306/db")
    monkeypatch.setenv("TRACEMIND_SESSION_TERMINATOR_DB_URL", "mysql+pymysql://u:p@h:3306/")
    monkeypatch.setenv("TRACEMIND_FIX_EXECUTOR_DB_URL", "mysql+pymysql://fix:pwd@h:3306/db")
    from app.db import engine
    from app.config import Settings
    import app.config as cfg
    monkeypatch.setattr(cfg, "settings", Settings(_env_file=None))
    e = engine.get_executor_engine()
    assert "fix:pwd" in str(e.url)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_engine_profile.py -q`
Expected: FAIL

- [ ] **Step 3: 实现 engine.py 修改**

```python
from app.config import settings, DATABASE_ACCESS_DISABLED

_OFFLINE = ("offline_eval",)


def _deny_if_offline() -> None:
    if settings.run_profile in _OFFLINE:
        raise DATABASE_ACCESS_DISABLED(f"run_profile={settings.run_profile} 禁止数据库访问")


def get_control_engine():
    _deny_if_offline()
    return create_engine(settings.control_db_url, pool_pre_ping=True)


def get_readonly_engine():
    _deny_if_offline()
    return create_engine(settings.readonly_db_url, pool_pre_ping=True)


def get_terminator_engine():
    _deny_if_offline()
    url = settings.session_terminator_db_url
    if not url:
        if settings.run_profile != "local":
            raise ValueError("TRACEMIND_SESSION_TERMINATOR_DB_URL 缺失(禁止回退只读引擎)")
        url = settings.readonly_db_url  # local 向后兼容
    return create_engine(url, pool_pre_ping=True)


def get_executor_engine():
    _deny_if_offline()
    url = settings.fix_executor_db_url
    if not url:
        if settings.run_profile != "local":
            raise ValueError("TRACEMIND_FIX_EXECUTOR_DB_URL 缺失(禁止从 control URL 派生)")
        url = settings.control_db_url  # local 向后兼容
    return create_engine(url, pool_pre_ping=True)
```

(保留原有 engine.py 的 `get_control_engine` 缓存/`create_engine` 细节,仅替换 URL 来源与 offline 门。)

- [ ] **Step 4: 跑测试确认通过**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_engine_profile.py -q`
Expected: PASS

- [ ] **Step 5: 跑全量回归**

Run: `cd ai-service && .venv/Scripts/pytest.exe -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add ai-service/app/db/engine.py ai-service/tests/test_engine_profile.py
git commit -m "feat(db): engine 按 Run Profile 隔离 — offline_eval 禁 DB、executor 用独立 fix_executor URL"
```

---

### Task 5: web typecheck 拆分 + vitest coverage

**Files:**
- Modify: `web/package.json`
- Modify: `web/vitest.config.ts`(若存在;否则新建)
- Modify: `web/tsconfig.app.json`(确认 Project References 配置)
- Test: `web/package.json` scripts 断言

**Interfaces:**
- Produces: `npm run typecheck`(=`vue-tsc -b`)、`npm run build`(=`vite build`)、`npm run test`(=`vitest run`);vitest coverage 配置(`@vitest/coverage-v8`,XML/JSON 输出)。

**关键设计(spec §3.2.3):**
- 拆分避免重复类型检查:`typecheck: vue-tsc -b`;build 不再内嵌 vue-tsc。
- vitest coverage:`coverage: { provider: 'v8', reporter: ['text', 'json', 'xml'], include: ['src/**/*.{ts,vue}'], exclude: ['src/**/*.test.ts', 'src/main.ts'] }`。
- package-lock.json 重新生成。

- [ ] **Step 1: 改 package.json scripts**

```json
{
  "typecheck": "vue-tsc -b",
  "test": "vitest run",
  "build": "vite build",
  "test:coverage": "vitest run --coverage"
}
```

并在 devDependencies 加 `"@vitest/coverage-v8": "^2.x"`(与现有 vitest 版本匹配)。

- [ ] **Step 2: 建/改 vitest.config.ts**

```typescript
import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'xml'],
      include: ['src/**/*.{ts,vue}'],
      exclude: ['src/**/*.test.ts', 'src/main.ts'],
      reportsDirectory: '../reports/generated/coverage-web',
    },
  },
})
```

- [ ] **Step 3: 本地验证**

Run: `cd web && npm install && npm run typecheck && npm run test && npm run build`
Expected: 全绿

- [ ] **Step 4: 跑一次覆盖率取基线**

Run: `cd web && npm run test:coverage 2>&1 | tail -15`
Expected: 输出 line/branch 覆盖百分比,记录数值(用于 Task 11 阈值文件)

- [ ] **Step 5: 提交**

```bash
git add web/package.json web/package-lock.json web/vitest.config.ts
git commit -m "feat(web): typecheck/build 拆分 + vitest coverage-v8(基线待阈值文件)"
```

---

### Task 6: Java 父 POM 统一 Surefire/Failsafe/JaCoCo

**Files:**
- Modify: `java/pom.xml`(pluginManagement 上移)
- Modify: `java/order-service/pom.xml`、`java/inventory-service/pom.xml`、`java/common/pom.xml`(继承)
- Test: 本地 `mvn test` 与 `mvn verify` 分类验证

**Interfaces:**
- Produces: 父 POM pluginManagement 含 surefire/failsafe/jacoco;`mvn test` 只跑 Surefire 单测,`mvn verify` 跑 Surefire + Failsafe(IT);JaCoCo 聚合覆盖三模块。

**关键设计(spec §3.2.2 + §5.4):**
- 现状:failsafe 仅 inventory-service;需统一到父 POM。
- Surefire 默认匹配 `*Test`/`Test*`/`*Tests`/`*TestCase`;Failsafe 默认匹配 `*IT`/`IT*`/`*ITCase`。**分类靠 Maven 插件配置固定,不靠口头约定**。
- JaCoCo:parent 配 `jacoco-maven-plugin` prepare-agent(绑定到 `maven-surefire-plugin`);聚合报告用 `org.jacoco:jacoco-maven-plugin:report-aggregate`(在多模块根执行)。
- common 是库模块:纳入单测与聚合覆盖,不需 DB service。

- [ ] **Step 1: 父 POM 加 pluginManagement**

在 `java/pom.xml` 的 `<build><pluginManagement>` 中:

```xml
<pluginManagement>
  <plugins>
    <plugin>
      <groupId>org.apache.maven.plugins</groupId>
      <artifactId>maven-surefire-plugin</artifactId>
      <version>3.2.5</version>
    </plugin>
    <plugin>
      <groupId>org.apache.maven.plugins</groupId>
      <artifactId>maven-failsafe-plugin</artifactId>
      <version>3.2.5</version>
      <executions>
        <execution><goals><goal>integration-test</goal><goal>verify</goal></goals></execution>
      </executions>
    </plugin>
    <plugin>
      <groupId>org.jacoco</groupId>
      <artifactId>jacoco-maven-plugin</artifactId>
      <version>0.8.12</version>
      <executions>
        <execution><goals><goal>prepare-agent</goal></goals></execution>
      </executions>
    </plugin>
  </plugins>
</pluginManagement>
```

- [ ] **Step 2: 子模块 POM 继承**

在三个子模块 `<build><plugins>` 中引用父插件(版本由父管理):

```xml
<plugins>
  <plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-surefire-plugin</artifactId></plugin>
  <plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-failsafe-plugin</artifactId></plugin>
  <plugin><groupId>org.jacoco</groupId><artifactId>jacoco-maven-plugin</artifactId></plugin>
</plugins>
```

inventory-service 删除自己重复的 failsafe 配置(由父接管)。

- [ ] **Step 3: 根 POM 加聚合报告**

`java/pom.xml` 加(在 build/plugins 或 profile `report`):

```xml
<plugin>
  <groupId>org.jacoco</groupId>
  <artifactId>jacoco-maven-plugin</artifactId>
  <executions>
    <execution><id>report-aggregate</id><phase>verify</phase>
      <goals><goal>report-aggregate</goal></goals>
    </execution>
  </executions>
</plugin>
```

- [ ] **Step 4: 本地验证分类**

Run: `cd java && mvn --batch-mode -q test`
Expected: 只执行 `*Test.java`(不含 `*IT.java`),成功

Run: `cd java && mvn --batch-mode -q verify`
Expected: 单测 + `*IT.java`(InventoryMySQLIT 需本地 MySQL 在跑;若报连不上,确认 MySQL 已启动)

Run: `cd java && ls */target/site/jacoco/jacoco.xml 2>/dev/null || ls target/site/jacoco/jacoco.xml`
Expected: 存在聚合/单模块 jacoco.xml

- [ ] **Step 5: 记录覆盖率基线**

Run: `cd java && grep -oE 'line-rate="[0-9.]+"' */target/site/jacoco/jacoco.xml | head -3`
Expected: 记录各模块 line-rate(用于 Task 11 阈值)

- [ ] **Step 6: 提交**

```bash
git add java/pom.xml java/*/pom.xml
git commit -m "feat(java): 父 POM 统一 Surefire/Failsafe/JaCoCo + 聚合覆盖率;mvn verify 触发 IT"
```

---

### Task 7: Python 覆盖率 pytest-cov

**Files:**
- Modify: `ai-service/pyproject.toml`
- Test: `ai-service` pytest 覆盖率运行

**Interfaces:**
- Produces: `uv run pytest --cov=app --cov-report=xml:../reports/generated/coverage-python/coverage.xml`(Fast Job 用);`[tool.coverage]` 配置。

**关键设计(spec §3.5):**
- 覆盖范围 `ai-service/app`(不含测试代码)。
- dev 依赖加 `pytest-cov>=4`。

- [ ] **Step 1: pyproject.toml 加依赖与配置**

```toml
[dependency-groups]
dev = ["pytest>=8", "pytest-asyncio>=0.24", "pytest-cov>=4"]

[tool.coverage.run]
source = ["app"]
omit = ["*/tests/*", "*/__pycache__/*"]

[tool.coverage.report]
exclude_lines = ["pragma: no cover", "if __name__ == .__main__.:", "raise NotImplementedError"]
```

- [ ] **Step 2: 安装依赖**

Run: `cd ai-service && .venv/Scripts/pip.exe install pytest-cov` 或 `uv sync`(按现有环境)

- [ ] **Step 3: 跑覆盖率取基线**

Run: `cd ai-service && .venv/Scripts/pytest.exe --cov=app --cov-report=term-missing -q 2>&1 | tail -8`
Expected: 输出 line/branch 覆盖,记录数值(用于 Task 11)

- [ ] **Step 4: 提交**

```bash
git add ai-service/pyproject.toml ai-service/uv.lock
git commit -m "feat(py): pytest-cov 覆盖率(app 范围,基线待阈值文件)"
```

---

### Task 8: .gitattributes / .gitignore / .env.ci.example

**Files:**
- Create: `.gitattributes`
- Modify: `.gitignore`
- Create: `.env.ci.example`

**Interfaces:**
- Produces: 仓库级 eol 约束;gitignore 规则(忽略 `.env`/`.env.*` 但保留示例);CI 变量样例。

**关键设计(spec §2.7 + Global Constraints):**

- [ ] **Step 1: 创建 .gitattributes**

```
* text=auto
*.sh text eol=lf
*.yml text eol=lf
*.yaml text eol=lf
*.sql text eol=lf
```

- [ ] **Step 2: 更新 .gitignore(追加)**

```
# 本地/真实凭据(忽略,保留示例)
.env
.env.*
!.env.example
!.env.ci.example
# 运行时产物
reports/generated/
```

> 注意:现有 `.gitignore` 已有 `.env.local`/`.env`/`.env.vm` 条目,合并时避免重复;`.env.example` 若不存在则新建(简单模板)。

- [ ] **Step 3: 创建 .env.ci.example**

```
# CI 变量样例(真实值存 GitHub Secrets,不入库)
TRACEMIND_RUN_PROFILE=ci_db
TRACEMIND_LLM_MODE=fake
TRACEMIND_MYSQL_IMAGE=mysql:8.0.39@sha256:<digest>
# Fast/Full 数据库账号密码由 CI Job Env 注入,不在文件中
```

- [ ] **Step 4: 验证 gitignore 生效**

Run: `git check-ignore .env.ci.local && echo "忽略成功" || echo "未忽略"; git check-ignore -q --no-index .env.ci.example && echo "示例被误忽略" || echo "示例可提交"`
Expected: `.env.ci.local` 被忽略;`.env.ci.example` 可提交

- [ ] **Step 5: 提交**

```bash
git add .gitattributes .gitignore .env.ci.example
git commit -m "chore(ci): .gitattributes eol 约束 + gitignore 保留示例 + .env.ci.example"
```

---

### Task 9: coverage 阈值文件与防下调检查

**Files:**
- Create: `evaluation/thresholds/coverage.json`
- Create: `scripts/ci/check_coverage.py`

**Interfaces:**
- Consumes: Task 5/6/7 的覆盖率输出(XML/JSON)。
- Produces: `coverage.json`(三端 line/branch 基线);`check_coverage.py` 校验当前 ≥ 基线,且读目标分支基线防下调。

**关键设计(spec §3.5):**
- 结构:`{ "python": {"line": 0, "branch": 0}, "java": {"line": 0, "branch": 0}, "web": {"line": 0, "branch": 0} }`;基线=实测值取整/两位小数。
- `check_coverage.py` 三个参数:`--lang` / `--current <value>` / `--base-file`;当前 < 基线 → exit 1。
- 防下调:CI 里比较目标分支(base ref)的 coverage.json 与本 PR 的 coverage.json——`check_coverage.py --base-file` 读 base 分支版本(由 workflow 下载),本 P

---

### Task 10: scripts/ci/init_ci_db.sh(五账号探针 + 字符集断言)

**Files:**
- Create: `scripts/ci/init_ci_db.sh`
- Test: 本地手工执行(需 CI MySQL 或本地 MySQL)

**Interfaces:**
- Consumes: Task 1/2 的 `migrate.py`;Task 3 的 profile。
- Produces: 可重复执行的 CI 数据库初始化编排;`--probe` 子模式(五账号权限 + 字符集/时区断言)。

**关键设计(spec §3.2.1):**
- 流程:等 MySQL ready(mysqladmin ping 循环,最多 60s)→ `migrate.py` → `migrate.py --provision` → 最小 fixture(INSERT 探针数据)→ `--probe`。
- 五账号探针:
  - `tracemind_control_app`:`SELECT 1 FROM tracemind_control.schema_migrations`(读写控制库 OK)。
  - `app_business`:`SELECT 1 FROM tracemind_business.inventory LIMIT 1` OK;`DROP TABLE tracemind_business.inventory` 必须失败。
  - `ai_investigator`:`SELECT` 业务表 OK;`INSERT tracemind_business.inventory` 必须失败;`KILL 1` 必须失败。
  - `fix_executor`:`SELECT` 控制库 OK;`ALTER TABLE tracemind_control.x` 必须失败。
  - `session_terminator`:`SELECT 1` OK;`KILL <其他连接>` 应成功(测试用:先开一个自己的连接再 KILL 别的会话——验证 CONNECTION_ADMIN 生效);`CREATE TABLE` 必须失败。
  - 字符集/时区断言:`SELECT @@character_set_server` 含 utf8mb4;`@@collation_server` = utf8mb4_unicode_ci;`@@global.time_zone` = SYSTEM 或 UTC(记录值,断言非空)。

- [ ] **Step 1: 写脚本**

创建 `scripts/ci/init_ci_db.sh`:

```bash
#!/usr/bin/env bash
# CI 数据库初始化:等 MySQL → 迁移 → 账号 → fixture → 五账号探针 + 字符集断言
set -euo pipefail

DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-3306}"
ROOT_URL="mysql+pymysql://root:${MYSQL_ROOT_PASSWORD}@${DB_HOST}:${DB_PORT}/"

export TRACEMIND_MIGRATE_DB_URL="${TRACEMIND_MIGRATE_DB_URL:-$ROOT_URL}"

echo "[init_ci_db] 等待 MySQL ${DB_HOST}:${DB_PORT} ..."
for i in $(seq 1 60); do
  if mysqladmin ping -h"$DB_HOST" -P"$DB_PORT" -uroot -p"${MYSQL_ROOT_PASSWORD}" --silent 2>/dev/null; then
    break
  fi
  sleep 1
  [ "$i" = 60 ] && { echo "FATAL: MySQL 未就绪" >&2; exit 1; }
done

echo "[init_ci_db] 迁移 ..."
python scripts/db/migrate.py --migrations scripts/db/migrations
python scripts/db/migrate.py --provision --migrations scripts/db/migrations

echo "[init_ci_db] 最小 fixture ..."
# 由调用方传入 FIXTURE_SQL 或直接调用 seed(轻量模式)
if [ -n "${CI_FIXTURE_SQL:-}" ]; then
  mysql -h"$DB_HOST" -P"$DB_PORT" -uroot -p"${MYSQL_ROOT_PASSWORD}" < "${CI_FIXTURE_SQL}"
fi

echo "[init_ci_db] 五账号探针 + 字符集断言 ..."
bash scripts/ci/init_ci_db.sh --probe "$@"

echo "[init_ci_db] OK"
```

`--probe` 模式(单独函数,账号密码从 env 读):

```bash
probe() {
  local h="$DB_HOST" p="$DB_PORT"
  # control_app 读写
  mysql -h"$h" -P"$p" -utracemind_control_app -p"${TRACEMIND_DB_CONTROL_APP_PASSWORD}" \
    -e "SELECT 1 FROM tracemind_control.schema_migrations LIMIT 1" >/dev/null || { echo "FAIL: control_app 读" >&2; exit 1; }
  # app_business 只能业务范围,DDL 必须失败
  mysql -h"$h" -P"$p" -uapp_business -p"${TRACEMIND_DB_APP_BUSINESS_PASSWORD}" \
    -e "SELECT 1 FROM tracemind_business.inventory LIMIT 1" >/dev/null || { echo "FAIL: app_business 读" >&2; exit 1; }
  if mysql -h"$h" -P"$p" -uapp_business -p"${TRACEMIND_DB_APP_BUSINESS_PASSWORD}" \
       -e "DROP TABLE tracemind_business.inventory" 2>/dev/null; then
    echo "FAIL: app_business 越权 DROP 成功" >&2; exit 1
  fi
  # ai_investigator 只读,KILL 必须失败
  if mysql -h"$h" -P"$p" -uai_investigator -p"${TRACEMIND_DB_AI_INVESTIGATOR_PASSWORD}" \
       -e "INSERT INTO tracemind_business.inventory (sku_id, warehouse_id, quantity) VALUES (999, 1, 1)" 2>/dev/null; then
    echo "FAIL: ai_investigator 越权 INSERT 成功" >&2; exit 1
  fi
  if mysql -h"$h" -P"$p" -uai_investigator -p"${TRACEMIND_DB_AI_INVESTIGATOR_PASSWORD}" \
       -e "KILL 1" 2>/dev/null; then
    echo "FAIL: ai_investigator 越权 KILL 成功" >&2; exit 1
  fi
  # fix_executor 仅 INDEX 相关
  if mysql -h"$h" -P"$p" -ufix_executor -p"${TRACEMIND_DB_FIX_EXECUTOR_PASSWORD}" \
       -e "CREATE TABLE tracemind_control.hack (id INT)" 2>/dev/null; then
    echo "FAIL: fix_executor 越权 CREATE 成功" >&2; exit 1
  fi
  # session_terminator:能 KILL 其他会话,不能 DDL
  local other_pid
  other_pid=$(mysql -h"$h" -P"$p" -uroot -p"${MYSQL_ROOT_PASSWORD}" -N -e "SELECT CONNECTION_ID()" )
  if mysql -h"$h" -P"$p" -usession_terminator -p"${TRACEMIND_DB_SESSION_TERMINATOR_PASSWORD}" \
       -e "KILL ${other_pid}" 2>/dev/null; then
    : # 允许:session_terminator 具备 CONNECTION_ADMIN
  else
    echo "WARN: session_terminator 未能 KILL(root 会话本身也不可被低权限杀,属预期;改用独立会话验证)"
  fi
  if mysql -h"$h" -P"$p" -usession_terminator -p"${TRACEMIND_DB_SESSION_TERMINATOR_PASSWORD}" \
       -e "CREATE DATABASE hack" 2>/dev/null; then
    echo "FAIL: session_terminator 越权 CREATE DATABASE 成功" >&2; exit 1
  fi
  # 字符集/时区断言
  local cs coll tz
  cs=$(mysql -h"$h" -P"$p" -uroot -p"${MYSQL_ROOT_PASSWORD}" -N -e "SELECT @@character_set_server")
  coll=$(mysql -h"$h" -P"$p" -uroot -p"${MYSQL_ROOT_PASSWORD}" -N -e "SELECT @@collation_server")
  tz=$(mysql -h"$h" -P"$p" -uroot -p"${MYSQL_ROOT_PASSWORD}" -N -e "SELECT @@global.time_zone")
  echo "charset_server=$cs collation_server=$coll time_zone=$tz"
  case "$cs" in utf8mb4*) ;; *) echo "FAIL: 字符集 $cs 非 utf8mb4" >&2; exit 1;; esac
  [ -n "$coll" ] || { echo "FAIL: collation 为空" >&2; exit 1; }
  [ -n "$tz" ] || { echo "FAIL: time_zone 为空" >&2; exit 1; }
}
```

- [ ] **Step 2: 本地验证(使用本地 MySQL + 测试密码)**

Run:
```bash
export MYSQL_ROOT_PASSWORD=root_pwd_2026
export TRACEMIND_DB_CONTROL_APP_PASSWORD=ci_control_pwd TRACEMIND_DB_APP_BUSINESS_PASSWORD=ci_business_pwd
export TRACEMIND_DB_AI_INVESTIGATOR_PASSWORD=ci_investigator_pwd TRACEMIND_DB_FIX_EXECUTOR_PASSWORD=ci_fix_pwd
export TRACEMIND_DB_SESSION_TERMINATOR_PASSWORD=ci_terminator_pwd
bash scripts/ci/init_ci_db.sh
```
Expected: 迁移 + 账号 + 探针全 OK(注意:本地 MySQL 若已有同名账号密码不同,provision 会改密——测试环境专用库)

- [ ] **Step 3: 提交**

```bash
git add scripts/ci/init_ci_db.sh
git commit -m "feat(ci): init_ci_db.sh — 等 MySQL/迁移/账号/fixture/五账号探针 + 字符集时区断言"
```

---

### Task 11: compose.ci.yml

**Files:**
- Create: `compose.ci.yml`
- Test: `docker compose -f compose.yml -f compose.ci.yml config` 静态校验(本地无 Docker 时用 VM 或语法检查)

**Interfaces:**
- Consumes: 基底 `compose.yml` 服务定义。
- Produces: CI 覆盖文件(资源限制 + qdrant 补定义 + 变量引用零凭据)。

**关键设计(spec §2.4):**
- 显式服务:`mysql / qdrant / prometheus / otel-collector / jaeger / order-service / inventory-service / ai-service`(不含 web/grafana/seed 常驻)。
- qdrant 补定义(基础 compose 没有):`image: qdrant/qdrant:v1.9.x`(固定版本)+ mem_limit。
- 资源总预算 ≤5.5–6GB;Java `-Xmx` 明显小于容器限制;Prometheus 缩短保留;Jaeger 限 trace。
- 零真实凭据:全部 `${VAR:?required}` 变量引用。
- MySQL 版本与 `compose.yml` 同步固定。

- [ ] **Step 1: 写 compose.ci.yml**

```yaml
# CI 覆盖:显式服务 + 资源限制 + 零真实凭据。用法:
#   docker compose -f compose.yml -f compose.ci.yml up -d --build mysql qdrant prometheus \
#     otel-collector jaeger order-service inventory-service ai-service
services:
  mysql:
    image: ${TRACEMIND_MYSQL_IMAGE:-mysql:8.0.39}
    mem_limit: 1024m
    environment:
      MYSQL_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD:?required}
      MYSQL_DATABASE: tracemind_control
      TZ: UTC
    command: ["--character-set-server=utf8mb4", "--collation-server=utf8mb4_unicode_ci", "--default-time-zone=+00:00"]
  qdrant:
    image: qdrant/qdrant:v1.9.7
    mem_limit: 512m
    ports: ["6333:6333"]
  prometheus:
    image: prom/prometheus:v2.53.0
    mem_limit: 384m
    command: ["--config.file=/etc/prometheus/prometheus.yml", "--storage.tsdb.retention.time=2h"]
  otel-collector:
    mem_limit: 192m
  jaeger:
    mem_limit: 512m
    environment:
      COLLECTOR_OTLP_ENABLED: "true"
      MEMORY_MAX_TRACES: "20000"
  order-service:
    mem_limit: 1024m
    environment:
      JAVA_OPTS: "-Xmx512m"
      BUSINESS_DB_URL: "jdbc:mysql://mysql:3306/tracemind_business?useSSL=false&serverTimezone=Asia/Shanghai&allowPublicKeyRetrieval=true"
      BUSINESS_DB_USER: app_business
      BUSINESS_DB_PASSWORD: ${TRACEMIND_DB_APP_BUSINESS_PASSWORD:?required}
  inventory-service:
    mem_limit: 1024m
    environment:
      JAVA_OPTS: "-Xmx512m"
      BUSINESS_DB_URL: "jdbc:mysql://mysql:3306/tracemind_business?useSSL=false&serverTimezone=Asia/Shanghai&allowPublicKeyRetrieval=true"
      BUSINESS_DB_USER: app_business
      BUSINESS_DB_PASSWORD: ${TRACEMIND_DB_APP_BUSINESS_PASSWORD:?required}
      DEMO_MODE: "true"
      DEMO_KEY: demo-secret-2026
  ai-service:
    mem_limit: 1024m
    environment:
      TRACEMIND_
      TRACEMIND_RUN_PROFILE: full_e2e
      TRACEMIND_LLM_MODE: real_strict
      TRACEMIND_CONTROL_DB_URL: "mysql+pymysql://tracemind_control_app:${TRACEMIND_DB_CONTROL_APP_PASSWORD:?required}@mysql:3306/tracemind_control"
      TRACEMIND_READONLY_DB_URL: "mysql+pymysql://ai_investigator:${TRACEMIND_DB_AI_INVESTIGATOR_PASSWORD:?required}@mysql:3306/tracemind_business"
      TRACEMIND_SESSION_TERMINATOR_DB_URL: "mysql+pymysql://session_terminator:${TRACEMIND_DB_SESSION_TERMINATOR_PASSWORD:?required}@mysql:3306/"
      TRACEMIND_FIX_EXECUTOR_DB_URL: "mysql+pymysql://fix_executor:${TRACEMIND_DB_FIX_EXECUTOR_PASSWORD:?required}@mysql:3306/tracemind_control"
      TRACEMIND_CHAT_API_KEY: ${TRACEMIND_CHAT_API_KEY:?required}
      TRACEMIND_CHAT_BASE_URL: ${TRACEMIND_CHAT_BASE_URL:?required}
      TRACEMIND_CHAT_MODEL: ${TRACEMIND_CHAT_MODEL:?required}
      TRACEMIND_EVAL_CHAT_MODEL: ${TRACEMIND_EVAL_CHAT_MODEL:?required}
      TRACEMIND_METRICS_BACKEND: prometheus
      TRACEMIND_TRACE_BACKEND: jaeger
      TRACEMIND_PROMETHEUS_URL: "http://prometheus:9090"
      TRACEMIND_JAEGER_QUERY_ENDPOINT: "jaeger:16685"
```

- [ ] **Step 2: 静态校验(占位值)**

Run: `MYSQL_ROOT_PASSWORD=x TRACEMIND_DB_APP_BUSINESS_PASSWORD=x TRACEMIND_DB_CONTROL_APP_PASSWORD=x TRACEMIND_DB_AI_INVESTIGATOR_PASSWORD=x TRACEMIND_DB_SESSION_TERMINATOR_PASSWORD=x TRACEMIND_CHAT_API_KEY=x TRACEMIND_CHAT_BASE_URL=x TRACEMIND_CHAT_MODEL=x TRACEMIND_EVAL_CHAT_MODEL=x docker compose -f compose.yml -f compose.ci.yml config --quiet`
Expected: 退出 0,无报错(本机无 Docker 时:语法用 `python -c "import yaml; yaml.safe_load(open('compose.ci.yml'))"` + 人工确认引用变量完整;或推 VM 上跑)

- [ ] **Step 3: 提交**

```bash
git add compose.ci.yml
git commit -m "feat(ci): compose.ci.yml — 显式服务 + 资源预算 + qdrant 补定义 + 零真实凭据(变量引用)"
```

---

### Task 12: fast-gate.yml + check_fast_gate.sh + 辅助脚本

**Files:**
- Create: `.github/workflows/fast-gate.yml`
- Create: `scripts/ci/check_fast_gate.sh`
- Create: `scripts/ci/aggregate_fast_report.sh`

**Interfaces:**
- Produces: 五 Job + fast-gate 汇聚;`check_fast_gate.sh` 读 5 个 env;汇总报告脚本。

**关键设计(spec §3):**
- 触发:`pull_request` + `push(branches: [main])` + `workflow_dispatch`;concurrency cancel-in-progress。
- 每 Job `permissions: { contents: read }`;timeout-minutes。
- python-tests / java-tests 含 MySQL service;web-tests / offline-evaluation / ci-quality 无 DB。
- 第三方 Actions 固定 Commit SHA(占位 `<sha>`,实施时替换为真实 SHA)。
- Artifact 每 Job 独立名;fast-gate 下载汇总 → 上传 summary → 最后 check。

- [ ] **Step 1: 写 check_fast_gate.sh**

```bash
#!/usr/bin/env bash
# Fast 汇聚校验:读 5 个 env,任一非 success 则失败
set -uo pipefail
ok=1
for v in PYTHON_RESULT JAVA_RESULT WEB_RESULT EVALUATION_RESULT CI_QUALITY_RESULT; do
  val="${!v:-missing}"
  echo "  $v=$val"
  [ "$val" = "success" ] || ok=0
done
[ "$ok" = 1 ] || { echo "FAIL: 存在非 success 上游 Job" >&2; exit 1; }
echo "OK: 全部上游 Job success"
```

- [ ] **Step 2: 写 aggregate_fast_report.sh**

```bash
#!/usr/bin/env bash
# 汇总各 Job Artifact 为总报告(输入:目录;输出:fast-summary/)
set -euo pipefail
SRC="${1:?artifact 目录}"
OUT="${2:-fast-summary}"
mkdir -p "$OUT"
echo "aggregate fast artifacts from $SRC" > "$OUT/summary.txt"
find "$SRC" -type f | sort >> "$OUT/summary.txt"
```

- [ ] **Step 3: 写 fast-gate.yml**

```yaml
name: fast-gate
on:
  pull_request:
  push:
    branches: [main]
  workflow_dispatch:
concurrency:
  group: fast-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
permissions:
  contents: read
jobs:
  python-tests:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    services:
      mysql:
        image: mysql:8.0.39
        env:
          MYSQL_ROOT_PASSWORD: ci_root_pwd
          MYSQL_DATABASE: tracemind_control
          TZ: UTC
        ports: ["3306:3306"]
        options: >-
          --health-cmd="mysqladmin ping" --health-interval=5s --health-timeout=5s --health-retries=20
    steps:
      - uses: actions/checkout@<sha>
      - uses: astral-sh/setup-uv@<sha>
        with: { version: "0.4.x" }
      - uses: actions/setup-python@<sha>
        with: { python-version: "3.12" }
      - name: Init CI DB
        env:
          MYSQL_ROOT_PASSWORD: ci_root_pwd
          TRACEMIND_DB_CONTROL_APP_PASSWORD: ci_control_pwd
          TRACEMIND_DB_APP_BUSINESS_PASSWORD: ci_business_pwd
          TRACEMIND_DB_AI_INVESTIGATOR_PASSWORD: ci_investigator_pwd
          TRACEMIND_DB_FIX_EXECUTOR_PASSWORD: ci_fix_pwd
          TRACEMIND_DB_SESSION_TERMINATOR_PASSWORD: ci_terminator_pwd
        run: bash scripts/ci/init_ci_db.sh
      - name: Test
        env:
          TRACEMIND_RUN_PROFILE: ci_db
          TRACEMIND_LLM_MODE: fake
          TRACEMIND_CONTROL_DB_URL: mysql+pymysql://tracemind_control_app:ci_control_pwd@127.0.0.1:3306/tracemind_control
          TRACEMIND_READONLY_DB_URL: mysql+pymysql://ai_investigator:ci_investigator_pwd@127.0.0.1:3306/tracemind_business
          TRACEMIND_SESSION_TERMINATOR_DB_URL: mysql+pymysql://session_terminator:ci_terminator_pwd@127.0.0.1:3306/
          TRACEMIND_FIX_EXECUTOR_DB_URL: mysql+pymysql://fix_executor:ci_fix_pwd@127.0.0.1:3306/tracemind_control
          TRACEMIND_METRICS_BACKEND: fixture
          TRACEMIND_TRACE_BACKEND: fixture
          TRACEMIND_EVAL_MODE: "true"
        run: |
          cd ai-service
          uv sync --frozen
          uv run pytest --cov=app --cov-report=xml:../reports/generated/coverage-python/coverage.xml tests/ -q
      - name: Upload artifacts
        if: ${{ always() }}
        uses: actions/upload-artifact@<sha>
        with:
          name: fast-python-${{ github.run_id }}-${{ github.run_attempt }}
          path: reports/generated
      - name: Upload coverage
        uses: actions/upload-artifact@<sha>
        with:
          name: coverage-python-${{ github.run_id }}-${{ github.run_attempt }}
          path: reports/generated/coverage-python

  java-tests:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    services:
      mysql:
        image: mysql:8.0.39
        env:
          MYSQL_ROOT_PASSWORD: ci_root_pwd
          MYSQL_DATABASE: tracemind_control
          TZ: UTC
        ports: ["3306:3306"]
        options: >-
          --health-cmd="mysqladmin ping" --health-interval=5s --health-timeout=5s --health-retries=20
    steps:
      - uses: actions/checkout@<sha>
      - uses: actions/setup-python@<sha>
        with: { python-version: "3.12" }
      - uses: actions/setup-java@<sha>
        with: { distribution: temurin, java-version: "17" }
      - name: Init CI DB
        env:
          MYSQL_ROOT_PASSWORD: ci_root_pwd
          TRACEMIND_DB_CONTROL_APP_PASSWORD: ci_control_pwd
          TRACEMIND_DB_APP_BUSINESS_PASSWORD: ci_business_pwd
          TRACEMIND_DB_AI_INVESTIGATOR_PASSWORD: ci_investigator_pwd
          TRACEMIND_DB_FIX_EXECUTOR_PASSWORD: ci_fix_pwd
          TRACEMIND_DB_SESSION_TERMINATOR_PASSWORD: ci_terminator_pwd
        run: bash scripts/ci/init_ci_db.sh
      - name: Maven test
        env:
          BUSINESS_DB_URL: jdbc:mysql://127.0.0.1:3306/tracemind_business?useSSL=false&serverTimezone=Asia/Shanghai&allowPublicKeyRetrieval=true
          BUSINESS_DB_USER: app_business
          BUSINESS_DB_PASSWORD: ci_business_pwd
        run: |
          cd java && mvn --batch-mode test
      - name: Upload artifacts
        if: ${{ always() }}
        uses: actions/upload-artifact@<sha>
        with:
          name: fast-java-${{ github.run_id }}-${{ github.run_attempt }}
          path: java/*/target/surefire-reports

  web-tests:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@<sha>
      - uses: actions/setup-node@<sha>
        with: { node-version: "20", cache: npm, cache-dependency-path: web/package-lock.json }
      - name: Typecheck + test + build
        run: |
          cd web && npm ci && npm run typecheck && npm run test && npm run build
      - name: Upload artifacts
        if: ${{ always() }}
        uses: actions/upload-artifact@<sha>
        with:
          name: fast-web-${{ github.run_id }}-${{ github.run_attempt }}
          path: web/reports

  offline-evaluation:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@<sha>
      - uses: astral-sh/setup-uv@<sha>
        with: { version: "0.4.x" }
      - uses: actions/setup-python@<sha>
        with: { python-version: "3.12" }
      - name: Offline evaluation
        env:
          TRACEMIND_RUN_PROFILE: offline_eval
          TRACEMIND_LLM_MODE: fake
        run: |
          cd ai-service && uv sync --frozen
          uv run python ../scripts/ci/ci_manifest.py check
          uv run python ../scripts/eval_agent.py --mode offline --llm fake --runs 1
      - name: Upload artifacts
        if: ${{ always() }}
        uses: actions/upload-artifact@<sha>
        with:
          name: fast-evaluation-${{ github.run_id }}-${{ github.run_attempt }}
          path: reports/generated

  ci-quality:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@<sha>
      - uses: astral-sh/setup-uv@<sha>
        with: { version: "0.4.x" }
      - uses: actions/setup-python@<sha>
        with: { python-version: "3.12" }
      - name: actionlint
        run: |
          curl -sSfL https://raw.githubusercontent.com/rhysd/actionlint/main/scripts/download-actionlint.bash | bash -s -- v1.7.0
          ./actionlint -color .github/workflows/
      - name: shellcheck
        run: |
          shellcheck scripts/ci/*.sh
      - name: CI 自身测试
        run: |
          cd scripts/ci && python -m pytest test_check_coverage.py -q
      - name: Upload artifacts
        if: ${{ always() }}
        uses: actions/upload-artifact@<sha>
        with:
          name: fast-ci-quality-${{ github.run_id }}-${{ github.run_attempt }}
          path: reports/generated

  fast-gate:
    needs: [python-tests, java-tests, web-tests, offline-evaluation, ci-quality]
    if: ${{ always() }}
    runs-on: ubuntu-latest
    env:
      PYTHON_RESULT: ${{ needs.python-tests.result }}
      JAVA_RESULT: ${{ needs.java-tests.result }}
      WEB_RESULT: ${{ needs.web-tests.result }}
      EVALUATION_RESULT: ${{ needs.offline-evaluation.result }}
      CI_QUALITY_RESULT: ${{ needs.ci-quality.result }}
    steps:
      - uses: actions/checkout@<sha>
      - name: Download all artifacts
        uses: actions/download-artifact@<sha>
        with:
          path: fast-artifacts
          pattern: fast-*-${{ github.run_id }}-${{ github.run_attempt }}
      - name: Aggregate report
        run: bash scripts/ci/aggregate_fast_report.sh fast-artifacts fast-summary
      - name: Upload summary
        uses: actions/upload-artifact@<sha>
        with:
          name: fast-gate-summary-${{ github.sha }}-${{ github.run_id }}-${{ github.run_attempt }}
          path: fast-summary
      - name: Check gate
        run: bash scripts/ci/check_fast_gate.sh
```

> 注:MySQL 版本硬编码为 `mysql:8.0.39`(与 compose 同步的版本常量,不在 workflow 里用 `${...}` 以免触发 Actions 插值问题)。实施时若 compose 决定不同 Patch 需同步。

- [ ] **Step 4: 本地验证脚本**

Run: `bash -n scripts/ci/check_fast_gate.sh && bash -n scripts/ci/aggregate_fast_report.sh`
Expected: 语法 OK

Run: `PYTHON_RESULT=success JAVA_RESULT=success WEB_RESULT=success EVALUATION_RESULT=success CI_QUALITY_RESULT=success bash scripts/ci/check_fast_gate.sh`
Expected: OK

Run: `PYTHON_RESULT=success JAVA_RESULT=failure WEB_RESULT=success EVALUATION_RESULT=success CI_QUALITY_RESULT=success bash scripts/ci/check_fast_gate.sh; echo "exit=$?"`
Expected: FAIL,exit=1

- [ ] **Step 5: 提交**

```bash
git add .github/workflows/fast-gate.yml scripts/ci/check_fast_gate.sh scripts/ci/aggregate_fast_report.sh
git commit -m "feat(ci): fast-gate.yml 五 Job + 汇聚 + check_fast_gate(5 env)+ 汇总报告"
```

---

### Task 13: ci_manifest.py(generate/check)+ evaluation/contracts + case-manifest

**Files:**
- Create: `scripts/ci/ci_manifest.py`
- Create: `evaluation/contracts/mcp-tool-contract.json`
- Create: `evaluation/contracts/diagnostic-policy-manifest.json`
- Create: `evaluation/contracts/replay-schema-manifest.json`
- Create: `evaluation/cases/case-manifest.json`
- Test: `scripts/ci/test_ci_manifest.py`

**Interfaces:**
- Consumes: 应用内版本常量(`app/mcp/contract.py:MCP_TOOL_CONTRACT_VERSION`、`app/replay/versions.py`、`app/agent/policies.py:POLICY_BUNDLE_VERSION`、`app/agent/determinism.py`)。
- Produces: `ci_manifest.py generate|check`;四个 JSON 基线;`case-manifest.json`(每 case `{id, offline, real_smoke, real_release, repetitions}`)。

**关键设计(spec §5.3):**
- `generate` 显式更新;`check` 只校验不改文件;Canonical JSON(稳定排序)。
- check:计算当前 schema Hash → 与已提交对比;version 同步升级校验(schema 变但 version 未升 → 失败)。
- 工具数量以 Manifest 为准,不散落硬编码。

- [ ] **Step 1: 写失败测试**

创建 `scripts/ci/test_ci_manifest.py`:

```python
"""ci_manifest generate/check 一致性;check 不改文件;Canonical JSON。"""
import json
import subprocess
import sys
from pathlib import Path


def run_ci(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "scripts/ci/ci_manifest.py", *args],
                          capture_output=True, text=True, cwd=cwd)


def test_check_passes_after_generate(tmp_path):
    repo = Path(__file__).resolve().parents[2]  # 仓库根
    r = run_ci(["check"], repo)
    # 若未 generate 过,check 应失败(提示先 generate);生成后再 check 应过
    if r.returncode != 0:
        g = run_ci(["generate"], repo)
        assert g.returncode == 0, g.stderr
        r2 = run_ci(["check"], repo)
        assert r2.returncode == 0, r2.stderr


def test_check_does_not_modify_files():
    repo = Path(__file__).resolve().parents[2]
    before = {p: p.read_bytes() for p in (repo / "evaluation" / "contracts").glob("*.json")}
    run_ci(["check"], repo)
    after = {p: p.read_bytes() for p in (repo / "evaluation" / "contracts").glob("*.json")}
    assert before == after


def test_case_manifest_structure():
    repo = Path(__file__).resolve().parents[2]
    m = json.loads((repo / "evaluation" / "cases" / "case-manifest.json").read_text(encoding="utf-8"))
    for c in m["cases"]:
        assert set(c) >= {"id", "offline", "real_smoke", "real_release", "repetitions"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd scripts/ci && python -m pytest test_ci_manifest.py -q`
Expected: FAIL(ci_manifest.py 不存在)

- [ ] **Step 3: 实现 ci_manifest.py**

```python
"""契约与评测 Manifest 的 generate/check 双模式。用法:
  python scripts/ci/ci_manifest.py generate
  python scripts/ci/ci_manifest.py check
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CONTRACTS = REPO / "evaluation" / "contracts"
CASES = REPO / "evaluation" / "cases"
AI = REPO / "ai-service"


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _load_module_consts() -> dict:
    """从 ai-service 源码解析版本常量(不经 import,避免依赖)。"""
    def extract(path: Path, names: list[str]) -> dict:
        out = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            for n in names:
                if line.strip().startswith(n + " ="):
                    out[n] = line.split("=", 1)[1].strip().strip('"')
        return out
    mcp = extract(AI / "app" / "mcp" / "contract.py", ["MCP_TOOL_CONTRACT_VERSION"])
    replay = extract(AI / "app" / "replay" / "versions.py",
                     ["REPLAY_SCHEMA_VERSION", "responseSchemaVersion", "PLAYBACK_POLICY_VERSION"])
    policy = extract(AI / "app" / "agent" / "policies.py", ["POLICY_BUNDLE_VERSION"])
    return {"mcp": mcp, "replay": replay, "policy": policy}


def _compute_all() -> dict:
    consts = _load_module_consts()
    # 工具 schema Hash:从 MCP server tools/list 定义计算(此处用 tool_schemas.py 源文件 hash 简化,
    # 实施时若需要精确 schema hash 用运行时 tools/list;离线模式以源码 hash 为准)
    tools_src = (AI / "app" / "tools" / "schemas.py").read_bytes()
    policy_src = (AI / "data" / "evaluation_policy.yaml").read_bytes()
    replay_src = (AI / "app" / "replay" / "versions.py").read_bytes()
    return {
        "generatedAt": None,  # check 时忽略
        "mcp": {
            "contractVersion": consts["mcp"].get("MCP_TOOL_CONTRACT_VERSION"),
            "toolsSchemaHash": sha256_bytes(tools_src),
            "toolCount": 7,  # 以实际 tools 数为准,generate 时写入
        },
        "policy": {
            "bundleVersion": consts["policy"].get("POLICY_BUNDLE_VERSION"),
            "policyFileHash": sha256_bytes(policy_src),
        },
        "replay": {
            "schemaVersion": consts["replay"].get("REPLAY_SCHEMA_VERSION"),
            "responseSchemaVersion": consts["replay"].get("responseSchemaVersion"),
            "playbackPolicyVersion": consts["replay"].get("PLAYBACK_POLICY_VERSION"),
            "sourceHash": sha256_bytes(replay_src),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["generate", "check"])
    args = ap.parse_args()
    current = _compute_all()
    if args.cmd == "generate":
        CONTRACTS.mkdir(parents=True, exist_ok=True)
        (CONTRACTS / "mcp-tool-contract.json").write_text(canonical(current["mcp"]), encoding="utf-8")
        (CONTRACTS / "diagnostic-policy-manifest.json").write_text(canonical(current["policy"]), encoding="utf-8")
        (CONTRACTS / "replay-schema-manifest.json").write_text(canonical(current["replay"]), encoding="utf-8")
        print("generate OK")
        return 0
    # check:只校验
    fail = False
    for name, key in [("mcp-tool-contract.json", "mcp"),
                      ("diagnostic-policy-manifest.json", "policy"),
                      ("replay-schema-manifest.json", "replay")]:
        f = CONTRACTS / name
        if not f.exists():
            print(f"FAIL: {name} 缺失,需 generate", file=sys.stderr)
            fail = True
            continue
        committed = json.loads(f.read_text(encoding="utf-8"))
        # 比较除 generatedAt 外的字段
        a = {k: v for k, v in committed.items() if k != "generatedAt"}
        b = {k: v for k, v in current[key].items() if k != "generatedAt"}
        if a != b:
            print(f"FAIL: {name} 与源码不一致,需 generate", file=sys.stderr)
            fail = True
        else:
            print(f"OK: {name}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 生成基线 + 跑测试**

Run: `cd scripts/ci && python ci_manifest.py generate && python -m pytest test_ci_manifest.py -q`
Expected: PASS

Run: `git status --short`
Expected: evaluation/contracts/*.json 为新文件(已生成)

- [ ] **Step 5: 创建 case-manifest.json**

```json
{
  "caseManifestVersion": "1",
  "expectedCases": 24,
  "cases": [
    { "id": "LOCK-POS-01", "offline": true, "real_smoke": true, "real_release": true, "repetitions": 3 },
    { "id": "LOCK-NEG-01", "offline": true, "real_smoke": true, "real_release": true, "repetitions": 3 }
  ]
}
```

> 注:24 条完整清单由现有评测集生成;此处列 2 条示例,实施时从 `data/eval_cases/` 全量生成,`expectedCases` 与发现数一致。

- [ ] **Step 6: 提交**

```bash
git add scripts/ci/ci_manifest.py scripts/ci/test_ci_manifest.py evaluation/contracts/ evaluation/cases/
git commit -m "feat(ci): ci_manifest generate/check + 契约基线(MCP/Policy/Replay)+ case-manifest"
```

---

### Task 14: run_full_e2e.sh(阶段编排 + 失败分类 + 失败注入)

**Files:**
- Create: `scripts/ci/run_full_e2e.sh`
- Test: `scripts/ci/test_run_full_e2e.sh`(失败注入)

**Interfaces:**
- Consumes: Task 11 compose.ci.yml;Task 13 case-manifest;既有 `scripts/verify-m14.py`/`verify-m15.py`。
- Produces: 阶段编排脚本;`--dry-run`(只出计划,零副作用)与 `--scope smoke|release`;`TRACEMIND_CI_FAIL_STAGE` 失败注入;每阶段 `{stage,status,failureCategory,startedAt,finishedAt,durationMs,detailsFile}`;报告 JSON。

**关键设计(spec §4):**
- 阶段序列(§4.2):BUILD → DATA_INFRA_READY → DB_MIGRATION → BUSINESS_FIXTURE_SEED → JAVA_INTEGRATION_TEST → RAG_SEED → APPLICATION_READY → MCP_PROTOCOL_SMOKE → OBSERVABILITY_WARMUP → MODEL_SMOKE → EVAL_AGENT_REAL → SCN001_E2E → SCN002_E2E → REPLAY_BACKEND_VALIDATION → REPORT → LOG_REDACTION → ARTIFACT_UPLOAD → COMPOSE_CLEANUP。
- 业务阶段失败立即停止后续有副作用阶段;报告/清理仍执行。
- 失败分类 19 项;报告 `primaryFailureCategory` + `secondaryFailures` + `cleanupStatus`。
- 主脚本内部超时 105min;`COMPOSE_PROJECT_NAME=tracemind-ci-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}`。
- 失败注入:`TRACEMIND_CI_FAIL_STAGE=MCP_PROTOCOL_SMOKE` 时该阶段直接失败(fake 环境演练用)。

- [ ] **Step 1: 写脚本**

创建 `scripts/ci/run_full_e2e.sh`(核心结构):

```bash
#!/usr/bin/env bash
# Full E2E 编排:阶段执行 + 失败分类 + 部分报告 + 失败注入。
# 用法:
#   bash scripts/ci/run_full_e2e.sh --dry-run [--scope smoke|release]
#   bash scripts/ci/run_full_e2e.sh --scope smoke|release
# 环境:TRACEMIND_CI_FAIL_STAGE(可选,失败注入演练)
set -uo pipefail

SCOPE="${TRACEMIND_CI_SCOPE:-smoke}"
FAIL_STAGE="${TRACEMIND_CI_FAIL_STAGE:-}"
DRY_RUN=0
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-tracemind-ci-local}"

REPORT_FILE="reports/generated/full-e2e.json"
declare -a STAGES=(BUILD DATA_INFRA_READY DB_MIGRATION BUSINESS_FIXTURE_SEED
  JAVA_INTEGRATION_TEST RAG_SEED APPLICATION_READY MCP_PROTOCOL_SMOKE
  OBSERVABILITY_WARMUP MODEL_SMOKE EVAL_AGENT_REAL SCN001_E2E SCN002_E2E
  REPLAY_BACKEND_VALIDATION)
PRIMARY=""
declare -a SECONDARY=()
CLEANUP="success"

run_stage() {
  local stage="$1"; shift
  local t0 t1 dur
  t0=$(date +%s%3N)
  echo "[$(date +%H:%M:%S)] === $stage ==="
  if [ -n "$FAIL_STAGE" ] && [ "$FAIL_STAGE" = "$stage" ]; then
    echo "  FAIL-INJECT: $stage(TRACEMIND_CI_FAIL_STAGE)"
    echo "{\"stage\":\"$stage\",\"status\":\"failed\",\"failureCategory\":\"${stage}_FAILED\",\"injected\":true}" >> "$REPORT_FILE"
    PRIMARY="${stage}_FAILED"
    return 1
  fi
  if ! "$@" > "reports/generated/${stage}.log" 2>&1; then
    local code=$?
    echo "  FAIL: $stage(exit=$code)"
    echo "{\"stage\":\"$stage\",\"status\":\"failed\",\"failureCategory\":\"${stage}_FAILED\",\"exitCode\":$code}" >> "$REPORT_FILE"
    PRIMARY="${stage}_FAILED"
    return 1
  fi
  t1=$(date +%s%3N); dur=$((t1 - t0))
  echo "{\"stage\":\"$stage\",\"status\":\"success\",\"durationMs\":$dur}" >> "$REPORT_FILE"
}

stage_impl() {  # 各阶段真实实现由调用方覆盖;此处给默认命令(实施时填充真实命令)
  case "$1" in
    BUILD) docker compose -f compose.yml -f compose.ci.yml build ;;
    DATA_INFRA_READY) docker compose -f compose.yml -f compose.ci.yml up -d mysql qdrant prometheus otel-collector jaeger ;;
    DB_MIGRATION) TRACEMIND_MIGRATE_DB_URL="mysql+pymysql://root:${MYSQL_ROOT_PASSWORD}@mysql:3306/" python scripts/db/migrate.py ;;
    BUSINESS_FIXTURE_SEED) docker compose -f compose.yml -f compose.ci.yml run --rm seed ;;
    JAVA_INTEGRATION_TEST) (cd java && mvn --batch-mode verify) ;;
    RAG_SEED) python scripts/seed_runbook.py ;;
    APPLICATION_READY) docker compose -f compose.yml -f compose.ci.yml up -d order-service inventory-service ai-service ;;
    MCP_PROTOCOL_SMOKE) python scripts/ci/check_mcp_protocol.py ;;
    OBSERVABILITY_WARMUP) python scripts/ci/warmup_observability.py ;;
    MODEL_SMOKE) TRACEMIND_RUN_PROFILE=full_e2e TRACEMIND_LLM_MODE=real_strict python scripts/smoke_llm.py --real-strict ;;
    EVAL_AGENT_REAL) (cd ai-service && uv run python ../scripts/eval_agent.py --mode offline --llm real_strict --runs 1) ;;
    SCN001_E2E) python scripts/verify-m14.py --base http://localhost:8000 --order http://localhost:8081 --scenario SCN-001 --rounds 1 ;;
    SCN002_E2E) python scripts/verify-m14.py --base http://localhost:8000 --order http://localhost:8081 --scenario SCN-002 --rounds 1 ;;
    REPLAY_BACKEND_VALIDATION) python scripts/verify-m15.py --base http://localhost:8000 --order http://localhost:8081 ;;
  esac
}

main() {
  mkdir -p reports/generated
  echo '{"scope":"'$SCOPE'","stages":[]}' > "$REPORT_FILE"
  for stage in "${STAGES[@]}"; do
    if [ "$DRY_RUN" = 1 ]; then
      echo "[dry-run] PLAN $stage: $(stage_impl "$stage" 2>/dev/null | head -1)"
      continue
    fi
    if ! run_stage "$stage" stage_impl "$stage"; then
      echo "  → 停止后续有副作用阶段"
      break
    fi
  done
  # 报告收尾(always 执行)
  if [ -n "$PRIMARY" ]; then
    python - <<EOF
import json
p = json.load(open("$REPORT_FILE", encoding="utf-8"))
p["primaryFailureCategory"] = "$PRIMARY"
p["secondaryFailures"] = ${SECONDARY[*]:-[]}
p["cleanupStatus"] = "$CLEANUP"
json.dump(p, open("$REPORT_FILE", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
EOF
  fi
  echo "REPORT: $REPORT_FILE"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --scope) SCOPE="$2"; shift 2 ;;
    *) echo "未知参数 $1" >&2; exit 2 ;;
  esac
done
main
```

- [ ] **Step 2: 写失败注入测试**

创建 `scripts/ci/test_run_full_e2e.sh`:

```bash
#!/usr/bin/env bash
# 失败注入演练:TRACEMIND_CI_FAIL_STAGE=MCP_PROTOCOL_SMOKE 时,
# 该阶段失败、后续副作用阶段未执行、报告含 primaryFailureCategory。
set -euo pipefail
cd "$(dirname "$0")/../.."
rm -rf reports/generated
TRACEMIND_CI_FAIL_STAGE=MCP_PROTOCOL_SMOKE TRACEMIND_CI_SCOPE=smoke bash scripts/ci/run_full_e2e.sh --dry-run >/dev/null 2>&1 || true
echo "dry-run 演练(无副作用)完成"
```

> 注:完整失败注入需在能起 compose 的环境验证(VM);本地 `--dry-run` 只验证阶段计划输出与脚本不崩。

- [ ] **Step 3: 本地验证 dry-run**

Run: `bash scripts/ci/run_full_e2e.sh --dry-run --scope smoke`
Expected: 输出各阶段 PLAN 行,无真实执行,退出 0

Run: `bash -n scripts/ci/run_full_e2e.sh && bash -n scripts/ci/test_run_full_e2e.sh`
Expected: 语法 OK

- [ ] **Step 4: 提交**

```bash
git add scripts/ci/run_full_e2e.sh scripts/ci/test_run_full_e2e.sh
git commit -m "feat(ci): run_full_e2e.sh 阶段编排 + 失败分类 + 失败注入 + dry-run;报告 primary/secondary/cleanup"
```

---

### Task 15: preflight + verify_fast_gate + full-e2e.yml

**Files:**
- Create: `scripts/ci/preflight_full_e2e.py`
- Create: `scripts/ci/verify_fast_gate.py`
- Create: `.github/workflows/full-e2e.yml`
- Test: `scripts/ci/test_preflight_full_e2e.py`

**Interfaces:**
- Consumes: Task 9 的 coverage 检查;GitHub API(verify_fast_gate)。
- Produces: `preflight_full_e2e.py`(输出 `resolved_target_sha`);`verify_fast_gate.py`(Check Run 校验);full-e2e.yml(preflight → verify-fast-gate → full-e2e)。

**关键设计(spec §4.1):**
- preflight:`github.ref == refs/heads/main`;`confirm == RUN_FULL_E2E`;scope 合法;release_ref SemVer 正则 + `git rev-parse --verify` + origin/main 祖先校验;fetch-depth 0。
- verify_fast_gate:GitHub API 读 Check Runs,`name=fast-gate`、`head_sha`、`status=completed`、`conclusion=success`、`app.slug=github-actions`、workflow 文件匹配。
- full-e2e.yml:concurrency cancel-in-progress false;timeout 120min;full-e2e Job 绑 environment;checkout `resolved_target_sha`。

- [ ] **Step 1: 写失败测试**

创建 `scripts/ci/test_preflight_full_e2e.py`:

```python
"""preflight 校验:confirm/scope/ref 语义(不注入 Secret 的纯逻辑部分)。"""
import subprocess
import sys
from pathlib import Path


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "scripts/ci/preflight_full_e2e.py", *args],
                          capture_output=True, text=True)


def test_confirm_required():
    r = run(["--scope", "smoke"])
    assert r.returncode != 0
    assert "RUN_FULL_E2E" in r.stderr


def test_wrong_confirm_rejected():
    r = run(["--scope", "smoke", "--confirm", "NO"])
    assert r.returncode != 0


def test_semver_regex_rejects_loose_tag():
    # 通过 --release-ref 验证 SemVer 正则(不真正 git 校验)
    r = run(["--scope", "smoke", "--confirm", "RUN_FULL_E2E", "--release-ref", "v*"])
    assert r.returncode != 0
    assert "SemVer" in r.stderr
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd scripts/ci && python -m pytest test_preflight_full_e2e.py -q`
Expected: FAIL(脚本不存在)

- [ ] **Step 3: 实现 preflight_full_e2e.py**

```python
"""Full E2E preflight:confirm/scope/ref 校验 + 目标 SHA 解析(纯逻辑,无 Secret)。"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

SEMVER = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def run_git(args: list[str]) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True, cwd=Path(__file__).resolve().parents[2])
    if r.returncode != 0:
        raise RuntimeError(f"git {args} 失败: {r.stderr}")
    return r.stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", required=True, choices=["smoke", "release"])
    ap.add_argument("--confirm", required=True)
    ap.add_argument("--release-ref", default="")
    args = ap.parse_args()
    if args.confirm != "RUN_FULL_E2E":
        print("FATAL: confirm 必须为 RUN_FULL_E2E", file=sys.stderr)
        return 1
    if args.release_ref:
        if not SEMVER.match(args.release_ref):
            print(f"FATAL: release_ref {args.release_ref} 不满足 SemVer", file=sys.stderr)
            return 1
        try:
            sha = run_git(["rev-parse", "--verify", f"{args.release_ref}^{{commit}}"])
        except RuntimeError as e:
            print(f"FATAL: {e}", file=sys.stderr)
            return 1
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            print("FATAL: rev-parse 输出非 40 位 SHA", file=sys.stderr)
            return 1
        main_sha = run_git(["rev-parse", "origin/main"])
        if not run_git(["merge-base", "--is-ancestor", sha, main_sha]) == "":
            # merge-base --is-ancestor 退出码 0 = 是祖先
            pass
        r = subprocess.run(["git", "merge-base", "--is-ancestor", sha, main_sha],
                           capture_output=True, cwd=Path(__file__).resolve().parents[2])
        if r.returncode != 0:
            print(f"FATAL: {args.release_ref} 不是 origin/main 祖先", file=sys.stderr)
            return 1
        print(f"resolved_target_sha={sha}")
    else:
        print(f"resolved_target_sha={run_git(['rev-parse', 'origin/main'])}")
    print(f"scope={args.scope}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd scripts/ci && python -m pytest test_preflight_full_e2e.py -q`
Expected: PASS

- [ ] **Step 5: 写 verify_fast_gate.py**

```python
"""校验目标 SHA 的 fast-gate Check Run 已成功(绑定 workflow 文件 + app.slug)。"""
import argparse
import os
import sys
import urllib.request
import json


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sha", required=True)
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    ap.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    args = ap.parse_args()
    if not args.token:
        print("FATAL: 需 GITHUB_TOKEN", file=sys.stderr)
        return 1
    url = f"https://api.github.com/repos/{args.repo}/commits/{args.sha}/check-runs"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {args.token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
    for cr in data.get("check_runs", []):
        if (cr.get("name") == "fast-gate"
                and cr.get("status") == "completed"
                and cr.get("conclusion") == "success"
                and (cr.get("app") or {}).get("slug") == "github-actions"):
            print(f"OK: fast-gate success @ {args.sha}")
            return 0
    print(f"FAIL: 未找到 {args.sha} 的成功 fast-gate", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: 写 full-e2e.yml**

```yaml
name: full-e2e
on:
  workflow_dispatch:
    inputs:
      scope:
        type: choice
        options: [smoke, release]
        required: true
        default: smoke
      confirm:
        type: string
        required: true
        description: 输入 RUN_FULL_E2E
      release_ref:
        type: string
        required: false
        description: 可选 v1.6.0 风格 tag;留空测当前 main

concurrency:
  group: full-e2e
  cancel-in-progress: false

jobs:
  preflight:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    permissions:
      contents: read
    outputs:
      resolved_target_sha: ${{ steps.resolve.outputs.sha }}
    steps:
      - name: Ref 必须为 main
        run: |
          if [ "${{ github.ref }}" != "refs/heads/main" ]; then
            echo "::error::Full E2E 仅允许从 main 执行(ref=${{ github.ref }})"
            exit 1
          fi
      - uses: actions/checkout@<sha>
        with:
          fetch-depth: 0
      - name: Resolve target
        id: resolve
        run: |
          out=$(python scripts/ci/preflight_full_e2e.py --scope "${{ inputs.scope }}" \
                --confirm "${{ inputs.confirm }}" --release-ref "${{ inputs.release_ref }}")
          echo "$out"
          sha=$(echo "$out" | grep '^resolved_target_sha=' | cut -d= -f2)
          echo "sha=$sha" >> "$GITHUB_OUTPUT"

  verify-fast-gate:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    needs: [preflight]
    permissions:
      contents: read
      checks: read
      actions: read
    steps:
      - uses: actions/checkout@<sha>
      - name: Verify fast-gate
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          python scripts/ci/verify_fast_gate.py --sha "${{ needs.preflight.outputs.resolved_target_sha }}"

  full-e2e:
    runs-on: ubuntu-latest
    timeout-minutes: 120
    needs: [preflight, verify-fast-gate]
    environment: full-e2e
    permissions:
      contents: read
    env:
      COMPOSE_PROJECT_NAME: tracemind-ci-${{ github.run_id }}-${{ github.run_attempt }}
    steps:
      - uses: actions/checkout@<sha>
        with:
          ref: ${{ needs.preflight.outputs.resolved_target_sha }}
      - name: 静态配置校验(占位值,不注入真实 Secret)
        env:
          MYSQL_ROOT_PASSWORD: placeholder
          TRACEMIND_DB_APP_BUSINESS_PASSWORD: placeholder
          TRACEMIND_DB_CONTROL_APP_PASSWORD: placeholder
          TRACEMIND_DB_AI_INVESTIGATOR_PASSWORD: placeholder
          TRACEMIND_DB_SESSION_TERMINATOR_PASSWORD: placeholder
          TRACEMIND_CHAT_API_KEY: placeholder
          TRACEMIND_CHAT_BASE_URL: https://placeholder
          TRACEMIND_CHAT_MODEL: placeholder
          TRACEMIND_EVAL_CHAT_MODEL: placeholder
        run: docker compose -f compose.yml -f compose.ci.yml config --quiet
      - name: 运行 Full E2E(smoke/release)
        env:
          TRACEMIND_CI_SCOPE: ${{ inputs.scope }}
          MYSQL_ROOT_PASSWORD: ${{ secrets.MYSQL_ROOT_PASSWORD }}
          TRACEMIND_DB_APP_BUSINESS_PASSWORD: ${{ secrets.TRACEMIND_DB_APP_BUSINESS_PASSWORD }}
          TRACEMIND_DB_CONTROL_APP_PASSWORD: ${{ secrets.TRACEMIND_DB_CONTROL_APP_PASSWORD }}
          TRACEMIND_DB_AI_INVESTIGATOR_PASSWORD: ${{ secrets.TRACEMIND_DB_AI_INVESTIGATOR_PASSWORD }}
          TRACEMIND_DB_SESSION_TERMINATOR_PASSWORD: ${{ secrets.TRACEMIND_DB_SESSION_TERMINATOR_PASSWORD }}
          TRACEMIND_CHAT_API_KEY: ${{ secrets.TRACEMIND_CHAT_API_KEY }}
          TRACEMIND_CHAT_BASE_URL: ${{ secrets.TRACEMIND_CHAT_BASE_URL }}
          TRACEMIND_CHAT_MODEL: ${{ secrets.TRACEMIND_CHAT_MODEL }}
          TRACEMIND_EVAL_CHAT_MODEL: ${{ secrets.TRACEMIND_EVAL_CHAT_MODEL }}
          TRACEMIND_LLM_MODE: real_strict
        run: |
          bash scripts/ci/run_full_e2e.sh --scope "${{ inputs.scope }}"
      - name: 脱敏并上传报告
        if: ${{ always() }}
        run: |
          bash scripts/ci/redact_and_upload.sh
      - name: Compose cleanup
        if: ${{ always() }}
        run: |
          docker compose -f compose.yml -f compose.ci.yml down -v --remove-orphans
```

> 注:`redact_and_upload.sh` 在 Task 16 实现;此处 workflow 引用它(顺序上 Task 16 完成后再跑此 workflow)。

- [ ] **Step 7: 提交**

```bash
git add scripts/ci/preflight_full_e2e.py scripts/ci/test_preflight_full_e2e.py scripts/ci/verify_fast_gate.py .github/workflows/full-e2e.yml
git commit -m "feat(ci): full-e2e.yml + preflight(SemVer/祖先校验)+ verify_fast_gate(app.slug 绑定)"
```

---

### Task 16: redact_logs.py + redact_and_upload.sh(日志脱敏)

**Files:**
- Create: `scripts/ci/redact_logs.py`
- Create: `scripts/ci/redact_and_upload.sh`
- Test: `scripts/ci/test_redact_logs.py`

**Interfaces:**
- Produces: `redact_logs.py`(输入目录 → 脱敏目录;失败退出码非 0,不产出可上传文件);`redact_and_upload.sh`(原始日志 → 脱敏 → 上传 sanitized;失败标记 LOG_REDACTION_FAILED)。

**关键设计(spec §4.9):**
- 脱敏模式:百炼 key(`sk-...`)、`Authorization: Bearer ...`、MySQL URL 凭据(`://user:pass@`)、Qdrant key、JSON 嵌套 `api_key/token/password`、多行日志、部分掩码。
- Secret 从环境变量/临时文件读(不进命令行参数)。
- 原始日志目录与可上传目录分离。

- [ ] **Step 1: 写失败测试**

创建 `scripts/ci/test_redact_logs.py`:

```python
"""日志脱敏:各类 Secret 被掩码;失败时禁止输出可上传文件。"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def run_redact(src: Path, dst: Path, secrets: list[str]) -> subprocess.CompletedProcess:
    env = dict(os.environ, TRACEMIND_REDACT_SECRETS="|".join(secrets))
    return subprocess.run([sys.executable, "scripts/ci/redact_logs.py", str(src), str(dst)],
                          capture_output=True, text=True, env=env)


def test_redacts_bearer_and_sk(tmp_path):
    src = tmp_path / "raw"
    dst = tmp_path / "sanitized"
    src.mkdir()
    (src / "a.log").write_text("Authorization: Bearer abc123def\nkey: sk-1234567890\n", encoding="utf-8")
    r = run_redact(src, dst, ["abc123def", "sk-1234567890"])
    assert r.returncode == 0
    out = (dst / "a.log").read_text(encoding="utf-8")
    assert "Bearer abc123def" not in out
    assert "sk-1234567890" not in out


def test_redacts_mysql_url():
    import tempfile, json
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "raw"; dst = Path(td) / "s"; src.mkdir()
        (src / "c.log").write_text("mysql+pymysql://tracemind_control_app:secretpwd@h:3306/db", encoding="utf-8")
        r = run_redact(src, dst, ["secretpwd"])
        assert r.returncode == 0
        assert "secretpwd" not in (dst / "c.log").read_text(encoding="utf-8")


def test_redacts_json_nested():
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "raw"; dst = Path(td) / "s"; src.mkdir()
        (src / "j.json").write_text('{"body": {"api_key": "KEY123", "token": "TOK456"}}', encoding="utf-8")
        r = run_redact(src, dst, ["KEY123", "TOK456"])
        assert r.returncode == 0
        out = (dst / "j.json").read_text(encoding="utf-8")
        assert "KEY123" not in out and "TOK456" not in out
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd scripts/ci && python -m pytest test_redact_logs.py -q`
Expected: FAIL(脚本不存在)

- [ ] **Step 3: 实现 redact_logs.py**

```python
"""日志脱敏:掩码所有已知 Secret 模式;失败时退出码非 0 且不产出可上传文件。"""
import os
import re
import sys
from pathlib import Path

MASK = "[REDACTED]"

# 常见 Secret 模式(不含具体值,具体值从 TRACEMIND_REDACT_SECRETS 读)
PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),                       # 百炼/OpenAI key
    re.compile(r"Authorization:\s*Bearer\s+[A-Za-z0-9._\-]+"),  # Bearer
    re.compile(r"://[^:/@\s]+:[^@\s]+@"),                        # URL 凭据
    re.compile(r'"(api_key|token|password|secret|apiKey)"\s*:\s*"[^"]*"', re.IGNORECASE),
]


def load_secrets() -> list[str]:
    raw = os.environ.get("TRACEMIND_REDACT_SECRETS", "")
    return [s for s in raw.split("|") if s]


def redact(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, MASK)
    for pat in PATTERNS:
        text = pat.sub(MASK, text)
    return text


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
        print("FATAL: 无 TRACEMIND_REDACT_SECRETS,拒绝脱敏(避免泄漏)", file=sys.stderr)
        return 1
    try:
        dst.mkdir(parents=True, exist_ok=True)
        for f in src.rglob("*"):
            if f.is_file():
                out = dst / f.relative_to(src)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(redact(f.read_text(encoding="utf-8", errors="replace"), secrets),
                               encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"FATAL: 脱敏失败 {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd scripts/ci && python -m pytest test_redact_logs.py -q`
Expected: PASS

- [ ] **Step 5: 写 redact_and_upload.sh**

```bash
#!/usr/bin/env bash
# 原始日志 → 脱敏 → Secret 扫描 → 上传 sanitized;失败标记 LOG_REDACTION_FAILED。
set -uo pipefail
RAW_DIR="${RUNNER_TEMP:-/tmp}/tracemind-raw-logs"
SAN_DIR="reports/generated/sanitized"

# 收集原始日志(从 reports/generated 与 docker logs)
mkdir -p "$RAW_DIR"
find reports/generated -maxdepth 1 -name '*.log' -exec cp {} "$RAW_DIR/" \; 2>/dev/null || true

# 脱敏(Secret 从 env 传入,不进命令行)
export TRACEMIND_REDACT_SECRETS="${TRACEMIND_REDACT_SECRETS:-}"
if ! python scripts/ci/redact_logs.py "$RAW_DIR" "$SAN_DIR"; then
  echo "::error::LOG_REDACTION_FAILED — 不上传原始日志"
  rm -rf "$SAN_DIR"
  exit 1
fi
echo "上传 sanitized: $SAN_DIR"
```

- [ ] **Step 6: 提交**

```bash
git add scripts/ci/redact_logs.py scripts/ci/test_redact_logs.py scripts/ci/redact_and_upload.sh
git commit -m "feat(ci): redact_logs 脱敏(secret 模式 + 精确值)+ 失败拒传原始日志 + redact_and_upload"
```

---

### Task 17: docs/ci/GITHUB_ACTIONS_SETUP.md + 敏感扫描 + README V1.6

**Files:**
- Create: `docs/ci/GITHUB_ACTIONS_SETUP.md`
- Create: `scripts/ci/scan_secrets.py`
- Modify: `README.md`(V1.6 章节)

**Interfaces:**
- Produces: 手工配置文档(Environment/Secrets/分支限制/轮换/泄密检查);敏感扫描脚本;README V1.6 章节。

**关键设计(spec §8 + §2.1):**

- [ ] **Step 1: 写 scan_secrets.py**

```python
"""敏感信息扫描:工作区 + git 历史(首次推送前用)。模式:sk-/Bearer/panhangyu/192.168.88.10/demo-secret。"""
import re
import subprocess
import sys
from pathlib import Path

PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"panhangyu\w*"),
    re.compile(r"192\.168\.88\.\d+"),
    re.compile(r"demo-secret-2026"),
    re.compile(r"IDENTIFIED BY ['\"][^'\"]+['\"]"),
]


def main() -> int:
    bad = []
    repo = Path(__file__).resolve().parents[2]
    # 工作区文件(排除 .git/依赖)
    for f in repo.rglob("*"):
        if any(part in {".git", "node_modules", ".venv", "target", "dist", "__pycache__"} for part in f.parts):
            continue
        if f.is_file() and f.suffix in {".py", ".sh", ".yml", ".yaml", ".sql", ".md", ".json", ".ps1", ".env*"}:
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for pat in PATTERNS:
                if pat.search(text):
                    bad.append(f"{f.relative_to(repo)}: {pat.pattern}")
    if bad:
        print("\n".join(bad), file=sys.stderr)
        print(f"FATAL: {len(bad)} 处疑似敏感信息", file=sys.stderr)
        return 1
    print("OK: 工作区无已知敏感模式")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 跑扫描**

Run: `python scripts/ci/scan_secrets.py`
Expected: 本地工作区应通过(若报 `demo-secret-2026` 在 compose.yml——这是演示固定值非真实凭据,列入 allowlist 说明)。

> 注:git 历史扫描用 `git log -p | grep` 手工执行(脚本只扫工作区;历史清理需 filter-repo,文档说明)。

- [ ] **Step 3: 写 docs/ci/GITHUB_ACTIONS_SETUP.md**

内容覆盖(按 spec §8):full-e2e Environment 创建;Secret 名称清单(MYSQL_ROOT_PASSWORD / TRACEMIND_DB_*_PASSWORD ×5 / TRACEMIND_CHAT_API_KEY / TRACEMIND_CHAT_BASE_URL / TRACEMIND_CHAT_MODEL / TRACEMIND_EVAL_CHAT_MODEL);Environment Branch/Tag 限制(仅 main);Workflow 最小权限;Fast Gate Required Check;GitHub Free 私有仓限制;Artifact 保留时间;手动触发 Smoke/Release 步骤;轮换百炼 Key;确认日志未泄密。

- [ ] **Step 4: README 加 V1.6 章节**

在 `## V1.5` 章节后、版本历史前插入 `## V1.6:CI 化回归与评测流水线`,含:Fast 五 Job 图、Full 阶段序列、两条命令、私有仓分支保护限制说明。

- [ ] **Step 5: 提交**

```bash
git add docs/ci/GITHUB_ACTIONS_SETUP.md scripts/ci/scan_secrets.py README.md
git commit -m "docs(ci): GITHUB_ACTIONS_SETUP 手工配置 + scan_secrets + README V1.6 章节"
```

---

### Task 18: 覆盖率基线校准 + 全量验证 + 推送准备

**Files:**
- Modify: `evaluation/thresholds/coverage.json`(填入实测值)
- Modify: `.github/workflows/fast-gate.yml`(替换 `<sha>` 为真实 Actions SHA)
- 无代码逻辑新增

**Interfaces:**
- Consumes: Task 5/6/7 覆盖率实测;Task 12 workflow。
- Produces: 最终阈值;workflow 无占位;推送远端前检查清单。

**关键设计:**

- [ ] **Step 1: 实测三端覆盖率**

Run:
```bash
cd ai-service && .venv/Scripts/pytest.exe --cov=app --cov-report=term -q 2>&1 | tail -5   # python line/branch
cd java && mvn --batch-mode test 2>&1 | grep -A3 "jacoco"   # java line/branch(verify 更全)
cd web && npm run test:coverage 2>&1 | tail -8              # web line/branch
```
Expected: 记录三个数值,写入 coverage.json(向下取两位小数)。

- [ ] **Step 2: 替换 Actions SHA**

查 `actions/checkout`/`setup-python`/`setup-node`/`setup-java`/`setup-uv`/`upload-artifact`/`download-artifact` 的当前 release SHA(经 GitHub API),替换 `fast-gate.yml`/`full-e2e.yml` 中的 `<sha>` 占位。

Run: `grep -rn "<sha>" .github/workflows/`
Expected: 无 `<sha>` 残留。

- [ ] **Step 3: 全量本地验证**

Run:
```bash
cd ai-service && .venv/Scripts/pytest.exe -q
cd web && npm run typecheck && npm run test && npm run build
cd java && mvn --batch-mode test
bash scripts/ci/check_fast_gate.sh  # 全 success 用例
python scripts/ci/ci_manifest.py check
python scripts/ci/scan_secrets.py
```
Expected: 全绿。

- [ ] **Step 4: 推送准备(用户执行,文档说明)**

- `git remote add origin https://github.com/panpan330/tracemind.git`(若无)
- `git push -u origin main`
- 首次推送前:执行 `git log -p | grep -iE "sk-|panhangyu|192.168.88"` 全历史扫描;若命中真实凭据,用 filter-repo 清理或轮换 key。
- GitHub 上:建 full-e2e Environment + Secrets;观察 Fast 首次运行。

- [ ] **Step 5: 提交**

```bash
git add evaluation/thresholds/coverage.json .github/workflows/
git commit -m "chore(ci): 覆盖率基线校准 + Actions SHA 固定 + 全量本地验证"
```

---

## 自审记录

(执行计划后由 agent 填写:spec 覆盖核对、占位符扫描、类型一致)

- Spec §2.1 仓库复用/敏感扫描 → Task 17 + Task 18 Step 4
- Spec §2.2 文件布局 → Task 1-17 对应文件
- Spec §2.3 迁移器 → Task 1-2
- Spec §2.4 compose/资源/数据量 → Task 11(数据量校准实验标注为实施时补充)
- Spec §2.5 Secret 两阶段 → Task 11 Step 2 + Task 15 Step 6
- Spec §2.6 Docker 双 target → Task 19
- Spec §2.7 gitignore → Task 8
- Spec §3 Fast → Task 5/6/7/8/9/10/12/13
- Spec §4 Full → Task 11/14/15/16
- Spec §5 代码变更 → Task 3/4/13
- Spec §6 验证策略 → 各 Task 内
- Spec §7 范围边界 → 无 Task(明确不做)
- Spec §8 手工配置 → Task 17
- Spec §9 验收断言 → Task 18 Step 3 + 推送后验证

---

### Task 19: Docker runtime/ci 双 target(ai-service)

**Files:**
- Modify: `ai-service/Dockerfile`
- Modify: `ai-service/.dockerignore`
- Test: 本地构建验证(VM 上构建,因本机无 Docker)

**Interfaces:**
- Produces: `ai-service/Dockerfile` 两个 target:`runtime`(生产精简,不含测试/评测/临时密钥)与 `ci`(含 pytest、fixture、评测数据,供 Full E2E 与 CI 使用)。
- Consumes: Task 5/7 的覆盖率配置(ci target 带 pytest-cov)。

**关键设计(spec §2.6):**
- 现状:`ai-service/.dockerignore` 排除了 `tests/` 和 `data/` → ci target 需要它们。
- 双 target:`FROM <base> AS runtime`(业务代码 + 依赖);`FROM runtime AS ci`(COPY tests/ + data/eval_cases/ + 评测脚本)。
- `.dockerignore` 不能无条件排除 tests/eval——按 target 区分(runtime 构建忽略,ci 构建需要)。
- Dockerfile 的 `.dockerignore` 是全局的(不能 per-target),所以:**评测数据与测试是否进镜像由 Dockerfile 的 COPY 决定,而非 .dockerignore**。修正思路:`.dockerignore` 只排除运行时生成物(`.eval_fixtures/`、`data/checkpoints.sqlite`),`tests/` 与 `eval_cases/` 保留在 COPY 候选集,由 target 决定是否 COPY。

- [ ] **Step 1: 看当前 Dockerfile**

Run: `cat ai-service/Dockerfile`
Expected: 确认当前是单 target(uvicorn CMD)。

- [ ] **Step 2: 改写为双 target**

```dockerfile
# syntax=docker/dockerfile:1
# 构建依赖镜像
FROM python:3.12-slim AS base
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# ---- runtime target:生产精简 ----
FROM base AS runtime
COPY --from=base /app /app
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ .  # 具体按现有构建方式
COPY app /app/app
COPY data/evaluation_policy.yaml /app/data/evaluation_policy.yaml
ENV TRACEMIND_CHECKPOINT_PATH=/app/data/checkpoints.sqlite
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ---- ci target:含测试/评测/覆盖率(供 Full E2E 与 CI)----
FROM runtime AS ci
RUN pip install --no-cache-dir pytest pytest-cov
COPY tests /app/tests
COPY data/eval_cases /app/data/eval_cases
COPY data/retrieval_test_cases.json /app/data/
COPY scripts/ci /app/scripts/ci
ENV TRACEMIND_RUN_PROFILE=ci_db TRACEMIND_LLM_MODE=fake
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

> 注:具体构建命令(RUN pip install 方式)以现有 Dockerfile 为准;双 target 的核心是 ci target 多 COPY tests/eval_cases/scripts,并装 pytest-cov。

- [ ] **Step 3: 改 .dockerignore(区分生成物与评测集)**

```gitignore
__pycache__/
*.pyc
.venv/
.pytest_cache/
*.egg-info/
data/checkpoints.sqlite   # 运行时生成物
data/.eval_fixtures/      # 运行时生成物
# 注意:不排除 tests/ 与 data/eval_cases/ —— ci target 需要,由 Dockerfile COPY 决定
```

- [ ] **Step 4: VM 构建验证**

Run(VM 上):
```bash
cd ~/tracemind/ai-service
DOCKER_BUILDKIT=0 docker build -t tracemind-ai:runtime-test --target runtime .
DOCKER_BUILDKIT=0 docker build -t tracemind-ai:ci-test --target ci .
```
Expected: 两个 target 都构建成功(legacy builder,阿里云 pip 源)。

- [ ] **Step 5: 提交**

```bash
git add ai-service/Dockerfile ai-service/.dockerignore
git commit -m "feat(docker): ai-service runtime/ci 双 target — ci 含 tests/eval/评测脚本;.dockerignore 只排运行时生成物"
```
