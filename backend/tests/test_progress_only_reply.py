# -*- coding: utf-8 -*-
"""Progress-only final reply + http_fetch thrash family."""
from __future__ import annotations

from backend.agent.decisive import (
    family_bucket,
    is_progress_only_reply,
    thrash_force_final_text,
)


class _TC:
    def __init__(self, name: str, **kwargs):
        self.name = name
        self.arguments = kwargs


def test_progress_only_detects_fetch_chatter():
    assert is_progress_only_reply("用 Python 拉完整 README 并解码。")
    assert is_progress_only_reply("页面 HTML 太长被截断了，直接拉 README 原文看排版。")
    assert is_progress_only_reply("先打开你们的 GitHub 项目页，再看排版和呈现。")
    assert not is_progress_only_reply(
        "# 评价\n\n排版整体清晰：居中 logo + 徽章 + 分区说明。\n\n"
        "## 优点\n- 信息层次清楚\n\n## 建议\n- 首屏加安装一键复制"
    )


def test_http_fetch_family_bucket():
    calls = [
        _TC("http", url="https://github.com/a"),
        _TC("web_search", query="tevarn"),
    ]
    assert family_bucket(calls) == "http_fetch"
    msg = thrash_force_final_text(family="http_fetch")
    assert "禁止" in msg and "工具" in msg


def test_command_family_still_buckets_mixed_shell():
    """Root cause of 46×command: different argv still one family."""
    calls = [
        _TC("command", command="netsh winhttp set proxy 127.0.0.1:3128"),
        _TC("command", command="reg add HKCU\\... /v ProxyEnable /t REG_DWORD /d 1"),
        _TC("command", command="start ChatGPT.exe --proxy-server=..."),
    ]
    assert family_bucket(calls) == "command_family"
