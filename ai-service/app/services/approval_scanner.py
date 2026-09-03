"""过期审批扫描:每 30 秒将 pending 且过期的 Approval 置 expired,并恢复图进入 report。"""
import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import Approval, utcnow
from app.repositories import approval_repo, incident_repo, run_repo
from app.services.runner import resume_investigation

logger = logging.getLogger(__name__)

SCAN_INTERVAL_SECONDS = 30


async def scan_expired_approvals_once() -> int:
    now = utcnow()
    expired_ids: list[int] = []
    with Session(get_control_engine()) as session:
        rows = session.scalars(select(Approval).filter(
            Approval.status == "pending",
            Approval.expires_at.is_not(None),
            Approval.expires_at < now,
        )).all()
        for approval in rows:
            approval.status = "expired"
            expired_ids.append(approval.id)
        session.commit()

    for approval_id in expired_ids:
        approval = approval_repo.get_approval(approval_id)
        if approval is None:
            continue
        # V2.0-A closure:仅恢复 approval.agent_run_id 绑定的 Run。
        # NULL/无效/不匹配绑定 → fail closed:不恢复任何 Run(禁止猜最近 Run),
        # Incident 转人工(termination_reason 列宽 64,详情进日志)。
        run = None
        if approval.agent_run_id:
            candidate = run_repo.get_run(approval.agent_run_id)
            if candidate is not None and candidate.incident_id == approval.incident_id:
                run = candidate
        if run is None:
            reason = ("approval_run_binding_missing" if not approval.agent_run_id
                      else "approval_run_binding_invalid")
            logger.error("approval %s 无法恢复(绑定损坏): %s", approval_id, reason)
            try:
                incident_repo.update_status(approval.incident_id, "needs_human",
                                            termination_reason=reason)
            except Exception:  # noqa: BLE001 标记失败不阻塞扫描
                logger.exception("scanner 标记 needs_human 失败 approval=%s", approval_id)
            continue
        await resume_investigation(
            run.thread_id,
            {"decision": "rejected", "comment": "expired"},
        )
    return len(expired_ids)


async def scanner_loop() -> None:
    while True:
        try:
            await scan_expired_approvals_once()
        except Exception:
            logger.exception("approval scanner error")
        await asyncio.sleep(SCAN_INTERVAL_SECONDS)
