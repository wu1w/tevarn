from pathlib import Path
import re
import ast

p = Path(r"E:/项目/taktonl-0.1.0/backend/agent/loop.py")
text = p.read_text(encoding="utf-8")

init_block = r'''    def __init__(
        self,
        session_repo: SessionRepository,
        message_repo: MessageRepository,
        task_repo: TaskRepository | None = None,
        ctx_item_repo: CtxItemRepository | None = None,
        context_flow_repo: ContextFlowRepository | None = None,
        ws_manager=None,
        agent_name: str = "Takton",
        user_id: uuid.UUID | None = None,
        notification_repo: NotificationRepository | None = None,
        progress_sink=None,
    ):
        self.session_repo = session_repo
        self.message_repo = message_repo
        self.task_repo = task_repo
        self.ctx_item_repo = ctx_item_repo
        self.context_flow_repo = context_flow_repo
        self.ws_manager = ws_manager
        self.agent_name = agent_name
        self.user_id = user_id
        self.notification_repo = notification_repo
        # 可选：async (kind: str, text: str) -> None；仅人类可读进度/思考，不含工具细节
        self.progress_sink = progress_sink
        self.context_manager = ContextManager(ctx_item_repo=ctx_item_repo)
        # 长链/编码任务默认允许更多工具轮次；可用 TAKTON_AGENT_MAX_ITERATIONS 覆盖
        self.max_iterations = int(getattr(settings, "agent_max_iterations", 25) or 25)
        # 停止信号
        self._should_stop = False
        self._llm_fail_streak = 0
        # RAG 服务（懒加载）
        self._rag_service = None

    def stop(self)'''

text, n = re.subn(
    r"    def __init__\(\r?\n(?:.*\r?\n)*?    def stop\(self\)",
    lambda m: init_block,
    text,
    count=1,
)
print("init replacements", n)

push_block = r'''    async def _emit_progress(self, kind: str, text: str) -> None:
        """推送通道/外部进度（不含工具细节）。失败静默，不影响主循环。"""
        sink = self.progress_sink
        if not sink or not text or not str(text).strip():
            return
        try:
            await sink(kind, str(text).strip())
        except Exception as e:
            logger.debug("progress_sink failed: %s", e)

    async def _push_status(
        self, session_id: uuid.UUID, state: str, detail: str
    ) -> None:
        """推送状态更新到前端；同步 mirror 到 progress_sink（通道思考流）"""
        if self.ws_manager:
            await self.ws_manager.broadcast(
                session_id,
                StatusUpdate(
                    session_id=session_id,
                    state=state,
                    detail=detail,
                ).model_dump(mode="json"),
            )
        if state in ("thinking", "error", "running", "idle"):
            detail_out = detail or ""
            if detail_out.startswith("Thinking (round"):
                import re as _re

                m = _re.search(r"round\s+(\d+)", detail_out)
                detail_out = f"第 {m.group(1)} 轮思考中…" if m else "思考中…"
            await self._emit_progress(
                "error" if state == "error" else "status", detail_out
            )

    async def _push_stream(
        self,
        session_id: uuid.UUID,
        message_id: uuid.UUID,
        delta: str,
    ) -> None:
        """推送流式文本到前端"""
        if self.ws_manager:
            await self.ws_manager.broadcast(
                session_id,
                StreamDelta(
                    session_id=session_id,
                    message_id=message_id,
                    content=delta,
                ).model_dump(mode="json"),
            )

    async def _push_tool_event('''

# Prefer replacing from _emit_progress if present, else from _push_status
pat1 = r"    async def _emit_progress\(self, kind: str, text: str\) -> None:\r?\n(?:.*\r?\n)*?    async def _push_tool_event\("
pat2 = r"    async def _push_status\(\r?\n(?:.*\r?\n)*?    async def _push_tool_event\("
if re.search(pat1, text):
    text, n2 = re.subn(pat1, lambda m: push_block, text, count=1)
    print("push via emit", n2)
else:
    text, n2 = re.subn(pat2, lambda m: push_block, text, count=1)
    print("push via status", n2)

needle = "            # 判断是否有 tool calls\n            if tool_calls:\n"
needle_cr = "            # 判断是否有 tool calls\r\n            if tool_calls:\r\n"
insert = (
    "            # 通道进度：本轮模型自然语言思考（不含 tool 调用细节）\n"
    "            if accumulated_content and str(accumulated_content).strip():\n"
    "                await self._emit_progress(\"thinking\", str(accumulated_content).strip()[:1200])\n"
    "\n"
    "            # 判断是否有 tool calls\n"
    "            if tool_calls:\n"
)
if "通道进度：本轮模型自然语言思考" not in text:
    if needle in text:
        text = text.replace(needle, insert, 1)
        print("inserted thinking emit lf")
    elif needle_cr in text:
        text = text.replace(needle_cr, insert.replace("\n", "\r\n"), 1)
        print("inserted thinking emit crlf")
    else:
        print("needle not found")
else:
    print("thinking already present")

p.write_text(text, encoding="utf-8")
ast.parse(text)
print("syntax OK", len(text))
