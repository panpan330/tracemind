# README 优化设计(GitHub 首页精炼+增强)

日期:2026-08-14
版本:文档优化(V1.15 后续)
前置:V1.15(简历素材打包)

## 1. 背景与目标

README 作为 GitHub 项目首页,存在短板:无视觉元素(但用户决定纯文字)、首屏信息密度不当、V1.1-V1.7 超长技术章节(约 200 行)淹没核心卖点、V1.8-V1.14 无对应详细章节结构不对称、测试数字过期(422→436)。

目标:**把 README 优化为标准 GitHub 首页**——卖点前置、可扫读、结构对称,长技术章节移出,数字更新。

## 2. 现状盘点

- README 384 行:架构总览(mermaid)/关键设计 5 条/快速开始/演示流程/目录结构/测试与验收/技术栈/简历亮点三层/V1.1-V1.7 详细章节(200 行)/版本历史 15 项。
- 测试数已过时:README 写 422/45,实际后端 436、前端 46。
- 有 docs/interview-qa.md、docs/superpowers/specs/ 可链接。

## 3. 新结构(自上而下)

```
1. 标题 + 徽章行(shields.io 静态徽章)
2. 一句话描述(保留现有)
3. 核心功能一览(新增功能表格)
4. 架构总览(mermaid,保留)
5. 关键设计(保留 5 条,微调)
6. 快速开始(保留,压缩关键命令)
7. 演示流程(保留 SCN-001/002 精简)
8. 技术栈 + 目录结构(精简)
9. 测试与验收(压缩,数字更新 436/46)
10. 简历亮点三层(保留)
11. 版本历史(15 行精炼 + 指向 docs/changelog.md)
12. 文档链接区(specs / changelog / interview-qa)
```

## 4. 关键改动

### 4.1 徽章行(shields.io 静态)

```markdown
![tests](https://img.shields.io/badge/tests-436%20passed-brightgreen)
![version](https://img.shields.io/badge/version-v1.16-blue)
![langgraph](https://img.shields.io/badge/LangGraph-Agent-blue)
![mcp](https://img.shields.io/badge/MCP-Tools-green)
![vue](https://img.shields.io/badge/Vue3-Frontend-4FC08D)
```

- 静态值(无 CI),诚实标注不伪装自动更新。

### 4.2 核心功能一览(新增)

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

### 4.3 V1.1-V1.7 章节迁移

- 新建 `docs/changelog.md`:V1.1-V1.7 详细技术章节**原样移入**(含 V1.8-V1.14 补一个精炼描述,凑齐 14 个版本)。
- README 版本历史保留 15 行精炼列表,末尾加:`> 各版本详细技术说明见 [docs/changelog.md](docs/changelog.md)`。

### 4.4 数字更新

- 后端 422→436、前端 45→46(README 测试与验收 + 简历亮点层同步)。

### 4.5 文档链接区(新增)

```markdown
## 文档
- [各版本设计文档](docs/superpowers/specs/)
- [版本演进详细说明](docs/changelog.md)
- [面试 Q&A](docs/interview-qa.md)
```

## 5. 测试与验收

- 文档类改动,无代码测试。
- 验收:README 新结构完整、数字 436/46 一致、changelog 含 V1.1-V1.14、链接可用。

## 6. 范围边界(明确不做)

- 不加截图/GIF(用户决定纯文字)。
- 不改代码、不动架构/快速开始的核心内容(只压缩表述)。
- 徽章用静态值,不做 CI 自动更新。
