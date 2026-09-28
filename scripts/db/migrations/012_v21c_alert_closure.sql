-- V2.1-C:告警闭环 —— 历史基线快照 / episode 关闭 / 基线采集状态。
-- 追加式;不删旧列。healthy_metrics_baseline 列保留但停止写入/读取
-- (存量值为故障态实时值,不得充当健康基线;权威来源改为 baseline_metrics_json)。
USE tracemind_control;

ALTER TABLE incident
  ADD COLUMN baseline_window_start DATETIME(3) NULL,
  ADD COLUMN baseline_window_end   DATETIME(3) NULL,
  ADD COLUMN baseline_metrics_json JSON NULL,
  ADD COLUMN baseline_quality      VARCHAR(32) NULL,
  ADD COLUMN auto_run_started_at   DATETIME(3) NULL,
  ADD COLUMN closed_at             DATETIME(3) NULL,
  ADD COLUMN current_health_snapshot_json JSON NULL;

ALTER TABLE agent_run
  ADD COLUMN baseline_capture_status VARCHAR(32) NULL;
-- baseline_capture_status 语义(两阶段冻结契约):
--   NULL            = 未封存(阶段 2 未发生)
--   'OK'            = 已封存(digest 快照 + 健康窗口合格)——终值,不重采
--   'INSUFFICIENT'  = 已封存(健康窗口不合格,不伪造基线)——终值,不重采
--   'CAPTURE_FAILED'= 采集异常(未封存)——可按封存 CAS 同一条件重试
-- 封存 CAS:status='queued' AND dispatch_status='CLAIMED' AND lease_owner=:owner
--   AND lease_until>=:now AND (baseline_capture_status IS NULL
--   OR baseline_capture_status='CAPTURE_FAILED')

CREATE INDEX idx_incident_alert_lifecycle
  ON incident (source, alert_status, lifecycle_status);
