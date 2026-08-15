"""V1.13/1.16 评测平台 API:评测记录列表/详情 + 触发运行。"""
import threading

from fastapi import APIRouter, HTTPException

from app.repositories import eval_run_repo

router = APIRouter(prefix="/api/evals", tags=["evals"])

_VALID_SCENARIOS = {"SCN-001", "SCN-002"}


def _run_eval_background(scenario: str, rounds: int) -> None:
    """后台跑一轮评测,结果自动写 eval_run。"""
    from scripts.eval_agent_report import run_evals
    run_evals(base="http://localhost:8000", order="http://localhost:8081",
              rounds=rounds, scenario=scenario)


@router.get("")
def list_evals() -> list[dict]:
    return eval_run_repo.list_eval_runs()


@router.get("/{eval_run_id}")
def get_eval(eval_run_id: int) -> dict:
    row = eval_run_repo.get_eval_run(eval_run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="eval run not found")
    return row


@router.post("/run", status_code=202)
def run_eval(payload: dict) -> dict:
    scenario = payload.get("scenario", "")
    try:
        rounds = int(payload.get("rounds", 1))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="rounds must be int")
    if scenario not in _VALID_SCENARIOS or not 1 <= rounds <= 5:
        raise HTTPException(status_code=400, detail="invalid scenario/rounds")
    t = threading.Thread(target=_run_eval_background, args=(scenario, rounds), daemon=True)
    t.start()
    return {"status": "accepted"}
