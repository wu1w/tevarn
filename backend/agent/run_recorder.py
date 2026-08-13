"""RunRecorder：loop → AgentRun/RunStep 落库 + EventBus 发布 的胶水层（Phase 0.5.2）

设计原则：
- **绝不打断主循环**：所有 public 方法内部 try/except，失败仅 warn 日志
- 本地镜像 status / seq / 计数器，避免每次迁移都读库
- 状态机校验走 agent/run_state.py；非法迁移降级为 warning + 跳过（不 raise 进 loop）
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from backend.agent.run_lifecycle import build_create_payload, public_status
from backend.agent.run_state import (
    TERMINAL_STATES,
    IllegalTransitionError,
    RunStatus,
    validate_transition,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RunRecorder:
    """一次 Agent 运行的记录器（每次 NexusAgentLoop.run() 创建一个）"""

    def __init__(
        self,
        session_id: uuid.UUID,
        *,
        user_id: uuid.UUID | None = None,
        mode: str = "default",
        meta: dict[str, Any] | None = None,
        origin: str | None = None,
        identity_id: uuid.UUID | str | None = None,
        parent_run_id: uuid.UUID | str | None = None,
        token_limit: int = 0,
    ) -> None:
        self.session_id = session_id
        self.user_id = user_id
        self.mode = mode
        self.meta = dict(meta or {})
        self.origin = origin
        self.identity_id = identity_id
        self.parent_run_id = parent_run_id
        self.token_limit = int(token_limit or 0)
        self.run_id: uuid.UUID | None = None
        self.kernel_process_id: str | None = None
        self._status: RunStatus | None = None
        self._seq = 0
        self._tool_calls = 0
        self._iterations = 0
        self._token_used = 0
        self._token_used_source: str | None = None

    # ─────────── 内部工具 ───────────

    async def _repo(self):
        from backend.repositories.agent_run_repo import AsyncAgentRunRepository

        return AsyncAgentRunRepository()

    async def _publish(self, topic: str, payload: dict[str, Any]) -> None:
        try:
            from backend.core.event_bus import event_bus

            await event_bus.publish(topic, payload)
        except Exception as e:
            logger.debug("run_recorder publish %s failed: %s", topic, e)

    def _base_payload(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "run_id": str(self.run_id) if self.run_id else None,
            "session_id": str(self.session_id),
            "mode": self.mode,
        }
        gen = getattr(self, "generation", None)
        if gen is not None:
            out["generation"] = int(gen)
            out["run_generation"] = int(gen)
        return out

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    # ─────────── 生命周期 ───────────

    async def start(self, input_summary: str = "") -> uuid.UUID | None:
        """创建 run 记录（status=created）；失败返回 None，后续调用全部 no-op"""
        try:
            repo = await self._repo()
            payload = build_create_payload(
                session_id=self.session_id,
                user_id=self.user_id,
                mode=self.mode,
                input_summary=input_summary or "",
                meta=self.meta,
                origin=self.origin,
                identity_id=self.identity_id,
                parent_run_id=self.parent_run_id,
                token_limit=self.token_limit,
                status=RunStatus.CREATED.value,
                started_at=_utcnow(),
            )
            # 同步 origin 到实例，便于事件载荷
            self.origin = str(payload.get("origin") or self.origin or "chat")
            self.meta = dict(payload.get("meta") or self.meta or {})
            obj = await repo.create_run(payload)
            self.run_id = obj.id
            self._status = RunStatus.CREATED
            await self._publish("run.created", {
                **self._base_payload(),
                "origin": self.origin,
                "public_status": public_status(RunStatus.CREATED),
                "input_summary": (input_summary or "")[:200],
            })
            return self.run_id
        except Exception as e:
            logger.warning("RunRecorder.start failed: %s", e)
            return None

    async def transition(self, dst: RunStatus | str, note: str = "") -> bool:
        """状态迁移 + phase step + run.status_changed 事件；非法迁移 warn 并跳过。

        写库走 lifecycle 语义（validate_transition）；保持 recorder 内 seq/事件顺序。
        """
        if self.run_id is None or self._status is None:
            return False
        try:
            dst_s = validate_transition(self._status, dst)
        except IllegalTransitionError as e:
            logger.warning("RunRecorder.transition skipped: %s", e)
            return False
        if dst_s == self._status:
            return True
        src = self._status
        try:
            repo = await self._repo()
            await repo.update_run(self.run_id, {"status": dst_s.value})
            await repo.add_step({
                "run_id": self.run_id,
                "seq": self._next_seq(),
                "kind": "phase",
                "name": f"{src.value} -> {dst_s.value}",
                "status": "completed",
                "payload": {"note": note} if note else None,
            })
            self._status = dst_s
            await self._publish("run.status_changed", {
                **self._base_payload(),
                "origin": getattr(self, "origin", None),
                "from": src.value,
                "to": dst_s.value,
                "public_status": public_status(dst_s),
                "note": note,
            })
            return True
        except Exception as e:
            logger.warning("RunRecorder.transition %s->%s failed: %s", src, dst_s, e)
            return False

    # ─────────── 步骤记录 ───────────

    async def tool_step(
        self,
        name: str,
        *,
        args_summary: str = "",
        status: str = "completed",
        result_summary: str = "",
        duration_ms: float = 0.0,
    ) -> None:
        """记录一次工具调用（tool.called / tool.completed / tool.failed 事件）"""
        if self.run_id is None:
            return
        self._tool_calls += 1
        try:
            repo = await self._repo()
            await repo.add_step({
                "run_id": self.run_id,
                "seq": self._next_seq(),
                "kind": "tool",
                "name": name[:128],
                "status": status,
                "payload": {
                    "args": args_summary[:500],
                    "result": result_summary[:500],
                },
                "duration_ms": duration_ms,
            })
            topic = "tool.completed" if status == "completed" else "tool.failed"
            await self._publish(topic, {
                **self._base_payload(),
                "tool": name,
                "status": status,
                "duration_ms": duration_ms,
            })
            # Mid-run flush so crash/idle doesn't leave total_*=0
            if self._tool_calls == 1 or self._tool_calls % 5 == 0:
                await self._flush_counters(repo)
        except Exception as e:
            logger.warning("RunRecorder.tool_step %s failed: %s", name, e)

    async def note(self, text: str, *, kind: str = "note") -> None:
        """记录一条备注/迭代步骤"""
        if self.run_id is None:
            return
        try:
            repo = await self._repo()
            await repo.add_step({
                "run_id": self.run_id,
                "seq": self._next_seq(),
                "kind": kind[:20],
                "name": text[:128],
                "status": "completed",
            })
        except Exception as e:
            logger.warning("RunRecorder.note failed: %s", e)

    def bump_iteration(self, n: int = 1) -> None:
        """loop 每轮迭代调用（内存计数，定期 flush）。"""
        try:
            self._iterations = max(0, int(self._iterations) + int(n or 1))
        except (TypeError, ValueError):
            self._iterations += 1
        # Fire-and-forget flush every 5 iters (sync schedule from async loop)
        if self.run_id is not None and self._iterations % 5 == 0:
            try:
                import asyncio

                loop = asyncio.get_running_loop()
                loop.create_task(self._flush_counters_safe())
            except Exception:
                pass

    async def _flush_counters_safe(self) -> None:
        try:
            repo = await self._repo()
            await self._flush_counters(repo)
        except Exception as e:
            logger.debug("RunRecorder flush skip: %s", e)

    async def _flush_counters(self, repo: Any = None) -> None:
        """Persist total_tool_calls / total_iterations without changing status."""
        if self.run_id is None:
            return
        try:
            if repo is None:
                repo = await self._repo()
            payload: dict[str, Any] = {
                "total_tool_calls": int(self._tool_calls or 0),
                "total_iterations": int(self._iterations or 0),
                "token_used": int(self._token_used or 0),
            }
            if self._token_used_source:
                payload["meta"] = self._token_meta()
            await repo.update_run(self.run_id, payload)
        except Exception as e:
            logger.debug("RunRecorder._flush_counters: %s", e)

    def set_token_used(self, used: int) -> None:
        """Legacy absolute setter. Does not override provider-accrued totals.

        Prefer ``note_llm_round_usage`` — kernel snapshots are often 0
        (charge skipped when token_budget is None; Rust client returns copies).
        """
        if self._token_used_source in ("provider", "partial"):
            return
        try:
            self._token_used = max(0, int(used or 0))
        except (TypeError, ValueError):
            pass

    def note_llm_round_usage(self, usage: dict[str, Any] | None) -> None:
        """Accrue provider-reported tokens for this run.

        Empty/missing usage is recorded as ``omitted`` rather than a silent 0
        that looks like a free run. Estimates are not written here.
        """
        try:
            from backend.services.llm.usage_normalize import finalize_usage

            u = finalize_usage(dict(usage) if isinstance(usage, dict) else {})
        except Exception:
            u = dict(usage) if isinstance(usage, dict) else {}
        try:
            prompt = int(u.get("prompt_tokens") or 0)
            completion = int(u.get("completion_tokens") or 0)
            total = int(u.get("total_tokens") or 0)
        except (TypeError, ValueError):
            prompt, completion, total = 0, 0, 0
        n = total if total > 0 else max(0, prompt + completion)
        if n > 0:
            self._token_used = int(self._token_used or 0) + n
            if self._token_used_source == "omitted":
                self._token_used_source = "partial"
            else:
                self._token_used_source = "provider"
            return
        if self._token_used_source is None:
            self._token_used_source = "omitted"
        elif self._token_used_source == "provider":
            self._token_used_source = "partial"

    def apply_process_ledger(self, process_id: str | None = None) -> None:
        """Copy provider-real usage from usage_ledger.by_process onto this run.

        Live packed runs already wrote prompt/completion into
        ``~/.tevarn/data/usage_ledger.json`` while ``agent_runs.token_used``
        stayed 0 (kernel snapshot copy). Only prompt+completion are used —
        estimated rounds store ``tokens`` with prompt=completion=0.
        """
        pid = (process_id or self.kernel_process_id or "").strip()
        if not pid or pid == "system":
            return
        try:
            from backend.services.usage_ledger import process_cost

            bucket = process_cost(pid)
        except Exception:
            return
        if not bucket:
            return
        try:
            prompt = int(bucket.get("prompt") or 0)
            completion = int(bucket.get("completion") or 0)
        except (TypeError, ValueError):
            return
        if prompt <= 0 and completion <= 0:
            return
        n = prompt + completion
        self._token_used = max(int(self._token_used or 0), n)
        if self._token_used_source in (None, "omitted"):
            self._token_used_source = "provider"
        elif self._token_used_source == "partial":
            self._token_used_source = "provider"

    def _token_meta(self) -> dict[str, Any]:
        meta = dict(self.meta or {})
        if self._token_used_source:
            meta["token_used_source"] = self._token_used_source
        return meta

    # ─────────── 终态 ───────────

    async def _finish(
        self,
        dst: RunStatus,
        *,
        final_summary: str = "",
        error: str | None = None,
    ) -> None:
        if self.run_id is None or self._status is None:
            return
        if self._status in TERMINAL_STATES:
            return  # 幂等：已终态不重复写
        try:
            validate_transition(self._status, dst)
        except IllegalTransitionError as e:
            logger.warning("RunRecorder.finish skipped: %s", e)
            return
        try:
            repo = await self._repo()
            self.apply_process_ledger()
            data: dict[str, Any] = {
                "status": dst.value,
                "ended_at": _utcnow(),
                "total_tool_calls": self._tool_calls,
                "total_iterations": int(self._iterations or 0),
                "token_used": int(self._token_used or 0),
            }
            if self._token_used_source:
                data["meta"] = self._token_meta()
            if self.token_limit:
                data["token_limit"] = int(self.token_limit)
            if final_summary:
                data["final_summary"] = final_summary[:2000]
            if error:
                data["error"] = error[:2000]
            await repo.update_run(self.run_id, data)
            self._status = dst
            topic = {
                RunStatus.DONE: "run.completed",
                RunStatus.FAILED: "run.failed",
                RunStatus.CANCELLED: "run.cancelled",
            }[dst]
            await self._publish(topic, {
                **self._base_payload(),
                "total_tool_calls": self._tool_calls,
                "error": (error or "")[:200] or None,
            })
        except Exception as e:
            logger.warning("RunRecorder.finish(%s) failed: %s", dst, e)

    async def finish_ok(self, final_summary: str = "") -> None:
        await self._finish(RunStatus.DONE, final_summary=final_summary)

    async def finish_fail(self, error: str) -> None:
        await self._finish(RunStatus.FAILED, error=error)

    async def cancel(self, reason: str = "") -> None:
        await self._finish(RunStatus.CANCELLED, error=reason or None)
