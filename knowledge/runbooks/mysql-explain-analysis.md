---
doc_id: runbook-mysql-explain-analysis
title: 用 EXPLAIN 区分索引失效与查询本身低效
doc_fault_category: slow-sql
doc_service: inventory
doc_scenario_id: SCN-001
doc_version: 1.0
---
## 症状
接口变慢但无法直接确认是否缺索引,需要执行计划定位。
## 证据
get_query_plan 对 INVENTORY_LOOKUP 白名单模板执行 EXPLAIN FORMAT=JSON;关注 access_type:
ALL 表示全表扫描,ref 表示走索引。结合 get_index_info 判断是索引缺失还是索引未被命中。
## 排除
access_type=ref 且 key_len 覆盖查询列 → 索引存在且被使用,排除"缺索引"根因;若 access_type=ALL
且索引存在,可能是隐式类型转换或查询写法问题,需要人工复核。
## 边界
EXPLAIN 参数仅允许白名单模板(INVENTORY_LOOKUP),演示参数由程序固定,模型不得编造表名或 SQL。
