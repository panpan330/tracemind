# V1.10 设计:Agent 反思与自我改进(含失败案例负样本记忆)

日期:2026-08-14
版本:V1.10
前置:V1.9(Agent 长期记忆 qdrant 案例沉淀 + 上下文压缩)

## 1. 背景与目标

V1.9 让 Agent 拥有了长期记忆(诊断成功案例向量化沉淀 + 语义检索复用)与上下文压缩。对照"AI Agent / 大模型应用工程师"岗位,仍缺一块面试高频能力:**Agent 的自我改进(reflection)**。

当前行为:修复后恢复验证失败(`verify_recovery` 未 recovered)时,**无条件走向 report**,不做任何复盘与再尝试——Agent 失败一次就放弃,和真实工程师"失败→复盘→调整→再试"的行为不符。

本版本目标(两个,同一闭环):

1. **反思重试**:修复失败后,`reflect` 节点复盘证据链,生成修正假设与调整策略,最多 N 轮(3 轮)重试;用尽仍失败则转 needs_human。
2. **失败案例负样本记忆**:反思用尽仍未恢复的案例沉淀到 qdrant(标 `recovered=False`),下次相似故障检索时作为"避坑参考",避免重复失败路径。

## 2. 现状盘点

- **graph 线性链**:`ingest → hypothesize → collect_evidence → diagnose → propose_fix → human_approval → execute_fix → verify_recovery → report → END`;`verify_recovery → report` 为无条件边。
- **失败机制已成熟**:`needs_human` + `termination_reason`(no_progress / budget_exhausted / llm_unavailable / invalid_tool_decision / duplicate_tool_call 等),`diagnose`/`human_approval` 已有条件边。
- **V1.9 记忆基建**:`memory.py` 的 `record_case` 仅沉淀 `status == "recovered"`;`_case_references` 检索注入 `<case_reference>`(top_k=3,text 前 300 字符);复用 `RunbookStore`/`Embedder`/`Retriever`。
- **上下文压缩**:`evidence_summary.py` EvidenceSummarizer 可复用(反思 prompt 证据先摘要)。
- **state 已有字段**:`termination_reason`、`decision_attempt_count`(budget 计数思路可复用)。

## 3. 块 1:反思节点 + 重试循环

### 3.1 图结构

```
verify_recovery ──recovered──▶ report ──▶ END
      │
      └─not_recovered─▶ reflect(反思) ──修正策略──▶ hypothesize(重试)
                          │
                          └─反思次数≥3 或 LLM 不可用─▶ needs_human ─▶ report
```

- `verify_recovery` 改为条件边:`{"recovered": "report", "not_recovered": "reflect"}`
- `reflect` 条件边:`{"retry": "hypothesize", "give_up": "report"}`(经 needs_human 状态)

### 3.2 新增 reflect 节点(nodes.py)

输入:完整证据链(经 EvidenceSummarizer 摘要)+ 已执行修复 + 恢复验证结果 + 失败原因。

LLM 结构化输出(4 字段):
- `root_cause_revisit`:对根因判断的修正/确认
- `evidence_gap`:现有证据缺口(还需要什么证据)
- `new_hypothesis`:修正后的假设
- `adjust_strategy`:策略调整说明

每轮追加写 `reflection_log`:`{attempt_no, reason, new_hypothesis, strategy_change}`。

失败处理:反思本身失败(LLM 不可用)→ 直接 needs_human,不阻塞、不崩溃。

### 3.3 循环控制

- state 新增 `reflection_log: list` + `reflection_count: int`
- `reflection_count >= 3` → 转 needs_human,`termination_reason = "reflection_exhausted"`
- 复用现有 budget 思路防死循环

### 3.4 与现有机制关系

- 复用 `termination_reason` 机制记录反思放弃原因
- 复用 `evidence_summary.py` 控制反思 prompt 长度
- 不改变 human_approval rejected 路径(那是用户决策,不触发反思)

## 4. 块 2:失败案例沉淀 + 避坑检索

### 4.1 沉淀时机与范围(memory.py)

- `record_case` 从"仅 recovered"改为**两种都沉淀**,payload 加 `recovered: bool`:
  - `recovered=True`:正常成功案例(现状保留)
  - `recovered=False`:**仅反思循环用尽仍未恢复时**沉淀(非每次 needs_human 都沉淀,避免垃圾负样本;human_approval rejected、llm_unavailable 等非反思失败不沉淀)
- `_case_text` 对失败案例追加"失败原因 + 尝试过的路径",供检索时区分
- 失败案例 doc_id 前缀:`case-{run_id}-fail`

### 4.2 避坑检索(_case_references 增强)

- 检索结果带 `recovered` 标记,注入 prompt 时明确标注:
  ```
  <case_reference id="..." recovered="false" title="失败案例(避坑)">
    曾尝试 {修复路径},失败原因:{原因}
  </case_reference>
  ```
- hypothesize prompt 加指令:**recovered=false 的案例仅作"避坑参考",不要重复其失败路径**
- top_k 仍 3,不增加检索量

### 4.3 复用与边界

- 复用现有 `RunbookStore`/`Embedder`/`Retriever`,零新增依赖
- 沉淀/检索失败不阻塞诊断(catch + log + 降级,沿用 V1.9)
- 注意:失败案例也走 text-embedding-v4,会多耗少量百炼额度

## 5. 测试与验收

### 5.1 单元测试(TDD)

新建 `tests/test_reflection.py`,扩展 `tests/test_memory.py`:

1. 反思节点:修复失败 state → 结构化反思 4 字段齐全;LLM 不可用 → needs_human 不崩溃
2. 循环控制:reflection_count ≥ 3 → needs_human(`reflection_exhausted`),不死循环
3. reflection_log 累积:多轮重试每轮追加,report 可读完整链
4. 失败案例沉淀:recovered=False 的 payload 带标记 + 失败原因;`_case_text` 含避坑信息
5. 避坑检索:recovered=false 命中输出 `recovered="false"` 标注 + 指令
6. 边界:非反思失败(如 human_approval rejected)不沉淀失败案例;沉淀/检索异常不阻塞诊断

### 5.2 回归

- 后端全量 pytest(当前 379 passed,无回归)
- 前端不动

### 5.3 E2E 验收(VM 真实模型,耗百炼额度)

- 构造"修复失败"场景 → 观察反思循环触发 → report 含 reflection_log
- 检索验证:query 相似故障命中失败案例且标注 recovered=false
- 遇 429/额度错误立即停下告知用户(见 tracemind-real-model-quota)

## 6. 范围边界(明确不做)

- 不做反思结果的自我评测/打分(LLM-as-judge 留后续)
- 不做失败案例的淘汰策略(留后续)
- 不引入新依赖、不碰前端、不重构 graph 其他部分

## 7. 简历亮点(面试可讲)

1. **Agent 自我改进闭环**:失败 → 结构化反思(根因修正/证据缺口/新假设/策略调整)→ 重试 → 记录完整反思链,用尽转 needs_human——展示对 Agent 失败处理的设计能力
2. **负样本记忆**:不仅沉淀成功经验,还沉淀失败教训并注入"避坑"指令,展示记忆系统的双向设计(正样本复用 + 负样本规避)
3. **工程稳健性**:反思/沉淀/检索任一失败都不阻塞诊断,预算控制防死循环
