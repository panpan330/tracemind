---
doc_id: runbook-mysql-lock-wait
title: 长事务锁等待阻塞库存预占(SCN-002)
doc_fault_category: mysql_lock_wait
doc_service: inventory
doc_scenario_id: SCN-002
doc_version: 1.1
---
## 症状
库存预占/扣减接口超时;trace 显示耗时集中在 database 阶段;查询等待锁而非执行;接口 P95 异常。
## 证据
get_lock_waiters(waits 列表,目标 inventory 记录,waits 中 object_schema=tracemind_business / object_table=inventory / waiting_query_ref=INVENTORY_RESERVATION,wait_duration_ms≥3000);
get_transaction_details(阻塞事务 age_ms≥5000,transaction_id/processlist_id 与 waits 中 blocking_transaction_id/blocking_processlist_id 复合匹配)。
## 排除
联合索引存在且执行计划正常(X-INDEX-NORMAL)→ 排除缺索引根因;索引状态未知 → 继续收集,不得确认锁根因。
## 处置
TERMINATE_BLOCKING_SESSION(仅经审批执行):执行前重查 8 项(关系仍存在/processlist 对应同一事务/仍持锁/超阈值/账号白名单/非系统线程/审批未过期/动作未执行);
ALREADY_RESOLVED(事务已自行结束)/ TARGET_CHANGED(连接被复用)/ EVIDENCE_STALE(关系变化)三种结果一律禁止误杀;
只允许终止 app_business 白名单连接,拒绝 TraceMind/调查/控制/系统线程。
