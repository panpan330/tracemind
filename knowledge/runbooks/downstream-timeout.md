---
doc_id: runbook-downstream-timeout
title: 下游服务超时
doc_fault_category: downstream
doc_service: order
doc_scenario_id: SCN-001
doc_version: 1.0
---
## 症状
order-service 调用 inventory-service 超时,错误率上升,但 inventory 自身指标正常。
## 证据
get_trace 显示 inventory_http 阶段超时或 5xx;inventory 的 get_service_metrics 正常 → 问题在
调用链路而非目标库。
## 排除
inventory 数据库证据全部正常 → 排除缺索引根因;这是下游/调用方问题。
## 处置
下游超时修复不在白名单内,确认后转人工。
