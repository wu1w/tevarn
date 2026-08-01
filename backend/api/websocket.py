"""
WebSocket 通信层
处理前端长连接、消息路由、心跳保活、断线重连、用户跨设备同步
"""

import asyncio
import contextvars
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

# 当前 agent task 的 run_generation（broadcast 自动打戳，ingest 过滤 late event）
_run_gen_ctx: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "takton_run_generation", default=None
)

from backend.agent import NexusAgentLoop
from backend.core.config import settings
from backend.core.security import decode_access_token
from backend.database import get_db_context
from backend.repositories.session_repo import AsyncSessionRepository

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

# 单 session 流式快照：用户乱切页 / 断线后仍保留 in-flight 状态，回连 sync 可恢复
_MAX_PARTIAL_CHARS = 120_000
_MAX_LIVE_TOOLS = 48


@dataclass
class SessionRunSnapshot:
    """进程内 per-session 运行快照（不落库）。"""

    agent_running: bool = False
    state: str = "idle"
    detail: str = ""
    partial_content: str = ""
    stream_message_id: str | None = None
    live_tools: list[dict[str, Any]] = field(default_factory=list)
    updated_at: float = 0.0
    # 与 ConnectionManager._run_generations 对齐；late event gen 不符则不写入
    generation: int = 0

    def to_sync_fields(self) -> dict[str, Any]:
        return {
            "agent_running": self.agent_running,
            "state": self.state if self.agent_running else "idle",
            "stream_status": self.detail or None,
            "partial_content": self.partial_content if self.agent_running else "",
            "stream_message_id": self.stream_message_id if self.agent_running else None,
            "live_tools": list(self.live_tools) if self.agent_running else [],
            "snapshot_updated_at": self.updated_at or None,
            "run_generation": self.generation or None,
        }


async def safe_close_ws(
    websocket: WebSocket, code: int = 1000, reason: str = ""
) -> None:
    """安全关闭 WebSocket，避免重复 close 导致 RuntimeError。

    模块级函数：websocket_endpoint 不是方法，拿不到 self。此前那里直接写了
    `self._safe_close(...)`，使**每一条认证失败路径都抛 NameError**
    （空/非法 auth 消息、token 过期、token 非法、会话过期、无权访问），
    连接不会被干净关闭，异常反而冒到 ASGI 层。
    """
    try:
        await websocket.close(code=code, reason=reason)
    except RuntimeError as e:
        if "close message has been sent" in str(e):
            return
        raise
    except Exception:
        pass


def _ws_client_is_loopback(websocket: WebSocket) -> bool:
    """WS 对端是否本机。与 dependencies._is_loopback_host 同一口径：
    只信 socket 对端，不信可伪造的 X-Forwarded-For。"""
    from backend.api.dependencies import _is_loopback_host

    client = getattr(websocket, "client", None)
    return _is_loopback_host(getattr(client, "host", None) if client else None)


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
        # session_id -> 当前 agent 主任务（可 stop/cancel）
        self._agent_tasks: dict[uuid.UUID, asyncio.Task] = {}
        # session_id -> 运行中流式快照（断线/跳页后仍可 sync 恢复）
        self._run_snapshots: dict[uuid.UUID, SessionRunSnapshot] = {}
        # 保护 WebSocket 并发发送：per-session，避免全局锁堵住其它会话
        self._send_locks: dict[uuid.UUID, asyncio.Lock] = {}
        # 连续发送失败计数：瞬断不立刻 disconnect
        self._send_fail_counts: dict[uuid.UUID, int] = {}
        # run generation：忽略旧 task 的 late stream 污染新一轮
        self._run_generations: dict[uuid.UUID, int] = {}

    def _send_lock_for(self, session_id: uuid.UUID) -> asyncio.Lock:
        lock = self._send_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._send_locks[session_id] = lock
        return lock

    def bump_run_generation(self, session_id: uuid.UUID) -> int:
        n = int(self._run_generations.get(session_id, 0)) + 1
        self._run_generations[session_id] = n
        return n

    def current_run_generation(self, session_id: uuid.UUID) -> int:
        return int(self._run_generations.get(session_id, 0))

    # ── run snapshot (navigate-away safe) ───────────────────────────

    def begin_run_snapshot(self, session_id: uuid.UUID) -> None:
        """新 user turn 开始：标记 running，清空上一轮残留。"""
        gen = self.bump_run_generation(session_id)
        self._run_snapshots[session_id] = SessionRunSnapshot(
            agent_running=True,
            state="thinking",
            detail="Starting…",
            updated_at=time.time(),
            generation=gen,
        )
        self._persist_snapshot_disk(session_id)

    def end_run_snapshot(self, session_id: uuid.UUID) -> None:
        """Agent 主任务结束：释放快照，避免假 running。"""
        self._run_snapshots.pop(session_id, None)
        try:
            from backend.agent.run_snapshot_store import delete_snapshot

            delete_snapshot(str(session_id))
        except Exception:
            pass

    def get_run_snapshot(self, session_id: uuid.UUID) -> SessionRunSnapshot | None:
        snap = self._run_snapshots.get(session_id)
        if snap is not None:
            return snap
        # 内存空：尝试从磁盘恢复（崩溃/worker 切换）
        try:
            from backend.agent.run_snapshot_store import load_snapshot

            raw = load_snapshot(str(session_id))
            if not raw or not raw.get("agent_running"):
                return None
            snap = SessionRunSnapshot(
                agent_running=bool(raw.get("agent_running")),
                state=str(raw.get("state") or "thinking"),
                detail=str(raw.get("detail") or raw.get("stream_status") or ""),
                partial_content=str(raw.get("partial_content") or "")[:_MAX_PARTIAL_CHARS],
                stream_message_id=raw.get("stream_message_id"),
                live_tools=list(raw.get("live_tools") or [])[:_MAX_LIVE_TOOLS],
                updated_at=float(raw.get("updated_at") or raw.get("persisted_at") or 0),
                generation=int(raw.get("generation") or raw.get("run_generation") or 0),
            )
            self._run_snapshots[session_id] = snap
            if snap.generation:
                self._run_generations[session_id] = max(
                    int(self._run_generations.get(session_id, 0) or 0),
                    snap.generation,
                )
            return snap
        except Exception:
            return None

    def _persist_snapshot_disk(self, session_id: uuid.UUID) -> None:
        snap = self._run_snapshots.get(session_id)
        if snap is None:
            return
        try:
            from backend.agent.run_snapshot_store import save_snapshot

            save_snapshot(
                str(session_id),
                {
                    **snap.to_sync_fields(),
                    "detail": snap.detail,
                    "generation": snap.generation,
                    "updated_at": snap.updated_at,
                    "agent_running": snap.agent_running,
                    "state": snap.state,
                    "partial_content": snap.partial_content,
                    "stream_message_id": snap.stream_message_id,
                    "live_tools": list(snap.live_tools),
                },
            )
        except Exception:
            pass

    def _touch_snapshot(self, session_id: uuid.UUID) -> SessionRunSnapshot:
        snap = self._run_snapshots.get(session_id)
        if snap is None:
            snap = SessionRunSnapshot(agent_running=self.has_running_agent(session_id))
            self._run_snapshots[session_id] = snap
        snap.updated_at = time.time()
        return snap

    def _maybe_flush_snapshot(self, session_id: uuid.UUID) -> None:
        """节流落盘：最多约 1.5s 一次，避免每帧 delta 打盘。"""
        snap = self._run_snapshots.get(session_id)
        if snap is None:
            return
        last = float(getattr(snap, "_last_disk_flush", 0) or 0)
        now = time.time()
        if now - last < 1.5 and snap.state not in ("idle", "error"):
            return
        try:
            snap._last_disk_flush = now  # type: ignore[attr-defined]
        except Exception:
            pass
        self._persist_snapshot_disk(session_id)

    def _ingest_run_event(self, session_id: uuid.UUID, message: dict[str, Any]) -> None:
        """从广播消息维护快照。无 WS 连接时也要更新（用户在别的页面）。

        带 run_generation 且与当前 generation 不符 → 丢弃（旧 task late event）。
        """
        msg_type = message.get("type")
        if msg_type not in ("stream_delta", "status", "tool_event", "error"):
            return

        msg_gen = message.get("run_generation")
        cur_gen = self.current_run_generation(session_id)
        if msg_gen is not None:
            try:
                if int(msg_gen) != int(cur_gen):
                    return
            except (TypeError, ValueError):
                return

        running = self.has_running_agent(session_id)
        snap = self._touch_snapshot(session_id)
        if getattr(snap, "generation", 0) == 0 and cur_gen:
            snap.generation = cur_gen
        if running:
            snap.agent_running = True

        if msg_type == "stream_delta":
            content = message.get("content") or ""
            mid = message.get("message_id")
            mid_s = str(mid) if mid else None
            done = bool(message.get("done"))
            # 新 message_id：若上一轮已有较长正文且本段像整段重推，则替换；否则重置缓冲
            if mid_s and snap.stream_message_id and mid_s != snap.stream_message_id:
                if done or (len(content) > 80 and len(content) >= len(snap.partial_content)):
                    snap.partial_content = content
                else:
                    snap.partial_content = content
                snap.stream_message_id = mid_s
            else:
                if mid_s:
                    snap.stream_message_id = mid_s
                if done and content and len(content) >= len(snap.partial_content):
                    snap.partial_content = content
                else:
                    snap.partial_content = (snap.partial_content + content)[:_MAX_PARTIAL_CHARS]
            if snap.state in ("idle", ""):
                snap.state = "thinking"
            self._maybe_flush_snapshot(session_id)
            return

        if msg_type == "status":
            state = str(message.get("state") or "")
            detail = message.get("detail")
            if state:
                snap.state = state
            if detail is not None:
                snap.detail = str(detail)
            if state == "idle":
                # 终态：若 agent 任务已结束则清快照；否则保留到 task done
                if not running:
                    self.end_run_snapshot(session_id)
                else:
                    snap.agent_running = True
                    self._maybe_flush_snapshot(session_id)
            elif state == "error":
                if not running:
                    self.end_run_snapshot(session_id)
                else:
                    self._maybe_flush_snapshot(session_id)
            else:
                self._maybe_flush_snapshot(session_id)
            return

        if msg_type == "error":
            detail = message.get("detail")
            if detail:
                snap.detail = str(detail)
            snap.state = "error"
            if not running:
                self.end_run_snapshot(session_id)
            else:
                self._maybe_flush_snapshot(session_id)
            return

        if msg_type == "tool_event":
            tid = str(message.get("tool_call_id") or "")
            if not tid:
                return
            name = str(message.get("name") or "tool")
            phase = message.get("phase") or "start"
            status = message.get("status") or ("running" if phase == "start" else "completed")
            args = message.get("arguments") if isinstance(message.get("arguments"), dict) else {}
            result = message.get("result")
            # upsert
            found = False
            for i, t in enumerate(snap.live_tools):
                if str(t.get("id") or t.get("tool_call_id") or "") == tid:
                    snap.live_tools[i] = {
                        "id": tid,
                        "name": name or t.get("name") or "tool",
                        "arguments": args if args else (t.get("arguments") or {}),
                        "status": status,
                        "result": result if result is not None else t.get("result"),
                    }
                    found = True
                    break
            if not found:
                snap.live_tools.append(
                    {
                        "id": tid,
                        "name": name,
                        "arguments": args or {},
                        "status": status,
                        "result": result,
                    }
                )
            if len(snap.live_tools) > _MAX_LIVE_TOOLS:
                snap.live_tools = snap.live_tools[-_MAX_LIVE_TOOLS:]
            if snap.state in ("idle", ""):
                snap.state = "tool_executing"
            self._maybe_flush_snapshot(session_id)
            return

    async def _safe_close(self, websocket: WebSocket, code: int = 1000, reason: str = "") -> None:
        """安全关闭 WebSocket，避免重复 close 导致 RuntimeError"""
        await safe_close_ws(websocket, code=code, reason=reason)

    def _track_task(self, session_id: uuid.UUID, task: asyncio.Task) -> None:
        """跟踪一个后台任务，任务完成时自动移除"""
        if session_id not in self._tasks:
            self._tasks[session_id] = set()
        self._tasks[session_id].add(task)
        # 任务完成时自动清理
        task.add_done_callback(
            lambda t, sid=session_id: self._tasks.get(sid, set()).discard(t)
        )

    def _cancel_session_tasks(
        self, session_id: uuid.UUID, *, cancel_agent: bool = False
    ) -> None:
        """取消 session 附属后台任务。

        默认 **不** 取消 agent 主任务：用户跳转 /skills 等页面会卸载聊天组件并断开 WS，
        若一并 cancel agent，会导致「对话中跳页后推理中断」。显式 stop / 新 user_input 叠跑
        时通过 cancel_agent=True 或 cancel_agent() 终止。
        """
        agent_task = self._agent_tasks.get(session_id)
        tasks = self._tasks.pop(session_id, set())
        for task in tasks:
            # agent 主任务也可能被误登记进 _tasks；断线时跳过
            if agent_task is not None and task is agent_task and not cancel_agent:
                if session_id not in self._tasks:
                    self._tasks[session_id] = set()
                self._tasks[session_id].add(task)
                continue
            if not task.done():
                task.cancel()
        if cancel_agent:
            agent = self._agent_tasks.pop(session_id, None)
            if agent is not None and not agent.done():
                agent.cancel()

    def track_agent_task(self, session_id: uuid.UUID, task: asyncio.Task) -> None:
        """登记当前 agent 主任务，便于显式 stop；断线不自动取消。"""
        old = self._agent_tasks.get(session_id)
        if old is not None and not old.done() and old is not task:
            old.cancel()
        self._agent_tasks[session_id] = task
        # 新主任务：确保快照为 running（begin 可能已在 user_input 调用）
        snap = self._run_snapshots.get(session_id)
        if snap is None:
            self.begin_run_snapshot(session_id)
        else:
            snap.agent_running = True
            snap.updated_at = time.time()
        # 不把 agent 放进 _tasks：disconnect 只清附属任务，保留推理

        def _cleanup(t: asyncio.Task, sid: uuid.UUID = session_id) -> None:
            cur = self._agent_tasks.get(sid)
            if cur is t:
                self._agent_tasks.pop(sid, None)
                # 任务结束后释放快照，防止 UI 永久 Resuming
                self.end_run_snapshot(sid)

        task.add_done_callback(_cleanup)

    def has_running_agent(self, session_id: uuid.UUID) -> bool:
        t = self._agent_tasks.get(session_id)
        return t is not None and not t.done()

    def active_session_ids(self) -> set[str]:
        """有活跃 WS 连接或运行中 agent 的 session id 集合（字符串形式）。

        供前端「空白会话清理」等逻辑兜底：活跃会话绝不许误删——
        流式运行中消息可能尚未落库，仅按 DB 内容判空白会误杀活跃会话。
        """
        ids = {str(sid) for sid in self._connections.keys()}
        for sid, t in self._agent_tasks.items():
            if t is not None and not t.done():
                ids.add(str(sid))
        return ids

    async def cancel_agent(self, session_id: uuid.UUID, *, wait: float = 2.0) -> None:
        """取消正在运行的 agent（配合 agent.stop() 使用）。

        wait: 尽量等旧 task 退出，避免立刻清 _should_stop 导致叠跑。
        """
        t = self._agent_tasks.get(session_id)
        if t is None or t.done():
            return
        t.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(t), timeout=max(0.05, float(wait)))
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        except Exception:
            pass

    async def wait_agent_idle(self, session_id: uuid.UUID, *, timeout: float = 2.0) -> bool:
        """轮询直到无运行中 agent 或超时。返回是否已 idle。"""
        deadline = time.monotonic() + max(0.05, float(timeout))
        while time.monotonic() < deadline:
            if not self.has_running_agent(session_id):
                return True
            await asyncio.sleep(0.05)
        return not self.has_running_agent(session_id)

    async def connect(
        self,
        session_id: uuid.UUID,
        websocket: WebSocket,
        user_id: uuid.UUID | None = None,
    ) -> None:
        """建立连接，如果该 session 已有连接则先关闭旧连接。

        重连时不取消 agent：跳页导致的短暂断线后，回到会话应能继续收流/同步历史。
        """
        # 仅清理附属任务，保留 agent 主任务
        self._cancel_session_tasks(session_id, cancel_agent=False)

        if session_id in self._connections:
            old_ws = self._connections[session_id]
            try:
                await old_ws.close(code=1001, reason="New connection established")
            except Exception:
                pass
            logger.info(f"Replaced old WebSocket connection for session {session_id}")

        self._connections[session_id] = websocket

        # 保留已有 _tasks；没有则初始化
        if session_id not in self._tasks:
            self._tasks[session_id] = set()

        # 关联用户
        if user_id:
            if user_id not in self._user_sessions:
                self._user_sessions[user_id] = set()
            self._user_sessions[user_id].add(session_id)

        logger.info(
            f"WebSocket connected: session={session_id}, user={user_id}, "
            f"total={len(self._connections)} agent_running={self.has_running_agent(session_id)}"
        )

    def disconnect(self, session_id: uuid.UUID, user_id: uuid.UUID | None = None) -> None:
        """断开连接。默认不取消 agent 主任务（支持跳页后后台继续推理）。"""
        self._cancel_session_tasks(session_id, cancel_agent=False)

        self._connections.pop(session_id, None)
        self._send_fail_counts.pop(session_id, None)
        # 保留 _send_locks / _run_generations：agent 可能仍在跑，回连还要用

        if user_id and user_id in self._user_sessions:
            self._user_sessions[user_id].discard(session_id)
            if not self._user_sessions[user_id]:
                del self._user_sessions[user_id]

        logger.info(
            f"WebSocket disconnected: session={session_id}, "
            f"total={len(self._connections)} agent_running={self.has_running_agent(session_id)}"
        )

    async def broadcast(
        self, session_id: uuid.UUID, message: dict[str, Any]
    ) -> None:
        """向指定 session 的 WebSocket 发送消息（连接不存在时静默忽略）。

        无论是否有连接，都先更新 run snapshot——用户跳页断线后 agent 仍在跑，
        回连 sync 才能恢复 partial 正文与 live tools。
        """
        # 打戳 run_generation（来自 agent task contextvar），供 ingest/前端过滤 late event
        if isinstance(message, dict) and message.get("type") in (
            "stream_delta",
            "status",
            "tool_event",
            "error",
        ):
            if message.get("run_generation") is None:
                ctx_gen = _run_gen_ctx.get()
                if ctx_gen is not None:
                    message = {**message, "run_generation": int(ctx_gen)}
            if not message.get("session_id"):
                message = {**message, "session_id": str(session_id)}

        try:
            self._ingest_run_event(session_id, message)
        except Exception as e:
            logger.debug("run snapshot ingest skipped: %s", e)

        ws = self._connections.get(session_id)
        if ws is None:
            return
        try:
            # 未 accept 的连接不能发送；FastAPI 会在 client 连接时自动 accept，
            # 但此处防御性检查，避免 'not connected' 错误。
            st = getattr(ws, "client_state", None)
            if st is not None and getattr(st, "value", None) != 1:
                # 0=CONNECTING, 1=CONNECTED; 非 CONNECTED 不发，也不立刻 disconnect
                return
            async with self._send_lock_for(session_id):
                await ws.send_json(message)
            self._send_fail_counts[session_id] = 0
        except Exception as e:
            n = int(self._send_fail_counts.get(session_id, 0)) + 1
            self._send_fail_counts[session_id] = n
            logger.debug(
                "Failed to send message to session %s (fail=%s): %s",
                session_id,
                n,
                e,
            )
            # 连续失败才 disconnect，避免浏览器休眠/半关闭一次就清映射
            if n >= 3:
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
            st = getattr(ws, "client_state", None)
            if st is not None and getattr(st, "value", None) != 1:
                return
            async with self._send_lock_for(session_id):
                await ws.send_text(text)
            self._send_fail_counts[session_id] = 0
        except Exception as e:
            n = int(self._send_fail_counts.get(session_id, 0)) + 1
            self._send_fail_counts[session_id] = n
            logger.error(
                "Failed to send text to session %s (fail=%s): %s", session_id, n, e
            )
            if n >= 3:
                self.disconnect(session_id)

    def is_connected(self, session_id) -> bool:
        """该 session 是否有处于 CONNECTED 状态的 WS 连接。

        confirm_manager 用它判断确认弹窗能否送达——broadcast 对未知 session 静默 return。
        """
        if session_id is None:
            return False
        sid = session_id
        if isinstance(session_id, str):
            try:
                sid = uuid.UUID(session_id)
            except (ValueError, AttributeError):
                return False
        ws = self._connections.get(sid)
        if ws is None:
            return False
        state = getattr(ws, "client_state", None)
        # Starlette WebSocketState.CONNECTED.value == 1
        return bool(state is not None and getattr(state, "value", state) == 1)

    def user_has_live_connection(self, user_id) -> bool:
        """该用户任意会话是否有 live WS（CEO 在别的 tab 打开了应用也算有人可问）。"""
        if user_id is None:
            return False
        uid = user_id
        if isinstance(user_id, str):
            try:
                uid = uuid.UUID(user_id)
            except (ValueError, AttributeError):
                return False
        for sid in list(self._user_sessions.get(uid, set()) or set()):
            if self.is_connected(sid):
                return True
        return False


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
            if not raw_auth or not raw_auth.strip():
                await websocket.close(code=4001, reason="Empty auth message")
                return
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
            await safe_close_ws(websocket, code=1008, reason="Authentication required")
            return

    effective_token = token_from_query or token_from_message or ""

    user_id: uuid.UUID | None = None

    if effective_token:
        payload = decode_access_token(effective_token)
        if not payload or "sub" not in payload:
            await _accept_once()
            try:
                await websocket.send_json(
                    {"type": "error", "detail": "Invalid or expired token"}
                )
            except Exception:
                pass
            await safe_close_ws(websocket, code=1008, reason="Invalid or expired token")
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
            await safe_close_ws(websocket, code=1008, reason="Invalid token")
            return

        # 单用户模式下：如果 token 里的 user_id 在数据库中不存在，
        # 可能是前端缓存了旧 token，回退到默认用户（admin@takton.dev）。
        if settings.single_user_mode:
            from backend.repositories.user_repo import AsyncUserRepository
            user_repo_check = AsyncUserRepository()
            existing = await user_repo_check.get_by_id(user_id)
            if not existing:
                default_user = await user_repo_check.get_by_email("admin@takton.dev")
                if default_user:
                    user_id = default_user.id
                    logger.info(
                        f"Single-user fallback: token sub not found, using default user {user_id}"
                    )
    elif settings.single_user_mode and _ws_client_is_loopback(websocket):
        # 单用户免认证直连：**仅本机** 无 token 时回落默认 admin。
        #
        # 这条闸门此前不存在，而 WS 是驱动 Agent Loop 的主通道 —— 绑 0.0.0.0 时
        # 等于把 command 工具的执行权无鉴权暴露出去。HTTP 侧的
        # dependencies.get_current_user 一直有这道门，两边口径必须一致。
        from backend.api.dependencies import resolve_default_admin_password
        from backend.repositories.user_repo import AsyncUserRepository

        user_repo_check = AsyncUserRepository()
        default_user = await user_repo_check.get_by_email("admin@takton.dev")
        if default_user:
            user_id = default_user.id
        else:
            from backend.core.security import get_password_hash

            user_id = uuid.uuid4()
            try:
                await user_repo_check.create(
                    {
                        "id": user_id,
                        "email": "admin@takton.dev",
                        "username": "admin",
                        "hashed_password": get_password_hash(
                            resolve_default_admin_password()
                        ),
                        "is_superuser": True,
                        "is_active": True,
                    }
                )
            except Exception:
                default_user = await user_repo_check.get_by_email("admin@takton.dev")
                if default_user:
                    user_id = default_user.id
        logger.info(f"Single-user WS: no token, using default user {user_id}")
    else:
        await _accept_once()
        try:
            await websocket.send_json(
                {"type": "error", "detail": "Authentication required"}
            )
        except Exception:
            pass
        await safe_close_ws(websocket, code=1008, reason="Authentication required")
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
                    await safe_close_ws(websocket, code=1008, reason="Session expired")
                    return

                # 会话用户隔离检查（单用户模式本机不卡）
                if (
                    not settings.single_user_mode
                    and session.user_id is not None
                    and session.user_id != user_id
                ):
                    await websocket.send_json(
                        {"type": "error", "detail": "Session access denied"}
                    )
                    await safe_close_ws(websocket, code=1008, reason="Session access denied")
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

    # 重连恢复：若该 session 后台 agent 仍在跑，立刻推 status
    if manager.has_running_agent(session_id):
        try:
            await manager.broadcast(
                session_id,
                {
                    "type": "status",
                    "state": "thinking",
                    "detail": "Resumed — agent still running",
                    "agent_running": True,
                },
            )
        except Exception:
            pass

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
            if not raw or not raw.strip():
                continue
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

            elif msg_type in ("user_input", "regenerate"):
                regenerate = msg_type == "regenerate" or bool(data.get("regenerate"))
                user_input = data.get("content", "").strip()
                if not user_input and regenerate:
                    # 从历史取最后一条用户消息
                    try:
                        hist = await message_repo.get_history_by_session(
                            session_id, limit=20
                        )
                        for m in reversed(list(hist or [])):
                            if getattr(m, "role", None) == "user" and (m.content or "").strip():
                                user_input = (m.content or "").strip()
                                break
                    except Exception as e:
                        logger.warning("regenerate load history failed: %s", e)
                if not user_input:
                    continue

                attachments = data.get("attachments", [])
                mode = data.get("mode", "default")
                sub_agent_ids = data.get("sub_agent_ids") or data.get("subAgentIds") or []
                if not isinstance(sub_agent_ids, list):
                    sub_agent_ids = []
                sub_agent_ids = [str(x) for x in sub_agent_ids if x]

                # 若上一轮仍在跑：先请求停止并尽量等退出，避免叠跑 / late delta
                if manager.has_running_agent(session_id):
                    agent.stop()
                    await manager.broadcast(
                        session_id,
                        {
                            "type": "status",
                            "state": "thinking",
                            "detail": "Stopping previous run to start new input...",
                        },
                    )
                    await manager.cancel_agent(session_id, wait=6.0)
                    idle = await manager.wait_agent_idle(session_id, timeout=6.0)
                    if not idle and manager.has_running_agent(session_id):
                        await manager.broadcast(
                            session_id,
                            {
                                "type": "error",
                                "detail": "上一轮仍在结束，请稍后再发（或点停止后等待）",
                            },
                        )
                        continue

                agent._should_stop = False
                agent._skip_user_persist = bool(regenerate)
                manager.begin_run_snapshot(session_id)
                run_gen = manager.current_run_generation(session_id)

                # 后台跑 Agent，保持收包循环可响应 stop/ping
                task = asyncio.create_task(
                    _run_agent_safe(
                        agent,
                        session_id,
                        user_input,
                        attachments,
                        mode,
                        sub_agent_ids,
                        run_generation=run_gen,
                    ),
                    name=f"agent:{session_id}:g{run_gen}",
                )
                manager.track_agent_task(session_id, task)

            elif msg_type == "stop":
                logger.info(f"Stop signal received for session {session_id}")
                agent.stop()
                await manager.cancel_agent(session_id, wait=6.0)
                manager.end_run_snapshot(session_id)
                manager.bump_run_generation(session_id)  # 使旧 task late event 失效
                await manager.broadcast(
                    session_id,
                    {"type": "status", "state": "idle", "detail": "Generation stopped by user"},
                )

            elif msg_type == "confirm_response":
                # 危险操作确认结果：唤醒等待的工具执行协程
                # scope: once | session | agent | deny
                from backend.services import confirm_manager

                confirm_id = str(data.get("confirm_id", ""))
                approved = bool(data.get("approved", False))
                scope = data.get("scope")
                if scope is None and approved:
                    scope = "once"
                confirm_manager.resolve_confirmation(
                    confirm_id,
                    approved,
                    scope=str(scope) if scope is not None else None,
                    user_id=str(user_id) if user_id else None,
                )

            elif msg_type == "sync":
                # 断线/跳页回连：漏消息 + agent_running + 流式快照（正文/tools）
                running = manager.has_running_agent(session_id)
                snap = manager.get_run_snapshot(session_id)
                # 快照与 task 对齐：task 在跑但快照丢失时建轻量占位（勿用 begin 抹掉假设数据）
                if running and snap is None:
                    manager._run_snapshots[session_id] = SessionRunSnapshot(
                        agent_running=True,
                        state="thinking",
                        detail="Resuming…",
                        updated_at=time.time(),
                    )
                    snap = manager.get_run_snapshot(session_id)
                if not running:
                    # task 已结束：清快照，避免假 Resuming
                    if snap is not None:
                        manager.end_run_snapshot(session_id)
                    snap = None
                msgs_out: list[dict] = []
                try:
                    last_id = data.get("last_message_id")
                    if last_id:
                        last_uuid = uuid.UUID(str(last_id))
                        messages = await message_repo.get_messages_after(
                            session_id, last_uuid
                        )
                        msgs_out = [
                            {
                                "id": str(m.id),
                                "role": m.role,
                                "content": m.content,
                                "created_at": m.created_at.isoformat()
                                if getattr(m, "created_at", None)
                                else None,
                            }
                            for m in messages
                        ]
                    snap_fields = (
                        snap.to_sync_fields()
                        if snap is not None
                        else {
                            "agent_running": running,
                            "state": "thinking" if running else "idle",
                            "stream_status": None,
                            "partial_content": "",
                            "stream_message_id": None,
                            "live_tools": [],
                            "snapshot_updated_at": None,
                        }
                    )
                    # 以 task 存活为准
                    snap_fields["agent_running"] = running
                    if not running:
                        snap_fields["state"] = "idle"
                        snap_fields["partial_content"] = ""
                        snap_fields["live_tools"] = []
                    elif not snap_fields.get("state") or snap_fields["state"] == "idle":
                        snap_fields["state"] = "thinking"

                    await manager.broadcast(
                        session_id,
                        {
                            "type": "sync_response",
                            "messages": msgs_out,
                            **snap_fields,
                        },
                    )
                    if running:
                        detail = (snap.detail if snap else None) or "Resumed — agent still running"
                        await manager.broadcast(
                            session_id,
                            {
                                "type": "status",
                                "state": (snap.state if snap and snap.state not in ("idle", "") else "thinking"),
                                "detail": detail,
                                "agent_running": True,
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
    agent: NexusAgentLoop,
    session_id: uuid.UUID,
    user_input: str,
    attachments: list = None,
    mode: str = "default",
    sub_agent_ids: list | None = None,
    run_generation: int = 0,
) -> None:
    """安全地运行 Agent Loop，捕获异常与取消。

    run_generation：若结束时 generation 已前进（新 user_input / stop），
    不再 end_snapshot / 推 idle，避免 late event 污染新一轮。
    """

    def _still_current() -> bool:
        if not run_generation:
            return True
        return manager.current_run_generation(session_id) == run_generation

    _gen_token = _run_gen_ctx.set(int(run_generation) if run_generation else None)
    try:
        # Phase 2.2：chat 路径显式 origin（不靠 mode 默认猜）
        agent._run_origin = "chat"
        await agent.run(
            session_id,
            user_input,
            attachments=attachments or [],
            mode=mode,
            sub_agent_ids=sub_agent_ids or [],
        )
        # 正常结束：若 epilogue 已推 idle，快照会在 ingest 时清理；双保险
        if _still_current() and not manager.has_running_agent(session_id):
            manager.end_run_snapshot(session_id)
    except asyncio.CancelledError:
        logger.info(f"Agent loop cancelled for session {session_id}")
        if _still_current():
            try:
                manager.end_run_snapshot(session_id)
                await manager.broadcast(
                    session_id,
                    {"type": "status", "state": "idle", "detail": "Generation stopped"},
                )
            except Exception:
                pass
        raise
    except Exception as e:
        logger.exception(f"Agent loop failed for session {session_id}: {e}")
        if not _still_current():
            return
        try:
            manager.end_run_snapshot(session_id)
        except Exception:
            pass
        await manager.broadcast(
            session_id,
            {
                "type": "error",
                "detail": f"Agent error: {str(e)}",
            },
        )
    finally:
        try:
            _run_gen_ctx.reset(_gen_token)
        except Exception:
            pass
        # 异常分支漏清时的兜底：仅当 generation 仍是本轮且无存活 agent
        try:
            if _still_current() and not manager.has_running_agent(session_id):
                if manager.get_run_snapshot(session_id) is not None:
                    manager.end_run_snapshot(session_id)
        except Exception:
            pass
