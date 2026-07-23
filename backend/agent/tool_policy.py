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

from dataclasses import dataclass, field
from typing import Iterable

# ── 核心白名单（任何 non-full 模式的底座）────────────────────────
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
    "list_devices_tool",
    "remote_exec",
    "session_search",
    "clarify",
    "use_tool_pack",  # meta：动态扩容
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
        "configure_takton",
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
    "goal": ("manage_goal", "autopilot"),
    "cluster": ("manage_sub_agent", "delegate_task", "agent_call"),
    "data": ("sqlite_query", "http"),
    "github": ("github", "manage_git"),
}

# 产品 profile → 默认 pack 集合（scene 关键词仅在 dynamic 扩包）
PROFILE_BASE_PACKS: dict[str, tuple[str, ...]] = {
    "coding": ("coding", "web"),
    "assistant": ("coding", "web"),
    "ops": ("coding", "web", "manage", "devices"),
    "dynamic": (),  # 由场景推断
    "core": (),
    "full": ("*",),
}

# assistant 额外单工具（不在 pack 内）
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
    "dynamic": ("use_tool_pack",),
    "core": ("use_tool_pack",),
}

MODE_TOOL_EXTRAS: dict[str, tuple[str, ...]] = {
    "search": ("web_search", "search", "fetch_webpage"),
    "ppt": ("generate_ppt", "doc_read", "doc_write"),
    "report": ("generate_report", "doc_read", "doc_write", "render_chart"),
    "goal": ("manage_goal", "autopilot"),
    "cluster": ("manage_sub_agent", "delegate_task", "agent_call"),
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
        "mcp",
        "配置 takton",
        "configure",
        "频道",
        "channel",
        "系统状态",
        "改配置",
        "settings",
        "模型列表",
    ),
    "evolution": (
        "进化",
        "evolution",
        "自动生成 skill",
        "evo_",
        "自主进化",
        "curator",
    ),
    "office": (
        "ppt",
        "幻灯片",
        "报告",
        "docx",
        "表格图",
        "tts",
        "语音",
        "日历",
        "calendar",
        "生成图片",
        "image",
    ),
    "devices": (
        "远程",
        "设备",
        "takton-agent",
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
    "goal": ("长期任务", "拆解目标", "autopilot", "里程碑"),
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
    "你好",
    "嗨",
    "在吗",
    "hello",
    "hi ",
    "thanks",
    "谢谢",
    "好的",
    "ok",
    "嗯",
)


@dataclass
class ScenePlan:
    """单轮场景计划。"""

    packs: list[str] = field(default_factory=list)
    injection_tier: str = "standard"  # minimal | standard | rich
    reasons: list[str] = field(default_factory=list)
    profile: str = "dynamic"

    def summary(self) -> str:
        return (
            f"packs={self.packs or ['core']} tier={self.injection_tier} "
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
    return {k: list(v) for k, v in TOOL_PACKS.items() if k != "core"}


def tools_for_packs(packs: Iterable[str]) -> list[str]:
    """合并 pack → 去重工具名（core 顺序优先）。"""
    base: set[str] = set(DEFAULT_CHAT_TOOL_WHITELIST)
    for p in packs:
        key = (p or "").strip().lower()
        if key in {"*", "all", "full"}:
            return []  # 信号：调用方应视作 full
        if key == "core":
            continue
        if key in TOOL_PACKS:
            base.update(TOOL_PACKS[key])
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

    if prof in {"core", "coding", "assistant", "ops"}:
        # 固定 profile：可叠 ChatMode packs，不做关键词扩包
        base = list(PROFILE_BASE_PACKS.get(prof, ()))
        for p in base:
            if p not in packs and p != "*":
                packs.append(p)
        tier = "standard"
        if not text or len(text) < 8 or any(h in low or h in text for h in _MINIMAL_HINTS):
            if prof in {"coding", "core", "assistant"} and not packs:
                tier = "minimal"
        if any(h in low or h in text for h in _KNOWLEDGE_HINTS) or len(text) > 400:
            tier = "rich"
        return ScenePlan(
            packs=packs,
            injection_tier=tier,
            reasons=reasons or [f"profile:{prof}"],
            profile=prof,
        )

    # dynamic：关键词扩包
    for pack, kws in _PACK_KEYWORDS.items():
        for kw in kws:
            if kw.lower() in low or kw in text:
                if pack not in packs:
                    packs.append(pack)
                    reasons.append(f"kw:{kw[:16]}")
                break

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

    if wants_full_tools(raw_tools, profile=prof) or "*" in plan.packs or "full" in plan.packs:
        plan.profile = "full"
        plan.injection_tier = "rich"
        return None, plan

    names = _norm_list(raw_tools)
    skills = _norm_list(raw_skills)

    # 显式 tools 名单（非 *）
    if names is not None and len(names) > 0:
        base = set(names)
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
        # 空 packs → 仅 core 白名单
        merged = tools_for_packs(packs)
        base = set(merged)
        base.update(PROFILE_EXTRA_TOOLS.get(prof, ()))
        if skills is not None and len(skills) > 0:
            if not (len(skills) == 1 and skills[0].lower() in {"*", "all"}):
                base.update(skills)

    mode_key = (mode or "default").strip().lower()
    base.update(MODE_TOOL_EXTRAS.get(mode_key, ()))
    if extra:
        base.update(str(x).strip() for x in extra if str(x).strip())
    # meta 始终在
    base.add("use_tool_pack")

    ordered = _order_tools(base)
    plan.packs = list(dict.fromkeys(plan.packs))
    return ordered, plan


def merge_tools_with_packs(
    current: list[str] | None,
    packs: Iterable[str],
) -> list[str] | None:
    """中途扩容：None(全量) 保持 None；否则并入 pack。"""
    pack_list = [str(p).strip().lower() for p in packs if str(p).strip()]
    if any(p in {"*", "all", "full"} for p in pack_list):
        return None
    added = tools_for_packs(pack_list)
    if current is None:
        return None
    return _order_tools(set(current) | set(added))


def compact_capability_brief(
    tool_names: list[str] | None,
    *,
    scene: ScenePlan | None = None,
) -> str:
    """短 brief + 可选场景说明。"""
    n = len(tool_names) if tool_names is not None else "all"
    lines = [
        "Tool discipline: use the provided tools for facts, files, shell, and live data. "
        f"Available tool count this turn: {n}. "
        "Do not claim a capability is missing without trying the matching tool first.",
    ]
    if scene and scene.profile != "full":
        lines.append(
            f"Profile/scene: {scene.summary()}. "
            "If you need desktop/manage/evolution/office tools not listed, "
            "call use_tool_pack(action='enable', packs=[...]) first "
            "(action='list' to see packs)."
        )
    lines.append(
        "Skill discipline: if an installed skill index matches the task, "
        "you MUST follow/load that skill guidance before improvising workflows."
    )
    lines.append("Prefer tools over speculation; finish the user task.")
    return "\n".join(lines)


# pack → skill 标签/关键词加权（与 prompt-skill 对齐）
SCENE_SKILL_HINTS: dict[str, tuple[str, ...]] = {
    "coding": ("code", "python", "git", "debug", "refactor", "编程", "代码", "test", "lint"),
    "web": ("search", "browser", "web", "http", "crawl", "搜索", "网页"),
    "desktop": ("desktop", "gui", "uia", "click", "screenshot", "桌面", "键鼠"),
    "manage": ("cron", "config", "ops", "channel", "mcp", "webhook", "运维", "配置"),
    "evolution": ("evolution", "skill", "进化", "curator", "tee"),
    "office": ("ppt", "docx", "report", "office", "chart", "tts", "日历", "幻灯"),
    "devices": ("device", "remote", "ssh", "agent", "设备", "远程"),
    "github": ("github", "pr", "ci", "gh"),
    "goal": ("goal", "plan", "autopilot", "目标", "里程碑"),
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

