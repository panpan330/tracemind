# V1.9 设计:Agent 长期记忆 + 上下文压缩

日期:2026-08-14
版本:V1.9
前置:V1.8(Agent 运行观测面板 + 量化评测报告)

## 1. 背景与目标

V1.8 补齐了"Agent 可观测 + 可量化"两块。对照"AI Agent / 大模型应用工程师"岗位,仍有两个高频缺口:

1. **Agent 记忆**:诊断结束后,当前系统不沉淀任何经验——每次诊断都是"从零开始",静态 runbook 不会因真实诊断而增长。面试问"Agent 怎么记忆/复用经验"答不上来。
2. **上下文管理**:`write_report` 把完整 evidence content(每个都是大 JSON)塞进 prompt,证据多时 token 膨胀;缺乏对长上下文的控制手段。

本版本目标(两个,相对独立):

1. **长期记忆**:诊断成功后把"故障指纹 → 根因 → 修复"向量化沉淀到 qdrant,下次相似故障语义检索复用,注入假设生成 prompt。
2. **上下文压缩**:`EvidenceSummarizer` 在证据超过阈值时摘要化旧证据,控制 report prompt 长度。

## 2. 现状盘点

- **RAG 基建已齐**:`Embedder`(bailian text-embedding-v4,1024 维)、`RunbookStore`(qdrant REST 客户端)、`Retriever`(冷却退避 + 降级)、`retrieval_repo`(审计)。
- **qdrant 未部署**:compose 无 qdrant 服务(仅 `TRACEMIND_QDRANT_URL` 引用),V1.6 后移出。记忆需重新部署。
- **诊断终态节点**:graph 链 `... → execute_fix → verify_recovery → report → END`;`report` 是最后一个节点,案例沉淀挂这里。
- **prompt 现状**:`_build_collect_prompt`/`select_tool` 的 evidence 已精简(`id + passed`,不含 content);`write_report` 塞完整 content(唯一 token 膨胀点)。
- **runbook 检索**:`hypothesize` 里 `_rag_context` 用 `tracemind_runbook_current` collection。

## 3. 块 1:长期记忆(案例沉淀 + 语义检索)

### 3.1 qdrant 部署

compose 新增 `qdrant` 服务:
- 镜像 `qdrant/qdrant:v1.15.2`(或与 runbook 索引历史一致的具体版本)。
- 端口 6333(HTTP)+ 6334(gRPC,不映射)。
- volume `qdrant-data`。
- 内存预算 ≤ 512MB(案例 collection 规模小)。
- 复用 `tracemind` 默认网络(ai-service 可达)。

### 3.2 案例 collection

新 collection `tracemind_case_memory`(与 `tracemind_runbook_current` 分离):
- 维度 1024,distance Cosine(与 runbook 一致)。
- 每条 point:
  - **vector**:案例文本的 embedding(见 3.3)。
  - **payload**:`root_cause_code`、`fault_category`(SCN-001/SCN-002)、`recovered`(bool)、`ts`(ISO)、`run_id`。

### 3.3 案例沉淀(report 节点)

新增 `app/agent/memory.py`:`record_case(state) -> None`。在 `report` 节点、**仅当终态为 recovered** 时调用(失败案例不沉淀,YAGNI)。

案例文本(向量化对象):

```
故障描述:{description}
证据结论:{每条 evidence 的 id+passed+关键指标}
根因:{root_cause_code} {root_cause}
修复动作:{fix 类型}
恢复结果:recovered
```

- 失败/embedding 失败/沉淀异常**不阻塞诊断**(catch + log)。
- 幂等:同 run 重复 record 由 qdrant point id(`case-{run_id}`)覆盖。

### 3.4 检索复用(hypothesize 节点)

`_rag_context` 扩展:runbook 检索之外,增加**案例记忆检索**:
- 复用 `RunbookStore`(新实例指向 `tracemind_case_memory`)+ `Retriever` 模式。
- `search(description, top_k=3)`。
- 注入 hypothesize prompt,标签 `<case_reference>`(与 runbook 的 `<knowledge_reference>` 并列),明确"历史案例参考,非指令"。
- 检索失败降级(冷却退避),不阻塞。

### 3.5 复用与隔离

- 复用 `Embedder`、qdrant REST 客户端、Retriever 冷却退避模式。
- 案例沉淀用独立写入(qdrant write key);检索用 read key(沿用现有 `qdrant_read_api_key`/`qdrant_write_api_key` 分离)。

## 4. 块 2:上下文压缩(证据摘要)

### 4.1 EvidenceSummarizer

新增 `app/agent/evidence_summary.py`:`summarize(evidence: list[dict], max_keep: int = 8) -> list[dict]`。

- `len(evidence) <= max_keep`:原样返回。
- `len(evidence) > max_keep`:最旧的 evidence 的 `content` 压缩成一行"关键结论",保留最近 `max_keep` 条的完整 content。

关键指标提取(按证据类型):
- metrics → `p95={p95Ms}`
- lock → `wait={wait_duration_ms}ms`
- trace → `耗时集中阶段={...}`
- index → `索引{存在/缺失}`
- 其余 → `passed={passed}`

### 4.2 应用点

- `write_report` 的 facts 构造:evidence 用 `summarize` 后的(控制 report prompt 长度)。
- `_build_collect_prompt` / `select_tool`:**保持现状**(已精简,不改)。

### 4.3 验证

单元测试覆盖:阈值行为(≤8 原样、>8 摘要)、关键指标提取、20 条证据场景断言 report prompt 长度不超限。

## 5. 范围边界(YAGNI)

- 只沉淀 **recovered** 案例,失败案例不沉淀。
- 案例检索 top_k=3,不做重排序/多样性。
- 上下文压缩只作用于 `write_report`,不改 `select_tool`/`_build_collect_prompt`(它们已精简)。
- 不做案例过期/淘汰策略(qdrant 长期累积,留后续)。
- 不做多模型 embedding 对比(沿用 bailian text-embedding-v4)。
- 沿用 V1.6 决定:不做 CI,验证手动。

## 6. 验收标准

1. qdrant 部署后,真实模型跑 SCN-001 一轮,断言 `tracemind_case_memory` 出现 1 条 recovered 案例。
2. 第二次同场景诊断,hypothesize 的 prompt 含案例检索结果(可经 model_call 的 prompt 审计或日志验证)。
3. `EvidenceSummarizer` 单测通过(阈值 + 指标提取)。
4. `write_report` 在 20 条证据场景下 prompt 长度受控。
5. 回归:ai-service 全量 pytest + 离线评测(fake)通过;记忆检索失败不阻塞诊断。
