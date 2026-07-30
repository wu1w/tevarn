"""Phase 3.1 memory_bus：写入路由 + supersede 后 recall 不命中旧版。"""
from __future__ import annotations

import uuid

import pytest


@pytest.mark.asyncio
async def test_remember_graph_and_recall():
    from backend.services import memory_bus

    title = f"ppt-style-{uuid.uuid4().hex[:8]}"
    content = "公司 PPT 风格：深蓝封面 + 少字多图 + 页脚含版本号"
    wr = await memory_bus.remember(
        "preference",
        content,
        title=title,
        tags=["ppt", "style"],
        source="agent",
    )
    assert wr.ok, wr.message
    assert wr.source == "graph"
    assert wr.id

    hits = await memory_bus.recall("PPT 风格 深蓝", kinds=["preference", "graph"], top_k=10)
    assert any(title in (h.title or "") or content[:20] in h.content for h in hits)


@pytest.mark.asyncio
async def test_graph_supersede_hides_old():
    from backend.services import memory_bus

    title = f"policy-{uuid.uuid4().hex[:8]}"
    wr = await memory_bus.remember(
        "decision",
        "旧政策：默认允许",
        title=title,
        source="agent",
    )
    assert wr.ok
    old_id = wr.id

    sr = await memory_bus.supersede(
        f"graph:{old_id}",
        "新政策：默认拒绝敏感路径",
        approved_by="test",
    )
    assert sr.ok, sr.message

    hits = await memory_bus.recall(title, kinds=["decision", "graph"], top_k=20)
    # 旧内容不应再以高置信出现
    for h in hits:
        if h.id == old_id:
            pytest.fail("superseded node should not appear in recall")
        assert "旧政策" not in h.content or h.id == sr.id


@pytest.mark.asyncio
async def test_entity_remember_and_supersede():
    from backend.services import memory_bus

    name = f"entity-{uuid.uuid4().hex[:6]}"
    wr = await memory_bus.remember(
        "entity",
        "客户 A 偏好横向幻灯片",
        title=name,
        meta={"entity_type": "preference"},
    )
    assert wr.ok, wr.message
    assert wr.source == "entity"

    sr = await memory_bus.supersede(
        f"entity:{wr.id}",
        "客户 A 偏好竖版海报",
        approved_by="test",
    )
    assert sr.ok, sr.message

    hits = await memory_bus.recall(name, kinds=["entity"], top_k=10)
    assert any("竖版" in h.content for h in hits)
    assert not any(h.id == wr.id for h in hits)


def test_court_capability_unknown_process():
    from backend.kernel.permission_court import decide_capability

    d = decide_capability(
        process_id="nope",
        action="tool_call",
        target="file_read",
        proc=None,
    )
    assert d.verdict == "deny"
    assert d.layer == "capability"
    assert "tool" in d.to_audit()
    assert "args_digest" in d.to_audit()
    assert "matched_rule" in d.to_audit()


@pytest.mark.asyncio
async def test_wiki_remember_and_recall():
    """Phase 3.1 补全：wiki 经总线真写 wiki_entities。"""
    from backend.services import memory_bus

    title = f"wiki-bus-{uuid.uuid4().hex[:8]}"
    content = "总线写入的 Wiki 概念：统一 Run 与权限法院"
    wr = await memory_bus.remember(
        "wiki",
        content,
        title=title,
        meta={"entity_type": "concept"},
        source="agent",
    )
    assert wr.ok, wr.message
    assert wr.source == "wiki"
    assert wr.id

    hits = await memory_bus.recall(title, kinds=["wiki"], top_k=10)
    assert any(h.source == "wiki" and title in (h.title or "") for h in hits)
    assert any("权限法院" in h.content or "统一 Run" in h.content for h in hits)

    # 同名再写 → version 递增
    wr2 = await memory_bus.remember(
        "wiki",
        content + "（修订）",
        title=title,
        meta={"entity_type": "concept"},
    )
    assert wr2.ok
    assert wr2.version >= 2


@pytest.mark.asyncio
async def test_court_decide_tool_disabled():
    from backend.core.config import settings
    from backend.kernel.permission_court import decide_tool

    prev = getattr(settings, "agent_permission_enabled", True)
    try:
        settings.agent_permission_enabled = False
        d = await decide_tool("file_read", {"path": "README.md"})
        assert d.verdict == "allow"
        assert d.layer == "disabled"
    finally:
        settings.agent_permission_enabled = prev


@pytest.mark.asyncio
async def test_court_audit_fields_present():
    from backend.kernel.permission_court import decide_tool

    d = await decide_tool("file_read", {"path": "README.md", "_session_id": "x"})
    audit = d.to_audit()
    assert audit["tool"] == "file_read"
    assert audit["args_digest"].startswith("sha256:")
    assert audit["verdict"] in ("allow", "deny", "ask")
    assert audit["layer"]
    assert audit["matched_rule"]
