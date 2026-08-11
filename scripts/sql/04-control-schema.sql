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
  finished_at DATETIME NULL,
  termination_reason VARCHAR(64) NULL,
  degraded TINYINT NOT NULL DEFAULT 0,
  degradation_reasons VARCHAR(500) NULL
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

-- model_call:LLM 逻辑调用审计(含每次尝试)
CREATE TABLE tracemind_control.model_call (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  incident_id BIGINT NOT NULL,
  agent_run_id BIGINT NOT NULL,
  node VARCHAR(50) NOT NULL,
  mode VARCHAR(20) NOT NULL,
  provider VARCHAR(20) NOT NULL,
  model VARCHAR(100) NOT NULL,
  model_snapshot VARCHAR(100) DEFAULT '',
  prompt_version VARCHAR(20) DEFAULT '',
  prompt_hash CHAR(16) DEFAULT '',
  tool_schema_version VARCHAR(20) DEFAULT '',
  logical_call_id VARCHAR(64) DEFAULT '',
  attempts_json TEXT,
  finish_reason VARCHAR(30) DEFAULT '',
  structured_output_valid TINYINT DEFAULT 0,
  tool_call_count INT DEFAULT 0,
  provider_request_id VARCHAR(64) DEFAULT '',
  fallback_executor VARCHAR(50) DEFAULT '',
  input_snapshot_json TEXT,
  latency_ms INT DEFAULT 0,
  input_tokens INT,
  output_tokens INT,
  status VARCHAR(20) NOT NULL,
  error_code VARCHAR(100) DEFAULT '',
  degraded TINYINT DEFAULT 0,
  git_commit_sha CHAR(40) DEFAULT '',
  knowledge_chunk_ids VARCHAR(500) DEFAULT '',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_model_call_incident (incident_id),
  INDEX idx_model_call_run (agent_run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- retrieval_record:RAG 检索审计(知识参考,不参与 E 闸门)
CREATE TABLE tracemind_control.retrieval_record (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  incident_id BIGINT NOT NULL,
  agent_run_id BIGINT NOT NULL,
  node VARCHAR(50) NOT NULL,
  query_text_hash CHAR(16) DEFAULT '',
  collection_alias VARCHAR(100) DEFAULT '',
  collection_version VARCHAR(50) DEFAULT '',
  embedding_model VARCHAR(50) DEFAULT '',
  embedding_dimensions INT DEFAULT 0,
  candidate_top_k INT DEFAULT 0,
  final_chunk_ids VARCHAR(500) DEFAULT '',
  scores VARCHAR(500) DEFAULT '',
  latency_ms INT DEFAULT 0,
  status VARCHAR(20) NOT NULL,
  error_code VARCHAR(100) DEFAULT '',
  degraded TINYINT DEFAULT 0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_retrieval_incident (incident_id),
  INDEX idx_retrieval_run (agent_run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- V1.1 幂等迁移:为已存在的 incident 表补充状态属性列(信息 schema 判断,MySQL 8 兼容)
SET @have_col := (SELECT COUNT(*) FROM information_schema.COLUMNS
                  WHERE TABLE_SCHEMA='tracemind_control' AND TABLE_NAME='incident'
                    AND COLUMN_NAME='termination_reason');
SET @ddl := IF(@have_col = 0,
  'ALTER TABLE tracemind_control.incident ADD COLUMN termination_reason VARCHAR(64) NULL AFTER finished_at',
  'SELECT 1');
PREPARE stmt_t FROM @ddl; EXECUTE stmt_t; DEALLOCATE PREPARE stmt_t;

SET @have_col := (SELECT COUNT(*) FROM information_schema.COLUMNS
                  WHERE TABLE_SCHEMA='tracemind_control' AND TABLE_NAME='incident'
                    AND COLUMN_NAME='degraded');
SET @ddl := IF(@have_col = 0,
  'ALTER TABLE tracemind_control.incident ADD COLUMN degraded TINYINT NOT NULL DEFAULT 0 AFTER termination_reason',
  'SELECT 1');
PREPARE stmt_d FROM @ddl; EXECUTE stmt_d; DEALLOCATE PREPARE stmt_d;

SET @have_col := (SELECT COUNT(*) FROM information_schema.COLUMNS
                  WHERE TABLE_SCHEMA='tracemind_control' AND TABLE_NAME='incident'
                    AND COLUMN_NAME='degradation_reasons');
SET @ddl := IF(@have_col = 0,
  'ALTER TABLE tracemind_control.incident ADD COLUMN degradation_reasons VARCHAR(500) NULL AFTER degraded',
  'SELECT 1');
PREPARE stmt_r FROM @ddl; EXECUTE stmt_r; DEALLOCATE PREPARE stmt_r;

-- fix_definition 种子(幂等):两条预定义动作
INSERT INTO fix_definition (action_name, risk_level, description)
SELECT 'CREATE_INVENTORY_INDEX', 'medium', '创建 idx_sku_warehouse(sku_id, warehouse_id) 联合索引'
WHERE NOT EXISTS (SELECT 1 FROM fix_definition WHERE action_name = 'CREATE_INVENTORY_INDEX');
INSERT INTO fix_definition (action_name, risk_level, description)
SELECT 'TERMINATE_BLOCKING_SESSION', 'high', '终止持有库存目标记录排他锁的阻塞会话'
WHERE NOT EXISTS (SELECT 1 FROM fix_definition WHERE action_name = 'TERMINATE_BLOCKING_SESSION');
