# V1.13 设计:评测平台可视化

日期:2026-08-14
版本:V1.13
前置:V1.12(动态路由学习)

## 1. 背景与目标

V1.1-V1.12 迭代中,量化评测(`eval_agent.py` → `reports/evals/agent-eval-*.md`)持续验证 Agent 改进有效,但评测结果只是**静态 markdown 文件**:不可浏览、不可对比、无法直观看到"版本迭代 → 指标变化"趋势。对照"AI Agent / 大模型应用工程师"岗位,缺一块能证明"量化验证改进"的数据 sense 与工程完整度。

本版本目标:**把评测报告升级为可浏览的评测平台**——后端持久化评测记录,前端可视化展示成功率/耗时/成本及多版本趋势。

## 2. 现状盘点

- **评测产出**:`scripts/eval_agent_report.py` 的 `aggregate(rounds)` 产出 stats → `render_markdown()` 写 `reports/evals/agent-eval-{ts}.md`(静态文件)。
- **前端**:Vue 3 + Element Plus + vue-router,现有 5 个视图(Scenario/IncidentDetail/Report/RunObservation/Replay),无评测列表/详情页,无图表库。
- **后端**:SQLAlchemy + control 库(model_call 同库),repository 模式(`model_call_repo` 可参考)。
- **约束**:沿用零新增依赖原则(不引入 ECharts 等图表库,图表用纯 Element Plus)。

## 3. 后端:评测记录持久化 + API

### 3.1 eval_run 表(新增)

```sql
eval_run: id, created_at(datetime), scenario(str), rounds(int),
          success_rate(decimal), avg_duration_ms(int), total_cost(decimal),
          model_snapshot(str), summary(str), raw_json(text)
```

- 与 model_call 同库(control),repository 模式。

### 3.2 eval_agent_report.py 增强

- `aggregate(rounds)` 产出 stats 后,渲染 markdown 的同时**写 eval_run 行**(复用同 out_dir 的时间戳)。
- 写库失败不阻塞报告生成(降级:只出 md 文件,与现状一致)。

### 3.3 新增 API(app/api/evals.py)

- `GET /api/evals` → 列表(按时间倒序,含 id/created_at/scenario/rounds/success_rate/avg_duration_ms/total_cost/model_snapshot)。
- `GET /api/evals/:id` → 详情(含列表字段 + summary + raw_json 里的轮次明细)。

## 4. 前端:评测平台页面

### 4.1 EvalDashboardView.vue(路由 /evals)

- 顶部统计卡:总评测数 / 平均成功率 / 平均成本。
- 表格(按时间倒序):时间 / 场景 / 轮次 / 成功率(el-progress)/ 平均耗时 / 成本 / 操作(详情)。
- 列表即"多版本对比"——按时间倒序,一眼看出成功率/成本趋势。

### 4.2 EvalDetailView.vue(路由 /evals/:id)

- 指标卡:成功率 / 平均耗时 / 总成本 / 模型快照。
- 轮次明细表(每轮:场景 / 耗时 / 是否恢复)。
- 纯 Element Plus(el-progress / el-table / el-statistic),零新依赖。

## 5. 测试与验收

### 5.1 后端测试

- `tests/test_evals_repo.py`:eval_run CRUD(插入/列表/详情)。
- `tests/test_evals_api.py`:API 返回列表/详情,空库返回空列表不报错。
- `tests/test_eval_report.py`:`aggregate` 后写库成功;写库失败降级只出 md。

### 5.2 前端测试

- `EvalDashboardView.test.ts` + `EvalDetailView.test.ts`(沿用现有 vue 测试模式,如 ScenarioView.test.ts)。

### 5.3 验收

- 本地:跑一次评测(`eval_agent.py`)→ 库有 eval_run → API 返回 → 前端页面渲染。
- 前端 build + 单测通过。
- VM 可选:部署后浏览器看页面(不强制,前端本地 build 验证即可)。

## 6. 范围边界(明确不做)

- 不做复杂图表(不引入 ECharts)。
- 不做评测触发 UI(评测仍命令行跑)。
- 不做多版本 diff 图(列表页按时间倒序即轻量对比)。
- 不改现有评测脚本的 md 输出逻辑(增量写库)。

## 7. 简历亮点(面试可讲)

1. **量化验证改进**:每次 Agent 改进(记忆/反思/路由)都有评测记录,列表页能看出成功率/成本随版本迭代的变化——"改进有效不是拍脑袋,是数据证明"
2. **数据 sense**:成功率/耗时/成本多维指标可视化,展示工程完整度
3. **零依赖增量**:纯 Element Plus 实现图表,沿用项目零新增依赖约束,写库失败降级不阻塞
