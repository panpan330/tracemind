"""get_query_plan handler:经 PlanPort 端口获取数据(不含业务实现)。"""
from app.tools_core.errors import ToolBusinessError


from app.tools_core.ports import PlanPort

def build(ports: dict) -> dict:
    p = ports.get("plan")

    def get_query_plan(query_ref: str, sample_parameters: dict) -> dict:
        if p is None:
            raise ToolBusinessError("PORT_UNAVAILABLE", "plan 端口未配置", retryable=False)
        try:
            return p.explain(query_ref, sample_parameters)
        except ToolBusinessError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ToolBusinessError("PLAN_QUERY_FAILED", str(e), retryable=True) from e

    return {"get_query_plan": get_query_plan}
