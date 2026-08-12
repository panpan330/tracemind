# TraceMind V1.4 设计:可观测性升级(Micrometer/Prometheus Metrics + OTel Trace)

> 前置:V1.0(核心闭环)/ V1.1(真实 LLM + Tool Calling + RAG + 评测)/ V1.2(MCP 工具化)/ V1.3(多场景 SCN-002 + 回归评测流水线)均完成验收。
> 本文档定义 V1.4 范围与设计:把内部观测端点升级为**真实可观测性链路**——Metrics 用 Spring Boot Micrometer → Prometheus,Trace 用 OTel Java Agent → Collector → Jaeger;Agent 证据(metrics / trace)改为查询真实后端,消除对旧内部 `ObservationStore` 的依赖。

## 1. 目标与范围

- **目标**:让 Agent 的 E1(get_service_metrics)与 E2(get_trace)证据来自真实 Prometheus 与 Jaeger;交付可演示的 Grafana 监控面板;确立标准遥测管道(OTel Collector)与版本化供应链。
- **范围**:
  - Metrics 数据面:Micrometer Prometheus Registry → `/actuator/prometheus` → Prometheus 拉取 → Grafana + AI `get_service_metrics`
  - Trace 数据面:OTel Java Agent 自动插桩 → OTLP/gRPC → OTel Collector → Jaeger → AI `get_trace`
  - AI 侧客户端拆分(`PrometheusMetricsClient` / `JaegerTraceClient` / `TraceNormalizer`)与 Backend 收紧
  - 统一 OTel Trace ID(替换自定义 MDC trace id)
  - VM 部署(8GB 内存预算)+ E2E / 故障注入 / 回归适配
- **范围外(后续)**:Prometheus Exemplars、MCP HTTP/SSE 传输、调查回放、Qdrant 公网暴露与 TLS、非重叠区间合并(多 DB span)。

## 2. 架构总览

```
                        ┌─ Micrometer(/actuator/prometheus)──► Prometheus ─► Grafana(仅演示)
Java Services ──────────┤                                         │
(order/inventory)       │                                         └─► AI: PrometheusMetricsClient → get_service_metrics
                        │
                        └─ OTel Java Agent ──OTLP/gRPC──► OTel Collector ─► Jaeger
                                                                        │
                                                                        └─► AI: JaegerTraceClient → TraceNormalizer → get_trace
```

**两条数据面在采集、传输、存储、查询层分治;在证据层通过白名单服务、操作、Incident 时间窗口、trace_id 关联。**

| | Metrics | Trace |
|---|---|---|
| 采集 | Spring Boot Actuator + Micrometer(Prometheus Registry) | OTel Java Agent 自动插桩 |
| 传输 | Prometheus 主动拉取 `/actuator/prometheus` | OTLP/gRPC → OTel Collector → Jaeger |
| 消费 | Grafana(人工)+ AI `get_service_metrics` | Jaeger UI + AI `get_trace` |
| Backend 配置 | `TRACEMIND_METRICS_BACKEND=prometheus\|fixture` | `TRACEMIND_TRACE_BACKEND=jaeger\|fixture` |

## 3. 组件职责(单一职责)

- **PrometheusMetricsClient**:`query()` / `query_range()` / `get_service_metrics()`——只执行**固定 PromQL 模板注册表**(如 `HTTP_SERVER_P95_V1`),不接收 LLM 生成的查询文本;只查 Prometheus,输出 P95/QPS/错误率 + 观测时间窗口。
- **JaegerTraceClient**:`get_trace_by_id(trace_id)` / `search_traces(service_ref, operation_ref, start_time, end_time, strategy)`;优先稳定 gRPC QueryService(`jaeger:16685`);若用 UI HTTP JSON API 则封装在 Client 内并固定 Jaeger 版本(该 HTTP 接口属内部 API)。
- **TraceObservationService.get_representative_trace(...)**:先 `search_traces` 取候选 → 本地校验完整性 → 按 duration/error 排序 → 按 `strategy=SLOWEST|ERROR`(仅程序按调查阶段选择,LLM 不能生成)选出代表 → `get_trace_by_id` 加载完整链路。
- **TraceNormalizer**:Jaeger span 数据 → Agent 稳定证据结构(`TRACE_NORMALIZER_V1`);阶段映射基于语义属性与父子关系,见 §6。
- **MetricsObservationService / TraceObservationService**:上层门面,供 MCP 工具 handler 调用。
- **模型只选工具**:时间窗口、服务名、查询策略由程序从 Incident 与 Metrics 证据注入。

## 4. Java 侧改造

### 4.1 指标暴露(Micrometer → Prometheus)

- 双服务加 `micrometer-registry-prometheus` 依赖;Actuator 暴露 `/actuator/prometheus`。
- **Histogram 必须启用**:`management.metrics.distribution.percentiles-histogram.http.server.requests=true`,并配置显式 SLO buckets 覆盖 `10ms/50ms/100ms/250ms/500ms/1s/2s/5s/10s`(SCN-002 锁等待超时不能落在最高 bucket 外)。
- **服务标签稳定**:`management.metrics.tags.service=${spring.application.name}`(Micrometer 不天然输出 service 标签;HTTP 路由标签按真实输出可能是 `uri` 而非 `route`)。
- **启动契约测试(冻结标签契约与模板版本)**:`service=order-service|inventory-service` 存在;`uri` 使用低基数模板路径(非具体 ID 路径);管理端点与场景端点不进入业务查询;`P95 / QPS / 错误率` 模板各返回唯一预期时间序列。实现完成后**冻结标签契约与 PromQL 模板版本**,不能只写"以实际输出为准"。

### 4.2 Actuator 暴露收紧

- `management.endpoints.web.exposure.include=health,prometheus`;**禁止 `*`**;`show-details=never`;不暴露 `env` / `configprops` 等敏感端点。
- **管理端口与业务端口分离**:order 业务 8081 / 管理 9081;inventory 业务 8082 / 管理 9082。
- Compose 中管理端口只 `expose`(不映射宿主端口),Prometheus 内部抓取 `order-service:9081` / `inventory-service:9082`;不通过 Gateway / 公网暴露。

### 4.3 Trace 采集(OTel Java Agent)

- 镜像内下载**固定版本 + SHA-256 校验**的 `opentelemetry-javaagent.jar`(构建期从 Maven Central 拉取;校验失败终止构建);版本写入镜像 Label 并记录到回归报告;`opentelemetry-instrumentation-annotations` 也固定版本(Maven 锁定)。
- 容器启动 `java -javaagent:... -jar app.jar`,环境变量:

```env
OTEL_SERVICE_NAME=order-service|inventory-service
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
OTEL_EXPORTER_OTLP_PROTOCOL=grpc        # 显式指定(Java Agent 2.x 默认 http/protobuf)
OTEL_TRACES_EXPORTER=otlp
OTEL_METRICS_EXPORTER=none              # metrics 不走 OTLP,与 Micrometer/Prometheus 分治
OTEL_LOGS_EXPORTER=none
OTEL_TRACES_SAMPLER=always_on           # 仅演示环境;不宣称生产采样配置
OTEL_PROPAGATORS=tracecontext,baggage
OTEL_SEMCONV_STABILITY_OPT_IN=http,database   # 语义约定稳定字段(db.system.name 等)
OTEL_RESOURCE_ATTRIBUTES=service.version=<git-version>,deployment.environment.name=demo,service.instance.id=<container-instance>
```

- 自动插桩覆盖:Spring HTTP SERVER span、RestTemplate/WebClient CLIENT span(inventory_http)、JDBC CLIENT span(database)、`traceparent` 跨服务传播。

### 4.4 业务 Span(@WithSpan 注解,不手写 SDK)

只补自动插桩无法表达的业务方法语义,**不重复创建 HTTP / JDBC span**:

| Span | 服务 | 语义 |
|---|---|---|
| `order.reserve-inventory` | order-service | 订单侧库存预占业务操作 |
| `inventory.reserve` | inventory-service | 库存预占入口业务操作 |
| `inventory.lookup` | inventory-service | 库存查询入口业务操作 |
| `inventory.update` | inventory-service | 库存扣减/预占更新业务操作 |

- 不包:Controller(已有 HTTP Server span)、HTTP Client 调用、Mapper/JDBC 调用、**故障注入持锁线程**。
- **禁止** `inventory.lock-holder` / `scenario.inject` 等会泄露故障答案的 span(违背 V1.3"不向 Agent 传 scenario_id 与根因"不变量);故障注入控制行为仅走 Java 日志。
- 期望结构:`HTTP SERVER → order.reserve-inventory → HTTP CLIENT → inventory HTTP SERVER → inventory.reserve → JDBC CLIENT`。

### 4.5 统一 Trace ID

- **系统唯一 Trace ID = OpenTelemetry Trace ID**;删除 V1.0 手动生成传播的另一套 MDC trace id;跨服务传播完全交给 Java Agent 的 `traceparent`。
- `x-trace-id` 响应头若保留,值必须来自当前 `SpanContext.traceId`。
- 验收:HTTP `x-trace-id` = order OTel Trace ID = inventory OTel Trace ID = Jaeger 查询 Trace ID = Incident Evidence.trace_id(四者一致)。

### 4.6 internal 观测去留

- 现有 `ObservationStore` / `/internal/observations` 保留代码,但 V1.4 标准后端下 AI 不再查询。
- 开关:`TRACEMIND_INTERNAL_OBSERVATION_ENABLED` 默认 `false`,仅 test Profile 可开启;标准容器镜像与 VM 配置禁止开启;`/internal/observations` 测试模式下也不对外网暴露;V1.4 结束标记 deprecated,未来删除。

## 5. AI 侧改造

### 5.1 工具契约变化(get_trace)

- `get_service_metrics`:输出 `{windowStart, windowEnd, evaluatedAt, p95Ms, qps, errorRate}`;窗口由程序按 Prometheus scrape interval + Incident 时间 + padding 计算,模型不能决定任意范围;**不再返回 `representativeSlowTraceId`**。
- `get_trace` 输入改为**抽象 `trace_ref` 或 `trace_id`**;模型只见 `trace_ref = REPRESENTATIVE_SLOW_TRACE`,程序解析为 `service_ref + operation_ref + window + strategy`。
- **三层参数边界**:
  - 模型可见:`{"trace_ref": "REPRESENTATIVE_SLOW_TRACE"}`
  - AI 程序解析:`{"service_ref": "inventory-service", "operation_ref": "INVENTORY_RESERVATION", "window_start": "...", "window_end": "...", "strategy": "SLOWEST"}`
  - MCP 调用:Client 注入 `incident_id` / `agent_run_id`
- `trace_id` 只能来自:前序可信证据 / Jaeger 搜索结果 / 与 OTel 一致的 `x-trace-id`;不能由 LLM 直接生成。
- **`operation_ref` 来源(非根因上下文)**:Incident 创建时由调用方提供 `affected_service_ref` + `affected_operation_ref`(允许值 `ORDER_CREATE / INVENTORY_LOOKUP / INVENTORY_RESERVATION`),表示"哪个业务接口发生异常",**不代表 scenario_id / root_cause / DiagnosticPolicy / 修复动作**。程序通过注册表映射:`affected_operation_ref → Prometheus service/uri 模板 → Jaeger service/http.route → get_trace 搜索参数`;避免 LLM 从描述解析或生成任意 Route。
- **资格逻辑同步更新**:`get_service_metrics` 返回有效异常时间窗口 → 程序能解析 `service_ref + operation_ref + window` → `get_trace` eligible;同步更新 `compute_eligible_tools` / `resolve_arguments` / Deterministic Planner / Fixture Key / Evidence Evaluator / MCP Contract 与 Schema Hash(契约版本升级)。

### 5.2 TraceNormalizer 确定性规则(`TRACE_NORMALIZER_V1`)

1. 找到目标 inventory SERVER span
2. 找其全部后代 DB CLIENT span
3. 过滤 `db.system.name=mysql`(**稳定语义字段**;旧字段 `db.system` 兼容映射仅用于旧 Fixture 与迁移)
4. 过滤允许的 `db.operation.name`(SELECT / UPDATE;旧字段 `db.operation` 兼容映射)
5. 优先选择位于目标业务 span 下的 DB span
6. 使用**关键路径上耗时最长的目标 DB span**(多 DB span 不累加重叠区间;非重叠合并留后续)

- 阶段映射基于 `span.kind` / `service.name` / `http.route` / `server.address` / `db.system.name` / `db.operation.name` / `parent_span_id`;不依赖完整 SQL 文本(OTel 可能脱敏)。
- **语义约定策略**:Java Agent 配置 `OTEL_SEMCONV_STABILITY_OPT_IN=http,database` 后优先输出稳定字段;Normalizer 以稳定字段为主,并提供旧字段兼容映射(`db.system→db.system.name`、`db.operation→db.operation.name`)。
- 回归报告记录:OTel Semantic Convention 模式、OTel Java Agent 版本、TraceNormalizer 兼容字段版本。
- **契约测试必须使用真实 Java Agent Trace**,不能只用手工 Fixture 验证。
- 输出:

```json
{ "inventoryServerDurationMs": 900, "targetDbDurationMs": 820, "dbDominanceRatio": 0.91,
  "targetDbSpanId": "...", "normalizationRuleVersion": "TRACE_NORMALIZER_V1" }
```

- 鲁棒处理:span 乱序 / 缺可选属性 / 父 span 缺失 / 跨服务时钟偏差 / 重复 span / trace 不完整;无法可靠归一化时返回 **`TRACE_INCOMPLETE`**,不勉强生成 L2/E2 事实。
- **忽略管理端点与场景控制 span**;`operation_ref` 白名单只含真实业务接口。

### 5.3 Jaeger 搜索边界

- `MAX_TRACE_SEARCH_WINDOW_SECONDS` / `MAX_TRACE_CANDIDATES` 固定;`service_ref` / `operation_ref` 白名单;`strategy` 固定。
- 流程:按服务/操作/时间窗口查最多 N 条 → 本地校验 trace 完整性 → 按 duration/error 排序 → 选代表 → 按 trace_id 加载完整链路。

### 5.4 Span 导出延迟处理(SCN-002 时序)

Jaeger 只能查询**已结束并完成导出**的 span。**SCN-002 采用持续负载,保证两类证据同时存在**:

1. blocker 事务持续持锁
2. loadgen 持续发起库存预占请求(**固定并发参数**:`target_qps` / `max_in_flight` / `request_timeout` / `total_duration`,防止 HikariCP 连接池耗尽、线程堆积、CPU 升高混入第三种故障)
3. 至少一个请求超时结束,形成完整慢 Trace
4. 等待该 Trace 导出到 Jaeger(有限轮询)
5. loadgen 继续运行,保证当前仍有请求处于锁等待
6. **调查开始前断言**:至少一条已导出的超时 Trace(`minimum_completed_timeout_traces`)+ 至少一条实时锁等待(`minimum_active_lock_waiters`)+ HikariCP 未耗尽 + CPU 未超场景安全阈值——证明诊断的是长事务锁阻塞,而非负载发生器造成的连接池耗尽
7. 创建 Incident 并开始调查

- Jaeger:已结束的超时请求 → 证明数据库阶段耗时异常
- MySQL MCP 工具:当前锁等待关系 → 证明实时阻塞因果链
- 处置完成后停止 loadgen;恢复验证用正常探测请求。
- 配置:`TRACE_EXPORT_WAIT_TIMEOUT_SECONDS` / `TRACE_SEARCH_RETRY_INTERVAL_SECONDS` / `TRACE_SEARCH_MAX_ATTEMPTS`;短暂无结果有限轮询,**不回退 internal**。
- 错误语义:仅 `TRACE_NOT_FOUND`(可能由导出延迟导致)允许有限重试;Schema 错误、鉴权错误、非法响应不重试。

### 5.5 Backend 收紧与启动校验

- 配置:`TRACEMIND_METRICS_BACKEND=prometheus|fixture`、`TRACEMIND_TRACE_BACKEND=jaeger|fixture`、`TRACEMIND_PROMETHEUS_URL=http://prometheus:9090`、`TRACEMIND_JAEGER_QUERY_ENDPOINT=jaeger:16685`;地址只来自服务配置,LLM/Incident/前端不可传入。
- **fixture 生产保护**:`fixture` 仅 `TRACEMIND_EVAL_MODE=true` 或 test Profile 允许;fixture 模式禁止访问真实 Prometheus/Jaeger;VM/full E2E 启动校验强制 `prometheus + jaeger`(任一不满足拒绝执行,不自动切换)。
- 标准后端失败**明确报错,不静默回退 internal**。
- **错误码统一(10 个)**:`METRICS_BACKEND_UNAVAILABLE` / `METRICS_NOT_FOUND` / `METRICS_STALE` / `METRICS_RESULT_INVALID`;`TRACE_BACKEND_UNAVAILABLE` / `TRACE_NOT_FOUND` / `TRACE_EXPORT_TIMEOUT` / `TRACE_OUTSIDE_INCIDENT_WINDOW` / `TRACE_INCOMPLETE` / `TRACE_RESULT_INVALID`。**仅 `TRACE_NOT_FOUND`**(可能由导出延迟导致)允许有限重试;Schema 错误、鉴权错误、非法响应不重试。
- **证据新鲜度判定**:
  - Metrics:`latest_sample_at` 必须位于有效 Incident 窗口,且 `queried_at - latest_sample_at <= METRICS_MAX_AGE_SECONDS`;否则 `METRICS_STALE`,不生成 E1/共享 Fact
  - Trace:`trace_start/end` 必须与 Incident 窗口相交,且 service/operation 与当前 Incident 匹配;否则 `TRACE_OUTSIDE_INCIDENT_WINDOW`,不生成 E2/共享 Fact
  - 防止 Prometheus/Jaeger 中**上一次故障的数据**被错误用于当前 Incident。

### 5.6 证据溯源与审计

- 每条证据带 `sourceBackend` 与 TraceMind 生成的 `observationQueryId`(关联查询模板 / 参数 / 时间窗口 / backend / 结果 / 延迟 / 错误):

```json
{ "sourceBackend": "prometheus", "observationQueryId": "...", "queryTemplateId": "HTTP_SERVER_P95_V1", "observedAt": "..." }
{ "sourceBackend": "jaeger", "observationQueryId": "...", "traceId": "...", "observedAt": "..." }
```

- **审计不存完整原始响应**:存 `observation_query_id / backend / query_template_id / 规范化参数 / 时间窗口 / 调用状态 / 耗时 / 错误码 / result_hash / trace_id / normalized_result / created_at`;原始 Jaeger Trace 不写入控制库。
- **Jaeger 内存存储与复现边界**:Jaeger 进程生命周期内可通过 Trace ID 回查完整 Trace;**Jaeger 重启后**控制库仅保留归一化证据、关键 Span ID、Trace ID、结果 Hash 与查询元数据,**不保证重新取得原始 Trace**(持久化 Trace 存储属后续版本范围);简历不宣称原始 Trace 永久可回放。
- **Prometheus 持久化**:使用命名 Volume `prometheus-data`(否则"保留 6h"只在容器未重建时成立)。
- **时间语义拆分**:`queried_at`(TraceMind 发起查询时间)/ `window_start|end`(查询数据窗口)/ `latest_sample_at`(Prometheus 最新样本时间)/ `trace_start|end`(Jaeger Trace 实际时间)。

## 6. 部署(Compose,VM 8GB)

### 6.1 新增服务(全部固定版本,禁 latest;版本与 Digest 写入回归报告)

| 服务 | 镜像形态 | 角色 | 内存上限 |
|---|---|---|---|
| `otel-collector` | OTel Collector(含 receiver/processor/exporter/health 扩展) | OTLP/gRPC(4317,容器内)→ batch → Jaeger | 192MB |
| `jaeger` | Jaeger 2.x 统一镜像 | 内存存储(设最大 Trace 数,重启不保证保留)、Query API、UI | 512MB |
| `prometheus` | Prometheus | 拉取双服务 `/actuator/prometheus`,抓取 10~15s、保留 6h、限最大存储容量 | 384MB |
| `grafana` | grafana/grafana | 预置 1 张面板,仅人工演示;`observability-ui` Profile 默认不启动 | 256MB |

- `docker compose --profile observability-ui up -d` 按需启动 Grafana;核心 Agent 闭环不依赖 Grafana。

### 6.2 8GB 内存预算

| 组件 | 上限 |
|---|---|
| MySQL / Qdrant | 1GB / 512MB |
| order / inventory | 512MB 各(JVM `-Xms128m -Xmx256m -XX:MaxMetaspaceSize=128m`) |
| ai-service / Web / Loadgen | 768MB / 128MB / 256MB |
| Collector / Jaeger / Prometheus / Grafana | 192MB / 512MB / 384MB / 256MB(可选) |
| 合计 | ≈5GB(宿主机留 ≥3GB:daemon/缓存/网络/构建峰值) |

- 目标:正常运行 < 5.5GB;Full E2E 峰值 < 6.5GB;可用内存始终 ≥ 1GB。
- Compose 使用**实际生效**的 `mem_limit`,并用 `docker inspect` 验证;不能只写未生效的资源声明。

### 6.3 Collector Trace-only 管道

- Receiver 监听容器内 `0.0.0.0:4317`,**不映射到宿主机/公网**;Exporter 指向 `jaeger:4317`;开启 `sending_queue` + `retry_on_failure`;`memory_limiter` 上限低于 192MB 容器上限。
- `service.name` / `service.version` / `deployment.environment` 由 Java Agent 提供,Collector 不覆盖 `service.name`。

### 6.4 网络与暴露边界(网络分区)

- **`expose` 不限制同网络内访问**:Docker `expose` 只是声明端口,不阻止同一网络内其他容器连接;访问隔离由**独立 Docker 网络**保证。
- 网络划分:
  - `app-net`:order / inventory / ai / web / loadgen / mysql / qdrant(业务互通)
  - `metrics-scrape-net`:order / inventory / prometheus(管理端口 9081/9082 仅在此网,不映射宿主机)
  - `trace-ingest-net`:order / inventory / otel-collector / jaeger(OTLP 4317 仅在此网,不映射宿主机)
  - `observability-query-net`:ai / prometheus / jaeger / grafana
- 职责边界:AI 不能连接 Java 管理端口;Prometheus 不能访问业务数据库;Java 不能访问 Jaeger Query API;Collector 不能访问 Prometheus;Grafana 只能查询 Prometheus。
- 管理端口不映射宿主机,并只加入 `metrics-scrape-net`;人工 UI 仅绑宿主机回环:`127.0.0.1:16686:16686`(Jaeger)、`127.0.0.1:3000:3000`(Grafana);远程访问走 SSH Tunnel,不直接暴露公网。
- Grafana:禁匿名管理;管理员密码经环境 Secret 注入(不提交仓库);不在线安装动态插件;Dashboard 与数据源通过 Provisioning 进入版本控制。

### 6.5 配置文件(仓库新增 `observability/`)

```
observability/
├─ otel-collector.yaml
├─ jaeger.yaml
├─ prometheus.yml
└─ grafana/
   ├─ provisioning/datasources/ + dashboards/
   └─ dashboards/tracemind-overview.json
```

- Grafana 单面板:`order/inventory` P95/QPS/错误率、JVM Heap/CPU、HikariCP 活跃/空闲/等待连接、SCN-001/SCN-002 故障前后指标变化;仅人工展示,不参与 Agent Fact 判断。

### 6.6 启动与就绪校验(Full E2E 前)

依次确认:Collector Health → Jaeger Query API 可用 → Prometheus `/-/ready` → 双 Java Target UP → 双 Java Health → 至少完成两个 Scrape 周期 → 发送一条 **Canary 业务请求** → Jaeger 可查到完整 Trace → AI 配置 `prometheus + jaeger` → Fixture / Internal Observation 未启用。**不能只根据容器 running 判定观测系统可用**。

## 7. 评测与回归适配

- **本地 Fast**(不要求 Docker 与真实观测后端):`metrics_backend=fixture` + `trace_backend=fixture`;Fixture 仅 `TRACEMIND_EVAL_MODE=true` 或 test Profile 允许;执行 `mvn test` / `pytest` / `vitest` / Fake Agent Eval / Fixture RAG Eval。
- **VM Full**(`--tier full` 强制):`metrics_backend=prometheus` + `trace_backend=jaeger` + `internal_observation_enabled=false` + `eval_mode=false`;任一不满足拒绝执行,不自动切换。
- 回归报告记录:实际 Metrics/Trace Backend、OTel Java Agent 版本、OTel Collector 版本、Jaeger/Prometheus/Grafana 版本、`normalizationRuleVersion`、容器峰值内存、Prometheus Target 状态、Jaeger Canary Trace ID。

## 8. 验收

### 8.1 正常路径 E2E(自动断言)

- Metrics 证据 `sourceBackend=prometheus`;Trace 证据 `sourceBackend=jaeger`;`observationQueryId` 存在;Metrics 窗口包含真实 Prometheus 样本。
- `x-trace-id` = order Trace = inventory Trace = Jaeger Trace = Evidence Trace ID 完全一致。
- Trace 包含跨服务 HTTP span 与 inventory JDBC span;不含 `scenario.inject` / `lock-holder` / `SCN-001/002` 等答案泄露内容。
- Java 内部 Observation 端点关闭或返回 404;全程未调用 `/internal/observations`。
- SCN-001 E2E 连续 3/3;SCN-002 E2E 连续 3/3(SCN-002 另确认:Jaeger 存在已完成的超时慢 JDBC span + MySQL 存在当前实时锁等待证据 + HikariCP 未耗尽 + CPU 未超阈值;处置后锁等待消失、Trace 与指标恢复)。

### 8.3 Grafana Profile Smoke(单独验收)

- Prometheus 数据源 Healthy;Dashboard UID 存在;所有面板无查询错误;SCN-001/SCN-002 压测时能看到指标变化;Dashboard JSON 已提交仓库。
- Grafana 测试**不阻塞默认 Agent 闭环**,但**阻塞"V1.4 展示交付完成"验收**。

### 8.2 Backend 故障验收(独立 `observability-resilience` 测试)

- 停 Prometheus → `METRICS_BACKEND_UNAVAILABLE`,不回退 Internal;恢复后等待 Target UP。
- 停 Jaeger → `TRACE_BACKEND_UNAVAILABLE`,不回退 Internal;恢复后生成新请求验证查询恢复(Jaeger 内存存储,重启后历史 Trace 丢失是预期行为,恢复测试必须生成新请求)。
- 停 Collector → Java 业务请求仍可执行,新 Trace 无法进入 Jaeger;恢复后重新生成 Trace。
- 所有故障测试与双场景 E2E 用 `try/finally` 收尾:`reset scenario` → `restore observability services` → `stop loadgen`。

## 9. 版本兼容与后续

- **保持不变**:MCP 工具名称与数量(7 个)、stdio 传输方式、Agent 工具白名单安全原则、Fixture 隔离机制、双 Policy 与根因阈值语义、审批与处置框架、恢复判定。
- **必须改动(证据获取/归一化/资格适配)**:`get_trace` 输入 Schema 与 Handler、MCP Contract Version 与 Schema Hash、Fixture 内容及 Key、`compute_eligible_tools`、`resolve_arguments`、Metrics/Trace Evidence Evaluator、E1/E2 与共享 Fact 的数据提取适配器。
- **准确表述**:根因判定语义不变,但证据获取、归一化和资格计算适配新的标准观测后端。
- **后续扩展**:Prometheus Exemplars(metrics 与 trace 关联)、非重叠区间合并(多 DB span)、持久化 Trace 存储、MCP HTTP/SSE 传输、调查回放。
