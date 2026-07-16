"""
WebSocket 通信层
处理前端长连接、消息路由、心跳保活、断线重连、用户跨设备同步
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from backend.agent import NexusAgentLoop
from backend.core.config import settings
from backend.core.security import decode_access_token
from backend.database import get_db_context
from backend.repositories.session_repo import AsyncSessionRepository
from backend.schemas.ws import SyncRequest, UserInput

from .dependencies import (
    get_context_flow_repo,
    get_ctx_item_repo,
    get_message_repo,
    get_notification_repo,
    get_session_repo,
    get_task_repo,
    get_user_repo,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class ConnectionManager:
    """
    WebSocket 连接管理器

    管理 session_id -> WebSocket 的映射，支持：
    - 单 session 单连接（新连接踢掉旧连接）
    - 用户级广播（同一用户的所有设备收到通知）
    - 广播消息到指定 session
    - 心跳检测
    - 后台任务跟踪与清理（防止内存泄漏）
    """

    def __init__(self):
        self._connections: dict[uuid.UUID, WebSocket] = {}
        # user_id -> set of session_ids
        self._user_sessions: dict[uuid.UUID, set[uuid.UUID]] = {}
        # session_id -> set of running tasks（防止 create_task 内存泄漏）
        self._tasks: dict[uuid.UUID, set[asyncio.Task]] = {}
        # 保护 WebSocket 并发发送（后台任务与主循环可能同时 send）
        self._send_lock = asyncio.Lock()

    async def _safe_close(self, websocket: WebSocket, code: int = 1000, reason: str = "") -> None:
        """安全关闭 WebSocket，避免重复 close 导致 RuntimeError"""
        try:
            await websocket.close(code=code, reason=reason)
        except RuntimeError as e:
            if "close message has been sent" in str(e):
                return
            raise
        except Exception:
            pass

    def _track_task(self, session_id: uuid.UUID, task: asyncio.Task) -> None:
        """跟踪一个后台任务，任务完成时自动移除"""
        if session_id not in self._tasks:
            self._tasks[session_id] = set()
        self._tasks[session_id].add(task)
        # 任务完成时自动清理
        task.add_done_callback(
            lambda t, sid=session_id: self._tasks.get(sid, set()).discard(t)
        )

    def _cancel_session_tasks(self, session_id: uuid.UUID) -> None:
        """取消指定 session 的所有后台任务"""
        tasks = self._tasks.pop(session_id, set())
        for task in tasks:
            if not task.done():
                task.cancel()

    async def connect(
        self,
        session_id: uuid.UUID,
        websocket: WebSocket,
        user_id: uuid.UUID | None = None,
    ) -> None:
        """建立连接，如果该 session 已有连接则先关闭旧连接"""
        # 先取消旧连接的所有任务
        self._cancel_session_tasks(session_id)

        if session_id in self._connections:
            old_ws = self._connections[session_id]
            try:
                await old_ws.close(code=1001, reason="New connection established")
            except Exception:
                pass
            logger.info(f"Replaced old WebSocket connection for session {session_id}")

        self._connections[session_id] = websocket

        # 初始化任务集合
        self._tasks[session_id] = set()

        # 关联用户
        if user_id:
            if user_id not in self._user_sessions:
                self._user_sessions[user_id] = set()
            self._user_sessions[user_id].add(session_id)

        logger.info(
            f"WebSocket connected: session={session_id}, user={user_id}, "
            f"total={len(self._connections)}"
        )

    def disconnect(self, session_id: uuid.UUID, user_id: uuid.UUID | None = None) -> None:
        """断开连接并清理后台任务"""
        # 取消所有后台任务
        self._cancel_session_tasks(session_id)

        self._connections.pop(session_id, None)

        if user_id and user_id in self._user_sessions:
            self._user_sessions[user_id].discard(session_id)
            if not self._user_sessions[user_id]:
                del self._user_sessions[user_id]

        logger.info(f"WebSocket disconnected: session={session_id}, total={len(self._connections)}")

    async def broadcast(
        self, session_id: uuid.UUID, message: dict[str, Any]
    ) -> None:
        """向指定 session 的 WebSocket 发送消息（连接不存在时静默忽略）"""
        ws = self._connections.get(session_id)
        if ws is None:
            return
        try:
            # 未 accept 的连接不能发送；FastAPI 会在 client 连接时自动 accept，
            # 但此处防御性检查，避免 'not connected' 错误。
            if not getattr(ws, 'client_state', None) or ws.client_state.value != 1:
                # 0=CONNECTING, 1=CONNECTED; 非 CONNECTED 不发
                return
            async with self._send_lock:
                await ws.send_json(message)
        except Exception as e:
            logger.debug(f"Failed to send message to session {session_id}: {e}")
            self.disconnect(session_id)

    async def broadcast_to_user(
        self, user_id: uuid.UUID, message: dict[str, Any], exclude_session: uuid.UUID | None = None
    ) -> None:
        """向同一用户的所有设备广播消息（跨设备同步）"""
        session_ids = self._user_sessions.get(user_id, set()).copy()
        for sid in session_ids:
            if exclude_session and sid == exclude_session:
                continue
            await self.broadcast(sid, message)

    async def send_text(self, session_id: uuid.UUID, text: str) -> None:
        """向指定 session 发送文本"""
        ws = self._connections.get(session_id)
        if ws is None:
            return
        try:
            await ws.send_text(text)
        except Exception as e:
            logger.error(f"Failed to send text to session {session_id}: {e}")
            self.disconnect(session_id)

    def is_connected(self, session_id: uuid.UUID) -> bool:
        return session_id in self._connections


# 全局连接管理器单例
manager = ConnectionManager()


@router.websocket("/ws/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    session_id: uuid.UUID,
    token: str = Query(""),
    session_repo=Depends(get_session_repo),
    message_repo=Depends(get_message_repo),
    task_repo=Depends(get_task_repo),
    ctx_item_repo=Depends(get_ctx_item_repo),
    context_flow_repo=Depends(get_context_flow_repo),
    user_repo=Depends(get_user_repo),
    notification_repo=Depends(get_notification_repo),
):
    """
    WebSocket 端点

    消息格式（JSON）：
    - {"type": "user_input", "content": "..."}     -> 用户输入，触发 Agent Loop
    - {"type": "sync", "last_message_id": "..."}   -> 断线重连同步请求
    - {"type": "ping"}                              -> 心跳
    """

    # ---- 用户认证（优先 query token；否则 accept 后等首条 auth 消息）----
    # 注意：WebSocket 只能 accept 一次。消息鉴权路径会先 accept，后面禁止再 accept。
    token_from_query = (token or "").strip()
    token_from_message = None
    accepted = False

    async def _accept_once() -> None:
        nonlocal accepted
        if not accepted:
            await websocket.accept()
            accepted = True

    if not token_from_query:
        await _accept_once()
        try:
            raw_auth = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
            auth_data = json.loads(raw_auth)
            if auth_data.get("type") == "auth":
                token_from_message = auth_data.get("token", "")
        except (asyncio.TimeoutError, json.JSONDecodeError, Exception):
            try:
                await websocket.send_json(
                    {"type": "error", "detail": "Authentication required"}
                )
            except Exception:
                pass
            await self._safe_close(websocket, code=1008, reason="Authentication required")
            return

    effective_token = token_from_query or token_from_message or ""
    if not effective_token:
        await _accept_once()
        try:
            await websocket.send_json(
                {"type": "error", "detail": "Authentication required"}
            )
        except Exception:
            pass
        await self._safe_close(websocket, code=1008, reason="Authentication required")
        return

    payload = decode_access_token(effective_token)
    if not payload or "sub" not in payload:
        await _accept_once()
        try:
            await websocket.send_json(
                {"type": "error", "detail": "Invalid or expired token"}
            )
        except Exception:
            pass
        await self._safe_close(websocket, code=1008, reason="Invalid or expired token")
        return

    try:
        user_id = uuid.UUID(payload["sub"])
    except ValueError:
        await _accept_once()
        try:
            await websocket.send_json(
                {"type": "error", "detail": "Invalid token"}
            )
        except Exception:
            pass
        await self._safe_close(websocket, code=1008, reason="Invalid token")
        return

    # query-token 路径此前尚未 accept
    await _accept_once()

    # ---- 长会话保持：检查/创建 session（同一事务） ----
    try:
        async with get_db_context() as db:
            session_repo_tx = AsyncSessionRepository(db)
            session = await session_repo_tx.get_by_id(session_id)
            if session:
                # 检查是否过期
                expires_at = getattr(session, "expires_at", None)
                if expires_at and expires_at < datetime.now(timezone.utc):
                    await websocket.send_json(
                        {"type": "error", "detail": "Session expired"}
                    )
                    await self._safe_close(websocket, code=1008, reason="Session expired")
                    return

                # 会话用户隔离检查
                if session.user_id != user_id:
                    await websocket.send_json(
                        {"type": "error", "detail": "Session access denied"}
                    )
                    await self._safe_close(websocket, code=1008, reason="Session access denied")
                    return
            else:
                # Session 不存在，自动创建（使用前端传入的 session_id）
                session = await session_repo_tx.create(
                    {"id": session_id, "user_id": user_id, "config": {}}
                )
    except Exception as e:
        logger.warning(f"Session validation warning: {e}")
        session = None

    await manager.connect(session_id, websocket, user_id=user_id)

    # 初始化 Agent Loop
    agent = NexusAgentLoop(
        session_repo=session_repo,
        message_repo=message_repo,
        task_repo=task_repo,
        ctx_item_repo=ctx_item_repo,
        context_flow_repo=context_flow_repo,
        ws_manager=manager,
        agent_name="Takton",
        user_id=user_id,
        notification_repo=notification_repo,
    )

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await manager.broadcast(
                    session_id, {"type": "error", "detail": "Invalid JSON"}
                )
                continue

            msg_type = data.get("type")

            if msg_type == "ping":
                await manager.broadcast(session_id, {"type": "pong"})

            elif msg_type == "user_input":
                user_input = data.get("content", "").strip()
                if not user_input:
                    continue

                attachments = data.get("attachments", [])
                mode = data.get("mode", "default")

                # 重置停止信号（新消息重置）
                agent._should_stop = False

                # 同步运行 Agent Loop（不在后台执行，确保流式推送可靠）
                await _run_agent_safe(agent, session_id, user_input, attachments, mode)

            elif msg_type == "stop":
                logger.info(f"Stop signal received for session {session_id}")
                agent.stop()
                await manager.broadcast(
                    session_id,
                    {"type": "status", "state": "idle", "detail": "Generation stopped by user"},
                )

            elif msg_type == "sync":
                last_id = data.get("last_message_id")
                if last_id:
                    try:
                        last_uuid = uuid.UUID(last_id)
                        messages = await message_repo.get_messages_after(
                            session_id, last_uuid
                        )
                        await manager.broadcast(
                            session_id,
                            {
                                "type": "sync_response",
                                "messages": [
                                    {
                                        "id": str(m.id),
                                        "role": m.role,
                                        "content": m.content,
                                        "created_at": m.created_at.isoformat(),
                                    }
                                    for m in messages
                                ],
                            },
                        )
                    except Exception as e:
                        logger.error(f"Sync error: {e}")
                        await manager.broadcast(
                            session_id, {"type": "error", "detail": f"Sync failed: {e}"}
                        )

            elif msg_type == "auth":
                # 支持在连接后通过消息进行认证
                new_token = data.get("token", "")
                if new_token:
                    payload = decode_access_token(new_token)
                    if payload and "sub" in payload:
                        try:
                            new_user_id = uuid.UUID(payload["sub"])
                            user_id = new_user_id
                            # 更新用户 session 映射
                            if user_id not in manager._user_sessions:
                                manager._user_sessions[user_id] = set()
                            manager._user_sessions[user_id].add(session_id)
                            await manager.broadcast(
                                session_id, {"type": "auth_ok", "user_id": str(user_id)}
                            )
                        except ValueError:
                            await manager.broadcast(
                                session_id, {"type": "error", "detail": "Invalid token"}
                            )

            else:
                await manager.broadcast(
                    session_id,
                    {"type": "error", "detail": f"Unknown message type: {msg_type}"},
                )

    except WebSocketDisconnect:
        manager.disconnect(session_id, user_id=user_id)
    except Exception as e:
        logger.error(f"WebSocket error for session {session_id}: {e}")
        manager.disconnect(session_id, user_id=user_id)


async def _run_agent_safe(
    agent: NexusAgentLoop, session_id: uuid.UUID, user_input: str, attachments: list = None, mode: str = "default"
) -> None:
    """安全地运行 Agent Loop，捕获异常"""
    try:
        await agent.run(session_id, user_input, attachments=attachments or [], mode=mode)
    except Exception as e:
        logger.exception(f"Agent loop failed for session {session_id}: {e}")
        await manager.broadcast(
            session_id,
            {
                "type": "error",
                "detail": f"Agent error: {str(e)}",
            },
        )
