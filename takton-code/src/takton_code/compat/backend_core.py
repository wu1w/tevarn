"""Optional bridge: reuse Takton backend agent modules when monorepo path is available.

Isolated ``pip install takton-code`` keeps local implementations.
When ``backend`` is importable (Desktop / monorepo), prefer shared Batch1/2 modules.

TUI / AgentRuntime should import shared capabilities from here.
"""
from __future__ import annotations

HAS_BACKEND = False

# --- always-local pieces (code-only or richer than backend lite) ---
from takton_code.agent.permissions import (  # noqa: E402
    PermissionBroker,
    Reply,
)
from takton_code.agent.file_history import FileHistory  # noqa: E402
from takton_code.agent.redo import RedoStack, apply_redo_files, new_entry  # noqa: E402

try:
    from takton_code.agent.file_history import format_rewind_side_panel  # noqa: E402
except ImportError:  # pragma: no cover
    def format_rewind_side_panel(*_a, **_k):  # type: ignore
        return ""

try:
    from backend.agent.doom_loop import DoomLoopGuard
    from backend.agent.hunks import (
        apply_selected_hunks,
        hunks_summary,
        parse_unified_hunks,
    )
    from backend.agent.permissions_rules import (
        PermissionGate,
        PermissionRule,
        rules_for_profile,
        TOOL_TO_KEY,
    )
    from backend.agent.plan_gate import (
        PlanDocument,
        PlanGate,
        PlanState,
        PlanStep,
        should_auto_plan,
    )
    from backend.agent.best_of_n import BonCandidate, score_candidate, pick_winner
    from backend.agent.multimodal_parts import (
        build_user_content,
        content_for_storage,
        find_image_paths,
    )
    from backend.tools.diff_engine import DiffEngine, FileChange
    from backend.project.worktree import (
        WorktreeError,
        add_worktree,
        find_git_root,
        gc_worktrees,
        inspect_worktree_state,
        list_worktrees,
        remove_worktree,
        resolve_session_root,
        show_worktree,
    )

    try:
        from backend.agent.best_of_n import rank_candidates, summarize_bon
    except ImportError:  # pragma: no cover
        rank_candidates = None  # type: ignore
        summarize_bon = None  # type: ignore

    HAS_BACKEND = True
except ImportError:
    from takton_code.agent.doom_loop import DoomLoopGuard
    from takton_code.agent.hunks import (
        apply_selected_hunks,
        hunks_summary,
        parse_unified_hunks,
    )
    from takton_code.agent.permissions import (
        PermissionGate,
        PermissionRule,
        rules_for_profile,
        TOOL_TO_KEY,
    )
    from takton_code.plan.gate import (
        PlanDocument,
        PlanGate,
        PlanState,
        PlanStep,
        should_auto_plan,
    )
    from takton_code.agent.best_of_n import BonCandidate, score_candidate

    def pick_winner(cands):  # type: ignore
        from takton_code.agent.best_of_n import score_candidate as _sc

        xs = list(cands)
        for c in xs:
            _sc(c)
        return sorted(xs, key=lambda c: c.score, reverse=True)[0] if xs else None

    from takton_code.agent.multimodal import (
        build_user_content,
        content_for_storage,
        find_image_paths,
    )
    from takton_code.diff.engine import DiffEngine, FileChange
    from takton_code.project.worktree import (
        WorktreeError,
        add_worktree,
        find_git_root,
        gc_worktrees,
        inspect_worktree_state,
        list_worktrees,
        remove_worktree,
        resolve_session_root,
        show_worktree,
    )

    rank_candidates = None  # type: ignore
    summarize_bon = None  # type: ignore

def __getattr__(name: str):
    """Lazy export to avoid import cycles with agent.best_of_n."""
    if name == "run_best_of_n":
        from takton_code.agent.best_of_n import run_best_of_n as _r

        return _r
    raise AttributeError(name)

__all__ = [
    "HAS_BACKEND",
    "DoomLoopGuard",
    "parse_unified_hunks",
    "apply_selected_hunks",
    "hunks_summary",
    "PermissionGate",
    "PermissionBroker",
    "PermissionRule",
    "Reply",
    "rules_for_profile",
    "TOOL_TO_KEY",
    "PlanGate",
    "PlanDocument",
    "PlanState",
    "PlanStep",
    "should_auto_plan",
    "BonCandidate",
    "score_candidate",
    "pick_winner",
    "run_best_of_n",
    "build_user_content",
    "content_for_storage",
    "find_image_paths",
    "DiffEngine",
    "FileChange",
    "FileHistory",
    "format_rewind_side_panel",
    "RedoStack",
    "apply_redo_files",
    "new_entry",
    "WorktreeError",
    "find_git_root",
    "list_worktrees",
    "add_worktree",
    "remove_worktree",
    "show_worktree",
    "gc_worktrees",
    "inspect_worktree_state",
    "resolve_session_root",
]
