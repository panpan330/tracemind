-- TraceMind:账号与授权(幂等)
CREATE USER IF NOT EXISTS 'app_business'@'%' IDENTIFIED BY 'app_business_pwd';
CREATE USER IF NOT EXISTS 'tracemind_control_app'@'%' IDENTIFIED BY 'control_app_pwd';
CREATE USER IF NOT EXISTS 'ai_investigator'@'%' IDENTIFIED BY 'investigator_pwd';
CREATE USER IF NOT EXISTS 'fix_executor'@'%' IDENTIFIED BY 'fix_executor_pwd';

GRANT SELECT, INSERT, UPDATE, DELETE ON tracemind_business.* TO 'app_business'@'%';
-- 场景注入/重置需要索引 DDL(仅 INDEX 权限,最小化)
GRANT INDEX ON tracemind_business.* TO 'app_business'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON tracemind_business_test.* TO 'app_business'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON tracemind_control.* TO 'tracemind_control_app'@'%';
GRANT CREATE TEMPORARY TABLES ON tracemind_control.* TO 'tracemind_control_app'@'%';
GRANT SELECT ON tracemind_business.* TO 'ai_investigator'@'%';
GRANT SELECT ON tracemind_business_test.* TO 'ai_investigator'@'%';
GRANT SELECT ON performance_schema.* TO 'ai_investigator'@'%';
-- fix_executor:仅 INDEX 权限,execute_fix 唯一写路径
GRANT INDEX ON tracemind_business.* TO 'fix_executor'@'%';
FLUSH PRIVILEGES;

-- 第五账号:会话终止专用(session_terminator)
CREATE USER IF NOT EXISTS 'session_terminator'@'%' IDENTIFIED BY 'terminator_pwd';
GRANT SELECT ON information_schema.* TO 'session_terminator'@'%';
GRANT SELECT ON performance_schema.* TO 'session_terminator'@'%';
GRANT PROCESS ON *.* TO 'session_terminator'@'%';
