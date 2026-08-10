from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.db.models import utcnow
from app.repositories import approval_repo, run_repo
from app.services.runner import resume_investigation

router = APIRouter(prefix="/api/incidents")


class DecisionIn(BaseModel):
    decision: str  # approved | rejected
    comment: str | None = None


@router.post("/{incident_id}/approvals/{approval_id}/decision")
async def decide(incident_id: int, approval_id: int, body: DecisionIn) -> dict:
    approval = approval_repo.get_approval(approval_id)
    if approval is None or approval.incident_id != incident_id:
        raise HTTPException(404, "approval not found")
    if approval.status != "pending":
        raise HTTPException(409, f"approval already {approval.status}")
    if approval.expires_at and approval.expires_at < utcnow():
        raise HTTPException(409, "approval expired")
    if body.decision not in ("approved", "rejected"):
        raise HTTPException(422, "decision must be 'approved' or 'rejected'")

    # 审批人身份由服务端确定,不信任请求体
    approval_repo.update_approval(
        approval_id,
        status=body.decision,
        approver=settings.demo_approver_id,
        comment=body.comment,
    )

    # 恢复 LangGraph(thread_id 取该 incident 最近一次 run)
    runs = run_repo.list_runs(incident_id)
    if runs:
        await resume_investigation(runs[0].thread_id, {
            "decision": body.decision,
            "comment": body.comment,
        })
    return {
        "incident_id": incident_id,
        "approval_id": approval_id,
        "status": body.decision,
        "approved_by": settings.demo_approver_id,
    }
