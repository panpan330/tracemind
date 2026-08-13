-- V1.7:tool_call_attempt(传输与执行尝试审计)+ tool_call 扩展列;不含环境密码(账号走 Provisioning)
USE tracemind_control;

ALTER TABLE tool_call
    ADD COLUMN tool_call_id VARCHAR(64) NULL AFTER agent_run_id,
    ADD COLUMN purpose VARCHAR(32) NULL,
    ADD COLUMN context_version VARCHAR(16) NULL,
    ADD UNIQUE KEY uk_tool_call_agent_toolcall (agent_run_id, tool_call_id);

CREATE TABLE IF NOT EXISTS tool_call_attempt (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    tool_call_pk BIGINT NOT NULL,
    tool_call_id VARCHAR(64) NOT NULL,
    attempt_no INT NOT NULL,
    client_attempt_id VARCHAR(64) NOT NULL,
    mcp_request_id VARCHAR(64) NOT NULL,
    incident_id BIGINT NOT NULL,
    agent_run_id BIGINT NOT NULL,
    purpose VARCHAR(32) NOT NULL,
    transport VARCHAR(32) NOT NULL,
    outcome VARCHAR(24) NOT NULL,
    error_code VARCHAR(64) NULL,
    retryable TINYINT(1) NULL,
    latency_ms INT NOT NULL DEFAULT 0,
    protocol_version VARCHAR(32) NULL,
    server_instance_id VARCHAR(64) NULL,
    trace_id VARCHAR(64) NULL,
    request_hash VARCHAR(16) NULL,
    result_hash VARCHAR(16) NULL,
    started_at DATETIME NOT NULL,
    completed_at DATETIME NULL,
    UNIQUE KEY uk_attempt_toolcall_attempt (tool_call_pk, attempt_no),
    UNIQUE KEY uk_attempt_toolcall_client (tool_call_pk, client_attempt_id),
    UNIQUE KEY uk_attempt_mcp_request (mcp_request_id),
    KEY idx_attempt_agent_run (agent_run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE ROLE IF NOT EXISTS 'role_mcp_tool_auditor';
