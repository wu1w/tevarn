"""Lightweight needsFollowUp / completion gate (Claude-style, deterministic).

When the model claims done without having done the required *kind* of work
(e.g. only glob on a fix-bug task), force one more turn with a concrete nudge.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass
class CompletionVerdict:
    ok: bool
    reason: str = ""
    nudge: str = ""


_FIX_RE = re.compile(
    r"(修\s*bug|修复|fix|off-?by-?one|broken|failing\s*test|pytest.*fail|改代码|patch)",
    re.I,
)
_BUILD_RE = re.compile(
    r"(建包|创建包|scaffold|package|gen_pkg|从零|new\s+package|写一个包|实现包)",
    re.I,
)
_FIND_RE = re.compile(
    r"(找针|SECRET|checksum|needle|在大文件|corpus|搜索秘密)",
    re.I,
)

_WRITE_TOOLS = frozenset({"file_write", "edit", "apply_patch"})
_VERIFY_TOOLS = frozenset({"command", "python", "process"})
_READ_ONLY = frozenset(
    {
        "file_read",
        "grep",
        "glob",
        "search",
        "web_search",
        "doc_read",
        "session_search",
        "browser",
        "http",
        "current_time",
        "clarify",
        "use_tool_pack",
        "list_devices_tool",
    }
)


def _norm_tools(tools: Iterable[str]) -> list[str]:
    return [str(t) for t in (tools or []) if t]


def evaluate_completion(
    user_input: str,
    tools_used: Iterable[str],
    final_text: str = "",
    *,
    max_followups_done: int = 0,
) -> CompletionVerdict:
    """Return whether the turn looks complete enough to idle."""
    if max_followups_done >= 2:
        return CompletionVerdict(ok=True, reason="followup_budget_exhausted")

    text = (user_input or "").strip()
    tools = _norm_tools(tools_used)
    final = (final_text or "").strip()
    toolset = set(tools)

    # No tools at all on an action task → incomplete
    actiony = bool(_FIX_RE.search(text) or _BUILD_RE.search(text) or _FIND_RE.search(text))
    if actiony and not tools:
        return CompletionVerdict(
            ok=False,
            reason="action_task_no_tools",
            nudge=(
                "【完成校验】这是动手任务，但你尚未调用任何工具就结束了。"
                "请立刻用工具执行（读/改/跑测），不要只描述计划。"
            ),
        )

    # Fix-bug: must have written something OR run a mutating command that isn't just ls
    if _FIX_RE.search(text):
        wrote = bool(toolset & _WRITE_TOOLS)
        ran = bool(toolset & _VERIFY_TOOLS)
        only_explore = tools and all(t in _READ_ONLY or t == "command" for t in tools)
        # command-only without write: check if any command looks like edit (sed -i)
        if not wrote:
            # allow if command contains clear fix signal - we don't have args here;
            # require write tools for code fix tasks
            if only_explore or not wrote:
                # if they only glob/grep/read
                if not wrote and toolset <= (_READ_ONLY | {"command"}):
                    # if has command, might have run tests only without fix
                    if not wrote:
                        return CompletionVerdict(
                            ok=False,
                            reason="fix_without_write",
                            nudge=(
                                "【完成校验】修 bug 任务需要实际修改代码（edit/file_write/apply_patch），"
                                "不能只 glob/grep/读文件就声称完成。"
                                "请定位缺陷、改文件，并 command 再跑测试验证。"
                            ),
                        )

    # Build package: need multiple writes ideally, at least one write + verify
    if _BUILD_RE.search(text):
        writes = sum(1 for t in tools if t in _WRITE_TOOLS)
        if writes < 1:
            return CompletionVerdict(
                ok=False,
                reason="build_without_write",
                nudge=(
                    "【完成校验】建包/脚手架任务需要 file_write 创建源码与测试文件。"
                    "请在本轮并行写出所需文件，再运行 pytest。"
                ),
            )
        if writes == 1 and not (toolset & _VERIFY_TOOLS):
            return CompletionVerdict(
                ok=False,
                reason="build_single_write_no_test",
                nudge=(
                    "【完成校验】目前只写了很少文件且未跑测。"
                    "请继续并行 file_write 补齐模块/tests，然后 command 执行 pytest。"
                ),
            )

    # Find-needle: need some read/grep and non-empty final with substance
    if _FIND_RE.search(text):
        if not tools:
            return CompletionVerdict(
                ok=False,
                reason="find_no_tools",
                nudge="【完成校验】请用 grep/file_read 实际查找后再报告 SECRET/checksum/needle。",
            )
        if final and len(final) < 8:
            return CompletionVerdict(
                ok=False,
                reason="find_empty_report",
                nudge="【完成校验】请给出包含查到值的完整简短报告，不要空结束。",
            )

    # Generic: only glob/ls style and claims done with long user task
    if tools and set(tools) <= {"glob", "current_time", "use_tool_pack"} and len(text) > 40:
        return CompletionVerdict(
            ok=False,
            reason="only_glob",
            nudge=(
                "【完成校验】你似乎只列了文件就结束。请继续读取/修改/验证，完成用户要求的交付物。"
            ),
        )

    return CompletionVerdict(ok=True, reason="ok")


__all__ = ["CompletionVerdict", "evaluate_completion"]
