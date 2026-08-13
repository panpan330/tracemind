-- TraceMind 角色与授权(幂等);账号创建与密码由 migrate.py --provision 从环境变量注入
-- 角色与授权分离:本文件不含任何明文密码

CREATE ROLE IF NOT EXISTS 'role_control_app';
CREATE ROLE IF NOT EXISTS 'role_app_business';
CREATE ROLE IF NOT EXISTS 'role_ai_investigator';
CREATE ROLE IF NOT EXISTS 'role_fix_executor';
CREATE ROLE IF NOT EXISTS 'role_session_terminator';

-- tracemind_control_app:控制库读写(含临时表,场景注入需要)
GRANT SELECT, INSERT, UPDATE, DELETE ON tracemind_control.* TO 'role_control_app';
GRANT CREATE TEMPORARY TABLES ON tracemind_control.* TO 'role_control_app';

-- app_business:业务库读写 + 索引 DDL(场景注入/重置)
GRANT SELECT, INSERT, UPDATE, DELETE ON tracemind_business.* TO 'role_app_business';
GRANT INDEX ON tracemind_business.* TO 'role_app_business';
GRANT SELECT, INSERT, UPDATE, DELETE ON tracemind_business_test.* TO 'role_app_business';

-- ai_investigator:只读调查 + performance_schema 观测 + PROCESS(processlist 观测)
GRANT SELECT ON tracemind_business.* TO 'role_ai_investigator';
GRANT SELECT ON tracemind_business_test.* TO 'role_ai_investigator';
GRANT SELECT ON performance_schema.* TO 'role_ai_investigator';
GRANT PROCESS ON *.* TO 'role_ai_investigator';

-- fix_executor:仅 INDEX 权限(execute_fix 唯一写路径)
GRANT INDEX ON tracemind_business.* TO 'role_fix_executor';

-- session_terminator:会话终止专用(performance_schema 观测 + PROCESS/CONNECTION_ADMIN)
-- 注:information_schema 为 MySQL 内置虚拟库,所有用户默认可读,无需 GRANT
GRANT SELECT ON performance_schema.* TO 'role_session_terminator';
GRANT PROCESS ON *.* TO 'role_session_terminator';
GRANT CONNECTION_ADMIN ON *.* TO 'role_session_terminator';
