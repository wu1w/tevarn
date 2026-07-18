"""危险操作确认管理器

agent 执行危险命令前，通过 WS 向前端弹窗请求确认；
前端用户点「允许/拒绝」后回传 confirm_response，此处唤醒等待的协程。
超时（默认 30s）无响应默认拒绝，防止 agent 卡死。
"""
from __future__ import annotations

import asyncio
import logging
import uuid as _uuid

logger = logging.getLogger(__name__)

# confirm_id -> (asyncio.Event, result_holder)
_pending: dict[str, tuple[asyncio.Event, dict]] = {}

DEFAULT_TIMEOUT = 30.0


async def request_confirmation(
    ws_manager,
    session_id,
    *,
    title: str,
    command: str,
    reason: str = "",
    timeout: float = DEFAULT_TIMEOUT,
) -> bool:
    """推送确认请求并等待用户决定。返回 True=允许，False=拒绝/超时。"""
    if ws_manager is None:
        # 无前端连接时，危险操作默认拒绝（保守）
        logger.warning("confirm: no ws_manager, auto-deny dangerous op: %s", command)
        return False

    confirm_id = _uuid.uuid4().hex[:12]
    event = asyncio.Event()
    holder: dict = {"approved": False}
    _pending[confirm_id] = (event, holder)

    # broadcast 的 _connections 以 UUID 对象为 key，统一转成 UUID
    sid = session_id
    if isinstance(session_id, str):
        try:
            sid = _uuid.UUID(session_id)
        except (ValueError, AttributeError):
            sid = session_id

    try:
        await ws_manager.broadcast(
            sid,
            {
                "type": "confirm_request",
                "session_id": str(session_id),
                "confirm_id": confirm_id,
                "title": title,
                "command": command,
                "reason": reason,
            },
        )
    except Exception as e:
        logger.warning("confirm: broadcast failed: %s", e)
        _pending.pop(confirm_id, None)
        return False

    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
        return bool(holder["approved"])
    except asyncio.TimeoutError:
        logger.info("confirm: timeout (%ss), auto-deny: %s", timeout, command)
        return False
    finally:
        _pending.pop(confirm_id, None)


def resolve_confirmation(confirm_id: str, approved: bool) -> bool:
    """前端回传确认结果，唤醒等待的协程。返回是否找到对应请求。"""
    entry = _pending.get(confirm_id)
    if entry is None:
        return False
    event, holder = entry
    holder["approved"] = approved
    event.set()
    return True
