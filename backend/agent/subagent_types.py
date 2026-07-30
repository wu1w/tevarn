"""Typed subagents: explore | implement | review (Grok-inspired roles)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

SubagentKind = Literal["explore", "implement", "review", "general"]


@dataclass
class SubagentTypeSpec:
    kind: SubagentKind
    name: str
    description: str
    system_prompt: str
    chat_mode: str  # plan | build | subagent
    worktree: bool = False
    max_iterations: int = 12
    capabilities_hint: list[str] = field(default_factory=list)


SPECS: dict[str, SubagentTypeSpec] = {
    "explore": SubagentTypeSpec(
        kind="explore",
        name="explore",
        description="Read-only codebase investigation",
        system_prompt=(
            "你是只读探索代理。只允许读文件、搜索、列目录；"
            "禁止写文件、改代码、执行会改系统状态的 shell。"
            "输出：关键路径、结论、证据摘录。"
        ),
        chat_mode="plan",  # forces read-only via PermissionGate mode
        worktree=False,
        max_iterations=16,
        capabilities_hint=["file_rw"],  # read side of file tools
    ),
    "implement": SubagentTypeSpec(
        kind="implement",
        name="implement",
        description="Implement changes in an isolated worktree when possible",
        system_prompt=(
            "你是实现代理。按目标改代码并跑必要验证。"
            "优先最小改动；改完说明改了哪些文件与如何验证。"
        ),
        chat_mode="subagent",
        worktree=True,
        max_iterations=20,
    ),
    "review": SubagentTypeSpec(
        kind="review",
        name="review",
        description="Review code changes without applying new edits",
        system_prompt=(
            "你是代码审查代理。只读检查问题（正确性、安全、边界）。"
            "不要直接改文件；输出按严重级别排序的 findings。"
        ),
        chat_mode="plan",
        worktree=False,
        max_iterations=12,
    ),
    "general": SubagentTypeSpec(
        kind="general",
        name="general",
        description="Full-capability helper",
        system_prompt="你是通用子代理，使用可用工具完成任务并返回精炼结果。",
        chat_mode="subagent",
        worktree=False,
        max_iterations=16,
    ),
}


def resolve_type(kind: str | None) -> SubagentTypeSpec:
    k = (kind or "general").strip().lower()
    return SPECS.get(k) or SPECS["general"]


@dataclass
class _SyntheticAgent:
    id: str
    name: str
    description: str
    system_prompt: str
    model_ref: str = ""
    max_iterations: int = 12


async def run_typed_subagent(
    *,
    kind: str,
    goal: str,
    session_id: uuid.UUID | str,
    context: str = "",
    user_id: uuid.UUID | None = None,
    ws_manager: Any = None,
    parent_run_id: uuid.UUID | None = None,
    parent_kernel_process_id: str | None = None,
    depth: int = 0,
    use_worktree: bool | None = None,
) -> str:
    """Run a typed mini-agent; optionally isolate implement in a git worktree."""
    from backend.agent.subagent_runner import run_subagent
    from backend.core.config import settings

    spec = resolve_type(kind)
    want_wt = spec.worktree if use_worktree is None else bool(use_worktree)
    wt_path: str | None = None
    cleanup = None

    if want_wt and bool(getattr(settings, "agent_worktree_enabled", True)):
        try:
            from pathlib import Path

            from backend.project.worktree import add_worktree, find_git_root
            from backend.tools.permissions import resolve_agent_workspace_root

            root = find_git_root(Path(resolve_agent_workspace_root()))
            if root is not None:
                info = add_worktree(root, name=f"sub-{spec.kind}-{uuid.uuid4().hex[:8]}")
                wt_path = info.path
                logger.info("subagent worktree: %s", wt_path)
        except Exception as e:
            logger.debug("worktree skipped: %s", e)

    agent = _SyntheticAgent(
        id=f"type-{spec.kind}-{uuid.uuid4().hex[:8]}",
        name=spec.name,
        description=spec.description,
        system_prompt=spec.system_prompt,
        max_iterations=spec.max_iterations,
    )
    extra_ctx = context or ""
    if wt_path:
        extra_ctx = (
            f"{extra_ctx}\n\n[isolation] 工作目录请优先使用 worktree: {wt_path}"
        ).strip()
    # Inject chat mode for permission overlay via loop kwargs is hard;
    # explore/review use plan-mode system line so model avoids writes;
    # PermissionGate also keys off _chat_mode in tool args from loop.
    if spec.chat_mode == "plan":
        extra_ctx += "\n[mode=plan] 只读：禁止写文件与破坏性命令。"

    try:
        # Stash mode for nested tools if loop supports it
        result = await run_subagent(
            session_id=session_id,
            sub_agent=agent,
            goal=goal,
            context=extra_ctx,
            user_id=user_id,
            ws_manager=ws_manager,
            parent_run_id=parent_run_id,
            depth=depth,
            parent_kernel_process_id=parent_kernel_process_id,
        )
    finally:
        if cleanup:
            try:
                cleanup()
            except Exception:
                pass
    return result
