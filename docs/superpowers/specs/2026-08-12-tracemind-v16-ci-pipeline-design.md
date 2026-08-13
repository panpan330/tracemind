# V1.6 设计:CI 化回归与评测流水线

> 版本:2026-08-12 · 状态:设计定稿(经 4 段评审修正)
> 前置:V1.0–V1.5 全部交付(187 提交);仓库无 git remote,历史 spec 预留"CI 化回归流水线"为范围外项。

## 1. 背景与目标

### 1.1 目标

把回归评测流水线(`scripts/run_regression.py` fast/full 两档)升级为 GitHub Actions CI:

- **Fast 持续门禁**:每次 PR / main push 跑四个离线可复现的并行 Job(后端 pytest / Java 单测 / 前端测试构建 / 离线评测),聚合为 `fast-gate` 单一 Required Check。
- **Full 手动发布验收**:`workflow_dispatch` 手动触发,在全新 Runner 环境用 `compose.ci.yml` 从零构建启动全栈,调用真实模型执行发布验收,生成可下载报告。

工程叙事:**普通提交用完全离线、可复现的测试保障开发效率;发布前通过真实模型与真实基础设施完成全栈验收。**

### 1.2 名词界定

- **"纯离线"准确表述**:评测过程**不调用**真实 LLM/Embedding、Qdrant、Prometheus、Jaeger 等运行时外部服务,**不消耗模型额度**;依赖安装(`uv sync`/`npm ci`/Maven)仍访问受信任软件仓库。不断言"整个 Workflow 断网"。完全断网需额外依赖镜像/缓存,不属于 V1.6。
- **MySQL Service 属于可控测试基础设施**:临时 Runner 内可重复创建,不是外部运行时服务。

## 2. 仓库准备与整体结构

### 2.1 仓库状态

- **复用已有仓库**:`https://github.com/panpan330/tracemind.git`。本地无 remote → `git remote add origin <该地址>` 重新绑定;不新建、不覆盖远端。绑定前对比本地与远端提交历史,确认推送策略。
- **首次推送前敏感信息扫描**(`.gitignore` 拦不住 git 历史):
  - 工作区当前文件:`sk-` / `Bearer` / `panhangyu` / `192.168.88.10` / `demo-secret` / 百炼 key 等模式。
  - **git 历史**:`git log -p` 全历史 grep `.env` / key / 密码;若历史已含真实凭据 → `git filter-repo` 清理或换新 key。
  - `reports/` 与评测输出(现状已查无 `sk-` / VM 地址,仍列入扫描清单)。
- **私有仓分支保护限制(文档写清,不承诺)**:GitHub Free 私有仓 Actions 可用,但 Branch Protection / Rulesets 通常需 Pro(Free 主要面向公开仓)。开发期私有无 Pro → fast-gate 会执行但可能无法强制阻断合并;求职前转公开或升级 Pro 后启用 Required Check。

### 2.2 文件布局(定稿)

```
.github/workflows/fast-gate.yml          # 持续门禁
.github/workflows/full-e2e.yml           # 手动发布验收
.gitattributes                           # *.sh/*.yml/*.yaml/*.sql eol=lf
compose.yml                              # 唯一基底(统一文件名,全仓库一致)
compose.ci.yml                           # CI 覆盖(资源限制 + qdrant 定义 + 变量引用零凭据)

scripts/db/migrate.py                    # 唯一正式迁移器(环境无关:本地/Compose/CI 共用)
scripts/db/migrations/*.sql              # 唯一正式迁移文件(001_xxx.sql 数字版本)

scripts/ci/init_ci_db.sh                 # CI 编排:等 MySQL → migrate → fixture → 四账号探针
scripts/ci/check_fast_gate.sh            # 汇聚校验:读 4 个 env 结果
scripts/ci/preflight_full_e2e.py         # Full preflight:scope/confirm/ref/目标 SHA 解析
scripts/ci/verify_fast_gate.py           # Check Run 校验(main 与 tag)
scripts/ci/check_mcp_protocol.py         # MCP 协议探针
scripts/ci/warmup_observability.py       # Prometheus/Jaeger 预热
scripts/ci/run_full_e2e.sh               # Full 编排:阶段 + failureCategory + 部分报告 + 失败注入
scripts/ci/redact_logs.py                # 日志脱敏 + Secret 扫描(安全关键,须单测)
scripts/ci/ci_manifest.py                # generate/check 双模式(Canonical JSON)
scripts/ci/validate_replay_backend.py    # Replay Backend 验收包装(复用 verify-m15 逻辑)

evaluation/contracts/mcp-tool-contract.json
evaluation/contracts/diagnostic-policy-manifest.json
evaluation/contracts/replay-schema-manifest.json
evaluation/thresholds/coverage.json
evaluation/cases/case-manifest.json
.env.ci.example
docs/ci/GITHUB_ACTIONS_SETUP.md
```

原则:Workflow 只负责触发、权限、Job 依赖与 Artifact;复杂业务逻辑放 `scripts/ci/*`,**不内联进 YAML**。

### 2.3 数据库迁移(正式迁移入口)

**现状问题**:`scripts/sql/01~06` 手写文件,`init-database.ps1` 只执行 01~04,05/06 靠手工补;无版本表/checksum/幂等。

**V1.6 方案**(简历亮点,也是消除 Schema 漂移的工程能力):

- `scripts/db/migrate.py`:唯一迁移器,本地 / Compose / CI 共用同一套迁移文件。
- 迁移文件 `scripts/db/migrations/001_*.sql`(数字版本,按数字排序,非字符串排序);已执行文件禁止修改,新迁移只追加。
- `schema_migrations(migration_id, filename, checksum_sha256, status: started/applied/failed, started_at, applied_at, execution_ms, error_code)`。
- 幂等:已 applied 跳过;checksum 变更 → 失败;**Dirty Migration**(started/failed 残留)→ 拒绝自动继续,需 Repair。
- **MySQL Advisory Lock**(`GET_LOCK`)防两个 Job 并发迁移。
- checksum 基于规范化稳定字节;`.gitattributes` 含 `*.sql text eol=lf`。
- **不使用 `split(";")` 解析 SQL**:V1.6 迁移文件只允许受支持 SQL 子集并校验(注释 / DELIMITER 由执行方式处理,受支持子集在迁移器文档中列明)。
- 覆盖 02-users(四账号授权)、03/04 业务/控制表、05/06 迁移——全部由同一入口管理。
- `scripts/init-database.ps1` 改造为调用 `migrate.py`(Windows 包装);Compose Seed 服务同样调用正式迁移器。

### 2.4 Compose 与资源预算

- **基底文件名统一**:`compose.yml` + `compose.ci.yml`(Workflow / README / 本地 / VM / Full 编排 / 清理命令全部一致)。
- **显式服务清单**(不靠 profile 过滤,杜绝 Web/Grafana 意外启动):
  `docker compose -f compose.yml -f compose.ci.yml up -d --build mysql qdrant prometheus otel-collector jaeger order-service inventory-service ai-service`
- **compose.ci.yml 补 qdrant 服务定义**(基础 compose 无 qdrant;`TRACEMIND_QDRANT_URL: "http://qdrant:6333"` 已有引用)。
- **loadgen 不常驻**:按场景 `docker compose ... run --rm loadgen` 受控运行(SCN-001 准备→跑→停;SCN-002 注入→跑→停),避免污染基线/恢复指标。
- **资源总预算**(私有 runner 8GB):长期容器 mem_limit 总和 ≤5.5–6GB,留 ≥2GB 给 Runner OS + Docker daemon + 构建 + 验收脚本;Java `-Xmx` 明显小于容器 mem_limit;MySQL 保留 performance_schema(两个诊断场景依赖);Prometheus 缩短保留期;Jaeger 限内存 trace 数;构建后采集 `docker stats` + `docker system df`。
- **数据量基线**:`TRACEMIND_CI_INVENTORY_ROWS` 为经校准固定值(非模糊区间)。校准约束:初始化时间可接受 / 磁盘不超预算 / 缺索引稳定产生扫描延迟证据 / 建索引后执行计划与延迟确实改善 / 连续 3 次无缓存失真。数值由校准实验确定并写入报告(V1.6 实施 Task)。

### 2.5 Secret 两阶段

- **阶段一(静态)**:注入假占位值后 `docker compose config` → 校验语法 / 依赖 / 卷网络 / 变量完整 / **无硬编码真实凭据**。
- **阶段二(真实)**:百炼 key 仅运行时注入(`TRACEMIND_CHAT_API_KEY: ${TRACEMIND_CHAT_API_KEY:?required}` 变量引用),之后**不再输出/展开 config**;禁 `set -x`;不上传 `.env`;`docker inspect` 完整结果不进 Artifact;日志收集前脱敏。
- compose.ci.yml **零真实凭据,只允许环境变量引用**。

### 2.6 Docker 双 Target 与 .dockerignore

- **ai-service/Dockerfile 增加 `runtime` / `ci` 双 target**:runtime 不含测试/评测/临时密钥;ci target 含 pytest / fixture / 评测数据;Full E2E 构建 `--target ci`。
- **Java(order/inventory)Dockerfile**:与多阶段构建方式一致(内部 Maven 构建可排除 `target/`;若用本地预构建 JAR 则不能排除)——`.dockerignore` 必须与实际 Dockerfile 构建方式一致。
- 标准评测集(`data/eval_cases/`、`retrieval_test_cases.json`、`evaluation_policy.yaml`)必须进镜像;运行时生成物(`.eval_fixtures/`)排除。

### 2.7 .gitignore 修正

```
# 本地/真实凭据(忽略)
.env
.env.*
!.env.example
!.env.ci.example
# 运行时产物(忽略)
reports/generated/
ai-service/.eval_fixtures/
# 版本化基线(提交)
evaluation/baselines/
evaluation/thresholds/
```

## 3. fast-gate.yml(持续门禁)

### 3.1 触发与安全基线

```yaml
on:
  pull_request:
  push:
    branches: [main]
  workflow_dispatch:
concurrency:
  group: fast-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true   # Fast 可取消旧执行
```

- 所有 Job:`permissions: { contents: read }`(最小权限);合理 `timeout-minutes`(15–30)。
- 第三方 Actions **固定完整 Commit SHA**(不用浮动 tag)。

### 3.2 四 Job 并行 + 汇聚门禁

```
python-tests(MySQL service) ────────┐
java-tests(MySQL service) ──────────┤
web-tests(无 DB) ───────────────────┼── fast-gate(汇聚,if: always())
offline-evaluation(无 DB) ──────────┘
```

#### 3.2.1 python-tests(MySQL service)

- MySQL service 显式配置:

```yaml
services:
  mysql:
    image: mysql:8.0        # 与交付 compose 同一具体版本,不用浮动 tag
    env:
      MYSQL_ROOT_PASSWORD: <ci-only 固定测试密码,不复用任何环境密码>
      MYSQL_DATABASE: tracemind_control   # 迁移统一管理,此字段可选
      MYSQL_CHARSET: utf8mb4
      MYSQL_COLLATION: utf8mb4_unicode_ci
      TZ: UTC
    ports: ["3306:3306"]
    options: >-
      --health-cmd="mysqladmin ping" --health-interval=5s
      --health-timeout=5s --health-retries=20
```

- **全部连接池显式配置(fail-closed,零回退)**:

| 连接池 | 账号 | CI 显式值 |
|---|---|---|
| `TRACEMIND_CONTROL_DB_URL` | tracemind_control_app | `...@127.0.0.1:3306/tracemind_control` |
| `TRACEMIND_READONLY_DB_URL` | ai_investigator | `...@127.0.0.1:3306/tracemind_business` |
| `TRACEMIND_SESSION_TERMINATOR_DB_URL` | session_terminator | `...@127.0.0.1:3306/`(显式提供,不再回退 readonly) |
| `get_executor_engine()`(派生) | fix_executor | 由 control URL 派生,无需独立变量 |

- **Run Profile**(见 §5):`TRACEMIND_RUN_PROFILE=ci_db`,全部 URL 必填、禁止默认 localhost。
- 初始化 `scripts/ci/init_ci_db.sh`:等 MySQL → `scripts/db/migrate.py` → 最小确定性 fixture → **四账号权限探针**(control 读写 / app_business 仅业务范围 / ai_investigator 只读 / fix_executor 仅 INDEX;调查与业务账号执行越权 DDL/KILL 必须失败)。
- 命令:`uv sync --frozen && uv run pytest tests/ -q`;覆盖率 pytest-cov → Cobertura/XML。
- 不注入任何百炼 / Qdrant / Prometheus / Jaeger 凭据。

#### 3.2.2 java-tests(MySQL service)

- **运行时依赖声明**:该 Job 除 JDK/Maven 外需 Python + uv(执行 `migrate.py`)——用 `setup-python` + `astral-sh/setup-uv`;**不复制第二套 SQL**。
- 数据源用 Spring 实际格式:`jdbc:mysql://127.0.0.1:3306/tracemind_business` + `BUSINESS_DB_USER/PASSWORD`;order / inventory 各自配置。
- 命令:`mvn --batch-mode test`。
- **测试分类由 Maven 插件配置固定**(父 POM pluginManagement,见 §5.4):Fast 的 `mvn test` 由 Surefire 执行单测(默认匹配 `*Test`/`Test*`/`*Tests`/`*TestCase` 等);Full 的 `mvn verify` 由 Failsafe 执行 `*IT`/`IT*`/`*ITCase` 集成测试。**`mvn test` 绝不 `-DskipTests`**。
- 现状:`failsafe` 仅 inventory-service 配置 → 统一上移父 POM;InventoryMySQLIT 目前从未被任何流水线执行 → **Full 必须跑 `mvn verify`**。
- 覆盖率:JaCoCo XML;common 纳入单测与聚合(库模块,不需 DB service)。

#### 3.2.3 web-tests(无 DB)

- **scripts 拆分(避免重复类型检查)**:
  ```json
  { "typecheck": "vue-tsc -b", "test": "vitest run", "build": "vite build" }
  ```
  (`vue-tsc -b` 适配 Project References;build 不再内嵌 vue-tsc,由 typecheck 单独负责)
- CI:`npm ci && npm run typecheck && npm run test && npm run build`。
- `package-lock.json` 已存在 ✓;固定 Node 主版本;vitest 用 `run` 模式;失败仍上传 JUnit 报告。
- 覆盖率:Vitest Coverage(`@vitest/coverage-v8`),覆盖所有业务源文件(非仅 import 的文件)。

#### 3.2.4 offline-evaluation(无 DB)

- **Run Profile `offline_eval`**:数据库访问禁用,代码意外访问 → `DATABASE_ACCESS_DISABLED`;LLM=fake。
- 已核实:`eval_agent.py` 用 `InMemorySaver()` + fixture 文件,不落控制库、结果只写文件 → 无需 MySQL。
- **评测数量动态化**:版本化 Case Manifest 声明 `expectedCases`;执行时动态发现 N 条并断言 `discovered == expected && executed == discovered`;新增 SCN-003 不需改 workflow。
- **契约校验对比"已提交基线"**(`evaluation/contracts/`):
  - `mcp-tool-contract.json`:当前 schema 重算 Hash ≠ 已提交 Hash → 失败;Version 升但 Hash 没变 → 警告/失败;工具数量以 Manifest 为准。
  - `diagnostic-policy-manifest.json`:policy 文件变化但 `POLICY_BUNDLE_VERSION` 未变 → 失败。
  - `replay-schema-manifest.json`:Python 后端常量 / API Response Schema / Vue TS 类型 / 已提交 Manifest 四元一致。
- **MCP 错误限定**:MCP 基础设施与协议错误 = 0(`MCP_START_FAILED`/`TIMEOUT`/`DISCONNECTED`/`PROTOCOL_ERROR`/`SCHEMA_MISMATCH`/意外 `direct_fallback`);业务 fixture 主动返回的工具错误/预期非法参数/安全拒绝属评测用例正常结果。保留 `transport=mcp_stdio` + `direct_fallback=false` 断言。
- **失败也生成报告**:编排器跑完完整 case 清单(不中途退出)→ JSON+Markdown → 阈值判定退出码;上传 `if: always()`。

### 3.3 fast-gate 汇聚(修正后)

```yaml
fast-gate:
  needs: [python-tests, java-tests, web-tests, offline-evaluation]
  if: ${{ always() }}
  runs-on: ubuntu-latest
  env:
    PYTHON_RESULT: ${{ needs.python-tests.result }}
    JAVA_RESULT: ${{ needs.java-tests.result }}
    WEB_RESULT: ${{ needs.web-tests.result }}
    EVALUATION_RESULT: ${{ needs.offline-evaluation.result }}
  steps:
    - uses: actions/checkout@<sha>
    - run: bash scripts/ci/check_fast_gate.sh   # 只读 4 个 env,任一非 success(含 failure/cancelled/skipped)→ exit 1
```

### 3.4 报告与 Artifact(统一)

- Artifact 名:`fast-gate-${github.sha}-${github.run_id}-${github.run_attempt}`。
- 报告字段:Git SHA / Run+Attempt / Case Manifest Version / Prompt+Policy+MCP+Replay 版本 / FakeLLM 版本 / 阈值 / 通过-失败数 / 失败用例 / 基础设施错误 vs 业务断言错误分类。
- 上传 `if: always()`。

### 3.5 覆盖率(第一版"基线不下降")

- Python pytest-cov / Java JaCoCo / Vue Vitest Coverage 分别记录当前基线为最低阈值,存 `evaluation/thresholds/coverage.json`。
- **防下调**:check 时读取目标分支原阈值,新阈值 < 目标分支 → 失败;允许持平/提高;下调必须显式 Override + 记录原因。
- 三端指标:`{ line, branch }`;第一版基线 = 实测值向下取整/两位小数。
- 覆盖范围:Python `ai-service/app`(不含测试代码);Java 三模块聚合;Vue 所有业务源文件。

## 4. full-e2e.yml(手动发布验收)

### 4.1 Job 链与 Secret 边界

```
preflight(无 Secret,不绑 Environment)
   ↓
verify-fast-gate(只读 API,不绑 Environment)
   ↓
full-e2e(此时才绑 Environment,读取百炼 Key)
```

#### 4.1.1 preflight(无 Environment/Secret)

```yaml
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
```

- 校验:① `confirm == RUN_FULL_E2E` ② scope 合法 ③ 执行 ref 逻辑。
- **Workflow 永远从 main 执行**(不从 tag 跑,消除"tag 内 workflow 文件不可信"问题):默认测当前 main;`release_ref` 填 tag 时由 main 上的可信 workflow 解析 → 校验是 `origin/main` 祖先 + **SemVer**(拒绝宽泛 `v*`)→ `resolved_target_sha`。Environment Deployment Rules 只允许 main + 受保护版本 tag。

#### 4.1.2 verify-fast-gate(main 与 tag 都校验)

- GitHub API 读取 Check Runs,断言:`name=fast-gate`、`head_sha=resolved_target_sha`、`status=completed`、`conclusion=success`、`app=GitHub Actions`。
- 最小权限:`contents: read, checks: read, actions: read`。
- 该 SHA 无 fast-gate 记录 → 失败,要求先跑 Fast;不重跑四个 Fast Job。
- 备选:`fast-gate.yml` 提供 `workflow_call`(个人项目选 API 读取,避免重复)。

### 4.2 启动顺序(两阶段,消除"应用等迁移"死锁)

```
BUILD                       构建镜像
DATA_INFRA_READY            mysql/qdrant/prometheus/otel-collector/jaeger 各自 healthy
DB_MIGRATION                scripts/db/migrate.py(正式迁移入口)
BUSINESS_FIXTURE_SEED       seed 一次性服务
RAG_SEED                    runbook 入 qdrant
APPLICATION_READY           order/inventory/ai 启动 + healthy(schema 已就绪)
MCP_PROTOCOL_SMOKE          见 §4.3
OBSERVABILITY_WARMUP        见 §4.4
MODEL_SMOKE                 real_strict 单次冒烟(key/额度/模型可用)
EVAL_AGENT_REAL             eval_agent --llm real_strict(按 Manifest)
SCN001_E2E / SCN002_E2E     verify 脚本(真实观测后端)
REPLAY_BACKEND_VALIDATION   见 §4.6
REPORT → LOG_REDACTION → ARTIFACT_UPLOAD → COMPOSE_CLEANUP
```

### 4.3 MCP 协议探针(独立阶段,非容器 health check)

- spawn MCP stdio 子进程 → initialize → `tools/list` → 校验 `MCP_TOOL_CONTRACT_VERSION` + schema Hash → 调一个安全只读探针工具 → 关闭会话。
- 失败分类 `MCP_PROTOCOL_FAILED`,不合并进普通 healthy 语义。

### 4.4 OBSERVABILITY_WARMUP

- Prometheus:targets 全 UP、HTTP Histogram 已存在、≥2 个有效 scrape 样本。
- 生成一条预热请求 → Jaeger 能查到对应跨服务 Trace → TraceNormalizer 正常处理。
- 断言不用旧 `/internal/observations`。
- 预热通过才进故障场景。

### 4.5 smoke / release 真实评测范围(Manifest 定义)

- 版本化 Case Manifest 每 case:`{ id, offline, real_smoke, real_release, repetitions }`。
- **smoke**:模型冒烟×1 + 精选正例/负例各 1 条(各 1 次)+ SCN-001×1 + SCN-002×1 + Replay Backend×1。
- **release**:Manifest 中所有 `real_release=true` 用例按 `repetitions` + SCN-001×3 + SCN-002×3 + Replay Backend×1。
- 报告记录 `eligible/discovered/executed/passed`;**禁止 CLI 临时决定评测范围**。

### 4.6 Replay 验收更名

- Full 不启动 Web → **`REPLAY_BACKEND_VALIDATION`**:证明快照完整 / Projector 正确 / 只读 API 无副作用 / Run-Step-Hash 稳定 / SCN-001/002 均生成正确回放。
- 前端回放交互由 Fast 的 Vitest 覆盖;Playwright 浏览器 E2E 留后续(届时才在 Full 启动 Web)。

### 4.7 失败分类(统一实现)

```
BUILD_FAILED / DATA_INFRASTRUCTURE_FAILED / DATABASE_MIGRATION_FAILED /
BUSINESS_SEED_FAILED / RAG_SEED_FAILED / APPLICATION_START_FAILED /
MCP_PROTOCOL_FAILED / OBSERVABILITY_WARMUP_FAILED / MODEL_PROVIDER_FAILED /
EVALUATION_THRESHOLD_FAILED / SCN001_E2E_FAILED / SCN002_E2E_FAILED /
REPLAY_VALIDATION_FAILED / REPORT_GENERATION_FAILED / RUNNER_RESOURCE_EXHAUSTED
```

- 由 `run_full_e2e.sh` 统一实现(非靠 step 名):每阶段 `{ stage, status, failureCategory, startedAt, finishedAt, durationMs, detailsFile }`。
- 业务阶段失败 → 立即停止后续有副作用阶段(报告/清理仍执行);不 all continue-on-error。

### 4.8 超时与清理缓冲区

- Job 总超时 120min;主验收脚本内部超时 105min(留 15min 给报告/日志/清理);每阶段子超时。
- `COMPOSE_PROJECT_NAME=tracemind-ci-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}`(唯一 project → `down -v --remove-orphans` 只清本次)。
- 运行中监控 `docker system df`;**不执行全局 `docker system prune`**(临时 runner 无需,且可能破坏待收集镜像信息)。
- 顺序:采集资源/日志 → 脱敏 → 上传 → 仅清理当前 project。

### 4.9 日志脱敏

- 原始日志 → 本地临时目录 → 脱敏 + Secret 扫描 → 上传**脱敏副本**。
- 脱敏/扫描失败 → **不上传原始日志**,标记 `LOG_REDACTION_FAILED`,只上传无业务输出的阶段状态文件。
- 原始日志 `$RUNNER_TEMP/tracemind-raw-logs`;可上传 `reports/generated/sanitized`;Artifact 只引用 sanitized,禁止过宽路径(`reports/**`、`**/*.log`)。
- Secret 经环境或临时文件传给扫描器,**不作为命令行参数**出现在进程信息中。
- Artifact 设置保留天数 + 大小上限。

## 5. 代码行为变更(设计层)

### 5.1 config.py:Run Profile(fail-closed)

| Profile | 数据库 | LLM | 默认地址 |
|---|---|---|---|
| `local` | 可默认 localhost | fake/real | 允许 |
| `ci_db` | 全部 URL 必填 | fake | 禁止默认 |
| `offline_eval` | **禁用**(访问→`DATABASE_ACCESS_DISABLED`) | fake | 禁止创建 engine |
| `full_e2e` | 全部 URL 必填 | real_strict | 禁止默认 |
| `production` | 全部 URL 必填 | real | 禁止默认 |

关键:LLM 模式与数据库模式是**两个独立维度**;`offline_eval` 下代码意外访问数据库抛 `DATABASE_ACCESS_DISABLED`,不偷偷连 localhost。

### 5.2 迁移器(见 §2.3)

### 5.3 ci_manifest.py:generate / check 分离

- `generate`(开发者显式更新)/ `check`(CI 只校验,禁止修改文件)。
- check:计算 → 与已提交对比 → 检查 version 同步升级 → 检查工作树未被修改 → 不自动覆盖。
- Canonical JSON 稳定排序,避免字段顺序产生无意义 diff。

### 5.4 Java 父 POM

- `java/pom.xml` pluginManagement:Surefire / Failsafe / JaCoCo;子模块只继承 + 声明差异。
- `mvn test` 只 Surefire;`mvn verify` Surefire + Failsafe;JaCoCo 同时覆盖单测与集成测试;多模块聚合报告。

### 5.5 eval_agent 增强

- 报告始终生成(即使失败);输出 `caseManifestVersion`;MCP 错误限定协议/基础设施类;编排器跑完完整 case 清单。

## 6. 验证策略

| 层 | 方法 |
|---|---|
| 业务层 | migrate 幂等/篡改失败;config profile 单测;前端拆分;Java 分类;覆盖率基线 |
| **CI 自身测试(Fast 新增 job)** | actionlint(workflow 语法)、shellcheck(sh 脚本)、migrate.py 单测、ci_manifest generate/check 一致性、redact_logs 脱敏与拒绝上传、run_full_e2e 阶段失败注入、check_fast_gate 四种非成功状态、compose 合并静态校验 |
| 工具固定 | 第三方 Actions 固定 Commit SHA;actionlint/shellcheck 版本固定 |
| 推送验证 | 推私有仓跑真实 Fast;Free 限制文档注明 |
| Full | VM **Smoke Rehearsal**(真实最小调用)+ 失败注入演练;之后推远端跑 smoke/release |

**dry-run 与 Smoke 区分**:`run_full_e2e.sh --dry-run` 只输出阶段计划/配置校验/命令,不执行模型调用与副作用;`--scope smoke` 执行真实最小冒烟;VM 上实际验证叫 **Smoke Rehearsal**,不叫 dry-run。

**失败注入**:`TRACEMIND_CI_FAIL_STAGE=MCP_PROTOCOL_SMOKE` 在 fake 环境验证——指定阶段失败、后续副作用阶段未执行、failureCategory 正确、部分报告生成、清理流程被调用。

## 7. 范围边界(YAGNI)

- 不做依赖镜像 / 完全断网缓存(§1.2)。
- 不做 Playwright 浏览器 E2E(留后续,Full 暂不启动 Web)。
- 不做自托管 Runner(资源不足时兜底顺序:关容器 → 调资源 → 拆 Job → 转公开仓,而非上自托管)。
- 覆盖率第一版只设"基线不下降",不拍高阈值。

## 8. 手工配置说明(docs/ci/GITHUB_ACTIONS_SETUP.md 摘要)

- full-e2e Environment 创建方式;需配置的 Secret 名称;Environment Branch/Tag 限制;Workflow 最小权限;Fast Gate Required Check;GitHub Free 私有仓限制;Artifact 保留时间;如何手动触发 Smoke/Release;如何轮换百炼 Key;如何确认日志没有泄密。

## 9. 验收

- **Fast**:本地按 CI env 手工跑四步(CI MySQL + 迁移 + 权限探针)全绿;actionlint/shellcheck/CI 自身测试绿;推远端真实 Fast 绿。
- **Full**:VM Smoke Rehearsal + 失败注入演练通过;远端 smoke/release 各跑一次,报告含 `eligible/discovered/executed/passed` 与全部阶段状态。
