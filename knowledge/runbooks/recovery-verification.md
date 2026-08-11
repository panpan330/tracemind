---
doc_id: runbook-recovery-verification
title: 修复后恢复验证标准
doc_fault_category: process
doc_service: inventory
doc_scenario_id: SCN-001
doc_version: 1.0
---
## 验证项
恢复验证检查:目标索引存在、执行计划使用目标索引、扫描行数回到基线范围、P95 回到健康基线。
## 探测方法
修复后主动执行三批固定探测请求,每批独立计算 P95,全部通过才算恢复;不读取含修复前慢请求的
滑动窗口。
## 未恢复
返回 not_recovered 或 inconclusive 时进入 needs_human,不自动重试执行修复。
## 记录
恢复验证结果写入 recovery_check,与修复执行记录关联,供复盘报告引用。
