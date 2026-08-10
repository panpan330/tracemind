# TraceMind

面向微服务系统的 AI 故障诊断与安全处置平台(求职作品集项目)。

## 当前进度:M1 Java 故障目标系统 ✅

已完成:两个独立 Spring Boot 服务(order-service / inventory-service)、真实 MySQL(双 Schema + 四账号隔离)、traceId 跨服务关联与阶段耗时观测、Micrometer 指标汇总、SCN-001 故障注入/重置/审计、负载发生器与验收脚本。

## 环境要求

- JDK 21、Maven 3.9+
- MySQL 8(本机 `localhost:3306`)
- Python 3.12+(仅数据生成/负载脚本,依赖 `pymysql`)

## 本地启动(开发模式)

```powershell
# 1. 初始化数据库(幂等;需先设置 root 密码)
$env:MYSQL_ROOT_PASSWORD = "你的root密码"
powershell -ExecutionPolicy Bypass -File scripts/init-database.ps1

# 2. 灌入压测数据(50 万行,可配 INVENTORY_ROWS)
powershell -ExecutionPolicy Bypass -File scripts/generate-data.ps1 -Rows 500000

# 3. 启动 inventory-service(8082,DEMO_MODE 开启场景控制)
$env:DEMO_MODE = "true"; $env:DEMO_KEY = "demo-secret-2026"
cd java\inventory-service; mvn spring-boot:run

# 4. 启动 order-service(8081)
cd java\order-service; mvn spring-boot:run

# 5. 产生负载(可选)
powershell -ExecutionPolicy Bypass -File scripts/run-load.ps1 -Seconds 60 -Qps 20
```

## 关键端点

| 端点 | 说明 |
|---|---|
| `POST /api/orders/{id}/check-stock` | 订单校验库存(跨服务调用) |
| `GET /api/inventory?skuId=&warehouseId=` | 库存查询(目标 SQL) |
| `GET /internal/observations/traces/{traceId}` | 阶段耗时观测(404=TRACE_NOT_FOUND) |
| `GET /internal/observations/metrics?window_seconds=300` | P95/QPS/错误率/最慢 trace |
| `POST /internal/scenarios/SCN-001/inject` | 注入故障(drop 联合索引,需 DEMO_KEY) |
| `POST /internal/scenarios/SCN-001/reset` | 实验环境重置(重建索引) |
| `GET /internal/scenarios/SCN-001/status` | 场景状态 |

## M1 验收

```powershell
powershell -ExecutionPolicy Bypass -File scripts/verify-m1.ps1 -DemoKey "demo-secret-2026"
```

预期:健康态 EXPLAIN `type=ref`/rows=1/P95≈2ms;故障态 `type=ALL`/rows≈50 万/P95 明显升高。

## 目录结构

```
java/            Maven 多模块(common + order-service + inventory-service)
scripts/         数据库初始化、数据生成、负载、验收脚本
scripts/sql/     幂等 SQL(建库/账号/DDL)
docs/            设计文档与实施计划
```
