# V1.16 设计:工程补强(多臂老虎机 + 成本告警 + 案例淘汰 + 评测触发 UI)

日期:2026-08-14
版本:V1.16
前置:V1.15(简历素材打包)

## 1. 背景与目标

V1.1-V1.15 完成 15 个版本建设,但历次 spec 里留了 4 个"明确不做"的小项,是项目完整度的尾巴:
1. **多臂老虎机探索**(V1.12 留):动态路由只有"利用"(选最优),无"探索"。
2. **成本预算告警**(V1.11 留):能统计成本,无总预算概念。
3. **失败案例淘汰策略**(V1.10 留):负样本记忆只增不减。
4. **评测触发 UI**(V1.13 留):评测只能命令行,演示不直观。

本版本目标:**一次补齐这四个工程补强项**,凑齐后项目无"明确不做"尾巴。四项都小(各 1-2 Task),默认关闭/零风险,开启才生效。

## 2. 现状盘点

- **动态路由**:`ModelScorer.best()`(model_scorer.py)窗口评分选最优;`route()`(model_router.py)候选里调 best;`settings.dynamic_routing` 默认关。
- **成本**:`CostTracker.aggregate_model_costs`(cost.py)按模型聚合;`model_call` 表有 model/tokens;`_audit_model_call`(llm.py)落库后已调 scorer.update。
- **负样本记忆**:`record_case`(memory.py)沉淀 `case-*-fail` 失败案例,payload 带 `ts`;`_case_payload` 有 `recovered`/`ts` 字段。
- **评测**:`eval_agent_report.py` 的 `write_report`(ts, rounds, stats, out_dir)写 md + eval_run;`GET /api/evals` 列表/详情已有;前端 EvalDashboardView 展示。

## 3. 块 1:多臂老虎机探索(增强 ModelScorer)

### 3.1 配置

```
TRACEMIND_ROUTING_EPSILON: float = 0.1   # ε-greedy 探索概率;0=纯利用(V1.12 兼容)
```

### 3.2 实现

`ModelScorer.best()` 加 ε-greedy:

```python
def best(self, node, candidates, epsilon=0.0, rng=None) -> str | None:
    # 候选里选评分最高者;ε 概率随机探索
    ...
    if epsilon > 0 and rng.random() < epsilon:
        return rng.choice(scored_candidates)   # 探索:随机选有数据的候选
    return scored[0][1]                        # 利用:选最优
```

- `rng` 可注入(测试固定 seed);`route()` 传 `settings.routing_epsilon`。
- ε=0 时与 V1.12 完全一致(零风险)。

## 4. 块 2:成本预算告警(新增 cost_alarm)

### 4.1 配置

```
TRACEMIND_COST_BUDGET: float = 0.0   # 累计成本预算(元);0=不启用
```

### 4.2 实现

- `CostTracker` 或新函数 `check_cost_budget()`:按 model_call 累计成本,超预算 → 写 `cost_over_budget` 事件(事件流已有基建,前端进度面板可显示)。
- 复用现有 model_call 审计(成本已统计),零新增埋点。
- 预算 0 / 未超 → 不触发;聚合异常降级。

## 5. 块 3:失败案例淘汰策略(增强 memory)

### 5.1 配置

```
TRACEMIND_CASE_RETENTION_DAYS: int = 0   # 失败案例保留天数;0=不清理
```

### 5.2 实现

- 新函数 `purge_expired_cases(store=None)`:`_case_payload` 已带 `ts`(ISO),扫 qdrant `case-*-fail` 案例,超过保留期的删除(只删失败案例,成功案例保留)。
- 触发时机:report 节点沉淀后顺带检查(不新增定时器,最简)。
- 幂等:删不存在的 point 不报错;qdrant 异常降级不阻塞诊断。

## 6. 块 4:评测触发 UI(后端触发接口 + 前端按钮)

### 6.1 后端 `POST /api/evals/run`

- body: `{scenario: str, rounds: int}`
- 校验:scenario ∈ {SCN-001, SCN-002},rounds ∈ [1,5];非法 → 400
- 后台线程跑 `eval_agent_report` 的 run 逻辑;结果自动写 eval_run(复用 write_report 链路)
- 返回 202 + run_id;真实模型时耗额度(前端标注)

### 6.2 前端 EvalDashboardView

- 加"运行评测"按钮 + scenario 下拉 + rounds 输入
- 提交后 loading + 完成后自动刷新列表
- 测试:mock POST,验证按钮触发 + 刷新

## 7. 测试与验收

### 7.1 单元测试

1. 多臂老虎机:ε=0 恒选最优(V1.12 兼容);ε=1 全随机(注入 seed);ε=0.1 大部分选最优
2. 成本告警:累计超预算 → 写 cost_over_budget;预算 0/未超不触发;聚合异常降级
3. 案例淘汰:purge 删超期 -fail 案例;未到期/成功案例保留;qdrant 异常降级;retention=0 不启用
4. 评测触发 API:参数非法 → 400;正常 → 202;后台跑完 eval_run 有记录
5. 前端:运行评测按钮 mock POST → loading → 刷新

### 7.2 回归

- 后端全量 pytest(当前 422 passed,无回归)
- 前端全量 vitest(45 passed)

### 7.3 E2E 验收(可选,VM 真实模型耗额度)

- 评测触发 UI 跑一轮 SCN-001,列表出现新记录
- 多臂老虎机 ε 开启下 select_tool 偶发探索其他候选

## 8. 范围边界(明确不做)

- 不做自动充值、不做 LLM-as-judge 自我打分
- 多臂老虎机仅 ε-greedy(不做 UCB)
- 评测触发不排队(一次一个,后端简单互斥)
- 不引入新依赖、不碰前端主流程

## 9. 简历价值

1. **探索 vs 利用**:能讲出 ε-greedy 权衡(路由系统高频考点),补上 V1.12 动态路由的最后一块
2. **记忆生命周期**:案例淘汰 = "记忆不只写入还淘汰",记忆系统高阶话题
3. **成本运维**:总预算告警,体现工程成本意识
4. **演示直观**:评测触发 UI,面试官浏览器点一下就能看评测跑起来
