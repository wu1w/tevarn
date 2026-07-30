"""Channel Gateway 入站面回归测试（Phase 1.1）。

IM 入站消息是外部不可信输入，直达 agent loop，是公开后的第一攻击面。
本测试冻结当前 gateway 的入站处理不变量：短时去重、平台探测、QQ @标记清理。

取自 backend/services/channel_gateway.py:
- ChannelGateway._is_duplicate_message
- ChannelGateway._detect_platform_from_data
- QQ @bot 清理正则 r'<@!\\d+>'

Phase 5 D1：sanitize_channel_ingress 长度/NUL 门禁已落地；
激进 prompt-injection 改写仍不做（避免误伤代码块）。
"""

from __future__ import annotations

import re

import pytest

from backend.services.channel_gateway import (
    ChannelGateway,
    ChannelIngressRejected,
    sanitize_channel_ingress,
)

# ── 去重：platform:chat:msg_id 短时去重 ───────────────────────────────

def test_gateway_dedup_defaults() -> None:
    gw = ChannelGateway()
    assert gw._seen_ttl_s == 300.0
    assert gw._seen_max == 2000


@pytest.mark.asyncio
async def test_no_msg_id_never_deduped() -> None:
    gw = ChannelGateway()
    # 无 msg_id 不拦截（否则会误吞所有无 id 平台的消息）
    assert await gw._is_duplicate_message("tg", "chat1", "") is False
    assert await gw._is_duplicate_message("tg", "chat1", "") is False


@pytest.mark.asyncio
async def test_duplicate_same_message_detected() -> None:
    gw = ChannelGateway()
    assert await gw._is_duplicate_message("tg", "chat1", "m1") is False
    # 同一 (platform, chat, msg_id) 二次到达 → 判重
    assert await gw._is_duplicate_message("tg", "chat1", "m1") is True


@pytest.mark.asyncio
async def test_distinct_messages_not_deduped() -> None:
    gw = ChannelGateway()
    assert await gw._is_duplicate_message("tg", "chat1", "m1") is False
    assert await gw._is_duplicate_message("tg", "chat1", "m2") is False
    # 不同 chat 即便同 msg_id 也应独立
    assert await gw._is_duplicate_message("tg", "chat2", "m1") is False


# ── 平台探测：注入的 _platform 优先且被清理 ──────────────────────────

def test_detect_platform_uses_injected_field_and_pops_it() -> None:
    gw = ChannelGateway()
    data = {"_platform": "telegram", "content": "hi"}
    assert gw._detect_platform_from_data(data) == "telegram"
    # _platform 是内部注入字段，不应泄漏给下游
    assert "_platform" not in data


# ── QQ @bot 清理正则冻结 ──────────────────────────────────────────────

def test_qq_at_mention_strip_regex() -> None:
    # gateway 用 re.sub(r'<@!\d+>', '', content) 清理 QQ @标记
    cleaned = re.sub(r"<@!\d+>", "", "<@!123456> hello world").strip()
    assert cleaned == "hello world"


# ── Phase 5 D1：入站长度 / NUL ────────────────────────────────────────

def test_ingress_strips_nul() -> None:
    assert sanitize_channel_ingress("a\x00b", max_chars=100) == "ab"


def test_ingress_rejects_too_long() -> None:
    with pytest.raises(ChannelIngressRejected, match="too_long"):
        sanitize_channel_ingress("x" * 100, max_chars=50)


def test_ingress_rejects_non_printable() -> None:
    with pytest.raises(ChannelIngressRejected, match="non_printable"):
        sanitize_channel_ingress("\x01\x02\x03", max_chars=100)


def test_ingress_allows_normal_text_and_newlines() -> None:
    body = "hello\nworld\t!"
    assert sanitize_channel_ingress(body, max_chars=1000) == body
