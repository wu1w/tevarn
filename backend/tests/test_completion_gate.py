import os

from backend.agent.completion_gate import (
    clear_policy_cache,
    evaluate_completion,
    get_policy,
    maybe_annotate_report,
    resolve_mode,
)


def setup_function():
    os.environ.pop("TAKTON_GROUNDING_MODE", None)
    clear_policy_cache()


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


_LONG_LIST_TASK = (
    "帮我仔细看看这个项目的完整目录结构是什么样的，"
    "并且尽量多描述各模块职责与依赖关系，不要只给一句话。"
)


def test_soft_only_glob_allows():
    """soft 默认：仅 glob 不硬拦，避免绑死强模型。"""
    os.environ["TAKTON_GROUNDING_MODE"] = "soft"
    clear_policy_cache()
    v = evaluate_completion(
        _LONG_LIST_TASK,
        ["glob"],
        "大致有 backend 和 frontend",
    )
    assert v.ok is True


def test_balanced_only_glob_nudges():
    os.environ["TAKTON_GROUNDING_MODE"] = "balanced"
    clear_policy_cache()
    v = evaluate_completion(
        _LONG_LIST_TASK,
        ["glob"],
        "大致有 backend 和 frontend",
    )
    assert v.ok is False
    assert v.reason == "only_glob"


def test_strong_model_relaxes_strict_to_balanced():
    os.environ["TAKTON_GROUNDING_MODE"] = "strict"
    clear_policy_cache()
    assert resolve_mode("claude-opus-4") == "balanced"
    assert get_policy("claude-opus-4").mode == "balanced"


def test_empty_tools_action_hard():
    v = evaluate_completion("请修 bug", [], "我想先想想")
    assert v.ok is False
    assert v.reason == "action_task_no_tools"
