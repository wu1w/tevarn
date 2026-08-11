# -*- coding: utf-8 -*-
"""Decisiveness gates: direct intent, thrash fingerprint, clarify strip."""
from __future__ import annotations

from types import SimpleNamespace

from backend.agent.decisive import (
    family_bucket,
    is_tool_thrash,
    orchestration_cap_results,
    thrash_fingerprint,
    thrash_force_final_text,
    tool_round_fingerprint,
)
from backend.agent.direct_intent import (
    filter_clarify_from_tools,
    is_direct_execute_intent,
    last_user_text,
)


def test_direct_execute_patterns():
    assert is_direct_execute_intent("你按照我的指令来，先清理分支")
    assert is_direct_execute_intent("直接执行，不要问了")
    assert is_direct_execute_intent("just do it, don't ask")
    assert not is_direct_execute_intent("帮我看看这个方案怎么样？")
    assert not is_direct_execute_intent("【系统·编制自动回调】工单结束")


def test_filter_clarify_from_tools():
    tools = [
        {"type": "function", "function": {"name": "grep"}},
        {"type": "function", "function": {"name": "clarify"}},
        {"type": "function", "function": {"name": "file_read"}},
    ]
    out = filter_clarify_from_tools(tools, user_text="按我说的直接做")
    names = [(t.get("function") or {}).get("name") for t in out]
    assert "clarify" not in names
    assert "grep" in names

    out2 = filter_clarify_from_tools(tools, user_text="你觉得该怎么设计？")
    assert any((t.get("function") or {}).get("name") == "clarify" for t in out2)


def test_last_user_text():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "直接执行合并"},
    ]
    assert "直接执行" in last_user_text(msgs)


def test_tool_round_fingerprint_stable():
    a = SimpleNamespace(name="grep", arguments={"pattern": "foo", "path": "x.py"})
    b = SimpleNamespace(name="grep", arguments={"path": "x.py", "pattern": "foo"})
    assert tool_round_fingerprint([a]) == tool_round_fingerprint([b])
    c = SimpleNamespace(name="grep", arguments={"pattern": "bar", "path": "x.py"})
    assert tool_round_fingerprint([a]) != tool_round_fingerprint([c])


def test_is_tool_thrash():
    assert is_tool_thrash("abc", "abc", thrash_streak=1, force_after=2)
    assert not is_tool_thrash("abc", "abc", thrash_streak=0, force_after=2)
    assert not is_tool_thrash("abc", "def", thrash_streak=5, force_after=2)


def test_family_bucket_orch_and_result_load():
    crews = [
        SimpleNamespace(name="crew_steward", id=f"c{i}", arguments={"name": f"e{i}"})
        for i in range(7)
    ]
    one_read = SimpleNamespace(name="file_read", id="r1", arguments={"path": "a.py"})
    assert family_bucket(crews + [one_read]) == "orch_heavy"
    rls = [
        SimpleNamespace(name="result_load", id=f"l{i}", arguments={"id": f"x{i}"})
        for i in range(3)
    ]
    assert family_bucket(rls) == "result_load_heavy"
    assert family_bucket([one_read]) == ""


def test_family_bucket_command_family():
    cmds = [
        SimpleNamespace(name="command", id=f"c{i}", arguments={"command": f"cmd /c echo {i}"})
        for i in range(4)
    ]
    assert family_bucket(cmds) == "command_family"
    assert thrash_fingerprint(cmds) == "fam:command_family"
    assert "终端" in thrash_force_final_text(family="fam:command_family") or "Command" in thrash_force_final_text(
        family="fam:command_family"
    )


def test_family_bucket_cargo_and_probe():
    cargo = [
        SimpleNamespace(name="command", id=f"c{i}", arguments={"command": "cargo check"})
        for i in range(3)
    ]
    assert family_bucket(cargo) == "cargo_verify"
    probe = [
        SimpleNamespace(name="command", id=f"p{i}", arguments={"command": "where python"})
        for i in range(3)
    ]
    assert family_bucket(probe) == "shell_probe"


def test_thrash_fingerprint_collapses_orch_heavy():
    a = [
        SimpleNamespace(name="crew_steward", id="1", arguments={"name": "alice"}),
        SimpleNamespace(name="crew_steward", id="2", arguments={"name": "bob"}),
        SimpleNamespace(name="file_read", id="3", arguments={"path": "a.py"}),
    ]
    b = [
        SimpleNamespace(name="crew_steward", id="9", arguments={"name": "carol"}),
        SimpleNamespace(name="crew_steward", id="8", arguments={"name": "dave"}),
        SimpleNamespace(name="grep", id="7", arguments={"pattern": "x"}),
    ]
    assert thrash_fingerprint(a) == thrash_fingerprint(b) == "fam:orch_heavy"
    assert thrash_force_final_text(family="fam:orch_heavy").startswith("【强制收束")


def test_orchestration_cap_results_keeps_first_n():
    calls = [
        SimpleNamespace(name="crew_steward", id=f"c{i}", arguments={"name": f"e{i}"})
        for i in range(5)
    ] + [SimpleNamespace(name="file_read", id="r1", arguments={"path": "a.py"})]
    capped = orchestration_cap_results(calls, max_orch=2)
    assert set(capped.keys()) == {"c2", "c3", "c4"}
    assert "c0" not in capped and "c1" not in capped
    assert "r1" not in capped
    assert "[Orchestration cap]" in capped["c2"]
