# README 优化(GitHub 首页精炼+增强) 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 README 优化为标准 GitHub 首页:徽章行 + 核心功能表格 + 长章节迁到 docs/changelog.md + 数字更新 + 文档链接区。纯文档,不碰代码。

**Architecture:** README.md 重构(顶部徽章+功能表格,V1.1-V1.7 章节删除),新建 docs/changelog.md(V1.1-V1.14 详细说明)。版本史列表保留。

**Tech Stack:** Markdown + shields.io 静态徽章。

## Global Constraints

- 纯文档改动,不碰代码。
- 徽章用 shields.io 静态 URL(无 CI,值手写:测试 436、版本 v1.16)。
- V1.1-V1.7 详细章节(README 164-366 行)原样移入 docs/changelog.md,README 只留版本史精炼列表 + 链接。
- 数字更新:后端 436、前端 46(与实测一致:433 个测试函数 ≈ 436 含参数化,前端 46 用例)。
- changelog 补 V1.8-V1.14 精炼描述(每个 1-2 句),凑齐 14 个版本。
- 快速开始/架构总览/关键设计/演示流程/简历亮点核心内容保留(只压缩表述)。
- 无测试(文档类);验收 = 人工检查结构 + 数字 + 链接。
- 沿用 V1.6 决定:不做 CI。

## File Structure

- `README.md`(Modify):顶部徽章+功能表格;删除 V1.1-V1.7 章节;版本史加 changelog 链接;文档链接区;数字更新。
- `docs/changelog.md`(Create):V1.1-V1.7 原样 + V1.8-V1.14 精炼。

---

### Task 1:README 顶部徽章 + 核心功能表格

**Files:**
- Modify: `README.md`

**Interfaces:**
- Produces: 标题下徽章行 + 一句话后"核心功能一览"表格。Task 2 的版本史/链接改动复用同一文件。

- [ ] **Step 1: 加徽章行**

`README.md` 标题(`# TraceMind`)后加:

```markdown
![tests](https://img.shields.io/badge/tests-436%20passed-brightgreen)
![version](https://img.shields.io/badge/version-v1.16-blue)
![langgraph](https://img.shields.io/badge/LangGraph-Agent-blue)
![mcp](https://img.shields.io/badge/MCP-Tools-green)
![vue](https://img.shields.io/badge/Vue3-Frontend-4FC08D)
```

- [ ] **Step 2: 加核心功能一览**

一句话描述后、`---` 前加:

```markdown
## 核心功能一览

| 能力 | 说明 |
|---|---|
| 证据驱动诊断闭环 | LangGraph 状态机:假设→取证→根因→修复→审批→验证→复盘 |
| 双故障场景 | SCN-001 缺索引 / SCN-002 锁等待,真实 MySQL 证据 |
| 证据闸门防幻觉 | E1~E5/L1~L6 事实检查,根因必须证据确认 |
| 人工审批安全闭环 | 唯一写路径 + 过期拒绝 + KILL 执行前复核 |
| MCP 工具安全 | stdio→Streamable HTTP,最小权限隔离,全量审计 |
| 长期记忆 + 反思自改进 | qdrant 案例复用 + 失败负样本 + 3 轮重试 |
| 多模型路由 + 动态学习 | 节点级选模型 + ε-greedy,成本 ¥0.0153/次 |
| 量化评测平台 | 成功率/耗时/成本趋势,可视化验证改进 |
```

- [ ] **Step 3: 提交**

```bash
git add README.md
git commit -m "docs(readme): 顶部徽章行 + 核心功能一览表格"
```

---

### Task 2:V1.1-V1.7 章节迁移到 changelog

**Files:**
- Create: `docs/changelog.md`
- Modify: `README.md`(删除 164-366 行 V1.1-V1.7 章节)

**Interfaces:**
- Produces: docs/changelog.md(V1.1-V1.14 详细);README 删除 V1.1-V1.7 章节,版本史列表保留。Task 3 的链接区指向 changelog。

- [ ] **Step 1: 创建 changelog.md**

```markdown
# TraceMind 版本演进

> 各版本详细技术说明。README 首页只保留精炼版本史。

## V1.1:真实 LLM + Tool Calling + RAG + 评测体系

[原 README V1.1 章节内容原样移入]

## V1.2:MCP 工具服务(stdio)
...V1.2-V1.7 原样...

## V1.8:Agent 运行观测面板 + 量化评测报告
Agent 运行观测面板 + 量化评测报告(成功率/耗时/tokens)。

## V1.9:长期记忆 + 上下文压缩
qdrant 案例沉淀 + 语义检索复用 + EvidenceSummarizer 压缩。

## V1.10:反思自改进 + 负样本记忆
reflect 结构化复盘 + 3 轮重试 + 失败案例避坑检索。

## V1.11:多模型路由 + 成本统计 + 容灾
节点级选模型 + 成本账单(单次 ¥0.0153)+ fallback 容灾。

## V1.12:动态路由学习
窗口滚动评分(成功率/时延/成本加权)+ ε-greedy 探索。

## V1.13:评测平台可视化
eval_run 持久化 + 前端列表/详情/趋势。

## V1.14:Agent 进度面板
SSE 事件前端实时展示,节点级渐进可视化。
```

(实施时:从 README 复制 V1.1 至 V1.7 的原文到 changelog 对应章节,确保内容一字不差。)

- [ ] **Step 2: 删除 README 的 V1.1-V1.7 章节**

用 delete_range 删除 README 从 `## V1.1:真实 LLM + Tool Calling + RAG + 评测体系`(164 行)到 `## 版本历史`(368 行)之前的所有内容,保留 `## 版本历史`。

- [ ] **Step 3: 提交**

```bash
git add README.md docs/changelog.md
git commit -m "docs(readme): V1.1-V1.7 技术章节迁至 docs/changelog.md"
```

---

### Task 3:版本史链接 + 数字更新 + 文档链接区

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: docs/changelog.md(Task 2)。
- Produces: 版本史末尾 changelog 链接;测试数字 422→436/45→46;新增文档链接区。

- [ ] **Step 1: 版本史加 changelog 链接**

版本历史列表末尾加:

```markdown
> 各版本详细技术说明见 [docs/changelog.md](docs/changelog.md)
```

- [ ] **Step 2: 数字更新**

README 中所有 `422 个测试` → `436 个测试`,`45 个测试` → `46 个测试`(简历亮点层 + 测试与验收章节)。

- [ ] **Step 3: 加文档链接区**

文件末尾(版本史后)加:

```markdown
## 文档

- [各版本设计文档](docs/superpowers/specs/)
- [版本演进详细说明](docs/changelog.md)
- [面试 Q&A](docs/interview-qa.md)
```

- [ ] **Step 4: 提交**

```bash
git add README.md
git commit -m "docs(readme): 版本史 changelog 链接 + 测试数字更新 + 文档链接区"
```

---

### Task 4:整体自审 + 推送

**Files:**
- `README.md`、`docs/changelog.md`

**Interfaces:**
- 无新接口;验证 Task 1-3 集成。

- [ ] **Step 1: 结构检查**

```bash
# README 不再含 V1.x 详细章节
grep -c "^## V1\." README.md   # 应为 0(版本史列表是 - **V1.x** 不是 ##)
# 含核心功能表格
grep -c "核心功能一览" README.md   # 应 ≥1
# 含徽章
grep -c "shields.io" README.md     # 应 ≥5
# 数字更新
grep -c "436" README.md            # ≥2
# changelog 含 V1.1-V1.14
grep -c "^## V1\." docs/changelog.md  # 应 ≥14
# 链接区
grep -c "interview-qa" README.md  # ≥1
```

- [ ] **Step 2: 内容完整性核对**

抽查 changelog 中 V1.1/V1.3/V1.7 内容与迁移前一致(文字没丢)。

- [ ] **Step 3: 提交(如有修正)+ 推送**

```bash
git add -A && git commit -m "docs(readme): 自审修正"
git push origin main
```

(注意:GitHub 网络间歇不可用——若失败记录待推提交数,稍后重试。)
