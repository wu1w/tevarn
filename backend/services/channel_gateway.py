"""
消息通道 Gateway 管理器

在 Takton 后端启动时自动连接所有 enabled 的通道，
维持 WebSocket 长连接，收到的消息转发给 agent loop。

支持 /命令：/new /reset /compact /model /goal /status /help /stop
Session 管理：每个 chat_id 对应一个 session，首次连接自动创建，后续复用。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections import OrderedDict
from typing import Any, Optional

from backend.services.slash_commands import resolve_command, build_help_text, CommandDef

logger = logging.getLogger(__name__)


class ChannelProgressPublisher:
    """社交通道进度推送：

    - **只推**模型思考（thinking），可选 error
    - **不推**硬编码 ACK、「第 N 轮思考中」、工具名/参数/结果
    - 节流：最少间隔 + 最大条数，避免刷屏
    """

    def __init__(
        self,
        gateway: "ChannelGateway",
        platform: str,
        chat_id: str,
        event_type: str,
        data: dict,
        reply_to_id: str = "",
        *,
        min_interval_s: float = 1.5,
        max_msgs: int = 16,
        max_chars: int = 900,
    ):
        import time

        self._gw = gateway
        self._platform = platform
        self._chat_id = chat_id
        self._event_type = event_type
        self._data = data
        self._reply_to_id = reply_to_id
        self._min_interval = min_interval_s
        self._max_msgs = max_msgs
        self._max_chars = max_chars
        self._last_sent = 0.0
        self._count = 0
        self._last_text = ""
        self._time = time

    async def __call__(self, kind: str, text: str) -> None:
        # 只推思考；error 允许
        if kind not in ("thinking", "error"):
            return
        raw = (text or "").strip()
        if not raw:
            return
        # 过滤硬编码/状态腔
        if re.match(r"^(收到[，,]?正在处理|第\s*\d+\s*轮思考|思考中…?|Thinking \(round)", raw):
            return
        if raw in ("收到，正在处理…", "思考中…", "⏳ 思考中…"):
            return
        low = raw.lower()
        if any(
            x in low
            for x in (
                "tool_call",
                "function.arguments",
                "tool_calls",
                '"name": "bash"',
                "arguments:",
            )
        ):
            return
        if kind == "error":
            body = f"⚠️ {raw}"
            force = True
        else:
            body = raw
            force = True  # 每轮完整思考优先发出，不被状态挤掉
        await self._send(body, force=force)

    async def _send(self, text: str, *, force: bool = False) -> None:
        t = text.strip()
        if not t:
            return
        if len(t) > self._max_chars:
            t = t[: self._max_chars - 1] + "…"
        if t == self._last_text:
            return
        now = self._time.monotonic()
        if not force:
            if self._count >= self._max_msgs:
                return
            if now - self._last_sent < self._min_interval:
                return
        else:
            # force 也受总条数限制，但不受间隔限制
            if self._count >= self._max_msgs:
                return
            # 极短间隔防 API 连发拒收
            if now - self._last_sent < 0.6:
                return
        try:
            await self._gw._reply(
                self._platform,
                self._chat_id,
                t,
                self._event_type,
                self._data,
                self._reply_to_id,
            )
            self._last_sent = now
            self._count += 1
            self._last_text = t
        except Exception as e:
            logger.debug("channel progress send failed: %s", e)


class ChannelGateway:
    """管理所有消息通道的连接和消息路由。"""

    def __init__(self):
        self._adapters: dict[str, Any] = {}  # channel_id → adapter
        self._tasks: dict[str, asyncio.Task] = {}  # channel_id → maintain task
        self._running = False

        # Session 映射：chat_key → session_id
        # chat_key = f"{platform}:{chat_id}"  e.g. "qqbot:04B3735A..."
        self._session_map: dict[str, uuid.UUID] = {}

        # Goal 存储：chat_key → goal_text
        self._goals: dict[str, dict[str, Any]] = {}

        # 默认用户 ID（单机模式）
        self._default_uid = uuid.UUID("314016d7-a9d5-4719-8371-7ec9301fba0b")

        # Agent stop 标记：session_id → True
        self._stop_flags: dict[uuid.UUID, bool] = {}

        # 消息去重：platform:chat_id:msg_id → monotonic 时间
        # 缓解网络抖动/平台重推导致的重复处理
        self._seen_msgs: OrderedDict[str, float] = OrderedDict()
        self._seen_ttl_s = 300.0
        self._seen_max = 2000
        self._seen_lock = asyncio.Lock()

    # ─── 生命周期 ──────────────────────────────────────────

    async def start(self):
        """启动所有 enabled 的通道。"""
        if self._running:
            return
        self._running = True

        from backend.database import get_db_context
        from backend.models.channel import Channel

        async with get_db_context() as db:
            from sqlalchemy import select
            result = await db.execute(
                select(Channel).where(Channel.enabled == True)
            )
            channels = result.scalars().all()

        for ch in channels:
            task = asyncio.create_task(
                self._maintain_channel(ch), name=f"gateway_{ch.platform}_{ch.id}"
            )
            self._tasks[str(ch.id)] = task

        logger.info("Channel Gateway started, %d channels", len(self._tasks))

    async def stop(self):
        """停止所有通道。"""
        self._running = False
        for cid, adapter in self._adapters.items():
            try:
                await adapter.disconnect()
            except Exception as e:
                logger.error("Error disconnecting %s: %s", cid, e)
        for task in self._tasks.values():
            task.cancel()
        self._adapters.clear()
        self._tasks.clear()
        logger.info("Channel Gateway stopped")

    # ─── 通用通道维护 ──────────────────────────────────────

    async def _maintain_channel(self, ch: "Channel"):
        """维持通道连接（自动重连）。"""
        from backend.services.channel_adapters import ADAPTER_MAP

        adapter_cls = ADAPTER_MAP.get(ch.platform)
        if not adapter_cls:
            logger.error("Unknown platform: %s, skipping", ch.platform)
            return

        backoff = 2
        while self._running:
            try:
                adapter = self._create_adapter(adapter_cls, ch)
                await adapter.connect()
                self._adapters[str(ch.id)] = adapter
                backoff = 2
                logger.info("%s [%s] connected", ch.platform, ch.id.hex[:8])

                # 等待断连
                while self._running and adapter.connected:
                    await asyncio.sleep(5)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("%s error: %s", ch.platform, e, exc_info=True)

            if self._running:
                logger.info("%s reconnecting in %ds...", ch.platform, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def _create_adapter(self, adapter_cls, ch: "Channel"):
        """根据 Channel DB 记录创建适配器实例。"""
        extra = ch.extra or {}

        # 包装 on_message 回调，自动附加 platform
        async def _wrapped_on_message(event_type, data):
            # 注入 platform 信息到 data 里，方便后续识别
            if isinstance(data, dict):
                data["_platform"] = ch.platform
            await self._on_channel_message(event_type, data)

        if ch.platform == "qqbot":
            return adapter_cls(
                channel_id=str(ch.id),
                app_id=extra.get("app_id", ""),
                client_secret=ch.api_key or extra.get("client_secret", ""),
                on_message=_wrapped_on_message,
            )
        elif ch.platform == "telegram":
            return adapter_cls(
                channel_id=str(ch.id),
                token=ch.token or "",
                on_message=_wrapped_on_message,
            )
        elif ch.platform == "discord":
            return adapter_cls(
                channel_id=str(ch.id),
                token=ch.token or "",
                on_message=_wrapped_on_message,
            )
        elif ch.platform == "wecom":
            return adapter_cls(
                channel_id=str(ch.id),
                corp_id=ch.home_channel_id or extra.get("corp_id", ""),
                corp_secret=ch.api_key or "",
                agent_id=extra.get("agent_id", ""),
                callback_token=extra.get("token", ""),
                callback_aes_key=extra.get("encoding_aes_key", ""),
                on_message=_wrapped_on_message,
            )
        elif ch.platform == "slack":
            return adapter_cls(
                channel_id=str(ch.id),
                bot_token=ch.token or "",
                app_token=extra.get("app_token", ""),
                on_message=_wrapped_on_message,
            )
        elif ch.platform == "feishu":
            return adapter_cls(
                channel_id=str(ch.id),
                app_id=extra.get("app_id", ""),
                app_secret=ch.api_key or "",
                on_message=_wrapped_on_message,
            )
        elif ch.platform == "dingtalk":
            return adapter_cls(
                channel_id=str(ch.id),
                app_key=extra.get("app_key", ""),
                app_secret=ch.api_key or "",
                on_message=_wrapped_on_message,
            )
        elif ch.platform == "signal":
            return adapter_cls(
                channel_id=str(ch.id),
                signal_cli_url=extra.get("signal_cli_url", "http://localhost:8080"),
                phone_number=extra.get("phone_number", ""),
                on_message=_wrapped_on_message,
            )
        else:
            raise ValueError(f"Unknown platform: {ch.platform}")

    # ─── Session 管理 ──────────────────────────────────────

    async def _get_or_create_session(self, chat_key: str, event_type: str, data: dict) -> uuid.UUID:
        """获取或创建 session。首次连接自动创建，后续复用。

        若内存 map 中的 session 已从 DB 消失（换库/删除/多实例），自动重建，
        避免后续 INSERT tasks 触发 FOREIGN KEY 失败。
        """
        from backend.api.dependencies import get_session_repo

        session_repo = await get_session_repo()

        cached = self._session_map.get(chat_key)
        if cached is not None:
            existing = await session_repo.get_by_id(cached)
            if existing is not None:
                return cached
            logger.warning(
                "Stale session map entry %s for %s — recreating",
                str(cached)[:8],
                chat_key,
            )
            self._session_map.pop(chat_key, None)

        # 确保默认用户存在（硬编码 UUID 在换库后可能对不上）
        uid = await self._resolve_default_user_id()

        platform = chat_key.split(":", 1)[0] if ":" in chat_key else "unknown"
        session = await session_repo.create({
            "user_id": uid,
            "config": {
                "source": "channel",
                "platform": platform,
                "chat_key": chat_key,
                "event_type": event_type,
            },
        })
        self._session_map[chat_key] = session.id
        logger.info("Created new session %s for chat_key %s", session.id.hex[:8], chat_key)
        return session.id

    async def _create_new_session(self, chat_key: str) -> uuid.UUID:
        """强制创建新 session（/new 命令用）。"""
        platform = chat_key.split(":", 1)[0] if ":" in chat_key else "unknown"
        from backend.api.dependencies import get_session_repo
        session_repo = await get_session_repo()
        uid = await self._resolve_default_user_id()

        session = await session_repo.create({
            "user_id": uid,
            "config": {
                "source": "channel",
                "platform": platform,
                "chat_key": chat_key,
            },
        })
        self._session_map[chat_key] = session.id
        logger.info("New session %s for chat_key %s", session.id.hex[:8], chat_key)
        return session.id

    async def _resolve_default_user_id(self) -> uuid.UUID:
        """解析桌面/单用户模式下的真实用户 ID，避免硬编码与 DB 不一致。"""
        if getattr(self, "_resolved_uid", None):
            return self._resolved_uid  # type: ignore[return-value]

        try:
            from backend.api.dependencies import get_user_repo
            user_repo = await get_user_repo()
            # 优先默认邮箱
            user = None
            if hasattr(user_repo, "get_by_email"):
                user = await user_repo.get_by_email("admin@takton.dev")
            if user is None and hasattr(user_repo, "list_all"):
                try:
                    users = await user_repo.list_all()  # type: ignore[attr-defined]
                    user = users[0] if users else None
                except Exception:
                    user = None
            if user is not None and getattr(user, "id", None):
                self._resolved_uid = user.id if isinstance(user.id, uuid.UUID) else uuid.UUID(str(user.id))
                self._default_uid = self._resolved_uid
                return self._resolved_uid
        except Exception as e:
            logger.warning("Resolve default user failed, using hardcoded uid: %s", e)

        self._resolved_uid = self._default_uid
        return self._default_uid

    # ─── /命令处理 ─────────────────────────────────────────

    async def _handle_command(
        self, cmd: CommandDef, args: str, chat_key: str, event_type: str, data: dict
    ) -> str | None:
        """处理 /命令，返回回复文本。"""

        if cmd.name in ("new", "reset"):
            return await self._cmd_new(chat_key)

        elif cmd.name == "compact":
            return await self._cmd_compact(chat_key, args)

        elif cmd.name == "model":
            return await self._cmd_model(chat_key, args)

        elif cmd.name == "tools":
            return await self._cmd_tools(chat_key, args)

        elif cmd.name == "toolset":
            return await self._cmd_toolset(chat_key, args)

        elif cmd.name == "goal":
            return await self._cmd_goal(chat_key, args)

        elif cmd.name == "status":
            return await self._cmd_status(chat_key)

        elif cmd.name == "stop":
            return await self._cmd_stop(chat_key)

        elif cmd.name in ("help", "commands"):
            return build_help_text()

        return None

    async def _cmd_new(self, chat_key: str) -> str:
        """开启新会话。"""
        sid = await self._create_new_session(chat_key)
        # 清除 goal
        self._goals.pop(chat_key, None)
        return f"✅ 新会话已创建 (session: {sid.hex[:8]})"

    async def _cmd_compact(self, chat_key: str, args: str) -> str:
        """手动压缩上下文。"""
        if chat_key not in self._session_map:
            return "⚠️ 当前没有活跃会话，发送消息后会自动创建"

        sid = self._session_map[chat_key]
        try:
            from backend.agent.context_compress import compress_history_if_needed
            from backend.api.dependencies import get_message_repo
            from backend.core.config import settings

            message_repo = await get_message_repo()
            messages = await message_repo.list_by_session(sid, limit=500)

            if not messages:
                return "⚠️ 当前会话没有消息历史"

            # 强制压缩（threshold=0 触发）
            compressed, meta = await compress_history_if_needed(
                messages=messages,
                session_id=sid,
                threshold_percent=0.0,  # 强制触发
                context_window=int(getattr(settings, "context_window", 128000) or 128000),
            )

            if meta.get("compressed"):
                before = meta.get("tokens_before", 0)
                after = meta.get("tokens_after", 0)
                layers = meta.get("layers", [])
                layer_desc = ", ".join(f"L{l.get('level')}" for l in layers)
                ratio = (1 - after / before) * 100 if before else 0
                return f"✅ 上下文已压缩\n📊 {before}→{after} tokens (压缩率 {ratio:.1f}%)\n🔄 层级: {layer_desc}"
            else:
                return "ℹ️ 上下文未超过阈值，无需压缩"

        except Exception as e:
            logger.error("Compact failed: %s", e, exc_info=True)
            return f"❌ 压缩失败: {e}"

    async def _cmd_model(self, chat_key: str, args: str) -> str:
        """切换模型。"""
        if not args:
            # 显示当前模型
            from backend.core.config import settings
            current = getattr(settings, "llm_model", "unknown")
            provider = getattr(settings, "llm_provider", "unknown")
            return f"📋 当前模型: {provider}/{current}\n💡 用法: /model <model_name>"

        # 切换模型 — 存到 session config
        if chat_key in self._session_map:
            from backend.api.dependencies import get_session_repo
            session_repo = await get_session_repo()
            sid = self._session_map[chat_key]
            session = await session_repo.get_by_id(sid)
            if session:
                config = session.config or {}
                config["model_override"] = args
                await session_repo.update(sid, {"config": config})

        return f"✅ 模型已切换为: {args}"

    async def _cmd_tools(self, chat_key: str, args: str) -> str:
        """管理工具：查看/启用/禁用。"""
        from backend.api.dependencies import get_session_repo

        parts = args.split(maxsplit=1)
        action = parts[0].lower() if parts else "list"
        tool_name = parts[1].strip() if len(parts) > 1 else ""

        if action == "list":
            # 列出所有工具及状态
            try:
                from backend.tools.registry import ToolRegistry as UnifiedToolRegistry
                all_tools = UnifiedToolRegistry.get_all()
                if not all_tools:
                    return "⚠️ 工具注册表为空"

                # 获取 session 的工具过滤
                session_tools = await self._get_session_tools(chat_key)

                lines = ["🔧 工具列表", ""]
                for t in sorted(all_tools, key=lambda x: x.name):
                    enabled_icon = "✅" if t.enabled else "❌"
                    # 检查是否在 session 过滤中
                    if session_tools is not None:
                        in_session = "🟢" if t.name in session_tools else "⚪"
                    else:
                        in_session = "🟢"  # 全部启用
                    source = t.source.value if hasattr(t.source, 'value') else str(t.source)
                    lines.append(f"  {enabled_icon} {in_session} {t.name} ({source})")

                if session_tools is not None:
                    lines.append("")
                    lines.append("🟢=当前会话启用 ⚪=当前会话禁用 ✅=全局启用 ❌=全局禁用")
                else:
                    lines.append("")
                    lines.append("🟢=启用 ✅=全局启用")

                return "\n".join(lines)
            except Exception as e:
                logger.error("Tools list failed: %s", e)
                return f"❌ 获取工具列表失败: {e}"

        elif action == "enable":
            if not tool_name:
                return "⚠️ 用法: /tools enable <工具名>"
            return await self._toggle_session_tool(chat_key, tool_name, enable=True)

        elif action == "disable":
            if not tool_name:
                return "⚠️ 用法: /tools disable <工具名>"
            return await self._toggle_session_tool(chat_key, tool_name, enable=False)

        else:
            return "⚠️ 用法: /tools [list | enable <名> | disable <名>]"

    async def _cmd_toolset(self, chat_key: str, args: str) -> str:
        """切换工具集预设。"""
        from backend.services.slash_commands import TOOLSET_PRESETS, build_toolset_list_text

        if not args or args == "list":
            return build_toolset_list_text()

        preset_name = args.strip().lower()
        preset = TOOLSET_PRESETS.get(preset_name)
        if not preset:
            return f"⚠️ 未知工具集: {preset_name}\n{build_toolset_list_text()}"

        # 应用到 session config
        if chat_key in self._session_map:
            from backend.api.dependencies import get_session_repo
            session_repo = await get_session_repo()
            sid = self._session_map[chat_key]
            session = await session_repo.get_by_id(sid)
            if session:
                config = session.config or {}
                config["tools"] = preset["tools"] if preset["tools"] is not None else ["*"]
                await session_repo.update(sid, {"config": config})

        tools = preset["tools"]
        if tools is None:
            tool_str = "全部启用"
        elif len(tools) == 0:
            tool_str = "无工具"
        else:
            tool_str = ", ".join(tools)

        return f"✅ 工具集已切换为: {preset_name} ({preset['description']})\n🔧 工具: {tool_str}"

    async def _get_session_tools(self, chat_key: str) -> set[str] | None:
        """获取 session 的工具过滤列表。None=全部启用。"""
        if chat_key not in self._session_map:
            return None
        try:
            from backend.api.dependencies import get_session_repo
            session_repo = await get_session_repo()
            sid = self._session_map[chat_key]
            session = await session_repo.get_by_id(sid)
            if session and session.config:
                raw_tools = session.config.get("tools")
                if raw_tools is None or raw_tools == [] or raw_tools == ["*"]:
                    return None  # 全部
                return set(raw_tools)
        except Exception as e:
            logger.warning("_get_session_tools failed for %s: %s", chat_key, e)
        return None

    async def _toggle_session_tool(self, chat_key: str, tool_name: str, enable: bool) -> str:
        """启用/禁用 session 级别的单个工具。"""
        if chat_key not in self._session_map:
            return "⚠️ 当前没有活跃会话"

        from backend.api.dependencies import get_session_repo
        session_repo = await get_session_repo()
        sid = self._session_map[chat_key]
        session = await session_repo.get_by_id(sid)
        if not session:
            return "⚠️ 会话不存在"

        config = session.config or {}
        raw_tools = config.get("tools", ["*"])

        # 如果是通配符，先展开为全部工具名
        if raw_tools == ["*"] or raw_tools is None or raw_tools == []:
            try:
                from backend.tools.registry import ToolRegistry as UnifiedToolRegistry
                all_tools = UnifiedToolRegistry.get_all()
                tool_list = [t.name for t in all_tools if t.enabled]
            except Exception:
                tool_list = []
        else:
            tool_list = list(raw_tools)

        if enable:
            if tool_name not in tool_list:
                tool_list.append(tool_name)
            action_str = "启用"
        else:
            if tool_name in tool_list:
                tool_list.remove(tool_name)
            action_str = "禁用"

        config["tools"] = tool_list
        await session_repo.update(sid, {"config": config})
        return f"✅ 已{action_str}工具: {tool_name}"

    async def _cmd_goal(self, chat_key: str, args: str) -> str:
        """管理持续目标。"""
        if not args or args == "show":
            goal = self._goals.get(chat_key)
            if not goal:
                return "📋 当前没有设置目标\n💡 用法: /goal <目标描述>"
            status = "暂停中" if goal.get("paused") else "进行中"
            return f"🎯 目标 [{status}]: {goal['text']}"

        if args == "pause":
            if chat_key in self._goals:
                self._goals[chat_key]["paused"] = True
                return "⏸️ 目标已暂停"
            return "⚠️ 没有活跃目标"

        if args == "resume":
            if chat_key in self._goals:
                self._goals[chat_key]["paused"] = False
                return "▶️ 目标已恢复"
            return "⚠️ 没有活跃目标"

        if args == "clear":
            self._goals.pop(chat_key, None)
            return "🗑️ 目标已清除"

        # 设置新目标
        self._goals[chat_key] = {"text": args, "paused": False}
        return f"🎯 目标已设置: {args}"

    async def _cmd_status(self, chat_key: str) -> str:
        """查看会话状态。"""
        from backend.core.config import settings

        lines = ["📊 Takton 状态", ""]

        # 模型信息
        model = getattr(settings, "llm_model", "unknown")
        provider = getattr(settings, "llm_provider", "unknown")
        ctx_win = int(getattr(settings, "context_window", 128000) or 128000)
        lines.append(f"🤖 模型: {provider}/{model}")
        lines.append(f"📏 上下文窗口: {ctx_win}")

        # Session 信息
        if chat_key in self._session_map:
            sid = self._session_map[chat_key]
            lines.append(f"💬 会话: {sid.hex[:8]}")

            # 消息数
            try:
                from backend.api.dependencies import get_message_repo
                message_repo = await get_message_repo()
                messages = await message_repo.list_by_session(sid, limit=1000)
                lines.append(f"📝 消息数: {len(messages)}")
            except Exception as e:
                logger.warning("status: list messages failed: %s", e)
        else:
            lines.append("💬 会话: 未创建")

        # Goal
        goal = self._goals.get(chat_key)
        if goal:
            status = "⏸️ 暂停" if goal.get("paused") else "▶️ 进行中"
            lines.append(f"🎯 目标: {status} — {goal['text'][:50]}")

        # 通道
        lines.append(f"📡 通道: {len(self._adapters)} 个已连接")

        return "\n".join(lines)

    async def _cmd_stop(self, chat_key: str) -> str:
        """停止当前运行的 agent。"""
        if chat_key in self._session_map:
            sid = self._session_map[chat_key]
            self._stop_flags[sid] = True
            return "🛑 已发送停止信号"
        return "⚠️ 当前没有活跃会话"

    # ─── 消息处理 ──────────────────────────────────────────

    async def _is_duplicate_message(
        self, platform: str, chat_id: str, msg_id: str
    ) -> bool:
        """基于 platform+chat+message_id 的短时去重。无 msg_id 时不拦截。"""
        if not msg_id:
            return False
        key = f"{platform}:{chat_id}:{msg_id}"
        now = time.monotonic()
        async with self._seen_lock:
            # 过期清理
            while self._seen_msgs:
                oldest_key, oldest_ts = next(iter(self._seen_msgs.items()))
                if now - oldest_ts <= self._seen_ttl_s:
                    break
                self._seen_msgs.popitem(last=False)
            if key in self._seen_msgs:
                return True
            self._seen_msgs[key] = now
            while len(self._seen_msgs) > self._seen_max:
                self._seen_msgs.popitem(last=False)
        return False

    async def _on_channel_message(self, event_type: str, data: dict):
        """通用消息回调 — 支持 QQ/Telegram/Discord/Slack/飞书/钉钉/Signal。"""
        # 根据 adapter 类型提取通用字段
        platform = self._detect_platform_from_data(data)

        content = ""
        chat_id = ""
        user_id = ""
        reply_to_id = ""

        if platform == "qqbot":
            content = data.get("content", "")
            author = data.get("author", {})
            user_id = author.get("user_openid", "")
            if event_type == "C2C_MESSAGE_CREATE":
                chat_id = user_id
            elif event_type == "GROUP_AT_MESSAGE_CREATE":
                chat_id = data.get("group_openid", "")
            else:
                chat_id = data.get("channel_id", "")
            reply_to_id = data.get("id", "")
        elif platform == "telegram":
            content = data.get("text", "")
            chat_id = str(data.get("chat", {}).get("id", ""))
            user_id = str(data.get("from", {}).get("id", ""))
            reply_to_id = str(data.get("message_id", ""))
        elif platform == "discord":
            content = data.get("content", "")
            chat_id = data.get("channel_id", "")
            user_id = data.get("author", {}).get("id", "")
            reply_to_id = data.get("id", "")
        elif platform == "slack":
            content = data.get("text", "")
            chat_id = data.get("channel", "")
            user_id = data.get("user", "")
            reply_to_id = data.get("ts", "")
        elif platform == "wecom":
            content = data.get("content", "")
            chat_id = data.get("from_user", "")
            user_id = chat_id
            reply_to_id = ""
        elif platform == "feishu":
            content = data.get("content", "")
            chat_id = data.get("chat_id", "")
            user_id = data.get("sender", {}).get("sender_id", {}).get("user_id", "")
        elif platform == "dingtalk":
            content = data.get("text", {}).get("content", "") if isinstance(data.get("text"), dict) else data.get("content", "")
            chat_id = data.get("conversationId", "")
            user_id = data.get("senderStaffId", data.get("senderId", ""))
        elif platform == "signal":
            content = data.get("content", "")
            chat_id = data.get("source_number", data.get("source", ""))
            user_id = chat_id
        else:
            # 通用 fallback
            content = data.get("content", data.get("text", ""))
            chat_id = data.get("chat_id", data.get("channel_id", ""))
            user_id = data.get("user_id", data.get("from", ""))

        logger.info(
            "%s message: type=%s, chat=%s, user=%s, content=%.60s",
            platform, event_type, chat_id[:16], user_id[:16], content[:60],
        )

        if not content.strip():
            return

        # 平台重推 / 网络抖动去重
        if await self._is_duplicate_message(platform, str(chat_id), str(reply_to_id or "")):
            logger.info(
                "%s duplicate message dropped: chat=%s msg_id=%s",
                platform, str(chat_id)[:16], str(reply_to_id)[:32],
            )
            return

        # 去掉 @bot 标记
        user_msg = content
        if event_type in ("AT_MESSAGE_CREATE", "GROUP_AT_MESSAGE_CREATE") and platform == "qqbot":
            user_msg = re.sub(r'<@!\d+>', '', content).strip()

        if not user_msg:
            return

        # 生成 chat_key
        chat_key = f"{platform}:{chat_id}"

        # ── 解析 /命令 ──
        cmd, args = resolve_command(user_msg)
        if cmd:
            reply = await self._handle_command(cmd, args, chat_key, event_type, data)
            if reply:
                await self._reply(platform, chat_id, reply, event_type, data, reply_to_id)
            return

        # ── @device 远程（优先于完整 agent）──
        if "@" in user_msg:
            try:
                from backend.services.remote.dispatch import try_handle_at_device

                card = await try_handle_at_device(self._default_uid, user_msg)
                if card is not None:
                    await self._reply(platform, chat_id, card, event_type, data, reply_to_id)
                    return
            except Exception as e:
                logger.warning("channel @device failed: %s", e)

        # ── 普通消息 → agent loop ──
        try:
            from backend.api.dependencies import (
                get_session_repo, get_message_repo, get_task_repo,
                get_ctx_item_repo, get_context_flow_repo, get_notification_repo,
            )
            from backend.agent import NexusAgentLoop
            from backend.core.config import settings as app_settings

            sid = await self._get_or_create_session(chat_key, event_type, data)

            if self._stop_flags.pop(sid, False):
                await self._reply(platform, chat_id, "🛑 已停止", event_type, data, reply_to_id)
                return

            session_repo = await get_session_repo()
            message_repo = await get_message_repo()
            task_repo = await get_task_repo()
            ctx_item_repo = await get_ctx_item_repo()
            context_flow_repo = await get_context_flow_repo()
            notification_repo = await get_notification_repo()

            session = await session_repo.get_by_id(sid)
            model_override = None
            if session and session.config:
                model_override = session.config.get("model_override")

            goal = self._goals.get(chat_key)
            final_input = user_msg
            if goal and not goal.get("paused"):
                final_input = f"[🎯 当前目标: {goal['text']}]\n\n{user_msg}"

            # 社交通道：先 ACK + 思考/状态节流推送（不含工具调用/结果）
            progress = ChannelProgressPublisher(
                self, platform, chat_id, event_type, data, reply_to_id
            )
            agent = NexusAgentLoop(
                session_repo=session_repo,
                message_repo=message_repo,
                task_repo=task_repo,
                ctx_item_repo=ctx_item_repo,
                context_flow_repo=context_flow_repo,
                ws_manager=None,
                notification_repo=notification_repo,
                user_id=self._default_uid,
                progress_sink=progress,
            )
            agent.max_iterations = int(getattr(app_settings, "agent_max_iterations", 25) or 25)

            original_model = None
            if model_override:
                original_model = getattr(app_settings, "llm_model", None)
                app_settings.llm_model = model_override

            try:
                result = await agent.run(sid, final_input, mode="default")
            finally:
                if original_model is not None:
                    app_settings.llm_model = original_model

            if result:
                reply_text = (result or "")[:4000]
                # 终态答案与中间思考去重（若完全相同则跳过）
                if reply_text.strip() and reply_text.strip() != progress._last_text:
                    await self._reply(platform, chat_id, reply_text, event_type, data, reply_to_id)
                logger.info("%s reply sent (progress_msgs=%s)", platform, progress._count)

        except Exception as e:
            logger.error("%s message processing failed: %s", platform, e, exc_info=True)
            # 用户侧不要甩 SQLAlchemy 长栈 / FK 原文
            msg = str(e)
            if "FOREIGN KEY" in msg or "IntegrityError" in msg:
                friendly = "会话状态已失效，请再发一条消息（或发送 /new 开新会话）后重试。"
            elif len(msg) > 180:
                friendly = msg[:180] + "…"
            else:
                friendly = msg
            await self._reply(platform, chat_id, f"❌ 处理失败: {friendly}", event_type, data, reply_to_id)

    def _detect_platform_from_data(self, data: dict) -> str:
        """从消息数据推断平台类型（优先用注入的 _platform 字段）"""
        # 优先使用注入的 _platform
        platform = data.get("_platform")
        if platform:
            # 清理注入字段，不传给下游
            data.pop("_platform", None)
            return platform

        # fallback: 根据数据特征推断
        if "user_openid" in str(data.get("author", {})):
            return "qqbot"
        if "update_id" in data:
            return "telegram"
        if "guild_id" in data or "discriminator" in data.get("author", {}):
            return "discord"
        if "team" in data or "blocks" in data or "ts" in data:
            return "slack"
        if "chat_id" in data and "header" in data:
            return "feishu"
        if "conversationId" in data or "senderCorpId" in data:
            return "dingtalk"
        if "source_number" in data:
            return "signal"
        return "unknown"

    async def _reply(self, platform: str, chat_id: str, text: str, event_type: str, data: dict, reply_to_id: str = ""):
        """通用回复方法"""
        from backend.services.channel_adapters import ADAPTER_MAP

        for cid, adapter in self._adapters.items():
            if not adapter.connected:
                continue

            # 匹配对应平台的 adapter
            if hasattr(adapter, "platform") and adapter.platform != platform:
                continue

            try:
                if platform == "qqbot":
                    if event_type == "C2C_MESSAGE_CREATE":
                        openid = data.get("author", {}).get("user_openid", "")
                        if openid:
                            await adapter.send_c2c_message(openid, text, msg_id=reply_to_id)
                    elif event_type == "GROUP_AT_MESSAGE_CREATE":
                        group_openid = data.get("group_openid", "")
                        if group_openid:
                            await adapter.send_group_message(group_openid, text, msg_id=reply_to_id)
                    else:
                        await adapter.send_message(chat_id, text)
                elif platform == "telegram":
                    await adapter.send_text(chat_id, text, reply_to_message_id=reply_to_id)
                elif platform == "discord":
                    await adapter.send_text(chat_id, text)
                elif platform == "slack":
                    await adapter.send_text(chat_id, text)
                elif platform == "wecom":
                    await adapter.send_text(chat_id, text)
                elif platform == "feishu":
                    await adapter.send_text(chat_id, text, receive_id_type="chat_id")
                elif platform == "dingtalk":
                    await adapter.send_text(chat_id, text)
                elif platform == "signal":
                    await adapter.send_text(chat_id, text)
                else:
                    await adapter.send_text(chat_id, text)
            except Exception as e:
                logger.error("%s reply failed: %s", platform, e)
            break


# ─── 全局单例 ──────────────────────────────────────────────

_gateway: ChannelGateway | None = None


async def get_channel_gateway() -> ChannelGateway:
    global _gateway
    if _gateway is None:
        _gateway = ChannelGateway()
    return _gateway


async def start_channel_gateway():
    gw = await get_channel_gateway()
    await gw.start()


async def stop_channel_gateway():
    global _gateway
    if _gateway:
        await _gateway.stop()
        _gateway = None
