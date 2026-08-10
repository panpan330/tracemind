"""SSE 事件流:快照先行 + Last-Event-ID 断线补发 + 轮询新事件 + heartbeat。"""
import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.repositories import event_repo, incident_repo

router = APIRouter(prefix="/api/incidents")

POLL_SECONDS = 2
HEARTBEAT_SECONDS = 20

TERMINAL_STATUSES = {"recovered", "needs_human", "rejected", "failed"}


def _format_event(event) -> str:
    payload = json.dumps(event.payload, ensure_ascii=False) if event.payload else "{}"
    return f"event: {event.event_type}\ndata: {payload}\nid: {event.sequence}\n\n"


async def _event_stream(incident_id: int, after: int):
    yield f"event: snapshot\ndata: {json.dumps({'incident_id': incident_id}, ensure_ascii=False)}\nid: 0\n\n"
    events = event_repo.list_events(incident_id, after)
    for ev in events:
        yield _format_event(ev)
    last = events[-1].sequence if events else after
    while True:
        new_events = event_repo.list_events(incident_id, last)
        if new_events:
            for ev in new_events:
                yield _format_event(ev)
            last = new_events[-1].sequence
        else:
            yield ": ping\n\n"
        # Incident 进入终态:发最终事件并关闭连接(不再轮询)
        inc = incident_repo.get_incident(incident_id)
        if inc is not None and inc.status in TERMINAL_STATUSES:
            yield (f"event: incident_finished\ndata: "
                   f"{json.dumps({'status': inc.status}, ensure_ascii=False)}\n"
                   f"id: {last}\n\n")
            return
        await asyncio.sleep(POLL_SECONDS)


@router.get("/{incident_id}/stream")
async def stream(incident_id: int, request: Request):
    inc = incident_repo.get_incident(incident_id)
    if inc is None:
        raise HTTPException(404, "incident not found")
    try:
        after = int(request.headers.get("last-event-id", "0"))
    except ValueError:
        after = 0
    return StreamingResponse(
        _event_stream(incident_id, after),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
