-- V2.0-A 基线可信化:Run 上下文冻结 / checkpoint 绑定 / 版本冻结前移 / 审批绑定准确 Run / 提案动作显式化
-- 全部为追加式变化(新列可空 + 回填),不改已有列,不删任何数据。
USE tracemind_control;

-- 1) agent_run:Run 创建事务内冻结的不可变上下文快照 + bundle 版本(V2.0 完成标准第 6/8 条)
ALTER TABLE agent_run
    ADD COLUMN run_context_snapshot_json JSON NULL AFTER incident_digest_baseline,
    ADD COLUMN checkpoint_thread_id VARCHAR(128) NULL AFTER thread_id,
    ADD COLUMN checkpoint_namespace VARCHAR(128) NULL AFTER checkpoint_thread_id,
    ADD COLUMN capability_bundle_version VARCHAR(32) NULL AFTER expected_policy_bundle_version,
    ADD COLUMN prompt_bundle_version VARCHAR(32) NULL AFTER policy_bundle_version,
    ADD COLUMN tool_bundle_version VARCHAR(32) NULL AFTER prompt_bundle_version;

-- 存量 Run 回填:checkpoint thread 与 thread_id 一一对应,namespace 空串 = 默认命名空间
UPDATE agent_run SET checkpoint_thread_id = thread_id, checkpoint_namespace = ''
WHERE checkpoint_thread_id IS NULL;

CREATE UNIQUE INDEX uk_agent_run_ckpt_thread ON agent_run (checkpoint_thread_id);

-- 2) approval:绑定创建它的 Run(审批决定/过期扫描只恢复该 Run;存量行 NULL → 兼容回退)
ALTER TABLE approval
    ADD COLUMN agent_run_id BIGINT NULL AFTER incident_id;

CREATE INDEX idx_approval_agent_run ON approval (agent_run_id);

-- 3) fix_proposal:动作类型显式落库(此前仅经 fix_definition 间接表达;
--    存量行按 fix_definition 映射回填,新行由 proposal_repo 显式写入)
ALTER TABLE fix_proposal
    ADD COLUMN action_type VARCHAR(64) NULL AFTER incident_id;

UPDATE fix_proposal p
JOIN fix_definition d ON p.fix_definition_id = d.id
SET p.action_type = d.action_name
WHERE p.action_type IS NULL;
