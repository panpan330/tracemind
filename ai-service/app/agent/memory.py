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


def _case_payload(state: dict, recovered: bool = True) -> dict:
    """案例 payload;失败案例(recovered=False)doc_id 加 -fail 前缀,text 含避坑信息。"""
    run_id = state.get("run_id", 0)
    doc_id = f"case-{run_id}" if recovered else f"case-{run_id}-fail"
    text = _case_text(state)
    if not recovered:
        text = (
            f"失败案例(避坑):{text}\n"
            f"失败原因:{state.get('termination_reason', '')}\n"
            f"尝试路径:{state.get('reflection_log') or []}"
        )
    return {"doc_id": doc_id,
            "title": "历史诊断案例",
            "text": text,
            "root_cause_code": state.get("root_cause_code", ""),
            "fault_category": state.get("fault_category") or state.get("root_cause_code", ""),
            "recovered": recovered,
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id}


def _get_store():
    from app.rag.embedder import Embedder
    from app.rag.runbook_store import RunbookStore
    return RunbookStore(embedder=Embedder(), collection_alias=CASE_COLLECTION)


def _upsert(state: dict, vector: list, payload: dict) -> None:
    store = _get_store()
    store.ensure_collection(settings.embedding_dimensions)
    store.upsert(point_id=state.get("run_id", 0), vector=vector, payload=payload)


def record_case(state: dict, store=None) -> None:
    """report 节点后调用:recovered 沉淀成功案例;反思用尽(reflection_exhausted)沉淀失败案例。
    store 可选注入(测试用);默认走 _upsert。任何失败不阻塞诊断。"""
    status = state.get("status")
    is_success = status == "recovered"
    is_reflection_failure = (
        status == "needs_human"
        and state.get("termination_reason") == "reflection_exhausted"
    )
    if not (is_success or is_reflection_failure):
        return
    try:
        from app.rag.embedder import Embedder
        vec = Embedder().embed(_case_text(state))
        if not vec:
            logger.warning("案例 embedding 失败,跳过沉淀")
            return
        payload = _case_payload(state, recovered=is_success)
        if store is not None:
            store.upsert(point_id=state.get("run_id", 0), vector=vec, payload=payload)
        else:
            _upsert(state, vec, payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning("案例沉淀失败(不阻塞): %s", exc)
