"""V2.0-B:Capability Registry 行为测试(先于实现编写;冻结既有诊断语义)。

等价性基准 = V2.0-A 的 facts.py/policies.py 行为(抽取后必须逐项一致):
- extract_facts ≡ facts.evaluate_facts
- evaluate_policies ≡ policies.evaluate_policies(键沿用 scn001/scn002)
- decide_root_cause ≡ policies.decide_root_cause(全部状态×排除组合)
- 评估器归属、恢复策略路由、重复 code / 未知 code 的注册表行为。
"""
import pytest

from app.agent import facts as facts_compat
from app.agent import policies as policies_compat
from app.capabilities.registry import (CapabilityRegistry,
                                       DuplicateCapabilityError,
                                       InvalidCapabilityError,
                                       UnknownCapabilityError, registry)


# ---------- 注册表基础行为 ----------

def test_builtin_capabilities_registered():
    codes = {c.code for c in registry.all()}
    assert {"mysql_missing_index", "mysql_blocking_transaction"} <= codes


def test_duplicate_capability_code_rejected():
    fresh = CapabilityRegistry()
    cap = registry.get("mysql_missing_index")
    fresh.register(cap)
    with pytest.raises(DuplicateCapabilityError):
        fresh.register(registry.get("mysql_missing_index"))


def test_unknown_capability_code_rejected():
    with pytest.raises(UnknownCapabilityError):
        registry.get("mysql_cache_eviction")


# ---------- extract_facts ≡ 旧 facts.evaluate_facts ----------

@pytest.mark.parametrize("evidence_map", [
    {},
    {"e1": {"passed": True}, "e2": {"passed": True}},
    {"e1": {"passed": True}, "e2": {"passed": True}, "e3": {"passed": False},
     "e4": {"passed": False}, "e5": {"passed": False}},
    {"l1": {"passed": True}, "l2": {"passed": True}},
    {"l1": {"passed": True}, "l2": {"passed": False}},
    {"l1": {"passed": True}},
])
def test_extract_facts_equivalent_to_legacy(evidence_map):
    assert registry.extract_facts(evidence_map) == facts_compat.evaluate_facts(evidence_map)


# ---------- evaluate_policies ≡ 旧 policies.evaluate_policies ----------

@pytest.mark.parametrize("facts", [
    {},
    {"F_ENDPOINT_DEGRADED": True, "F_DB_STAGE_DOMINANT": True,
     "F_TARGET_QUERY_EXPENSIVE": True, "F_PLAN_FULL_SCAN": True, "F_INDEX_MISSING": True},
    {"F_ENDPOINT_DEGRADED": True, "F_TARGET_LOCK_WAIT": False},
    {"F_TARGET_LOCK_WAIT": True, "F_BLOCKER_LONG_RUNNING": True, "F_BLOCKER_CONFIRMED": True},
])
def test_evaluate_policies_equivalent_to_legacy(facts):
    assert registry.evaluate_policies(facts) == policies_compat.evaluate_policies(facts)


def test_policy_keys_unchanged_for_state_compatibility():
    facts = {"F_ENDPOINT_DEGRADED": True, "F_DB_STAGE_DOMINANT": True,
             "F_TARGET_QUERY_EXPENSIVE": True, "F_PLAN_FULL_SCAN": True,
             "F_INDEX_MISSING": True, "F_TARGET_LOCK_WAIT": False}
    pol = registry.evaluate_policies(facts)
    assert set(pol) == {"scn001", "scn002"}  # state/快照契约不变
    assert pol == {"scn001": "confirmed", "scn002": "refuted"}


# ---------- decide_root_cause ≡ 旧四分支判定(全组合冻结期望) ----------

_ROOT_INDEX = policies_compat.ROOT_CAUSE_INDEX
_ROOT_LOCK = policies_compat.ROOT_CAUSE_LOCK

_CASES = []
for s1 in ("confirmed", "refuted", "unknown"):
    for s2 in ("confirmed", "refuted", "unknown"):
        for idx_normal in (True, False):
            for lock_absent in (True, False):
                pol = {"scn001": s1, "scn002": s2}
                excl = {"x_index_normal": idx_normal, "x_no_target_lock_wait": lock_absent}
                if s1 == "confirmed" and s2 == "confirmed":
                    expected = (None, "multiple_confirmed_causes")
                elif s1 == "confirmed" and s2 == "refuted" and lock_absent:
                    expected = (_ROOT_INDEX, None)
                elif s2 == "confirmed" and s1 == "refuted" and idx_normal:
                    expected = (_ROOT_LOCK, None)
                else:
                    expected = (None, None)
                _CASES.append(pytest.param(pol, excl, expected,
                                           id=f"{s1}-{s2}-idx{idx_normal}-lock{lock_absent}"))


@pytest.mark.parametrize("pol,excl,expected", _CASES)
def test_decide_root_cause_frozen_equivalence(pol, excl, expected):
    assert registry.decide_root_cause(pol, excl) == expected
    # 兼容层委托 registry,行为一致
    assert policies_compat.decide_root_cause(pol, excl) == expected


# ---------- 评估器归属与恢复策略 ----------

def test_evaluator_ownership_by_tool():
    for tool, owner in (("get_service_metrics", "mysql_missing_index"),
                        ("get_trace", "mysql_missing_index"),
                        ("list_expensive_query_digests", "mysql_missing_index"),
                        ("get_query_plan", "mysql_missing_index"),
                        ("get_index_info", "mysql_missing_index"),
                        ("get_lock_waiters", "mysql_blocking_transaction"),
                        ("get_transaction_details", "mysql_blocking_transaction")):
        assert registry.evaluator_for(tool) is not None
        assert registry.evaluator_owner(tool) == owner


def test_recovery_verifier_routing():
    verifier = registry.recovery_verifier_for(
        "LONG_RUNNING_TRANSACTION_BLOCKING_INVENTORY_RESERVATION")
    assert callable(verifier)          # 锁根因 → 目标范围六项验证
    assert registry.recovery_verifier_for("MISSING_INVENTORY_INDEX") is None


def test_capabilities_declare_contract_fields():
    mi = registry.get("mysql_missing_index")
    assert mi.root_cause_code == "MISSING_INVENTORY_INDEX"
    assert mi.policy_key == "scn001"
    assert set(mi.required_fact_codes) == {
        "F_ENDPOINT_DEGRADED", "F_DB_STAGE_DOMINANT", "F_TARGET_QUERY_EXPENSIVE",
        "F_PLAN_FULL_SCAN", "F_INDEX_MISSING"}
    bt = registry.get("mysql_blocking_transaction")
    assert bt.root_cause_code == "LONG_RUNNING_TRANSACTION_BLOCKING_INVENTORY_RESERVATION"
    assert bt.policy_key == "scn002"
    assert set(bt.required_fact_codes) == {
        "F_TARGET_LOCK_WAIT", "F_BLOCKER_CONFIRMED", "F_BLOCKER_LONG_RUNNING"}


def test_dual_conflict_via_registry_from_facts():
    """双根因冲突:两组必需 Fact 同时全部成立 → multiple_confirmed_causes(不产处置)。"""
    facts = {"F_ENDPOINT_DEGRADED": True, "F_DB_STAGE_DOMINANT": True,
             "F_TARGET_QUERY_EXPENSIVE": True, "F_PLAN_FULL_SCAN": True,
             "F_INDEX_MISSING": True,
             "F_TARGET_LOCK_WAIT": True, "F_BLOCKER_LONG_RUNNING": True,
             "F_BLOCKER_CONFIRMED": True}
    pol = registry.evaluate_policies(facts)
    root, reason = registry.decide_root_cause(pol, registry.evaluate_exclusions(facts))
    assert root is None and reason == "multiple_confirmed_causes"


# ---------- V2.0-B closure:架构约束(capabilities 禁止依赖 deprecated 兼容层) ----------

def test_capabilities_do_not_import_deprecated_agent_modules():
    """capabilities 包不得导入 app.agent.policies / app.agent.facts,
    确保兼容层未来可直接删除(依赖方向:agent → capabilities)。"""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app" / "capabilities"
    banned = {"app.agent.policies", "app.agent.facts"}
    bad = []
    for py in root.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                bad += [a.name for a in node.names if a.name in banned]
            elif isinstance(node, ast.ImportFrom) and node.module in banned:
                bad.append(node.module)
    assert not bad, f"capabilities 反向导入 deprecated 模块: {bad}"


def test_root_cause_codes_single_source():
    """根因代码权威定义在 capabilities.codes;agent.policies 仅重导出同一对象。"""
    from app.capabilities import codes
    from app.agent import policies
    assert policies.ROOT_CAUSE_INDEX is codes.ROOT_CAUSE_INDEX
    assert policies.ROOT_CAUSE_LOCK is codes.ROOT_CAUSE_LOCK
    assert registry.get("mysql_missing_index").root_cause_code is codes.ROOT_CAUSE_INDEX


def _stub_capability(code, *, policy_key=None, root_cause=None, exclusion_key=None,
                     tools=(), evaluators=None):
    """最小注册约束测试替身(不触碰真实诊断知识)。"""
    from app.capabilities.base import DiagnosticCapability

    class _Stub(DiagnosticCapability):
        def extract_facts(self, evidence_map):
            return {}

        def exclusion(self, facts):
            return False

    cap = _Stub()
    cap.code = code
    # 默认值按 code 派生,避免无关字段在重复性测试中先行碰撞
    cap.policy_key = policy_key or f"pk_{code}"
    cap.root_cause_code = root_cause or f"rc_{code}"
    cap.exclusion_key = exclusion_key or f"xk_{code}"
    cap.tool_names = tuple(tools)
    cap._evaluators = evaluators or {}
    return cap


def test_register_rejects_duplicate_policy_key():
    fresh = CapabilityRegistry()
    fresh.register(_stub_capability("a", policy_key="scn001"))
    with pytest.raises(DuplicateCapabilityError, match="policy_key 重复"):
        fresh.register(_stub_capability("b", policy_key="scn001"))


def test_register_rejects_duplicate_root_cause_code():
    fresh = CapabilityRegistry()
    fresh.register(_stub_capability("a", root_cause="R1"))
    with pytest.raises(DuplicateCapabilityError, match="root_cause_code 重复"):
        fresh.register(_stub_capability("b", root_cause="R1"))


def test_register_rejects_duplicate_exclusion_key():
    fresh = CapabilityRegistry()
    fresh.register(_stub_capability("a", exclusion_key="x_dup"))
    with pytest.raises(DuplicateCapabilityError, match="exclusion_key 重复"):
        fresh.register(_stub_capability("b", exclusion_key="x_dup"))


def test_register_rejects_duplicate_tool_evaluator():
    """同一工具评估器不得被后注册 Capability 静默覆盖(V2.0-B 最小方案)。"""
    fresh = CapabilityRegistry()
    fresh.register(_stub_capability("a", tools=("get_trace",),
                                    evaluators={"get_trace": lambda r, s: []}))
    with pytest.raises(DuplicateCapabilityError, match="工具评估器重复注册"):
        fresh.register(_stub_capability("b", tools=("get_trace",),
                                        evaluators={"get_trace": lambda r, s: []}))


def test_register_rejects_tool_without_callable_evaluator():
    fresh = CapabilityRegistry()
    with pytest.raises(InvalidCapabilityError, match="缺少可调用评估器"):
        fresh.register(_stub_capability("a", tools=("get_trace",), evaluators={}))
    with pytest.raises(InvalidCapabilityError, match="缺少可调用评估器"):
        fresh.register(_stub_capability("a", tools=("get_trace",),
                                        evaluators={"get_trace": "not-callable"}))
