---
doc_id: runbook-db-connection-pool-exhaustion
title: 数据库连接池耗尽
doc_fault_category: connection
doc_service: inventory
doc_scenario_id: SCN-001
doc_version: 1.0
---
## 症状
接口大面积超时,错误率上升,指标显示 database 阶段耗时异常但单条查询本身不慢。
## 证据
get_service_metrics 确认错误率异常;get_trace 显示请求在等待连接而非执行 SQL;连接池指标
不在当前观测范围时,证据不足以确认连接池根因。
## 排除
单条 SQL EXPLAIN 正常、trace 未显示数据库执行耗时 → 排除缺索引根因;连接池耗尽属另一类根因。
## 处置
连接池相关修复不在白名单内,证据不足时转人工,禁止臆断根因。
