from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.db.models import utcnow
from app.replay.writer import ReplayWriter
from app.repositories import approval_repo, run_repo
from app.services.runner import resume_investigation

router = APIRouter(prefix="/api/incidents")

replay_writer: ReplayWriter | None = None  # 测试注入;生产每次新建


def _record_approval_decided(incident_id: int, run_id: int, approval_id: int,
                             decision: str, comment: str | None = None) -> None:
    """审批决定回放步骤(外部 API):businessKey=approval:{id} 幂等,避免重复提交生成重复步骤。"""
    try:
        writer = replay_writer or ReplayWriter(incident_id, run_id)
        business_key = f"approval:{approval_id}"
        lid = writer.existing_logical_id("APPROVAL_DECIDED", business_key)
        if lid is not None:
            return  # 幂等:同一审批已记录(客户端重试/并发提交不重复生成步骤)
        lid = f"ls-app-{approval_id}"
        decision_payload = {"decision": decision, "comment": comment}
        writer.write("APPROVAL_DECIDED", "started", logical_step_id=lid,
                     step_title="审批决定", decision=decision_payload,
                     source_refs={"approval_id": approval_id,
                                  "businessKey": business_key})
        writer.complete("APPROVAL_DECIDED", lid, outcome=decision,
                        source_refs={"approval_id": approval_id,
                                     "businessKey": business_key})
    except Exception as exc:  # 回放写入失败不阻塞审批
        import logging
        logging.getLogger("replay").warning("approval_decided 回放写入失败: %s", exc)


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

    # V1.5 回放:审批决定步骤(幂等)
    runs = run_repo.list_runs(incident_id)
    run_id = getattr(runs[0], "id", None) if runs else None
    if run_id:
        _record_approval_decided(incident_id, run_id, approval_id,
                                 body.decision, body.comment)

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
