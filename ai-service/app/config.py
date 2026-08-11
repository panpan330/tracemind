from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRACEMIND_", env_file=".env.local", extra="ignore")

    control_db_url: str = "mysql+pymysql://tracemind_control_app:control_app_pwd@localhost:3306/tracemind_control"
    readonly_db_url: str = "mysql+pymysql://ai_investigator:investigator_pwd@localhost:3306/tracemind_business"
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

    # ---- V1.0 旧 LLM 字段(deprecated fallback)----
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_provider: str = "bailian"

    @property
    def chat_base_url_resolved(self) -> str:
        return self.chat_base_url or self.llm_base_url

    @property
    def chat_api_key_resolved(self) -> str:
        return self.chat_api_key or self.llm_api_key

    @property
    def chat_model_resolved(self) -> str:
        return self.chat_model or self.llm_model


settings = Settings()
