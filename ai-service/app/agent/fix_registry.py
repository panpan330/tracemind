"""FixRegistry:修复动作唯一执行权威(代码内)。数据库 fix_definition 仅为展示投影。"""
import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class FixActionDefinition:
    action_type: str
    table_ref: str
    index_name: str
    columns: list[str]
    risk_level: str
    reason_template: str


_FIXES = {
    "MISSING_INVENTORY_INDEX": FixActionDefinition(
        action_type="CREATE_INVENTORY_INDEX",
        table_ref="inventory",
        index_name="idx_sku_warehouse",
        columns=["sku_id", "warehouse_id"],
        risk_level="medium",
        reason_template=("已通过 E1~E5 证据链确认库存查询缺少 idx_sku_warehouse(sku_id, warehouse_id),"
                         "建议执行预定义索引创建操作。"),
    ),
}


class FixRegistry:
    @staticmethod
    def resolve(root_cause: str) -> FixActionDefinition:
        return _FIXES[root_cause]


def _sha256(parameters: dict) -> str:
    blob = json.dumps(parameters, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_proposal(state: dict) -> dict:
    fix = FixRegistry.resolve("MISSING_INVENTORY_INDEX")
    parameters = {
        "index_name": fix.index_name,
        "table": fix.table_ref,
        "columns": fix.columns,
        "action": "CREATE_INDEX",
    }
    return {
        "action_type": fix.action_type,
        "risk_level": fix.risk_level,
        "parameters": parameters,
        "parameters_hash": _sha256(parameters),
        "reason": fix.reason_template,
    }
