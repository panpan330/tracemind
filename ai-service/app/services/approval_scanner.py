"""过期审批扫描:每 30 秒将 pending 且过期的 Approval 置 expired,并恢复图进入 report。"""
import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import Approval, utcnow
from app.repositories import approval_repo, run_repo
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
        runs = run_repo.list_runs(approval.incident_id)
        if runs:
            await resume_investigation(
                runs[0].thread_id,
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
