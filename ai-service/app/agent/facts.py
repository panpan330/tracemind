"""共享 Fact 判定(V1.3 设计)。

.. deprecated:: V2.0-B
   场景 Fact 知识已迁入 app.capabilities(mysql_missing_index / mysql_blocking_transaction)。
   本模块保留兼容导出,行为委托 Capability Registry;待参数化 Playbook 版本完成后删除。
   新代码请使用 app.capabilities.registry。
"""

# 长事务阈值(ms)与锁等待阈值(ms):兼容常量(评估器内的权威值随能力模块迁移)
LONG_TRANSACTION_THRESHOLD_MS = 5000
LOCK_WAIT_THRESHOLD_MS = 3000


def evaluate_facts(evidence: dict[str, dict]) -> dict[str, bool]:
    """已采集证据(键 → {"passed": bool})→ 合并 Fact 字典;未采集键不输出。
    行为 ≡ registry.extract_facts(委托)。"""
    from app.capabilities import registry
    return registry.extract_facts(evidence)
