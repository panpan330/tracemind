from sqlalchemy import text

from app.db.engine import get_control_engine, get_readonly_engine
from app.services.baseline_service import TARGET_DIGEST_LIKE


def list_expensive_digests(incident_id: int) -> list[dict]:
    """E3:Incident 期间目标 SQL 的执行次数/耗时/扫描行数增量(基线差值)。"""
    with get_control_engine().connect() as conn:
        row = conn.execute(text(
            "SELECT incident_digest_baseline FROM agent_run "
            "WHERE incident_id = :i ORDER BY id DESC LIMIT 1"), {"i": incident_id}).fetchone()
        raw = row[0] if row else None
        if isinstance(raw, str):  # 原生 SQL 读 JSON 列返回 str,含 'null'
            import json
            raw = json.loads(raw)
        baseline = raw if isinstance(raw, dict) else {}

    current: dict[str, dict] = {}
    with get_readonly_engine().connect() as conn:
        for r in conn.execute(text("""
            SELECT DIGEST_TEXT, COUNT_STAR, SUM_TIMER_WAIT, SUM_ROWS_EXAMINED
            FROM performance_schema.events_statements_summary_by_digest
            WHERE DIGEST_TEXT LIKE :p"""), {"p": TARGET_DIGEST_LIKE}):
            current[r.DIGEST_TEXT] = {
                "count": int(r.COUNT_STAR),
                "total_latency_us": int(r.SUM_TIMER_WAIT) // 1000,
                "rows_examined": int(r.SUM_ROWS_EXAMINED),
            }

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
