# TraceMind V1.4 实施计划:可观测性升级(Micrometer/Prometheus Metrics + OTel Trace)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Agent 的 metrics/trace 证据从内部 `ObservationStore` 升级为真实 Prometheus + Jaeger(OTel),交付 Grafana 面板与可复现的观测验收。

**Architecture:** 信号分治——Micrometer Prometheus Registry → `/actuator/prometheus` → Prometheus 拉取 → Grafana/AI;OTel Java Agent → OTLP/gRPC → Collector → Jaeger → AI。AI 侧拆 `PrometheusMetricsClient` / `JaegerTraceClient` / `TraceNormalizer`,Backend 收紧(`prometheus|fixture` / `jaeger|fixture`)。

**Tech Stack:** Micrometer Prometheus Registry · OpenTelemetry Java Agent 2.x(@WithSpan 注解)· OTel Collector · Jaeger all-in-one · Prometheus · Grafana Provisioning · FastAPI httpx。

## Global Constraints

- **固定版本,禁 latest**:OTel Java Agent / OTel Collector / Jaeger / Prometheus / Grafana 镜像与 agent 全部固定具体版本 + SHA-256(agent);实际版本与 Digest 写入回归报告。
- **OTel 环境变量**:`OTEL_EXPORTER_OTLP_PROTOCOL=grpc`(4317,显式指定)、`OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317`、`OTEL_TRACES_EXPORTER=otlp`、`OTEL_METRICS_EXPORTER=none`、`OTEL_LOGS_EXPORTER=none`、`OTEL_TRACES_SAMPLER=always_on`(仅演示)、`OTEL_PROPAGATORS=tracecontext,baggage`、`OTEL_SEMCONV_STABILITY_OPT_IN=http,database`、`OTEL_RESOURCE_ATTRIBUTES=service.version=<git-version>,deployment.environment.name=demo,service.instance.id=<container-instance>`。
- **管理端口**:order 业务 8081 / 管理 9081;inventory 业务 8082 / 管理 9082;Actuator 仅 `health,prometheus`,`show-details=never`;管理端口不映射宿主机,只入 `metrics-scrape-net`。
- **统一 Trace ID**:系统唯一 = OTel Trace ID;删除手动 MDC trace id 传播;`x-trace-id` 响应头 = `SpanContext.traceId`;四者一致(order/inventory/Jaeger/Evidence)。
- **不泄露故障答案**:禁止 `inventory.lock-holder` / `scenario.inject` span;故障注入仅 Java 日志;`operation_ref` 白名单只含真实业务接口。
- **Backend 收紧**:`TRACEMIND_METRICS_BACKEND=prometheus|fixture`、`TRACEMIND_TRACE_BACKEND=jaeger|fixture`、`TRACEMIND_PROMETHEUS_URL`、`TRACEMIND_JAEGER_QUERY_ENDPOINT`(仅配置来源);`fixture` 仅 `TRACEMIND_EVAL_MODE=true` 或 test Profile;VM/full E2E 强制 `prometheus + jaeger`,任一不满足拒绝执行;标准后端失败不回退 internal。
- **错误码(10 个)**:`METRICS_BACKEND_UNAVAILABLE/METRICS_NOT_FOUND/METRICS_STALE/METRICS_RESULT_INVALID`、`TRACE_BACKEND_UNAVAILABLE/TRACE_NOT_FOUND/TRACE_EXPORT_TIMEOUT/TRACE_OUTSIDE_INCIDENT_WINDOW/TRACE_INCOMPLETE/TRACE_RESULT_INVALID`;仅 `TRACE_NOT_FOUND` 因导出延迟有限重试。
- **证据新鲜度**:Metrics `latest_sample_at` 在 Incident 窗口内且 `queried_at - latest_sample_at <= METRICS_MAX_AGE_SECONDS`,否则 `METRICS_STALE`;Trace `trace_start/end` 与 Incident 窗口相交且 service/operation 匹配,否则 `TRACE_OUTSIDE_INCIDENT_WINDOW`;两者不生成 E1/E2/共享 Fact。
- **非根因上下文**:Incident 创建携带 `affected_service_ref` + `affected_operation_ref`(允许值 `ORDER_CREATE/INVENTORY_LOOKUP/INVENTORY_RESERVATION`),不代表 scenario/root_cause/Policy/修复动作;程序注册表映射到 Prometheus 模板与 Jaeger 搜索。
- **TraceNormalizer**:`TRACE_NORMALIZER_V1`;稳定字段 `db.system.name`/`db.operation.name` 为主,旧字段兼容映射仅 Fixture/迁移;无法可靠归一化返回 `TRACE_INCOMPLETE`。
- **网络分区(4 网)**:`app-net`(order/inventory/ai/web/loadgen/mysql/qdrant)、`metrics-scrape-net`(order/inventory/prometheus)、`trace-ingest-net`(order/inventory/otel-collector/jaeger)、`observability-query-net`(ai/prometheus/jaeger/grafana);职责边界见 spec §6.4。
- **SCN-002 负载约束**:loadgen 固定 `target_qps/max_in_flight/request_timeout/total_duration`;调查前断言 `minimum_completed_timeout_traces` + `minimum_active_lock_waiters` + HikariCP 未耗尽 + CPU 未超阈值。
- **内存预算(8GB)**:`mem_limit` 实际生效并 `docker inspect` 验证;JVM `-Xms128m -Xmx256m -XX:MaxMetaspaceSize=128m`;Collector 192MB / Jaeger 512MB / Prometheus 384MB / Grafana 256MB(可选)。
- **Prometheus**:命名 Volume `prometheus-data`,抓取 10~15s,保留 6h,限最大存储。
- **Jaeger 复现边界**:进程生命周期内可回查 Trace ID;重启后控制库仅保留归一化证据/关键 Span ID/Trace ID/结果 Hash/查询元数据,不保证原始 Trace。
- **UI 暴露**:Jaeger UI 16686 与 Grafana 3000 仅绑 `127.0.0.1`;远程走 SSH Tunnel;Grafana 禁匿名管理、密码 Secret 注入、Provisioning 进版本控制。
- **Grafana**:`observability-ui` Profile 默认不启动;单面板;测试阻塞"展示交付完成",不阻塞 Agent 闭环。

---

### Task 1: Java 指标暴露升级(Prometheus + Histogram + 管理端口)

**Files:**
- Modify: `java/common/pom.xml`(若依赖统一管理)与 `java/order-service/pom.xml` / `java/inventory-service/pom.xml`(加 `micrometer-registry-prometheus`)
- Modify: `java/order-service/src/main/resources/application.yml`、`java/inventory-service/src/main/resources/application.yml`(management 配置)
- Test: `java/common/src/test/java/com/tracemind/common/obs/MetricsCollectorTest.java`(追加断言,若适用)

**Interfaces:**
- Consumes: 现有 `MetricsCollector`(common 模块)内部观测汇总。
- Produces: `/actuator/prometheus` 端点(仅 `metrics-scrape-net` 可达),含 `http_server_requests_seconds_bucket{...,le=...}` histogram 与 `service="order-service|inventory-service"` 标签。

- [ ] **Step 1: 两个服务 pom 加依赖**

`java/order-service/pom.xml` 与 `java/inventory-service/pom.xml` 的 dependencies 追加:

```xml
<dependency>
    <groupId>io.micrometer</groupId>
    <artifactId>micrometer-registry-prometheus</artifactId>
</dependency>
```

- [ ] **Step 2: application.yml 配置(两个服务)**

`order-service/src/main/resources/application.yml` 改为:

```yaml
server:
  port: ${ORDER_SERVICE_PORT:8081}
  management:
    port: ${ORDER_MANAGEMENT_PORT:9081}
spring:
  application:
    name: order-service
  datasource:
    url: ${BUSINESS_DB_URL:jdbc:mysql://localhost:3306/tracemind_business?useSSL=false&serverTimezone=Asia/Shanghai&allowPublicKeyRetrieval=true}
    username: ${BUSINESS_DB_USER:app_business}
    password: ${BUSINESS_DB_PASSWORD:app_business_pwd}
management:
  server:
    port: ${ORDER_MANAGEMENT_PORT:9081}
  endpoints:
    web:
      exposure:
        include: health,prometheus
  endpoint:
    health:
      show-details: never
  metrics:
    tags:
      service: order-service
    distribution:
      percentiles-histogram:
        http.server.requests: true
      slo:
        http.server.requests: 10ms,50ms,100ms,250ms,500ms,1s,2s,5s,10s
```

`inventory-service/src/main/resources/application.yml` 同样处理(端口 8082/9082、`service: inventory-service`、datasource 同业务库)。

- [ ] **Step 3: 本地编译验证**

Run: `cd java && export JAVA_HOME="D:\jdk21\jdk-21.0.12" && timeout 300 mvn -pl order-service,inventory-service -am -q compile`
Expected: BUILD SUCCESS

- [ ] **Step 4: 启动一个服务手动验证(本地 MySQL 需运行)**

Run: `cd java/inventory-service && "$JAVA_HOME/bin/java" -jar target/inventory-service-0.1.0-SNAPSHOT.jar --spring.config.additional-location=...`(或先 `mvn package` 后启动),然后:
```
curl -s http://localhost:9082/actuator/prometheus | grep -E "http_server_requests_seconds_bucket|service=\"inventory-service\""
```
Expected: 出现 `_bucket{...,le="0.05",...}` 行与 `service="inventory-service"` 标签;`/actuator/env` 返回 404。

- [ ] **Step 5: 提交**

```bash
git add java/order-service java/inventory-service
git commit -m "feat(java): Prometheus 指标暴露 — micrometer-registry-prometheus + histogram SLO + 管理端口 9081/9082 + service 标签"
```

---

### Task 2: OTel Java Agent 集成(Dockerfile + 环境变量)

**Files:**
- Modify: `java/order-service/Dockerfile`、`java/inventory-service/Dockerfile`
- Modify: `java/pom.xml` 或各服务 pom(`opentelemetry-instrumentation-annotations` 固定版本)
- Test: 无(容器构建 + VM 验证在 Task 15)

**Interfaces:**
- Consumes: OTel Java Agent(固定版本 + SHA-256,构建期下载)。
- Produces: 容器启动带 `-javaagent:...`;env 含 OTLP/gRPC 与资源属性;`@WithSpan` 注解可用(Task 3 使用)。

- [ ] **Step 1: 固定版本常量**

在 `java/order-service/Dockerfile` 与 `java/inventory-service/Dockerfile` 顶部:

```dockerfile
ARG OTEL_JAVA_AGENT_VERSION=2.14.0
ARG OTEL_JAVA_AGENT_SHA256=<实现时从 https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases 取该版本校验值>
```

- [ ] **Step 2: 构建期下载并校验 agent**

两个 Dockerfile 的 `FROM maven:3.9-eclipse-temurin-21 AS build` 阶段追加:

```dockerfile
# 固定版本 + SHA-256 校验的 OTel Java Agent
ADD https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/download/v${OTEL_JAVA_AGENT_VERSION}/opentelemetry-javaagent.jar /tmp/otel-agent.jar
RUN echo "${OTEL_JAVA_AGENT_SHA256}  /tmp/otel-agent.jar" | sha256sum -c - \
    && mkdir -p /opt/otel && cp /tmp/otel-agent.jar /opt/otel/opentelemetry-javaagent.jar
```

> 若 VM 网络无法直连 GitHub,改为从 Maven Central(`io/opentelemetry/javaagent/opentelemetry-javaagent`)经阿里云镜像下载;两种方式都要求 SHA-256 校验,校验失败终止构建。

- [ ] **Step 3: 运行时阶段拷贝 agent + 启动参数 + env**

两个 Dockerfile 的 `FROM eclipse-temurin:21-jre` 阶段:

```dockerfile
COPY --from=build /opt/otel/opentelemetry-javaagent.jar /opt/otel/opentelemetry-javaagent.jar
ENV JAVA_TOOL_OPTIONS="-javaagent:/opt/otel/opentelemetry-javaagent.jar"
ENV OTEL_SERVICE_NAME=order-service \
    OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317 \
    OTEL_EXPORTER_OTLP_PROTOCOL=grpc \
    OTEL_TRACES_EXPORTER=otlp \
    OTEL_METRICS_EXPORTER=none \
    OTEL_LOGS_EXPORTER=none \
    OTEL_TRACES_SAMPLER=always_on \
    OTEL_PROPAGATORS=tracecontext,baggage \
    OTEL_SEMCONV_STABILITY_OPT_IN=http,database \
    OTEL_RESOURCE_ATTRIBUTES=service.version=1.4.0,deployment.environment.name=demo,service.instance.id=${HOSTNAME}
```

(inventory 用 `OTEL_SERVICE_NAME=inventory-service`;`OTEL_SERVICE_NAME` 与 `OTEL_RESOURCE_ATTRIBUTES` 中的 service.name 不冲突——service.name 由 `OTEL_SERVICE_NAME` 提供,Resource Attributes 不覆盖。)

- [ ] **Step 4: 依赖注解库**

`java/order-service/pom.xml` 与 `java/inventory-service/pom.xml` 加(固定版本,如 `2.14.0`):

```xml
<dependency>
    <groupId>io.opentelemetry.instrumentation</groupId>
    <artifactId>opentelemetry-instrumentation-annotations</artifactId>
    <version>2.14.0</version>
</dependency>
```

- [ ] **Step 5: 本地编译验证**

Run: `cd java && export JAVA_HOME="D:\jdk21\jdk-21.0.12" && timeout 300 mvn -pl order-service,inventory-service -am -q compile`
Expected: BUILD SUCCESS(注解库编译通过)

- [ ] **Step 6: 提交**

```bash
git add java/order-service/Dockerfile java/inventory-service/Dockerfile java/*/pom.xml
git commit -m "feat(java): OTel Java Agent 集成 — 固定版本+SHA256,OTLP/gRPC,resource attributes"
```

---

### Task 3: 业务 Span + 统一 OTel Trace ID

**Files:**
- Modify: `java/inventory-service/src/main/java/com/tracemind/inventory/service/InventoryService.java`(或库存预占/查询/更新入口方法,加 `@WithSpan`)
- Modify: `java/order-service/src/main/java/com/tracemind/order/client/InventoryClient.java`(统一 trace id:删除手动 MDC 传播,x-trace-id 取 `SpanContext.traceId`)
- Modify: `java/order-service/src/main/java/com/tracemind/order/trace/TraceIdFilter.java`(x-trace-id 响应头改为 OTel trace id)
- Modify: `java/order-service/src/main/java/com/tracemind/order/service/OrderService.java`(订单侧业务方法,加 `@WithSpan("order.reserve-inventory")`)
- Test: `java/inventory-service/src/test/.../InventoryServiceTest.java`、`java/order-service/src/test/.../TraceIdFilterTest.java`(更新)

**Interfaces:**
- Consumes: OTel annotations(Task 2);`Span.current().getSpanContext().getTraceId()`。
- Produces: `x-trace-id` = OTel Trace ID;业务 span:`order.reserve-inventory` / `inventory.reserve` / `inventory.lookup` / `inventory.update`。

- [ ] **Step 1: 更新 TraceIdFilter(x-trace-id 取 OTel trace id)**

`TraceIdFilter.java` 改为:若当前 OTel Span 存在,`traceId = Span.current().getSpanContext().getTraceId()`;响应头 `x-trace-id` 写该值(十六进制 32 位);`traceparent` 由 agent 自动注入。移除手动生成/传播的自定义 trace id(MDC 写 traceId 保留,但值来自 OTel)。

- [ ] **Step 2: InventoryClient 移除手动 traceparent 传播**

删除自定义 trace id 头注入;跨服务传播完全交给 Java Agent(RestTemplate/WebClient 自动注入 `traceparent`)。

- [ ] **Step 3: 业务方法加 @WithSpan**

order-service 库存预占业务方法:

```java
import io.opentelemetry.instrumentation.annotations.WithSpan;

@WithSpan("order.reserve-inventory")
public boolean reserveInventory(long skuId, long warehouseId, int quantity) { ... }
```

inventory-service 三个入口业务方法分别加 `@WithSpan("inventory.reserve")` / `@WithSpan("inventory.lookup")` / `@WithSpan("inventory.update")`。**禁止**给故障注入持锁线程或场景控制加任何 span。

- [ ] **Step 4: 更新单测并运行**

Run: `cd java && export JAVA_HOME="D:\jdk21\jdk-21.0.12" && timeout 300 mvn -pl order-service,inventory-service -am -q test`
Expected: 全绿(TraceIdFilterTest 断言 x-trace-id 为 32 位 hex 且等于 OTel trace id 格式)

- [ ] **Step 5: 提交**

```bash
git add java/order-service java/inventory-service
git commit -m "feat(java): 业务 span(@WithSpan 4 个)+ 统一 OTel Trace ID(x-trace-id=SpanContext.traceId)"
```
---

### Task 4: AI 配置扩展(Backend 收紧 + 超时 + 新鲜度)

**Files:**
- Modify: `ai-service/app/config.py`
- Test: `ai-service/tests/test_config.py`(追加)

**Interfaces:**
- Consumes: 现有 `Settings`(pydantic-settings,前缀 `TRACEMIND_`)。
- Produces: 配置字段 `metrics_backend / trace_backend / prometheus_url / jaeger_query_endpoint / metrics_max_age_seconds / trace_export_wait_timeout_seconds / trace_search_retry_interval_seconds / trace_search_max_attempts / max_trace_search_window_seconds / max_trace_candidates / internal_observation_enabled`。

- [ ] **Step 1: 写失败测试**

`ai-service/tests/test_config.py` 追加:

```python
from app.config import Settings


def test_observability_defaults():
    s = Settings()
    assert s.metrics_backend == "fixture"
    assert s.trace_backend == "fixture"
    assert s.prometheus_url == "http://localhost:9090"
    assert s.jaeger_query_endpoint == "localhost:16685"
    assert s.metrics_max_age_seconds == 120
    assert s.trace_export_wait_timeout_seconds == 30
    assert s.trace_search_retry_interval_seconds == 2
    assert s.trace_search_max_attempts == 5
    assert s.max_trace_search_window_seconds == 600
    assert s.max_trace_candidates == 20
    assert s.internal_observation_enabled is False
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && uv run pytest tests/test_config.py -q`
Expected: FAIL(AttributeError: 字段不存在)

- [ ] **Step 3: config.py 追加字段**

在 `Settings` 中(现有字段之后)追加:

```python
    # ---- V1.4 可观测性 ----
    metrics_backend: str = "fixture"              # prometheus | fixture
    trace_backend: str = "fixture"                # jaeger | fixture
    prometheus_url: str = "http://localhost:9090"
    jaeger_query_endpoint: str = "localhost:16685"  # gRPC QueryService
    metrics_max_age_seconds: int = 120
    trace_export_wait_timeout_seconds: int = 30
    trace_search_retry_interval_seconds: int = 2
    trace_search_max_attempts: int = 5
    max_trace_search_window_seconds: int = 600
    max_trace_candidates: int = 20
    internal_observation_enabled: bool = False
```

- [ ] **Step 4: 运行确认通过**

Run: `cd ai-service && uv run pytest tests/test_config.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add ai-service/app/config.py ai-service/tests/test_config.py
git commit -m "feat(ai): V1.4 可观测性配置 — backend/url/超时/新鲜度字段(默认 fixture)"
```

---

### Task 5: PrometheusMetricsClient(固定 PromQL 模板)

**Files:**
- Create: `ai-service/app/services/prometheus_client.py`
- Create: `ai-service/app/services/promql_templates.py`
- Test: `ai-service/tests/test_prometheus_client.py`

**Interfaces:**
- Consumes: `settings.prometheus_url`、`settings.metrics_max_age_seconds`。
- Produces:
  - `promql_templates.TEMPLATES: dict[str, dict]`(`HTTP_SERVER_P95_V1` / `HTTP_SERVER_QPS_V1` / `HTTP_SERVER_ERROR_RATE_V1`)
  - `PrometheusMetricsClient.query(query_template_id: str, labels: dict, window_seconds: int) -> list[dict]`
  - `PrometheusMetricsClient.get_service_metrics(service_ref: str, window_start: str, window_end: str) -> dict`(含 `windowStart/windowEnd/evaluatedAt/p95Ms/qps/errorRate/latestSampleAt/sourceBackend/observationQueryId`)

- [ ] **Step 1: 写失败测试**

`ai-service/tests/test_prometheus_client.py`:

```python
import pytest
from app.services.prometheus_client import PrometheusMetricsClient
from app.services import promql_templates


class FakeResponse:
    def __init__(self, data): self._data = data
    def json(self): return self._data
    def raise_for_status(self): pass


class FakeClient:
    def __init__(self, responses): self.responses = responses; self.calls = []
    def post(self, url, data=None, **kw):
        self.calls.append((url, data))
        return FakeResponse(self.responses.pop(0))


def test_templates_registered():
    assert "HTTP_SERVER_P95_V1" in promql_templates.TEMPLATES
    assert "HTTP_SERVER_QPS_V1" in promql_templates.TEMPLATES
    assert "HTTP_SERVER_ERROR_RATE_V1" in promql_templates.TEMPLATES


def test_get_service_metrics_parses_instant_vector(monkeypatch):
    fake = FakeClient([
        {"status": "success", "data": {"resultType": "vector",
         "result": [{"metric": {"le": "+Inf"}, "value": [1700000000.0, "0.42"]}]}},
        {"status": "success", "data": {"resultType": "vector",
         "result": [{"metric": {}, "value": [1700000000.0, "12.5"]}]}},
        {"status": "success", "data": {"resultType": "vector",
         "result": [{"metric": {}, "value": [1700000000.0, "0.08"]}]}},
    ])
    monkeypatch.setattr("app.services.prometheus_client.httpx.Client", lambda *a, **k: fake)
    c = PrometheusMetricsClient(base_url="http://prom:9090")
    out = c.get_service_metrics("inventory-service", "2026-08-12T00:00:00Z", "2026-08-12T00:05:00Z")
    assert out["p95Ms"] == 420 and out["qps"] == 12.5 and out["errorRate"] == 0.08
    assert out["sourceBackend"] == "prometheus"
    assert out["queryTemplateId"] == "HTTP_SERVER_P95_V1"
    assert len(fake.calls) == 3  # P95/QPS/错误率各一次,不接收 LLM 生成的查询文本


def test_stale_detection(monkeypatch):
    import time
    old = time.time()
    fake = FakeClient([
        {"status": "success", "data": {"resultType": "vector",
         "result": [{"metric": {"le": "+Inf"}, "value": [old - 500, "0.42"]}]}},
        {"status": "success", "data": {"resultType": "vector", "result": []}},
        {"status": "success", "data": {"resultType": "vector", "result": []}},
    ])
    monkeypatch.setattr("app.services.prometheus_client.httpx.Client", lambda *a, **k: fake)
    c = PrometheusMetricsClient(base_url="http://prom:9090")
    with pytest.raises(ValueError) as ei:
        c.get_service_metrics("inventory-service", "2026-08-12T00:00:00Z", "2026-08-12T00:05:00Z")
    assert "METRICS_STALE" in str(ei.value)
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && uv run pytest tests/test_prometheus_client.py -q`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: promql_templates.py(固定模板注册表)**

```python
"""固定 PromQL 模板注册表(V1.4 冻结;标签契约见 spec §4.1)。
指标名基于 Micrometer 实际输出(http_server_requests_seconds_*)。"""

TEMPLATES = {
    "HTTP_SERVER_P95_V1": {
        "expr": ('histogram_quantile(0.95, sum by (le) ('
                 'rate(http_server_requests_seconds_bucket{service=~"%(service)s",'
                 'uri=~"%(uri)s",%(method)s%(status)s}[%(window)s])))'),
    },
    "HTTP_SERVER_QPS_V1": {
        "expr": ('sum(rate(http_server_requests_seconds_count{service=~"%(service)s",'
                 'uri=~"%(uri)s",%(method)s%(status)s}[%(window)s]))'),
    },
    "HTTP_SERVER_ERROR_RATE_V1": {
        "expr": ('sum(rate(http_server_requests_seconds_count{service=~"%(service)s",'
                 'uri=~"%(uri)s",status=~"5.."}[%(window)s])) / '
                 '(sum(rate(http_server_requests_seconds_count{service=~"%(service)s",'
                 'uri=~"%(uri)s"}[%(window)s])) + 1e-9)'),
    },
}
```

> 注:`%(method)s` / `%(status)s` 由调用方填充为空串或 `method="GET",` 片段;Task 15 VM 验证后按 Micrometer 真实输出回填并冻结模板版本。

- [ ] **Step 4: prometheus_client.py**

```python
"""PrometheusMetricsClient:只执行固定 PromQL 模板,不接收 LLM 生成的查询文本。"""
import time
import uuid
import httpx

from app.config import settings
from app.services import promql_templates

ERROR_METRICS_BACKEND_UNAVAILABLE = "METRICS_BACKEND_UNAVAILABLE"
ERROR_METRICS_NOT_FOUND = "METRICS_NOT_FOUND"
ERROR_METRICS_STALE = "METRICS_STALE"
ERROR_METRICS_RESULT_INVALID = "METRICS_RESULT_INVALID"


class PrometheusMetricsClient:
    def __init__(self, base_url: str | None = None):
        self.base_url = base_url or settings.prometheus_url

    def query(self, query_template_id: str, labels: dict,
              window_seconds: int) -> list[dict]:
        tpl = promql_templates.TEMPLATES.get(query_template_id)
        if tpl is None:
            raise ValueError(ERROR_METRICS_RESULT_INVALID)
        expr = tpl["expr"] % labels
        try:
            with httpx.Client(base_url=self.base_url, timeout=10.0) as client:
                resp = client.post("/api/v1/query",
                                   data={"query": expr, "time": str(int(time.time()))})
                resp.raise_for_status()
                body = resp.json()
        except httpx.HTTPError as e:
            raise ValueError(ERROR_METRICS_BACKEND_UNAVAILABLE) from e
        if body.get("status") != "success":
            raise ValueError(ERROR_METRICS_BACKEND_UNAVAILABLE)
        result = body.get("data", {}).get("result", [])
        if not result:
            raise ValueError(ERROR_METRICS_NOT_FOUND)
        return result

    def _latest_sample_time(self, result: list[dict]) -> float:
        ts = 0.0
        for r in result:
            val = r.get("value") or []
            if val:
                ts = max(ts, float(val[0]))
        return ts

    def get_service_metrics(self, service_ref: str,
                            window_start: str, window_end: str) -> dict:
        obs_id = uuid.uuid4().hex[:12]
        evaluated_at = int(time.time())
        window = f"{int(settings.metrics_max_age_seconds * 2)}s"
        labels = {"service": service_ref, "uri": ".+", "method": "",
                  "status": "", "window": window}
        p95_rows = self.query("HTTP_SERVER_P95_V1", labels, 300)
        qps_rows = self.query("HTTP_SERVER_QPS_V1", labels, 300)
        err_rows = self.query("HTTP_SERVER_ERROR_RATE_V1", labels, 300)
        latest = self._latest_sample_time(p95_rows)
        if evaluated_at - latest > settings.metrics_max_age_seconds:
            raise ValueError(ERROR_METRICS_STALE)
        try:
            p95 = float(p95_rows[0].get("value", [0, 0])[1]) * 1000.0
            qps = float(qps_rows[0].get("value", [0, 0])[1])
            err = float(err_rows[0].get("value", [0, 0])[1])
        except (IndexError, TypeError, ValueError) as e:
            raise ValueError(ERROR_METRICS_RESULT_INVALID) from e
        return {
            "sourceBackend": "prometheus",
            "observationQueryId": obs_id,
            "queryTemplateId": "HTTP_SERVER_P95_V1",
            "windowStart": window_start,
            "windowEnd": window_end,
            "evaluatedAt": evaluated_at,
            "latestSampleAt": int(latest),
            "p95Ms": p95,
            "qps": qps,
            "errorRate": err,
        }
```

- [ ] **Step 5: 运行确认通过**

Run: `cd ai-service && uv run pytest tests/test_prometheus_client.py -q`
Expected: 3 passed

- [ ] **Step 6: 提交**

```bash
git add ai-service/app/services/prometheus_client.py ai-service/app/services/promql_templates.py ai-service/tests/test_prometheus_client.py
git commit -m "feat(ai): PrometheusMetricsClient — 固定 PromQL 模板注册表 + 新鲜度判定"
```

---

### Task 6: JaegerTraceClient + TraceNormalizer

**Files:**
- Create: `ai-service/app/services/jaeger_client.py`
- Create: `ai-service/app/services/trace_normalizer.py`
- Test: `ai-service/tests/test_jaeger_client.py`、`ai-service/tests/test_trace_normalizer.py`

**Interfaces:**
- Consumes: `settings.jaeger_query_endpoint`、`settings.trace_search_retry_interval_seconds`、`settings.trace_search_max_attempts`、`settings.max_trace_search_window_seconds`、`settings.max_trace_candidates`。
- Produces:
  - `JaegerTraceClient.get_trace_by_id(trace_id: str) -> dict`(Jaeger gRPC QueryService 响应,含 spans)
  - `JaegerTraceClient.search_traces(service_ref: str, operation_ref: str, start_time: str, end_time: str, strategy: str) -> list[dict]`(候选摘要,按 duration 排序,限 `max_trace_candidates`)
  - `TraceNormalizer.normalize(trace: dict, operation_ref: str) -> dict`(`TRACE_NORMALIZER_V1`;失败返回 `{"status": "TRACE_INCOMPLETE"}`)

- [ ] **Step 1: 写失败测试**

`ai-service/tests/test_trace_normalizer.py`(真实 Java Agent Trace 的契约验证在 Task 15):

```python
from app.services.trace_normalizer import TraceNormalizer


def _span(span_id, kind, service, name, start, dur, parent=None, attrs=None):
    return {"spanId": span_id, "kind": kind, "process": {"serviceName": service},
            "operationName": name, "startTime": start, "duration": dur,
            "parentSpanId": parent, "tags": attrs or {}}


def test_normalize_full_chain():
    trace = {"traceID": "abc", "spans": [
        _span("s1", "SPAN_KIND_SERVER", "inventory-service", "GET /api/inventory",
              1000, 900000, None, {"http.route": "/api/inventory"}),
        _span("s2", "SPAN_KIND_INTERNAL", "inventory-service", "inventory.lookup",
              1100, 850000, "s1", {}),
        _span("s3", "SPAN_KIND_CLIENT", "inventory-service", "SELECT inventory",
              1200, 820000, "s2", {"db.system.name": "mysql",
                                   "db.operation.name": "SELECT"}),
    ]}
    out = TraceNormalizer().normalize(trace, "INVENTORY_LOOKUP")
    assert out["inventoryServerDurationMs"] == 900
    assert out["targetDbDurationMs"] == 820
    assert out["dbDominanceRatio"] > 0.9
    assert out["targetDbSpanId"] == "s3"
    assert out["normalizationRuleVersion"] == "TRACE_NORMALIZER_V1"


def test_normalize_ignores_management_spans():
    trace = {"traceID": "def", "spans": [
        _span("s1", "SPAN_KIND_SERVER", "inventory-service", "GET /api/inventory",
              1000, 900000, None, {"http.route": "/api/inventory"}),
        _span("s2", "SPAN_KIND_SERVER", "inventory-service", "GET /internal/scenarios/inject",
              2000, 10000, None, {"http.route": "/internal/scenarios/inject"}),
    ]}
    out = TraceNormalizer().normalize(trace, "INVENTORY_LOOKUP")
    # 管理 span 被忽略,仍以业务 SERVER span 为准
    assert out["inventoryServerDurationMs"] == 900


def test_normalize_incomplete_trace_returns_incomplete():
    trace = {"traceID": "ghi", "spans": [
        _span("s1", "SPAN_KIND_SERVER", "inventory-service", "GET /api/inventory",
              1000, 900000, None, {"http.route": "/api/inventory"}),
    ]}  # 无 DB CLIENT span
    out = TraceNormalizer().normalize(trace, "INVENTORY_LOOKUP")
    assert out.get("status") == "TRACE_INCOMPLETE"
```

`ai-service/tests/test_jaeger_client.py`:

```python
from app.services.jaeger_client import JaegerTraceClient


def test_search_uses_whitelisted_bounds(monkeypatch):
    captured = {}
    def fake_query(endpoint, request):
        captured["req"] = request
        return {"traces": []}
    monkeypatch.setattr("app.services.jaeger_client._query_grpc", fake_query)
    c = JaegerTraceClient(endpoint="jaeger:16685")
    out = c.search_traces("inventory-service", "INVENTORY_RESERVATION",
                          "2026-08-12T00:00:00Z", "2026-08-12T00:05:00Z", "SLOWEST")
    assert out == []
    assert captured["req"]["service"] == "inventory-service"
    assert captured["req"]["operation"] == "INVENTORY_RESERVATION"
    assert captured["req"]["limit"] <= 20
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && uv run pytest tests/test_trace_normalizer.py tests/test_jaeger_client.py -q`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: trace_normalizer.py**

```python
"""TraceNormalizer(TRACE_NORMALIZER_V1):Jaeger span → Agent 稳定证据结构。"""
import datetime

NORMALIZER_VERSION = "TRACE_NORMALIZER_V1"
ERROR_TRACE_INCOMPLETE = "TRACE_INCOMPLETE"

# 稳定语义字段 + 旧字段兼容映射(仅 Fixture/迁移;agent 配 SEMCONV_STABILITY_OPT_IN 后输出稳定字段)
_SEMCONV_LEGACY_MAP = {"db.system": "db.system.name", "db.operation": "db.operation.name"}


def _attr(tags: dict, key: str):
    if key in tags:
        return tags[key]
    return tags.get(_SEMCONV_LEGACY_MAP.get(key))


class TraceNormalizer:
    def normalize(self, trace: dict, operation_ref: str) -> dict:
        spans = trace.get("spans") or []
        by_id = {s.get("spanId"): s for s in spans}
        servers = [s for s in spans
                   if s.get("kind") == "SPAN_KIND_SERVER"
                   and s.get("process", {}).get("serviceName") == "inventory-service"
                   and not str(s.get("operationName", "")).startswith("/internal/")]
        if not servers:
            return {"status": ERROR_TRACE_INCOMPLETE, "normalizationRuleVersion": NORMALIZER_VERSION}
        server = sorted(servers, key=lambda s: s.get("duration", 0), reverse=True)[0]
        server_ms = server.get("duration", 0) / 1000.0
        db_spans = []
        for s in spans:
            if s.get("kind") != "SPAN_KIND_CLIENT":
                continue
            tags = s.get("tags") or {}
            if _attr(tags, "db.system.name") != "mysql":
                continue
            if _attr(tags, "db.operation.name") not in ("SELECT", "UPDATE"):
                continue
            if not self._is_descendant(s, server, by_id):
                continue
            db_spans.append(s)
        if not db_spans:
            return {"status": ERROR_TRACE_INCOMPLETE, "normalizationRuleVersion": NORMALIZER_VERSION}
        target = max(db_spans, key=lambda s: s.get("duration", 0))
        db_ms = target.get("duration", 0) / 1000.0
        ratio = (db_ms / server_ms) if server_ms > 0 else 0.0
        start_us = trace.get("startTime") or 0
        return {
            "status": "ok",
            "inventoryServerDurationMs": round(server_ms),
            "targetDbDurationMs": round(db_ms),
            "dbDominanceRatio": round(ratio, 2),
            "targetDbSpanId": target.get("spanId"),
            "traceId": trace.get("traceID"),
            "traceStart": _iso(start_us),
            "traceEnd": _iso(start_us + (server.get("duration", 0) or 0)),
            "normalizationRuleVersion": NORMALIZER_VERSION,
        }

    @staticmethod
    def _is_descendant(span, ancestor, by_id):
        cur = span.get("parentSpanId")
        seen = 0
        while cur and seen < 100:
            if cur == ancestor.get("spanId"):
                return True
            parent = by_id.get(cur)
            if not parent:
                return False
            cur = parent.get("parentSpanId")
            seen += 1
        return False


def _iso(epoch_us: int) -> str:
    return datetime.datetime.fromtimestamp(epoch_us / 1_000_000,
                                           tz=datetime.timezone.utc).isoformat()
```

- [ ] **Step 4: jaeger_client.py**

```python
"""JaegerTraceClient:search_traces / get_trace_by_id;固定搜索边界,服务/操作白名单由调用方保证。"""
from app.config import settings

ERROR_TRACE_BACKEND_UNAVAILABLE = "TRACE_BACKEND_UNAVAILABLE"
ERROR_TRACE_NOT_FOUND = "TRACE_NOT_FOUND"
ERROR_TRACE_RESULT_INVALID = "TRACE_RESULT_INVALID"


class JaegerTraceClient:
    def __init__(self, endpoint: str | None = None):
        self.endpoint = endpoint or settings.jaeger_query_endpoint

    def search_traces(self, service_ref: str, operation_ref: str,
                      start_time: str, end_time: str, strategy: str) -> list[dict]:
        request = {
            "service": service_ref,
            "operation": operation_ref,
            "start": start_time,
            "end": end_time,
            "limit": settings.max_trace_candidates,
            "strategy": strategy,
        }
        resp = _query_grpc(self.endpoint, request)
        traces = resp.get("traces") or []
        traces.sort(key=lambda t: _total_duration(t), reverse=(strategy == "SLOWEST"))
        return traces[:settings.max_trace_candidates]

    def get_trace_by_id(self, trace_id: str) -> dict:
        resp = _query_grpc(self.endpoint, {"trace_id": trace_id})
        trace = resp.get("trace")
        if not trace:
            raise ValueError(ERROR_TRACE_NOT_FOUND)
        return trace


def _query_grpc(endpoint: str, request: dict) -> dict:
    """Task 15 接入真实 Jaeger gRPC QueryService;此处为接口占位(测试 monkeypatch)。"""
    raise NotImplementedError("gRPC QueryService 实现在 Task 15")


def _total_duration(trace: dict) -> int:
    spans = trace.get("spans") or []
    return max((s.get("duration") or 0) for s in spans) if spans else 0
```

- [ ] **Step 5: 运行确认通过**

Run: `cd ai-service && uv run pytest tests/test_trace_normalizer.py tests/test_jaeger_client.py -q`
Expected: 4 passed(jaeger 测试 monkeypatch `_query_grpc`,不触网)

- [ ] **Step 6: 提交**

```bash
git add ai-service/app/services/jaeger_client.py ai-service/app/services/trace_normalizer.py ai-service/tests/
git commit -m "feat(ai): JaegerTraceClient + TraceNormalizer(TRACE_NORMALIZER_V1)"
```

---

### Task 7: 服务层双后端(metrics/trace 门面)

**Files:**
- Modify: `ai-service/app/services/metrics_service.py`、`ai-service/app/services/trace_service.py`
- Test: `ai-service/tests/test_observation_services.py`

**Interfaces:**
- Consumes: `PrometheusMetricsClient`(Task 5)、`JaegerTraceClient`/`TraceNormalizer`(Task 6)、`settings.metrics_backend/trace_backend`。
- Produces:
  - `metrics_service.get_metrics(service_ref, window_start, window_end) -> dict`
  - `trace_service.get_trace(trace_ref: str | None, trace_id: str | None, incident: dict) -> dict`(trace_ref 由程序解析为 service/operation/window/strategy)

- [ ] **Step 1: 写失败测试**

`ai-service/tests/test_observation_services.py`:

```python
from app.services import metrics_service, trace_service


def test_metrics_fixture_backend(monkeypatch):
    monkeypatch.setattr("app.config.settings.metrics_backend", "fixture")
    out = metrics_service.get_metrics("inventory-service",
                                      "2026-08-12T00:00:00Z", "2026-08-12T00:05:00Z")
    assert out["sourceBackend"] == "fixture"


def test_metrics_prometheus_backend(monkeypatch):
    monkeypatch.setattr("app.config.settings.metrics_backend", "prometheus")
    captured = {}
    def fake(service, ws, we):
        captured["ok"] = True
        return {"sourceBackend": "prometheus", "p95Ms": 100}
    monkeypatch.setattr("app.services.prometheus_client.PrometheusMetricsClient", fake)
    out = metrics_service.get_metrics("inventory-service", "a", "b")
    assert captured["ok"] and out["p95Ms"] == 100


def test_trace_fixture_backend(monkeypatch):
    monkeypatch.setattr("app.config.settings.trace_backend", "fixture")
    out = trace_service.get_trace(trace_ref="REPRESENTATIVE_SLOW_TRACE", trace_id=None,
                                  incident={"id": 1, "affected_service_ref": "inventory-service",
                                            "affected_operation_ref": "INVENTORY_LOOKUP"})
    assert out["sourceBackend"] == "fixture"


def test_trace_jaeger_backend_maps_ref(monkeypatch):
    monkeypatch.setattr("app.config.settings.trace_backend", "jaeger")
    calls = {}
    class FakeClient:
        def search_traces(self, svc, op, s, e, strat):
            calls.update(svc=svc, op=op, strat=strat)
            return [{"traceID": "t1"}]
        def get_trace_by_id(self, tid):
            return {"traceID": tid, "spans": []}
    class FakeNorm:
        def normalize(self, trace, op):
            return {"status": "ok", "dbDominanceRatio": 0.9,
                    "normalizationRuleVersion": "TRACE_NORMALIZER_V1"}
    monkeypatch.setattr("app.services.jaeger_client.JaegerTraceClient", FakeClient)
    monkeypatch.setattr("app.services.trace_normalizer.TraceNormalizer", FakeNorm)
    out = trace_service.get_trace(trace_ref="REPRESENTATIVE_SLOW_TRACE", trace_id=None,
                                  incident={"id": 1, "affected_service_ref": "inventory-service",
                                            "affected_operation_ref": "INVENTORY_RESERVATION"})
    assert calls["svc"] == "inventory-service"
    assert calls["op"] == "INVENTORY_RESERVATION"
    assert calls["strat"] == "SLOWEST"
    assert out["sourceBackend"] == "jaeger" and out["traceId"] == "t1"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && uv run pytest tests/test_observation_services.py -q`
Expected: FAIL(当前 metrics_service/trace_service 无这些签名)

- [ ] **Step 3: metrics_service.py 重写**

```python
"""get_service_metrics 后端门面:prometheus | fixture。"""
from app.config import settings
from app.services.prometheus_client import PrometheusMetricsClient


def get_metrics(service_ref: str, window_start: str, window_end: str) -> dict:
    if settings.metrics_backend == "prometheus":
        return PrometheusMetricsClient().get_service_metrics(service_ref, window_start, window_end)
    return {"sourceBackend": "fixture", "p95Ms": 2, "qps": 10.0, "errorRate": 0.0,
            "windowStart": window_start, "windowEnd": window_end,
            "observationQueryId": "fixture-0"}
```

- [ ] **Step 4: trace_service.py 重写**

```python
"""get_trace 后端门面:jaeger | fixture;trace_ref 由程序解析为搜索参数。"""
from app.config import settings
from app.services.jaeger_client import JaegerTraceClient
from app.services.trace_normalizer import TraceNormalizer


def _resolve_incident_window(incident: dict) -> tuple[str, str]:
    start = incident.get("observed_at") or "2026-08-12T00:00:00Z"
    return start, "2026-08-12T00:05:00Z"  # Task 15 按 Incident 真实窗口回填


def get_trace(trace_ref: str | None, trace_id: str | None, incident: dict) -> dict:
    service_ref = incident.get("affected_service_ref") or "inventory-service"
    operation_ref = incident.get("affected_operation_ref") or "INVENTORY_LOOKUP"
    if settings.trace_backend == "jaeger":
        client = JaegerTraceClient()
        if trace_id:
            raw = client.get_trace_by_id(trace_id)
        else:
            start, end = _resolve_incident_window(incident)
            candidates = client.search_traces(service_ref, operation_ref, start, end, "SLOWEST")
            if not candidates:
                raise ValueError("TRACE_NOT_FOUND")
            raw = client.get_trace_by_id(candidates[0]["traceID"])
        normalized = TraceNormalizer().normalize(raw, operation_ref)
        if normalized.get("status") == "TRACE_INCOMPLETE":
            raise ValueError("TRACE_INCOMPLETE")
        return {"sourceBackend": "jaeger", "traceId": raw.get("traceID"),
                "observationQueryId": "obs-" + str(raw.get("traceID", ""))[:8], **normalized}
    return {"sourceBackend": "fixture", "traceId": "fixture-trace-1",
            "inventoryServiceDurationMs": 900, "targetDbDurationMs": 820,
            "dbDominanceRatio": 0.91, "targetDbSpanId": "s3",
            "normalizationRuleVersion": "TRACE_NORMALIZER_V1"}
```

- [ ] **Step 5: 运行确认通过**

Run: `cd ai-service && uv run pytest tests/test_observation_services.py -q`
Expected: 4 passed

- [ ] **Step 6: 提交**

```bash
git add ai-service/app/services/metrics_service.py ai-service/app/services/trace_service.py ai-service/tests/test_observation_services.py
git commit -m "feat(ai): metrics/trace 服务层双后端(prometheus|fixture / jaeger|fixture)+ trace_ref 程序解析"
```
---

### Task 8: Incident 非根因上下文(affected_service_ref / affected_operation_ref)

**Files:**
- Modify: `ai-service/app/api/incidents.py`(创建 Incident 接受上下文字段)
- Modify: `ai-service/app/db/models.py`(incident 表加列,如可空)
- Modify: `scripts/sql/04-control-schema.sql`(迁移:两列可空)
- Test: `ai-service/tests/test_api_incidents.py`(追加)

**Interfaces:**
- Consumes: 现有 Incident 创建 API。
- Produces: Incident 记录 `affected_service_ref` + `affected_operation_ref`(允许值 `ORDER_CREATE/INVENTORY_LOOKUP/INVENTORY_RESERVATION`);供 Task 7 的 trace 搜索与 Task 9 的资格逻辑使用。

- [ ] **Step 1: 写失败测试**

`ai-service/tests/test_api_incidents.py` 追加:

```python
def test_create_incident_accepts_operation_context(client):
    r = client.post("/api/incidents", json={
        "title": "t", "description": "d", "severity": "high",
        "service_ref": "inventory-service",
        "affected_service_ref": "inventory-service",
        "affected_operation_ref": "INVENTORY_RESERVATION"})
    assert r.status_code == 201
    body = r.json()
    assert body["affected_operation_ref"] == "INVENTORY_RESERVATION"


def test_operation_ref_whitelist(client):
    r = client.post("/api/incidents", json={
        "title": "t", "description": "d", "severity": "high",
        "service_ref": "inventory-service",
        "affected_operation_ref": "DROP_TABLE"})
    assert r.status_code == 422  # 白名单外拒绝
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && uv run pytest tests/test_api_incidents.py -q`
Expected: FAIL

- [ ] **Step 3: 模型与 schema 加列**

`ai-service/app/db/models.py` 的 `Incident` 加:

```python
    affected_service_ref: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    affected_operation_ref: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
```

`scripts/sql/04-control-schema.sql` 的 `incident` 建表追加两列(幂等:本地库用 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 兼容脚本或 information_schema 判断,VM 走增量迁移)。

- [ ] **Step 4: API 接受并白名单校验**

`ai-service/app/api/incidents.py` 创建请求体加 `affected_service_ref: str | None` 与 `affected_operation_ref: str | None`;`affected_operation_ref` 校验 ∈ `{ORDER_CREATE, INVENTORY_LOOKUP, INVENTORY_RESERVATION}`(不合法返回 422);写入 Incident。

- [ ] **Step 5: 本地库执行迁移**

Run: `mysql -uroot -proot tracemind_control -e "ALTER TABLE incident ADD COLUMN affected_service_ref VARCHAR(64) NULL; ALTER TABLE incident ADD COLUMN affected_operation_ref VARCHAR(64) NULL;" 2>&1 | grep -v Warning; echo migrated`

- [ ] **Step 6: 运行确认通过**

Run: `cd ai-service && uv run pytest tests/test_api_incidents.py -q`
Expected: PASS(新增 2 + 既有)

- [ ] **Step 7: 提交**

```bash
git add ai-service/app/api/incidents.py ai-service/app/db/models.py scripts/sql/04-control-schema.sql ai-service/tests/test_api_incidents.py
git commit -m "feat(ai): Incident 非根因上下文 — affected_service_ref/affected_operation_ref(白名单)"
```

---

### Task 9: tool_calling 资格/解析改造(get_trace 抽象 trace_ref)

**Files:**
- Modify: `ai-service/app/agent/tool_calling.py`(compute_eligible_tools / resolve_arguments)
- Modify: `ai-service/app/agent/determinism.py`(planner 的 get_trace 参数构造)
- Test: `ai-service/tests/test_tool_calling.py`、`ai-service/tests/test_determinism.py`(追加)

**Interfaces:**
- Consumes: Incident 的 `affected_service_ref/affected_operation_ref`(Task 8)。
- Produces:
  - `compute_eligible_tools`:`get_trace` eligible 条件从"metrics 返回 representativeSlowTraceId"改为"metrics 证据含有效异常时间窗口,且 Incident 能解析 service_ref + operation_ref + window"
  - `resolve_arguments("get_trace", ...)`:模型传 `{"trace_ref": "REPRESENTATIVE_SLOW_TRACE"}` 或 `{"trace_id": "..."}`;程序解析为 `{"service_ref": ..., "operation_ref": ..., "window_start": ..., "window_end": ..., "strategy": "SLOWEST"}`(trace_id 优先)

- [ ] **Step 1: 写失败测试**

`ai-service/tests/test_tool_calling.py` 追加:

```python
def test_get_trace_eligible_with_metrics_window():
    state = {"incident_id": 1,
             "affected_service_ref": "inventory-service",
             "affected_operation_ref": "INVENTORY_LOOKUP",
             "evidence_gate": {"E1": True}, "evidence": []}
    eligible = tool_calling.compute_eligible_tools(state)
    assert "get_trace" in eligible


def test_get_trace_not_eligible_without_window():
    state = {"incident_id": 1, "evidence_gate": {}, "evidence": []}
    eligible = tool_calling.compute_eligible_tools(state)
    assert "get_trace" not in eligible


def test_resolve_get_trace_trace_ref():
    state = {"incident_id": 1, "affected_service_ref": "inventory-service",
             "affected_operation_ref": "INVENTORY_RESERVATION",
             "observed_at": "2026-08-12T00:00:00Z"}
    out = tool_calling.resolve_arguments("get_trace", {"trace_ref": "REPRESENTATIVE_SLOW_TRACE"}, state)
    assert out["service_ref"] == "inventory-service"
    assert out["operation_ref"] == "INVENTORY_RESERVATION"
    assert out["strategy"] == "SLOWEST"


def test_resolve_get_trace_trace_id_priority():
    state = {"incident_id": 1, "affected_service_ref": "inventory-service",
             "affected_operation_ref": "INVENTORY_RESERVATION"}
    out = tool_calling.resolve_arguments("get_trace", {"trace_id": "abc123"}, state)
    assert out["trace_id"] == "abc123"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && uv run pytest tests/test_tool_calling.py -q`
Expected: FAIL

- [ ] **Step 3: compute_eligible_tools 改造**

现有 `get_trace` 资格分支改为:

```python
    if "get_trace" not in eligible and not satisfied("e2"):
        # V1.4:metrics 证据含有效异常时间窗口且 Incident 可解析 service/operation/window 才放行
        ev = {e.get("key"): e for e in state.get("evidence") or []}
        e1_content = (ev.get("e1") or {}).get("content") or {}
        has_window = bool(e1_content.get("windowStart") and e1_content.get("windowEnd"))
        has_ctx = bool(state.get("affected_service_ref") and state.get("affected_operation_ref"))
        if has_window and has_ctx:
            eligible.add("get_trace")
```

- [ ] **Step 4: resolve_arguments 加 get_trace 分支**

```python
    if name == "get_trace":
        raw_trace_id = (raw_args or {}).get("trace_id")
        if raw_trace_id:
            return {"trace_id": raw_trace_id}
        return {
            "service_ref": state.get("affected_service_ref") or state.get("service_ref"),
            "operation_ref": state.get("affected_operation_ref") or "INVENTORY_LOOKUP",
            "window_start": state.get("observed_at") or "2026-08-12T00:00:00Z",
            "window_end": "2026-08-12T00:05:00Z",
            "strategy": "SLOWEST",
        }
```

- [ ] **Step 5: determinism.py planner 适配**

`DeterministicEvidencePlanner` 中 e2 的 `_arguments_for` 返回 `{"trace_ref": "REPRESENTATIVE_SLOW_TRACE"}`(而非内部 trace_id);删除对 E1 content 的 `representativeSlowTraceId` 依赖。

- [ ] **Step 6: 运行确认通过**

Run: `cd ai-service && uv run pytest tests/test_tool_calling.py tests/test_determinism.py -q`
Expected: PASS(新增 4 + 既有;若旧测试断言 `representativeSlowTraceId` 依赖,同步更新)

- [ ] **Step 7: 提交**

```bash
git add ai-service/app/agent/tool_calling.py ai-service/app/agent/determinism.py ai-service/tests/
git commit -m "feat(tooling): get_trace 资格改为异常窗口驱动 + trace_ref 抽象解析(三层参数)"
```

---

### Task 10: MCP 契约升级 + Evidence Evaluator 适配

**Files:**
- Modify: `ai-service/app/mcp/contract.py`(get_trace Schema,契约版本升级)
- Modify: `ai-service/app/mcp/server.py`(get_trace handler 签名)
- Modify: `ai-service/app/agent/nodes.py`(`_evaluate_metrics` / `_evaluate_trace` 适配新证据结构 + 新鲜度)
- Modify: `ai-service/app/agent/facts.py`(共享 Fact 数据提取适配)
- Modify: `data/eval_cases/*`(Fixture 更新:metrics 窗口结构、trace_ref)
- Test: `ai-service/tests/test_contract.py`、`ai-service/tests/test_agent_graph.py`(追加)

**Interfaces:**
- Consumes: `metrics_service.get_metrics` / `trace_service.get_trace`(Task 7)、`tool_calling`(Task 9)。
- Produces: MCP Contract Version 升级;`get_trace` 输入 Schema 为 `trace_ref | trace_id`;E1 证据结构含 `windowStart/windowEnd/latestSampleAt`;E2 证据结构为 TraceNormalizer 输出。

- [ ] **Step 1: 写失败测试**

`ai-service/tests/test_contract.py` 追加:

```python
def test_get_trace_schema_trace_ref():
    schemas = contract.mcp_tool_schemas()
    props = schemas["get_trace"]["properties"]
    assert "trace_ref" in props or "trace_id" in props
    assert "representative_slow_trace_id" not in props  # 旧字段移除


def test_contract_version_bumped():
    assert contract.MCP_TOOL_CONTRACT_VERSION == "2.1.0"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && uv run pytest tests/test_contract.py -q`
Expected: FAIL

- [ ] **Step 3: contract.py 升级**

- `MCP_TOOL_CONTRACT_VERSION = "2.1.0"`;`get_trace` 的 input_schema 改为 `GetTraceIn(trace_ref: str | None = None, trace_id: str | None = None)`(`trace_ref` 枚举 `REPRESENTATIVE_SLOW_TRACE`);删除旧 `trace_id` 必填与内部字段。

- [ ] **Step 4: server.py get_trace handler**

```python
@mcp.tool()
def get_trace(incident_id: int, agent_run_id: int,
              trace_ref: str | None = None, trace_id: str | None = None) -> dict:
    incident = incident_repo.get_incident(incident_id)
    return trace_service.get_trace(trace_ref, trace_id,
                                   {"id": incident_id,
                                    "affected_service_ref": getattr(incident, "affected_service_ref", None),
                                    "affected_operation_ref": getattr(incident, "affected_operation_ref", None),
                                    "observed_at": str(incident.created_at)})
```

- [ ] **Step 5: nodes.py evaluator 适配**

`_evaluate_metrics`:`content` 记录 `windowStart/windowEnd/latestSampleAt/sourceBackend`;新鲜度判定由 PrometheusMetricsClient 负责(抛 `METRICS_STALE` → 评估器不产出 E1)。

`_evaluate_trace`:输入改为 TraceNormalizer 输出结构:

```python
def _evaluate_trace(result: dict, state: dict) -> list[dict]:
    data = result.get("data") or {}
    if data.get("sourceBackend") != "jaeger" and data.get("sourceBackend") != "fixture":
        return []
    passed = bool(data.get("dbDominanceRatio") is not None
                  and (data.get("dbDominanceRatio") or 0) >= 0.5
                  and data.get("inventoryServerDurationMs"))
    return [{"id": "E2", "key": "e2", "source": "get_trace",
             "content": data, "passed": passed}]
```

- [ ] **Step 6: facts.py 适配**

`evaluate_facts` 的 E2 Fact(`F_DB_STAGE_DOMINANT`)改读 `content.get("dbDominanceRatio") >= 0.5`(兼容旧 `inventory_service` 结构:迁移期两者并存)。

- [ ] **Step 7: Fixture 更新**

`data/eval_cases/*.json` 中 `get_trace` 的 fixture key 与 data 改为新结构(`dbDominanceRatio` 等);`get_service_metrics` fixture data 加 `windowStart/windowEnd/latestSampleAt`(latestSampleAt 用当前时间,避免 stale)。

- [ ] **Step 8: 运行确认通过 + 全量回归**

Run: `cd ai-service && uv run pytest tests/ -q`
Expected: 全绿(含更新后的 Fixture 评测;旧断言同步)

- [ ] **Step 9: 提交**

```bash
git add ai-service/app/mcp ai-service/app/agent/nodes.py ai-service/app/agent/facts.py data/eval_cases ai-service/tests/
git commit -m "feat(mcp): 契约 2.1.0 — get_trace 抽象 trace_ref;E1/E2 证据结构适配 + 新鲜度"
```

---

### Task 11: 观测审计表(observation_query)

**Files:**
- Modify: `scripts/sql/04-control-schema.sql`(追加表,幂等)
- Create: `ai-service/app/repositories/observation_repo.py`
- Modify: `ai-service/app/services/metrics_service.py` / `trace_service.py`(写入审计)
- Test: `ai-service/tests/test_observation_services.py`(追加)

**Interfaces:**
- Consumes: `observationQueryId` / `sourceBackend` / `queryTemplateId`(Task 5/7 产出)。
- Produces: `observation_repo.record_query(...)`;表 `observation_query`(id/incident_id/agent_run_id/observation_query_id/backend/query_template_id/normalized_params_json/window_start/window_end/status/error_code/duration_ms/result_hash/trace_id/normalized_result_json/queried_at)。

- [ ] **Step 1: DDL 追加**

`04-control-schema.sql` 追加(幂等 `CREATE TABLE IF NOT EXISTS`):

```sql
CREATE TABLE IF NOT EXISTS observation_query (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    incident_id BIGINT NULL,
    agent_run_id BIGINT NULL,
    observation_query_id VARCHAR(64) NOT NULL,
    backend VARCHAR(16) NOT NULL,
    query_template_id VARCHAR(64) NULL,
    normalized_params_json JSON NULL,
    window_start VARCHAR(40) NULL,
    window_end VARCHAR(40) NULL,
    status VARCHAR(16) NOT NULL,
    error_code VARCHAR(48) NULL,
    duration_ms INT NULL,
    result_hash VARCHAR(64) NULL,
    trace_id VARCHAR(64) NULL,
    normalized_result_json JSON NULL,
    queried_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    INDEX idx_incident (incident_id),
    INDEX idx_obs_id (observation_query_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

- [ ] **Step 2: 本地库执行**

Run: `mysql -uroot -proot tracemind_control < scripts/sql/04-control-schema.sql 2>&1 | grep -v Warning | head -2; echo done`

- [ ] **Step 3: observation_repo.py**

```python
"""observation_query 审计写入(control 库);不存原始 Prometheus/Jaeger 响应。"""
import json
from sqlalchemy import text
from app.db.engine import get_control_engine

control_engine = get_control_engine()


def record_query(*, incident_id, agent_run_id, observation_query_id, backend,
                 query_template_id, normalized_params, window_start, window_end,
                 status, error_code, duration_ms, result_hash, trace_id,
                 normalized_result) -> None:
    with control_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO observation_query (incident_id, agent_run_id, observation_query_id, "
            "backend, query_template_id, normalized_params_json, window_start, window_end, "
            "status, error_code, duration_ms, result_hash, trace_id, normalized_result_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"),
            (incident_id, agent_run_id, observation_query_id, backend, query_template_id,
             json.dumps(normalized_params, ensure_ascii=False), window_start, window_end,
             status, error_code, duration_ms, result_hash, trace_id,
             json.dumps(normalized_result, ensure_ascii=False)))
```

- [ ] **Step 4: 服务层接入审计(失败/成功都记录,不存原始响应)**

`metrics_service.get_metrics` 与 `trace_service.get_trace` 在执行后调用 `record_query`(成功:`status="ok"`、`result_hash=sha256(json.dumps(result))[:16]`;异常:`status="error"`、`error_code=...`);`incident_id/agent_run_id` 从调用上下文传入(服务层签名加 `incident_id/agent_run_id` 参数,默认 0)。

- [ ] **Step 5: 测试追加**

`ai-service/tests/test_observation_services.py` 追加(monkeypatch `observation_repo.record_query` 断言被调用且不含原始 trace):

```python
def test_metrics_records_audit_without_raw(monkeypatch):
    records = []
    def fake_record(**kw):
        records.append(kw)
        assert "normalized_result" in kw  # 仅归一化结果
    monkeypatch.setattr("app.services.metrics_service.observation_repo.record_query", fake_record)
    monkeypatch.setattr("app.config.settings.metrics_backend", "fixture")
    metrics_service.get_metrics("inventory-service", "a", "b", incident_id=7, agent_run_id=8)
    assert records and records[0]["incident_id"] == 7
```

- [ ] **Step 6: 运行确认通过 + 提交**

Run: `cd ai-service && uv run pytest tests/test_observation_services.py -q`
Expected: PASS(5)

```bash
git add scripts/sql/04-control-schema.sql ai-service/app/repositories/observation_repo.py ai-service/app/services/ ai-service/tests/test_observation_services.py
git commit -m "feat(audit): observation_query 审计(元数据+归一化结果,不存原始响应)"
```
---

### Task 12: observability/ 配置(Collector / Jaeger / Prometheus / Grafana)

**Files:**
- Create: `observability/otel-collector.yaml`
- Create: `observability/jaeger.yaml`
- Create: `observability/prometheus.yml`
- Create: `observability/grafana/provisioning/datasources/prometheus.yml`
- Create: `observability/grafana/provisioning/dashboards/dashboards.yml`
- Create: `observability/grafana/dashboards/tracemind-overview.json`
- Test: `ai-service/tests/test_observability_config.py`(YAML 解析与关键键断言)

**Interfaces:**
- Consumes: 无(纯配置;被 Task 13 的 compose 引用)。
- Produces: VM 部署使用的观测配置;`otel-collector.yaml` 的 trace-only 管道;`prometheus.yml` 抓取 9081/9082。

- [ ] **Step 1: 写失败测试**

`ai-service/tests/test_observability_config.py`:

```python
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_collector_trace_only_pipeline():
    cfg = yaml.safe_load((ROOT / "observability" / "otel-collector.yaml").read_text(encoding="utf-8"))
    svc = cfg["service"]["pipelines"]
    assert "traces" in svc and "metrics" not in svc and "logs" not in svc
    rcvr = svc["traces"]["receivers"][0]
    assert rcvr == "otlp"


def test_prometheus_scrapes_management_ports():
    cfg = yaml.safe_load((ROOT / "observability" / "prometheus.yml").read_text(encoding="utf-8"))
    targets = cfg["scrape_configs"][0]["static_configs"][0]["targets"]
    assert "order-service:9081" in targets and "inventory-service:9082" in targets


def test_grafana_provisioning_committed():
    ds = ROOT / "observability" / "grafana" / "provisioning" / "datasources" / "prometheus.yml"
    dash = ROOT / "observability" / "grafana" / "dashboards" / "tracemind-overview.json"
    assert ds.exists() and dash.exists()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && uv run pytest tests/test_observability_config.py -q`
Expected: FAIL(FileNotFoundError)

- [ ] **Step 3: otel-collector.yaml(trace-only 管道)**

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
processors:
  memory_limiter:
    check_interval: 1s
    limit_mib: 160
  batch:
    send_batch_size: 512
    timeout: 5s
exporters:
  otlp:
    endpoint: jaeger:4317
    tls:
      insecure: true
service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [otlp]
```

> 不配置 metrics/logs pipeline;4317 仅容器内监听,compose 不映射宿主机;`service.name` 由 Java Agent 提供,Collector 不覆盖。

- [ ] **Step 4: jaeger.yaml(all-in-one 内存存储)**

```yaml
# jaegertracing/all-in-one 环境变量在 compose 中设置:
# SPAN_STORAGE_TYPE=memory MEMORY_MAX_TRACES=10000 COLLECTOR_OTLP_ENABLED=true
```

- [ ] **Step 5: prometheus.yml**

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s
  scrape_timeout: 10s
scrape_configs:
  - job_name: tracemind-java
    metrics_path: /actuator/prometheus
    static_configs:
      - targets: ["order-service:9081", "inventory-service:9082"]
```

> 保留时间与容量上限在 compose 启动参数(TSDB retention + storage)中设置;数据落命名 Volume `prometheus-data`。

- [ ] **Step 6: grafana provisioning**

`datasources/prometheus.yml`:

```yaml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
```

`dashboards/dashboards.yml`:

```yaml
apiVersion: 1
providers:
  - name: tracemind
    orgId: 1
    folder: ""
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30
    options:
      path: /var/lib/grafana/dashboards
```

`tracemind-overview.json`:单面板 dashboard,含 `order-service` / `inventory-service` 的 P95 / QPS / 错误率各一个 panel(查询使用 `promql_templates` 同款 PromQL),UID 固定 `tracemind-overview`;实现时用 Grafana dashboard JSON 格式编写并本地校验。

- [ ] **Step 7: 运行确认通过 + 提交**

Run: `cd ai-service && uv run pytest tests/test_observability_config.py -q`
Expected: 3 passed

```bash
git add observability/ ai-service/tests/test_observability_config.py
git commit -m "feat(observability): Collector trace-only / Prometheus 抓取管理端口 / Grafana provisioning 单面板"
```

---

### Task 13: Compose 升级(观测服务 + 网络分区 + 内存预算)

**Files:**
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: `observability/` 配置(Task 12);Java 管理端口 9081/9082(Task 1)。
- Produces: 4 个观测服务(固定版本);四网络分区;`mem_limit` 与 JVM 限制;Grafana `observability-ui` profile。

- [ ] **Step 1: 固定版本常量(示例;实现时用当前稳定版本 + 验证 Digest)**

```yaml
x-observability-images: &obs-images
  otel-collector: otel/opentelemetry-collector:0.119.0
  jaeger: jaegertracing/jaeger:2.6.0
  prometheus: prom/prometheus:v2.55.1
  grafana: grafana/grafana:11.5.0
```

- [ ] **Step 2: 网络分区**

```yaml
networks:
  tracemind:          # 保留为 app-net(业务)
  metrics-scrape-net: { driver: bridge }
  trace-ingest-net:   { driver: bridge }
  observability-query-net: { driver: bridge }
```

服务归属:`order-service` / `inventory-service` 加入 `tracemind` + `metrics-scrape-net` + `trace-ingest-net`;`prometheus` 加入 `metrics-scrape-net` + `observability-query-net`;`otel-collector` 加入 `trace-ingest-net`;`jaeger` 加入 `trace-ingest-net` + `observability-query-net`;`ai-service` 加入 `tracemind` + `observability-query-net`;`grafana` 仅 `observability-query-net`;`web/loadgen/mysql/qdrant` 仅 `tracemind`。

- [ ] **Step 3: 观测服务定义(固定版本 + mem_limit + UI 回环)**

```yaml
  otel-collector:
    image: otel/opentelemetry-collector:0.119.0
    command: ["--config=/etc/otel/config.yaml"]
    volumes:
      - ./observability/otel-collector.yaml:/etc/otel/config.yaml:ro
    mem_limit: 192m
    networks: [trace-ingest-net]

  jaeger:
    image: jaegertracing/jaeger:2.6.0
    environment:
      SPAN_STORAGE_TYPE: memory
      MEMORY_MAX_TRACES: "10000"
      COLLECTOR_OTLP_ENABLED: "true"
    mem_limit: 512m
    ports:
      - "127.0.0.1:16686:16686"   # UI 仅宿主机回环;gRPC QueryService 16685 只入 observability-query-net
    networks: [trace-ingest-net, observability-query-net]

  prometheus:
    image: prom/prometheus:v2.55.1
    command:
      - --config.file=/etc/prometheus/prometheus.yml
      - --storage.tsdb.retention.time=6h
      - --storage.tsdb.path=/prometheus
    volumes:
      - ./observability/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus-data:/prometheus
    mem_limit: 384m
    networks: [metrics-scrape-net, observability-query-net]

  grafana:
    image: grafana/grafana:11.5.0
    profiles: ["observability-ui"]
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD:-admin}
      GF_USERS_ALLOW_SIGN_UP: "false"
    volumes:
      - ./observability/grafana/provisioning:/etc/grafana/provisioning:ro
      - ./observability/grafana/dashboards:/var/lib/grafana/dashboards:ro
    mem_limit: 256m
    ports: ["127.0.0.1:3000:3000"]
    networks: [observability-query-net]
```

- [ ] **Step 4: Java 服务管理端口只入 metrics-scrape-net(不映射宿主机)**

`order-service` / `inventory-service` 增加:

```yaml
    expose:
      - "9081"     # order;inventory 用 9082
    networks: [tracemind, metrics-scrape-net, trace-ingest-net]
```

并保持业务端口 `ports` 映射不变。

- [ ] **Step 5: JVM 与资源**

Java 服务 `environment` 加 `JAVA_TOOL_OPTIONS=-Xms128m -Xmx256m -XX:MaxMetaspaceSize=128m`(与 Task 2 的 javaagent 合并:`JAVA_TOOL_OPTIONS="-javaagent:... -Xmx256m ..."`,或分开:`JAVA_OPTS` 管内存、`JAVA_TOOL_OPTIONS` 管 agent;实现时二选一并在 Dockerfile 固化);`mem_limit: 512m`。

- [ ] **Step 6: 本地校验 compose 语法**

Run: `python -c "import yaml; yaml.safe_load(open('docker-compose.yml', encoding='utf-8')); print('compose YAML OK')"`

- [ ] **Step 7: 提交**

```bash
git add docker-compose.yml
git commit -m "feat(compose): 观测服务(固定版本)+ 四网络分区 + 管理端口仅 metrics-scrape-net + 内存预算 + Grafana profile"
```

---

### Task 14: 回归适配(Backend 强制校验 + 报告字段)

**Files:**
- Modify: `scripts/run_regression.py`(STAGES_FULL 启动校验 + 报告字段)
- Modify: `scripts/report_utils.py`(报告记录 backend/agent 版本)
- Test: 无(脚本;Task 15 VM 执行)

**Interfaces:**
- Consumes: `settings.metrics_backend/trace_backend`;`observability/` 配置。
- Produces: `--tier full` 强制 `metrics_backend=prometheus` + `trace_backend=jaeger` + `internal_observation_enabled=false` + `eval_mode=false`,任一不满足拒绝执行;报告记录版本/backend/normalizationRuleVersion。

- [ ] **Step 1: run_regression.py 增加启动校验**

`STAGES_FULL` 前置阶段(在 pytest 之前):

```python
("backend_check", ".",
 ["python", "-c",
  "import os; "
  "assert os.environ.get('TRACEMIND_METRICS_BACKEND') == 'prometheus', 'full 必须 prometheus'; "
  "assert os.environ.get('TRACEMIND_TRACE_BACKEND') == 'jaeger', 'full 必须 jaeger'; "
  "print('backend OK')"]),
```

- [ ] **Step 2: report_utils 记录观测元数据**

`report_utils.collect_metadata()` 增加:`metrics_backend / trace_backend / otel_java_agent_version / otel_collector_version / jaeger_version / prometheus_version / grafana_version / normalization_rule_version / prometheus_target_status / jaeger_canary_trace_id`(键存在则记录,缺失用 "n/a")。

- [ ] **Step 3: 提交**

```bash
git add scripts/run_regression.py scripts/report_utils.py
git commit -m "feat(regression): full 档强制 prometheus+jaeger 后端校验;报告记录观测版本与 backend"
```

---

### Task 15: E2E 验收(verify-m14 + Jaeger gRPC 接入 + 双证据断言)

**Files:**
- Modify: `ai-service/app/services/jaeger_client.py`(接入真实 gRPC QueryService)
- Create: `scripts/verify-m14.py`
- Modify: `scripts/loadgen.py`(SCN-002 并发约束参数)
- Test: `ai-service/tests/test_jaeger_client.py`(真实桩可选)

**Interfaces:**
- Consumes: 全部前置 Task;VM 部署(Task 13)。
- Produces: `verify-m14.py`(正常路径 E2E:SCN-001/SCN-002 各 3 轮 + 双证据 + 防伪断言)。

- [ ] **Step 1: jaeger_client 接入真实 gRPC(或固定版本 HTTP JSON API)**

若用 gRPC QueryService(`jaeger:16685`,稳定 API),加依赖 `grpcio` 并实现:

```python
def _query_grpc(endpoint: str, request: dict) -> dict:
    # 用 grpc 调 jaeger.api_v2.QueryService(GetTrace/FindTraces);
    # 或用固定 Jaeger 版本 + UI HTTP JSON API(http://<endpoint>/api/traces...)封装于此。
    # 实现时二选一;两者都要求固定 Jaeger 版本并处理 TRACE_BACKEND_UNAVAILABLE。
    raise NotImplementedError
```

实现后 `test_jaeger_client.py` 保持 monkeypatch 通过;Task 15 VM 上跑真实查询验证。

- [ ] **Step 2: loadgen 增加 SCN-002 并发约束**

`scripts/loadgen.py` 增加环境变量:`LOAD_MAX_IN_FLIGHT`(信号量,默认 1)、`LOAD_TIMEOUT_SECONDS`(urlopen timeout);配合既有 `LOAD_SKU/LOAD_WAREHOUSE/LOAD_DURATION_SECONDS/LOAD_QPS` 形成固定并发模型。

- [ ] **Step 3: verify-m14.py(正常路径 E2E)**

流程(每场景每轮):

```
reset scenario
健康负载(基线)→ create incident(带 affected_service_ref/affected_operation_ref)→ 采集基线
SCN-001:inject(删索引)/ SCN-002:inject(持锁)+ 持续负载(固定并发)
等待:SCN-002 至少 1 条已导出超时 Trace + 至少 1 条实时锁等待(minimum_active_lock_waiters)
start investigation → 轮询 awaiting_approval → approve → verify_recovered
断言:
  - Metrics 证据 sourceBackend=prometheus,observationQueryId 存在
  - Trace 证据 sourceBackend=jaeger,traceId 存在且 Jaeger 可再查
  - x-trace-id(HTTP 响应头)= order/inventory/Jaeger/Evidence 四者一致
  - Trace 含跨服务 HTTP span + inventory JDBC span;无 scenario.inject/lock-holder/SCN-00x
  - Java /internal/observations 返回 404;AI 日志无 /internal/observations 调用
  - SCN-002 双证据:Jaeger 慢 JDBC span + MySQL 实时锁等待;处置后锁消失
finally: reset scenario, stop loadgen, restore observability
```

SCN-001 / SCN-002 各连续 3 轮 PASS。

- [ ] **Step 4: 本地(替身)验证脚本可运行**

Run(本地,fixture backend):`python scripts/verify-m14.py --base http://localhost:8000 --fixture`
Expected: fixture 模式下脚本流程跑通(backend 断言跳过或断言 fixture)。

- [ ] **Step 5: 提交**

```bash
git add ai-service/app/services/jaeger_client.py scripts/verify-m14.py scripts/loadgen.py
git commit -m "feat(e2e): verify-m14 观测验收 — 双证据/trace 一致/防伪断言;Jaeger gRPC 接入;loadgen 并发约束"
```

---

### Task 16: observability-resilience 故障测试 + Grafana Smoke + 最终验收

**Files:**
- Create: `scripts/verify-observability-resilience.py`
- Create: `scripts/verify-grafana-smoke.py`
- Modify: `README.md`(V1.4 章节)

**Interfaces:**
- Consumes: VM 全栈(Task 15)。
- Produces: 故障注入验收与 Grafana 展示验收脚本。

- [ ] **Step 1: verify-observability-resilience.py**

```
停 Prometheus → get_service_metrics 返回 METRICS_BACKEND_UNAVAILABLE,不回退 internal → 恢复 → 等 Target UP
停 Jaeger → get_trace 返回 TRACE_BACKEND_UNAVAILABLE,不回退 internal → 恢复 → 生成新请求 → 查询恢复
停 Collector → Java 业务请求仍可执行;新 Trace 不进入 Jaeger;恢复后重新生成 Trace
每步 try/finally:reset scenario + restore observability + stop loadgen
```

- [ ] **Step 2: verify-grafana-smoke.py**

```
Grafana 数据源 Prometheus Healthy
Dashboard UID tracemind-overview 存在
面板无查询错误(Grafana API /api/dashboards/uid/... + search 校验)
SCN-001/SCN-002 压测期间指标变化可见(两次查询 P95 值不同)
```

- [ ] **Step 3: VM 全量验收(用户环境)**

在 VM 上执行(按记忆中的部署流程:相对路径 put 同步 → `DOCKER_BUILDKIT=0 docker build` 重建 ai/inventory/order → `docker compose up -d` → `docker compose --profile observability-ui up -d`),然后:

```bash
python scripts/verify-m14.py --base http://<vm-host>:8000 --order http://<vm-host>:8081   # 3/3 + 3/3
python scripts/verify-observability-resilience.py --base http://<vm-host>:8000
python scripts/verify-grafana-smoke.py --grafana http://127.0.0.1:3000
python scripts/run_regression.py --tier full
docker inspect --format '{{.Name}} {{.HostConfig.Memory}}' $(docker ps -q)   # mem_limit 生效验证
```

- [ ] **Step 4: README 更新 V1.4**

新增章节:观测架构图、Backend 配置表(`TRACEMIND_METRICS_BACKEND/TRACE_BACKEND/PROMETHEUS_URL/JAEGER_QUERY_ENDPOINT`)、验证命令、VM 部署步骤、Grafana profile、版本历史 V1.4。

- [ ] **Step 5: 提交**

```bash
git add scripts/verify-observability-resilience.py scripts/verify-grafana-smoke.py README.md
git commit -m "feat(observability): 故障注入验收 + Grafana Smoke + README V1.4"
```

---

## Self-Review

- **Spec 覆盖**:§2 架构(信号分治)→ T1/T2;§3 组件职责 → T5/T6/T7;§4 Java → T1/T2/T3;§5 AI → T4~T11;§6 部署 → T12/T13;§7 回归 → T14;§8 验收 → T15/T16;§9 兼容(契约升级/错误码/新鲜度)→ T10/T5/T9。
- **占位符**:jaeger_client 的 `_query_grpc` 为显式标注的"Task 15 接入"占位(Task 15 Step 1 补齐),非计划缺陷;其余步骤均含完整代码。
- **类型一致性**:`get_service_metrics` 输出 `windowStart/windowEnd/latestSampleAt`(T5)与 `compute_eligible_tools` 判定(T9)一致;`trace_service.get_trace(trace_ref, trace_id, incident)`(T7)与 MCP handler(T10)一致;`observation_repo.record_query`(T11)与审计断言一致;Contract Version `2.1.0`(T10)与测试一致。
- **遗留风险**:PromQL 模板与 Jaeger gRPC 桩的真实输出需在 VM(T15/T16)回填冻结;本地(fixture)与 VM(prometheus+jaeger)双环境均覆盖。
