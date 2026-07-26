"""best-of-n headless fanout (Grok-style). Winner is reported only — no auto-apply."""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from takton_code.project.worktree import WorktreeError, add_worktree, remove_worktree


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
    # prefer slightly shorter answers when tied (noise penalty)
    s -= min(3.0, len(c.final_text or "") / 5000.0)
    c.score = s
    return s


OpenRuntime = Callable[..., Awaitable[tuple[Any, Any, Any]]]


async def run_best_of_n(
    *,
    n: int,
    prompt: str,
    path: str | Path | None,
    open_runtime: OpenRuntime,
    mode: str = "build",
    force_bridge: bool | None = None,
    force_local: bool = False,
    concurrency: int = 3,
    run_tests: bool = True,
    keep_worktrees: bool = True,
) -> dict[str, Any]:
    """
    Run prompt N ways in isolated worktrees.
    Does NOT apply winner back to main tree (维护者 recommendation).
    """
    n = max(1, min(int(n), 8))
    concurrency = max(1, min(int(concurrency), n))
    root = Path(path or Path.cwd()).resolve()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    sem = asyncio.Semaphore(concurrency)

    async def one(i: int) -> BonCandidate:
        cand = BonCandidate(index=i)
        wt_name = f"bon-{stamp}-{i}"
        async with sem:
            try:
                info = add_worktree(root, name=wt_name, ref=None)
                cand.worktree_name = info.name
                cand.worktree_path = info.path
            except WorktreeError as e:
                cand.error = f"worktree: {e}"
                score_candidate(cand)
                return cand

            rt = store = br = None
            try:
                rt, store, br = await open_runtime(
                    str(info.path),
                    mode=mode,
                    session_id=None,
                    force_bridge=force_bridge,
                    force_local=force_local,
                    worktree=None,  # already inside worktree path
                    worktree_ref=None,
                    event_json=True,
                )
                cand.session_id = rt.session_id
                # silence noisy events for bon children if needed
                result = await rt.run_turn(prompt)
                cand.final_text = result.final_text or ""
                cand.changes_summary = result.changes_summary or ""
                cand.interrupted = bool(result.interrupted)
                cand.iterations = int(result.iterations or 0)
                if result.error:
                    cand.error = result.error
                if run_tests and rt.tools and not cand.error:
                    try:
                        out = await rt.tools.run_tests({})
                        cand.test_ok = out.startswith("exit=0") or "\nexit=0" in out or out.startswith(
                            "exit=0\n"
                        )
                        if out.startswith("exit="):
                            # parse exit=
                            try:
                                code = int(out.split("\n", 1)[0].split("=", 1)[1])
                                cand.test_ok = code == 0
                            except Exception:
                                cand.test_ok = "exit=0" in out[:20]
                    except Exception as e:  # noqa: BLE001
                        cand.test_ok = False
                        cand.error = (cand.error or "") + f"; tests: {e}"
            except Exception as e:  # noqa: BLE001
                cand.error = str(e)
            finally:
                try:
                    if rt:
                        await rt.llm.close()
                    if br:
                        await br.close()
                    if store:
                        await store.close()
                except Exception:
                    pass
                if not keep_worktrees and cand.worktree_name:
                    try:
                        remove_worktree(root, cand.worktree_name, force=True, delete_branch=True)
                    except Exception:
                        pass
            score_candidate(cand)
            return cand

    candidates = list(await asyncio.gather(*[one(i) for i in range(n)]))
    ranked = sorted(candidates, key=lambda c: c.score, reverse=True)
    winner = ranked[0] if ranked else None
    return {
        "n": n,
        "prompt": prompt,
        "winner_index": winner.index if winner else None,
        "winner": winner.to_dict() if winner else None,
        "candidates": [c.to_dict() for c in ranked],
        "note": "Winner is NOT auto-applied to main tree. Inspect worktree_path and merge manually.",
    }
