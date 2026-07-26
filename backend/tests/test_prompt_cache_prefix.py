"""T4 前置：可缓存前缀的稳定性契约。

背景：Volatile 层含秒级时间戳。若并入 messages[0] 的 system 块，
每个新用户轮次 system 都不同 —— Anthropic 的 system cache 与 OpenAI 的自动
前缀缓存会在第一个 block 就失配，整段历史前缀缓存全部作废。
本文件锁定「system 块跨轮次字节相同」这一前提；破坏它则 T4 的 cache_control 全部白做。
"""

import pytest

from backend.agent.system_prompt import build_system_prompt, merge_prompt_parts


def _parts(**kw):
    return build_system_prompt(
        identity="You are Takton.",
        tools_enabled=["file_read"],
        model="claude-opus-5",
        session_id="abcdef12-0000-0000-0000-000000000000",
        **kw,
    )


def test_volatile_layer_carries_the_unstable_bits():
    """确认时间戳确实在 volatile 层——本测试是下面几条的前提。"""
    parts = _parts()
    assert "Current time:" in parts["volatile"]
    assert "Current time:" not in parts["stable"]
    assert "Current time:" not in parts["context"]


def test_merged_system_is_unstable_when_volatile_included():
    """回归护栏：旧行为（include_volatile=True）确实不可缓存。

    若某次重构让这条断言失败（时间戳被移出 volatile），说明缓存前提变了，
    应同步复核 merge_prompt_parts 的调用方。
    """
    import time

    a = merge_prompt_parts(_parts(), include_volatile=True)
    time.sleep(1.05)
    b = merge_prompt_parts(_parts(), include_volatile=True)
    assert a != b


def test_merged_system_is_byte_stable_without_volatile():
    """T4 前置的核心断言：跨轮次 system 块必须逐字节相同。"""
    import time

    a = merge_prompt_parts(_parts(), include_volatile=False)
    time.sleep(1.05)
    b = merge_prompt_parts(_parts(), include_volatile=False)
    assert a == b
    assert "Current time:" not in a
    # 稳定层内容不能被一起丢掉
    assert "Takton" in a


def test_memory_block_also_kept_out_of_cached_prefix():
    """记忆会随写入变化，同样不能进可缓存前缀。"""
    a = merge_prompt_parts(
        _parts(memory_block="user likes tabs"), include_volatile=False
    )
    b = merge_prompt_parts(
        _parts(memory_block="user likes spaces"), include_volatile=False
    )
    assert a == b


def test_volatile_content_is_not_lost():
    """剥离不等于丢弃：volatile 仍须完整可用，供调用方挂到 messages 尾部。"""
    parts = _parts(memory_block="user likes tabs")
    assert "user likes tabs" in parts["volatile"]
    assert "Current time:" in parts["volatile"]
    assert "claude-opus-5" in parts["volatile"]


@pytest.mark.asyncio
async def test_context_manager_puts_volatile_before_user_message(monkeypatch):
    """ContextManager 组装后：messages[0] 稳定，volatile 落在用户问题之前。"""
    from backend.agent.context import ContextManager

    # 非 None 的 repo 才会走 _collect_ctx_items 分支（下面 monkeypatch 掉真实读取）
    cm = ContextManager(ctx_item_repo=object())

    async def _no_ctx(_sid):
        return {
            "identity": None,
            "user_system_prompt": None,
            "context_files": None,
            "memory_block": "remembered fact",
            "accessed_items": [],
        }

    monkeypatch.setattr(cm, "_collect_ctx_items", _no_ctx)

    import uuid as _uuid

    messages, _, _ = await cm.build_messages(
        session_id=_uuid.uuid4(),
        user_input="hello",
        history=[],
        fallback_config={},
    )

    assert messages[0]["role"] == "system"
    assert "Current time:" not in messages[0]["content"]

    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == "hello"

    volatile_msgs = [
        m
        for m in messages
        if m["role"] == "system" and "Current time:" in str(m.get("content") or "")
    ]
    assert len(volatile_msgs) == 1, "volatile 应恰好出现一次，且不在 messages[0]"
    assert "remembered fact" in volatile_msgs[0]["content"]
