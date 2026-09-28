"""V2.1-C:Preflight —— 写操作执行前的共享复核(建索引与 KILL 两条写路径共用)。

职责:在**真正产生副作用之前**,用当前实测状态复核"这次写操作是否仍然必要"。

判定依据(只用**当前前置条件**,不用时间戳):
- 当前是否已自愈:与告警同口径的 HTTP P95 已恢复 → 拒绝(`PREFLIGHT_ALREADY_RECOVERED`),
  零写操作 + 转人工;
- 目标索引是否已存在:属于**既有 no_op 幂等路径**(调用方在 Preflight 之前处理),
  Preflight 不得把它变成拒绝 —— 已存在即"无需写",语义仍是安全 no_op;
- 锁等待关系是否仍成立:由 KILL 执行器既有的 8 项重查判定(evidence_stale 等)。

明确不做的事(用户裁决):**不因 `last_seen_at` 晚于 Proposal 就拒绝** ——
新的 FIRING 只意味着需要重新取证,不等于 Proposal 失效;是否可执行只看当前前置条件。
复核结果(告警状态/生命周期/阈值来源/实测 P95/计划)写入返回的 checks 供 Replay 与坐席审计。
"""
import logging
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.services import recovery_signal

logger = logging.getLogger(__name__)

REASON_ALREADY_RECOVERED = "PREFLIGHT_ALREADY_RECOVERED"
REASON_INDEX_PRESENT = "PREFLIGHT_INDEX_ALREADY_PRESENT"
REASON_INCIDENT_MISSING = "PREFLIGHT_INCIDENT_MISSING"


@dataclass
class PreflightResult:
    ok: bool
    reason: str | None = None
    checks: dict = field(default_factory=dict)


def _incident_snapshot(incident_id: int) -> dict:
    with Session(get_control_engine()) as s:
        row = s.execute(text(
            "SELECT status, alert_status, lifecycle_status, service_ref, "
            "affected_operation_ref, baseline_quality FROM incident WHERE id=:i"),
            {"i": incident_id}).fetchone()
    if row is None:
        return {}
    return {"incident_status": row.status, "alert_status": row.alert_status,
            "lifecycle_status": row.lifecycle_status,
            "service_ref": row.service_ref,
            "affected_operation_ref": row.affected_operation_ref,
            "baseline_quality": row.baseline_quality}


def _frozen_context(agent_run_id: int, incident_id: int) -> tuple[str, str | None, dict | None]:
    """服务/操作/合格基线取冻结 RunContext(与告警同口径;缺失则回退 Incident 行)。"""
    if agent_run_id:
        try:
            from app.repositories import run_repo
            from app.services.run_context import load_snapshot
            run = run_repo.get_run(agent_run_id)
            if run is not None and run.incident_id == incident_id:
                snap = load_snapshot(run)
                return snap.service_ref, snap.affected_operation_ref, \
                    snap.healthy_baseline_ref
        except Exception:  # noqa: BLE001 快照不可用 → 回退 Incident 行(仅用于复核)
            logger.warning("preflight: run %s 冻结上下文不可用,回退 Incident 行",
                           agent_run_id)
    snap = _incident_snapshot(incident_id)
    return snap.get("service_ref") or "", snap.get("affected_operation_ref"), None


def preflight_for_index(incident_id: int, baseline: dict | None,
                        agent_run_id: int = 0) -> PreflightResult:
    """建索引写路径复核(在索引不存在、幂等未命中之后调用)。

    调用方保证:索引已存在 / 幂等键已成功 → 走 no_op,不进入本函数。
    本函数只判定"当前是否已自愈"(已恢复 → 拒绝)。
    """
    snap = _incident_snapshot(incident_id)
    if not snap:
        return PreflightResult(ok=False, reason=REASON_INCIDENT_MISSING, checks={})
    service_ref, operation_ref, frozen_baseline = _frozen_context(agent_run_id,
                                                                 incident_id)
    effective_baseline = baseline if isinstance(baseline, dict) else frozen_baseline
    signal = recovery_signal.measure_current_p95(service_ref, operation_ref,
                                                baseline=effective_baseline)
    checks = {**snap, "p95_ms": signal.p95_ms,
              "threshold_ms": signal.threshold_ms,
              "threshold_source": signal.source,
              "sample_count": signal.sample_count,
              "signal_status": signal.status,
              "post_signal_window": signal.post_signal_window}
    if signal.status == recovery_signal.STATUS_RECOVERED:
        logger.warning("preflight 拒绝写操作 incident=%s:当前已自愈(p95=%s ≤ %s,来源=%s)",
                       incident_id, signal.p95_ms, signal.threshold_ms, signal.source)
        return PreflightResult(ok=False, reason=REASON_ALREADY_RECOVERED,
                               checks=checks)
    if signal.status == recovery_signal.STATUS_INCONCLUSIVE:
        # 无法判定是否已恢复 → 不因此拒绝(缺少观测不代表故障仍在,执行安全由既有
        # 审批/幂等/前置检查保证);但把 INCONCLUSIVE 记入审计供坐席判断
        logger.warning("preflight 复核无法判定 incident=%s(reason=%s),按前置条件继续",
                       incident_id, signal.reason)
    return PreflightResult(ok=True, reason=None, checks=checks)


def preflight_for_kill(incident_id: int, baseline: dict | None,
                       agent_run_id: int = 0) -> PreflightResult:
    """KILL 写路径复核(在既有 8 项重查之前调用;语义与索引路径一致)。"""
    return preflight_for_index(incident_id, baseline, agent_run_id=agent_run_id)
