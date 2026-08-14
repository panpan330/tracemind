"""Agent 长期记忆:诊断成功(recovered)后把案例向量化沉淀到 qdrant,供下次语义检索复用。"""
import logging
from datetime import datetime, timezone

from app.config import settings

logger = logging.getLogger(__name__)

CASE_COLLECTION = "tracemind_case_memory"


def _case_text(state: dict) -> str:
    lines = [f"故障描述:{state.get('description', '')}"]
    ev = state.get("evidence") or []
    ev_lines = "; ".join(f"{e.get('id', e.get('key'))}={e.get('passed')}" for e in ev)
    lines.append(f"证据结论:{ev_lines}")
    lines.append(f"根因:{state.get('root_cause_code', '')} {state.get('root_cause', '')}")
    fix = state.get("fix_execution") or {}
    lines.append(f"修复动作:{fix.get('status', '')}")
    recovery = state.get("recovery") or {}
    lines.append(f"恢复结果:{recovery.get('status', '')}")
    return "\n".join(lines)


def _case_payload(state: dict) -> dict:
    return {"doc_id": f"case-{state.get('run_id', 0)}",
            "title": "历史诊断案例",
            "text": _case_text(state),
            "root_cause_code": state.get("root_cause_code", ""),
            "fault_category": state.get("fault_category") or state.get("root_cause_code", ""),
            "recovered": True,
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": state.get("run_id", 0)}


def _get_store():
    from app.rag.embedder import Embedder
    from app.rag.runbook_store import RunbookStore
    return RunbookStore(embedder=Embedder(), collection_alias=CASE_COLLECTION)


def _upsert(state: dict, vector: list, payload: dict) -> None:
    store = _get_store()
    store.ensure_collection(settings.embedding_dimensions)
    store.upsert(point_id=state.get("run_id", 0), vector=vector, payload=payload)


def record_case(state: dict) -> None:
    """report 节点后调用;仅 recovered 沉淀;任何失败不阻塞诊断。"""
    if state.get("status") != "recovered":
        return
    try:
        from app.rag.embedder import Embedder
        vec = Embedder().embed(_case_text(state))
        if not vec:
            logger.warning("案例 embedding 失败,跳过沉淀")
            return
        _upsert(state, vec, _case_payload(state))
    except Exception as exc:  # noqa: BLE001
        logger.warning("案例沉淀失败(不阻塞): %s", exc)
