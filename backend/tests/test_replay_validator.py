"""Phase 4.1：回放验证 + apply 门禁。"""
from __future__ import annotations

import uuid


def _good_skill_md(name: str = "demo_skill") -> str:
    return f"""---
name: {name}
description: 演示用可复用流程
source: tevarn-evolution
---

## 适用场景
当需要按固定步骤完成同类任务时。

## 步骤
1. 读取输入
2. 执行核心动作
3. 验证结果

## 验证方式
检查输出是否满足验收条件。

## 常见陷阱
不要跳过验证步骤。
"""


def _bad_skill_md() -> str:
    return "not a real skill\nshort"


def test_validate_structure_pass():
    from backend.evolution.replay_validator import validate_skill_replay

    asset = {
        "id": "x",
        "name": "demo_skill",
        "content": _good_skill_md(),
        "meta": {},
    }
    r = validate_skill_replay(asset)
    assert r["pass"] is True
    assert r["mode"] in ("heuristic", "trajectory")


def test_validate_structure_fail():
    from backend.evolution.replay_validator import validate_skill_replay

    asset = {"id": "y", "name": "bad", "content": _bad_skill_md(), "meta": {}}
    r = validate_skill_replay(asset)
    assert r["pass"] is False


def test_validate_high_tool_error_rate_fails():
    from backend.evolution.replay_validator import validate_skill_replay

    trace = [
        {"name": "file_read", "result": "ok"},
        {"name": "file_write", "result": "[Error] boom"},
        {"name": "file_write", "result": "[Error] boom2"},
        {"name": "file_write", "result": "[Error] boom3"},
    ]
    asset = {
        "id": "z",
        "name": "demo_skill",
        "content": _good_skill_md("err_skill"),
        "meta": {"tool_trace": trace},
    }
    r = validate_skill_replay(asset)
    assert r["pass"] is False
    assert r["metrics"]["tool_error_rate"] >= 0.5


def test_apply_blocked_when_replay_fails(tmp_path, monkeypatch):
    """回放失败的 draft 不能 apply。"""
    from backend.evolution import store
    from backend.evolution.replay_validator import assert_replay_allows_apply

    # isolate store db if possible
    asset = store.create_asset(
        kind="skill",
        name=f"bad_apply_{uuid.uuid4().hex[:6]}",
        summary="bad",
        content=_bad_skill_md(),
        source="auto",
        status="draft",
        meta={},
    )
    gate = assert_replay_allows_apply(asset)
    assert gate["ok"] is False
    assert gate["replay"]["pass"] is False


def test_apply_allows_good_skill():
    from backend.evolution import store
    from backend.evolution.replay_validator import assert_replay_allows_apply

    asset = store.create_asset(
        kind="skill",
        name=f"good_apply_{uuid.uuid4().hex[:6]}",
        summary="good flow",
        content=_good_skill_md(f"good_{uuid.uuid4().hex[:4]}"),
        source="auto",
        status="draft",
        meta={},
    )
    gate = assert_replay_allows_apply(asset)
    assert gate["ok"] is True
    assert gate["replay"]["pass"] is True


def test_sanitize_tool_error_has_next_step():
    from backend.agent.loop import _sanitize_tool_error

    msg = _sanitize_tool_error("file_read", FileNotFoundError("nope"))
    assert "[Error]" in msg
    assert "下一步" in msg
