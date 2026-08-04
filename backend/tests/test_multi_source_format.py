# -*- coding: utf-8 -*-
"""Multi-source aggregate: preserve tables; single-agent skips merge."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from backend.agent.loop_cluster import LoopClusterMixin


class _Agg(LoopClusterMixin):
    def __init__(self) -> None:
        self._should_stop = False
        self._status = []

    async def _push_status(self, *a, **k):
        self._status.append((a, k))

    async def _emit_progress(self, *a, **k):
        return None


def test_pipe_table_and_structured_detects_markdown_table():
    t = _Agg()
    draft = (
        "## 盘点\n\n"
        "| 项目 | 状态 | 备注 |\n"
        "| --- | --- | --- |\n"
        "| A | 完成 | ok |\n"
        "| B | 进行中 | - |\n"
    )
    assert t._pipe_table_score(draft) >= 6
    assert t._looks_like_structured_report(draft)


def test_multi_answer_false_when_table_present():
    t = _Agg()
    draft = "答案1 如下\n\n| x | y |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |\n"
    # table-heavy → structured → not "multi answer juxta"
    assert t._looks_like_structured_report(draft)
    assert not t._looks_like_multi_answer(draft)


def test_aggregate_skips_without_multi_pending():
    t = _Agg()

    async def _run():
        draft = "答案1：foo\n答案2：bar\n" + ("详" * 80)
        out = await t._maybe_aggregate_multi_source(
            llm_service=SimpleNamespace(chat=AsyncMock()),
            session_id=__import__("uuid").uuid4(),
            user_input="q",
            draft=draft,
            tool_rounds=3,
            last_tool_count=5,
            multi_pending=False,
        )
        return out

    out = asyncio.run(_run())
    assert out.startswith("答案1")


def test_aggregate_skips_structured_even_when_multi_pending():
    t = _Agg()
    draft = (
        "## 报告\n\n"
        "| 项 | 值 |\n"
        "| --- | --- |\n"
        "| a | 1 |\n"
        "| b | 2 |\n"
        "| c | 3 |\n\n"
        "结论：保持表格。\n"
    )

    async def _run():
        return await t._maybe_aggregate_multi_source(
            llm_service=SimpleNamespace(chat=AsyncMock()),
            session_id=__import__("uuid").uuid4(),
            user_input="q",
            draft=draft,
            tool_rounds=2,
            last_tool_count=3,
            multi_pending=True,
        )

    out = asyncio.run(_run())
    assert out == draft
