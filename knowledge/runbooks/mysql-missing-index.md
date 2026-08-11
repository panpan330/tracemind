---
doc_id: runbook-mysql-missing-index
title: MySQL 缺少联合索引导致慢查询
doc_fault_category: slow-sql
doc_service: inventory
doc_scenario_id: SCN-001
doc_version: 1.0
---
## 症状
按 sku_id + warehouse_id 查询库存时,接口 P95 从毫秒级升到百毫秒级,数据库 CPU 无明显异常。
## 证据
get_service_metrics 确认 P95 异常;get_trace 显示耗时集中于 database 阶段;get_query_plan 的
EXPLAIN 显示 type=ALL 全表扫描;get_index_info 确认 (sku_id, warehouse_id) 联合索引缺失。
## 根因
当且仅当 E1~E5 五证据齐备时确认根因为缺少联合索引 idx_sku_warehouse(sku_id, warehouse_id)。
## 修复
修复动作 CREATE_INVENTORY_INDEX,须人工审批后执行;参数由系统固定,不可由模型编造。
## 验证
恢复验证:索引存在、执行计划使用目标索引、扫描行数下降、P95 回到健康基线(连续三批探测)。
