"""双 DiagnosticPolicy(V1.3 设计;状态词表 confirmed/refuted/unknown)。

.. deprecated:: V2.0-B
   诊断 Policy 已迁入 app.capabilities(mysql_missing_index / mysql_blocking_transaction)。
   本模块保留兼容导出(常量 + 评估函数委托 Registry);待参数化 Playbook 版本完成后删除。
   新代码请使用 app.capabilities.registry。
"""

ROOT_CAUSE_INDEX = "MISSING_INVENTORY_INDEX"
ROOT_CAUSE_LOCK = "LONG_RUNNING_TRANSACTION_BLOCKING_INVENTORY_RESERVATION"

POLICY_SCN001 = ("F_ENDPOINT_DEGRADED", "F_DB_STAGE_DOMINANT",
                 "F_TARGET_QUERY_EXPENSIVE", "F_PLAN_FULL_SCAN", "F_INDEX_MISSING")
# SCN-002 只依赖锁链路事实(与设计 V1.3 §4.2 一致);F_ENDPOINT_DEGRADED 属于 SCN-001,
# fixture 观测下 E1 恒健康会错误 refuted 锁场景
POLICY_SCN002 = ("F_TARGET_LOCK_WAIT", "F_BLOCKER_CONFIRMED", "F_BLOCKER_LONG_RUNNING")


def evaluate_policies(facts_dict: dict[str, bool]) -> dict[str, str]:
    """{policy_key: confirmed|refuted|unknown};行为 ≡ registry.evaluate_policies(委托)。"""
    from app.capabilities import registry
    return registry.evaluate_policies(facts_dict)


def evaluate_exclusions(facts_dict: dict[str, bool]) -> dict[str, bool]:
    """自动处置排他条件(非正向证据);行为 ≡ registry.evaluate_exclusions(委托)。"""
    from app.capabilities import registry
    return registry.evaluate_exclusions(facts_dict)


def decide_root_cause(pol: dict[str, str],
                      exclusions: dict[str, bool]) -> tuple[str | None, str | None]:
    """四分支裁决;行为 ≡ registry.decide_root_cause(委托)。"""
    from app.capabilities import registry
    return registry.decide_root_cause(pol, exclusions)
