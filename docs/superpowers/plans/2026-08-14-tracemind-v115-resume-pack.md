# V1.15 简历素材打包 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 V1.1-V1.14 的能力沉淀成可直接求职的素材:README 版本历史补全(V1.7-V1.14)+ 简历亮点升级(三层 + 量化证据)+ 面试 Q&A 文档 + 项目一句话描述。纯文档,不做新功能。

**Architecture:** 纯文档改动——README.md(版本历史列表追加 + 简历亮点重写 + 顶部一句话)、新建 docs/interview-qa.md。无代码。

**Tech Stack:** Markdown。

## Global Constraints

- 纯文档改动,不碰代码、不碰 README 的架构/快速开始/演示流程/技术栈章节。
- 版本历史用现有列表格式(`- **V1.x**:...`)追加 V1.7-V1.14。
- 简历亮点分三层(工程能力/Agent 智能/数据验证),每条带量化证据(数字必须真实:后端 422 测试、前端 45 测试、单次诊断成本 ¥0.0153、SCN-001/002 等)。
- 面试 Q&A 8-10 题,答案 3-5 句,紧扣项目落地。
- 一句话描述 ≤40 字。
- 无测试(文档类);验收 = 人工 review 素材质量。
- 沿用 V1.6 决定:不做 CI;验证 = 人工检查 + git diff 审阅。

## File Structure

- `README.md`(Modify):顶部一句话 + 简历亮点重写 + 版本历史列表追加 V1.7-V1.14。
- `docs/interview-qa.md`(Create):面试 Q&A。

---

### Task 1:README 顶部一句话 + 简历亮点升级

**Files:**
- Modify: `README.md`

**Interfaces:**
- Produces: 项目一句话描述(≤40 字)+ 三层简历亮点(覆盖 V1.1-V1.14,带量化证据)。Task 2 的版本历史追加复用同一文件。

- [ ] **Step 1: 读 README 顶部 + 简历亮点现状**

读 `README.md` 第 1-10 行(顶部)与 145-152 行(简历亮点),确认插入位置与现有措辞。

- [ ] **Step 2: 加顶部一句话描述**

`README.md` 第 1 行标题后(或"架构总览"前)加:

```markdown
> **一句话**:基于 LangGraph 的 AI 故障诊断系统:证据驱动消除幻觉,多模型路由控成本,记忆+反思持续进化,评测平台量化验证改进。
```

(字数 ≤40,措辞可按实际调整。)

- [ ] **Step 3: 重写简历亮点**

把现有 4 条亮点替换为三层结构(保留原有价值措辞 + 补齐 V1.8-V1.14):

```markdown
## 简历亮点

**工程能力层**
- 用 LangGraph 状态机编排**证据驱动**诊断流程,E1~E5/L1~L6 事实闸门替代 LLM 猜测式根因,消除幻觉;双 Diagnostic Policy 支持多故障场景(缺索引、锁等待)与冲突检测。
- 将人工审批(human-in-the-loop)嵌入状态机:唯一写路径 + 前置校验 + 过期自动拒绝;锁场景处置对 KILL 执行前复核(目标未变/仍持锁/账号白名单),负例零误杀。
- 工具层 MCP 协议标准化(stdio → Streamable HTTP 独立容器),最小权限隔离(五账号/三连接池/白名单参数),全量审计 + 回放;真实可观测性(Prometheus/Jaeger/OTel);SSE 实时 Agent 进度面板。

**Agent 智能层**
- 长期记忆:qdrant 案例向量沉淀 + 语义检索复用;上下文压缩(EvidenceSummarizer)控长链 token。
- 反思自改进:修复失败 → 结构化复盘(根因修正/证据缺口/新假设/策略调整)→ 最多 3 轮重试;失败案例负样本记忆(避坑检索,不重复失败路径)。
- 多模型路由 + 动态路由学习:强推理节点用大模型、高频工具节点用快模型,窗口滚动评分按成功率/时延/成本自动选优。

**数据验证层**
- 后端 422 个测试、前端 45 个测试、24+ 离线评测、SCN-001/002 真实模型验收通过。
- 成本量化:多模型路由后单次诊断成本 ¥0.0153(实测)。
- 评测平台可视化:成功率/耗时/成本趋势,量化验证每次改进有效。
```

- [ ] **Step 4: 提交**

```bash
git add README.md
git commit -m "docs(resume): README 一句话描述 + 简历亮点三层升级(带量化证据)"
```

---

### Task 2:README 版本历史追加 V1.7-V1.14

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 1 的同一文件。
- Produces: 版本历史列表追加 V1.7-V1.14(现有列表格式)。无新接口。

- [ ] **Step 1: 追加列表项**

在 `## 版本历史` 列表末尾(V1.6 后)追加:

```markdown
- **V1.7**:MCP Streamable HTTP 远程传输与服务化(独立容器、标准传输、token 认证、两段式审计)
- **V1.8**:Agent 运行观测面板 + 量化评测报告(成功率/耗时/tokens)
- **V1.9**:长期记忆(qdrant 案例沉淀 + 语义检索复用)+ 上下文压缩(EvidenceSummarizer)
- **V1.10**:反思自改进(reflect 结构化复盘 + 3 轮重试)+ 失败案例负样本记忆(避坑检索)
- **V1.11**:多模型路由(节点级选模型)+ 成本统计(单次诊断 ¥0.0153)+ 容灾 fallback
- **V1.12**:动态路由学习(窗口滚动评分:成功率/时延/成本加权)
- **V1.13**:评测平台可视化(eval_run 持久化 + 前端列表/详情/趋势)
- **V1.14**:Agent 进度面板(SSE 事件前端实时展示,节点级渐进可视化)
```

- [ ] **Step 2: 提交**

```bash
git add README.md
git commit -m "docs(resume): README 版本历史补全 V1.7-V1.14"
```

---

### Task 3:面试 Q&A 文档

**Files:**
- Create: `docs/interview-qa.md`

**Interfaces:**
- Produces: 8-10 个高频面试问题 + 项目化答案(每题 3-5 句)。Task 4 的自审引用它。

- [ ] **Step 1: 写文档**

```markdown
# 面试 Q&A(TraceMind 项目)

> 基于 V1.1-V1.14 的落地经验,每个答案 3-5 句,紧扣本项目。

## 1. 这个项目解决了什么问题?
传统故障诊断依赖人工看监控/慢查询,慢且易漏。本项目用 LangGraph 编排 AI Agent 自动完成"假设→取证→根因→修复→验证"闭环,证据来自真实系统(MySQL 执行计划/慢查询/锁等待/P95),可重复、可量化。

## 2. Agent 怎么防幻觉?
两条:一是证据闸门(E1~E5/L1~L6 事实检查),LLM 只能提出假设,结论必须由真实证据确认;二是所有数据来自真实系统而非 LLM 编造,工具调用全量审计。

## 3. 多 Agent 怎么设计?
本项目是"单 Agent 能力纵深"演进:记忆(长期案例复用)→ 反思(失败自我改进)→ 路由(多模型按任务选优)。多 Agent 协作是把这些能力按角色拆分(Planner 拆任务/Worker 并行取证/Reviewer 交叉审查),每层有独立数据与降级路径。

## 4. 怎么验证 Agent 改进有效?
量化评测平台:每次改进跑 SCN-001/002 多轮,记录成功率/耗时/成本,可视化趋势。后端 422 测试 + 前端 45 测试 + 真实模型验收,改进有效是数据证明。

## 5. RAG 检索不到怎么办?
降级不阻塞:检索失败记录 rag_degraded 事件,Agent 用确定性数据源继续;记忆侧沉淀失败案例作避坑参考,下次相似问题不重复失败路径。

## 6. 模型成本怎么控?
节点级路由:强推理节点(hypothesize/reflect)用 qwen3.8-max,高频工具节点(select_tool)用 qwen3.7-flash;动态路由学习按窗口评分自动选优。实测单次诊断成本 ¥0.0153。

## 7. human-in-the-loop 怎么设计?
审批作为状态机节点:唯一写路径 + 前置校验 + 过期自动拒绝;锁场景处置(KILL)执行前复核目标未变/仍持锁/账号白名单,负例零误杀。

## 8. 工具层为什么用 MCP?
MCP 标准化工具协议:工具实现唯一(stdio/HTTP 只是 transport),权限隔离(五账号/三连接池/白名单参数),全量审计 + 回放。升级到 Streamable HTTP 后独立容器、可水平扩展。

## 9. 长上下文怎么处理?
EvidenceSummarizer 上下文压缩:证据超阈值(8 条)自动摘要,控 LLM 输入 token;配合长期记忆只注入 top-k 相关案例,避免无界上下文。

## 10. 测试怎么做的?
TDD:先写失败测试再实现;repo/API 测试用 FakeEngine/TestClient 不连真实库;真实模型验收在 VM 跑 SCN 闭环,遇额度错误立即停止。
```

- [ ] **Step 2: 提交**

```bash
git add docs/interview-qa.md
git commit -m "docs(resume): 面试 Q&A 文档(10 个高频问题 + 项目化答案)"
```

---

### Task 4:整体自审 + 验收

**Files:**
- `README.md`、`docs/interview-qa.md`

**Interfaces:**
- 无新接口;验证 Task 1-3 素材完整性。

- [ ] **Step 1: 素材完整性检查**

```bash
# 版本历史 V1.0-V1.14 齐全
grep -c "^\*\*V1\." README.md   # 应 ≥ 15(V1.0-V1.14)
# 简历亮点三层齐全
grep -c "层$" README.md | head  # 应含 工程能力层/Agent 智能层/数据验证层
# 面试 Q&A 10 题
grep -c "^## " docs/interview-qa.md  # 应 ≥ 10
# 一句话描述存在
grep -c "一句话" README.md
```

- [ ] **Step 2: 量化数据核对**

确认 README 中的数字与真实一致:
- 后端测试数(用 `grep -c "def test_" ai-service/tests/*.py` 汇总,当前 422)
- 前端测试数(web 目录 vitest 报告,当前 45)
- 成本 ¥0.0153(V1.11/1.12 验收实测)
- 如数字有出入,修正 README。

- [ ] **Step 3: 提交(如有修正)+ 推送**

```bash
git add -A && git commit -m "docs(resume): 自审修正"
git push origin main
```

(注意:GitHub 网络间歇不可用——若失败记录待推提交数,告知用户稍后重试。)
