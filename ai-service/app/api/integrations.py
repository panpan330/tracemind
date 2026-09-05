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

    limit = settings.alertmanager_max_body_bytes
    # V2.1-A closure:先查 Content-Length(存在且超限立即 413);
    # 缺失/伪造时经 stream() 累计读取,超限立即中断 —— 超限请求不进入解析/服务/数据库
    content_length = request.headers.get("content-length")
    if content_length is not None and content_length.isdigit()             and int(content_length) > limit:
        raise HTTPException(413, f"payload too large: content-length={content_length}")
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > limit:
            raise HTTPException(413, f"payload too large: >{limit} bytes")
    body = bytes(buf)
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
