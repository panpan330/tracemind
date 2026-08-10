# TraceMind 架构文档

## 1. 组件与端口

| 组件 | 技术 | 端口 | 职责 |
|---|---|---|---|
| web | Vue 3 + TS + Vite + Element Plus | 8080(nginx) | 工作台:场景控制、事件列表、调查详情(SSE 实时)、审批、复盘报告 |
| ai-service | FastAPI + LangGraph + SQLAlchemy | 8000 | Incident 管理、Agent 状态机、受控工具层、审计与 SSE |
| order-service | Spring Boot 3.3 / Java 21 | 8081 | 订单服务,调用 inventory 查库存,携带 traceId |
| inventory-service | Spring Boot 3.3 / Java 21 | 8082 | 库存查询(目标故障载体),SCN-001 故障注入/重置 |
| mysql | MySQL 8.0 | 3306 | business 库(真实数据)+ control 库(审计/事件)+ performance_schema |

## 2. LangGraph 状态机

```
┌─────────┐   ┌────────────┐   ┌──────────────────┐   ┌─────────┐
│ ingest  │──▶│ hypothesize │──▶│ collect_evidence │──▶│ diagnose │
└─────────┘   └────────────┘   │   (循环 ≤5 轮)   │   └────┬────┘
                               └──────────────────┘        │ E1~E5 全确认
                                                            ▼
┌─────────┐   ┌────────────┐   ┌──────────────────┐   ┌─────────┐
│ report  │◀──│ verify_     │◀──│ execute_fix      │◀──│ human_  │◀──┐
│(终态复盘)│   │ recovery   │   │ (唯一写路径,六项 │   │ approval│   │ Command
└─────────┘   └────────────┘   │  校验+幂等)      │   │(interrupt)│  │(resume)
                               └──────────────────┘   └─────────┘   │
      ▲                                    ▲                        │
      │ 未恢复/证据不足 → needs_human      └─ 拒绝/过期 ──────────────┘
      └───────── 恢复 → recovered
```

- 节点:9 个,边条件见 `ai-service/app/agent/graph.py`。
- `human_approval` 使用 `interrupt()` 挂起,审批接口以相同 `thread_id` 调 `Command(resume=...)` 恢复。
- 状态:`IncidentState`(Pydantic)含 incident 元信息、hypotheses、evidence、fix_proposal、approval、recovery、report。
- 检查点:SqliteSaver 持久化;服务重启后从最后 Checkpoint 恢复未完成任务。

## 3. 根因判定闸门(E1~E5)

| 证据 | 来源工具 | 判定内容 |
|---|---|---|
| E1 | `get_service_metrics` | 目标服务 P95 相对健康基线异常 |
| E2 | `get_trace` | 代表性慢请求耗时集中在数据库阶段 |
| E3 | `list_expensive_query_digests` | 目标 SQL 执行次数/总耗时/扫描行数增量异常 |
| E4 | `get_query_plan` | `EXPLAIN FORMAT=JSON` 显示全表扫描或未命中索引 |
| E5 | `get_index_info` | `(sku_id, warehouse_id)` 联合索引缺失 |

五项齐备 → 根因 confirmed;缺证据且预算充足 → 继续收集;预算耗尽 → `needs_human`。

## 4. 受控工具层

**LLM 可调用(只读调查,5 个)**:`get_service_metrics`、`get_trace`、`list_expensive_query_digests`、`get_query_plan`、`get_index_info`

**仅确定性节点调用(2 个)**:`execute_fix`(预定义 DDL + 六项校验 + 幂等 + no_op)、`verify_recovery`(三批固定探测,相对健康基线判定)

参数全部 Pydantic 白名单校验;Agent 无任意 SQL/Shell/表名能力。

## 5. 数据库与权限隔离

- **tracemind_business**:inventory(50 万行,idx_sku_warehouse)、orders、order_item。
- **tracemind_control**:13 张审计/事件表(incident、agent_run、hypothesis、evidence、tool_call、approval、fix_proposal、fix_execution、recovery_check、postmortem、incident_event 等)。
- 账号:`app_business`(业务读写+INDEX)、`tracemind_control_app`(控制库 CRUD)、`ai_investigator`(只读+performance_schema)、`fix_executor`(仅 INDEX,execute_fix 专用)。
- Python 三连接池:`control` / `readonly` / `executor`,职责互斥。

## 6. SSE 事件流

- 事件持久化到 `incident_event`(incident_id, sequence 唯一),支持 `Last-Event-ID` 断线补发。
- 事件类型:`snapshot`、`status_changed`、`tool_call`、`hypothesis`、`evidence`、`approval`、`incident_finished`(终态关闭)、`heartbeat`。
- 领域状态修改与事件写入尽量同一事务;前端按 event_id 去重。

## 7. 故障场景 SCN-001(可重复注入/重置)

- 注入:删除 `idx_sku_warehouse(sku_id, warehouse_id)` → 库存查询退化为全表扫描(50 万行)。
- 重置:重建索引 + 清理 Incident/观测缓存/场景状态(保留压测数据)。
- 验证效果:健康态 `EXPLAIN type=ref rows=1` P95 ≈ 2ms;故障态 `type=ALL rows≈50万` P95 ≈ 120ms(约 60 倍)。

## 8. 本地开发 vs Docker 交付

| 项 | 本地开发 | Docker Compose |
|---|---|---|
| MySQL | Windows 本机 3306 | 容器 3306 |
| AI 服务 | `TRACEMIND_*` 指向 localhost | 指向 compose 服务名 |
| 前端 | Vite dev :5173(代理 /api) | nginx :8080(代理 /api,SSE 关缓冲) |
| 数据 | `scripts/init-database.ps1` + `generate-data.ps1` | initdb 自动 + seed 服务(幂等) |
