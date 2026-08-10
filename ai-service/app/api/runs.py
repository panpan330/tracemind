from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.repositories import incident_repo, run_repo

router = APIRouter(prefix="/api/incidents")


class ToolCallIn(BaseModel):
    tool: str
    args: dict


@router.post("/{incident_id}/investigations", status_code=202)
def start_investigation(incident_id: int):
    inc = incident_repo.get_incident(incident_id)
    if inc is None:
        raise HTTPException(404, "incident not found")
    # 取该 incident 最近一次 digest 基线(创建 Incident 时已采集)
    runs = run_repo.list_runs(incident_id)
    baseline = runs[0].incident_digest_baseline if runs else None
    run = run_repo.create_run(incident_id, baseline=baseline)
    return {"run_id": run.id, "thread_id": run.thread_id, "status": run.status}


@router.get("/{incident_id}/runs/{run_id}")
def get_run(incident_id: int, run_id: int):
    run = run_repo.get_run(run_id)
    if run is None or run.incident_id != incident_id:
        raise HTTPException(404, "run not found")
    return {"run_id": run.id, "thread_id": run.thread_id, "status": run.status,
            "investigation_round": run.investigation_round,
            "tool_call_count": run.tool_call_count,
            "started_at": str(run.started_at),
            "finished_at": str(run.finished_at) if run.finished_at else None}


@router.post("/{incident_id}/tools")
def call_tool(incident_id: int, payload: ToolCallIn):
    """统一工具调用入口(LLM_MODE=fake 时即演示入口;M3 由 Agent 内部调用)。"""
    from app.tools.execute import execute_tool
    args = dict(payload.args)
    args.pop("incident_id", None)  # 路径参数为准,防调用方伪造
    return execute_tool(payload.tool, incident_id=incident_id, **args)
