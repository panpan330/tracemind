---
doc_id: runbook-mysql-lock-wait
title: 锁等待导致的查询阻塞
doc_fault_category: slow-sql
doc_service: inventory
doc_scenario_id: SCN-001
doc_version: 1.0
---
## 症状
查询耗时上升但 EXPLAIN 显示走了索引,扫描行数正常,指标异常与具体请求时段相关。
## 证据
get_trace 显示耗时集中在 database 阶段但 EXPLAIN 正常;可观察是否存在长事务或锁等待
(Performance Schema 锁相关计数)。当前系统未提供锁监控工具时,证据不足以确认锁根因。
## 排除
索引存在 + 执行计划正常 → 排除缺索引根因;若 E1~E5 无法齐备,应转人工而非臆断锁等待。
## 处置
锁等待修复动作不在当前白名单内,确认此类根因需升级人工。
