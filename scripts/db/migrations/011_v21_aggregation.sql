-- V2.1-B:聚合与调度 —— Incident 聚合列 / incident_alert 权威关联 / AgentRun 调度列。
-- 追加式;存量行 'manual'/NULL/DISPATCHED 默认,不参与聚合。
USE tracemind_control;

ALTER TABLE incident
  ADD COLUMN source VARCHAR(32) NOT NULL DEFAULT 'manual',
  ADD COLUMN alert_name VARCHAR(128) NULL,
  ADD COLUMN environment VARCHAR(32) NULL,
  ADD COLUMN alert_status VARCHAR(16) NULL,
  ADD COLUMN lifecycle_status VARCHAR(16) NULL,
  ADD COLUMN group_key CHAR(64) NULL,
  ADD COLUMN open_group_key CHAR(64) NULL,
  ADD COLUMN first_seen_at DATETIME(3) NULL,
  ADD COLUMN last_seen_at DATETIME(3) NULL,
  ADD COLUMN occurrence_count INT NOT NULL DEFAULT 0,
  ADD COLUMN labels_json JSON NULL,
  ADD COLUMN annotations_json JSON NULL,
  ADD UNIQUE KEY uk_incident_open_group (open_group_key),
  ADD KEY idx_incident_group (group_key);

CREATE TABLE IF NOT EXISTS incident_alert (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  incident_id BIGINT NOT NULL,
  alert_instance_key VARCHAR(190) NOT NULL,
  UNIQUE KEY uk_incident_alert_instance (alert_instance_key),
  UNIQUE KEY uk_incident_alert (incident_id, alert_instance_key),
  KEY idx_ia_incident (incident_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE agent_run
  ADD COLUMN trigger_source VARCHAR(16) NOT NULL DEFAULT 'manual',
  ADD COLUMN active_run_key VARCHAR(64) NULL,
  ADD COLUMN dispatch_status VARCHAR(16) NOT NULL DEFAULT 'DISPATCHED',
  ADD COLUMN lease_owner VARCHAR(64) NULL,
  ADD COLUMN lease_until DATETIME(3) NULL,
  ADD COLUMN dispatch_attempts INT NOT NULL DEFAULT 0,
  ADD UNIQUE KEY uk_agent_run_active (active_run_key),
  ADD KEY idx_agent_run_dispatch (status, dispatch_status, lease_until);
