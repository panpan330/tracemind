"""配置扩展与回退行为。"""
from app.config import Settings


def test_chat_fields_fallback_to_legacy_llm():
    s = Settings(llm_base_url="http://old", llm_api_key="k", llm_model="m",
                 chat_base_url="", chat_api_key="", chat_model="")
    assert s.chat_base_url_resolved == "http://old"
    assert s.chat_api_key_resolved == "k"
    assert s.chat_model_resolved == "m"
    assert s.chat_provider == "bailian"


def test_chat_fields_override_legacy():
    s = Settings(llm_base_url="http://old", chat_base_url="http://new")
    assert s.chat_base_url_resolved == "http://new"


def test_eval_chat_model_required_flag():
    # 显式排除 .env.local:默认空(评测固定快照必填)
    s = Settings(_env_file=None)
    assert s.eval_chat_model == ""
    assert s.chat_model_resolved == ""


def test_embedding_defaults():
    s = Settings()
    assert s.embedding_model == "text-embedding-v4"
    assert s.embedding_dimensions == 1024


def test_rag_defaults():
    s = Settings()
    assert s.rag_mode == "optional"
    assert s.rag_candidate_top_k == 6
    assert s.rag_final_top_k == 3
    assert s.rag_score_threshold == 0.0        # 校准前为 0(不过滤),校准后冻结


def test_qdrant_defaults():
    s = Settings()
    assert s.qdrant_url == "http://127.0.0.1:6333"
    assert s.qdrant_collection_alias == "tracemind_runbook_current"


def test_mcp_config_defaults():
    s = Settings(_env_file=None)
    assert s.mcp_timeout_seconds == 15.0
    assert s.mcp_max_restart == 1


def test_observability_defaults():
    from app.config import Settings
    s = Settings()
    assert s.metrics_backend == "fixture"
    assert s.trace_backend == "fixture"
    assert s.prometheus_url == "http://localhost:9090"
    assert s.jaeger_query_endpoint == "localhost:16685"
    assert s.metrics_max_age_seconds == 120
    assert s.trace_export_wait_timeout_seconds == 30
    assert s.trace_search_retry_interval_seconds == 2
    assert s.trace_search_max_attempts == 5
    assert s.max_trace_search_window_seconds == 600
    assert s.max_trace_candidates == 20
    assert s.internal_observation_enabled is False
