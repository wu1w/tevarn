from pathlib import Path
import re
import ast

# ── 1) LLMChunk: reasoning_delta ──
schemas = Path(r"E:/项目/taktonl-0.1.0/backend/services/llm/schemas.py")
st = schemas.read_text(encoding="utf-8")
if "reasoning_delta" not in st:
    st = st.replace(
        '    delta: str = ""  # 文本增量\n    tool_call: ToolCall | None = None  # 工具调用增量',
        '    delta: str = ""  # 文本增量（可见回复）\n'
        '    reasoning_delta: str = ""  # 思考/reasoning 增量（不进最终用户答复也可用于通道进度）\n'
        '    tool_call: ToolCall | None = None  # 工具调用增量',
    )
    schemas.write_text(st, encoding="utf-8")
    print("schemas OK")
else:
    print("schemas already")

# ── 2) openai_compatible: extract reasoning_content ──
oc = Path(r"E:/项目/taktonl-0.1.0/backend/services/llm/openai_compatible.py")
ot = oc.read_text(encoding="utf-8")
old = '''                        content = delta.get("content", "")
                        if content:
                            yield LLMChunk(message_id=message_id, delta=content)

                        for tc in delta.get("tool_calls") or []:
'''
new = '''                        content = delta.get("content", "") or ""
                        if content:
                            yield LLMChunk(message_id=message_id, delta=content)

                        # 思考链：DeepSeek/Qwen/部分兼容接口
                        reasoning = (
                            delta.get("reasoning_content")
                            or delta.get("reasoning")
                            or delta.get("thought")
                            or ""
                        )
                        if isinstance(reasoning, dict):
                            reasoning = (
                                reasoning.get("text")
                                or reasoning.get("content")
                                or reasoning.get("summary")
                                or ""
                            )
                        if reasoning:
                            yield LLMChunk(
                                message_id=message_id,
                                delta="",
                                reasoning_delta=str(reasoning),
                            )

                        for tc in delta.get("tool_calls") or []:
'''
if "reasoning_content" not in ot or "reasoning_delta" not in ot:
    if old in ot:
        ot = ot.replace(old, new, 1)
        print("openai stream reasoning patched")
    else:
        old2 = old.replace("\n", "\r\n")
        if old2 in ot:
            ot = ot.replace(old2, new.replace("\n", "\r\n"), 1)
            print("openai stream reasoning patched crlf")
        else:
            print("OPENAI BLOCK NOT FOUND")
            idx = ot.find('content = delta.get("content"')
            print(repr(ot[idx:idx+250]))
else:
    print("openai already has reasoning")
oc.write_text(ot, encoding="utf-8")

# non-stream path too (content from message)
# skip if complex

# ── 3) loop.py: accumulate reasoning; only emit thinking not status ──
lp = Path(r"E:/项目/taktonl-0.1.0/backend/agent/loop.py")
lt = lp.read_text(encoding="utf-8")

# fix _push_status to NOT emit status rounds to progress_sink (only error)
old_push = '''        if state in ("thinking", "error", "running", "idle"):
            detail_out = detail or ""
            if detail_out.startswith("Thinking (round"):
                import re as _re

                m = _re.search(r"round\\s+(\\d+)", detail_out)
                detail_out = f"第 {m.group(1)} 轮思考中…" if m else "思考中…"
            await self._emit_progress(
                "error" if state == "error" else "status", detail_out
            )
'''
# try flexible match
pat = r'        if state in \("thinking", "error", "running", "idle"\):\r?\n(?:.*\r?\n){1,12}?            await self\._emit_progress\(\r?\n                "error" if state == "error" else "status", detail_out\r?\n            \)\r?\n'
new_push = '''        # 社交通道只推「真实思考内容」，不推「第 N 轮思考中」这类硬编码状态
        if state == "error" and detail:
            await self._emit_progress("error", detail)
'''
lt2, n = re.subn(pat, new_push, lt, count=1)
print("push_status strip status", n)
if n == 0:
    # manual find
    if '第 {m.group(1)} 轮思考中' in lt or '第 {m.group(1)} 轮思考中…' in lt:
        # replace whole if block more loosely
        start = lt.find('        if state in ("thinking", "error", "running", "idle"):')
        if start < 0:
            start = lt.find("        if state in ('thinking', 'error', 'running', 'idle'):")
        end = lt.find('    async def _push_stream', start)
        if start > 0 and end > start:
            lt2 = lt[:start] + new_push + "\n" + lt[end:]
            print("push_status replaced by slice")
        else:
            lt2 = lt
            print("push_status FAIL", start, end)
    else:
        lt2 = lt
        print("no chinese status block")

# stream accumulation of reasoning
old_acc = '''            accumulated_content = ""
            tool_calls = []

            try:
'''
new_acc = '''            accumulated_content = ""
            accumulated_reasoning = ""
            tool_calls = []

            try:
'''
if "accumulated_reasoning" not in lt2:
    if old_acc in lt2:
        lt2 = lt2.replace(old_acc, new_acc, 1)
    else:
        lt2 = lt2.replace(old_acc.replace("\n","\r\n"), new_acc.replace("\n","\r\n"), 1)
    print("acc reasoning var")

old_chunk = '''                    if chunk.delta:
                        accumulated_content += chunk.delta
                        await self._push_stream(
                            session_id, message_id, chunk.delta
                        )

                    # 收集 tool call
'''
new_chunk = '''                    if chunk.delta:
                        accumulated_content += chunk.delta
                        await self._push_stream(
                            session_id, message_id, chunk.delta
                        )

                    # 思考链增量（不进前端 stream，仅汇总给通道 progress）
                    rdelta = getattr(chunk, "reasoning_delta", None) or ""
                    if rdelta:
                        accumulated_reasoning += rdelta

                    # 收集 tool call
'''
if "reasoning_delta" not in lt2 or "accumulated_reasoning +=" not in lt2:
    if old_chunk in lt2:
        lt2 = lt2.replace(old_chunk, new_chunk, 1)
        print("chunk reasoning")
    elif old_chunk.replace("\n","\r\n") in lt2:
        lt2 = lt2.replace(old_chunk.replace("\n","\r\n"), new_chunk.replace("\n","\r\n"), 1)
        print("chunk reasoning crlf")
    else:
        print("chunk block not found")

old_emit = '''            # 通道进度：本轮模型自然语言思考（不含 tool 调用细节）
            if accumulated_content and str(accumulated_content).strip():
                await self._emit_progress("thinking", str(accumulated_content).strip()[:1200])
'''
new_emit = '''            # 通道进度：优先 reasoning，其次可见 content（不含 tool 调用细节）
            _think = (accumulated_reasoning or accumulated_content or "").strip()
            if _think:
                await self._emit_progress("thinking", _think[:1200])
'''
if "accumulated_reasoning or accumulated_content" not in lt2:
    if old_emit in lt2:
        lt2 = lt2.replace(old_emit, new_emit, 1)
        print("emit prefer reasoning")
    elif old_emit.replace("\n","\r\n") in lt2:
        lt2 = lt2.replace(old_emit.replace("\n","\r\n"), new_emit.replace("\n","\r\n"), 1)
        print("emit prefer reasoning crlf")
    else:
        # try simpler
        if "await self._emit_progress(\"thinking\"" in lt2:
            lt2 = lt2.replace(
                'if accumulated_content and str(accumulated_content).strip():\n                await self._emit_progress("thinking", str(accumulated_content).strip()[:1200])',
                ' _think = (accumulated_reasoning or accumulated_content or "").strip()\n            if _think:\n                await self._emit_progress("thinking", _think[:1200])',
            )
            print("emit simpler replace", "accumulated_reasoning or accumulated_content" in lt2)
        else:
            print("emit block not found")

lp.write_text(lt2, encoding="utf-8")
ast.parse(lt2)
print("loop syntax OK")

# ── 4) channel_gateway: no ack, only thinking ──
gp = Path(r"E:/项目/taktonl-0.1.0/backend/services/channel_gateway.py")
gt = gp.read_text(encoding="utf-8")

# rewrite publisher class methods section
# Remove ack usage
gt = gt.replace("            await progress.ack()\n\n", "")
gt = gt.replace("            await progress.ack()\r\n\r\n", "")

# Replace ChannelProgressPublisher body more carefully
cls_start = gt.find("class ChannelProgressPublisher:")
cls_end = gt.find("class ChannelGateway:")
if cls_start < 0 or cls_end < 0:
    raise SystemExit("publisher class bounds not found")

new_cls = '''class ChannelProgressPublisher:
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
        if re.match(r"^(收到[，,]?正在处理|第\\s*\\d+\\s*轮思考|思考中…?|Thinking \\(round)", raw):
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


'''

# need import re at top of channel_gateway
if "import re" not in gt.split("class ChannelGateway")[0]:
    gt = gt.replace("import asyncio\n", "import asyncio\nimport re\n", 1)

gt = gt[:cls_start] + new_cls + gt[cls_end:]
gp.write_text(gt, encoding="utf-8")
ast.parse(gt)
print("gateway syntax OK", "ack(" in gt, "收到，正在处理" in gt)
