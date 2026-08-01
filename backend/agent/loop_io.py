"""Loop I/O mixin: WS push + transactional persistence (Phase 2.4 split)."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from backend.database import get_db_context
from backend.repositories.context_repo import AsyncCtxItemRepository
from backend.repositories.message_repo import AsyncMessageRepository
from backend.repositories.notification_repo import AsyncNotificationRepository
from backend.repositories.session_repo import AsyncSessionRepository
from backend.repositories.task_repo import AsyncTaskRepository
from backend.schemas.ws import MemoryUpdated, StatusUpdate, StreamDelta

logger = logging.getLogger(__name__)


class LoopIOMixin:

    # ─────────── WebSocket push helpers ───────────

    async def _emit_progress(self, kind: str, text: str) -> None:
        """推送通道/外部进度（不含工具细节）。失败静默，不影响主循环。"""
        sink = self.progress_sink
        if not sink or not text or not str(text).strip():
            return
        try:
            await sink(kind, str(text).strip())
        except Exception as e:
            logger.debug("progress_sink failed: %s", e)

    async def _push_status(
        self,
        session_id: uuid.UUID,
        state: str,
        detail: str,
        *,
        caps_count: int | None = None,
        tools_count: int | None = None,
    ) -> None:
        """推送状态：优先 EventSinkPort（字段与 WS 一致，含 caps/tools），回落 ws_manager。"""
        sink = getattr(self, "event_sink", None)
        if sink is not None:
            try:
                await sink.push_status(
                    session_id,
                    state,
                    detail or "",
                    caps_count=caps_count,
                    tools_count=tools_count,
                )
                if state == "error" and detail:
                    await self._emit_progress("error", detail)
                return
            except TypeError:
                # 旧 sink 无 kwargs：降级位置参数
                try:
                    await sink.push_status(session_id, state, detail or "")
                    if state == "error" and detail:
                        await self._emit_progress("error", detail)
                    # caps 仍走 WS，避免 H2-E 可观测字段丢失
                    if self.ws_manager and (caps_count is not None or tools_count is not None):
                        payload = StatusUpdate(
                            session_id=session_id,
                            state=state,
                            detail=detail,
                            caps_count=caps_count,
                            tools_count=tools_count,
                        ).model_dump(mode="json")
                        await self.ws_manager.broadcast(session_id, payload)
                    return
                except Exception as e:
                    logger.debug("event_sink.push_status legacy failed: %s", e)
            except Exception as e:
                logger.debug("event_sink.push_status failed: %s", e)
        if self.ws_manager:
            payload = StatusUpdate(
                session_id=session_id,
                state=state,
                detail=detail,
                caps_count=caps_count,
                tools_count=tools_count,
            ).model_dump(mode="json")
            await self.ws_manager.broadcast(session_id, payload)
        if state == "error" and detail:
            await self._emit_progress("error", detail)

    async def _push_stream(
        self,
        session_id: uuid.UUID,
        message_id: uuid.UUID,
        delta: str,
    ) -> None:
        """推送流式文本：优先 EventSinkPort，回落 ws_manager。"""
        sink = getattr(self, "event_sink", None)
        if sink is not None:
            try:
                # 兼容 message_id 关键字或位置
                try:
                    await sink.push_stream_delta(
                        session_id, delta, message_id=message_id
                    )
                except TypeError:
                    await sink.push_stream_delta(session_id, delta)
                return
            except Exception as e:
                logger.debug("event_sink.push_stream_delta failed: %s", e)
        if self.ws_manager:
            await self.ws_manager.broadcast(
                session_id,
                StreamDelta(
                    session_id=session_id,
                    message_id=message_id,
                    content=delta,
                ).model_dump(mode="json"),
            )

    async def _push_tool_event(
        self,
        session_id: uuid.UUID,
        *,
        phase: str,
        tool_call_id: str,
        name: str,
        arguments: dict[str, Any] | None = None,
        status: str = "running",
        result: str | None = None,
    ) -> None:
        """推送工具调用开始/结束事件，供前端实时渲染 tool 卡片"""
        if not self.ws_manager:
            return
        try:
            from backend.schemas.ws import ToolEvent

            # 结果截断，避免 WS 帧过大
            res = result
            if not isinstance(res, str) and res is not None:
                res = str(res)
            if isinstance(res, str) and len(res) > 8000:
                res = res[:8000] + "\n…[truncated]"

            # 只推送可 JSON 化的参数；剥离 _ws_manager 等私有注入
            safe_args = self._jsonable_tool_args(arguments)

            await self.ws_manager.broadcast(
                session_id,
                ToolEvent(
                    session_id=session_id,
                    phase=phase,  # type: ignore[arg-type]
                    tool_call_id=tool_call_id,
                    name=name,
                    arguments=safe_args,
                    status=status,  # type: ignore[arg-type]
                    result=res,
                ).model_dump(mode="json"),
            )
        except Exception as e:
            logger.warning(f"Failed to push tool_event: {e}")

    async def _maybe_push_screenshot(
        self,
        session_id: uuid.UUID,
        tool_name: str,
        tool_result: str,
    ) -> None:
        """从截图工具结果提取图像：支持 path 落盘 / data URL / base64，推送 WS。"""
        if not self.ws_manager:
            return
        try:
            import base64
            import json as _json
            import os
            import re
            from pathlib import Path

            b64: str | None = None
            image_url = ""
            mime = "image/png"
            raw = str(tool_result)

            # 1) data:image/...;base64,...（完整，非 omitted 截断）
            m = re.search(r"(data:image/([^;]+);base64,)([A-Za-z0-9+/=\s]{200,})", raw)
            if m and "...[omitted]" not in raw[m.start() : m.end() + 20]:
                mime = m.group(2) or "image/png"
                b64 = m.group(3).replace("\n", "").replace(" ", "")

            data_obj: dict | None = None
            if not b64:
                try:
                    data_obj = _json.loads(raw)
                except Exception:
                    # 工具结果可能是 "ok\n{json}"
                    mjson = re.search(r"(\{[\s\S]*\})\s*$", raw)
                    if mjson:
                        try:
                            data_obj = _json.loads(mjson.group(1))
                        except Exception:
                            data_obj = None

            path = None
            if isinstance(data_obj, dict):
                img = data_obj.get("image") or (data_obj.get("data") or {}).get("image")
                if isinstance(img, str) and len(img) > 200 and "omitted" not in img[:40]:
                    if img.startswith("data:image"):
                        mm = re.match(r"data:image/([^;]+);base64,(.+)", img, re.S)
                        if mm:
                            mime = mm.group(1)
                            b64 = mm.group(2).replace("\n", "")
                    else:
                        b64 = img
                path = (
                    data_obj.get("path")
                    or (data_obj.get("data") or {}).get("path")
                    or data_obj.get("filepath")
                )

            if not path:
                mp = re.search(
                    r"(?:path|filepath).{0,24}(/[\w./\\-]+\.(?:png|jpe?g|webp))",
                    raw,
                    re.I,
                )
                if mp:
                    path = mp.group(1)

            # 2) 落盘路径 → 读文件 + 生成可访问 URL
            if path and isinstance(path, str) and os.path.isfile(path):
                p = Path(path)
                if p.suffix.lower() in {".jpg", ".jpeg"}:
                    mime = "image/jpeg"
                elif p.suffix.lower() == ".webp":
                    mime = "image/webp"
                else:
                    mime = "image/png"
                # URL：经 /api/desktop/shots/{filename}
                image_url = f"/api/desktop/shots/{p.name}"
                if not b64:
                    try:
                        raw_bytes = p.read_bytes()
                        # WS 体积保护：>1.8MB 只走 URL
                        if len(raw_bytes) <= 1_800_000:
                            b64 = base64.b64encode(raw_bytes).decode("ascii")
                    except OSError:
                        pass

            if not b64 and not image_url:
                return

            from datetime import datetime, timezone

            from backend.schemas.ws import ScreenshotEvent

            payload_b64 = ""
            if b64:
                payload_b64 = (
                    b64
                    if b64.startswith("data:")
                    else f"data:{mime};base64,{b64}"
                )

            await self.ws_manager.broadcast(
                session_id,
                ScreenshotEvent(
                    session_id=session_id,
                    image_base64=payload_b64,
                    image_url=image_url or "",
                    tool_name=tool_name,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                ).model_dump(mode="json"),
            )
        except Exception as e:
            logger.debug(f"Screenshot push skipped: {e}")

    @staticmethod
    def _jsonable_tool_args(arguments: dict[str, Any] | None) -> dict[str, Any]:
        """过滤不可 JSON 序列化 / 内部注入字段，供 WS 与落库。"""
        if not isinstance(arguments, dict):
            return {}
        out: dict[str, Any] = {}
        skip_keys = {"ws_manager", "connection_manager"}
        for k, v in arguments.items():
            ks = str(k)
            if ks.startswith("_") or ks in skip_keys:
                continue
            if "ConnectionManager" in type(v).__name__:
                continue
            try:
                out[ks] = json.loads(json.dumps(v, default=str, ensure_ascii=False))
            except Exception:
                out[ks] = str(v)[:500]
        return out

    async def _push_task_update(
        self,
        session_id: uuid.UUID,
        task_id: Any,
        progress: int,
        status: str,
        message: str,
    ) -> None:
        """推送任务进度更新到前端"""
        if self.ws_manager:
            await self.ws_manager.broadcast(
                session_id,
                {
                    "type": "task_update",
                    "session_id": str(session_id),
                    "task_id": str(task_id),
                    "progress": progress,
                    "status": status,
                    "message": message,
                },
            )

    async def _push_memory_updated(
        self, session_id: uuid.UUID, diff: str
    ) -> None:
        """P0-6: 推送长期记忆更新通知"""
        if self.ws_manager:
            await self.ws_manager.broadcast(
                session_id,
                MemoryUpdated(
                    session_id=session_id,
                    type="memory_updated",
                    diff=diff,
                ).model_dump(mode="json"),
            )

    async def _push_goal_update(self, session_id: uuid.UUID) -> None:
        """推送 Goal / Todo 进度到前端面板"""
        if not self.ws_manager:
            return
        try:
            from backend.agent.goal_state import get_goal

            g = get_goal(session_id)
            payload = {
                "type": "goal_update",
                "session_id": str(session_id),
                "goal": g.to_dict() if g else None,
            }
            await self.ws_manager.broadcast(session_id, payload)
        except Exception as e:
            logger.warning(f"Failed to push goal_update: {e}")
    # ─────────── Transactional persistence helpers ───────────

    async def _persist_user_input(
        self,
        session_id: uuid.UUID,
        enriched_input: str,
        *,
        display_content: str | None = None,
    ) -> None:
        """原子化保存用户输入：TTL 清理 + Message + CtxItem。

        保存后广播 user_message_ack，供前端用服务端 id 替换乐观气泡。
        display_content：可选，ack 时一并带上原始展示文案，便于乐观合并。
        """
        if self.message_repo is None or self.ctx_item_repo is None:
            return
        saved = None
        async with get_db_context() as db:
            msg_repo = AsyncMessageRepository(db)
            ctx_repo = AsyncCtxItemRepository(db)
            await ctx_repo.prune_by_ttl(session_id=session_id, ttl="session")
            saved = await msg_repo.save_message(session_id, "user", enriched_input)
            await ctx_repo.create({
                "session_id": session_id,
                "scope": "session",
                "kind": "message",
                "key": f"user_{int(datetime.now(timezone.utc).timestamp() * 1000)}",
                "value": enriched_input,
                "tokens": max(8, round(len(enriched_input) / 3.4)),
                "pinned": False,
                "ttl": "session",
                "origin": f"agent:{self.agent_name}",
            })
        # 通知前端对齐乐观 id（无连接时静默）
        if saved is not None:
            try:
                from backend.api.websocket import manager as ws_manager

                mid = str(getattr(saved, "id", "") or "")
                created = getattr(saved, "created_at", None)
                if mid:
                    # 统一带 Z / offset，避免前端 Date.parse naive 当本地时区 → 与乐观 toISOString 差 8h
                    created_s = None
                    if created is not None:
                        try:
                            if getattr(created, "tzinfo", None) is None:
                                from datetime import timezone as _tz

                                created = created.replace(tzinfo=_tz.utc)
                            created_s = created.isoformat().replace("+00:00", "Z")
                        except Exception:
                            created_s = str(created)
                    payload = {
                        "type": "user_message_ack",
                        "id": mid,
                        "role": "user",
                        "content": enriched_input,
                        "session_id": str(session_id),
                        "created_at": created_s,
                    }
                    if display_content and display_content != enriched_input:
                        payload["display_content"] = display_content
                    await ws_manager.broadcast(session_id, payload)
            except Exception:
                pass

    async def _persist_tool_start(
        self, session_id: uuid.UUID, tool_name: str
    ) -> uuid.UUID | None:
        """原子化创建 Tool 任务并置为 running/50%。

        session 若已被删除（换库/前端删会话/map 过期），不再抛崩整轮 agent，
        仅跳过任务进度落库。
        """
        if self.task_repo is None:
            return None
        try:
            async with get_db_context() as db:
                # 先确认 session 仍在，避免 FK 炸穿整个 channel 回复
                sess_repo = AsyncSessionRepository(db)
                if await sess_repo.get_by_id(session_id) is None:
                    logger.warning(
                        "Skip tool task: session %s missing when starting %s",
                        session_id,
                        tool_name,
                    )
                    return None
                task_repo = AsyncTaskRepository(db)
                task = await task_repo.create_task(
                    session_id=session_id,
                    name=f"skill:{tool_name}",
                    description=f"Executing skill '{tool_name}'",
                )
                await task_repo.update_progress(task.id, progress=50, status="running")
                await task_repo.append_log(
                    task.id, {"level": "info", "message": f"Running {tool_name}"}
                )
                return task.id
        except Exception as e:
            logger.warning("Failed to persist tool start for %s: %s", tool_name, e)
            return None

    async def _persist_tool_completion(
        self,
        session_id: uuid.UUID,
        task_id: uuid.UUID | None,
        tool_name: str,
        tool_result: str,
        query: str = "",
    ) -> None:
        """原子化完成 Tool 任务：100% + 日志 + 可选 RAG CtxItem"""
        if self.task_repo is None:
            return
        async with get_db_context() as db:
            task_repo = AsyncTaskRepository(db)
            if task_id is not None:
                await task_repo.update_progress(
                    task_id, progress=100, status="completed"
                )
                await task_repo.append_log(
                    task_id, {"level": "info", "message": f"Result length: {len(tool_result)}"}
                )
            if tool_name == "search_knowledge_base" and self.ctx_item_repo is not None:
                ctx_repo = AsyncCtxItemRepository(db)
                await ctx_repo.create({
                    "session_id": session_id,
                    "scope": "knowledge",
                    "kind": "rag",
                    "key": f"rag_query_{int(datetime.now(timezone.utc).timestamp())}",
                    "value": f"Query: {query}\n\n{tool_result}",
                    "tokens": max(8, round(len(tool_result) / 3.4)),
                    "pinned": False,
                    "origin": "rag_skill",
                })

    async def _persist_tool_failure(
        self,
        task_id: uuid.UUID | None,
        tool_name: str,
        error: str,
    ) -> None:
        """原子化标记 Tool 任务失败"""
        if self.task_repo is None or task_id is None:
            return
        async with get_db_context() as db:
            task_repo = AsyncTaskRepository(db)
            await task_repo.update_progress(task_id, progress=0, status="failed")
            await task_repo.append_log(
                task_id, {"level": "error", "message": error}
            )

    async def _persist_final_response(
        self, session_id: uuid.UUID, final_content: str
    ) -> None:
        """原子化保存最终回复：Message + CtxItem + Session 状态 + 通知"""
        text = (final_content or "").strip()
        if not text:
            text = (
                "（本轮未生成可见正文：可能只调用了工具且后续未总结。"
                "请再发一条消息，或点「请继续」。若持续空白，可检查设备/RAG 相关工具是否报错。）"
            )
        async with get_db_context() as db:
            msg_repo = AsyncMessageRepository(db)
            ctx_repo = AsyncCtxItemRepository(db)
            session_repo = AsyncSessionRepository(db)

            token_estimate = max(8, round(len(text) / 3.4))
            await msg_repo.save_message(session_id, "assistant", text, token_count=token_estimate)
            if self.ctx_item_repo is not None:
                await ctx_repo.create({
                    "session_id": session_id,
                    "scope": "session",
                    "kind": "message",
                    "key": f"assistant_{int(datetime.now(timezone.utc).timestamp() * 1000)}",
                    "value": text,
                    "tokens": token_estimate,
                    "pinned": False,
                    "ttl": "session",
                    "origin": f"agent:{self.agent_name}",
                })
            await session_repo.update(
                session_id,
                {"status": "idle", "updated_at": datetime.now(timezone.utc)},
            )
            if self.notification_repo is not None and self.user_id is not None:
                await AsyncNotificationRepository(db).create({
                    "user_id": self.user_id,
                    "type": "message",
                    "title": "New assistant message",
                    "content": text[:200],
                    "data": {"session_id": str(session_id)},
                    "source_id": str(session_id),
                })

    def _build_user_input_with_attachments(
        self, user_input: str, attachments: list[dict[str, Any]]
    ) -> str:
        """将附件内容注入到用户输入中"""
        if not attachments:
            return user_input

        parts = [user_input]
        for i, att in enumerate(attachments, 1):
            filename = att.get("filename", f"附件{i}")
            text_content = att.get("text_content")
            file_type = att.get("type", "unknown")
            file_url = att.get("url", "")

            parts.append(f"\n\n[附件 {i}: {filename}]")
            if text_content:
                # 文本文件直接附内容
                content_preview = text_content[:8000]
                if len(text_content) > 8000:
                    content_preview += "\n...（内容已截断）"
                parts.append(content_preview)
            elif file_type in {"jpg", "jpeg", "png", "gif", "webp", "bmp", "svg"}:
                parts.append(f"[图片文件] {file_url}")
            else:
                parts.append(f"[文件类型: {file_type}] {file_url}")

        return "\n".join(parts)

