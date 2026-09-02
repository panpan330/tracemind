# 启动边界原则:本机 vs Docker

> 生成时间:2026-08(以提交为准)
> 依据:当前仓库 `compose.yml`、`ai-service/.env.local`、`ai-service/app/config/settings.py`、README。
> 一句话原则:**能在本机方便启动测试的,一律本机启动;原本就在 Docker 的中间件与展示面,继续用 Docker。**

---

## 0. 总原则

| 类别 | 归属 | 理由 |
|---|---|---|
| 开发测试主体(MySQL / ai-service / Java / web) | **本机** | 本机能方便启动、秒级反馈、断点调试 |
| 中间件(qdrant) | **Docker** | 本机不便跑,原本就在容器 |
| 观测展示面(prometheus / jaeger / otel / grafana) | **Docker** | 原本在容器;是项目的**展示界面**(Grafana 看板 / Jaeger Trace UI) |

**关键判断**:本机 MySQL 一直存在(Windows 服务,3306 监听),且 `.env.local` 与 `settings.py` 默认连接串都指向 `localhost:3306` —— 本机启动零配置改动。

---

## 1. 本机启动(Windows 直接跑)

| 服务 | 启动方式 | 端口 | 配置来源 |
|---|---|---|---|
| MySQL | Windows 服务(常驻,无需每次启动) | 3306 | 本机实例,已含 business/control 库 |
| ai-service | `cd ai-service && uv run uvicorn app.main:app --port 8000` | 8000 | `ai-service/.env.local`(已配 fake 模式 + localhost DB URL) |
| order-service | `cd java/order-service && mvn spring-boot:run` | 8081 | `java/order-service/src/main/resources/application.yml` |
| inventory-service | `cd java/inventory-service && mvn spring-boot:run` | 8082 | 同上(SCN-001/002 故障注入载体) |
| web | `cd web && npm run dev` | 5173(dev,代理 /api)→ 8080(构建) | Vite dev 服务器 |

**启动顺序建议**:MySQL(已常驻)→ Java 两服务 → ai-service → web。

### 本机连接事实(已核实)

- `settings.py` 默认值:
  - `control_db_url = mysql+pymysql://tracemind_control_app:***@localhost:3306/tracemind_control`
  - `readonly_db_url = mysql+pymysql://ai_investigator:***@localhost:3306/tracemind_business`
  - `session_terminator_db_url = ""`(本地处置 KILL 需在 `.env.local` 配置,见下)
  - `qdrant_url = http://127.0.0.1:6333`(本机 ai-service 连 Docker qdrant 的映射端口)
  - `llm_mode = "fake"`(默认;真实模型需改 `.env.local`)
- `ai-service/.env.local` 已配:`TRACEMIND_SESSION_TERMINATOR_DB_URL` 指向 `localhost:3306`、LLM 走百炼(qwen3.7-max,fake 模式不触网)。

---

## 2. Docker 启动(原本在容器,继续容器)

### 2.1 中间件:qdrant

```bash
docker compose up -d qdrant
```

- 端口:`127.0.0.1:6333:6333`(仅本机回环映射)
- 用途:长期记忆向量存储(`tracemind_case_memory` 等 collection)
- 注意:VM/本机重启后 runbook collection 可能丢失,需重建索引(见交接基线 §10.5)

### 2.2 观测展示面:prometheus / jaeger / otel-collector / grafana

```bash
docker compose --profile observability-ui up -d
```

- 全部挂 `profiles: [observability-ui]`,**不随默认 `docker compose up -d` 启动**,需显式拉起
- 端口(仅 127.0.0.1 回环):

| 服务 | 端口 | 展示定位 |
|---|---|---|
| grafana | 127.0.0.1:3000 | **指标看板**:P95 曲线,诊断前后性能对比的可视化 |
| jaeger | 127.0.0.1:16686 | **Trace UI**:慢请求链路,展示"耗时集中在数据库阶段"的证据 |
| prometheus | 9090(内部抓取) | 指标数据源(Java 管理端口 9081/9082 expose) |
| otel-collector | 4317(gRPC) | Trace 采集转发 |

> **定位说明**:观测栈不只是"验收工具",它是项目的**展示面**——Grafana/Jaeger 的 Web UI 是诊断证据的直观呈现,演示/面试时给他人看的核心界面。因此放在 Docker 作为独立可拉起的一套,不干扰本机开发。

### 2.3 重要联动(已核实,勿踩坑)

- mcp-tools 硬编码 `METRICS_BACKEND=prometheus`:观测栈未启动时,`get_service_metrics` 返回 `METRICS_BACKEND_UNAVAILABLE` → SCN-001 的 E1 证据永远采不到 → 诊断卡死。
- **本机开发用 `TRACEMIND_METRICS_BACKEND=fixture` / `TRACE_BACKEND=fixture`(默认)**,不依赖观测栈;只有真实验收/展示才拉起观测栈并切 `prometheus` / `jaeger`。

---

## 3. 三种运行形态速查

| 形态 | 启动内容 | 用途 |
|---|---|---|
| **本机开发** | 本机 MySQL + ai-service + Java + web(fixture 观测) | 日常开发、单测、断点调试 |
| **本机 + Docker 中间件** | 上述 + `docker compose up -d qdrant` | 需要长期记忆/RAG 时 |
| **全栈验收/展示** | 上述 + `docker compose --profile observability-ui up -d`(切真实观测) | SCN 真实验收、演示给他人看 |

---

## 4. 给新 Agent 的一句话

> 本机直接跑 `uvicorn`(ai-service)+ `mvn spring-boot:run`(Java)+ `npm run dev`(web),连**本机 MySQL(3306)**;qdrant 用 `docker compose up -d qdrant`,观测展示面(Grafana/Jaeger)用 `docker compose --profile observability-ui up -d`。本机开发默认 fixture 观测,真实验收/展示才拉起观测栈。
