"""V2.0-B:诊断能力(Capability)最小接口。

设计边界(升级方案 §4.4 + V2.0-B 范围):
- Capability 拥有"场景知识":证据评估器、Fact 抽取、诊断 Policy、排他条件、恢复验证路由;
- 不负责修复执行、不接触写账号、不允许 LLM 参与判定;
- PolicyDecision 的完整数据契约(CONFIRMED/REFUTED/UNKNOWN/STALE/CONFLICTED + EvidenceRecord)
  按方案在 V2.2/V2.3 数据平面版本演进;本版本冻结既有诊断语义(状态词表沿用
  confirmed/refuted/unknown,state["policy"] 键沿用 scn001/scn002)。
"""
from abc import ABC, abstractmethod

STATUS_CONFIRMED = "confirmed"
STATUS_REFUTED = "refuted"
STATUS_UNKNOWN = "unknown"

# 恢复验证路由策略
RECOVERY_TARGET_SCOPE_LOCK = "target_scope_lock"
RECOVERY_VERIFY_TOOL = "verify_tool"


class DiagnosticCapability(ABC):
    """诊断能力基类:一个 Capability 对应一类根因及其全部诊断知识。"""

    code: str                 # 注册标识(如 mysql_missing_index)
    policy_key: str           # state["policy"] 兼容键(V2.0-B 冻结为 scn001/scn002)
    version: str = "1.0"
    root_cause_code: str
    required_fact_codes: tuple[str, ...]   # 全部采集且为 True → confirmed
    exclusion_key: str                     # 排他条件在 state 排除字典中的键(沿用 x_*)
    recovery_strategy: str = RECOVERY_VERIFY_TOOL
    tool_names: tuple[str, ...] = ()       # 本能力的证据评估工具(评估器归属注册)

    @abstractmethod
    def extract_facts(self, evidence_map: dict) -> dict[str, bool]:
        """已采集证据(键 → {"passed": bool, ...})→ 本能力 Fact 片段;未采集键不输出。"""

    def evaluate(self, facts: dict[str, bool]) -> str:
        """既有语义:任一必需 Fact 已知且为 False → refuted;
        全部已知且全 True → confirmed;否则 unknown。"""
        known = [k for k in self.required_fact_codes if k in facts]
        if any(facts.get(k) is False for k in known):
            return STATUS_REFUTED
        if known and len(known) == len(self.required_fact_codes) \
                and all(facts[k] for k in known):
            return STATUS_CONFIRMED
        return STATUS_UNKNOWN

    @abstractmethod
    def exclusion(self, facts: dict[str, bool]) -> bool:
        """本场景正向证据确定性缺失(排他判定;不采集 = False,不允许自动处置)。"""

    def initial_hypothesis(self) -> dict | None:
        """候选假设(Registry 聚合;接入假设生成属 V2.3,本版本不改变现有行为)。"""
        return None

    def recovery_verifier(self):
        """RECOVERY_TARGET_SCOPE_LOCK 策略返回可调用 (state) -> state;其余返回 None。"""
        return None

    def evaluator_for_tool(self, tool_name: str):
        """工具 → 证据评估器 (result, state) -> list[dict];未知工具返回 None。"""
        return getattr(self, "_evaluators", {}).get(tool_name)
