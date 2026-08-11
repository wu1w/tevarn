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
    "session_search",
    "clarify",
    "result_load",  # 外置大结果回读（与 result_spill 配对）
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
        "manage_skill",  # 自定义可执行 skill 与 MCP 同属管理面
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
    "goal": ("manage_goal", "autopilot", "okr_goal"),
    # 编制派活（产品脊柱）：收件箱工单，不是临时子代理闷跑
    # okr_goal：经营目标 O-KR（目标页），管家改目标必用
    "crew": ("crew_steward", "delegate_task", "agent_call", "okr_goal"),
    # cluster 保留 manage_sub_agent 作技能包维护；派活优先走 crew
    "cluster": ("crew_steward", "manage_sub_agent", "delegate_task", "agent_call", "okr_goal"),
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
    "dynamic": ("use_tool_pack",),
    "core": ("use_tool_pack",),
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
        "mcp",
        "MCP",
        "mcp 商店",
        "MCP商店",
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


def live_custom_tool_names() -> list[str]:
    """用户可执行 skill / DB tool（DYNAMIC|DB），不含 pack 内置与 MCP。"""
    try:
        from backend.tools.base import ToolSource
        from backend.tools.registry import ToolRegistry

        out: list[str] = []
        for src in (ToolSource.DYNAMIC, ToolSource.DB):
            for t in ToolRegistry.get_all(source=src):
                if not getattr(t, "enabled", True):
                    continue
                n = str(getattr(t, "name", "") or "").strip()
                if n:
                    out.append(n)
        return sorted(set(out))
    except Exception:
        return []


def select_live_custom_tools(
    user_input: str,
    live_names: list[str] | None = None,
    *,
    matching_only: bool = True,
) -> list[str]:
    """Matching-only：用户点名自定义 skill/tool 名时才挂上（避免全量 dump）。"""
    live = list(live_names if live_names is not None else live_custom_tool_names())
    if not live:
        return []
    if not matching_only:
        return live
    raw = user_input or ""
    if not raw.strip():
        return []
    text_l = raw.lower()
    # 配置 manage_skill 轮不挂全部自定义 schema
    try:
        from backend.agent.simple_intent import is_mcp_configure_intent

        # 创建/管理 skill 时同样不 dump 全部可执行 skill
        if is_mcp_configure_intent(raw) and "skill" in text_l:
            return []
    except Exception:
        pass
    out: list[str] = []
    for name in live:
        nl = name.lower()
        # 全名或去分隔符后的片段出现在用户话里
        if nl in text_l or name in raw:
            out.append(name)
            continue
        compact = nl.replace("-", "").replace("_", "")
        if len(compact) >= 3 and compact in text_l.replace("-", "").replace("_", ""):
            out.append(name)
            continue
        # 多段名：任一段 token（≥3）命中
        parts = [p for p in re.split(r"[-_\s]+", nl) if len(p) >= 3]
        if any(p in text_l for p in parts):
            out.append(name)
    return out


def is_mcp_ops_intent(user_input: str) -> bool:
    """本轮是否在配置/安装/写密钥 MCP（非单纯调用搜索）。"""
    try:
        from backend.agent.simple_intent import is_mcp_configure_intent

        return is_mcp_configure_intent(user_input)
    except Exception:
        pass
    import re

    text = (user_input or "").strip()
    if not text:
        return False
    # 收紧：多字 ops，避免单字误触发
    if not re.search(r"(?i)\bmcp\b|manage_mcp|豆包|tavily|firecrawl|api\s*key|密钥", text):
        return False
    return bool(
        re.search(
            r"(?i)配置|安装|reload|热同步|api\s*key|密钥|env\s*[={]|配一下|配下|写密钥",
            text,
        )
    )


def tools_for_packs(
    packs: Iterable[str],
    *,
    mcp_names: Iterable[str] | None = None,
) -> list[str]:
    """合并 pack → 去重工具名（core 顺序优先）。

    pack ``mcp`` / ``integrations`` 并入 **匹配后的** live ``mcp_*``（Grok 风格）；
    ``manage`` 只给 manage_mcp 等管理工具，避免 80+ schema 默认塞满。
    """
    base: set[str] = set(DEFAULT_CHAT_TOOL_WHITELIST)
    need_mcp_live = False
    for p in packs:
        key = (p or "").strip().lower()
        if key in {"*", "all", "full"}:
            return []  # 信号：调用方应视作 full
        if key == "core":
            continue
        if key in TOOL_PACKS:
            base.update(TOOL_PACKS[key])
        if key in {"mcp", "integrations"}:
            need_mcp_live = True
    if need_mcp_live:
        if mcp_names is not None:
            base.update(str(n) for n in mcp_names if str(n).strip())
        else:
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
        # core / L0 薄面：禁止 workspace 再塞 coding pack
        if (
            bound
            and prof not in {"core"}
            and "coding" not in plan.packs
            and "*" not in plan.packs
        ):
            plan.packs = list(plan.packs) + ["coding"]
            plan.reasons = list(plan.reasons) + ["workspace_bound"]
    except Exception:
        pass

    # live MCP：Grok 风格 matching-only（Rust harness_select_mcp 权威）
    live_mcp_all = live_mcp_tool_names()
    try:
        from backend.core.config import settings as _mcp_s

        _auto = bool(getattr(_mcp_s, "agent_mcp_auto_attach_live", False))
        _matching = bool(getattr(_mcp_s, "agent_mcp_matching_only", True))
    except Exception:
        _auto = False
        _matching = True
    _mcp_use = False
    _mcp_cfg = False
    try:
        from backend.agent.simple_intent import (
            is_mcp_configure_intent,
            is_mcp_use_intent,
            select_live_mcp_tools,
        )

        _mcp_use = is_mcp_use_intent(user_input)
        _mcp_cfg = is_mcp_configure_intent(user_input)
        live_mcp = select_live_mcp_tools(
            user_input,
            live_mcp_all,
            matching_only=_matching,
            auto_attach_all=_auto,
        )
    except Exception:
        live_mcp = list(live_mcp_all) if _auto else []
        try:
            _mcp_cfg = is_mcp_ops_intent(user_input)
        except Exception:
            _mcp_cfg = False
    # 配置意图：只挂 manage_mcp，不 dump live schema
    if _mcp_cfg and not _auto:
        live_mcp = []
    # matching 命中（含自定义 server 名）→ 视为使用意图，不必写死在预制正则里
    if live_mcp and not _mcp_cfg and not _mcp_use:
        _mcp_use = True
    if (
        live_mcp
        and "mcp" not in plan.packs
        and "*" not in plan.packs
        and (_auto or _mcp_use)
    ):
        plan.packs = list(plan.packs) + ["mcp"]
        plan.reasons = list(plan.reasons) + [f"mcp_match:{len(live_mcp)}"]
    # MCP 配置：assistant/core 加 manage_mcp，不加 mcp pack 的全量 live
    if _mcp_cfg and not _auto:
        plan.reasons = list(plan.reasons) + ["mcp_configure:manage_only"]

    # 自定义可执行 skill/DB tool：matching-only 挂载（同类问题：只在 registry 却不在 pack）
    live_custom = select_live_custom_tools(user_input, matching_only=True)
    if live_custom:
        plan.reasons = list(plan.reasons) + [f"custom_tool_match:{len(live_custom)}"]

    if wants_full_tools(raw_tools, profile=prof) or "*" in plan.packs or "full" in plan.packs:
        plan.profile = "full"
        plan.injection_tier = "rich"
        return None, plan

    names = _norm_list(raw_tools)
    skills = _norm_list(raw_skills)

    # 显式 tools 名单（非 *）
    if names is not None and len(names) > 0:
        base = set(names)
        # 仅匹配后的 live（禁止 80+ schema）
        if live_mcp and (
            _mcp_use
            or any(str(n).startswith("mcp_") for n in names)
            or "manage_mcp" in names
            or _auto
        ):
            base.update(live_mcp)
        base.update(live_custom)
        if _mcp_cfg:
            base.add("manage_mcp")
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
        # 空 packs → 仅 core 白名单；mcp pack 用匹配子集
        merged = tools_for_packs(packs, mcp_names=live_mcp)
        base = set(merged)
        base.update(PROFILE_EXTRA_TOOLS.get(prof, ()))
        base.update(live_custom)
        if _mcp_cfg:
            base.add("manage_mcp")
        if skills is not None and len(skills) > 0:
            if not (len(skills) == 1 and skills[0].lower() in {"*", "all"}):
                base.update(skills)
        plan.packs = list(dict.fromkeys(packs))

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
    *,
    user_input: str = "",
    mcp_names: Iterable[str] | None = None,
) -> list[str] | None:
    """中途扩容：None(全量) 保持 None；否则并入 pack。

    Grok-style: pack ``mcp`` never dumps all live schemas — matching-only unless
    ``agent_mcp_auto_attach_live`` is on.
    """
    pack_list = [str(p).strip().lower() for p in packs if str(p).strip()]
    if any(p in {"*", "all", "full"} for p in pack_list):
        return None
    selected_mcp: list[str] | None = None
    if mcp_names is not None:
        selected_mcp = [str(n) for n in mcp_names if str(n).strip()]
    elif any(p in {"mcp", "integrations"} for p in pack_list):
        try:
            from backend.core.config import settings as _s

            _auto = bool(getattr(_s, "agent_mcp_auto_attach_live", False))
            _matching = bool(getattr(_s, "agent_mcp_matching_only", True))
        except Exception:
            _auto = False
            _matching = True
        try:
            from backend.agent.simple_intent import select_live_mcp_tools

            selected_mcp = select_live_mcp_tools(
                user_input,
                live_mcp_tool_names(),
                matching_only=_matching,
                auto_attach_all=_auto,
            )
        except Exception:
            selected_mcp = list(live_mcp_tool_names()) if _auto else []
    added = tools_for_packs(pack_list, mcp_names=selected_mcp)
    if current is None:
        return None
    return _order_tools(set(current) | set(added))


def compact_capability_brief(
    tool_names: list[str] | None,
    *,
    scene: ScenePlan | None = None,
) -> str:
    """短 brief + Tevarn 运行时纪律（注入每轮；宜短）。"""
    n = len(tool_names) if tool_names is not None else "all"
    name_set = set(tool_names or []) if tool_names is not None else set()
    lines = [
        "Tevarn runtime: only tools listed this turn exist. "
        f"Count={n}. Prefer specialized tools over shell.",
        "Anti-thrash: if http/mcp already returned enough body, ANSWER now — "
        "do not re-fetch alternate URLs. Progress chatter is not a final reply.",
        "MCP: matching-only — user names a server/product (built-in OR any custom "
        "already registered) → attach matching mcp_* only. Configure/install: "
        "one-shot key for presets, else manage_mcp add/update (any command/env). "
        "Custom executable skills: manage_skill create hot-registers; user names "
        "skill → attach that tool only. Never invent unlisted MCP/skill tools.",
        "Windows: cmd default; chain with &; dir not ls. "
        "Paths outside workspace need host-allowed roots (APPDATA Tevarn/takton, install dir).",
        "Coding: read → edit/apply_patch/file_write → command/python verify. "
        "Batch independent reads. After fix, run tests before claiming done.",
    ]
    if scene and scene.profile != "full":
        lines.append(
            f"Profile/scene: {scene.summary()[:60]}. Need packs? "
            "use_tool_pack(action='enable', packs=[...]) (action=list first)."
        )
    else:
        lines.append(
            "Need extra packs? use_tool_pack(action='list'|'enable', packs=[...])."
        )
    lines.append(
        "Skills: if a skill block/index matches the task, follow it before improvising."
    )
    if "result_load" in name_set or tool_names is None:
        lines.append("Large tool bodies may spill → result_load with the handle id.")
    if "crew_steward" in name_set:
        lines.append(
            "Workforce: multi-step team → crew_steward list/hire/assign. "
            "Simple Q&A/search: answer in-session, do NOT hire/assign."
        )
    if "okr_goal" in name_set or tool_names is None:
        lines.append(
            "Business goals (目标页 O-KR): okr_goal. Session todos: manage_goal. "
            "Do not grep the repo for goals."
        )
    if "manage_mcp" in name_set:
        lines.append(
            "MCP configure: manage_mcp for keys/reload; do not dump secrets into workspace files."
        )
    lines.append("Finish with a real deliverable, not a promise to act next turn.")
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
    "crew": ("crew", "hire", "assign", "员工", "派活", "编制", "管家", "工单"),
    "cluster": ("cluster", "delegate", "subagent", "多代理", "子代理"),
    "data": ("sql", "sqlite", "database", "数据"),
}


def injection_knobs(tier: str) -> dict[str, object]:
    """注入档位 → loop / prompt-skill / RAG 开关与阈值。"""
    t = (tier or "standard").strip().lower()
    if t == "minimal":
        # 轻环仍不塞全文 skill，但保留短目录，避免「已装 skill 完全不可见」
        return {
            "rag": False,
            "wiki": False,
            "entity": False,
            "rag_top_k": 0,
            "wiki_limit": 0,
            "entity_limit": 0,
            "rag_min_score": 0.85,
            "prompt_skills": True,
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
    # standard：阈值贴近 settings 默认 0.85，避免 0.95 饿死非点名全文
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
        "skill_threshold": 0.85,
        "skill_max_full": 1,
        "wiki_min_score": 0.2,
    }

