"""AI 服务 Settings(既有全局;按进程拆分后由 app.config 聚合导出)。"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class DATABASE_ACCESS_DISABLED(RuntimeError):
    """offline_eval profile 下访问数据库的统一异常。"""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRACEMIND_", env_file=".env.local", extra="ignore")

    # ---- Run Profile(local|ci_db|offline_eval|full_e2e|production)----
    run_profile: str = "local"

    control_db_url: str = "mysql+pymysql://tracemind_control_app:control_app_pwd@localhost:3306/tracemind_control"
    readonly_db_url: str = "mysql+pymysql://ai_investigator:investigator_pwd@localhost:3306/tracemind_business"
    # 会话终止专用账号(TRACEMIND_SESSION_TERMINATOR_DB_URL);为空时回退只读引擎(仅查询,无法 KILL)
    session_terminator_db_url: str = ""
    fix_executor_db_url: str = ""
    order_service_url: str = "http://localhost:8081"
    inventory_service_url: str = "http://localhost:8082"
    demo_mode: bool = False
    demo_key: str = ""
    demo_approver_id: str = "demo-approver"
    checkpoint_path: str = "./data/checkpoints.sqlite"

    # LLM 模式(fake / real_strict / real_demo)
    llm_mode: str = "fake"

    # ---- Chat Provider(V1.1 新命名;TRACEMIND_LLM_* 为 V1.0 旧字段,作 fallback)----
    chat_provider: str = "bailian"             # bailian | generic
    chat_base_url: str = ""
    chat_api_key: str = ""
    chat_model: str = ""
    eval_chat_model: str = ""                  # 评测固定快照;eval-* 命令必填

    # ---- V1.11 多模型路由:按节点选模型(空 → 回落 chat_model_resolved)----
    hypothesize_model: str = ""
    select_tool_model: str = ""
    reflect_model: str = ""
    report_model: str = ""
    fallback_model: str = ""            # 容灾备用;空 → 不启用 fallback

    # ---- Embedding Provider(与 Chat 独立,可不同 base_url/key)----
    embedding_provider: str = "bailian"
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-v4"
    embedding_dimensions: int = 1024

    # ---- Qdrant ----
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_read_api_key: str = ""
    qdrant_write_api_key: str = ""
    qdrant_collection_alias: str = "tracemind_runbook_current"

    # ---- RAG ----
    rag_mode: str = "optional"                 # off | optional | required
    rag_candidate_top_k: int = 6
    rag_final_top_k: int = 3
    rag_score_threshold: float = 0.0           # 校准集确定后冻结进 evaluation_policy.yaml

    # ---- 评测 ----
    eval_fixture_dir: str = ""
    eval_report_dir: str = "./reports/evals"
    eval_repetitions: int = 3
    eval_mode: bool = False                    # 启用 EvalApprover(自动审批)

    # ---- MCP 工具服务 ----
    mcp_timeout_seconds: float = 15.0   # 单次工具调用超时
    mcp_max_restart: int = 1            # Server 启动/初始化失败最多重启次数
    mcp_ready: bool = False             # 运行时:契约校验通过后置 True

    # ---- V1.7:MCP 传输(stdio | streamable_http)----
    mcp_transport: str = "stdio"
    mcp_http_url: str = ""
    mcp_http_bearer_token: str = ""
    mcp_http_connect_timeout_seconds: float = 5.0
    mcp_http_request_timeout_seconds: float = 30.0
    mcp_http_max_retries: int = 3
    mcp_auth_clients_file: str = ""
    mcp_max_request_bytes: int = 262144
    mcp_max_result_bytes: int = 1048576
    mcp_audit_db_url: str = ""

    # ---- V1.0 旧 LLM 字段(deprecated fallback)----
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_provider: str = "bailian"

    # ---- V1.4 可观测性 ----
    metrics_backend: str = "fixture"              # prometheus | fixture
    trace_backend: str = "fixture"                # jaeger | fixture
    prometheus_url: str = "http://localhost:9090"
    jaeger_query_endpoint: str = "localhost:16685"  # gRPC QueryService
    metrics_max_age_seconds: int = 120
    trace_export_wait_timeout_seconds: int = 30
    trace_search_retry_interval_seconds: int = 2
    trace_search_max_attempts: int = 5
    max_trace_search_window_seconds: int = 600
    max_trace_candidates: int = 50
    internal_observation_enabled: bool = False

    @property
    def chat_base_url_resolved(self) -> str:
        return self.chat_base_url or self.llm_base_url

    @property
    def chat_api_key_resolved(self) -> str:
        return self.chat_api_key or self.llm_api_key

    @property
    def chat_model_resolved(self) -> str:
        return self.chat_model or self.llm_model

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._fail_closed_check()

    def _fail_closed_check(self) -> None:
        """Run Profile fail-closed:严格 profile 缺 URL / LLM 模式不匹配 → 启动失败。"""
        required = {
            "ci_db": ["control_db_url", "readonly_db_url",
                      "session_terminator_db_url", "fix_executor_db_url"],
            "full_e2e": ["control_db_url", "readonly_db_url",
                         "session_terminator_db_url", "fix_executor_db_url"],
            "production": ["control_db_url", "readonly_db_url",
                           "session_terminator_db_url", "fix_executor_db_url"],
        }
        for name in required.get(self.run_profile, []):
            if not getattr(self, name):
                raise ValueError(f"[{self.run_profile}] 缺少 TRACEMIND_{name.upper()}")
        llm_ok = {"ci_db": {"fake"}, "offline_eval": {"fake"}, "full_e2e": {"real_strict"}}
        allowed = llm_ok.get(self.run_profile)
        if allowed and self.llm_mode not in allowed:
            raise ValueError(
                f"[{self.run_profile}] LLM 模式必须为 {sorted(allowed)},当前 {self.llm_mode}")


settings = Settings()
