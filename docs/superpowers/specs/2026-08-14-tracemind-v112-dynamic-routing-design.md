# V1.12 设计:动态路由学习(窗口滚动评分)

日期:2026-08-14
版本:V1.12
前置:V1.11(多模型路由 + 成本统计 + 容灾降级)

## 1. 背景与目标

V1.11 让 Agent 按节点静态路由模型(hypothesize/reflect → qwen3.8-max,select_tool/report → qwen3.7-flash)+ 容灾 fallback。对照"AI Agent / 大模型应用工程师"岗位,静态路由的短板是:**模型表现会漂移**(同模型在不同时段成功率/latency 不同),静态配置无法自适应。

本版本目标:**让路由根据历史调用表现自动调整**——每个 (node, model) 组合按成功率/latency/成本加权滚动评分,`route(node)` 在候选模型列表里选评分最高者。模型表现变化 → 路由自动跟随,形成"数据驱动"的自适应路由。

## 2. 现状盘点

- **静态路由**:`model_router.route(node)` 读配置返回固定模型(零配置回落 `chat_model_resolved`)。
- **容灾**:`LLMClient.chat` 主模型重试耗尽切 fallback(仅 RETRY_STATUS 类错误)。
- **评分数据源现成**:`model_call` 表已记录 `node / model / latency_ms / status / degraded / input_tokens / output_tokens`,`list_model_calls_by_run` 可查——**零新增埋点**。
- **观测面板**:`build_run_observation` 已存在,可复用。

## 3. 评分模型

### 3.1 滑动窗口

每个 `(node, model)` 组合维护最近 `N` 次调用(默认 20)的统计,窗口满则淘汰最旧。

### 3.2 评分公式

```
score = w1·success_rate + w2·(1 - latency_norm) + w3·(1 - cost_norm)
默认权重: w1=0.6(成功率最重要) w2=0.25(时延) w3=0.15(成本)
```

归一化:
- `latency_norm = latency / p95(候选窗口内)`,clamp [0,1](latency 越低越好)
- `cost_norm = cost / max_cost(候选内最贵)`,clamp [0,1]
- `success_rate = 成功次数 / 窗口内次数`

### 3.3 路由逻辑(增强 route)

- `route(node)` 先查该节点候选模型列表(配置);候选存在且动态路由开启:
  - 候选模型窗口数据 ≥ 5 次 → 选评分最高者
  - 窗口数据不足(冷启动)→ 用配置默认模型(第一候选)
- 候选未配置或 `TRACEMIND_DYNAMIC_ROUTING=false` → 回落 V1.11 静态行为(零风险)

## 4. 配置(config.py 新增)

```
TRACEMIND_DYNAMIC_ROUTING: bool = false     # 默认关;开启才启用评分路由
TRACEMIND_ROUTING_WINDOW: int = 20          # 滑动窗口大小
TRACEMIND_ROUTING_WEIGHTS: str = "0.6,0.25,0.15"  # 成功率/时延/成本权重
TRACEMIND_SELECT_TOOL_CANDIDATES: str = ""  # 如 "qwen3.7-flash,qwen3.8-max"
TRACEMIND_HYPOTHESIZE_CANDIDATES: str = ""  # 同理(仅需给可切换的节点配候选)
TRACEMIND_REFLECT_CANDIDATES: str = ""
TRACEMIND_REPORT_CANDIDATES: str = ""
```

## 5. 实现结构

### 5.1 ModelScorer(app/agent/model_scorer.py 新增)

```python
class ModelScorer:
    """按 (node, model) 滑动窗口维护评分。"""

    def __init__(self, window: int = 20, weights: tuple[float, float, float] = (0.6, 0.25, 0.15)):
        ...

    def update(self, node: str, model: str, outcome: dict) -> None:
        """调用后增量更新:outcome = {success: bool, latency_ms: int, cost: float}。"""

    def best(self, node: str, candidates: list[str]) -> str | None:
        """候选里选窗口评分最高者;数据不足(< MIN_SAMPLES=5)返回 None(调用方用默认)。"""
```

- 内存缓存:`_windows: dict[tuple[str, str], deque]`
- `update` 由审计落库后触发;`best` 由 `route` 调用

### 5.2 route 增强(app/agent/model_router.py)

```python
def route(node: str) -> str | None:
    """动态路由:候选里选评分最高者;未启用/无候选/数据不足 → 回落静态配置。"""
    if settings.dynamic_routing:
        candidates = _candidates(node)
        if candidates:
            chosen = scorer.best(node, candidates)
            if chosen:
                return chosen
    return _static_route(node)   # 原 V1.11 逻辑
```

- `scorer` 为模块级单例(进程内共享)
- 审计落库后调用 `scorer.update(...)`:在 `_audit_model_call` 内或节点调用完成后统一触发

### 5.3 与现有机制的关系

- fallback(容灾)保持不变:动态路由选出的模型若 429/5xx → 仍切 fallback 重试
- 成本统计保持不变:`aggregate_model_costs` 仍基于 model_call 表
- 动态路由开启是**可选增强**;关闭时与 V1.11 完全一致

## 6. 测试与验收

### 6.1 单元测试(TDD)

新建 `tests/test_model_scorer.py`,扩展 `tests/test_model_router.py`:

1. 评分计算:给定窗口数据 → score 符合权重公式;成功率高的模型 score 高
2. 窗口滚动:满 20 淘汰最旧,新记录计入(滚动非累计)
3. 路由选优:候选 [flash, max],flash 评分高 → route 返回 flash;反之返回 max
4. 冷启动:窗口 <5 次 → 返回配置默认模型
5. 零配置回落:`dynamic_routing=false` 或候选未配置 → 与 V1.11 一致(静态)
6. 异常降级:scorer 异常 → 回落静态 route,不崩溃

### 6.2 回归

- 后端全量 pytest(当前 405 passed,无回归)
- 前端不动

### 6.3 E2E 验收(VM 真实模型,耗百炼额度)

- 开启动态路由(select_tool 候选 [qwen3.7-flash, qwen3.8-max])跑 SCN-001
- 观察 model_call:select_tool 在候选间按评分选中模型(稳定选优或切换)
- 成本对比:动态路由前后单次诊断成本不劣化
- 遇 429/额度错误立即停下告知用户(见 tracemind-real-model-quota)

## 7. 范围边界(明确不做)

- 不做多臂老虎机/UCB 探索(方案 C 留 V1.13+ 候选)
- 不做评分面板前端可视化(数据可查即可)
- 不引入新依赖、不改前端主流程

## 8. 简历亮点(面试可讲)

1. **数据驱动的自适应路由**:路由不是写死的,而是按历史成功率/latency/成本滚动评分自动调整——"让数据决定用哪个模型"
2. **权重可解释**:成功率优先、时延/成本平衡,面试能讲清楚为什么这样加权
3. **工程稳健性**:冷启动用默认、数据不足不瞎猜、异常回落静态、开关默认关闭——每一步都有降级路径
4. **与 V1.11 闭环**:静态路由 → 动态学习,同一主题的进阶故事
