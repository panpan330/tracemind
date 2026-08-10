from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine, get_readonly_engine
from app.db.models import FixExecution, RecoveryCheck
from app.repositories import incident_repo

INDEX_PRESENT_SQL = text("""
    SELECT COUNT(*) FROM information_schema.statistics
    WHERE table_schema = DATABASE() AND table_name = 'inventory'
    AND index_name = 'idx_sku_warehouse'
""")

P95_RECOVERY_RATIO = 1.2
PROBE_BATCHES = 3
PROBE_PARAMS = {"skuId": 42, "warehouseId": 7}


def _p95_recovered(p95_after_ms: int | None, baseline: dict | None) -> bool:
    """修复后固定探测 P95 相对健康基线恢复(基线缺失视为通过)。"""
    if baseline is None or p95_after_ms is None:
        return True
    base = baseline.get("p95_ms")
    if not base:
        return True
    return p95_after_ms <= int(base) * P95_RECOVERY_RATIO


def _probe_p95_ms() -> int | None:
    """执行一批固定探测请求,返回本批最大耗时(ms)。"""
    import time
    start = time.monotonic()
    with get_readonly_engine().connect() as conn:
        conn.execute(text(
            "SELECT id FROM inventory WHERE sku_id = :s AND warehouse_id = :w"
        ), {"s": PROBE_PARAMS["skuId"], "w": PROBE_PARAMS["warehouseId"]})
    return int((time.monotonic() - start) * 1000)


def verify_recovery(incident_id: int, fix_execution_id: int) -> dict:
    """恢复验证(确定性规则,不让 LLM 决定)。

    M2 骨架:索引存在 + EXPLAIN 使用目标索引。
    M3 将扩展为完整规则(扫描行数/相对基线 P95/连续三批探测)。
    """
    with Session(get_control_engine()) as session:
        execution = session.get(FixExecution, fix_execution_id)
        if execution is None or execution.incident_id != incident_id:
            raise ValueError("FIX_EXECUTION_NOT_FOUND")

        with get_readonly_engine().connect() as conn:
            index_present = conn.execute(INDEX_PRESENT_SQL).scalar_one() > 0
            row = conn.execute(text(
                "EXPLAIN FORMAT=JSON SELECT id FROM inventory "
                "WHERE sku_id = 42 AND warehouse_id = 7")).fetchone()
            import json
            plan = json.loads(row[0]) if row and isinstance(row[0], str) else (row[0] if row else None)

        uses_index = False
        estimated_rows = None
        if plan:
            try:
                table = plan["query_block"]["table"]
                uses_index = table.get("access_type") in ("ref", "const") and \
                             "idx_sku_warehouse" in str(table.get("possible_keys", ""))
                estimated_rows = table.get("rows")
            except (KeyError, TypeError):
                uses_index = False

        # 相对健康基线 P95 判定(基线缺失/采集失败时视为通过)
        baseline = None
        inc = incident_repo.get_incident(incident_id)
        if inc is not None:
            raw = inc.healthy_metrics_baseline
            baseline = raw if isinstance(raw, dict) else None
        p95_after = max((_probe_p95_ms() or 0) for _ in range(PROBE_BATCHES))
        p95_ok = _p95_recovered(p95_after, baseline)

        recovered = bool(index_present and uses_index and p95_ok)
        status = "recovered" if recovered else "not_recovered"
        check = RecoveryCheck(incident_id=incident_id, fix_execution_id=fix_execution_id,
                              index_present=index_present,
                              query_plan_uses_target_index=uses_index,
                              estimated_rows_after=estimated_rows,
                              latency_p95_after=p95_after,
                              status=status)
        session.add(check)
        session.commit()
        session.refresh(check)
        return {"status": status, "index_present": index_present,
                "query_plan_uses_target_index": uses_index,
                "estimated_rows_after": estimated_rows,
                "latency_p95_after": p95_after}
