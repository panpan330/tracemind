"""FixRegistry:修复动作唯一执行权威(代码内)。数据库 fix_definition 仅为展示投影。
V1.3:支持 MISSING_INVENTORY_INDEX(建索引)与 LONG_RUNNING_TRANSACTION_BLOCKING_INVENTORY_RESERVATION(终止阻塞会话)。"""
import hashlib
import json
from dataclasses import dataclass

from app.agent.policies import ROOT_CAUSE_INDEX, ROOT_CAUSE_LOCK


@dataclass(frozen=True)
class FixActionDefinition:
    action_type: str
    table_ref: str
    index_name: str
    columns: list[str]
    risk_level: str
    reason_template: str


# blocking_relation_hash 稳定关系身份 10 项(不含任何时间字段;设计 V1.3 §5.2)
_RELATION_HASH_FIELDS = (
    "incident_id", "agent_run_id", "blocking_transaction_id", "blocking_processlist_id",
    "blocking_lock_ref", "waiting_transaction_id", "waiting_query_ref",
    "locked_schema", "locked_table", "locked_index",
)


def build_relation_hash(fields: dict) -> str:
    """稳定关系身份 10 项(不含时间字段);字段排序固定的规范化 JSON。"""
    blob = json.dumps({k: fields[k] for k in _RELATION_HASH_FIELDS if k in fields},
                      sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _extract_lock_parameters(state: dict) -> dict:
    """程序从已确认 Evidence 提取终止会话参数(LLM 不接触,不经过 LLM)。"""
    ev = {str(e.get("key") or e.get("id")).lower(): e.get("content") or {}
          for e in state.get("evidence") or []}
    waits = (ev.get("l1") or {}).get("waits") or []
    target = [w for w in waits
              if w.get("object_schema") == "tracemind_business"
              and w.get("object_table") == "inventory"
              and w.get("waiting_query_ref") == "INVENTORY_RESERVATION"]
    if not target:
        raise ValueError("无有效锁等待证据,无法构造 TERMINATE_BLOCKING_SESSION 参数")
    w = target[0]
    tx = ev.get("l2") or {}
    # blocking_transaction_id 取 L2 证据的真实 innodb_trx.trx_id(与执行前重查的 trx_id 同一 ID 空间,
    # 用于防连接复用误杀);L2 缺失时降级用 L1 的 ENGINE id(仅信息)。
    blocking_tx_id = tx.get("transaction_id") or w.get("blocking_transaction_id")
    return {
        "blocking_transaction_id": blocking_tx_id,
        "blocking_processlist_id": w.get("blocking_processlist_id"),
        "blocking_lock_ref": w.get("blocking_lock_ref"),
        "locked_schema": w.get("object_schema"),
        "locked_table": w.get("object_table"),
        "locked_index": w.get("index_name"),
        "waiting_transaction_id": w.get("requesting_transaction_id"),
        "waiting_query_ref": w.get("waiting_query_ref"),
        "processlist_id": w.get("blocking_processlist_id"),  # KILL 目标 = 会话
        "tx_age_ms": tx.get("age_ms"),
    }


_FIXES = {
    ROOT_CAUSE_INDEX: FixActionDefinition(
        action_type="CREATE_INVENTORY_INDEX", table_ref="inventory",
        index_name="idx_sku_warehouse", columns=["sku_id", "warehouse_id"],
        risk_level="medium",
        reason_template=("已通过 E1~E5 证据链确认库存查询缺少 idx_sku_warehouse(sku_id, warehouse_id),"
                         "建议执行预定义索引创建操作。")),
    ROOT_CAUSE_LOCK: FixActionDefinition(
        action_type="TERMINATE_BLOCKING_SESSION", table_ref="inventory",
        index_name="", columns=[], risk_level="high",
        reason_template=("已通过 L1~L5 证据链确认长事务持有库存目标记录排他锁,"
                         "阻塞库存预占事务,建议终止该阻塞会话。")),
}


class FixRegistry:
    @staticmethod
    def resolve(root_cause: str) -> FixActionDefinition:
        return _FIXES[root_cause]


def _sha256(parameters: dict) -> str:
    blob = json.dumps(parameters, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_proposal(state: dict) -> dict:
    root_cause = state.get("root_cause_code") or ROOT_CAUSE_INDEX
    if root_cause not in _FIXES:
        root_cause = ROOT_CAUSE_INDEX  # 未知/缺失根因回退默认场景(V1.0 兼容)
    fix = FixRegistry.resolve(root_cause)
    if root_cause == ROOT_CAUSE_INDEX:
        parameters = {"index_name": fix.index_name, "table": fix.table_ref,
                      "columns": fix.columns, "action": "CREATE_INDEX"}
        relation_hash = ""
    else:
        parameters = _extract_lock_parameters(state)
        relation_hash = build_relation_hash({
            "incident_id": state.get("incident_id"),
            "agent_run_id": state.get("run_id"),
            "blocking_transaction_id": parameters.get("blocking_transaction_id"),
            "blocking_processlist_id": parameters.get("blocking_processlist_id"),
            "blocking_lock_ref": parameters.get("blocking_lock_ref"),
            "waiting_transaction_id": parameters.get("waiting_transaction_id"),
            "waiting_query_ref": parameters.get("waiting_query_ref"),
            "locked_schema": parameters.get("locked_schema"),
            "locked_table": parameters.get("locked_table"),
            "locked_index": parameters.get("locked_index"),
        })
    return {
        "action_type": fix.action_type,
        "risk_level": fix.risk_level,
        "parameters": parameters,
        "parameters_hash": _sha256(parameters),
        "blocking_relation_hash": relation_hash,
        "reason": fix.reason_template,
    }
