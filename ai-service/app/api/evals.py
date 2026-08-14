"""V1.13 评测平台 API:评测记录列表/详情。"""
from fastapi import APIRouter, HTTPException

from app.repositories import eval_run_repo

router = APIRouter(prefix="/api/evals", tags=["evals"])


@router.get("")
def list_evals() -> list[dict]:
    return eval_run_repo.list_eval_runs()


@router.get("/{eval_run_id}")
def get_eval(eval_run_id: int) -> dict:
    row = eval_run_repo.get_eval_run(eval_run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="eval run not found")
    return row
