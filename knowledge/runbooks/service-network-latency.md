---
doc_id: runbook-service-network-latency
title: 服务间网络延迟
doc_fault_category: network
doc_service: order
doc_scenario_id: SCN-001
doc_version: 1.0
---
## 症状
order-service 调用 inventory-service 变慢,但 inventory 自身查询指标正常。
## 证据
get_trace 显示耗时集中在 inventory_http 阶段而非 database 阶段;get_service_metrics 显示
inventory 的 P95 正常 → 问题在网络或调用链而非数据库。
## 排除
database 阶段耗时占比 < 50% → 排除数据库根因;数据库证据(E3~E5)不成立 → 排除缺索引。
## 处置
网络类根因不在白名单修复内,确认后转人工或标记 needs_human。
