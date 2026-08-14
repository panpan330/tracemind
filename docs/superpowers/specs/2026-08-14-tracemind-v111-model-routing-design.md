# V1.11 设计:多模型路由 + 成本统计 + 容灾降级

日期:2026-08-14
版本:V1.11
前置:V1.10(Agent 反思与自我改进)

## 1. 背景与目标

V1.10 后 Agent 已具备:诊断闭环、MCP、多场景、可观测、回放、观测面板、量化评测、长期记忆、反思自改进。对照"AI Agent / 大模型应用工程师"岗位,仍缺一块高频工程能力:**多模型管理与成本控制**。

当前行为:整个 Agent 全程只用**一个模型**(`chat_model_resolved`,现为 qwen3.7-max),不同节点对模型能力需求不同却无差别调用——推理重的节点(假设生成/反思)和生成/高频节点(工具决策/报告)共用同档模型,成本无差别、无容灾。

本版本目标(三个,同一主题):

1. **按节点路由模型**:强推理节点(hypothesize/reflect)用 max 档,高频/生成节点(select_tool/report)用 flash 档——"用最合适的模型做最合适的事"。
2. **成本统计**:基于 model_call 审计表,按模型聚合 token 与估算成本,输出成本账单,可量化单次诊断成本。
3. **容灾降级**:主模型 429/5xx/超时 → 自动切异厂商 fallback 模型重试,不中断诊断;仍失败才转 needs_human。

## 2. 现状盘点

- **模型配置**:`settings.chat_model_resolved`(chat_model or llm_model);`TRACEMIND_EVAL_CHAT_MODEL` 固定评测快照。
- **调用链**:`LLMClient.chat/chat_json_with_usage(messages, max_tokens, model=None)`,`model or self.model` 已支持按调用覆盖——路由注入点现成。
- **审计表**:`model_call` 已记录 `model / input_tokens / output_tokens / status / degraded / node`——成本统计原始数据已齐,只缺聚合层。
- **降级机制**:`ModelDegradedError` + `_degrade(kind)`(real_strict 下抛错转 needs_human)——现有降级是"整体降级",缺"模型级切换重试"。
- **可用模型(实测通过 SO+TC,同一 apikey)**:qwen3.7-flash / qwen3.7-max / qwen3.8-max / qwen3.6-plus / qwen3.6-max-preview / deepseek-v4-flash-0731 / kimi-k2.7-code / glm-5.2。

## 3. 模型角色(精简 3 模型)

| 角色 | 模型 | 负责节点 | 理由 |
|---|---|---|---|
| 强推理 | `qwen3.8-max` | hypothesize、reflect | max 档推理质量最高,决定诊断成败 |
| 快而省 | `qwen3.7-flash` | select_tool、write_report | 高频 + 生成类,flash 便宜省成本 |
| 容灾兜底 | `deepseek-v4-flash-0731` | 主模型 429/5xx/超时自动切换 | 异厂商备用,防主模型故障中断诊断 |

## 4. 块 1:模型路由(ModelRouter)

### 4.1 配置(config.py 新增)

```
TRACEMIND_HYPOTHESIZE_MODEL: str = ""   # 空 → 回落 chat_model_resolved
TRACEMIND_SELECT_TOOL_MODEL: str = ""
TRACEMIND_REFLECT_MODEL: str = ""
TRACEMIND_REPORT_MODEL: str = ""
TRACEMIND_FALLBACK_MODEL: str = ""      # 容灾备用;空 → 不启用 fallback
```

### 4.2 路由逻辑(app/agent/model_router.py 新增)

```python
NODE_MODEL_KEY = {
    "hypothesize": "hypothesize_model",
    "select_tool": "select_tool_model",
    "reflect": "reflect_model",
    "write_report": "report_model",
}

def route(node: str) -> str | None:
    """返回该节点应使用的模型;未配置/未知节点返回 None(调用方回落默认)。"""
```

- 未配置节点 → 回落 `chat_model_resolved`(零配置行为不变)
- 全空配置 → 所有节点用默认模型(与现状完全一致,零风险)

### 4.3 注入点

- `OpenAICompatibleLLM.hypothesize/select_tool/reflect/write_report` 调用 `_chat_json_with_usage` 时传 `model=route(node)`(经 router 解析,None 则走默认)
- 每个节点的 model_call 审计 `model` 字段自然记录实际用模型——成本统计无需额外埋点

## 5. 块 2:成本统计(CostTracker)

### 5.1 单价表(app/agent/cost.py 新增)

```python
# 每百万 token 单价(元),按公开价配置,可覆盖
MODEL_PRICE_PER_M = {
    "qwen3.8-max": 20.0, "qwen3.7-max": 20.0, "qwen3.7-flash": 0.5,
    "deepseek-v4-flash-0731": 1.0,  # 示例;实际以百炼控制台为准
}
```

### 5.2 聚合逻辑

```python
def aggregate_model_costs(calls: list[dict]) -> dict:
    """按模型聚合:调用次数 / input_tokens / output_tokens / 估算成本(元)。"""
```

- 输入:`model_call` 表按 run/incident 查询结果
- 输出:`{"qwen3.8-max": {"calls": n, "input_tokens": x, "output_tokens": y, "cost": z}, ...}`
- 单价未配置的模型 → cost 记 0,不报错、不阻断

### 5.3 展示

- 复用观测面板(`RunObservationView`)或新增"成本账单"卡片,展示本次诊断各模型 token/成本占比
- 不新增后端 API(复用现有 run 查询)或极简 API

## 6. 块 3:容灾降级(模型级 fallback)

### 6.1 触发条件

`LLMClient.chat/chat_json_with_usage` 内:主模型抛 **429(限流/额度)、5xx、连接错误/超时** → 自动切换 `fallback_model` 重试同节点 1 次。

### 6.2 行为

- fallback 成功 → 返回正常结果,model_call 记录 `degraded=True` + 实际模型( fallback 模型名)
- fallback 也失败 → 抛 ModelDegradedError(上层转 needs_human,与现状一致)
- 参数错误(400)、结构化输出无效 → **不触发** fallback(不是模型可用性问题)

### 6.3 与现有机制的关系

- fallback 是"模型级切换重试"(不转 needs_human);`_degrade` 仍是"整体降级"(转 needs_human)——两层各司其职
- `fallback_model` 未配置 → 不启用 fallback,行为与现状完全一致

## 7. 测试与验收

### 7.1 单元测试(TDD)

新建 `tests/test_model_router.py`、`tests/test_cost.py`,扩展 `tests/test_llm_client.py`:

1. 路由:配置 `SELECT_TOOL_MODEL` → `route("select_tool")` 返回 flash;未配置节点 → None;全空配置 → None(回落默认)
2. 节点用路由模型:monkeypatch client,断言 select_tool 收到的 model 是 flash、hypothesize 是 max
3. 成本聚合:给定 model_call 记录 → 按模型聚合 token + 成本;单价未配置 → cost 0 不报错
4. 容灾:主模型 429 → fallback 重试成功 → 正常返回 + degraded 标记;fallback 也失败 → ModelDegradedError;400 错误不触发 fallback
5. 边界:fallback 与主模型相同 → 不重复重试;fallback 未配置 → 不启用

### 7.2 回归

- 后端全量 pytest(当前 391 passed,无回归)
- 前端:若加成本卡片则加对应测试;否则不动

### 7.3 E2E 验收(VM 真实模型,耗百炼额度)

- 真实跑 SCN-001:model_call 表按 node 检查——hypothesize= qwen3.8-max、select_tool= qwen3.7-flash
- 成本账单:一次诊断后 `aggregate_model_costs` 输出各模型 token + 成本
- 容灾:临时配置一个必然 429 的主模型名 → 观察自动切 fallback 完成诊断(可选)
- 遇 429/额度错误立即停下告知用户(见 tracemind-real-model-quota)

## 8. 范围边界(明确不做)

- 不做成本预算告警/自动充值
- 不做动态路由学习(按 latency/成功率调权重)——V1.12+ 候选
- 不引入新依赖、不改前端主流程

## 9. 简历亮点(面试可讲)

1. **按任务难度路由模型**:同一 Agent 内推理节点用 max、高频工具节点用 flash——"用最合适的模型做最合适的事",单次诊断成本可量化(成本账单)
2. **模型级容灾**:主模型 429/5xx 自动切异厂商 fallback 重试,不中断诊断;与整体降级(needs_human)分层
3. **成本可观测**:基于现有审计表零埋点聚合,展示各模型 token/成本占比
