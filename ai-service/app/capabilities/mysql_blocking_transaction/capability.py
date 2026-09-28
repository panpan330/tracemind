"""mysql_blocking_transaction Capability:长事务阻塞场景的诊断知识(V2.0-B 原文迁入)。

拥有:L1~L2 证据评估器、锁 Fact 抽取(含 F_BLOCKER_CONFIRMED 复合)、SCN-002 诊断
Policy、排他条件(x_no_target_lock_wait)、目标范围锁恢复验证。
不负责修复执行,不接触写账号;LLM 不参与判定。
"""
import logging
import time

from app.config import settings
from app.repositories import event_repo

from app.capabilities.codes import ROOT_CAUSE_LOCK
from app.capabilities.base import DiagnosticCapability, RECOVERY_TARGET_SCOPE_LOCK

logger = logging.getLogger(__name__)

# 长事务阈值(ms)与锁等待阈值(ms):评估器判定使用
LONG_TRANSACTION_THRESHOLD_MS = 5000
LOCK_WAIT_THRESHOLD_MS = 3000


def evaluate_lock_waiters(result: dict, state: dict) -> list[dict]:
    """L1:目标 inventory 记录上的锁等待(等待语句匹配库存预占,wait_duration ≥ 3s)。
    锁场景(INVENTORY_RESERVATION):锁等待未达阈值是暂态(等待累积中),触发重采。"""
    data = result.get("data") or {}
    waits = data.get("waits") or []
    target = [w for w in waits
              if w.get("object_schema") == "tracemind_business"
              and w.get("object_table") == "inventory"
              and w.get("waiting_query_ref") == "INVENTORY_RESERVATION"]
    op = state.get("affected_operation_ref") or ""
    reached = any((w.get("wait_duration_ms") or 0) >= 3000 for w in target)
    if reached:
        passed = True
    elif op == "INVENTORY_RESERVATION":
        # 锁场景:锁等待未达阈值(尚未产生或等待累积中)是暂态,触发重采
        return []
    else:
        # 慢查询场景:无目标锁等待是确定性否定
        passed = False
    return [{"id": "L1", "key": "l1", "source": "get_lock_waiters",
             "content": data, "passed": passed}]


def evaluate_transaction_details(result: dict, state: dict) -> list[dict]:
    """L2:阻塞事务详情(复合匹配见 facts/policies;此处只判定存在长事务)。
    锁场景(INVENTORY_RESERVATION):事务年龄未达阈值是暂态(累积中),触发重采。"""
    data = result.get("data") or {}
    op = state.get("affected_operation_ref") or ""
    has_trx = bool(data.get("transaction_id"))
    age_ok = (data.get("age_ms") or 0) >= 5000
    if has_trx and age_ok:
        passed = True
    elif op == "INVENTORY_RESERVATION" and not age_ok:
        # 锁场景:阻塞事务年龄未达阈值(累积中)是暂态,触发重采
        return []
    else:
        passed = False
    return [{"id": "L2", "key": "l2", "source": "get_transaction_details",
             "content": data, "passed": passed}]


def _poll_target_lock_gone(deadline_s: int = 60) -> bool:
    """轮询目标锁等待关系是否消失(≤deadline_s)。"""
    import time
    from app.tools import lock_queries
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        r = lock_queries.get_lock_waiters("tracemind_business", "inventory", 3000)
        waits = (r.get("data") or {}).get("waits") or []
        target = [w for w in waits
                  if w.get("object_schema") == "tracemind_business"
                  and w.get("object_table") == "inventory"
                  and w.get("waiting_query_ref") == "INVENTORY_RESERVATION"]
        if not target:
            return True
        time.sleep(5)
    return False


def _finish_lock_verification(state: dict, *, status: str, termination_reason,
                              extra: dict) -> dict:
    state["recovery"] = {"status": status, "termination_reason": termination_reason,
                         **extra}
    state["status"] = status
    event_repo.append_event(state["incident_id"], "status_changed",
                            {"status": state.get("status")})
    return state


def verify_lock_recovery(state: dict) -> dict:
    """锁根因恢复验证(六项目标范围,设计 §6)。

    V2.1-C:锁证据消失 + 三批探测通过后,必须再通过**与通用验证器相同的 HTTP P95
    恢复信号**(recovery_signal,服务/操作取冻结上下文,窗口位于修复完成之后);
    信号未恢复 → needs_human(recovery_p95_not_recovered);
    INCONCLUSIVE → needs_human(recovery_inconclusive)。"""
    from app.services import recovery_signal

    if not _poll_target_lock_gone(60):
        return _finish_lock_verification(
            state, status="needs_human", termination_reason="recovery_timeout",
            extra={})
    # 目标关系已消失:连续三批库存预占探测(复用 order check-stock 探测逻辑)
    probes = run_probe_batches(state, batches=3)
    if not all(p.get("success") for p in probes):
        return _finish_lock_verification(
            state, status="needs_human", termination_reason="recovery_probe_failed",
            extra={"probes": probes})
    # 统一恢复信号(与通用验证器同口径);信号时刻无法证明 → 转人工,不伪造
    signal_at = _fix_completed_at(state)
    if signal_at is None:
        return _finish_lock_verification(
            state, status="needs_human",
            termination_reason="recovery_signal_time_unknown",
            extra={"probes": probes})
    try:
        signal = recovery_signal.measure_post_signal_p95(
            state.get("service_ref") or "", state.get("affected_operation_ref"),
            signal_at, baseline=state.get("healthy_baseline_ref") or None)
    except ValueError:
        # 未注册 service/operation(配置错误)→ 不崩溃,转人工(fail closed)
        logger.exception("锁恢复信号口径错误 incident=%s", state.get("incident_id"))
        return _finish_lock_verification(
            state, status="needs_human", termination_reason="recovery_inconclusive",
            extra={"probes": probes,
                   "recoverySignal": {"reason": "metrics_result_invalid"}})
    if signal.status == recovery_signal.STATUS_RECOVERED:
        return _finish_lock_verification(
            state, status="recovered", termination_reason=None,
            extra={"probes": probes, "recoverySignal": signal.as_dict()})
    if signal.status == recovery_signal.STATUS_NOT_RECOVERED:
        return _finish_lock_verification(
            state, status="needs_human",
            termination_reason="recovery_p95_not_recovered",
            extra={"probes": probes, "recoverySignal": signal.as_dict()})
    return _finish_lock_verification(
        state, status="needs_human", termination_reason="recovery_inconclusive",
        extra={"probes": probes, "recoverySignal": signal.as_dict()})


def _fix_completed_at(state: dict):
    """恢复信号时刻 = 修复动作完成时刻。

    优先取 state 显式注入(测试/调用方);否则查 fix_execution 最近一次
    succeeded/no_op 的 finished_at(真实写库完成时刻)。两者都缺失 → None
    (调用方转人工,绝不能回退"当前时间"——那会让"信号后窗口"永不满足,
    也会把信号前的流量当作恢复证据)。"""
    from datetime import datetime

    from sqlalchemy import text as _text
    from sqlalchemy.orm import Session as _Session

    from app.db.engine import get_control_engine as _engine

    raw = (state.get("fix_execution") or {}).get("created_at")
    if raw:
        try:
            return datetime.fromisoformat(str(raw))
        except ValueError:
            pass
    with _Session(_engine()) as s:
        return s.execute(_text(
            "SELECT created_at FROM fix_execution "
            "WHERE incident_id = :i AND status IN ('succeeded','no_op') "
            "ORDER BY id DESC LIMIT 1"), {"i": state.get("incident_id")}).scalar()


def run_probe_batches(state: dict, batches: int = 3) -> list[dict]:
    """三批固定探测请求(与健康基线采集相同参数),每批记录 success。"""
    import httpx
    probes = []
    order_url = order_service_base()
    for _ in range(batches):
        try:
            resp = httpx.post(
                f"{order_url}/api/orders/1/check-stock",
                json={"skuId": 42, "warehouseId": 7, "quantity": 1}, timeout=10)
            probes.append({"success": resp.status_code < 500})
        except Exception:  # noqa: BLE001
            probes.append({"success": False})
    return probes


def order_service_base() -> str:
    return settings.order_service_url


class BlockingTransactionCapability(DiagnosticCapability):
    code = "mysql_blocking_transaction"
    policy_key = "scn002"          # state["policy"] 兼容键(冻结)
    version = "1.0"
    root_cause_code = ROOT_CAUSE_LOCK
    required_fact_codes = ("F_TARGET_LOCK_WAIT", "F_BLOCKER_CONFIRMED", "F_BLOCKER_LONG_RUNNING")
    exclusion_key = "x_no_target_lock_wait"
    recovery_strategy = RECOVERY_TARGET_SCOPE_LOCK
    tool_names = ("get_lock_waiters", "get_transaction_details")

    def __init__(self):
        self._evaluators = {
            "get_lock_waiters": evaluate_lock_waiters,
            "get_transaction_details": evaluate_transaction_details,
        }

    def extract_facts(self, evidence_map: dict) -> dict[str, bool]:
        out: dict[str, bool] = {}
        for ev_key, fact in (("l1", "F_TARGET_LOCK_WAIT"), ("l2", "F_BLOCKER_LONG_RUNNING")):
            ev = evidence_map.get(ev_key)
            if ev is not None:
                passed = ev.get("passed")
                if passed is None:
                    continue   # unknown 不输出
                out[fact] = bool(passed)
        # F_BLOCKER_CONFIRMED:目标锁等待 + 阻塞事务详情复合成立(评估器 L1/L2 各自判定)
        if evidence_map.get("l1") is not None and evidence_map.get("l2") is not None:
            p1, p2 = evidence_map["l1"].get("passed"), evidence_map["l2"].get("passed")
            if p1 is not None and p2 is not None:
                out["F_BLOCKER_CONFIRMED"] = bool(p1 and p2)
        return out

    def exclusion(self, facts: dict[str, bool]) -> bool:
        """锁正向证据确定性缺失:目标锁等待已采集且为 False。"""
        return facts.get("F_TARGET_LOCK_WAIT") is False

    def recovery_verifier(self):
        return verify_lock_recovery
