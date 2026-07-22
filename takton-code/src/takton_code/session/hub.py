"""In-process multi-session hub."""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from takton_code.agent.loop import AgentRuntime
from takton_code.session.store import SessionStore

RuntimeFactory = Callable[..., Awaitable[AgentRuntime]]


class SessionHubError(RuntimeError):
    pass


class SessionHub:
    """Own multiple AgentRuntime instances; one active for interactive UI."""

    def __init__(self, store: SessionStore) -> None:
        self.store = store
        self._runtimes: dict[str, AgentRuntime] = {}
        self.active_id: str | None = None

    @property
    def active(self) -> AgentRuntime | None:
        if not self.active_id:
            return None
        return self._runtimes.get(self.active_id)

    def list_open(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for sid, rt in self._runtimes.items():
            out.append(
                {
                    "id": sid,
                    "mode": rt.mode,
                    "running": bool(getattr(rt, "_running", False)),
                    "active": sid == self.active_id,
                    "model": (rt.llm_snapshot or {}).get("model"),
                    "project": str(rt.project.root),
                }
            )
        return out

    async def register(self, rt: AgentRuntime, *, make_active: bool = True) -> AgentRuntime:
        sid = rt.session_id
        if not sid:
            raise SessionHubError("runtime has no session_id")
        self._runtimes[sid] = rt
        if make_active:
            if self.active and getattr(self.active, "_running", False) and self.active_id != sid:
                raise SessionHubError("active session is busy; stop turn before switch")
            self.active_id = sid
        return rt

    async def switch(self, session_id: str) -> AgentRuntime:
        if session_id not in self._runtimes:
            raise SessionHubError(f"session not open: {session_id}")
        cur = self.active
        if cur and getattr(cur, "_running", False) and self.active_id != session_id:
            raise SessionHubError("active session is busy; /stop first")
        self.active_id = session_id
        return self._runtimes[session_id]

    async def detach(self, session_id: str) -> None:
        """Drop runtime from memory (DB session kept)."""
        rt = self._runtimes.pop(session_id, None)
        if rt:
            try:
                await rt.llm.close()
            except Exception:
                pass
        if self.active_id == session_id:
            self.active_id = next(iter(self._runtimes), None)

    async def close_all(self) -> None:
        for sid in list(self._runtimes):
            await self.detach(sid)

    async def list_db_sessions(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = await self.store.list_sessions(limit)
        open_ids = set(self._runtimes)
        for r in rows:
            r["open"] = r["id"] in open_ids
            r["active"] = r["id"] == self.active_id
            if r["id"] in self._runtimes:
                r["running"] = bool(getattr(self._runtimes[r["id"]], "_running", False))
            else:
                r["running"] = False
        return rows
