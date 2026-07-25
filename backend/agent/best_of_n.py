"""best-of-n scoring + optional fanout (Batch 1: score 可用；完整 fanout 依赖 worktree Batch2).

Ported/adapted from takton-code agent/best_of_n.py.
Default: disabled in settings (agent_best_of_n_enabled=False).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class BonCandidate:
    index: int
    session_id: str | None = None
    worktree_path: str | None = None
    worktree_name: str | None = None
    final_text: str = ""
    changes_summary: str = ""
    test_ok: bool | None = None
    score: float = 0.0
    error: str | None = None
    interrupted: bool = False
    iterations: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def score_candidate(c: BonCandidate) -> float:
    s = 0.0
    if c.error:
        s -= 1000.0
    if c.interrupted:
        s -= 50.0
    if c.test_ok is True:
        s += 100.0
    elif c.test_ok is False:
        s -= 20.0
    if c.changes_summary and "no file changes" not in c.changes_summary:
        s += 10.0
    if (c.final_text or "").strip():
        s += 5.0
    s -= min(3.0, len(c.final_text or "") / 5000.0)
    c.score = s
    return s


def rank_candidates(candidates: list[BonCandidate]) -> list[BonCandidate]:
    for c in candidates:
        score_candidate(c)
    return sorted(candidates, key=lambda x: x.score, reverse=True)


def pick_winner(candidates: list[BonCandidate]) -> BonCandidate | None:
    ranked = rank_candidates(list(candidates))
    return ranked[0] if ranked else None


def summarize_bon(candidates: list[BonCandidate], *, prompt: str = "") -> dict[str, Any]:
    ranked = rank_candidates(list(candidates))
    winner = ranked[0] if ranked else None
    return {
        "n": len(candidates),
        "prompt": prompt,
        "winner_index": winner.index if winner else None,
        "winner": winner.to_dict() if winner else None,
        "candidates": [c.to_dict() for c in ranked],
        "note": (
            "Winner is NOT auto-applied. Full worktree fanout lands in Batch 2 "
            "(agent_best_of_n_enabled + worktree)."
        ),
        "enabled_runtime": False,
    }
