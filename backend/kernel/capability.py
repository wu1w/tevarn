"""CapabilityToken —— 能力令牌。

.. deprecated:: P0-A
    权威实现：Rust ``takton_kernel::capability``。本模块供单测与反序列化兼容。
    生产路径令牌由 host ``issue_token`` 签发。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field


class CapabilityEscalationError(PermissionError):
    """试图通过 narrowing 扩大能力集 —— 一律拒绝。"""


@dataclass(frozen=True)
class CapabilityToken:
    capabilities: frozenset[str]
    process_id: str = ""
    parent_token_id: str | None = None
    expires_at: float | None = None  # None = 不过期（W2 接入强制检查）
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    issued_at: float = field(default_factory=time.time)

    def allows(self, capability: str) -> bool:
        if self.is_expired:
            return False
        if capability in self.capabilities or "*" in self.capabilities:
            return True
        try:
            from backend.agent.grant_store import tool_matches_crew_caps

            return tool_matches_crew_caps(capability, self.capabilities)
        except Exception:
            return False

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and time.time() >= self.expires_at

    def narrow(
        self,
        subset: list[str] | set[str] | frozenset[str],
        *,
        process_id: str = "",
        expires_at: float | None = None,
    ) -> "CapabilityToken":
        """产生更严格的子 Token。subset 必须是当前能力的子集（'*' 通配时任意子集合法）。

        子 Token 的过期时间不得晚于父 Token（取两者更早者）。
        """
        requested = frozenset(subset)
        if "*" not in self.capabilities:
            extra = requested - self.capabilities
            if extra:
                raise CapabilityEscalationError(
                    f"narrowing 不允许扩大能力：{sorted(extra)} 不在父 Token 能力集中"
                )
        effective_expiry = expires_at
        if self.expires_at is not None:
            effective_expiry = (
                min(self.expires_at, expires_at) if expires_at is not None else self.expires_at
            )
        return CapabilityToken(
            capabilities=requested,
            process_id=process_id,
            parent_token_id=self.id,
            expires_at=effective_expiry,
        )

    def to_dict(self, *, sign: bool = True) -> dict:
        data = {
            "id": self.id,
            "process_id": self.process_id,
            "parent_token_id": self.parent_token_id,
            "capabilities": sorted(self.capabilities),
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
        }
        if sign:
            from backend.kernel.signing import sign_token_dict

            data["signature"] = sign_token_dict(data)
        return data

    @classmethod
    def from_dict(cls, data: dict, *, verify: bool = True) -> "CapabilityToken":
        """反序列化。verify=True（默认）时签名缺失/不匹配即拒绝（防伪造）；
        verify=False 仅用于读取历史无签名数据（向后兼容窗口）。"""
        if verify:
            from backend.kernel.signing import TokenSignatureError, verify_token_dict

            if not verify_token_dict(data):
                raise TokenSignatureError(
                    "Token 签名验证失败——拒绝反序列化不可信来源的能力令牌"
                )
        return cls(
            capabilities=frozenset(data.get("capabilities") or []),
            process_id=str(data.get("process_id") or ""),
            parent_token_id=data.get("parent_token_id"),
            expires_at=data.get("expires_at"),
            id=str(data.get("id") or uuid.uuid4().hex[:16]),
            issued_at=float(data.get("issued_at") or time.time()),
        )
