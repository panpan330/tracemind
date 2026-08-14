# V1.8 设计:Agent 运行观测面板 + 量化评测报告

日期:2026-08-14
版本:V1.8
前置:V1.7(MCP Streamable HTTP 远程传输与服务化)

## 1. 背景与目标

V1.7 收尾后,项目核心功能(诊断闭环 / MCP 服务化 / 可观测性 / 回放 / 离线评测)均已落地并验证。但对照"AI Agent / 大模型应用工程师"岗位的面试需求,存在一个明确短板:**没有"Agent 可观测 + 可量化"的证据**。

面试高频问题"你怎么知道 Agent 在哪一步做错了 / 你怎么评估 Agent 的好坏",当前系统只能零散回答,没有一套可演示的观测面板和一份量化报告。

本版本目标(两个,相对独立):

1. **Agent Run 观测面板**:把一次运行的完整轨迹(决策链 + LLM 调用 + 工具调用 + RAG 检索)聚合、关联、可视化,并自动标注卡点(异常)。纯工程,不耗模型额度。
2. **量化评测报告**:真实模型(real_strict)跑 SCN-001 + SCN-002 各 3 轮,产出"成功率 / 耗时 / token / 工具调用 / 失败原因分布"的 markdown 报告。耗百炼额度(约 5 分钟)。

## 2. 现状盘点(关键:底层数据已齐全,缺的是聚合与呈现)

V1.5/V1.7 已落库的观测数据(散落在 control 库各表,无读取聚合端):

| 数据 | 表 | 已有字段 |
|---|---|---|
| LLM 调用 | `model_call` | node / mode / provider / model / model_snapshot / prompt_version / prompt_hash / tool_schema_version / logical_call_id / attempts_json(重试) / finish_reason / structured_output_valid / tool_call_count / fallback_executor(兜底) / input_snapshot_json / latency_ms / input_tokens / output_tokens / status / error_code / degraded / git_commit_sha / knowledge_chunk_ids |
| MCP 工具调用 | `tool_call` + `tool_call_attempt` | tool_name / transport / purpose / attempt_no / client_attempt_id / mcp_request_id / outcome / error_code / retryable / latency_ms / protocol_version / server_instance_id / trace_id |
| RAG 检索 | `retrieval_record` | collection_alias / embedding_model / candidate_top_k / final_chunk_ids / scores / latency_ms / status / degraded |
| 决策链 | replay steps | stepState / stepOutcome / decisionSummary(selectedTool) / actualDurationMs / missingParts |

**读取能力现状**:
- `tool_repo.list_tool_calls(incident_id)` 已有读取方法。
- `model_call` / `retrieval_record` / `tool_call_attempt` 当前只有 `insert`,**无按 run 读取的聚合方法**——本版本新增。

**结论**:增量不是"造数据",而是:① 新增读取聚合(只读端点 + repo 读取方法);② 卡点诊断;③ 前端可视化;④ 量化报告。

## 3. 块 1:后端——Run 观测聚合 API + 卡点诊断

### 3.1 只读端点

```
GET /api/incidents/{incident_id}/runs/{run_id}/observation
```

新增到 `app/api/runs.py`(或独立 `app/api/observation.py` 并挂载)。只读,不触碰写路径。

返回结构:

```json
{
  "run": { "runId": 1, "status": "needs_human", "terminationReason": "decision_budget_exhausted",
           "startedAt": "...", "durationMs": 45000 },
  "timeline": [
    {
      "phase": "hypothesize | collect_evidence | diagnose | fix | recovery",
      "stepId": "h1", "startedAt": "...", "durationMs": 3200,
      "llm": { "node": "hypothesize", "model": "qwen3.7-plus", "promptVersion": "v13",
               "inputTokens": 512, "outputTokens": 128, "latencyMs": 1800,
               "retries": 1, "finishReason": "stop", "structuredOutputValid": true,
               "fallbackTriggered": false, "knowledgeChunkIds": ["runbook-mysql-lock-wait"] },
      "tools": [ { "name": "get_lock_waiters", "transport": "mcp_streamable_http",
                   "attemptNo": 1, "outcome": "completed", "errorCode": null, "latencyMs": 210 } ],
      "retrieval": { "hitDocIds": ["runbook-mysql-lock-wait"], "scores": [0.91],
                     "latencyMs": 45, "degraded": false }
    }
  ],
  "diagnosis": {
    "terminationReason": "decision_budget_exhausted",
    "bottleneckStep": "collect_evidence",
    "anomalies": [
      { "type": "duplicate_tool_call", "stepId": "e3", "detail": "get_trace 连续选择 2 次" },
      { "type": "no_progress", "stepId": "e4", "detail": "连续 2 轮无新 evidence" }
    ]
  }
}
```

### 3.2 卡点诊断(anomalies 类型枚举)

`diagnosis` 由聚合函数自动归纳,异常类型固定为:

| type | 触发条件 |
|---|---|
| `duplicate_tool_call` | 同一 tool_name 在同一 phase 被 LLM 选择 ≥2 次 |
| `no_progress` | 连续 ≥2 轮无新 evidence 产出 |
| `decision_budget_exhausted` | 运行终止原因为决策预算耗尽 |
| `retry` | 任一 LLM 调用 attempts_json 重试次数 >0 |
| `fallback_triggered` | 任一 LLM 调用 fallback_executor 非空(兜底触发) |
| `structured_output_invalid` | 任一 LLM 调用 structured_output_valid=false |
| `degraded_rag` | 任一检索记录 degraded=true |
| `tool_failed` | 任一 tool_call_attempt outcome 为 failed/error/outcome_unknown |

`bottleneckStep` = 累计时长最长的 phase。

### 3.3 实现

- `app/repositories/model_call_repo.py` 新增 `list_by_run(agent_run_id) -> list[dict]`。
- `app/repositories/retrieval_repo.py` 新增 `list_by_run(agent_run_id) -> list[dict]`。
- `app/repositories/tool_repo.py` 新增 `list_attempts_by_run(agent_run_id) -> list[dict]`(读 tool_call_attempt)。
- 新增 `app/services/observation_service.py`:`build_run_observation(incident_id, run_id) -> dict`,负责按时间线聚合 + 诊断。
- 新增 API 端点(只读),复用既有路由。

## 4. 块 2:前端——Run 观测面板

新增视图 `web/src/views/RunObservationView.vue`,路由挂在 IncidentDetail 下(与 ReplayView 并列)。

### 4.1 页面结构(自上而下)

1. **诊断摘要卡片**:终态徽章(recovered ✅ / needs_human ⚠️)+ 一句话归因 + 异常计数徽章(如 "2 次重复工具" "1 次兜底触发" "RAG 降级")。
2. **时间线主体**:每个 phase 一个节点,横向标注时长,纵向可展开。
   - 展开后三栏:LLM(model / promptVersion / tokens / 耗时 / 重试 / 兜底标记)、工具(name / transport / attemptNo / outcome / 耗时,失败或重试标红)、检索(命中 doc id + score + 耗时,degraded 标黄)。
3. **异常高亮**:卡点节点红框,点开展示 `diagnosis.anomalies` 详情。

### 4.2 技术

- 纯展示组件,复用 Element Plus + `@/api/client`(新增 `fetchRunObservation` 函数),不引入新依赖。
- 单测覆盖:摘要渲染、时间线渲染、异常标注三块(`RunObservationView.test.ts`)。

## 5. 块 3:量化评测报告

新增脚本 `scripts/eval_agent_report.py`:

1. 调 `verify-m14 --rounds 3`(SCN-001 + SCN-002 各 3 轮,真实模型 `real_strict` + prometheus/jaeger 后端)。
2. 每轮结束后从块 1 的 `/observation` 端点拉数据,汇总:
   - **成功率**:每场景 recovered / needs_human 比例。
   - **耗时**:平均 / 中位诊断时长。
   - **token**:平均 input / output token(累计 LLM 成本)。
   - **工具调用**:平均每轮工具数、失败率。
   - **卡点分布**:各 anomaly 类型出现次数。
   - **逐轮明细表**:每轮一行(耗时 / token / 工具数 / 终态)。
3. 落盘 markdown 到 `reports/evals/agent-eval-YYYYMMDD-HHMMSS.md`。

产物示例:

```markdown
# TraceMind 真实模型评测报告(real_strict)
- SCN-001 缺索引:3/3 recovered,平均 28s,平均 3.2 次工具调用
- SCN-002 锁阻塞:3/3 recovered,平均 24s,平均 4.0 次工具调用
- 成功率 6/6(100%),零卡点

| 轮次 | 场景 | 终态 | 耗时 | input tokens | output tokens | 工具调用 |
|---|---|---|---|---|---|---|
| 1 | SCN-001 | recovered | 30s | 1200 | 300 | 3 |
| ... |
```

## 6. 范围边界(YAGNI)

- 不做真实用户/生产流量的接入(本项目故障为注入)。
- 不做 CI 集成(沿用 V1.6 决定:GitHub 纯远程仓库,验证手动)。
- 不做浏览器 Playwright E2E(沿用 V1.6 决定)。
- 不做多 Agent 协作、不做 Agent 安全护栏框架(留后续)。
- 量化评测规模限定为 SCN-001 + SCN-002 各 3 轮(用户拍板;额度不足时停下告知用户)。
- 观测面板不做实时流式刷新(按需请求一次即可),不做跨 Run 对比视图(留后续)。

## 7. 验收标准

1. 后端:`GET /api/incidents/{id}/runs/{run_id}/observation` 返回含 timeline + diagnosis,单元测试覆盖聚合与 8 类 anomaly 触发条件。
2. 前端:RunObservationView 渲染摘要 / 时间线 / 异常高亮,typecheck + 单测通过。
3. 报告:真实模型 SCN-001 + SCN-002 各 3 轮跑通,`reports/evals/` 生成含成功率 / 耗时 / token / 卡点分布的 markdown。
4. 回归:ai-service 全量 pytest 通过;离线评测(fake)不回归。
