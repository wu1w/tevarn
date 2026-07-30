"""Daily LLM token quota (in-process)."""
from __future__ import annotations

import time
from typing import Any


class DailyTokenQuota:
    """进程内日配额记账（单 worker alpha 足够；多 worker 后续可 Redis）。"""

    def __init__(self) -> None:
        self._day: str = ""
        self._global_used: int = 0
        self._by_identity: dict[str, int] = {}

    def _roll(self) -> None:
        day = time.strftime("%Y-%m-%d", time.localtime())
        if day != self._day:
            self._day = day
            self._global_used = 0
            self._by_identity.clear()

    def used_global(self) -> int:
        self._roll()
        return self._global_used

    def used_identity(self, identity_id: str | None) -> int:
        self._roll()
        if not identity_id:
            return 0
        return int(self._by_identity.get(str(identity_id), 0))

    def charge(self, identity_id: str | None, amount: int) -> None:
        if amount <= 0:
            return
        self._roll()
        self._global_used += int(amount)
        if identity_id:
            k = str(identity_id)
            self._by_identity[k] = int(self._by_identity.get(k, 0)) + int(amount)

    def would_exceed(
        self,
        identity_id: str | None,
        *,
        global_limit: int,
        per_identity_limit: int,
        estimated: int = 0,
    ) -> str | None:
        """返回拒绝原因；None = 可通过。limit<=0 表示不限制。"""
        self._roll()
        est = max(0, int(estimated or 0))
        # 已达硬顶即拒；或预估会顶穿
        if global_limit > 0 and (
            self._global_used >= global_limit
            or self._global_used + est > global_limit
        ):
            return "global_daily_quota"
        if per_identity_limit > 0 and identity_id:
            used_i = self.used_identity(identity_id)
            if used_i >= per_identity_limit or used_i + est > per_identity_limit:
                return "identity_daily_quota"
        return None

    def snapshot(
        self, *, global_limit: int, per_identity_limit: int
    ) -> dict[str, Any]:
        self._roll()
        by = [
            {
                "identity_id": iid,
                "used": used,
                "limit": per_identity_limit if per_identity_limit > 0 else None,
            }
            for iid, used in sorted(self._by_identity.items(), key=lambda x: -x[1])
        ]
        return {
            "day": self._day,
            "global_used_today": self._global_used,
            "global_limit": global_limit if global_limit > 0 else None,
            "per_identity_limit": per_identity_limit if per_identity_limit > 0 else None,
            "by_identity": by,
        }
