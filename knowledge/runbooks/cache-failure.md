---
doc_id: runbook-cache-failure
title: 缓存失效导致的降级
doc_fault_category: cache
doc_service: inventory
doc_scenario_id: SCN-001
doc_version: 1.0
---
## 症状
接口变慢呈间歇性,与缓存命中率相关,数据库查询本身计划正常。
## 证据
get_trace 显示部分请求慢;get_query_plan 正常;无稳定 digest 增量 → 缺索引根因不成立。
## 排除
数据库证据(E3~E5)不成立 → 排除缺索引;缓存失效需缓存指标确认,当前观测范围内证据不足。
## 处置
缓存类修复不在白名单内,证据不足转人工。
