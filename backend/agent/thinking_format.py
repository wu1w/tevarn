"""Reasoning / thinking presentation helpers.

Provider-native ``reasoning_content`` stays off the user channel (WS + chat
persist). ``wrap_thinking`` still exists for traces / tests / history reload of
older rows that stored CoT inside ``<thinking>`` tags. User-visible persist
uses ``user_channel.user_visible_content`` (strip, never re-wrap).

Also: force_final scare-report sanitizers so auto-resume history does not keep
replaying long 「强制收束」status inventories into the next run context.
"""

from __future__ import annotations

import re
from typing import Any, Optional

# Open/close tags must match frontend parseMessageContent PAIR_TAGS
_THINK_OPEN = "<thinking>"
_THINK_CLOSE = "</thinking>"

_THINK_BLOCK_RE = re.compile(
    r"<thinking\b[^>]*>[\s\S]*?</thinking>"
    r"|<think\b[^>]*>[\s\S]*?</think>"
    r"|\[Thinking\][\s\S]*?\[/Thinking\]"
    r"|【思考】[\s\S]*?【/思考】",
    re.I,
)
_THINK_OPEN_UNCLOSED_RE = re.compile(
    r"(?:<thinking\b[^>]*>|<think\b[^>]*>|\[Thinking\]|【思考】)([\s\S]*)$",
    re.I,
)

# Placeholders must not resemble think / tool-leak tags.
_CODE_MASK_RE = re.compile(r"\x00TEVARN_CODE_(\d+)\x00")
_FENCE_RE = re.compile(r"```[\s\S]*?```")
_INLINE_DBL_RE = re.compile(r"``[^`\n]*``")
_INLINE_SGL_RE = re.compile(r"`[^`\n]+`")


def _mask_code_spans(text: str) -> tuple[str, list[str]]:
    """Hide fenced code and inline backticks so think-tag regexes skip docs."""
    held: list[str] = []

    def _hold(m: re.Match[str]) -> str:
        held.append(m.group(0))
        return f"\x00TEVARN_CODE_{len(held) - 1}\x00"

    s = _FENCE_RE.sub(_hold, text)
    s = _INLINE_DBL_RE.sub(_hold, s)
    s = _INLINE_SGL_RE.sub(_hold, s)
    return s, held


def _unmask_code_spans(text: str, held: list[str]) -> str:
    def _put(m: re.Match[str]) -> str:
        i = int(m.group(1))
        if 0 <= i < len(held):
            return held[i]
        return m.group(0)

    return _CODE_MASK_RE.sub(_put, text)

# Force-final / segment-end scare patterns that pollute chat + resume context
_FORCE_FINAL_MARKERS = re.compile(
    r"(?:【强制收束|"
    r"强制收束报告|"
    r"本轮被系统拦截|"
    r"禁止再调(?:用)?任何工具|"
    r"本轮禁止再调工具|"
    r"工具轮次已达硬顶|"
    r"Token 预算将尽|"
    r"Token 预算耗尽|"
    r"按强制收束要求|"
    r"按收束要求用中文汇报|"
    r"根据系统强制收束)",
    re.I,
)

# Long inventory dumps after force_final often look like dozens of 1-line status notes
_STATUS_LINE_RE = re.compile(
    r"^(?:继续|改用|先|正在|对齐|修复|重写|补齐|读取|查看|列出|验证|运行|"
    r"尝试|执行|本地|检查|编译|汇总|收束|用 cmd|用 dir|cargo |Still |Wait )"
    r".{0,80}$",
    re.M,
)


def wrap_thinking(reasoning: Optional[str], content: Optional[str]) -> str:
    """Prefix visible content with a closed thinking block when reasoning exists."""
    r = (reasoning or "").strip()
    c = content or ""
    if not r:
        return c
    # Avoid double-wrapping if content already starts with a thinking block
    head = c.lstrip()[:20].lower()
    if head.startswith("<thinking") or head.startswith("<think") or head.startswith("[thinking]"):
        return c
    if c.strip():
        return f"{_THINK_OPEN}\n{r}\n{_THINK_CLOSE}\n\n{c}"
    return f"{_THINK_OPEN}\n{r}\n{_THINK_CLOSE}"


def strip_thinking(text: Optional[str]) -> str:
    """Remove closed (and trailing unclosed) thinking blocks; return visible body.

    Fenced code and inline backticks are masked first so documenting
    ``<think>`` / ``</think>`` in prose cannot eat the rest of the message.
    Real leading ``<thinking>…`` (closed or unclosed) is still stripped.
    """
    if not text:
        return ""
    masked, held = _mask_code_spans(text)
    s = _THINK_BLOCK_RE.sub("", masked)
    s = _THINK_OPEN_UNCLOSED_RE.sub("", s)
    s = _unmask_code_spans(s, held)
    # fenced ```thinking (real CoT fences, not backtick-documented tags)
    s = re.sub(r"```(?:thinking|thought|reasoning)\s*\n[\s\S]*?```", "", s, flags=re.I)
    return s.strip()


def extract_reasoning_content(text: Optional[str]) -> str:
    """从持久化正文里的 <thinking>…</thinking> 抽出原生 reasoning_content。

    DeepSeek V4 thinking + tools 要求后续请求回传 reasoning_content；
    Tevarn 落库时把它包进 thinking 标签，回放历史时需还原为独立字段。
    """
    if not text:
        return ""
    text, held = _mask_code_spans(text)
    parts: list[str] = []

    def _inner(block: str) -> str:
        s = block.strip()
        s = re.sub(
            r"^<thinking\b[^>]*>|^<think\b[^>]*>|^\[Thinking\]|^【思考】",
            "",
            s,
            count=1,
            flags=re.I,
        )
        s = re.sub(
            r"</thinking>\s*$|</think>\s*$|\[/Thinking\]\s*$|【/思考】\s*$",
            "",
            s,
            count=1,
            flags=re.I,
        )
        return s.strip()

    for m in _THINK_BLOCK_RE.finditer(text):
        inner = _inner(m.group(0))
        if inner:
            parts.append(inner)
    # 仅当没有闭合块时，再取未闭合尾巴（流中断）；避免把 </thinking> 前内容二次匹配
    if not parts:
        m2 = _THINK_OPEN_UNCLOSED_RE.search(text)
        if m2:
            tail = (m2.group(1) or "").strip()
            if tail:
                parts.append(tail)
    return _unmask_code_spans("\n\n".join(parts).strip(), held)


# Persist-shape for reasoning: messages.tool_calls JSON has no sibling column.
# List-compatible (MessageRead): first real call carries ``_tevarn_reasoning``.
# Load also accepts wrap ``{"calls":[...], "reasoning":"..."}``.
TEVARN_REASONING_KEY = "_tevarn_reasoning"


def _is_reasoning_sentinel(tc: Any) -> bool:
    if not isinstance(tc, dict) or TEVARN_REASONING_KEY not in tc:
        return False
    if tc.get("id") or tc.get("function"):
        return False
    t = tc.get("type")
    if t and t not in (TEVARN_REASONING_KEY, "reasoning"):
        return False
    return True


def stash_reasoning_in_tool_calls(tool_calls: Any, reasoning: str | None) -> Any:
    """Attach reasoning onto tool_calls JSON without touching user-visible content."""
    r = (reasoning or "").strip()
    if isinstance(tool_calls, dict) and (
        "calls" in tool_calls or "tool_calls" in tool_calls
    ):
        inner = tool_calls.get("calls") or tool_calls.get("tool_calls") or []
        return stash_reasoning_in_tool_calls(inner, r)
    if not isinstance(tool_calls, list) or not tool_calls:
        return tool_calls
    calls: list[Any] = []
    for tc in tool_calls:
        if _is_reasoning_sentinel(tc):
            continue
        if isinstance(tc, dict):
            tc2 = dict(tc)
            tc2.pop(TEVARN_REASONING_KEY, None)
            calls.append(tc2)
        else:
            calls.append(tc)
    if r:
        for tc in calls:
            if isinstance(tc, dict):
                tc[TEVARN_REASONING_KEY] = r
                break
    return calls


def split_reasoning_from_tool_calls(tool_calls: Any) -> tuple[Any, str]:
    """Undo persist-shape: clean tool_calls list + stashed reasoning string."""
    if tool_calls is None:
        return None, ""
    if isinstance(tool_calls, dict):
        reasoning = str(
            tool_calls.get("reasoning")
            or tool_calls.get(TEVARN_REASONING_KEY)
            or ""
        ).strip()
        inner = tool_calls.get("calls")
        if inner is None:
            inner = tool_calls.get("tool_calls")
        if isinstance(inner, list):
            calls, extra = split_reasoning_from_tool_calls(inner)
            return calls, reasoning or extra
        if tool_calls.get("id") or tool_calls.get("function"):
            tc2 = dict(tool_calls)
            r = str(tc2.pop(TEVARN_REASONING_KEY, "") or reasoning)
            return [tc2], r.strip()
        return None, reasoning
    if not isinstance(tool_calls, list):
        return tool_calls, ""
    reasoning = ""
    calls: list[Any] = []
    for tc in tool_calls:
        if _is_reasoning_sentinel(tc):
            reasoning = str(tc.get(TEVARN_REASONING_KEY) or reasoning or "")
            continue
        if isinstance(tc, dict):
            tc2 = dict(tc)
            r = str(tc2.pop(TEVARN_REASONING_KEY, "") or "")
            if r:
                reasoning = r
            calls.append(tc2)
        else:
            calls.append(tc)
    return calls, reasoning.strip()


def is_visible_empty(text: Optional[str]) -> bool:
    """True when there is no user-visible body after stripping thinking."""
    return not strip_thinking(text)


def canonicalize_thinking(
    reasoning: Optional[str], content: Optional[str]
) -> str:
    """Single thinking block for persist/UI.

    When provider-native reasoning exists, strip any model-written thinking tags
    from content so we do not double-wrap (native + prompt-taught tags).
    """
    r = (reasoning or "").strip()
    c = content or ""
    if r:
        body = strip_thinking(c)
        return wrap_thinking(r, body)
    # No native reasoning — keep model-written tags as-is (ThinkingBlock path)
    return c


def looks_like_force_final_report(text: Optional[str]) -> bool:
    """Heuristic: long force_final inventory / scare report in assistant body."""
    body = strip_thinking(text)
    if not body:
        return False
    if _FORCE_FINAL_MARKERS.search(body):
        return True
    # Dozens of short status lines with little structure = status avalanche
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    if len(lines) < 12:
        return False
    statusish = sum(1 for ln in lines if _STATUS_LINE_RE.match(ln) or len(ln) < 40)
    return statusish >= max(10, int(len(lines) * 0.55))


def short_segment_handoff_message(*, goal_mode: bool = False) -> str:
    """Fallback handoff when segment ends and the model left no real body.

    Prefer keeping the model's own progress summary when present; this string
    is only used when the body is empty or a scare inventory.
    """
    if goal_mode:
        return "本段已告一段落，将自动开启下一段继续。"
    return "本段已告一段落。发送「请继续」可以接着做。"


def _body_is_real_user_summary(body: str) -> bool:
    """True when body looks like a real user-facing summary (not a scare dump)."""
    b = (body or "").strip()
    if len(b) < 80:
        return False
    if looks_like_force_final_report(b):
        return False
    if _FORCE_FINAL_MARKERS.search(b) and len(b) < 200:
        return False
    return True


def sanitize_force_final_body(
    text: Optional[str],
    *,
    goal_mode: bool = False,
    exit_code: str = "",
    prefer_short: bool = False,
) -> str:
    """Light hygiene for force_final scare dumps only.

    **Never** replaces a substantive streamed/model answer with a short handoff
    or transcript excerpt. Only trims pure status-avalanche scare reports that
    match force_final markers; everything else passes through unchanged.
    """
    del prefer_short  # no longer collapses real answers to short handoffs
    raw = text or ""
    if not raw.strip():
        return raw

    body = strip_thinking(raw)

    # Structured / long user-facing content always kept (plans, reports, code)
    if len(body.strip()) >= 40 and not looks_like_force_final_report(body):
        out = body
    elif looks_like_force_final_report(body) and _FORCE_FINAL_MARKERS.search(body):
        # Only collapse explicit scare inventories; keep any real prose tail
        trimmed = _trim_status_avalanche(body)
        if _body_is_real_user_summary(trimmed) or len(trimmed.strip()) >= 80:
            out = trimmed
        else:
            # Last resort for pure scare dump — still not a multi-section 摘录
            out = short_segment_handoff_message(goal_mode=goal_mode)
    else:
        out = body

    # User channel: never re-attach <thinking> onto chat content.
    return out


def is_generic_segment_handoff(text: Optional[str]) -> bool:
    """True when body is empty or only the stock 'tool rounds exhausted' line."""
    body = strip_thinking(text).strip()
    if not body:
        return True
    if len(body) < 120 and (
        "工具轮次已用尽" in body
        or "工具轮已用尽" in body
        or "Segment tool rounds exhausted" in body
        or "可发送「请继续」" in body
        or "请继续」或等待自动续跑" in body
        or "本段已告一段落" in body
        or "没有生成可见回复" in body
    ):
        return True
    return False


def _goal_summary_looks_completed(goal_summary: str) -> bool:
    g = (goal_summary or "").strip()
    if not g:
        return False
    if re.search(r"(?i)status:\s*(completed|cancelled|idle)", g):
        return True
    if "All actionable todos done" in g:
        return True
    # All todos marked done and none remaining
    if re.search(r"(?i)remaining:\s*0\b", g) and "[x]" in g and "[ ]" not in g and "[~]" not in g:
        return True
    if "已完成" in g and "Remaining" not in g:
        return True
    return False


def _goal_summary_relevant_to_user(goal_summary: str, user_input: str) -> bool:
    """Avoid pasting an unrelated completed Goal when user asked something else."""
    g = (goal_summary or "").strip()
    q = (user_input or "").strip()
    if not g:
        return False
    if not q:
        return True
    # Pull title line
    title = ""
    for line in g.splitlines():
        if line.lower().startswith("# goal:") or line.startswith("# Goal:"):
            title = line.split(":", 1)[-1].strip()
            break
    if not title:
        title = g[:80]
    # Token overlap (CJK bigrams + latin words)
    def _tokens(s: str) -> set[str]:
        s = s.lower()
        out: set[str] = set()
        for w in re.findall(r"[a-z0-9_]{3,}", s):
            out.add(w)
        # CJK runs → overlapping bigrams
        for m in re.finditer(r"[\u4e00-\u9fff]{2,}", s):
            run = m.group(0)
            for i in range(len(run) - 1):
                out.add(run[i : i + 2])
        return out

    qt, gt = _tokens(q), _tokens(title + " " + g[:400])
    if not qt or not gt:
        return True
    overlap = len(qt & gt)
    # User asked about M0 plan but goal is quality-review → low overlap
    if overlap == 0 and len(q) >= 8:
        return False
    if overlap <= 1 and len(qt) >= 4 and _goal_summary_looks_completed(g):
        return False
    return True


def synthesize_run_user_summary(
    *,
    user_input: str = "",
    messages: list | None = None,
    exit_reason: str = "",
    goal_summary: str = "",
    tool_rounds: int = 0,
    goal_mode: bool = False,
) -> str:
    """Build a user-facing progress summary when the model left an empty/short end.

    Pulls recent assistant progress lines and optional *active* Goal state so the
    chat never ends on only 「本段工具轮次已用尽」. Never dumps an unrelated
    completed Goal over the current user question.
    """
    progress: list[str] = []
    tool_names: list[str] = []
    q = (user_input or "").strip()
    # Scope harvest to messages after the last matching user turn (avoid old-task bleed)
    msgs = list(messages or [])
    scan = msgs
    if q and msgs:
        cut = -1
        q_norm = " ".join(q.split())[:200]
        for i in range(len(msgs) - 1, -1, -1):
            m = msgs[i]
            if not isinstance(m, dict) or m.get("role") != "user":
                continue
            c = m.get("content")
            if not isinstance(c, str):
                continue
            c_norm = " ".join(c.split())[:200]
            if c_norm == q_norm or (len(q_norm) >= 8 and q_norm in c_norm):
                cut = i
                break
        if cut >= 0:
            scan = msgs[cut:]
    for m in reversed(scan):
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role == "assistant":
            body = strip_thinking(str(m.get("content") or "")).strip()
            if not body or is_generic_segment_handoff(body):
                continue
            # skip pure English **Planning** micro-lines if we already have better
            if body.startswith("**") and len(body) < 80 and not progress:
                progress.append(body[:300])
            elif len(body) >= 20:
                progress.append(body[:600])
            if len(progress) >= 6:
                break
        elif role == "tool":
            # tool name from message meta if present
            name = ""
            tc = m.get("name") or m.get("tool_name")
            if tc:
                name = str(tc)
            content = str(m.get("content") or "")[:80]
            if name:
                tool_names.append(name)
            elif "[Success]" in content or "Found " in content or "Finished" in content:
                tool_names.append("tool")
    progress.reverse()
    # unique tools last 12
    seen_t: set[str] = set()
    tools_u: list[str] = []
    for n in reversed(tool_names[:40]):
        if n and n not in seen_t:
            seen_t.add(n)
            tools_u.append(n)
        if len(tools_u) >= 12:
            break
    tools_u.reverse()

    lines: list[str] = ["## 本段工作小结", ""]
    if q:
        lines.append(f"**你的问题：** {q[:400]}")
        lines.append("")
    if progress:
        lines.append("**本段进展（从对话摘录）：**")
        for p in progress[-5:]:
            one = " ".join(p.split())
            if len(one) > 280:
                one = one[:280] + "…"
            lines.append(f"- {one}")
        lines.append("")
    if tools_u:
        lines.append(f"**本段用过的工具：** {', '.join(tools_u)}")
        lines.append("")
    # Only attach Goal when this turn is goal_mode AND summary is relevant/active
    gs = (goal_summary or "").strip()
    if (
        gs
        and goal_mode
        and not _goal_summary_looks_completed(gs)
        and _goal_summary_relevant_to_user(gs, q)
    ):
        lines.append("**Goal 状态：**")
        lines.append(gs[:1200])
        lines.append("")
    elif gs and goal_mode and _goal_summary_looks_completed(gs):
        # Mention completion briefly — do not paste full todo dump
        title = ""
        for line in gs.splitlines():
            if "goal:" in line.lower():
                title = line.split(":", 1)[-1].strip()[:80]
                break
        lines.append(
            "**说明：** 会话里另有已完成目标"
            + (f"「{title}」" if title else "")
            + "，与本问无关时已省略详情。"
        )
        lines.append("")
    reason = (exit_reason or "").strip() or "max_tool_rounds"
    if tool_rounds:
        lines.append(
            f"（系统：本段约 {int(tool_rounds)} 轮工具后暂停，原因 `{reason}`。"
            "发送「请继续」可接着做；不会丢上下文。）"
        )
    else:
        lines.append(
            f"（系统：本段已暂停，原因 `{reason}`。发送「请继续」可接着做。）"
        )
    return "\n".join(lines).strip()


def ensure_user_facing_final(
    text: Optional[str],
    *,
    user_input: str = "",
    messages: list | None = None,
    exit_reason: str = "",
    goal_summary: str = "",
    tool_rounds: int = 0,
    goal_mode: bool = False,
) -> str:
    """Keep the model/stream final as-is.

    Previously replaced short/generic bodies with ``synthesize_run_user_summary``
    (「本段工作小结 / 从对话摘录」), which wiped good streamed answers into a
    short excerpt. That harvest path is disabled — never rewrite user-visible
    finals from history snippets.

    Only when the visible body is *completely empty* do we leave a one-line
    segment handoff (not a multi-section transcript dump).
    """
    raw = text or ""
    body = strip_thinking(raw)
    # Any non-empty model text wins — including short plans and mid-length answers.
    # Do not treat stock handoff as empty either: keep it rather than inventing excerpts.
    if body.strip():
        return body
    reason = (exit_reason or "").strip()
    if reason in ("stopped_by_user",):
        return ""
    from backend.agent.exit_reasons import format_exit_user_message

    if reason in ("", "completed"):
        reason = "empty_content_thrash"
    return format_exit_user_message(reason)


def strip_force_final_scare_for_context(text: Optional[str]) -> str:
    """For LLM history reload: drop long force_final inventories so resume stays clean.

    Replaces them with a one-line handoff. Does not touch normal assistant replies.
    """
    body = strip_thinking(text)
    if not body:
        return ""
    if looks_like_force_final_report(body) or (
        _FORCE_FINAL_MARKERS.search(body) and len(body) > 400
    ):
        return short_segment_handoff_message(goal_mode=True)
    # Mild trim: drop pure status-line blocks at the start of long messages
    if len(body) > 1500:
        trimmed = _trim_status_avalanche(body)
        if len(trimmed) < len(body) * 0.7:
            return trimmed
    return body


def _trim_status_avalanche(body: str) -> str:
    """Drop leading/middle runs of 1-line status chatter; keep last meaningful section."""
    lines = body.splitlines()
    # Prefer content after the last "---" or markdown heading that looks like a real report
    keep_from = 0
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("## ") or s.startswith("### ") or s == "---":
            # skip pure scare headings
            if _FORCE_FINAL_MARKERS.search(s):
                continue
            if any(
                k in s
                for k in (
                    "已完成",
                    "未完成",
                    "状态",
                    "目标",
                    "Todos",
                    "下一步",
                    "卡点",
                    "结论",
                )
            ):
                keep_from = i
    chunk = "\n".join(lines[keep_from:]).strip() if keep_from else body.strip()
    # Collapse runs of short status lines
    out_lines: list[str] = []
    status_run = 0
    for ln in chunk.splitlines():
        s = ln.strip()
        if not s:
            out_lines.append("")
            continue
        if _STATUS_LINE_RE.match(s) or (
            len(s) < 36 and not s.startswith(("#", "-", "*", "|", "```"))
        ):
            status_run += 1
            if status_run <= 2:
                out_lines.append(s)
            continue
        status_run = 0
        out_lines.append(s)
    out = "\n".join(out_lines).strip()
    if len(out) > 1800:
        out = out[:1600].rstrip() + "\n…(已省略冗长状态复读)"
    return out or short_segment_handoff_message(goal_mode=False)
