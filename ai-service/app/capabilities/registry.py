"""V2.0-B:Capability Registry —— 场景知识的唯一消费入口。

nodes.py 不再直接判断 scn001/scn002/E1/L1;评估器归属、Fact 抽取、Policy 评估、
排他条件、根因裁决与恢复验证路由全部经本 Registry(委托各 DiagnosticCapability)。
裁决语义与 V2.0-A policies.decide_root_cause 逐项等价(见 test_capability_registry.py)。
"""


class DuplicateCapabilityError(RuntimeError):
    """注册了重复的 capability code。"""


class UnknownCapabilityError(RuntimeError):
    """查询了未注册的 capability code。"""


class InvalidCapabilityError(RuntimeError):
    """Capability 注册约束不满足(如声明工具缺少可调用评估器)。"""


class CapabilityRegistry:
    def __init__(self) -> None:
        self._capabilities: dict[str, "DiagnosticCapability"] = {}
        self._evaluators: dict[str, tuple[str, object]] = {}  # tool → (code, fn)

    def register(self, capability):
        """注册约束(V2.0-B closure):code/policy_key/root_cause_code/exclusion_key
        全局唯一;tool_names 中每个工具必须有可调用评估器,且同一工具评估器不得被
        后注册 Capability 静默覆盖(多消费者模型留待 V2.3 统一设计)。"""
        if capability.code in self._capabilities:
            raise DuplicateCapabilityError(f"capability code 重复: {capability.code}")
        for existing in self._capabilities.values():
            if capability.policy_key == existing.policy_key:
                raise DuplicateCapabilityError(
                    f"policy_key 重复: {capability.policy_key} "
                    f"({existing.code} 已占用)")
            if capability.root_cause_code == existing.root_cause_code:
                raise DuplicateCapabilityError(
                    f"root_cause_code 重复: {capability.root_cause_code} "
                    f"({existing.code} 已占用)")
            if capability.exclusion_key == existing.exclusion_key:
                raise DuplicateCapabilityError(
                    f"exclusion_key 重复: {capability.exclusion_key} "
                    f"({existing.code} 已占用)")
        for tool in capability.tool_names:
            evaluator = capability.evaluator_for_tool(tool)
            if not callable(evaluator):
                raise InvalidCapabilityError(
                    f"capability {capability.code}: 工具 {tool} 缺少可调用评估器")
            if tool in self._evaluators:
                raise DuplicateCapabilityError(
                    f"工具评估器重复注册: {tool}(已属于 {self._evaluators[tool][0]};"
                    f"多消费者模型留待 V2.3)")
        self._capabilities[capability.code] = capability
        for tool in capability.tool_names:
            self._evaluators[tool] = (capability.code, capability.evaluator_for_tool(tool))
        return capability

    def get(self, code: str):
        if code not in self._capabilities:
            raise UnknownCapabilityError(f"未注册的 capability: {code}")
        return self._capabilities[code]

    def all(self) -> list:
        return list(self._capabilities.values())

    # ---- 诊断知识聚合(语义 ≡ 旧 facts/policies)----

    def extract_facts(self, evidence_map: dict) -> dict[str, bool]:
        facts: dict[str, bool] = {}
        for cap in self._capabilities.values():
            facts.update(cap.extract_facts(evidence_map))
        return facts

    def evaluate_policies(self, facts: dict[str, bool]) -> dict[str, str]:
        return {cap.policy_key: cap.evaluate(facts) for cap in self._capabilities.values()}

    def evaluate_exclusions(self, facts: dict[str, bool]) -> dict[str, bool]:
        return {cap.exclusion_key: cap.exclusion(facts) for cap in self._capabilities.values()}

    def decide_root_cause(self, pol: dict[str, str],
                          exclusions: dict[str, bool]) -> tuple[str | None, str | None]:
        """四分支裁决的通用化(≡ 旧实现):双确认冲突;
        恰好一个确认 + 其余全部 refuted + 其余排他条件成立 → 该根因;否则继续收集。"""
        confirmed = [c for c in self._capabilities.values()
                     if pol.get(c.policy_key) == "confirmed"]
        if len(confirmed) > 1:
            return None, "multiple_confirmed_causes"
        for cap in confirmed:
            others = [c for c in self._capabilities.values() if c is not cap]
            if all(pol.get(o.policy_key) == "refuted" for o in others)                     and all(exclusions.get(o.exclusion_key) for o in others):
                return cap.root_cause_code, None
        return None, None

    # ---- 评估器归属与恢复路由 ----

    def evaluator_for(self, tool_name: str):
        entry = self._evaluators.get(tool_name)
        return entry[1] if entry else None

    def evaluator_owner(self, tool_name: str) -> str | None:
        entry = self._evaluators.get(tool_name)
        return entry[0] if entry else None

    def recovery_verifier_for(self, root_cause_code: str):
        for cap in self._capabilities.values():
            if cap.root_cause_code == root_cause_code:
                return cap.recovery_verifier()
        return None


from app.capabilities.base import DiagnosticCapability  # noqa: E402  (类型引用)

registry = CapabilityRegistry()


def _register_builtin() -> None:
    from app.capabilities.mysql_blocking_transaction.capability import (
        BlockingTransactionCapability)
    from app.capabilities.mysql_missing_index.capability import MissingIndexCapability

    registry.register(MissingIndexCapability())
    registry.register(BlockingTransactionCapability())


_register_builtin()
