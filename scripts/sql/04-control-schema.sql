-- TraceMind:控制库 13 表(幂等)
USE tracemind_control;

CREATE TABLE IF NOT EXISTS incident (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  title VARCHAR(255) NOT NULL,
  description TEXT NULL,
  severity VARCHAR(16) NOT NULL DEFAULT 'medium',
  service_ref VARCHAR(64) NULL,
  observed_at DATETIME NULL,
  trigger_trace_id VARCHAR(64) NULL,
  healthy_metrics_baseline JSON NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'created',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at DATETIME NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS agent_run (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  incident_id BIGINT NOT NULL,
  thread_id VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'created',
  investigation_round INT NOT NULL DEFAULT 0,
  tool_call_count INT NOT NULL DEFAULT 0,
  incident_digest_baseline JSON NULL,
  started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at DATETIME NULL,
  UNIQUE KEY uq_run_thread (thread_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS hypothesis (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  incident_id BIGINT NOT NULL,
  description VARCHAR(512) NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'proposed',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS evidence (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  incident_id BIGINT NOT NULL,
  tool_call_id VARCHAR(64) NULL,
  source VARCHAR(64) NOT NULL,
  content JSON NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS hypothesis_evidence (
  hypothesis_id BIGINT NOT NULL,
  evidence_id BIGINT NOT NULL,
  relation VARCHAR(16) NOT NULL,
  PRIMARY KEY (hypothesis_id, evidence_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS tool_call (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  incident_id BIGINT NULL,
  tool_name VARCHAR(64) NOT NULL,
  input JSON NULL,
  output JSON NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'success',
  duration_ms INT NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS fix_definition (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  action_name VARCHAR(64) NOT NULL,
  risk_level VARCHAR(16) NOT NULL DEFAULT 'medium',
  description VARCHAR(512) NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS fix_proposal (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  incident_id BIGINT NOT NULL,
  fix_definition_id BIGINT NOT NULL,
  parameters_json JSON NULL,
  parameters_hash VARCHAR(64) NOT NULL,
  risk_level VARCHAR(16) NOT NULL DEFAULT 'medium',
  reason VARCHAR(512) NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'proposed',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS approval (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  incident_id BIGINT NOT NULL,
  fix_proposal_id BIGINT NOT NULL,
  action_type VARCHAR(64) NOT NULL,
  parameters_hash VARCHAR(64) NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'pending',
  approver VARCHAR(64) NULL,
  comment VARCHAR(512) NULL,
  expires_at DATETIME NULL,
  consumed_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS fix_execution (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  incident_id BIGINT NOT NULL,
  fix_proposal_id BIGINT NOT NULL,
  approval_id BIGINT NOT NULL,
  idempotency_key VARCHAR(128) NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'pending',
  result JSON NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_fix_idem (idempotency_key)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS recovery_check (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  incident_id BIGINT NOT NULL,
  fix_execution_id BIGINT NULL,
  index_present TINYINT(1) NULL,
  query_plan_uses_target_index TINYINT(1) NULL,
  estimated_rows_before BIGINT NULL,
  estimated_rows_after BIGINT NULL,
  latency_p95_before BIGINT NULL,
  latency_p95_after BIGINT NULL,
  consecutive_healthy_checks INT NOT NULL DEFAULT 0,
  status VARCHAR(16) NOT NULL DEFAULT 'pending',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS postmortem (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  incident_id BIGINT NOT NULL,
  content JSON NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS incident_event (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  incident_id BIGINT NOT NULL,
  sequence INT NOT NULL,
  event_type VARCHAR(32) NOT NULL,
  payload JSON NULL,
  occurred_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_incident_seq (incident_id, sequence),
  KEY idx_event_incident (incident_id, id)
) ENGINE=InnoDB;
