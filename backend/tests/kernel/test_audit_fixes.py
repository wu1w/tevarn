"""审计修复项测试：subagent 父链 / Token HMAC / 能力显式化。"""

from __future__ import annotations

import asyncio

import pytest

from backend.kernel import (
    AgentKernel,
    CapabilityEscalationError,
    CapabilityToken,
    KernelPermissionError,
)

# ── subagent 父进程链（审计项 #1）──

def test_subagent_child_narrowed_from_parent() -> None:
    """子进程显式继承父能力集的子集；父为显式集时 narrowing 生效。"""
    async def go():
        k = AgentKernel()
        parent = await k.create_process("main", capabilities=["file_read", "grep", "terminal"])
        # 模拟 subagent_runner 接线后的子进程创建
        child = await k.create_process(
            "sub:researcher", parent_id=parent.id, capabilities=["file_read", "grep"]
        )
        assert child.parent_id == parent.id
        await k.mediate(child.id, "tool_call", "file_read")
        with pytest.raises(KernelPermissionError):
            await k.mediate(child.id, "tool_call", "terminal")
        # 子试图超集 → 数据结构级拒绝
        with pytest.raises(CapabilityEscalationError):
            await k.create_process("sub:evil", parent_id=parent.id, capabilities=["browser"])

    asyncio.run(go())


def test_subagent_child_of_compat_parent_inherits_none() -> None:
    """父为兼容模式（None）→ 子未指定时继承 None（当前默认路径）。"""
    async def go():
        k = AgentKernel()
        parent = await k.create_process("main")
        child = await k.create_process("sub:x", parent_id=parent.id)
        assert child.capabilities is None
        d = await k.mediate(child.id, "tool_call", "anything")
        assert d.allowed and not d.capability_checked

    asyncio.run(go())


# ── Token HMAC 签名（审计项 #5）──

def test_token_signed_roundtrip() -> None:
    tok = CapabilityToken(capabilities=frozenset({"file_read"}), process_id="p1")
    data = tok.to_dict()
    assert "signature" in data and len(data["signature"]) == 64
    restored = CapabilityToken.from_dict(data)
    assert restored.capabilities == tok.capabilities


def test_token_forged_signature_rejected() -> None:
    from backend.kernel.signing import TokenSignatureError

    tok = CapabilityToken(capabilities=frozenset({"file_read"}), process_id="p1")
    data = tok.to_dict()
    # 伪造：篡改能力集（签名不变 → 验签失败）
    forged = {**data, "capabilities": ["*"]}
    with pytest.raises(TokenSignatureError):
        CapabilityToken.from_dict(forged)
    # 伪造：无签名
    unsigned = {k: v for k, v in data.items() if k != "signature"}
    with pytest.raises(TokenSignatureError):
        CapabilityToken.from_dict(unsigned)


def test_token_unsigned_legacy_readable() -> None:
    """历史无签名数据：verify=False 兼容窗口可读。"""
    legacy = {"capabilities": ["file_read"], "process_id": "p1", "issued_at": 1.0}
    tok = CapabilityToken.from_dict(legacy, verify=False)
    assert "file_read" in tok.capabilities


def test_token_signature_stable_across_instances() -> None:
    """同字段不同 Token 实例 → 同签名（密钥进程内一致）。"""
    t1 = CapabilityToken(capabilities=frozenset({"a"}), process_id="p", id="fixed", issued_at=1.0)
    t2 = CapabilityToken(capabilities=frozenset({"a"}), process_id="p", id="fixed", issued_at=1.0)
    assert t1.to_dict()["signature"] == t2.to_dict()["signature"]


# ── 能力显式化开关（审计项 #2 前置）──

def test_explicit_capabilities_enables_narrowing() -> None:
    """显式全集父进程 → 子进程 narrow 语义真实生效（区别于兼容模式）。"""
    async def go():
        k = AgentKernel()
        all_tools = ["file_read", "grep", "terminal", "browser"]
        parent = await k.create_process("main", capabilities=all_tools)
        child = await k.create_process(
            "sub:worker", parent_id=parent.id, capabilities=["file_read"]
        )
        d = await k.mediate(child.id, "tool_call", "file_read")
        assert d.allowed and d.capability_checked  # 显式检查生效
        with pytest.raises(KernelPermissionError):
            await k.mediate(child.id, "tool_call", "browser")

    asyncio.run(go())
