"""CapabilityToken —— 能力令牌（阶段 1 / W1 雏形，W2 完善过期与序列化签名）。

设计要点：
- 可 narrowing：narrow(subset) 只能产生更严格的 Token，试图扩大能力抛异常。
- 父进程授予子进程时自动 narrow 到父能力子集（单调递减，不可提权）。
- W1 先落地数据模型与 narrowing 语义；签名/过期在 W2 补齐（接口已预留）。
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
        return capability in self.capabilities or "*" in self.capabilities

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

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "process_id": self.process_id,
            "parent_token_id": self.parent_token_id,
            "capabilities": sorted(self.capabilities),
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CapabilityToken":
        return cls(
            capabilities=frozenset(data.get("capabilities") or []),
            process_id=str(data.get("process_id") or ""),
            parent_token_id=data.get("parent_token_id"),
            expires_at=data.get("expires_at"),
            id=str(data.get("id") or uuid.uuid4().hex[:16]),
            issued_at=float(data.get("issued_at") or time.time()),
        )
