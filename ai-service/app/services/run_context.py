"""V2.0-A Run 上下文冻结。

Run 创建事务内生成不可变 RunContextSnapshot;恢复/续跑只读取该快照,
不从后来已变化的 Incident 行重新推导关键上下文(service/operation/基线/版本)。
快照缺失或不完整 → fail closed(不用默认 service/operation 补齐)。
"""
from datetime import datetime, timezone

from typing import Optional

from pydantic import BaseModel

SNAPSHOT_SCHEMA_VERSION = "1.0"
# 受支持的快照 schema 版本(只增不删;旧版本一律 fail closed,不静默升级语义)
SUPPORTED_SNAPSHOT_SCHEMA_VERSIONS = frozenset({"1.0"})


class RunContextMissing(RuntimeError):
    """快照缺失(如 009 迁移前创建的旧 Run)→ fail closed。"""


class RunContextInvalid(RuntimeError):
    """快照不完整 / 校验失败 → fail closed。"""


class ResumeBlocked(RuntimeError):
    """V2.0-A closure:恢复前统一校验失败 → 禁止恢复。reason 为短码(≤64,入 termination_reason)。"""

    def __init__(self, reason: str, detail: str):
        super().__init__(f"{reason}: {detail}")
        self.reason = reason


class RunContextSnapshot(BaseModel):
    schema_version: str
    incident_id: int
    agent_run_id: int
    incident_title: str
    service_ref: str                      # 必填:缺失即拒绝创建(无默认兜底)
    severity: str
    affected_service_ref: Optional[str] = None
    affected_operation_ref: Optional[str] = None
    observed_at: Optional[str] = None     # Incident 观测时间(调查窗口起点参考)
    investigation_window: dict = {}
    baseline_ref: Optional[dict] = None           # Run 级 digest 基线(创建时采集)
    healthy_baseline_ref: Optional[dict] = None   # incident.healthy_metrics_baseline
    checkpoint: dict                              # {"thread_id", "namespace"}
    bundle_versions: dict                         # {"capability","policy","prompt","tool"}
    frozen_at: str


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_snapshot(incident, agent_run_id: int, thread_id: str,
                   baseline: Optional[dict], bundle_versions: dict) -> dict:
    """从 Incident 行构建快照(仅在 Run 创建事务内调用一次)。
    service_ref 缺失 → ValueError(fail closed,禁止 'inventory-service' 类默认值)。"""
    service_ref = (incident.service_ref or "").strip()
    if not service_ref:
        raise ValueError(
            "service_ref missing: RunContextSnapshot 要求明确 service(拒绝默认值, fail closed)")
    frozen_at = _utcnow_iso()
    return RunContextSnapshot(
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        incident_id=incident.id,
        agent_run_id=agent_run_id,
        incident_title=incident.title or "",
        service_ref=service_ref,
        severity=incident.severity or "medium",
        affected_service_ref=incident.affected_service_ref,
        affected_operation_ref=incident.affected_operation_ref,
        observed_at=str(incident.observed_at) if incident.observed_at else None,
        investigation_window={"window_start": frozen_at, "window_end": None},
        baseline_ref=baseline,
        healthy_baseline_ref=incident.healthy_metrics_baseline,
        checkpoint={"thread_id": thread_id, "namespace": ""},
        bundle_versions=dict(bundle_versions),
        frozen_at=frozen_at,
    ).model_dump()


def load_snapshot(run) -> RunContextSnapshot:
    """读取并校验 Run 的冻结快照;缺失/不完整/与 Run 绑定不一致 → fail closed。"""
    data = getattr(run, "run_context_snapshot_json", None)
    run_id = getattr(run, "id", None)
    if not data:
        raise RunContextMissing(f"agent_run {run_id} 缺少 RunContextSnapshot(fail closed)")
    try:
        snap = RunContextSnapshot.model_validate(data)
    except Exception as exc:  # noqa: BLE001 pydantic 校验失败统一转 fail closed
        raise RunContextInvalid(f"agent_run {run_id} 快照不完整或非法: {exc}") from exc
    if not snap.service_ref:
        raise RunContextInvalid(f"agent_run {run_id} 快照缺 service_ref")
    ck = snap.checkpoint or {}
    if ck.get("thread_id") != getattr(run, "checkpoint_thread_id", None) \
            or ck.get("thread_id") != getattr(run, "thread_id", None):
        raise RunContextInvalid(
            f"agent_run {run_id} 快照 checkpoint 与 Run thread 绑定不一致")
    return snap


def validate_run_for_resume(run, *, current_bundle_versions: dict) -> RunContextSnapshot:
    """V2.0-A closure:恢复/启动前统一校验(fail closed),任一失败 → ResumeBlocked。
    校验序:快照存在且合法 → schema_version 受支持 → incident/agent_run/thread/namespace
    与 Run 一致 → Capability/Policy/Prompt/Tool 四类冻结版本与当前可执行版本一致。"""
    run_id = getattr(run, "id", None)
    data = getattr(run, "run_context_snapshot_json", None)
    if not data:
        raise ResumeBlocked("context_snapshot_missing",
                            f"agent_run {run_id} 缺少 RunContextSnapshot")
    try:
        snap = RunContextSnapshot.model_validate(data)
    except Exception as exc:  # noqa: BLE001
        raise ResumeBlocked("context_snapshot_invalid",
                            f"agent_run {run_id} 快照非法: {exc}") from exc
    if snap.schema_version not in SUPPORTED_SNAPSHOT_SCHEMA_VERSIONS:
        raise ResumeBlocked(
            "snapshot_schema_unsupported",
            f"agent_run {run_id} 快照 schema_version={snap.schema_version!r} 不受支持")
    if snap.incident_id != getattr(run, "incident_id", None):
        raise ResumeBlocked("snapshot_incident_mismatch",
                            f"agent_run {run_id} 快照 incident_id={snap.incident_id} 与 Run 不一致")
    if snap.agent_run_id != run_id:
        raise ResumeBlocked("snapshot_run_mismatch",
                            f"agent_run {run_id} 快照 agent_run_id={snap.agent_run_id} 与 Run 不一致")
    ck = snap.checkpoint or {}
    if ck.get("thread_id") != getattr(run, "thread_id", None) \
            or ck.get("thread_id") != getattr(run, "checkpoint_thread_id", None) \
            or ck.get("namespace") != getattr(run, "checkpoint_namespace", None):
        raise ResumeBlocked(
            "snapshot_checkpoint_mismatch",
            f"agent_run {run_id} 快照 checkpoint={ck} 与 Run thread/namespace 绑定不一致")
    frozen = snap.bundle_versions or {}
    for kind, code in (("policy", "version_mismatch"),
                       ("capability", "capability_version_mismatch"),
                       ("prompt", "prompt_version_mismatch"),
                       ("tool", "tool_version_mismatch")):
        want = current_bundle_versions.get(kind)
        if frozen.get(kind) != want:
            raise ResumeBlocked(
                code, f"agent_run {run_id} 冻结 {kind} 版本 {frozen.get(kind)!r} "
                      f"≠ 当前可执行 {want!r}")
        col = getattr(run, f"{kind}_bundle_version", None)
        if col is not None and col != want:
            raise ResumeBlocked(
                code, f"agent_run {run_id} 列 {kind}_bundle_version={col!r} "
                      f"≠ 当前可执行 {want!r}")
    if not snap.service_ref:
        raise ResumeBlocked("context_snapshot_invalid",
                            f"agent_run {run_id} 快照缺 service_ref")
    return snap
