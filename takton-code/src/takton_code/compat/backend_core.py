"""Optional bridge: reuse Takton backend agent modules when monorepo path is available.

Isolated ``pip install takton-code`` keeps local implementations.
When ``backend`` is importable (Desktop / monorepo), prefer shared Batch1/2 modules.
"""
from __future__ import annotations

HAS_BACKEND = False

try:
    from backend.agent.doom_loop import DoomLoopGuard  # noqa: F401
    from backend.agent.hunks import apply_selected_hunks, parse_unified_hunks  # noqa: F401
    from backend.agent.permissions_rules import (  # noqa: F401
        PermissionGate,
        rules_for_profile,
    )
    from backend.agent.plan_gate import PlanGate, should_auto_plan  # noqa: F401
    from backend.agent.best_of_n import BonCandidate, score_candidate  # noqa: F401
    from backend.agent.multimodal_parts import build_user_content  # noqa: F401
    from backend.tools.diff_engine import DiffEngine  # noqa: F401
    from backend.project.worktree import find_git_root, list_worktrees  # noqa: F401

    HAS_BACKEND = True
except ImportError:
    # standalone takton-code
    from takton_code.agent.doom_loop import DoomLoopGuard  # type: ignore  # noqa: F401
    from takton_code.agent.hunks import apply_selected_hunks, parse_unified_hunks  # type: ignore  # noqa: F401
    from takton_code.agent.permissions import (  # type: ignore  # noqa: F401
        PermissionGate,
        rules_for_profile,
    )
    from takton_code.plan.gate import PlanGate, should_auto_plan  # type: ignore  # noqa: F401
    from takton_code.agent.best_of_n import BonCandidate, score_candidate  # type: ignore  # noqa: F401
    from takton_code.agent.multimodal import build_user_content  # type: ignore  # noqa: F401
    from takton_code.diff.engine import DiffEngine  # type: ignore  # noqa: F401
    from takton_code.project.worktree import find_git_root, list_worktrees  # type: ignore  # noqa: F401

__all__ = [
    "HAS_BACKEND",
    "DoomLoopGuard",
    "parse_unified_hunks",
    "apply_selected_hunks",
    "PermissionGate",
    "rules_for_profile",
    "PlanGate",
    "should_auto_plan",
    "BonCandidate",
    "score_candidate",
    "build_user_content",
    "DiffEngine",
    "find_git_root",
    "list_worktrees",
]
