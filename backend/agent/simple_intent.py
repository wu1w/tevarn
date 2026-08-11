"""Simple single-session intents: answer in-session, never dispatch to crew.

Hard strip is intentionally narrow — false positives on steward sessions
block grants/assign and are worse than over-dispatch.
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
    r"上线|部署|优化|性能|权限|安全|登录页|提权|工单|编制|员工进度",
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


def _simple_session_py(
    text: str | None,
    *,
    max_chars: int = 200,
) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if t.startswith("【系统·") or t.startswith("【工作任务】"):
        return False
    if wants_team_dispatch(t):
        return False
    if _HEAVY.search(t):
        return False
    if _CREW_TOPIC.search(t):
        return False
    if _MULTI_STEP.search(t) and len(t) > 40:
        return False
    if _ACK_ONLY.match(t):
        return True
    if any(p.search(t) for p in _SIMPLE_PATTERNS):
        return len(t) <= max(max_chars, 160)
    return False


def is_simple_session_intent(
    text: str | None,
    *,
    max_chars: int = 200,
) -> bool:
    """True when this turn should stay in the current session (no Inbox).

    Prefer Rust ``harness_simple_session`` / harness_resolve.simple_session.
    """
    t = (text or "").strip()
    if not t:
        return False
    try:
        import os

        be = (os.environ.get("TAKTON_KERNEL_BACKEND") or "").strip().lower()
        if be not in {"python", "py", "off", "0", "none"}:
            from backend.kernel import get_kernel

            k = get_kernel()
            if hasattr(k, "harness_simple_session"):
                r = k.harness_simple_session(text=t, max_chars=max_chars)
                if isinstance(r, dict) and "simple_session" in r:
                    return bool(r.get("simple_session"))
            elif hasattr(k, "_call"):
                r = k._call(
                    "harness_simple_session",
                    {"text": t, "max_chars": int(max_chars)},
                )
                if isinstance(r, dict) and "simple_session" in r:
                    return bool(r.get("simple_session"))
    except Exception:
        pass
    return _simple_session_py(t, max_chars=max_chars)


def filter_dispatch_tools_from_schema(
    tools: list[dict[str, Any]] | None,
    *,
    user_text: str | None = None,
    messages: list[dict[str, Any]] | None = None,
    force: bool | None = None,
) -> list[dict[str, Any]] | None:
    if not tools:
        return tools
    text = user_text if user_text is not None else last_user_text(messages)
    if force is None:
        strip = is_simple_session_intent(text)
    else:
        strip = bool(force)
    if not strip:
        return tools

    out: list[dict[str, Any]] = []
    for t in tools:
        if not isinstance(t, dict):
            out.append(t)
            continue
        fn = t.get("function") if isinstance(t.get("function"), dict) else {}
        name = str((fn or {}).get("name") or t.get("name") or "")
        if name in DISPATCH_TOOL_NAMES:
            continue
        out.append(t)
    return out


def simple_session_system_note() -> str:
    return (
        f"{SIMPLE_NOTE_MARKER} 本条为简单查询/短任务。"
        "直接用 web_search / current_time / file_read 等在本会话完成并回答；"
        "禁止 crew_steward hire/assign、delegate_task、agent_call 或任何派工单。"
    )


def is_ephemeral_system_note(content: str | None) -> bool:
    return bool(content and SIMPLE_NOTE_MARKER in str(content))


# ── Harness mode (Grok Build open-source style: default thin, escalate only) ─
# Authority: crates/takton-kernel/src/harness.rs (RPC harness_resolve / harness_select_mcp)
# Python below is offline fallback for pytest without kernel host.
# chat  = interactive thin · max_iters≈8 (Grok hard cap, not 15/40 fat loop)
# ops   = shell/proxy · max_iters≈12
# coding = engineering · max_iters≈20
_MCP_CFG_RE = re.compile(
    r"(?i)manage_mcp|"
    r"配(置|一下|下|好).{0,20}(mcp|密钥|api\s*key)|"
    r"(mcp|MCP).{0,16}(配|装|reload|热同步|密钥|env)|"
    r"(api\s*key|密钥).{0,20}(配|写|设|加|填|更新)|"
    # 装/配 任意名 MCP（含自定义），不限于预制产品
    r"(装下|装个|装上|安装|配置|添加).{0,20}(mcp|MCP)|"
    r"(装|配置|安装|添加).{0,16}[\w\u4e00-\u9fff\-]{2,32}.{0,8}(mcp|MCP)|"
    r"(装|配置|安装).{0,12}(mcp|豆包|tavily|firecrawl)|"
    r"给你.{0,8}(加了|配).{0,16}(mcp|豆包|tavily)",
    re.I,
)
_OPS_RE = re.compile(
    r"(?i)\b(proxy|tunnel|codex|powershell|HTTP_PROXY|HTTPS_PROXY)\b|"
    r"代理|隧道|3128|1080|环境变量|启动\s*codex|进程|端口|"
    r"连不通|崩溃|卸载服务",
    re.I,
)
_CODING_RE = re.compile(
    r"(?i)改代码|重构|审计|全仓|实现功能|开发一个|写一(个|份)项目|修复bug|修\s*bug|"
    r"上线|部署|登录页|pytest|cargo|apply_patch|多文件|编译失败|"
    r"代码审查|code\s*review|review\s*(this|the|pr|pull)|"
    r"\brefactor\b|\bimplement\b|\bpull request\b|\bcommit\b|"
    r"file_write|补全.*(?:crate|源码|代码)|github\.com/",
    re.I,
)
# 使用意图：预制 + 任意服务名/slug（自定义 MCP 点名）
_MCP_USE_RE = re.compile(
    r"(?i)(用|调用|走|via)\s*(豆包|tavily|firecrawl|fetch|github|"
    r"[A-Za-z][\w\-]{1,40}|[\u4e00-\u9fff]{2,16})"
    r".{0,16}(搜|search|查|list|get|读|写|同步|工具)?|"
    r"(豆包|tavily|firecrawl|github).{0,8}(搜|search)|"
    r"mcp_[\w\-]+|"
    r"(用|调用).{0,12}mcp|"
    r"用tavily|用\s*tavily",
    re.I,
)
# 内置别名（中文/常见产品）；自定义服务器靠 live catalog 动态匹配
_MCP_PRODUCTS = (
    ("tavily", ("tavily",)),
    ("firecrawl", ("firecrawl",)),
    ("fetch", ("fetch",)),
    ("github", ("github", "gh_")),
    ("doubao", ("doubao", "豆包", "askecho")),
)
# 用户话里的标识符 stopwords（避免 "with/search" 误挂全量）
_MCP_TOKEN_STOP = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "use",
        "using",
        "via",
        "call",
        "mcp",
        "api",
        "key",
        "token",
        "search",
        "list",
        "get",
        "set",
        "add",
        "http",
        "https",
        "www",
        "com",
        "tool",
        "tools",
        "server",
        "query",
        "please",
        "help",
        "帮我",
        "一下",
        "什么",
        "怎么",
    }
)

# Grok-aligned turn/history caps (mirror Rust harness::HarnessMode::limits)
HARNESS_LIMITS: dict[str, dict[str, Any]] = {
    "chat": {
        "max_iters": 8,
        "history_cap": 40,
        "tool_profile": "core",
        "ban_crew": True,
        "ban_use_tool_pack": True,
    },
    "ops": {
        "max_iters": 12,
        "history_cap": 48,
        "tool_profile": "ops",
        "ban_crew": True,
        "ban_use_tool_pack": False,
    },
    "coding": {
        "max_iters": 20,
        "history_cap": 64,
        "tool_profile": "coding",
        "ban_crew": False,
        "ban_use_tool_pack": False,
    },
}


def is_mcp_configure_intent(text: str | None) -> bool:
    return bool(text and _MCP_CFG_RE.search(text))


def is_mcp_use_intent(text: str | None) -> bool:
    t = (text or "").strip()
    if not t or is_mcp_configure_intent(t):
        return False
    return bool(_MCP_USE_RE.search(t))


def _rust_harness_resolve(
    text: str | None,
    *,
    workforce: bool = False,
    mode: str | None = None,
    write_intent: bool | None = None,
) -> dict[str, Any] | None:
    """Prefer Rust kernel authority (Grok-style). None if host offline."""
    try:
        # Skip host boot in pure-Python / unit-test backend
        import os

        be = (os.environ.get("TAKTON_KERNEL_BACKEND") or "").strip().lower()
        if be in {"python", "py", "off", "0", "none"}:
            return None
        from backend.kernel import get_kernel

        k = get_kernel()
        # Python AgentKernel has no harness_* — do not attempt RPC
        if not hasattr(k, "harness_resolve") and not hasattr(k, "_call"):
            return None
        if type(k).__name__ == "AgentKernel" and not hasattr(k, "harness_resolve"):
            return None
        payload = {
            "text": str(text or ""),
            "workforce": bool(workforce),
            "mode": mode,
            "write_intent": write_intent,
        }
        if hasattr(k, "harness_resolve"):
            r = k.harness_resolve(**payload)
            if isinstance(r, dict) and r.get("harness_mode"):
                return r
        if hasattr(k, "_call"):
            r = k._call("harness_resolve", payload)
            if isinstance(r, dict) and r.get("harness_mode"):
                return r
    except Exception:
        return None
    return None


def resolve_harness_bundle(
    text: str | None,
    *,
    workforce: bool = False,
    mode: str | None = None,
    write_intent: bool | None = None,
) -> dict[str, Any]:
    """Full harness decision: prefer Rust, fallback pure Python."""
    if write_intent is None and text:
        try:
            from backend.agent.write_intent import is_write_intent

            write_intent = is_write_intent(text)
        except Exception:
            write_intent = False
    rust = _rust_harness_resolve(
        text, workforce=workforce, mode=mode, write_intent=write_intent
    )
    if rust:
        return rust
    return _resolve_harness_bundle_py(
        text, workforce=workforce, mode=mode, write_intent=write_intent
    )


def _resolve_harness_bundle_py(
    text: str | None,
    *,
    workforce: bool = False,
    mode: str | None = None,
    write_intent: bool | None = None,
) -> dict[str, Any]:
    h = _resolve_harness_mode_py(
        text, workforce=workforce, mode=mode, write_intent=write_intent
    )
    lim = dict(HARNESS_LIMITS.get(h) or HARNESS_LIMITS["chat"])
    intent = "L0"
    if h == "coding":
        intent = "L3"
    elif h == "ops":
        if is_mcp_configure_intent(text) and not _OPS_RE.search(text or ""):
            intent = "L1"
        else:
            intent = "L2"
    elif is_mcp_configure_intent(text):
        intent = "L1"
    if intent == "L1":
        lim["tool_profile"] = "assistant"
        lim["max_iters"] = 10
        lim["ban_use_tool_pack"] = False
    light = h == "chat" or intent == "L1"
    ops = h == "ops" and intent != "L1"
    return {
        "harness_mode": h,
        "loop_intent": intent,
        "max_iters": int(lim["max_iters"]),
        "history_cap": int(lim["history_cap"]),
        "tool_profile": str(lim["tool_profile"]),
        "light_loop": light,
        "ops_loop": ops,
        "ban_crew": bool(lim.get("ban_crew")) or intent == "L1",
        "ban_use_tool_pack": bool(lim.get("ban_use_tool_pack")) and intent != "L1",
        "mcp_configure": is_mcp_configure_intent(text),
        "mcp_use": is_mcp_use_intent(text),
        "mcp_keywords": mcp_product_keywords(text or ""),
        "write_intent": bool(write_intent),
        "cmd_family_force_after": 3 if light else (6 if ops else 8),
        "role_kind": (
            "implement"
            if workforce
            else ("chat" if light or ops else "coding")
        ),
        "auto_continue": not (light or ops),
        "max_segments": 1 if light or ops else 3,
        "authority": "python",
    }


def _resolve_harness_mode_py(
    text: str | None,
    *,
    workforce: bool = False,
    mode: str | None = None,
    write_intent: bool | None = None,
) -> str:
    mode_l = str(mode or "").strip().lower()
    if workforce or mode_l in {"cluster", "goal", "crew"}:
        return "coding"
    t = (text or "").strip()
    if not t:
        return "chat"
    if t.startswith("【系统·") or t.startswith("【工作任务】"):
        return "coding"
    if wants_team_dispatch(t):
        return "coding"
    if write_intent is None:
        try:
            from backend.agent.write_intent import is_write_intent

            write_intent = is_write_intent(t)
        except Exception:
            write_intent = False
    # GitHub review / audit must escalate (Grok never starves multi-step review at L0@5)
    if write_intent or _CODING_RE.search(t) or _HEAVY.search(t):
        return "coding"
    if "github" in t.lower() and re.search(
        r"(?i)审|review|pr|pull|issue|commit|仓库|repo", t
    ):
        return "coding"
    if is_mcp_configure_intent(t) or _OPS_RE.search(t):
        return "ops"
    return "chat"


def resolve_harness_mode(
    text: str | None,
    *,
    workforce: bool = False,
    mode: str | None = None,
    write_intent: bool | None = None,
) -> str:
    """Grok-style harness: default **chat** (thin); escalate to ops/coding only.

    Prefer Rust ``harness_resolve``; Python fallback for offline tests.
    """
    b = resolve_harness_bundle(
        text, workforce=workforce, mode=mode, write_intent=write_intent
    )
    return str(b.get("harness_mode") or "chat")


def classify_loop_intent(
    text: str | None,
    *,
    workforce: bool = False,
    mode: str | None = None,
) -> str:
    """Backward-compatible L0–L3 labels mapped from harness mode.

    L0/L1 → chat thin · L2 → ops · L3 → coding
    """
    b = resolve_harness_bundle(text, workforce=workforce, mode=mode)
    return str(b.get("loop_intent") or "L0")


def is_light_loop_intent(text: str | None, **kwargs: Any) -> bool:
    """True when bundle light_loop (includes L1 MCP configure)."""
    b = resolve_harness_bundle(text, **kwargs)
    return bool(b.get("light_loop"))


def is_mid_ops_loop_intent(text: str | None, **kwargs: Any) -> bool:
    """True when real ops loop (L2), not L1 configure-as-light."""
    b = resolve_harness_bundle(text, **kwargs)
    return bool(b.get("ops_loop"))


def mcp_product_keywords(user_input: str) -> list[str]:
    """Built-in product tokens mentioned in user text (not the only match source)."""
    t = (user_input or "").lower()
    raw = user_input or ""
    hits: list[str] = []
    for canon, aliases in _MCP_PRODUCTS:
        for a in aliases:
            if a.lower() in t or a in raw:
                hits.append(canon)
                break
    return hits


def live_mcp_server_map() -> dict[str, str]:
    """tool_name → server_name（自定义 MCP 靠 server 名匹配，不只看工具名）。"""
    try:
        from backend.tools.base import ToolSource
        from backend.tools.registry import ToolRegistry

        out: dict[str, str] = {}
        for t in ToolRegistry.get_all(source=ToolSource.MCP):
            if not getattr(t, "enabled", True):
                continue
            name = str(getattr(t, "name", "") or "")
            srv = str(getattr(t, "server_name", "") or "").strip()
            if name:
                out[name] = srv
        return out
    except Exception:
        return {}


def _user_mcp_tokens(user_input: str) -> list[str]:
    """从用户话抽取可能的 MCP 服务/工具标识（英数 slug + 短中文块）。"""
    raw = user_input or ""
    tokens: list[str] = []
    for m in re.finditer(r"[A-Za-z][A-Za-z0-9_\-]{1,40}", raw):
        tok = m.group(0).lower()
        if tok in _MCP_TOKEN_STOP or len(tok) < 2:
            continue
        tokens.append(tok)
    for m in re.finditer(r"[\u4e00-\u9fff]{2,12}", raw):
        tok = m.group(0)
        if tok in _MCP_TOKEN_STOP:
            continue
        tokens.append(tok)
    # 保序去重
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def match_mcp_tools_flexible(
    user_input: str,
    live_names: list[str],
    server_map: dict[str, str] | None = None,
) -> list[str]:
    """Matching-only：预制别名 ∪ 用户点名的 live server/tool 片段。

    不依赖硬编码白名单枚举全部自定义 MCP；只要已注册且用户话里出现
    server_name 或工具名中的显著 token，就挂上对应 mcp_*。
    """
    if not live_names:
        return []
    smap = server_map if server_map is not None else live_mcp_server_map()
    text_l = (user_input or "").lower()
    raw = user_input or ""
    kws: set[str] = {k.lower() for k in mcp_product_keywords(raw)}

    # 已注册 server 名出现在用户话里 → 纳入
    for name in live_names:
        srv = (smap.get(name) or "").strip()
        if not srv:
            continue
        sl = srv.lower()
        if len(sl) >= 2 and (sl in text_l or srv in raw):
            kws.add(sl)

    # 用户 token 命中 tool 名或 server 名
    for tok in _user_mcp_tokens(raw):
        tl = tok.lower()
        for name in live_names:
            nl = name.lower()
            srv = (smap.get(name) or "").lower()
            srv_norm = srv.replace("-", "_")
            tok_norm = tl.replace("-", "_")
            if (
                tl in nl
                or tok_norm in nl.replace("-", "_")
                or (srv and (tl == srv or tok_norm == srv_norm or tl in srv or tok_norm in srv_norm))
            ):
                kws.add(tl)
                if srv:
                    kws.add(srv)
                break

    if not kws:
        return []

    out: list[str] = []
    for name in live_names:
        nl = name.lower().replace("-", "_")
        srv = (smap.get(name) or "").lower().replace("-", "_")
        if any(
            (k.replace("-", "_") in nl)
            or (srv and (k.replace("-", "_") == srv or k.replace("-", "_") in srv))
            for k in kws
        ):
            out.append(name)
    return out


def select_live_mcp_tools(
    user_input: str,
    live_names: list[str] | None,
    *,
    matching_only: bool = True,
    auto_attach_all: bool = False,
    server_map: dict[str, str] | None = None,
) -> list[str]:
    """Grok-style matching MCP attach.

    Prefer local flexible match (custom servers via server_name + tokens).
    Rust harness_select_mcp remains fallback for product-keyword path.
    """
    live = list(live_names or [])
    if not live:
        return []
    if auto_attach_all or not matching_only:
        return live
    if is_mcp_configure_intent(user_input):
        return []

    # 1) 本地灵活匹配（自定义 MCP 主路径）
    smap = server_map if server_map is not None else live_mcp_server_map()
    flexible = match_mcp_tools_flexible(user_input, live, smap)
    if flexible:
        return flexible

    # 2) Rust 权威（预制产品 / 旧 host）
    try:
        from backend.kernel import get_kernel

        k = get_kernel()
        payload = {
            "text": str(user_input or ""),
            "live_tools": live,
            "matching_only": bool(matching_only),
            "auto_attach_all": bool(auto_attach_all),
        }
        r = None
        if hasattr(k, "harness_select_mcp"):
            r = k.harness_select_mcp(**payload)
        elif hasattr(k, "_call"):
            r = k._call("harness_select_mcp", payload)
        if isinstance(r, dict) and "tools" in r:
            rust_sel = [str(x) for x in (r.get("tools") or []) if x]
            if rust_sel:
                return rust_sel
    except Exception:
        pass

    # 3) 纯预制别名（无 registry 元数据时）
    kws = mcp_product_keywords(user_input)
    if not kws:
        return []
    out: list[str] = []
    for name in live:
        nl = name.lower()
        if any(k.lower() in nl for k in kws):
            out.append(name)
    return out


__all__ = [
    "DISPATCH_TOOL_NAMES",
    "SIMPLE_NOTE_MARKER",
    "HARNESS_LIMITS",
    "wants_team_dispatch",
    "is_simple_session_intent",
    "filter_dispatch_tools_from_schema",
    "simple_session_system_note",
    "is_ephemeral_system_note",
    "resolve_harness_mode",
    "resolve_harness_bundle",
    "classify_loop_intent",
    "is_light_loop_intent",
    "is_mid_ops_loop_intent",
    "is_mcp_configure_intent",
    "is_mcp_use_intent",
    "mcp_product_keywords",
    "live_mcp_server_map",
    "match_mcp_tools_flexible",
    "select_live_mcp_tools",
]
