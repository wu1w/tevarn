"""Auto rules config + rewind side summary."""

from __future__ import annotations

from pathlib import Path

import pytest

from takton_code.agent.auto_classify import (
    classify_tool_call,
    clear_rules_cache,
    format_rules_summary,
    load_rules,
)
from takton_code.agent.file_history import FileHistory, format_rewind_side_panel
from takton_code.session.store import SessionStore


def test_load_rules_default(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TAKTON_CODE_HOME", str(tmp_path / "h"))
    clear_rules_cache()
    rs = load_rules(force_reload=True)
    assert rs.deny_command
    assert (tmp_path / "h" / "auto_rules.toml").is_file()
    s = format_rules_summary(rs)
    assert "deny_command=" in s


def test_project_overlay_deny(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TAKTON_CODE_HOME", str(tmp_path / "h"))
    clear_rules_cache()
    proj = tmp_path / "proj"
    (proj / ".takton").mkdir(parents=True)
    (proj / ".takton" / "auto_rules.toml").write_text(
        """
[[deny]]
on = "command"
pattern = '''\\becho\\s+FORBIDDEN\\b'''
""",
        encoding="utf-8",
    )
    rs = load_rules(project_root=proj, force_reload=True)
    c = classify_tool_call("run_shell", {"command": "echo FORBIDDEN"}, rules=rs)
    assert c.decision == "deny"


def test_side_panel_format():
    text = format_rewind_side_panel(
        {
            "point_id": "chk_abc",
            "scope": "code",
            "kind": "edit",
            "label": "t",
            "diff": {"would_restore": 2, "would_delete": 0, "unchanged": 1, "dirty_hint": 0},
            "diff_files": [
                {"path": "a.py", "status": "restore"},
                {"path": "b.py", "status": "same"},
            ],
            "restored": ["restored a.py"],
            "dry_run": False,
            "unified_diffs": [
                {
                    "path": "a.py",
                    "status": "restore",
                    "patch": "--- a/a.py (disk)\n+++ b/a.py (checkpoint)\n@@ -1 +1 @@\n-new\n+old\n",
                }
            ],
        }
    )
    assert "REWIND chk_abc" in text
    assert "restore=2" in text
    assert "[restore] a.py" in text
    assert "unified (disk → checkpoint)" in text
    assert "+old" in text


def test_make_unified_diff():
    from takton_code.agent.file_history import make_unified_diff

    p = make_unified_diff("f.txt", "a\nb\n", "a\nc\n")
    assert "f.txt" in p
    assert "-b" in p or "-b\n" in p
    assert "+c" in p or "+c\n" in p


def test_hot_reload_mtime(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TAKTON_CODE_HOME", str(tmp_path / "h"))
    clear_rules_cache()
    load_rules(force_reload=True)
    user = tmp_path / "h" / "auto_rules.toml"
    assert user.is_file()
    user.write_text(
        """
[settings]
allow_risk_max = 0.4

[[deny]]
on = "command"
pattern = "HOTRELOAD_MARKER"
""",
        encoding="utf-8",
    )
    # no force — mtime change should reload
    rs = load_rules(force_reload=False)
    c = classify_tool_call("run_shell", {"command": "echo HOTRELOAD_MARKER"}, rules=rs)
    assert c.decision == "deny"
    from takton_code.agent.auto_classify import rules_reload_info

    assert "reload#" in rules_reload_info()


@pytest.mark.asyncio
async def test_rewind_includes_side_summary(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TAKTON_CODE_HOME", str(tmp_path / "home"))
    db = tmp_path / "s.db"
    root = tmp_path / "proj"
    root.mkdir()
    f = root / "x.txt"
    f.write_text("old\nline2\n", encoding="utf-8", newline="\n")
    store = SessionStore(db)
    await store.open()
    sid = await store.create_session(project_root=str(root), title="t")
    hist = FileHistory(store, root, home=tmp_path / "home")
    p = await hist.create_point(sid, label="b", paths=["x.txt"])
    f.write_text("new\nline2\n", encoding="utf-8", newline="\n")
    res = await hist.rewind(sid, p.id, dry_run=True)
    assert res.get("side_summary")
    assert "PREVIEW" in res["side_summary"]
    assert res.get("unified_diffs")
    assert "disk → checkpoint" in res["side_summary"] or "unified" in res["side_summary"]
    res2 = await hist.rewind(sid, p.id)
    assert "REWIND" in (res2.get("side_summary") or "")
    got = f.read_text(encoding="utf-8")
    # normalize newlines for platform
    assert got.replace("\r\n", "\n").replace("\n\n", "\n") in (
        "old\nline2\n",
        "old\nline2",
    ) or "old" in got and "line2" in got
    await store.close()
