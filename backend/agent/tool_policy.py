"""对话工具/注入策略。

产品 profile（agent_tool_profile）：
- coding（默认）：编码主脑，高密度
- assistant：coding + 会话/澄清
- ops：assistant + manage/devices
- dynamic：coding 底座 + 场景关键词加包
- core：固定白名单，不加场景包
- full：全部工具

始终保留 meta：use_tool_pack；injection_tier 控制 RAG/Wiki/实体。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

# ── Grok CLI 风格：空底座 + pack 累加 ────────────────────────────
# ALWAYS_META：任何 non-full 轮次都挂的最小元工具（扩容/时间/澄清）
ALWAYS_META_TOOLS: tuple[str, ...] = (
    "use_tool_pack",
    "current_time",
    "clarify",
)
# 兼容旧名：完整「曾用默认白名单」仅作排序参考与 core pack 内容，不再自动并入每轮
DEFAULT_CHAT_TOOL_WHITELIST: tuple[str, ...] = (
    "file_read",
    "file_write",
    "edit",
    "grep",
    "glob",
    "apply_patch",
    "command",
    "process",
    "python",
    "web_search",
    "search",
    "browser",
    "http",
    "current_time",
    "doc_read",
    "session_search",
    "clarify",
    "result_load",
    "use_tool_pack",
)

# ── 可热插拔能力包 ─────────────────────────────────────────────
TOOL_PACKS: dict[str, tuple[str, ...]] = {
    "core": DEFAULT_CHAT_TOOL_WHITELIST,
    "coding": (
        "file_read",
        "file_write",
        "edit",
        "grep",
        "glob",
        "apply_patch",
        "command",
        "process",
        "python",
        "result_load",
        "shell_session",
    ),
    "web": (
        "web_search",
        "search",
        "browser",
        "http",
        "fetch_webpage",
    ),
    "desktop": (
        "desktop_observe",
        "desktop_screenshot",
        "desktop_click",
        "desktop_type",
        "desktop_scroll",
        "desktop_open_app",
        "desktop_read_file",
        "desktop_write_file",
        "uia_snapshot",
        "vision_analyze",
    ),
    "devices": (
        "list_devices_tool",
        "remote_exec",
        "device_onboard",
        "shell_session",
    ),
    "manage": (
        "manage_cron",
        "manage_channel",
        "manage_mcp",
        "manage_webhook",
        "manage_git",
        "manage_package",
        "manage_profile",
        "manage_knowledge",
        "configure_tevarn",
        "update_config",
        "get_system_status",
        "list_available_models",
        "capability_status",
    ),
    "evolution": (
        "manage_evolution",
        "query_evolution",
        "manage_skill",
    ),
    "office": (
        "generate_ppt",
        "generate_report",
        "doc_read",
        "doc_write",
        "render_chart",
        "image_generate",
        "tts",
        "calendar",
        "calendar_read",
    ),
    "goal": ("manage_goal", "autopilot", "okr_goal"),
    # 编制派活（产品脊柱）：收件箱工单，不是临时子代理闷跑
    # okr_goal：经营目标 O-KR（目标页），管家改目标必用
    "crew": ("crew_steward", "delegate_task", "agent_call", "okr_goal"),
    # cluster 保留 manage_sub_agent 作技能包维护；派活优先走 crew
    "cluster": ("crew_steward", "manage_sub_agent", "delegate_task", "agent_call", "okr_goal"),
    "data": ("sqlite_query", "http"),
    "github": ("github", "manage_git"),
    # MCP 运行时工具：静态仅 manage_mcp；tools_for_packs 会并入 live mcp_*
    "mcp": ("manage_mcp",),
    "integrations": ("manage_mcp",),
}

# 产品 profile → 默认 pack 集合（scene 关键词仅在 dynamic 扩包）
PROFILE_BASE_PACKS: dict[str, tuple[str, ...]] = {
    # Grok-style: coding 不默认挂 web；搜索意图再加 web pack
    "coding": ("coding",),
    "assistant": ("coding",),
    "ops": ("coding", "manage", "devices"),
    "dynamic": (),  # 由场景推断；无关键词时默认 coding（见 infer_scene）
    "core": (),
    "full": ("*",),
}

# assistant 额外单工具（不在 pack 内）
# P0: 普通会话默认单 agent —— 不挂 crew_steward。
# 编制工具仅：steward 会话 (extra_packs=["crew"]) / mode=cluster / 显式 crew pack。
PROFILE_EXTRA_TOOLS: dict[str, tuple[str, ...]] = {
    "coding": ("current_time", "clarify", "use_tool_pack"),
    "assistant": (
        "current_time",
        "clarify",
        "session_search",
        "doc_read",
        "use_tool_pack",
    ),
    "ops": (
        "current_time",
        "clarify",
        "session_search",
        "doc_read",
        "use_tool_pack",
        "get_system_status",
        "capability_status",
    ),
    "dynamic": ("use_tool_pack", "current_time", "clarify"),
    "core": ("use_tool_pack", "current_time", "clarify"),
}

MODE_TOOL_EXTRAS: dict[str, tuple[str, ...]] = {
    "search": ("web_search", "search", "fetch_webpage"),
    "ppt": ("generate_ppt", "doc_read", "doc_write"),
    "report": ("generate_report", "doc_read", "doc_write", "render_chart"),
    "goal": ("manage_goal", "autopilot"),
    # 显式团队/集群模式才挂派工工具
    "cluster": (
        "crew_steward",
        "manage_sub_agent",
        "delegate_task",
        "agent_call",
    ),
    "deepthink": (),
    "default": (),
}

# mode → 默认 pack
MODE_DEFAULT_PACKS: dict[str, tuple[str, ...]] = {
    "search": ("web",),
    "ppt": ("office",),
    "report": ("office",),
    "goal": ("goal", "coding"),
    "cluster": ("cluster",),
    "deepthink": ("coding",),
    "default": (),
}

EVOLUTION_TOOL_NAMES: frozenset[str] = frozenset(
    {"manage_evolution", "query_evolution", "manage_skill"}
)

# 场景关键词（中英）→ pack
_PACK_KEYWORDS: dict[str, tuple[str, ...]] = {
    "desktop": (
        "桌面",
        "点击",
        "鼠标",
        "截图",
        "窗口",
        "uia",
        "gui",
        "desktop",
        "screenshot",
        "click",
        "键鼠",
        "自动化点击",
    ),
    "manage": (
        "cron",
        "定时",
        "webhook",
        "配置 tevarn",
        "configure",
        "频道",
        "channel",
        "系统状态",
        "改配置",
        "settings",
        "模型列表",
    ),
    # MCP 工具包（与 manage 运维包分离，避免只挂 manage_mcp 不挂运行时工具）
    "mcp": (
        "mcp",
        "MCP",
        "mcp 商店",
        "MCP商店",
        "mcp server",
        "model context protocol",
        "外部工具",
        "integrations",
        "doubao-search",
        "askecho",
        "豆包搜索",
        "search infinity",
        "Search Infinity",
    ),
    "evolution": (
        "进化",
        "evolution",
        "自动生成 skill",
        "evo_",
        "自主进化",
        "curator",
        "skill",
        "技能",
        "生成skill",
        "创建skill",
        "注册skill",
        "manage_skill",
        "写一个skill",
        "skill 注册",
    ),
    "office": (
        "ppt",
        "PPT",
        "幻灯片",
        "幻灯",
        "周报",
        "报告",
        "docx",
        "表格图",
        "tts",
        "语音",
        "日历",
        "calendar",
        "生成图片",
        "image",
        "pptx",
        "大纲",
    ),
    "devices": (
        "远程",
        "设备",
        "tevarn-agent",
        "remote",
        "onboard",
        "ssh",
    ),
    "github": ("github", "pr ", " pull request", "ci ", "gh "),
    "data": ("sqlite", "sql 查询", "数据库查询"),
    "web": (
        "搜索",
        "搜一下",
        "最新",
        "联网",
        "http://",
        "https://",
        "网页",
        "browse",
        "search the",
    ),
    "coding": (
        "代码",
        "bug",
        "修复",
        "refactor",
        "函数",
        "文件",
        "实现",
        "pytest",
        "编译",
        "报错",
        "stack",
        "traceback",
        ".py",
        ".ts",
        "git ",
        "commit",
    ),
    "goal": (
        "长期任务",
        "拆解目标",
        "autopilot",
        "里程碑",
        "目标",
        "OKR",
        "okr",
        "O-KR",
        "KR",
        "key result",
        "objective",
        "经营目标",
    ),
    "crew": (
        "派活",
        "派给",
        "招人",
        "入编",
        "员工",
        "编制",
        "班子",
        "工单",
        "巡检",
        "管家",
        "ceo",
        "hire",
        "assign",
        "workforce",
        "团队",
        "分工",
    ),
    "cluster": ("多代理", "子代理", "并行分工", "cluster"),
}

_KNOWLEDGE_HINTS = (
    "是什么",
    "什么是",
    "为什么",
    "知识库",
    "wiki",
    "文档里",
    "根据资料",
    "召回",
    "explain",
    "what is",
    "how does",
)

_MINIMAL_HINTS = (
    "你好", "嗨", "在吗", "hello", "hi ", "thanks", "谢谢", "好的", "ok", "嗯",
    "再见", "拜拜", "bye", "morning", "晚安",
)
_CHAT_QA_HINTS = (
    "是什么", "什么是", "为什么", "怎么理解", "解释一下", "讲讲", "说说",
    "你是谁", "你能做什么", "介绍一下你", "用一句话", "简单说说",
    "explain", "what is", "who are you", "tell me about",
)
_CODING_FORCE_HINTS = (
    "写代码", "改代码", "修 bug", "修bug", "修这个", "修复", "修好",
    "实现", "重构", "debug", "traceback", "typeerror", "type error",
    "报错", "编译", "单元测试", "文件", "目录", "仓库", "repo", "commit", "git ",
    "cargo", "npm ", "python ", "函数", "class ", "patch", "apply_patch",
    "读一下", "打开文件", "编辑", " bug", "bug ",
)
_SEARCH_ONLY_HINTS = (
    "搜一下", "搜索一下", "帮我搜", "联网搜", "查一下新闻", "search for", "google ",
)
THIN_CHAT_TOOLS: frozenset[str] = frozenset({
    "current_time", "clarify", "session_search", "doc_read", "use_tool_pack",
    "list_available_models", "get_system_status", "capability_status",
})
THIN_SEARCH_TOOLS: frozenset[str] = frozenset({
    "web_search", "search", "fetch_webpage", "current_time", "clarify",
    "use_tool_pack", "result_load",
})

_WEB_SURFACE_TOOLS = frozenset({
    "web_search", "search", "browser", "http", "fetch_webpage",
})

# ── MCP 运维意图（配/装/改/密钥）vs 纯搜索：避免「豆包搜索 MCP」被 web 关键词绑架 ──
# 不用 \bmcp\b：中文邻接「…搜索MCP」在 Unicode 下左右皆 \w，边界会失效
_MCP_MARKERS = re.compile(
    r"(?i)mcp|model\s*context\s*protocol|integrations|"
    r"doubao-search|askecho|search[\s-]*infinity|"
    r"豆包搜索|融合信息搜索|tavily|firecrawl"
)
_MCP_OPS_VERBS = re.compile(
    r"(?i)配\s*一?\s*下|配\s*置|配\s*个|安装|挂载|启用|写入|填\s*入|"
    r"装\s*上|接\s*上|manage_mcp|帮我\s*配|"
    r"\binstall\b|\bmount\b|\benable\b|\bconfigure\b"
)
_MCP_OPS_WEAK = re.compile(r"(?i)改\s*一下|修改|更新|设置|\bupdate\b")
_MCP_SECRET_HINTS = re.compile(
    r"(?i)api[_\s-]?key|密钥|secret|access[_\s-]?key|环境变量|\benv\b|token"
)
# 明确「要去搜」：保留 web；与产品名里的「搜索」二字区分
_PURE_SEARCH_VERBS = re.compile(
    r"(?i)搜\s*一下|搜索\s*一下|帮我\s*搜|联网\s*搜|查\s*一下|"
    r"search\s+(for|the|about)|\bgoogle\b|用.{0,16}搜"
)
# 用户把密钥贴在冒号后（不解析/不记录密钥本体，仅作意图信号）
_SECRET_HANDOFF_TAIL = re.compile(
    r"[：:]\s*[A-Za-z0-9_\-]{16,}\s*$"
)


def is_mcp_ops_intent(user_input: str) -> bool:
    """本轮是否在「配置/安装/写密钥」MCP，而非调用搜索。"""
    text = (user_input or "").strip()
    if not text or not _MCP_MARKERS.search(text):
        return False
    if _MCP_SECRET_HINTS.search(text) or _SECRET_HANDOFF_TAIL.search(text):
        return True
    for m in _MCP_MARKERS.finditer(text):
        lo = max(0, m.start() - 24)
        hi = min(len(text), m.end() + 24)
        window = text[lo:hi]
        if _MCP_OPS_VERBS.search(window) or re.search(r"配\s*一?\s*[下置个]", window):
            return True
        if _MCP_OPS_WEAK.search(window) and re.search(
            r"(?i)(env|密钥|api|key|装|配|server)", window
        ):
            return True
    return False


def should_demote_web_for_mcp_ops(user_input: str) -> bool:
    """运维 MCP 时，若仅因产品名含「搜索」挂上 web，则降级。"""
    if not is_mcp_ops_intent(user_input):
        return False
    if _PURE_SEARCH_VERBS.search(user_input or ""):
        return False
    return True


def is_mcp_secret_handoff(user_input: str) -> bool:
    """用户本轮是否像在交付 MCP API Key（不解析密钥本体）。"""
    text = (user_input or "").strip()
    if not text or not is_mcp_ops_intent(text):
        return False
    return bool(_SECRET_HANDOFF_TAIL.search(text) or _MCP_SECRET_HINTS.search(text))


def mcp_ops_capability_line(*, secret_handoff: bool = False) -> str:
    """compact brief 用的短运维纪律（控制长度）。"""
    base = (
        "MCP ops: manage_mcp list/update env/reload; mcp_* = live calls only. "
        "No web_search for how-to when Key is already given."
    )
    if secret_handoff:
        return (
            base
            + " Secret: manage_mcp update name=<server> env={API_KEY:…} → reload → mcp_* verify."
        )
    return base


@dataclass
class ScenePlan:
    """单轮场景计划。"""

    packs: list[str] = field(default_factory=list)
    injection_tier: str = "standard"  # minimal | standard | rich
    reasons: list[str] = field(default_factory=list)
    profile: str = "dynamic"

    def summary(self) -> str:
        return (
            f"packs={self.packs or ['meta']} tier={self.injection_tier} "
            f"({', '.join(self.reasons[:4]) or 'default'})"
        )


def _norm_list(raw: object | None) -> list[str] | None:
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple, set)):
        return None
    return [str(x).strip() for x in raw if str(x).strip()]


def wants_full_tools(
    raw_tools: object | None,
    *,
    profile: str = "core",
) -> bool:
    if (profile or "core").strip().lower() == "full":
        return True
    names = _norm_list(raw_tools)
    if not names:
        return False
    lowered = {n.lower() for n in names}
    return "*" in lowered or "all" in lowered or "full" in lowered


def list_pack_catalog() -> dict[str, list[str]]:
    """供 use_tool_pack action=list。"""
    cat = {k: list(v) for k, v in TOOL_PACKS.items() if k != "core"}
    # mcp pack 展示实时挂载工具名
    live = live_mcp_tool_names()
    if live:
        cat["mcp"] = list(dict.fromkeys([*(cat.get("mcp") or []), *live[:24]]))
        cat["integrations"] = list(cat["mcp"])
    return cat


def live_mcp_tool_names() -> list[str]:
    """当前 ToolRegistry 中已注册且 enabled 的 MCP 工具名。"""
    try:
        from backend.tools.base import ToolSource
        from backend.tools.registry import ToolRegistry

        return sorted(
            t.name
            for t in ToolRegistry.get_all(source=ToolSource.MCP)
            if getattr(t, "enabled", True)
        )
    except Exception:
        return []


def tools_for_packs(packs: Iterable[str]) -> list[str]:
    """合并 pack → 去重工具名（Grok：空底座 + pack 累加）。

    仅 pack ``mcp`` / ``integrations`` 并入 live ``mcp_*``；
    ``manage`` 只保留静态 manage_mcp。
    ``core`` pack 显式请求时才并入 DEFAULT 全量白名单。
    """
    base: set[str] = set(ALWAYS_META_TOOLS)
    need_mcp_live = False
    pack_list = [((p or "").strip().lower()) for p in packs]
    for key in pack_list:
        if key in {"*", "all", "full"}:
            return []  # 信号：调用方应视作 full
        if key == "core":
            base.update(DEFAULT_CHAT_TOOL_WHITELIST)
            continue
        if key in TOOL_PACKS:
            base.update(TOOL_PACKS[key])
        if key in {"mcp", "integrations"}:
            need_mcp_live = True
    if need_mcp_live:
        base.update(live_mcp_tool_names())
        base.add("manage_mcp")
    return _order_tools(base)


def _order_tools(names: set[str]) -> list[str]:
    preferred = list(DEFAULT_CHAT_TOOL_WHITELIST)
    # pack 内相对顺序
    pack_order: list[str] = []
    for pack_tools in TOOL_PACKS.values():
        for t in pack_tools:
            if t not in pack_order:
                pack_order.append(t)
    ordered: list[str] = []
    seen: set[str] = set()
    for n in preferred + pack_order + sorted(names):
        if n in names and n not in seen:
            ordered.append(n)
            seen.add(n)
    return ordered



def is_thin_chat_intent(user_input: str) -> bool:
    text = (user_input or "").strip()
    if not text:
        return True
    if len(text) > 280:
        return False
    low = text.lower()
    if any(h in low or h in text for h in _CODING_FORCE_HINTS):
        return False
    if _PURE_SEARCH_VERBS.search(text) or any(h in low or h in text for h in _SEARCH_ONLY_HINTS):
        return False
    if is_mcp_ops_intent(text):
        return False
    if any(h in low or h in text for h in _MINIMAL_HINTS):
        return True
    if len(text) < 8:
        return True
    if len(text) <= 80 and any(h in low or h in text for h in _CHAT_QA_HINTS):
        if not re.search(
            r"(?i)(搜\s*一下|search\s+for|打开文件|改代码|写代码|运行命令|执行脚本|安装\s|配置\s|命令行|终端里)",
            text,
        ):
            return True
    return False


def is_search_only_intent(user_input: str) -> bool:
    text = (user_input or "").strip()
    if not text or is_mcp_ops_intent(text):
        return False
    if any(h in text.lower() or h in text for h in _CODING_FORCE_HINTS):
        return False
    if _PURE_SEARCH_VERBS.search(text) or any(
        h in text.lower() or h in text for h in _SEARCH_ONLY_HINTS
    ):
        return True
    return False


def scene_max_iterations(scene_kind: str, *, default: int = 40) -> int:
    try:
        from backend.core.config import settings as _st
        chat_cap = int(getattr(_st, "agent_chat_max_iterations", 6) or 6)
        search_cap = int(getattr(_st, "agent_search_max_iterations", 15) or 15)
        coding_cap = int(getattr(_st, "agent_coding_max_iterations", 40) or 40)
    except Exception:
        chat_cap, search_cap, coding_cap = 6, 15, 40
    kind = (scene_kind or "").strip().lower()
    if kind in {"thin", "chat", "minimal", "core"}:
        return max(2, chat_cap)
    if kind in {"search", "web"}:
        return max(4, search_cap)
    if kind in {"coding", "goal", "ops", "full"}:
        return max(8, coding_cap)
    return max(4, default)


def infer_scene(
    user_input: str,
    *,
    mode: str = "default",
    profile: str = "dynamic",
) -> ScenePlan:
    """启发式场景判定（无额外 LLM 调用）。"""
    text = (user_input or "").strip()
    low = text.lower()
    mode_key = (mode or "default").strip().lower()
    prof = (profile or "dynamic").strip().lower()

    packs: list[str] = []
    reasons: list[str] = []

    # ChatMode 强制 pack
    for p in MODE_DEFAULT_PACKS.get(mode_key, ()):
        if p not in packs:
            packs.append(p)
            reasons.append(f"mode:{mode_key}")

    if prof == "full":
        return ScenePlan(packs=["*"], injection_tier="rich", reasons=["profile:full"], profile=prof)

    _auto_thin = True
    try:
        from backend.core.config import settings as _st
        _auto_thin = bool(getattr(_st, "agent_auto_thin_chat", True))
    except Exception:
        pass
    _thin = bool(
        _auto_thin
        and mode_key in {"default", "chat", ""}
        and is_thin_chat_intent(text)
        and not packs
    )

    if prof in {"core", "coding", "assistant", "ops"}:
        base = list(PROFILE_BASE_PACKS.get(prof, ()))
        for p in base:
            if p not in packs and p != "*":
                packs.append(p)
        tier = "standard"
        if _thin or (not text or len(text) < 8 or any(h in low or h in text for h in _MINIMAL_HINTS)):
            tier = "minimal"
            reasons.append("thin_injection")
        if any(h in low or h in text for h in _KNOWLEDGE_HINTS) or len(text) > 400:
            if tier != "minimal":
                tier = "rich"
        return ScenePlan(
            packs=packs,
            injection_tier=tier,
            reasons=reasons or [f"profile:{prof}"],
            profile=prof,
        )

    # dynamic：薄档优先
    if _thin:
        return ScenePlan(
            packs=[],
            injection_tier="minimal",
            reasons=reasons + ["auto_thin_chat"],
            profile="core",
        )

    # dynamic：关键词扩包
    for pack, kws in _PACK_KEYWORDS.items():
        for kw in kws:
            if kw.lower() in low or kw in text:
                if pack not in packs:
                    packs.append(pack)
                    reasons.append(f"kw:{kw[:16]}")
                break

    # 搜索-only：只挂 web
    if is_search_only_intent(text) and mode_key in {"default", "search", "chat", ""}:
        if "web" not in packs:
            packs.append("web")
            reasons.append("search_only:web")
        packs = [p for p in packs if p not in {"coding", "desktop", "cluster", "goal"}]
        reasons.append("search_only:strip_coding")

    # F9: 「添加 mcp github …」不要挂 github/git pack
    if re.search(r"(?i)(添加|挂载|安装|add|install).{0,16}(mcp|MCP)", text):
        if "github" in packs:
            packs = [p for p in packs if p != "github"]
            reasons.append("mcp_strip_github")

    # MCP 运维纠偏：强制 mcp（manage_mcp 已在 mcp pack），避免产品名「xx搜索」误挂 web
    if is_mcp_ops_intent(text):
        if "mcp" not in packs:
            packs.append("mcp")
            reasons.append("mcp_ops:force_mcp")
        if should_demote_web_for_mcp_ops(text) and "web" in packs:
            packs = [p for p in packs if p != "web"]
            reasons.append("mcp_ops:demote_web")

    # 注入档位
    tier = "standard"
    if not text or len(text) < 8 or any(h in low or h in text for h in _MINIMAL_HINTS):
        if not packs:
            tier = "minimal"
            reasons.append("short/greeting")
    if any(h in low or h in text for h in _KNOWLEDGE_HINTS) or len(text) > 400:
        tier = "rich"
        reasons.append("knowledge_or_long")
    if "coding" in packs or mode_key in {"goal", "cluster"}:
        if tier == "minimal":
            tier = "standard"
    if mode_key in {"ppt", "report", "search"}:
        tier = "standard" if tier == "minimal" else tier

    # 编码任务默认带 coding pack（已有读写工具，pack 补 shell_session）
    if any(x in low for x in ("fix", "bug", "实现", "refactor", ".py", "traceback")):
        if "coding" not in packs:
            packs.append("coding")
            reasons.append("coding_signal")
        # F5: 纯本地修 bug 不挂 web/browser
        if "web" in packs and not is_search_only_intent(text) and not re.search(
            r"(?i)(搜|search|联网|网页|浏览器|http)", text
        ):
            packs = [p for p in packs if p != "web"]
            reasons.append("coding_no_web")

    # 无 pack 的 dynamic 默认：coding（不挂 web），对齐工程主脑
    if not packs:
        packs = ["coding"]
        reasons.append("dynamic:default_coding")

    return ScenePlan(packs=packs, injection_tier=tier, reasons=reasons or ["dynamic:default"], profile=prof)


def resolve_enabled_tool_names(
    *,
    mode: str = "default",
    raw_tools: object | None = None,
    raw_skills: object | None = None,
    profile: str = "dynamic",
    extra: Iterable[str] | None = None,
    user_input: str = "",
    extra_packs: Iterable[str] | None = None,
    scene: ScenePlan | None = None,
) -> tuple[list[str] | None, ScenePlan]:
    """解析本轮工具名 + 场景计划。

    Returns:
        (None, plan) = 全量不过滤
        (list, plan) = 白名单
    """
    prof = (profile or "dynamic").strip().lower()
    plan = scene or infer_scene(user_input, mode=mode, profile=prof)

    # 专业模式已绑定项目目录 → 默认带 coding pack，减少 use_tool_pack 空转
    try:
        from backend.workspace.service import get_any_root

        bound = get_any_root() is not None
        if not bound:
            try:
                from backend.core.config import settings as _s
                fb = str(getattr(_s, "file_browser_root", "") or "").strip()
                bound = bool(fb) and fb not in (".", "workspace", "")
            except Exception:
                bound = False
        _thin_skip = "auto_thin_chat" in (plan.reasons or [])
        if (
            bound
            and not _thin_skip
            and "coding" not in plan.packs
            and "*" not in plan.packs
        ):
            plan.packs = list(plan.packs) + ["coding"]
            plan.reasons = list(plan.reasons) + ["workspace_bound"]
    except Exception:
        pass

    # P0-3：禁止「有 live MCP 就无脑挂全量 mcp_*」
    live_mcp = live_mcp_tool_names()
    text = (user_input or "").strip()
    _mcp_ops = bool(text and is_mcp_ops_intent(text))
    _product = re.search(
        r"(?i)(mcp|豆包|doubao|askecho|tavily|firecrawl|mcp_\w+)", text or ""
    )
    _mcp_use = bool(
        text
        and not _mcp_ops
        and _product
        and (
            _PURE_SEARCH_VERBS.search(text)
            or re.search(
                r"(?i)(用|调用|试|跑|执行|搜|查).{0,16}(mcp|豆包|doubao|tavily|firecrawl)",
                text,
            )
            or re.search(
                r"(?i)(mcp|豆包|doubao|tavily|firecrawl).{0,16}(搜|查|用|试|一下)",
                text,
            )
        )
    )
    if prof not in {"core"} and "*" not in plan.packs:
        if _mcp_ops:
            if "mcp" not in plan.packs:
                plan.packs = list(plan.packs) + ["mcp"]
                plan.reasons = list(plan.reasons) + ["mcp_ops:ensure_pack"]
        elif live_mcp and ("mcp" in plan.packs or "integrations" in plan.packs):
            plan.reasons = list(plan.reasons) + [f"live_mcp:{len(live_mcp)}"]
        elif live_mcp and _mcp_use:
            if "mcp" not in plan.packs:
                plan.packs = list(plan.packs) + ["mcp"]
            plan.reasons = list(plan.reasons) + [f"mcp_use:{len(live_mcp)}"]

    if wants_full_tools(raw_tools, profile=prof) or "*" in plan.packs or "full" in plan.packs:
        plan.profile = "full"
        plan.injection_tier = "rich"
        return None, plan

    names = _norm_list(raw_tools)
    skills = _norm_list(raw_skills)

    if names is not None and len(names) > 0:
        base = set(names)
        if live_mcp and (
            "mcp" in plan.packs
            or "integrations" in plan.packs
            or any(str(n).startswith("mcp_") for n in base)
        ):
            base.update(live_mcp)
        plan.reasons = list(plan.reasons) + ["explicit_tools"]
    else:
        packs = list(plan.packs)
        for p in PROFILE_BASE_PACKS.get(prof, ()):
            if p and p not in packs and p != "*":
                packs.append(p)
        if extra_packs:
            for p in extra_packs:
                if p and p not in packs:
                    packs.append(str(p).strip().lower())
        merged = tools_for_packs(packs)
        base = set(merged)
        base.update(PROFILE_EXTRA_TOOLS.get(prof, ()))
        if skills is not None and len(skills) > 0:
            if not (len(skills) == 1 and skills[0].lower() in {"*", "all"}):
                base.update(skills)
        plan.packs = list(dict.fromkeys(packs))

    mode_key = (mode or "default").strip().lower()
    base.update(MODE_TOOL_EXTRAS.get(mode_key, ()))
    if extra:
        base.update(str(x).strip() for x in extra if str(x).strip())
    base.add("use_tool_pack")

    if _mcp_ops and prof not in {"full"}:
        ops_allow = {
            "manage_mcp",
            "use_tool_pack",
            "clarify",
            "current_time",
            "get_system_status",
            "list_available_models",
            "update_config",
            "configure_tevarn",
            "capability_status",
            "file_read",
        }
        if is_mcp_secret_handoff(text):
            base = {n for n in base if n in ops_allow or str(n).startswith("mcp_")}
            base.update(live_mcp)
            plan.reasons = list(plan.reasons) + ["mcp_ops:thin+verify"]
        else:
            base = {n for n in base if n in ops_allow}
            plan.reasons = list(plan.reasons) + ["mcp_ops:thin_surface"]
        base.add("manage_mcp")
        base.add("use_tool_pack")

    # S1: 自动薄档
    if (
        "auto_thin_chat" in (plan.reasons or [])
        or (
            plan.injection_tier == "minimal"
            and not plan.packs
            and not _mcp_ops
            and is_thin_chat_intent(text)
        )
    ):
        base = {n for n in base if n in THIN_CHAT_TOOLS} or set(THIN_CHAT_TOOLS)
        plan.reasons = list(plan.reasons) + ["thin_chat_surface"]
    elif (
        is_search_only_intent(text)
        and not _mcp_ops
        and prof not in {"full", "coding", "ops"}
    ):
        keep = set(THIN_SEARCH_TOOLS)
        for n in list(base):
            if str(n).startswith("mcp_") and any(
                x in str(n) for x in ("search", "fetch", "scrape", "web")
            ):
                keep.add(n)
        base = {n for n in base if n in keep}
        if "web_search" not in base and "search" not in base:
            base.add("web_search")
        plan.reasons = list(plan.reasons) + ["search_thin_surface"]

    # F5: 纯 coding 去掉 web 表面（DEFAULT 底座含 browser/search）
    if (
        "coding_no_web" in (plan.reasons or [])
        or (
            "coding" in (plan.packs or [])
            and "web" not in (plan.packs or [])
            and not is_search_only_intent(text)
            and not _mcp_ops
        )
    ):
        before = set(base)
        base = {n for n in base if n not in _WEB_SURFACE_TOOLS}
        if before != base:
            plan.reasons = list(plan.reasons) + ["coding_no_web_surface"]

    ordered = _order_tools(base)
    plan.packs = list(dict.fromkeys(plan.packs))
    return ordered, plan


def merge_tools_with_packs(
    current: list[str] | None,
    packs: Iterable[str],
) -> list[str] | None:
    """中途扩容：None(全量) 保持 None；否则并入 pack 增量（不重灌整份 core）。"""
    pack_list = [str(p).strip().lower() for p in packs if str(p).strip()]
    if any(p in {"*", "all", "full"} for p in pack_list):
        return None
    if current is None:
        return None
    delta: set[str] = set()
    for p in pack_list:
        key = (p or "").strip().lower()
        if key in TOOL_PACKS:
            delta.update(TOOL_PACKS[key])
        if key in {"mcp", "integrations"}:
            delta.update(live_mcp_tool_names())
            delta.add("manage_mcp")
    return _order_tools(set(current) | delta)


def compact_capability_brief(
    tool_names: list[str] | None,
    *,
    scene: ScenePlan | None = None,
    user_input: str = "",
) -> str:
    """短 brief + 可选场景说明（compact 契约：总长 <600 字符）。"""
    n = len(tool_names) if tool_names is not None else "all"
    name_set = set(tool_names or []) if tool_names is not None else set()
    packs = list(scene.packs or []) if scene else []
    reasons = list(scene.reasons or []) if scene else []
    mcp_surface = (
        "manage_mcp" in name_set
        or "mcp" in packs
        or "integrations" in packs
        or any(str(r).startswith("mcp_ops") for r in reasons)
        or any(str(t).startswith("mcp_") for t in name_set)
    )
    secret_handoff = bool(user_input and is_mcp_secret_handoff(user_input))

    # Surface classification (empty-base + packs)
    coding_tools = {
        "file_read", "file_write", "edit", "apply_patch", "command", "python",
        "grep", "glob", "process", "shell_session",
    }
    search_tools = {"web_search", "search", "fetch_webpage", "browser", "http"}
    has_coding = bool(name_set & coding_tools) if tool_names is not None else True
    has_search = bool(name_set & search_tools) if tool_names is not None else False
    chat_only = (
        tool_names is not None
        and not has_coding
        and not has_search
        and not mcp_surface
    )

    lines = [
        f"Tool surface this turn: {n} tool(s). "
        "Use a listed tool when you need facts/files/shell/live data; "
        "never claim a capability is missing before trying the matching tool.",
    ]
    if mcp_surface:
        lines.append(mcp_ops_capability_line(secret_handoff=secret_handoff))
    elif chat_only:
        lines.append(
            "Chat surface: answer directly in text. "
            "Need files/shell/search? use_tool_pack(action='enable', packs=[...])."
        )
    elif has_search and not has_coding:
        lines.append(
            "Search surface: web_search/search then synthesize (budget a few queries). "
            "Do not open coding tools unless the user asked to change code."
        )
    elif has_coding:
        cmd_listed = "command" in name_set or "python" in name_set
        lines.append(
            "Coding surface: read → edit/apply_patch/file_write"
            + (" → command/python verify" if cmd_listed else "")
            + ". Prefer one coherent multi-hunk patch over many tiny edits. "
            "Batch independent reads. After fix/build, verify before claiming done."
        )
        if cmd_listed:
            lines.append(
                "cwd: session workspace_root (else TEVARN_TASK_ROOT); "
                "prefer file_write/edit over heredoc."
            )
    if scene and scene.profile != "full":
        lines.append(
            f"Scene: {scene.summary()[:72]}. "
            "Missing pack? use_tool_pack enable (or list)."
        )
    if not chat_only:
        lines.append(
            "Skill: follow a matching installed skill before improvising."
        )
    if chat_only:
        lines.append("Prefer a clear final answer; skip tools when unnecessary.")
    else:
        lines.append("Prefer tools over speculation; finish the task.")
    # 仅当本轮工具面真有 crew_steward 时再注入编制纪律（避免普通会话被诱导派工）
    if "crew_steward" in name_set:
        lines.append(
            "Workforce: multi-step team projects → crew_steward list/hire/assign (inbox). "
            "Simple Q&A / weather / trending / one-shot search / short facts: "
            "answer yourself with tools in this session — do NOT hire/assign. "
            "Do NOT spawn temp subagents for team work."
        )
    if tool_names is not None and "okr_goal" in name_set:
        lines.append(
            "Business goals (目标页 O-KR): use okr_goal list/update/create. "
            "Do NOT use manage_goal (session todos) or grep the repo for goals."
        )
    if tool_names is None:
        # full-tools 模式：仍提示 okr，但不默认推派工
        lines.append(
            "Business goals (目标页 O-KR): use okr_goal list/update/create when relevant."
        )
    text = "\n".join(lines)
    # compact 契约：硬截断，避免 prompt 膨胀（测试与 runtime 一致）
    if len(text) >= 600:
        text = text[:596].rstrip() + "..."
    return text


# pack → skill 标签/关键词加权（与 prompt-skill 对齐）
SCENE_SKILL_HINTS: dict[str, tuple[str, ...]] = {
    "coding": ("code", "python", "git", "debug", "refactor", "编程", "代码", "test", "lint"),
    "web": ("search", "browser", "web", "http", "crawl", "搜索", "网页"),
    "desktop": ("desktop", "gui", "uia", "click", "screenshot", "桌面", "键鼠"),
    "manage": ("cron", "config", "ops", "channel", "webhook", "运维", "配置"),
    "mcp": ("mcp", "MCP", "integrations", "外部工具"),
    "evolution": ("evolution", "skill", "进化", "curator", "tee"),
    "office": ("ppt", "docx", "report", "office", "chart", "tts", "日历", "幻灯"),
    "devices": ("device", "remote", "ssh", "agent", "设备", "远程"),
    "github": ("github", "pr", "ci", "gh"),
    "goal": ("goal", "plan", "autopilot", "目标", "里程碑"),
    "crew": ("crew", "hire", "assign", "员工", "派活", "编制", "管家", "工单"),
    "cluster": ("cluster", "delegate", "subagent", "多代理", "子代理"),
    "data": ("sql", "sqlite", "database", "数据"),
}


def injection_knobs(tier: str) -> dict[str, object]:
    """注入档位 → loop / prompt-skill / RAG 开关与阈值。"""
    t = (tier or "standard").strip().lower()
    if t == "minimal":
        return {
            "rag": False,
            "wiki": False,
            "entity": False,
            "rag_top_k": 0,
            "wiki_limit": 0,
            "entity_limit": 0,
            "rag_min_score": 0.85,
            "prompt_skills": False,
            "skill_mode": "summary",
            "skill_threshold": 9.0,
            "skill_max_full": 0,
            "wiki_min_score": 0.35,
        }
    if t == "rich":
        return {
            "rag": True,
            "wiki": True,
            "entity": True,
            "rag_top_k": 5,
            "wiki_limit": 8,
            "entity_limit": 5,
            "rag_min_score": 0.42,
            "prompt_skills": True,
            "skill_mode": "auto",
            "skill_threshold": 0.75,
            "skill_max_full": 2,
            "wiki_min_score": 0.12,
        }
    # standard：宁缺毋滥
    return {
        "rag": True,
        "wiki": True,
        "entity": True,
        "rag_top_k": 3,
        "wiki_limit": 4,
        "entity_limit": 3,
        "rag_min_score": 0.58,
        "prompt_skills": True,
        "skill_mode": "auto",
        "skill_threshold": 0.95,
        "skill_max_full": 1,
        "wiki_min_score": 0.2,
    }

