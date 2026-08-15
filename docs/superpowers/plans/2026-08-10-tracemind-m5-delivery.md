# M5 实施计划:最终工程交付(Testcontainers / Dockerfile / Compose / E2E / README)

对应设计文档 §13(M5)+ §12(部署)。前置:M1~M4 全部完成并合并到 `main`。

## 目标

- Dockerfile 覆盖四个组件(Java 双服务多阶段构建、AI 服务、前端 node→nginx)。
- `docker-compose.yml` 一键拉起 MySQL 8 + order-service + inventory-service + ai-service + web,依赖顺序与健康检查完备。
- 在 VM(<vm-host>,Docker 29.1.4)上真实 `docker compose up` 部署,`verify-m5.py` 跑通完整闭环。
- Testcontainers 集成测试与 Playwright E2E 冒烟:能跑则跑,受环境限制(内存/浏览器)则标注降级路径。
- README(架构图 + 快速开始 + 演示脚本 + 安全设计 + 简历亮点)、`docs/architecture.md`。

## 环境事实(已探测)

- VM:<vm-host> / <user>,Docker 29.1.4,Compose v5.0.1,x86_64,4 核,3.8G 内存(可用约 2G),15G 磁盘。
- VM 无 node/npm;docker pull、npm registry、download.docker.com 均可达。
- VM 原生 mysqld 占用宿主 3306 → compose MySQL 容器**映射 33061:3306**,容器内互联仍用 `mysql:3306`。
- Windows 侧用 `.reasonix/tools/vm_ssh.py`(paramiko)执行 VM 命令与 scp。
- 内存紧张:Java 容器 `-Xmx256m`;MySQL 容器限制 buffer pool。

## 任务清单

### Task 5.1: Dockerfile 与 .dockerignore

**Files:**
- Create: `java/order-service/Dockerfile`、`java/inventory-service/Dockerfile`(多阶段:`maven:3.9-eclipse-temurin-21` 构建 → `eclipse-temurin:21-jre` 运行;启动参数 `-Xmx256m`;inventory 需读 `DEMO_MODE`/`DEMO_KEY` 环境变量)
- Create: `ai-service/Dockerfile`(`python:3.12-slim`,`pip install -e .`;非 root 用户;暴露 8000)
- Create: `web/Dockerfile`(多阶段:`node:22-alpine` 构建 → `nginx:alpine` 托管 dist + 反向代理 `/api` → `ai:8000`;含 `web/nginx.conf`)
- Create: `web/nginx.conf`
- Create: `java/.dockerignore`、`ai-service/.dockerignore`、`web/.dockerignore`

**Interfaces:**
- 构建产物:`order-service.jar` / `inventory-service.jar`(多阶段 maven 内构建);ai 直接安装源码;web 产出 `dist/` 由 nginx 托管。
- 运行期环境变量:Java 服务沿用本地配置(`DB_URL` 等指向 compose 服务名);AI 服务 `TRACEMIND_*` 前缀沿用;web 无环境变量(代理路径固定 `/api`)。

- [ ] **Step 1: 写三个 Dockerfile + nginx.conf + .dockerignore**
  - Java 多阶段:构建阶段 `mvn -q -pl inventory-service -am package -DskipTests`(order 同理);运行阶段 COPY jar。
  - AI:`COPY pyproject.toml .` → `RUN pip install .`(避免依赖重建)。
  - Web:nginx.conf 里 `location /api/ { proxy_pass http://ai:8000; }`(SSE 需 `proxy_buffering off;`),SPA fallback 到 `index.html`。
- [ ] **Step 2: 本地语法检查**
  - `docker compose config` 需要 compose 文件(Task 5.2),本步仅肉眼 + `nginx -t` 逻辑核对(无本地 docker,标注 VM 实测)。
- [ ] **Step 3: Commit**

```bash
git add java/*/Dockerfile java/.dockerignore ai-service/Dockerfile ai-service/.dockerignore web/Dockerfile web/nginx.conf web/.dockerignore
git commit -m "feat(docker): 四个组件的 Dockerfile 与 .dockerignore"
```

### Task 5.2: docker-compose.yml

**Files:**
- Create: `docker-compose.yml`

**Interfaces:**
- 服务:`mysql8`、`order-service`、`inventory-service`、`ai-service`、`web`。
- 网络:`tracemind-net`(bridge);依赖:`ai-service` depends_on `mysql8`(condition: service_healthy)。
- 卷:`mysql-data`、`ai-checkpoints`。

- [ ] **Step 1: 写 compose 文件**
  - `mysql8`:image `mysql:8.0`,环境变量(`MYSQL_ROOT_PASSWORD`/`MYSQL_DATABASE=tracemind_business`),healthcheck `mysqladmin ping -h localhost -uroot -p$MYSQL_ROOT_PASSWORD`,ports `33061:3306`(避开宿主 3306),command 调低内存:`--innodb-buffer-pool-size=64M --innodb-flush-log-at-trx-commit=2`。
  - `order-service`/`inventory-service`:build 各自目录,env `DB_URL=jdbc:mysql://mysql:3306/tracemind_business`、`DB_USERNAME`/`DB_PASSWORD`;inventory 额外 `DEMO_MODE=true`、`DEMO_KEY=demo-secret-2026`;ports `8081:8081`/`8082:8082`;healthcheck `curl -f http://localhost:808x/actuator/health`(或 wget)。
  - `ai-service`:build ai-service,env `TRACEMIND_DB_URL=mysql+pymysql://...@mysql:3306/tracemind_control` 等(三连接池、服务 URL 指向 compose 名)、`TRACEMIND_DEMO_MODE=true`、`TRACEMIND_DEMO_KEY=demo-secret-2026`;ports `8000:8000`;volume `ai-checkpoints:/app/data`;depends_on mysql healthy。
  - `web`:build web,ports `8080:80`,depends_on ai healthy。
- [ ] **Step 2: 本地静态检查 + Commit**
  - `docker compose config -q` 需 VM 实测;本步核对 YAML 语法(Python yaml.safe_load)与服务名一致性。
  - Commit:`git add docker-compose.yml && git commit -m "feat(docker): docker-compose 一键编排"`。

### Task 5.3: VM 部署与 verify-m5 全链路验收

**Interfaces:**
- scp 源码到 VM `~/tracemind`(排除 .venv/target/node_modules/.git)。
- VM 执行 `docker compose build` + `docker compose up -d`;等待健康。
- Windows 侧 `scripts/verify-m5.py` 从 `http://<vm-host>:8000` 跑完整闭环(reset→注入→创建→调查→审批→执行→恢复→报告),与 verify-m3 同构但 base URL 可配。

- [ ] **Step 1: 同步源码到 VM**
  - 用 vm_ssh.py 建目录 + sftp 批量上传(打包 tar 传一个文件更快:本地 `tar` 排除后上传,VM 解包)。
- [ ] **Step 2: VM 构建镜像**
  - `docker compose build`(首次拉 maven/node/python 镜像,较久;分步观察)。
- [ ] **Step 3: 启动并等待健康**
  - `docker compose up -d`;轮询各 healthcheck;记录容器状态 `docker compose ps`。
- [ ] **Step 4: 写并跑 verify-m5.py**
  - 脚本:全链路断言 + 输出 PASS;若某步失败打印实际响应。
- [ ] **Step 5: Commit 脚本**
  - `git add scripts/verify-m5.py && git commit -m "feat(docker): compose 部署验收脚本"`

### Task 5.4: README + 架构图 + 演示脚本

**Files:**
- Create: `README.md`、`docs/architecture.md`、`docs/demo-script.md`(或并入 README)
- Modify: `.gitignore`(补充 compose 卷名、`*.tgz` 等)

**Interfaces:**
- README:项目简介/架构图(mermaid + ASCII)/快速开始(Docker 一键 + 本地开发两套)/演示流程/安全设计/目录结构/测试/简历亮点(证据驱动根因判定、人机协同审批、全链路审计回放、真实 MySQL 证据)。
- architecture.md:组件图、LangGraph 节点图、数据模型、工具分组、SSE 事件流。
- demo-script.md:面试演示脚本(逐屏要点)。

- [ ] **Step 1: 写 README.md**
- [ ] **Step 2: 写 docs/architecture.md**
- [ ] **Step 3: 写 docs/demo-script.md**
- [ ] **Step 4: Commit**

### Task 5.5: Testcontainers 集成测试(尽力而为)

**Files:**
- Create: `java/inventory-service/src/test/java/com/tracemind/inventory/it/InventoryMySQLIT.java`(Testcontainers MySQL:起容器 → 建表 → 断言 `selectBySkuAndWarehouse` 走索引/返回正确)
- Modify: `java/pom.xml`(testcontainers BOM + mysql 模块,test scope)
- Create: `.testcontainers.properties`(可选,`docker.host=ssh://...` 标注需密钥)

**Interfaces:**
- 本地 `mvn test` 默认跳过 IT(`skipITs`);显式 `-Dit.test=...` 且 DOCKER_HOST 指向 VM 时运行。
- VM 原生 docker 可达时尝试实测;SSH 密码认证不被 Testcontainers transport 支持 → 降级为"代码就绪 + 文档标注",不阻塞交付。

- [ ] **Step 1: 写 IT 测试与 pom 依赖**
- [ ] **Step 2: 本地编译通过(`mvn -q -DskipTests compile`)**
- [ ] **Step 3: VM 实测(若 SSH transport 可行则跑,否则标注)**
- [ ] **Step 4: Commit**

### Task 5.6: E2E 冒烟(Playwright,尽力而为)+ 最终验收

**Files:**
- Create: `web/e2e/smoke.spec.ts` + `web/playwright.config.ts`(baseURL 可配,指向 VM `http://<vm-host>:8080`)
- Modify: `web/package.json`(devDeps:@playwright/test)

**Interfaces:**
- 浏览器:Windows 本机 node 24 装 Playwright chromium;target=VM web 服务。
- 冒烟:打开页面 → 注入 → 创建 Incident → 开始调查 → 等到 awaiting_approval → 批准 → 等到 recovered → 打开报告页。任何一步 60s 超时则失败。

- [ ] **Step 1: 写 config + smoke spec**
- [ ] **Step 2: 安装 playwright + chromium(Windows)**
- [ ] **Step 3: 跑冒烟(VM compose 需已运行);失败则修**
- [ ] **Step 4: Commit**

### M5 验收

1. 本地回归:`ai-service` pytest 全绿、`web` vitest 全绿 + build、`java` mvn test 全绿。
2. VM:`docker compose ps` 全 healthy;`verify-m5.py` PASS。
3. 简历素材:README 亮点章节 + demo-script 就绪。
4. 未竟项显式标注(如 Testcontainers 密码 transport、Playwright 若未跑通)。
