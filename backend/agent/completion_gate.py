"""Lightweight needsFollowUp / completion gate (Claude-style, deterministic).

When the model claims done without having done the required *kind* of work
(e.g. only glob on a fix-bug task), force one more turn with a concrete nudge.

0.3.6: soft / balanced / strict via TAKTON_GROUNDING_MODE (default soft).
Strong models auto-relax one tier so long rituals do not bind Claude/GPT/Grok.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Literal

Mode = Literal["soft", "balanced", "strict"]

_STRONG = re.compile(
    r"(claude|sonnet|opus|haiku|gpt-?4|gpt-?5|o1|o3|o4|"
    r"grok|kimi|moonshot|glm|chatglm|gemini|deepseek|"
    r"qwen|codex|mistral-large|command-r)",
    re.I,
)
_WEAK = re.compile(
    r"(tiny|mini|nano|1b|3b|7b|8b|small|lite|flash-lite|haiku-?3)",
    re.I,
)


@dataclass(frozen=True)
class GroundingPolicy:
    mode: Mode
    hard_empty_tools: bool = True
    hard_fix_build_write: bool = True
    hard_list_only: bool = False  # only_glob hard force
    hard_build_verify: bool = False  # single write without test
    max_hard_followups: int = 1
    short_prompts: bool = True


def _raw_mode() -> Mode:
    raw = (os.environ.get("TAKTON_GROUNDING_MODE") or "soft").strip().lower()
    if raw in ("soft", "balanced", "strict", "hard"):
        return "strict" if raw == "hard" else raw  # type: ignore[return-value]
    return "soft"


def classify_model_tier(model_name: str | None) -> str:
    name = (model_name or "").strip()
    if not name:
        return "default"
    if _WEAK.search(name) and not _STRONG.search(name):
        return "weak"
    if _STRONG.search(name):
        return "strong"
    return "default"


def resolve_mode(model_name: str | None = None) -> Mode:
    """Env mode, then auto-relax for strong models (never tighten)."""
    mode = _raw_mode()
    tier = classify_model_tier(model_name)
    if tier == "strong":
        if mode == "strict":
            return "balanced"
        if mode == "balanced":
            return "soft"
    if tier == "weak" and mode == "soft":
        return "balanced"
    return mode


@lru_cache(maxsize=8)
def get_policy(model_name: str | None = None) -> GroundingPolicy:
    mode = resolve_mode(model_name)
    if mode == "soft":
        return GroundingPolicy(
            mode="soft",
            hard_empty_tools=True,
            hard_fix_build_write=True,
            hard_list_only=False,
            hard_build_verify=False,
            max_hard_followups=1,
            short_prompts=True,
        )
    if mode == "balanced":
        return GroundingPolicy(
            mode="balanced",
            hard_empty_tools=True,
            hard_fix_build_write=True,
            hard_list_only=True,
            hard_build_verify=False,
            max_hard_followups=1,
            short_prompts=True,
        )
    return GroundingPolicy(
        mode="strict",
        hard_empty_tools=True,
        hard_fix_build_write=True,
        hard_list_only=True,
        hard_build_verify=True,
        max_hard_followups=2,
        short_prompts=False,
    )


# clear cache when tests change env
def clear_policy_cache() -> None:
    get_policy.cache_clear()


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

_PATH_RE = re.compile(
    r"(?:^|[\s`\"'(])("
    r"(?:backend|frontend|src|lib|app|scripts|electron|docs)/"
    r"[\w./\\-]+\.\w{1,8}"
    r")",
    re.I,
)


def _norm_tools(tools: Iterable[str]) -> list[str]:
    return [str(t) for t in (tools or []) if t]


def evaluate_completion(
    user_input: str,
    tools_used: Iterable[str],
    final_text: str = "",
    *,
    max_followups_done: int = 0,
    model_name: str | None = None,
) -> CompletionVerdict:
    """Return whether the turn looks complete enough to idle."""
    pol = get_policy(model_name)
    if max_followups_done >= max(2, pol.max_hard_followups + 1):
        return CompletionVerdict(ok=True, reason="followup_budget_exhausted")
    # soft: only one hard followup by default
    if max_followups_done >= pol.max_hard_followups and pol.mode == "soft":
        # still allow fix/build write checks once more if never passed? keep simple:
        # after max_hard_followups soft stops forcing
        if max_followups_done >= 2:
            return CompletionVerdict(ok=True, reason="followup_budget_exhausted")

    text = (user_input or "").strip()
    tools = _norm_tools(tools_used)
    final = (final_text or "").strip()
    toolset = set(tools)

    actiony = bool(_FIX_RE.search(text) or _BUILD_RE.search(text) or _FIND_RE.search(text))

    # Empty tools on action task — hard in all modes (once)
    if pol.hard_empty_tools and actiony and not tools:
        if max_followups_done < pol.max_hard_followups:
            return CompletionVerdict(
                ok=False,
                reason="action_task_no_tools",
                nudge=(
                    "【补充取证】这是动手任务，但你尚未调用任何工具就结束了。"
                    "请立刻用工具执行（读/改/跑测），不要只描述计划。"
                ),
            )

    # Fix-bug: must write
    if pol.hard_fix_build_write and _FIX_RE.search(text):
        wrote = bool(toolset & _WRITE_TOOLS)
        if not wrote and max_followups_done < max(pol.max_hard_followups, 2):
            if not tools or toolset <= (_READ_ONLY | {"command"}):
                return CompletionVerdict(
                    ok=False,
                    reason="fix_without_write",
                    nudge=(
                        "【补充取证】修 bug 任务需要实际修改代码（edit/file_write/apply_patch），"
                        "不能只 glob/grep/读文件就声称完成。"
                        "请定位缺陷、改文件，并 command 再跑测试验证。"
                    ),
                )

    # Build package
    if pol.hard_fix_build_write and _BUILD_RE.search(text):
        writes = sum(1 for t in tools if t in _WRITE_TOOLS)
        if writes < 1 and max_followups_done < max(pol.max_hard_followups, 2):
            return CompletionVerdict(
                ok=False,
                reason="build_without_write",
                nudge=(
                    "【补充取证】建包/脚手架任务需要 file_write 创建源码与测试文件。"
                    "请在本轮并行写出所需文件，再运行 pytest。"
                ),
            )
        if (
            pol.hard_build_verify
            and writes == 1
            and not (toolset & _VERIFY_TOOLS)
            and max_followups_done < pol.max_hard_followups
        ):
            return CompletionVerdict(
                ok=False,
                reason="build_single_write_no_test",
                nudge=(
                    "【补充取证】目前只写了很少文件且未跑测。"
                    "请继续并行 file_write 补齐模块/tests，然后 command 执行 pytest。"
                ),
            )

    # Find-needle：soft 下仅拦「零工具」；空报告在 soft 放行（强模型常极短回复）
    if _FIND_RE.search(text):
        if not tools and max_followups_done < pol.max_hard_followups:
            return CompletionVerdict(
                ok=False,
                reason="find_no_tools",
                nudge="【补充取证】请用 grep/file_read 实际查找后再报告 SECRET/checksum/needle。",
            )
        if (
            pol.mode != "soft"
            and final
            and len(final) < 8
            and max_followups_done < pol.max_hard_followups
        ):
            return CompletionVerdict(
                ok=False,
                reason="find_empty_report",
                nudge="【补充取证】请给出包含查到值的完整简短报告，不要空结束。",
            )

    # only_glob: soft default allows; balanced/strict may force once
    if (
        pol.hard_list_only
        and tools
        and set(tools) <= {"glob", "current_time", "use_tool_pack"}
        and len(text) > 40
        and max_followups_done < 1
    ):
        return CompletionVerdict(
            ok=False,
            reason="only_glob",
            nudge=(
                "【轻提示】你似乎只列了文件就结束。若任务需要读/改/验证，请继续；"
                "否则直接给出结论即可。"
            ),
        )

    return CompletionVerdict(ok=True, reason="ok")


def grounding_prompt_block(model_name: str | None = None) -> str:
    """Short Evidence block for system prompt (soft = minimal)."""
    pol = get_policy(model_name)
    if pol.short_prompts:
        return (
            "# Evidence\n"
            "Prefer tools over guesses for paths, numbers, and CVE/stack claims. "
            "If blocked, say so — do not invent file contents or command output."
        )
    return (
        "# Evidence (strict)\n"
        "Use tools for any concrete path, metric, or external fact. "
        "Never fabricate tool results. Fix/build tasks require real writes and verification."
    )


def extract_cited_paths(text: str) -> list[str]:
    if not text:
        return []
    found: list[str] = []
    for m in _PATH_RE.finditer(text):
        p = m.group(1).replace("\\", "/")
        if p not in found:
            found.append(p)
    return found[:12]


def project_roots() -> list[Path]:
    roots: list[Path] = []
    cwd = Path.cwd().resolve()
    roots.append(cwd)
    # monorepo: backend parent
    if (cwd / "backend").is_dir():
        roots.append(cwd)
    if cwd.name == "backend" and cwd.parent.exists():
        roots.append(cwd.parent)
    # workspace env
    for key in ("TAKTON_WORKSPACE", "WORKSPACE_ROOT"):
        v = (os.environ.get(key) or "").strip()
        if v:
            try:
                roots.append(Path(v).resolve())
            except Exception:
                pass
    # dedupe
    out: list[Path] = []
    seen: set[str] = set()
    for r in roots:
        s = str(r)
        if s not in seen:
            seen.add(s)
            out.append(r)
    return out


def path_exists_in_project(rel: str) -> bool:
    rel_n = rel.replace("\\", "/").lstrip("./")
    for root in project_roots():
        cand = (root / rel_n).resolve()
        try:
            if cand.exists():
                return True
            # also try under root without forcing resolve outside
            if (root / rel_n).exists():
                return True
        except Exception:
            continue
    return False


def maybe_annotate_report(final_text: str | None, user_input: str = "") -> str | None:
    """Append a short footer when cited project paths look missing."""
    if not final_text:
        return final_text
    blob = f"{user_input or ''}\n{final_text}"
    cited = extract_cited_paths(blob)
    if not cited:
        return final_text
    missing = [p for p in cited if not path_exists_in_project(p)]
    if not missing:
        return final_text
    # avoid double footer
    if "落地校验" in final_text or "path missing" in final_text.lower():
        return final_text
    note = "；".join(missing[:5])
    return (
        final_text.rstrip()
        + f"\n\n——\n_落地校验：以下路径在当前工程中未找到（请核对）：{note}_"
    )


__all__ = [
    "CompletionVerdict",
    "GroundingPolicy",
    "evaluate_completion",
    "get_policy",
    "clear_policy_cache",
    "grounding_prompt_block",
    "maybe_annotate_report",
    "resolve_mode",
]
