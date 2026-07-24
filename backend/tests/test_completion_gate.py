from backend.agent.completion_gate import evaluate_completion


def test_fix_only_glob_incomplete():
    v = evaluate_completion(
        "请修这个 off-by-one bug 并跑 pytest",
        ["glob", "glob", "grep"],
        "已经修好了",
    )
    assert v.ok is False
    assert "edit" in v.nudge or "file_write" in v.nudge


def test_fix_with_edit_ok():
    v = evaluate_completion(
        "修 bug",
        ["file_read", "edit", "command"],
        "已修复并通过测试",
    )
    assert v.ok is True


def test_build_needs_writes():
    v = evaluate_completion(
        "从零建一个 gen_pkg 包并写 tests",
        ["glob"],
        "包建好了",
    )
    assert v.ok is False


def test_build_with_writes_ok():
    v = evaluate_completion(
        "创建 package",
        ["file_write", "file_write", "file_write", "command"],
        "pytest passed",
    )
    assert v.ok is True


def test_followup_budget():
    v = evaluate_completion("修 bug", ["glob"], "done", max_followups_done=2)
    assert v.ok is True
