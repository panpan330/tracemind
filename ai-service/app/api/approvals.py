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


def _run_for_approval(approval) -> object | None:
    """V2.0-A:审批 → 准确 Run(按 agent_run_id 绑定,禁止按 incident 猜最近 Run)。
    仅存量 NULL 绑定行(009 迁移前创建)回退 incident 最近一次 Run。"""
    if approval.agent_run_id:
        run = run_repo.get_run(approval.agent_run_id)
        if run is not None:
            return run
    runs = run_repo.list_runs(approval.incident_id)
    return runs[0] if runs else None


@router.post("/{incident_id}/approvals/{approval_id}/decision")
async def decide(incident_id: int, approval_id: int, body: DecisionIn) -> dict:
    approval = approval_repo.get_approval(approval_id)
    if approval is None or approval.incident_id != incident_id:
        raise HTTPException(404, "approval not found")
    if body.decision not in ("approved", "rejected"):
        raise HTTPException(422, "decision must be 'approved' or 'rejected'")

    # V2.0-A 审批 CAS:pending + 未过期原子裁决;now_utc 由应用生成(不依赖 DB NOW())
    decided = approval_repo.decide_approval_cas(
        approval_id,
        decision=body.decision,
        approver=settings.demo_approver_id,
        comment=body.comment,
        now_utc=utcnow(),
    )
    if decided is None:
        fresh = approval_repo.get_approval(approval_id)
        if fresh is None:
            raise HTTPException(404, "approval not found")
        if fresh.status == "pending":
            raise HTTPException(409, "approval expired")
        raise HTTPException(409, f"approval already {fresh.status}")

    run = _run_for_approval(approval)
    if run is not None:
        # V1.5 回放:审批决定步骤(幂等);绑定审批所属 Run
        _record_approval_decided(incident_id, run.id, approval_id,
                                 body.decision, body.comment)
        # 恢复 LangGraph(该审批所属 Run 的 checkpoint thread)
        await resume_investigation(run.thread_id, {
            "decision": body.decision,
            "comment": body.comment,
        })
    return {
        "incident_id": incident_id,
        "approval_id": approval_id,
        "status": body.decision,
        "approved_by": settings.demo_approver_id,
    }
