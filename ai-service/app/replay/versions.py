"""V1.5 回放版本常量(Run 级冻结)。
- POLICY_BUNDLE_VERSION:双 DiagnosticPolicy 的整体版本(V1.3 起为 scn001/scn002 组合)。
- REPLAY_SCHEMA_VERSION:快照/回放记录 schema 版本。
- PLAYBACK_POLICY_VERSION:前端展示时长计算规则版本。
tool_contract 用既有 MCP_TOOL_CONTRACT_VERSION;normalizer 用既有 TRACE_NORMALIZER_V1。
"""
POLICY_BUNDLE_VERSION = "1.0"
REPLAY_SCHEMA_VERSION = "1.0"
PLAYBACK_POLICY_VERSION = "1"
