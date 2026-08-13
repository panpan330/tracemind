-- V1.3 迁移:fix_proposal.blocking_relation_hash + fix_execution + lock_observation
-- 幂等:用 information_schema 判断列是否存在(与 005 同风格,可重复执行)
USE tracemind_control;

-- fix_proposal.blocking_relation_hash(若不存在则添加)
SET @have_col := (SELECT COUNT(*) FROM information_schema.COLUMNS
                  WHERE TABLE_SCHEMA='tracemind_control' AND TABLE_NAME='fix_proposal'
                    AND COLUMN_NAME='blocking_relation_hash');
SET @ddl := IF(@have_col = 0,
  'ALTER TABLE tracemind_control.fix_proposal ADD COLUMN blocking_relation_hash VARCHAR(64) NULL',
  'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

CREATE TABLE IF NOT EXISTS fix_execution (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    incident_id BIGINT NOT NULL,
    fix_proposal_id BIGINT NULL,
    approval_id BIGINT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    blocking_relation_hash VARCHAR(64) NULL,
    status VARCHAR(32) NOT NULL,
    execution_result VARCHAR(32) NULL,
    kill_attempted TINYINT NOT NULL DEFAULT 0,
    actual_processlist_id INT NULL,
    started_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    finished_at DATETIME(3) NULL,
    UNIQUE KEY uk_idempotency (idempotency_key),
    INDEX idx_incident (incident_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS lock_observation (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    incident_id BIGINT NOT NULL,
    agent_run_id BIGINT NOT NULL,
    blocker_ref VARCHAR(64) NOT NULL,
    transaction_id BIGINT NULL,
    processlist_id INT NULL,
    blocking_lock_ref VARCHAR(128) NULL,
    relation_identity_hash VARCHAR(64) NULL,
    observed_at DATETIME(3) NULL,
    expires_at DATETIME(3) NULL,
    UNIQUE KEY uk_blocker_ref (blocker_ref),
    INDEX idx_incident_run (incident_id, agent_run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
