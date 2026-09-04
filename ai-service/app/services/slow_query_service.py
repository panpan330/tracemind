from sqlalchemy import text

from app.db.engine import get_readonly_engine
from app.services.baseline_service import TARGET_DIGEST_LIKE


def _load_run_baseline(incident_id: int, agent_run_id: int) -> dict:
    """V2.0-B closure:按 agent_run_id 读取该 Run 冻结的 digest 基线(fail closed)。
    禁止再以 incident_id + ORDER BY id DESC 猜"最近 Run"(多 Run 会用错基线)。"""
    from app.repositories import run_repo
    from app.tools_core.errors import ToolBusinessError

    if not agent_run_id:
        raise ToolBusinessError(
            "RUN_CONTEXT_UNRESOLVED",
            "digest 调查需要 agent_run_id(禁止按 incident 猜最近 Run)", retryable=False)
    run = run_repo.get_run(agent_run_id)
    if run is None or run.incident_id != incident_id:
        raise ToolBusinessError(
            "RUN_CONTEXT_UNRESOLVED",
            f"agent_run {agent_run_id} 缺失或不属于 incident {incident_id}", retryable=False)
    baseline = run.incident_digest_baseline
    return baseline if isinstance(baseline, dict) else {}


def _fetch_current_digests() -> dict[str, dict]:
    with get_readonly_engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT DIGEST_TEXT, COUNT_STAR, SUM_TIMER_WAIT, SUM_ROWS_EXAMINED
            FROM performance_schema.events_statements_summary_by_digest
            WHERE DIGEST_TEXT LIKE :p"""), {"p": TARGET_DIGEST_LIKE}).mappings().all()
    return {r["DIGEST_TEXT"]: {"count": int(r["COUNT_STAR"]),
                               "total_latency_us": int(r["SUM_TIMER_WAIT"]) // 1000,
                               "rows_examined": int(r["SUM_ROWS_EXAMINED"])}
            for r in rows}


def list_expensive_digests(incident_id: int, agent_run_id: int = 0) -> list[dict]:
    """E3:Incident 期间目标 SQL 的执行次数/耗时/扫描行数增量(基线差值)。
    基线 = agent_run_id 对应 Run 的冻结快照(V2.0-B closure)。"""
    baseline = _load_run_baseline(incident_id, agent_run_id)

    current = _fetch_current_digests()

    delta = []
    for digest, cur in current.items():
        base = baseline.get(digest, {"count": 0, "total_latency_us": 0, "rows_examined": 0})
        delta.append({
            "digest": digest[:200],
            "count_delta": cur["count"] - base["count"],
            "total_latency_us_delta": cur["total_latency_us"] - base["total_latency_us"],
            "rows_examined_delta": cur["rows_examined"] - base["rows_examined"],
        })
    delta.sort(key=lambda d: -d["rows_examined_delta"])
    return delta
