"""领域事件流：REST 快照 + WebSocket 订阅。

- GET  /kernel/events/domain?limit=50
- WS   /ws/domain?token=…   （query token 鉴权）
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from backend.core.security import decode_access_token
from backend.schemas.user import UserRead

from ..dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["domain-events"])


@router.get("/kernel/events/domain")
async def list_domain_events(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    limit: int = Query(50, ge=1, le=200),
    prefix: str | None = Query(None, description="topic 前缀过滤，如 job."),
    since_ts: float | None = Query(None, description="只返回 ts > since_ts"),
    after_seq: int | None = Query(None, description="只返回 seq > after_seq（续订）"),
):
    """近期领域事件快照（连接 WS 前 / 断线续订可用）。"""
    from backend.kernel.domain_events import current_seq, recent_events

    items = recent_events(
        limit=limit, prefix=prefix, since_ts=since_ts, after_seq=after_seq
    )
    return {
        "events": items,
        "total": len(items),
        "head_seq": current_seq(),
        "cursor": {
            "after_seq": items[-1]["seq"] if items else after_seq,
            "since_ts": items[-1]["ts"] if items else since_ts,
        },
    }


@router.websocket("/ws/domain")
async def domain_event_stream(
    websocket: WebSocket,
    token: str = Query(""),
):
    """全局领域事件订阅：snapshot + 实时 fanout。

    鉴权：query ``token`` = JWT access token。
    消息：
      {"type":"domain_snapshot","events":[...]}
      {"type":"domain_event","topic":"job.done","ts":…,"data":{…}}
      {"type":"ping"} / {"type":"pong"}
    """
    raw = (token or "").strip()
    if not raw:
        await websocket.close(code=4401, reason="token required")
        return
    try:
        payload = decode_access_token(raw)
        if not payload or not payload.get("sub"):
            await websocket.close(code=4401, reason="invalid token")
            return
    except Exception:
        await websocket.close(code=4401, reason="invalid token")
        return

    await websocket.accept()
    from backend.kernel.domain_events import (
        current_seq,
        recent_events,
        subscribe_queue,
        unsubscribe_queue,
    )

    q = subscribe_queue()
    try:
        snap = recent_events(limit=40)
        await websocket.send_json({
            "type": "domain_snapshot",
            "events": snap,
            "head_seq": current_seq(),
        })
        while True:
            # 并行：收客户端 ping 或等队列事件
            get_task = asyncio.create_task(q.get())
            recv_task = asyncio.create_task(websocket.receive_text())
            done, pending = await asyncio.wait(
                {get_task, recv_task},
                return_when=asyncio.FIRST_COMPLETED,
                timeout=25.0,
            )
            for t in pending:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    # Py3.8+ CancelledError is BaseException, not Exception
                    pass
                except Exception:
                    pass
            if not done:
                # timeout → 服务端 ping
                try:
                    await websocket.send_json({"type": "ping"})
                except Exception:
                    break
                continue
            if get_task in done:
                try:
                    evt = get_task.result()
                    await websocket.send_json(evt)
                except Exception:
                    break
            if recv_task in done:
                try:
                    text = recv_task.result()
                    msg = json.loads(text) if text else {}
                    if isinstance(msg, dict) and msg.get("type") == "ping":
                        await websocket.send_json({"type": "pong"})
                except WebSocketDisconnect:
                    break
                except Exception:
                    pass
    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        # 客户端断开 / 服务端取消时勿冒泡成 ASGI Exception
        pass
    except Exception as e:
        logger.debug("domain stream end: %s", e)
    finally:
        unsubscribe_queue(q)
        try:
            await websocket.close()
        except Exception:
            pass
