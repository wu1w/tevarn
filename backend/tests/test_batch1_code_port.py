"""Batch1: doom_loop / hunks / diff_engine / best_of_n / multimodal."""
from __future__ import annotations

from pathlib import Path

from backend.agent.best_of_n import BonCandidate, pick_winner, score_candidate, summarize_bon
from backend.agent.doom_loop import DoomLoopGuard
from backend.agent.hunks import apply_selected_hunks, parse_unified_hunks
from backend.agent.multimodal_parts import build_user_content, find_image_paths
from backend.agent.robust import ToolRepeatGuard
from backend.tools.diff_engine import DiffEngine, FileChange


def test_doom_loop_trips_on_repeat():
    g = DoomLoopGuard(threshold=3)
    assert g.record("search", {"q": "a"}) is False
    assert g.record("search", {"q": "a"}) is False
    assert g.record("search", {"q": "a"}) is True
    assert g.tripped is True
    assert g.streak == 3


def test_doom_loop_resets_on_arg_change():
    g = DoomLoopGuard(threshold=3)
    g.record("search", {"q": "a"})
    g.record("search", {"q": "a"})
    assert g.record("search", {"q": "b"}) is False
    assert g.streak == 1
    assert g.tripped is False


def test_tool_repeat_guard_observe_calls():
    g = ToolRepeatGuard(max_repeat=3)
    assert g.observe_calls([("edit", {"path": "x"})]) is False
    assert g.observe_calls([("edit", {"path": "x"})]) is False
    assert g.observe_calls([("edit", {"path": "x"})]) is True


def test_hunks_parse_and_apply(tmp_path: Path):
    original = "line1\nline2\nline3\n"
    patch = """--- a/f
+++ b/f
@@ -1,3 +1,3 @@
 line1
-line2
+LINE2
 line3
"""
    hunks = parse_unified_hunks(patch)
    assert len(hunks) == 1
    new, errs = apply_selected_hunks(original, hunks, [0])
    assert "LINE2" in new
    assert "line2" not in new.splitlines() or "LINE2" in new


def test_diff_engine_snapshot_and_diff(tmp_path: Path):
    eng = DiffEngine(tmp_path)
    f = tmp_path / "a.txt"
    f.write_text("hello\n", encoding="utf-8")
    eng.begin_turn()
    eng.snapshot_before("a.txt")
    f.write_text("hello\nworld\n", encoding="utf-8")
    ch = eng.record_after("a.txt")
    assert ch is not None
    assert ch.op == "modify"
    d = ch.unified_diff()
    assert "+world" in d or "world" in d
    eng.revert("a.txt")
    assert f.read_text(encoding="utf-8") == "hello\n"


def test_best_of_n_scoring():
    c1 = BonCandidate(index=0, final_text="ok", test_ok=True, changes_summary="modify x")
    c2 = BonCandidate(index=1, final_text="", error="boom")
    score_candidate(c1)
    score_candidate(c2)
    w = pick_winner([c1, c2])
    assert w is not None and w.index == 0
    s = summarize_bon([c1, c2], prompt="hi")
    assert s["winner_index"] == 0
    assert s["enabled_runtime"] is False


def test_multimodal_finds_local_image(tmp_path: Path):
    img = tmp_path / "shot.png"
    # minimal PNG
    import base64

    img.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
    )
    text = f"please look at {img.name}"
    paths = find_image_paths(text, tmp_path)
    assert any(p.name == "shot.png" for p in paths)
    content = build_user_content(text, tmp_path, enabled=True, max_images=2)
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert any(p.get("type") == "image_url" for p in content)
