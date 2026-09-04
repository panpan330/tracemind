"""V2.0-B closure:根因代码与诊断标识 —— Capability 领域公共契约。

权威定义在本模块;app.agent.policies 仅重导出(deprecated 兼容层),
capabilities 包内禁止反向导入 app.agent.policies / app.agent.facts(架构测试钉死)。
"""
ROOT_CAUSE_INDEX = "MISSING_INVENTORY_INDEX"
ROOT_CAUSE_LOCK = "LONG_RUNNING_TRANSACTION_BLOCKING_INVENTORY_RESERVATION"
