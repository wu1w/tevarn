"""单个 bench 任务的执行器（T6）。

每次运行：
  1. 把 fixture 复制到临时 workspace（原始 fixture 永不被改）
  2. 把 agent 的工作区/权限/执行环境钉死在该 workspace
  3. 真跑一次 agent loop，记录轮数 / 工具调用 / token / 缓存命中
  4. 跑断言，产出 TaskRun

这里刻意不 mock LLM —— bench 的全部意义就是量出真实模型 + 真实工具回路的表现。
没有可用 LLM 配置时由 run_bench.py 提前拒绝，而不是在这里造假数据。
"""

from __future__ import annotations

import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .assertions import AssertionResult, run_assertions


@dataclass
class TaskRun:
    task: str
    repeat_index: int
    passed: bool
    iterations: int = 0
    tool_calls: int = 0
    tool_names: list[str] = field(default_factory=list)
    parallel_batches: int = 0
    wall_seconds: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read_tokens: int = 0
    assertions: list[AssertionResult] = field(default_factory=list)
    reply: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "repeat_index": self.repeat_index,
            "passed": self.passed,
            "iterations": self.iterations,
            "tool_calls": self.tool_calls,
            "tool_names": self.tool_names,
            "parallel_batches": self.parallel_batches,
            "wall_seconds": round(self.wall_seconds, 2),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "assertions": [
                {"type": a.type, "ok": a.ok, "detail": a.detail} for a in self.assertions
            ],
            "reply": self.reply[:2000],
            "error": self.error,
        }


def snapshot_workspace(ws: Path) -> dict[str, str]:
    """记录初始文件内容，供 workspace_unchanged 断言比对。"""
    snap: dict[str, str] = {}
    for p in ws.rglob("*"):
        if not p.is_file():
            continue
        try:
            snap[str(p.relative_to(ws))] = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
    return snap


def prepare_workspace(fixture: Path | None, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    if fixture is not None and fixture.is_dir():
        shutil.copytree(fixture, dest, dirs_exist_ok=True)
    return dest


class _Counter:
    """通过 tool hook 统计工具调用，不侵入 loop 代码。"""

    def __init__(self) -> None:
        self.names: list[str] = []

    def before(self, name: str, args: dict[str, Any]):
        from backend.agent.tool_hooks import BeforeHookResult

        self.names.append(name)
        return BeforeHookResult(arguments=args)


async def run_task(
    task: dict[str, Any],
    *,
    workspace: Path,
    repeat_index: int,
    working_mode: str = "autonomous",
    execution_mode: str = "local",
) -> TaskRun:
    """跑一个任务一次。

    默认 working_mode=autonomous：bench 无人值守，任何 ask 都会挂死。
    默认 execution_mode=local：fixture 在临时目录，沙箱会因 cwd 越界拒绝执行。
    这两个默认值是 bench 的运行前提，不代表产品默认（产品默认见 working_mode.py）。
    """
    from backend.agent.tool_hooks import (
        clear_tool_hooks,
        register_before_tool_call,
    )
    from backend.core.config import settings

    # 任务可覆盖工作方式：安全类任务若跑在 autonomous 下权限门全放行，断言就没意义了
    working_mode = str(task.get("working_mode") or working_mode)
    execution_mode = str(task.get("execution_mode") or execution_mode)

    name = task["name"]
    run = TaskRun(task=name, repeat_index=repeat_index, passed=False)

    baseline = snapshot_workspace(workspace)
    counter = _Counter()

    # 环境钉死：工作区 + 权限 + 执行环境
    prev = {
        "root": getattr(settings, "file_browser_root", None),
        "wm": getattr(settings, "agent_working_mode", None),
        "em": getattr(settings, "agent_execution_mode", None),
        "iters": getattr(settings, "agent_max_iterations", None),
        "computer": getattr(settings, "agent_computer_enabled", None),
    }
    budget = task.get("budget") or {}

    t0 = time.monotonic()
    try:
        settings.file_browser_root = str(workspace)
        settings.agent_working_mode = working_mode
        settings.agent_execution_mode = execution_mode
        settings.agent_computer_enabled = False
        settings.agent_max_iterations = int(budget.get("max_iterations", 25))

        clear_tool_hooks()
        register_before_tool_call(counter.before)
        # 权限 hook 在 clear 后需重新注册，否则 bench 会在无权限门的状态下跑
        from backend.agent.tool_hooks import ensure_builtin_hooks_registered

        ensure_builtin_hooks_registered()

        reply, iters = await _drive_agent(task, workspace)
        run.reply = reply or ""
        run.iterations = iters
    except Exception as e:
        run.error = f"{type(e).__name__}: {e}"
    finally:
        run.wall_seconds = time.monotonic() - t0
        clear_tool_hooks()
        if prev["root"] is not None:
            settings.file_browser_root = prev["root"]
        if prev["wm"] is not None:
            settings.agent_working_mode = prev["wm"]
        if prev["em"] is not None:
            settings.agent_execution_mode = prev["em"]
        if prev["iters"] is not None:
            settings.agent_max_iterations = prev["iters"]
        if prev["computer"] is not None:
            settings.agent_computer_enabled = prev["computer"]

    run.tool_names = counter.names
    run.tool_calls = len(counter.names)

    # token / 缓存指标取自引擎累计值（provider 有真实 usage 时是真值）
    try:
        from backend.agent.context_engine import get_context_engine

        meter = getattr(get_context_engine(), "meter", None)
        run.prompt_tokens = int(getattr(meter, "last_prompt_tokens", 0) or 0)
        run.completion_tokens = int(getattr(meter, "last_completion_tokens", 0) or 0)
    except Exception:
        pass

    specs = []
    for spec in task.get("assertions", []):
        spec = dict(spec)
        if spec.get("type") == "workspace_unchanged":
            spec["_baseline"] = baseline
        specs.append(spec)

    run.assertions = run_assertions(workspace, specs, run.reply)
    run.passed = bool(run.assertions) and all(a.ok for a in run.assertions) and not run.error
    return run




_tools_loaded = False


async def _ensure_tools_loaded() -> None:
    """Bench process does not run FastAPI lifespan — load ToolRegistry like main.py."""
    global _tools_loaded
    if _tools_loaded:
        return
    from backend.tools.loader import load_all_tools
    from backend.tools.registry import ToolRegistry

    if not ToolRegistry.get_all():
        await load_all_tools()
    _tools_loaded = True


async def _bench_user_id():
    """Resolve a real user for bench sessions (NOT NULL FK)."""
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models.user import User

    async with AsyncSessionLocal() as db:
        uid = (
            await db.execute(select(User.id).order_by(User.created_at.asc()).limit(1))
        ).scalar_one_or_none()
    if uid is None:
        raise RuntimeError("bench: no users in DB — cannot create session")
    return uid


async def _drive_agent(task: dict[str, Any], workspace: Path) -> tuple[str, int]:
    """真跑 agent loop，返回 (最终回复, 迭代轮数)。"""
    from backend.database import get_db_context  # noqa: F401  (确保 DB 初始化)
    from backend.agent.loop import NexusAgentLoop
    from backend.repositories.context_repo import (
        AsyncContextFlowRepository,
        AsyncCtxItemRepository,
    )
    from backend.repositories.message_repo import AsyncMessageRepository
    from backend.repositories.session_repo import AsyncSessionRepository
    from backend.repositories.task_repo import AsyncTaskRepository

    # sessions.user_id 是 NOT NULL：bench 必须绑到真实用户，否则 create 直接 IntegrityError
    # （此前 user_id=None 导致 0 轮 0 工具，假失败）。
    await _ensure_tools_loaded()
    user_id = await _bench_user_id()

    session_repo = AsyncSessionRepository()
    session = await session_repo.create(
        {"user_id": user_id, "config": {"workspace_root": str(workspace)}}
    )

    agent = NexusAgentLoop(
        session_repo=session_repo,
        message_repo=AsyncMessageRepository(),
        task_repo=AsyncTaskRepository(),
        ctx_item_repo=AsyncCtxItemRepository(),
        context_flow_repo=AsyncContextFlowRepository(),
        ws_manager=None,  # 无确认通道 → headless 兜底
        user_id=user_id,
    )
    budget = task.get("budget") or {}
    agent.max_iterations = int(budget.get("max_iterations", 25))

    reply = await agent.run(
        session.id, task["prompt"], mode=task.get("mode", "default")
    )
    iters = int(getattr(agent, "last_iterations", 0) or 0)
    return reply or "", iters
