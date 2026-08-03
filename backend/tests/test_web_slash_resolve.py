# -*- coding: utf-8 -*-
from backend.services.slash_commands import build_help_text, resolve_command


def test_resolve_basic():
    cmd, args = resolve_command("/help")
    assert cmd is not None
    assert cmd.name in ("help", "commands")
    cmd2, a2 = resolve_command("/status")
    assert cmd2 is not None and cmd2.name == "status"
    cmd3, a3 = resolve_command("/model gpt-test")
    assert cmd3 is not None and cmd3.name == "model" and a3 == "gpt-test"
    cmd4, _ = resolve_command("hello")
    assert cmd4 is None


def test_help_mentions_web():
    t = build_help_text()
    assert "/help" in t
    assert "Web" in t or "聊天" in t
