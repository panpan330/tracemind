"""V2.1-A:Alertmanager webhook 接入端点(鉴权 + 边界校验 + 网关服务)。"""
from fastapi import APIRouter, HTTPException, Request

from app.config import settings
from app.incident_gateway import service as gateway_service
from app.incident_gateway.schemas import AlertmanagerWebhookIn

router = APIRouter(prefix="/api/integrations")

MAX_ALERTS_DEFAULT = 20


@router.post("/alertmanager/webhook")
async def alertmanager_webhook(request: Request) -> dict:
    token = settings.alertmanager_webhook_token
    if not token:
        # 未配置即禁用:不允许无鉴权的告警入口
        raise HTTPException(403, "alertmanager webhook disabled(未配置 token)")
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer ") or not _consteq(auth[len("Bearer "):].strip(), token):
        raise HTTPException(401, "unauthorized")

    body = await request.body()
    if len(body) > settings.alertmanager_max_body_bytes:
        raise HTTPException(413, f"payload too large: {len(body)} bytes")
    try:
        payload = AlertmanagerWebhookIn.model_validate_json(body)
    except ValueError as exc:
        raise HTTPException(422, f"invalid payload: {exc}") from exc
    if len(payload.alerts) > settings.alertmanager_max_alerts:
        raise HTTPException(422, f"too many alerts: {len(payload.alerts)} "
                                 f"> {settings.alertmanager_max_alerts}")

    return gateway_service.process_alert_batch("alertmanager", payload)


def _consteq(a: str, b: str) -> bool:
    import hmac
    return hmac.compare_digest(a.encode(), b.encode())
