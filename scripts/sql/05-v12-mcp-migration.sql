-- V1.2 版本化迁移:tool_call 增加 MCP 审计字段(信息 schema 幂等判断)
SET @have_col := (SELECT COUNT(*) FROM information_schema.COLUMNS
                  WHERE TABLE_SCHEMA='tracemind_control' AND TABLE_NAME='tool_call'
                    AND COLUMN_NAME='agent_run_id');
SET @ddl := IF(@have_col = 0,
  'ALTER TABLE tracemind_control.tool_call ADD COLUMN agent_run_id BIGINT NULL AFTER incident_id',
  'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @have_col := (SELECT COUNT(*) FROM information_schema.COLUMNS
                  WHERE TABLE_SCHEMA='tracemind_control' AND TABLE_NAME='tool_call'
                    AND COLUMN_NAME='transport');
SET @ddl := IF(@have_col = 0,
  'ALTER TABLE tracemind_control.tool_call ADD COLUMN transport VARCHAR(32) NOT NULL DEFAULT ''legacy_direct'' AFTER status',
  'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @have_col := (SELECT COUNT(*) FROM information_schema.COLUMNS
                  WHERE TABLE_SCHEMA='tracemind_control' AND TABLE_NAME='tool_call'
                    AND COLUMN_NAME='mcp_invocation_id');
SET @ddl := IF(@have_col = 0,
  'ALTER TABLE tracemind_control.tool_call ADD COLUMN mcp_invocation_id VARCHAR(64) NULL AFTER transport',
  'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @have_col := (SELECT COUNT(*) FROM information_schema.COLUMNS
                  WHERE TABLE_SCHEMA='tracemind_control' AND TABLE_NAME='tool_call'
                    AND COLUMN_NAME='mcp_attempt');
SET @ddl := IF(@have_col = 0,
  'ALTER TABLE tracemind_control.tool_call ADD COLUMN mcp_attempt INT NULL AFTER mcp_invocation_id',
  'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @have_idx := (SELECT COUNT(*) FROM information_schema.STATISTICS
                  WHERE TABLE_SCHEMA='tracemind_control' AND TABLE_NAME='tool_call'
                    AND INDEX_NAME='idx_tool_call_agent_run');
SET @ddl := IF(@have_idx = 0,
  'ALTER TABLE tracemind_control.tool_call ADD INDEX idx_tool_call_agent_run (agent_run_id)',
  'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
