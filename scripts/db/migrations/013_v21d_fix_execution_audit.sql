-- V2.1-D:fix_execution 审计列补齐(KILL 审计缺口修复)。
-- 缺陷根因:004 与 006 均为 CREATE TABLE IF NOT EXISTS fix_execution,
-- 004 旧结构先建 → 006 的新列静默未生效 → create_execution INSERT 引用
-- 不存在列,审计写入自 V1.3 起一直失败且被调用方吞掉。
-- 本迁移仅补齐/放宽列(006 的设计意图),不删旧列、不丢数据;
-- result JSON 列保留(历史 210 行兼容),新代码不再写入。
USE tracemind_control;

ALTER TABLE fix_execution
  MODIFY COLUMN fix_proposal_id BIGINT NULL,
  MODIFY COLUMN approval_id     BIGINT NULL,
  MODIFY COLUMN status          VARCHAR(32) NOT NULL DEFAULT 'pending',
  ADD COLUMN blocking_relation_hash VARCHAR(64) NULL,
  ADD COLUMN execution_result       VARCHAR(32) NULL,
  ADD COLUMN kill_attempted         TINYINT NOT NULL DEFAULT 0,
  ADD COLUMN actual_processlist_id  INT NULL,
  ADD COLUMN started_at             DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  ADD COLUMN finished_at            DATETIME(3) NULL;

-- 历史幂等键为裸 parameters_hash(跨 Incident 冲突);新键带 appr:/inc: 前缀,
-- 与历史键天然不冲突,无需数据迁移。
