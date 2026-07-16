from pathlib import Path
import re

p = Path(r"E:/项目/taktonl-0.1.0/backend/services/channel_gateway.py")
text = p.read_text(encoding="utf-8")

helper = '''
class ChannelProgressPublisher:
    """社交通道进度推送（Hermes 风格）：

    - 立刻 ACK，消除「死机感」
    - 只推 status / thinking 自然语言
    - **绝不**推工具名、参数、工具结果
    - 节流：最少间隔 + 最大条数，避免刷屏触发平台限频
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
        min_interval_s: float = 2.8,
        max_msgs: int = 12,
        max_chars: int = 700,
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

    async def ack(self) -> None:
        await self._send("收到，正在处理…", force=True)

    async def __call__(self, kind: str, text: str) -> None:
        # 只允许这些 kind
        if kind not in ("status", "thinking", "error"):
            return
        raw = (text or "").strip()
        if not raw:
            return
        # 防御：若误传 tool 痕迹，丢弃
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
        # 状态条：前缀轻量标记
        if kind == "status":
            body = f"⏳ {raw}"
        elif kind == "error":
            body = f"⚠️ {raw}"
        else:
            # thinking：模型自然语言
            body = raw
            if len(body) > 40:
                body = "💭 " + body

        await self._send(body, force=(kind == "error"))

    async def _send(self, text: str, *, force: bool = False) -> None:
        t = text.strip()
        if not t:
            return
        if len(t) > self._max_chars:
            t = t[: self._max_chars - 1] + "…"
        # 去重
        if t == self._last_text:
            return
        now = self._time.monotonic()
        if not force:
            if self._count >= self._max_msgs:
                return
            if now - self._last_sent < self._min_interval:
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


'''

if "class ChannelProgressPublisher" not in text:
    text = text.replace(
        "logger = logging.getLogger(__name__)\n\n\nclass ChannelGateway:",
        "logger = logging.getLogger(__name__)\n\n" + helper + "\nclass ChannelGateway:",
        1,
    )
    print("added class")
else:
    print("class exists")

old = '''            agent = NexusAgentLoop(
                session_repo=session_repo,
                message_repo=message_repo,
                task_repo=task_repo,
                ctx_item_repo=ctx_item_repo,
                context_flow_repo=context_flow_repo,
                ws_manager=None,
                notification_repo=notification_repo,
                user_id=self._default_uid,
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
                await self._reply(platform, chat_id, reply_text, event_type, data, reply_to_id)
                logger.info("%s reply sent", platform)
'''

new = '''            # 社交通道：先 ACK + 思考/状态节流推送（不含工具调用/结果）
            progress = ChannelProgressPublisher(
                self, platform, chat_id, event_type, data, reply_to_id
            )
            await progress.ack()

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
'''

if "progress_sink=progress" not in text:
    if old in text:
        text = text.replace(old, new, 1)
        print("wired progress")
    else:
        # try CRLF
        old_cr = old.replace("\n", "\r\n")
        new_cr = new.replace("\n", "\r\n")
        if old_cr in text:
            text = text.replace(old_cr, new_cr, 1)
            print("wired progress crlf")
        else:
            print("OLD BLOCK NOT FOUND")
            # show vicinity
            idx = text.find("agent = NexusAgentLoop")
            print(repr(text[idx:idx+500]))
else:
    print("already wired")

p.write_text(text, encoding="utf-8")
import ast
ast.parse(text)
print("gateway syntax OK")
