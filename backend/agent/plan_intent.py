"""Plan mode intent: detect plan request / approve / complex tasks."""

from __future__ import annotations

import re
from typing import Any

_PLAN_REQUEST = re.compile(
    r"(?i)(先做计划|先出计划|先写计划|制定计划|给出计划|"
    r"plan\s*mode|make\s+a\s+plan|write\s+a\s+plan|"
    r"不要直接改|先别改代码|只出方案)"
)
# F2: 禁止裸「开始执行」——必须带「计划」语义
_PLAN_APPROVE = re.compile(
    r"(?i)("
    r"批准计划|同意计划|按计划执行|开始执行计划|执行该计划|执行此计划|"
    r"approve\s*(the\s*)?plan|lgtm\s*(the\s*)?plan|"
    r"按(这个|此|该)计划(做|执行|来)"
    r")"
)
_PLAN_REJECT = re.compile(
    r"(?i)(推翻计划|重做计划|不要这个计划|reject\s*plan|改计划)"
)
_COMPLEX_TASK = re.compile(
    r"(?i)(重构|migrate|迁移|架构|多模块|端到端|全栈|"
    r"implement\s+auth|add\s+authentication|从零实现|大规模|整个系统)"
)

PLAN_READONLY_TOOLS = frozenset({
    "file_read", "grep", "glob", "doc_read", "web_search", "search",
    "current_time", "clarify", "session_search", "result_load",
    "use_tool_pack", "list_available_models", "get_system_status",
})


def is_plan_request(text: str) -> bool:
    return bool(_PLAN_REQUEST.search(text or ""))


def is_plan_approve(text: str) -> bool:
    """仅明确批准计划；裸「开始执行」不命中。"""
    t = (text or "").strip()
    if not t:
        return False
    return bool(_PLAN_APPROVE.search(t))


def is_plan_reject(text: str) -> bool:
    return bool(_PLAN_REJECT.search(text or ""))


def is_complex_for_auto_plan(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 16:
        return False
    return bool(_COMPLEX_TASK.search(t))


def plan_system_prompt() -> str:
    return (
        "【Plan 模式】本轮只做计划，禁止写文件、禁止执行破坏性命令。\n"
        "请输出结构化计划（标题/摘要/步骤/风险/验证）。\n"
        "完成后等待用户说「批准计划」或「按计划执行」再动代码。"
    )


def filter_tools_for_plan(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return tools
    out: list[dict[str, Any]] = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") if isinstance(t.get("function"), dict) else {}
        name = str((fn or {}).get("name") or t.get("name") or "")
        if name in PLAN_READONLY_TOOLS:
            out.append(t)
        elif name.startswith("mcp_") and not any(
            x in name for x in ("write", "delete", "exec", "run")
        ):
            out.append(t)
    return out
