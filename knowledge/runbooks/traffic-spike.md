---
doc_id: runbook-traffic-spike
title: 流量突增导致接口变慢
doc_fault_category: traffic
doc_service: inventory
doc_scenario_id: SCN-001
doc_version: 1.0
---
## 症状
QPS 明显上升的同时 P95 上升,数据库查询计划正常,无新增慢 SQL。
## 证据
get_service_metrics 显示 QPS 显著高于健康基线;get_query_plan 显示走索引;get_index_info
显示索引存在 → 缺索引根因不成立。
## 排除
索引存在 + 执行计划正常 + 无 digest 增量 → 排除 E3/E4/E5;根因可能是容量而非代码缺陷。
## 处置
流量类根因不在白名单修复内,证据显示系统健康时不得强行套用索引修复,转人工评估容量。
