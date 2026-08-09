"""Simple / solo single-session intents: answer in-session, never dispatch to crew.

Hard strip is intentionally narrow — false positives on steward sessions
block grants/assign and are worse than over-dispatch.

Solo extends simple: plan/read/summarize turns also strip crew + goal tools
so CEO sessions do not start every doc/plan question with manage_goal/hire.
"""

from __future__ import annotations

import re
from typing import Any

from backend.agent.direct_intent import last_user_text

DISPATCH_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "crew_steward",
        "delegate_task",
        "agent_call",
        "manage_sub_agent",
    }
)

# Goal/orchestration surface stripped on solo plan/read turns (not on team ask).
SOLO_GOAL_TOOLS: frozenset[str] = frozenset(
    {
        "manage_goal",
        "okr_goal",
        "autopilot",
    }
)

SOLO_STRIP_TOOLS: frozenset[str] = DISPATCH_TOOL_NAMES | SOLO_GOAL_TOOLS

# Explicit team / dispatch / steward-ops language → never strip
_TEAM_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.I)
    for p in (
        r"派给",
        r"派工",
        r"指派",
        r"交给\s*员工",
        r"交给\s*(工程师|研究员|文秘|同事)",
        r"让\s*员工",
        r"让\s*(工程师|研究员|文秘|同事)",
        r"安排\s*(工程师|研究员|文秘|同事|员工)",
        r"叫\s*(工程师|研究员|文秘|同事|员工)",
        r"雇(一个|个|人|员)?",
        r"招聘",
        r"项目组",
        r"叫人",
        r"找(个|一位)?(工程师|研究员|文秘|同事)",
        r"团队(一起|协作|并行)",
        r"多角色",
        r"crew_steward",
        r"\bassign\b",
        r"\bhire\b",
        r"\bdelegate\b",
        r"员工去做",
        r"同事去做",
        r"分给",
        r"并行(处理|调研|开发)",
        r"提权",
        r"grant_caps",
        r"pending_grants",
        r"requeue",
        r"批(准|一下)?提权",
        r"list\s*(一下)?\s*(员工|编制|同事)",
        r"员工列表",
        r"编制列表",
    )
)

# High-confidence casual / factoid / trending search
_SIMPLE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.I)
    for p in (
        r"天气",
        r"气温",
        r"下雨",
        r"\bweather\b",
        r"热搜",
        r"热点",
        r"热门项目",
        r"热门\s*(开源|项目|仓库|repo)",
        r"github.*热门",
        r"热门.*github",
        r"trending\s*repo",
        r"star\s*最多",
        r"最多\s*star",
        r"\btrending\b",
        r"开源项目",
        r"现在几点",
        r"几点了",
        r"what\s*time",
        r"翻译",
        r"\btranslate\b",
        # definitional Q only when NOT about workforce (see _HEAVY / _CREW_TOPIC)
        r"什么是",
        r"what\s+is\b",
        r"who\s+is\b",
        r"是谁",
        r"(今日|今天|最新).{0,8}新闻",
        r"股价",
        r"汇率",
        r"算(一下|下)",
        r"怎么念",
        r"含义是什么",
        r"解释一下.{0,20}$",
        r"\bdefine\b",
        r"^(帮我)?搜(一下|下)\s*.{0,60}$",
        r"^(帮我)?查(一下|下)\s*.{0,60}$",
        r"^(帮我)?看看\s*.{0,60}$",
    )
)

# Plan / read / summarize — stay in-session, no crew hire / manage_goal default
_PLAN_READ_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.I)
    for p in (
        r"读(一下|读|下)?(文档|spec|readme|说明)",
        r"看(看|下)(文档|spec|readme|说明)",
        r"总结(一下|下)?",
        r"概(览|述)",
        r"阶段(划分|计划|安排)?",
        # Require planning language — bare「下一步」 alone matches 继续下一步 (false solo)
        r"下一步(应该|怎么|如何|做什么|计划|规划)",
        r"(做|写|出|给).{0,8}(个|一份)?\s*m0\b",
        r"\bm0\b.{0,12}(计划|plan|方案)",
        r"(制定|写|做).{0,8}(计划|方案|路线图|roadmap)",
        r"怎么设计",
        r"如何设计",
        r"现状(如何|怎么样)?",
        r"进度如何",
        r"read\s+(the\s+)?docs?",
        r"summar(y|ize)",
        r"what\s+next",
        r"how\s+(does|should|to)\b",
        r"\bexplain\b",
        r"\boverview\b",
        r"\bplan\s+(for|the|out)\b",
        r"按照文档",
        r"按文档",
        r"讲讲",
        r"解释一下",
    )
)

# Continue / execute work — never treat as casual plan/read solo
_CONTINUE_OR_EXECUTE = re.compile(
    r"(?i)("
    r"^(请继续|继续|接着做|接着干|往下做|继续推进|继续目标|continue|resume|keep\s+going)|"
    r"继续(下一步|推进|做|干|执行)|"
    r"下一步继续|"
    r"接着(?:做|干)?下一项|继续下一项|下一项工作|下一项任务|"
    r"开始执行|并开始|并执行|动手(改|做|写)|落地实现|写代码|改代码"
    r")",
)

# Steward / workforce topics must never be treated as casual Q&A.
# Bare「员工」 alone is NOT enough (avoid 员工食堂 false under-strip block).
_CREW_TOPIC = re.compile(
    r"编制|工单|提权|派工|收件箱|\binbox\b|管家|\bceo\b|\bgrant\b|预算|"
    r"身份档案|capabilities|令牌|"
    # 「员工」 only in workforce context
    r"员工(列表|进度|编制|档案|提权|工单)|"
    # Job-role probes
    r"(工程师|研究员|文秘|运维|同事|码农|开发)s?(进度|状态|在干嘛|在做什么|忙什么|怎么样|忙不忙)?|"
    r"(进度|状态|在干嘛).{0,12}(工程师|研究员|文秘|运维|同事)|"
    # 查/看员工… but not cafeteria/benefits small-talk
    r"(查|看|问).{0,8}(工程师|研究员|文秘|运维|同事)(?!食堂|福利|餐|宿舍)|"
    r"(查|看|问).{0,8}员工(?!食堂|福利|餐|宿舍|活动)",
    re.I,
)

_MULTI_STEP = re.compile(
    r"并且|然后|同时|分别|第一步|第二步|and\s+then|step\s*1|先.*再",
    re.I,
)
_HEAVY = re.compile(
    r"改代码|重构|审计|全仓|实现功能|开发一个|写一(个|份)项目|修复bug|修\s*bug|"
    r"上线|部署|优化|性能|权限|安全|登录页|提权|工单|编制|员工进度|"
    r"开始执行|并开始执行|动手实现|落地实现",
    re.I,
)

_ACK_ONLY = re.compile(
    r"^(好|好的|嗯|哦|谢谢|感谢|收到|ok|okay|thanks|thx|继续|下一个)[\s!！。.~～]*$",
    re.I,
)

# Ephemeral system note marker — never persist to chat history DB
SIMPLE_NOTE_MARKER = "【单会话·ephemeral】"


def wants_team_dispatch(text: str | None) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    return any(p.search(t) for p in _TEAM_PATTERNS)


def _solo_blocked(t: str) -> bool:
    """Shared gates: never treat team/heavy/crew-topic as simple/solo."""
    if not t:
        return True
    if t.startswith("【系统·") or t.startswith("【工作任务】"):
        return True
    if wants_team_dispatch(t):
        return True
    if _HEAVY.search(t):
        return True
    if _CREW_TOPIC.search(t):
        return True
    if _MULTI_STEP.search(t) and len(t) > 40:
        return True
    return False


def is_simple_session_intent(
    text: str | None,
    *,
    max_chars: int = 200,
) -> bool:
    """True when this turn should stay in the current session (no Inbox)."""
    t = (text or "").strip()
    if not t or _solo_blocked(t):
        return False

    if _ACK_ONLY.match(t):
        return True

    if any(p.search(t) for p in _SIMPLE_PATTERNS):
        return len(t) <= max(max_chars, 160)

    return False


def is_plan_or_read_intent(
    text: str | None,
    *,
    max_chars: int = 400,
) -> bool:
    """Plan / read / summarize Q&A — solo, no crew/manage_goal default."""
    t = (text or "").strip()
    if not t or _solo_blocked(t):
        return False
    # Continue / execute work is not a plan-only Q&A turn
    if _CONTINUE_OR_EXECUTE.search(t):
        return False
    try:
        from backend.agent.goal_facade import looks_like_goal_continue

        if looks_like_goal_continue(t):
            return False
    except Exception:
        pass
    if any(p.search(t) for p in _PLAN_READ_PATTERNS):
        return len(t) <= max(max_chars, 280)
    # Short pure questions (no implement verbs)
    if len(t) <= 80 and t.endswith(("？", "?")):
        if re.search(r"(?i)(实现|重构|部署|上线|修复bug|修\s*bug|执行)", t):
            return False
        return True
    return False


def is_solo_session_intent(
    text: str | None,
    *,
    mode: str | None = None,
) -> bool:
    """Simple + plan/read + plan/ask/explore modes → single-session tool surface.

    Team-dispatch language always wins (returns False).
    Continue/execute phrases are NOT solo — they need full coding surface + history.
    """
    t = (text or "").strip()
    mode_l = str(mode or "").strip().lower()
    if t and wants_team_dispatch(t):
        return False
    # 「继续下一步 / 请继续 / 开始执行」— full agent, not plan-solo strip
    if t and _CONTINUE_OR_EXECUTE.search(t):
        # Bare ack 「继续」 alone can stay simple (no crew); longer continue-work does not
        if not re.match(
            r"^(好|好的|嗯|哦|谢谢|感谢|收到|ok|okay|thanks|thx|继续|下一个)[\s!！。.~～]*$",
            t,
            re.I,
        ):
            return False
    if mode_l in ("plan", "ask", "explore"):
        # Mode-forced solo unless user explicitly asks for crew
        if t and (wants_team_dispatch(t) or _CREW_TOPIC.search(t)):
            return False
        return True
    if is_simple_session_intent(t):
        return True
    if is_plan_or_read_intent(t):
        return True
    # Reuse goal_facade casual heuristic when available
    try:
        from backend.agent.goal_facade import looks_like_casual_or_read_only

        if looks_like_casual_or_read_only(t) and not _CONTINUE_OR_EXECUTE.search(t):
            return True
    except Exception:
        pass
    return False


def filter_dispatch_tools_from_schema(
    tools: list[dict[str, Any]] | None,
    *,
    user_text: str | None = None,
    messages: list[dict[str, Any]] | None = None,
    force: bool | None = None,
    strip_goal_tools: bool = True,
    mode: str | None = None,
) -> list[dict[str, Any]] | None:
    if not tools:
        return tools
    if force is None:
        text = user_text if user_text is not None else last_user_text(messages)
        if not is_solo_session_intent(text, mode=mode):
            return tools
    elif not force:
        return tools

    ban = SOLO_STRIP_TOOLS if strip_goal_tools else DISPATCH_TOOL_NAMES
    out: list[dict[str, Any]] = []
    for t in tools:
        if not isinstance(t, dict):
            out.append(t)
            continue
        fn = t.get("function") if isinstance(t.get("function"), dict) else {}
        name = str((fn or {}).get("name") or t.get("name") or "")
        if name in ban:
            continue
        out.append(t)
    return out


def simple_session_system_note() -> str:
    return (
        f"{SIMPLE_NOTE_MARKER} Solo turn (simple query / plan / read)."
        " Finish in this session with file_read / web_search / current_time etc."
        " Do **not** call crew_steward hire/assign, delegate_task, agent_call,"
        " manage_goal, okr_goal, or autopilot unless the user explicitly asks."
        " Answer the user in their language."
    )


def is_ephemeral_system_note(content: str | None) -> bool:
    return bool(content and SIMPLE_NOTE_MARKER in str(content))


__all__ = [
    "DISPATCH_TOOL_NAMES",
    "SOLO_GOAL_TOOLS",
    "SOLO_STRIP_TOOLS",
    "SIMPLE_NOTE_MARKER",
    "wants_team_dispatch",
    "is_simple_session_intent",
    "is_plan_or_read_intent",
    "is_solo_session_intent",
    "filter_dispatch_tools_from_schema",
    "simple_session_system_note",
    "is_ephemeral_system_note",
]
