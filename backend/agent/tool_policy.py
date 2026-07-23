"""对话默认工具策略：收敛每轮暴露给 LLM 的工具面，提高指令密度。

约定：
- profile=core（默认）：仅核心白名单 + 模式附加
- profile=full 或 tools 含 \"*\"/\"all\"：全部已注册工具
- session config 显式 tools 名单：以名单为准（可再叠加 mode extra）
"""
from __future__ import annotations

from typing import Iterable

# 对话 default 核心面（约 18 个）：读写改搜 shell / 轻量网络 / 设备基础
# 管理类、进化、桌面全家桶、办公多媒体默认不进 schema
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
)

# 按 ChatMode 附加（仅在对应模式出现）
MODE_TOOL_EXTRAS: dict[str, tuple[str, ...]] = {
    "search": ("web_search", "search", "fetch_webpage"),
    "ppt": ("generate_ppt", "doc_read", "doc_write"),
    "report": ("generate_report", "doc_read", "doc_write", "render_chart"),
    "goal": ("manage_goal", "autopilot"),
    "cluster": ("manage_sub_agent", "delegate_task", "agent_call"),
    "deepthink": (),
    "default": (),
}

# 出现这些工具名时才注入 Evolution 长文 system 指导
EVOLUTION_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "manage_evolution",
        "query_evolution",
        "manage_skill",
    }
)


def _norm_list(raw: object | None) -> list[str] | None:
    """None → None；list → 去空白字符串列表。"""
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple, set)):
        return None
    out = [str(x).strip() for x in raw if str(x).strip()]
    return out


def wants_full_tools(
    raw_tools: object | None,
    *,
    profile: str = "core",
) -> bool:
    """是否暴露全部工具。"""
    if (profile or "core").strip().lower() == "full":
        return True
    names = _norm_list(raw_tools)
    if not names:
        return False
    lowered = {n.lower() for n in names}
    return "*" in lowered or "all" in lowered or "full" in lowered


def resolve_enabled_tool_names(
    *,
    mode: str = "default",
    raw_tools: object | None = None,
    raw_skills: object | None = None,
    profile: str = "core",
    extra: Iterable[str] | None = None,
) -> list[str] | None:
    """解析本轮应暴露的工具名。

    Returns:
        None = 不过滤（全量）
        list[str] = 白名单过滤
    """
    if wants_full_tools(raw_tools, profile=profile):
        return None

    names = _norm_list(raw_tools)
    skills = _norm_list(raw_skills)

    # 显式 tools 名单（非 *）
    if names is not None and len(names) > 0:
        base = set(names)
    else:
        # 缺省 / 空列表 → core 白名单
        # 注意：旧约定 skills=[] 表示 ALL；tools 同理。现改为 core。
        base = set(DEFAULT_CHAT_TOOL_WHITELIST)
        # 显式 skills 名单时并入（兼容旧 session）
        if skills is not None and len(skills) > 0 and skills != ["*"]:
            if not (len(skills) == 1 and skills[0].lower() in {"*", "all"}):
                base.update(skills)

    mode_key = (mode or "default").strip().lower()
    base.update(MODE_TOOL_EXTRAS.get(mode_key, ()))
    if extra:
        base.update(str(x).strip() for x in extra if str(x).strip())

    # 稳定顺序：白名单顺序优先，其余按名字
    preferred = list(DEFAULT_CHAT_TOOL_WHITELIST)
    ordered: list[str] = []
    seen: set[str] = set()
    for n in preferred + sorted(base):
        if n in base and n not in seen:
            ordered.append(n)
            seen.add(n)
    return ordered


def compact_capability_brief(tool_names: list[str] | None) -> str:
    """短 brief：只强调纪律，不广告全平台。"""
    n = len(tool_names) if tool_names is not None else "all"
    return (
        "Tool discipline: use the provided tools for facts, files, shell, and live data. "
        f"Available tool count this turn: {n}. "
        "Do not claim a capability is missing without trying the matching tool first. "
        "Prefer tools over speculation; finish the user task."
    )
