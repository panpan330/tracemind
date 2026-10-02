from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (BigInteger, Boolean, DateTime, ForeignKey, Integer, JSON,
                        String, Text)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Incident(Base):
    __tablename__ = "incident"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(16), default="medium")
    service_ref: Mapped[Optional[str]] = mapped_column(String(64))
    observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # V1.4 非根因上下文:哪个业务接口异常(不代表 scenario/root_cause/Policy)
    affected_service_ref: Mapped[Optional[str]] = mapped_column(String(64))
    affected_operation_ref: Mapped[Optional[str]] = mapped_column(String(64))
    trigger_trace_id: Mapped[Optional[str]] = mapped_column(String(64))
    healthy_metrics_baseline: Mapped[Optional[dict]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="created")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # V1.1 状态属性(degraded 是属性不是主状态)
    termination_reason: Mapped[Optional[str]] = mapped_column(String(64))
    # V2.1-B:告警聚合(三状态分离:alert_status/lifecycle_status 与调查 status 解耦)
    source: Mapped[str] = mapped_column(String(32), default="manual")
    alert_name: Mapped[Optional[str]] = mapped_column(String(128))
    environment: Mapped[Optional[str]] = mapped_column(String(32))
    alert_status: Mapped[Optional[str]] = mapped_column(String(16))
    lifecycle_status: Mapped[Optional[str]] = mapped_column(String(16))
    group_key: Mapped[Optional[str]] = mapped_column(String(64))
    open_group_key: Mapped[Optional[str]] = mapped_column(String(64))
    first_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(3))
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(3))
    occurrence_count: Mapped[int] = mapped_column(Integer, default=0)
    labels_json: Mapped[Optional[dict]] = mapped_column(JSON)
    annotations_json: Mapped[Optional[dict]] = mapped_column(JSON)
    # V2.1-C:历史健康基线(权威来源=baseline_metrics_json,仅 quality='OK' 有效)
    baseline_window_start: Mapped[Optional[datetime]] = mapped_column(DateTime(3))
    baseline_window_end: Mapped[Optional[datetime]] = mapped_column(DateTime(3))
    baseline_metrics_json: Mapped[Optional[dict]] = mapped_column(JSON)
    baseline_quality: Mapped[Optional[str]] = mapped_column(String(32))
    auto_run_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(3))
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(3))
    # 手动路径实时值(当前快照,非健康基线;旧列 healthy_metrics_baseline 停止写入/读取)
    current_health_snapshot_json: Mapped[Optional[dict]] = mapped_column(JSON)
    degraded: Mapped[bool] = mapped_column(default=False)
    degradation_reasons: Mapped[Optional[str]] = mapped_column(String(500))


class AgentRun(Base):
    __tablename__ = "agent_run"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    thread_id: Mapped[str] = mapped_column(String(64), unique=True)
    # V2.0-A:checkpoint 绑定(thread/namespace 与 agent_run 一一对应;唯一约束见 009)
    checkpoint_thread_id: Mapped[Optional[str]] = mapped_column(String(128))
    checkpoint_namespace: Mapped[Optional[str]] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="created")
    investigation_round: Mapped[int] = mapped_column(Integer, default=0)
    tool_call_count: Mapped[int] = mapped_column(Integer, default=0)
    incident_digest_baseline: Mapped[Optional[dict]] = mapped_column(JSON)
    # V2.0-A:Run 创建事务内冻结的不可变上下文快照(恢复只读此快照)
    run_context_snapshot_json: Mapped[Optional[dict]] = mapped_column(JSON)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # V1.5 回放:序号分配与版本冻结;V2.0-A 版本冻结前移至 Run 创建事务
    next_replay_sequence: Mapped[int] = mapped_column(Integer, default=0)
    # V2.1-B:聚合调度(trigger_source/active_run_key/dispatch 租约)
    trigger_source: Mapped[str] = mapped_column(String(16), default="manual")
    active_run_key: Mapped[Optional[str]] = mapped_column(String(64))
    dispatch_status: Mapped[str] = mapped_column(String(16), default="DISPATCHED")
    lease_owner: Mapped[Optional[str]] = mapped_column(String(64))
    lease_until: Mapped[Optional[datetime]] = mapped_column(DateTime(3))
    dispatch_attempts: Mapped[int] = mapped_column(Integer, default=0)
    # V2.1-C:启动前基线封存状态(NULL=未封存;OK/INSUFFICIENT=终值;
    # CAPTURE_FAILED=可按封存 CAS 同条件重试)
    baseline_capture_status: Mapped[Optional[str]] = mapped_column(String(32))
    expected_policy_bundle_version: Mapped[Optional[str]] = mapped_column(String(32))
    policy_bundle_version: Mapped[Optional[str]] = mapped_column(String(32))
    capability_bundle_version: Mapped[Optional[str]] = mapped_column(String(32))
    prompt_bundle_version: Mapped[Optional[str]] = mapped_column(String(32))
    tool_bundle_version: Mapped[Optional[str]] = mapped_column(String(32))


class Hypothesis(Base):
    __tablename__ = "hypothesis"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    description: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(16), default="proposed")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tool_call_id: Mapped[Optional[str]] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(64))
    content: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class HypothesisEvidence(Base):
    __tablename__ = "hypothesis_evidence"
    hypothesis_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("hypothesis.id"),
                                               primary_key=True)
    evidence_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("evidence.id"),
                                             primary_key=True)
    relation: Mapped[str] = mapped_column(String(16))


class ToolCall(Base):
    __tablename__ = "tool_call"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    agent_run_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    # V1.7:逻辑工具调用业务 ID(重试不变)、purpose、context_version
    tool_call_id: Mapped[Optional[str]] = mapped_column(String(64))
    purpose: Mapped[Optional[str]] = mapped_column(String(32))
    context_version: Mapped[Optional[str]] = mapped_column(String(16))
    tool_name: Mapped[str] = mapped_column(String(64))
    input: Mapped[Optional[dict]] = mapped_column(JSON)
    output: Mapped[Optional[dict]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="success")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    transport: Mapped[str] = mapped_column(String(32), default="legacy_direct")
    mcp_invocation_id: Mapped[Optional[str]] = mapped_column(String(64))
    mcp_attempt: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ToolCallAttempt(Base):
    """V1.7:MCP Server 侧传输与执行尝试审计(mcp_tool_auditor 最小权限写入)。"""
    __tablename__ = "tool_call_attempt"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tool_call_pk: Mapped[int] = mapped_column(BigInteger)
    tool_call_id: Mapped[str] = mapped_column(String(64))
    attempt_no: Mapped[int] = mapped_column(Integer)
    client_attempt_id: Mapped[str] = mapped_column(String(64))
    mcp_request_id: Mapped[str] = mapped_column(String(64))
    incident_id: Mapped[int] = mapped_column(BigInteger)
    agent_run_id: Mapped[int] = mapped_column(BigInteger)
    purpose: Mapped[str] = mapped_column(String(32))
    transport: Mapped[str] = mapped_column(String(32))
    outcome: Mapped[str] = mapped_column(String(24))  # started/completed/failed/outcome_unknown
    error_code: Mapped[Optional[str]] = mapped_column(String(64))
    retryable: Mapped[Optional[bool]] = mapped_column(Boolean)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    protocol_version: Mapped[Optional[str]] = mapped_column(String(32))
    server_instance_id: Mapped[Optional[str]] = mapped_column(String(64))
    trace_id: Mapped[Optional[str]] = mapped_column(String(64))
    request_hash: Mapped[Optional[str]] = mapped_column(String(16))
    result_hash: Mapped[Optional[str]] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(DateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class FixDefinition(Base):
    __tablename__ = "fix_definition"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    action_name: Mapped[str] = mapped_column(String(64))
    risk_level: Mapped[str] = mapped_column(String(16), default="medium")
    description: Mapped[Optional[str]] = mapped_column(String(512))


class FixProposal(Base):
    __tablename__ = "fix_proposal"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # V2.0-A:动作类型显式落库(此前仅经 fix_definition 间接表达,代码层默认回退已删除)
    action_type: Mapped[Optional[str]] = mapped_column(String(64))
    fix_definition_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    parameters_json: Mapped[Optional[dict]] = mapped_column(JSON)
    parameters_hash: Mapped[str] = mapped_column(String(64))
    blocking_relation_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, default=None)
    risk_level: Mapped[str] = mapped_column(String(16), default="medium")
    reason: Mapped[Optional[str]] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(16), default="proposed")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Approval(Base):
    __tablename__ = "approval"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # V2.0-A:审批绑定创建它的 Run(恢复/过期只处理该 Run,禁止按 incident 猜最近 Run)
    agent_run_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    fix_proposal_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action_type: Mapped[str] = mapped_column(String(64))
    parameters_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    approver: Mapped[Optional[str]] = mapped_column(String(64))
    comment: Mapped[Optional[str]] = mapped_column(String(512))
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FixExecution(Base):
    """V2.1-D:列结构对齐 migration 006 设计(013 补齐;004 旧结构曾遮蔽 006)。"""
    __tablename__ = "fix_execution"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fix_proposal_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    approval_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    blocking_relation_hash: Mapped[Optional[str]] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    execution_result: Mapped[Optional[str]] = mapped_column(String(32))
    kill_attempted: Mapped[bool] = mapped_column(Boolean, default=False)
    actual_processlist_id: Mapped[Optional[int]] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # result JSON / created_at 为 004 旧列:保留(历史数据兼容),新代码不写入
    result: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RecoveryCheck(Base):
    __tablename__ = "recovery_check"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fix_execution_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    index_present: Mapped[Optional[bool]] = mapped_column(Boolean)
    query_plan_uses_target_index: Mapped[Optional[bool]] = mapped_column(Boolean)
    estimated_rows_before: Mapped[Optional[int]] = mapped_column(BigInteger)
    estimated_rows_after: Mapped[Optional[int]] = mapped_column(BigInteger)
    latency_p95_before: Mapped[Optional[int]] = mapped_column(BigInteger)
    latency_p95_after: Mapped[Optional[int]] = mapped_column(BigInteger)
    consecutive_healthy_checks: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Postmortem(Base):
    __tablename__ = "postmortem"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class IncidentEvent(Base):
    __tablename__ = "incident_event"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32))
    payload: Mapped[Optional[dict]] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class IncidentReplayStep(Base):
    """V1.5 回放:不可变纯追加;两段式 = 同 logical_step_id 多条 phase 记录。"""
    __tablename__ = "incident_replay_step"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    agent_run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    logical_step_id: Mapped[str] = mapped_column(String(64), nullable=False)
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    round_no: Mapped[Optional[int]] = mapped_column(Integer)
    attempt_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    step_type: Mapped[str] = mapped_column(String(40), nullable=False)
    step_title: Mapped[Optional[str]] = mapped_column(String(128))
    step_outcome: Mapped[Optional[str]] = mapped_column(String(32))
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    state_before_json: Mapped[Optional[dict]] = mapped_column(JSON)
    state_after_json: Mapped[Optional[dict]] = mapped_column(JSON)
    decision_json: Mapped[Optional[dict]] = mapped_column(JSON)
    operation_json: Mapped[Optional[dict]] = mapped_column(JSON)
    source_references_json: Mapped[Optional[dict]] = mapped_column(JSON)
    actual_duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    replay_schema_version: Mapped[str] = mapped_column(String(16))
    policy_bundle_version: Mapped[Optional[str]] = mapped_column(String(32))
    prompt_version: Mapped[Optional[str]] = mapped_column(String(64))
    tool_contract_version: Mapped[Optional[str]] = mapped_column(String(32))
    normalization_rule_version: Mapped[Optional[str]] = mapped_column(String(32))
    snapshot_hash: Mapped[Optional[str]] = mapped_column(String(64))
    payload_size_bytes: Mapped[Optional[int]] = mapped_column(Integer)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class IncidentAlert(Base):
    """V2.1-B:Incident ↔ AlertInstance 权威关联(实例键唯一,一实例至多属一 Incident)。"""
    __tablename__ = "incident_alert"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    alert_instance_key: Mapped[str] = mapped_column(String(190), nullable=False,
                                                    unique=True)
