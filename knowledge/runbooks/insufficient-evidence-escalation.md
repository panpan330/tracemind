---
doc_id: runbook-insufficient-evidence-escalation
title: 证据不足时的升级策略
doc_fault_category: process
doc_service: all
doc_scenario_id: SCN-001
doc_version: 1.0
---
## 原则
根因确认必须满足 E1~E5 全部证据;任何证据缺失且预算耗尽时,进入 needs_human,禁止臆断。
## 预算
决策次数、工具执行次数、连续无效调用、连续无进展均有上限;超限即转人工,避免无限循环。
## 记录
每次工具调用、证据判定、降级事件均落库审计,供人工复核证据链。
## 处置
needs_human 的 Incident 保留全部证据与假设供人工决策,不自动执行任何修复。
