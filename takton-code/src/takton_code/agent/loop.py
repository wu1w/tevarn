"""Agent loop: plan/build/ask/explore/always + parts timeline + queue + undo + stream."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from takton_code.agent.parts import (
    Part,
    part_reasoning,
    part_step_finish,
    part_step_start,
    part_text,
    part_tool_end,
    part_tool_start,
)
from takton_code.agent.prompt import build_system_prompt
from takton_code.agent.refs import cycle_permission_mode, expand_at_refs
from takton_code.agent.tools import ToolRuntime
from takton_code.compat.backend_core import (
    DiffEngine,
    DoomLoopGuard,
    FileHistory,
    PermissionBroker,
    PermissionGate,
    PlanDocument,
    PlanGate,
    PlanState,
    Reply,
    rules_for_profile,
    should_auto_plan,
)
from takton_code.context.compressor import (
    ContextCompressor,
    TokenMeter,
    estimate_messages,
    ensure_anthropic_strict,
    is_context_overflow_error,
    microcompact_tools,
    validate_tool_integrity,
)
from takton_code.context.policy import (
    ThrashingGuard,
    build_context_meter,
    format_context_meter,
    rag_assist_summary,
    recommended_thrashing,
)
from takton_code.llm.provider import LLMProvider, LLMResponse, collect_stream
from takton_code.project.binder import ProjectContext
from takton_code.session.store import SessionStore

EventCallback = Callable[[dict[str, Any]], None]

MODES = ("build", "plan", "ask", "explore", "always")
CHECK_SUFFIX = (
    "\n\n# Self-check (Grok --check style)\n"
    "After finishing the task: (1) summarize what changed, "
    "(2) run tests or a minimal verification command, "
    "(3) report pass/fail with evidence. Do not claim success without running checks."
)


@dataclass
class TurnResult:
    ok: bool
    final_text: str
    mode: str
    interrupted: bool = False
    compress_count: int = 0
    iterations: int = 0
    changes_summary: str = ""
    plan_state: str = ""
    error: str | None = None
    turn_id: str | None = None
    parts: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AgentRuntime:
    settings_llm: Any
    settings_agent: Any
    project: ProjectContext
    store: SessionStore
    llm: LLMProvider
    bridge: Any = None
    session_id: str | None = None
    mode: str = "build"
    on_event: EventCallback | None = None

    messages: list[dict[str, Any]] = field(default_factory=list)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    steer_queue: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    plan_gate: PlanGate = field(default_factory=PlanGate)
    diff: DiffEngine | None = None
    tools: ToolRuntime | None = None
    compressor: ContextCompressor | None = None
    llm_snapshot: dict[str, Any] = field(default_factory=dict)
    turn_parts: list[dict[str, Any]] = field(default_factory=list)
    usage_totals: dict[str, int] = field(
        default_factory=lambda: {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
    )
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _running: bool = False
    stream: bool = True  # live text/reasoning deltas
    permission_gate: PermissionGate | None = None
    permission_broker: PermissionBroker | None = None
    file_history: FileHistory | None = None
    headless: bool = False  # when True, ask → deny unless always profile
    # Desktop full agent loop via /bridge/v1/agent/turn (set in setup from settings)
    use_bridge_agent_loop: bool = False
    bridge_session_id: str | None = None  # UUID on Desktop side
    autoloop_enabled: bool = False
    autoloop_max_fix: int = 3
    _last_rewind: dict[str, Any] | None = None

    def emit(self, typ: str, **payload: Any) -> None:
        if self.on_event:
            self.on_event({"type": typ, **payload, "ts": time.time()})

    def _compose_extra(self, bridge_skills: str = "") -> str:
        parts: list[str] = []
        if bridge_skills and bridge_skills.strip():
            parts.append(bridge_skills.strip())
        if bool(getattr(self.settings_agent, "local_memory", False)):
            from takton_code.agent.memory_local import memory_prompt_block

            mem = memory_prompt_block(enabled=True)
            if mem:
                parts.append(mem)
        return "\n\n".join(parts)

    async def _rebuild_system_prompt(self, *, extra_agent: str = "") -> str:
        """Reload project files + memory into system message (used by /rules)."""
        from takton_code.project.binder import bind_project

        try:
            self.project = bind_project(self.project.root)
        except Exception:
            pass
        extra = await self._load_bridge_skills()
        if extra_agent:
            extra = (extra + "\n\n" + extra_agent).strip()
        system = build_system_prompt(
            mode=self.mode,
            project_block=self.project.prompt_block(),
            extra_skills=self._compose_extra(extra),
        )
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0] = {"role": "system", "content": system}
        else:
            self.messages.insert(0, {"role": "system", "content": system})
        return system

    def answer_permission(self, request_id: str, decision: str) -> bool:
        if not self.permission_broker:
            return False
        d: Reply = decision if decision in ("allow", "deny", "always") else "deny"  # type: ignore[assignment]
        return self.permission_broker.answer(request_id, d)  # type: ignore[arg-type]

    def answer_permission_latest(self, decision: str) -> bool:
        if not self.permission_broker:
            return False
        d: Reply = decision if decision in ("allow", "deny", "always") else "deny"  # type: ignore[assignment]
        return self.permission_broker.answer_latest(d)  # type: ignore[arg-type]

    async def _emit_part(self, part: Part, *, turn_id: str | None = None) -> None:
        data = part.model_dump()
        self.turn_parts.append(data)
        self.emit("part", part=data)
        if self.session_id:
            await self.store.append_part(self.session_id, data, turn_id=turn_id)

    async def _run_subagent(self, agent: str, prompt: str, max_iter: int) -> str:
        from takton_code.agent.subagent import run_subagent

        return await run_subagent(
            llm=self.llm,
            project_root=self.project.root,
            project_block=self.project.prompt_block(),
            bridge=self.bridge,
            agent=agent,
            prompt=prompt,
            max_iterations=max_iter,
            test_command=self.project.test_command or getattr(self.settings_agent, "test_command", None),
            on_event=self.on_event,
        )

    async def setup(self, session_id: str | None = None, title: str | None = None) -> str:
        self.diff = DiffEngine(self.project.root)
        self.file_history = FileHistory(self.store, self.project.root)
        enable_sub = bool(getattr(self.settings_agent, "enable_subagents", True))
        self.autoloop_enabled = bool(getattr(self.settings_agent, "autoloop", False))
        # bridge full loop (default on when bridge enabled; override via settings_agent.use_desktop_agent_loop)
        try:
            br_on = bool(self.bridge and getattr(self.bridge, "enabled", False))
            use_loop = bool(getattr(self.settings_agent, "use_desktop_agent_loop", True))
            self.use_bridge_agent_loop = br_on and use_loop
        except Exception:
            self.use_bridge_agent_loop = bool(self.bridge and getattr(self.bridge, "enabled", False))
        self.autoloop_max_fix = int(getattr(self.settings_agent, "autoloop_max_fix", 3) or 3)
        profile = str(getattr(self.settings_agent, "permission_profile", "cautious") or "cautious")
        if self.mode == "always":
            profile = "always"
        self.permission_gate = PermissionGate(
            profile=profile,
            mode=self.mode,
            project_root=self.project.root,
            rules=rules_for_profile(profile),
        )

        def _perm_emit(typ: str, **payload: Any) -> None:
            self.emit(typ, **payload)

        self.permission_broker = PermissionBroker(
            self.permission_gate,
            emit=_perm_emit,
            timeout_sec=float(getattr(self.settings_agent, "permission_timeout_sec", 300) or 300),
            headless=bool(self.headless),
        )
        # headless handled inside broker (Grok: cancel to model, no stdin wait)

        self.tools = ToolRuntime(
            self.project.root,
            self.diff,
            mode=self.mode if self.mode != "always" else "build",
            test_command=self.project.test_command or self.settings_agent.test_command,
            allow_git_commit=self.settings_agent.allow_git_commit,
            allow_git_push=self.settings_agent.allow_git_push,
            bridge=self.bridge,
            enable_subagent=enable_sub,
            subagent_runner=self._run_subagent if enable_sub else None,
            todo_store=self.store,
            session_id=None,  # filled after session id known
            permission_broker=self.permission_broker,
        )
        self.tools.allow_web_fetch = bool(getattr(self.settings_agent, "allow_web_fetch", True))
        meter = TokenMeter(
            context_window=int(self.settings_llm.context_window),
            threshold_percent=float(self.settings_llm.compress_threshold),
        )
        from takton_code.config import home_dir

        offload = home_dir() / "tool-outputs" / "pending"
        thr_cfg = recommended_thrashing(int(self.settings_llm.context_window))
        # env/config overrides still win when explicitly set higher fidelity...
        # use recommended as defaults if settings still at factory large-window values
        max_ev = int(getattr(self.settings_llm, "thrashing_max_events", 0) or 0)
        win_s = float(getattr(self.settings_llm, "thrashing_window_sec", 0) or 0)
        cool_s = float(getattr(self.settings_llm, "thrashing_cooldown_sec", 0) or 0)
        # auto-calibrate when still at legacy large defaults or zero
        if max_ev in (0, 3) and int(self.settings_llm.context_window) <= 16000:
            max_ev = int(thr_cfg["max_events"])
            win_s = float(thr_cfg["window_sec"])
            cool_s = float(thr_cfg["cooldown_sec"])
        elif max_ev <= 0:
            max_ev = int(thr_cfg["max_events"])
            win_s = float(thr_cfg["window_sec"])
            cool_s = float(thr_cfg["cooldown_sec"])
        else:
            win_s = win_s or float(thr_cfg["window_sec"])
            cool_s = cool_s or float(thr_cfg["cooldown_sec"])
        self.thrashing = ThrashingGuard(
            max_events=max_ev,
            window_sec=win_s,
            cooldown_sec=cool_s,
        )
        self.doom_loop = DoomLoopGuard(
            threshold=int(getattr(self.settings_agent, "doom_loop_threshold", 3) or 3)
        )
        self.compressor = ContextCompressor(
            meter=meter,
            keep_recent=int(self.settings_llm.compress_keep_recent),
            keep_recent_tool_blocks=int(getattr(self.settings_llm, "compress_keep_tool_blocks", 4) or 4),
            max_tool_chars=int(getattr(self.settings_llm, "max_tool_result_chars", 4000) or 4000),
            offload_dir=offload,
            compact_mode=str(getattr(self.settings_llm, "compact_mode", "static") or "static"),
            retain_turns=int(getattr(self.settings_llm, "retain_turns", 24) or 24),
            archive_dir=home_dir() / "archives",
        )
        self.llm_snapshot = {
            "provider": "openai_compatible",
            "base_url": getattr(self.settings_llm, "base_url", ""),
            "model": getattr(self.settings_llm, "model", ""),
            "temperature": getattr(self.settings_llm, "temperature", 0.2),
            "max_tokens": getattr(self.settings_llm, "max_tokens", 4096),
            "context_window": getattr(self.settings_llm, "context_window", 65536),
        }

        extra_skills = await self._load_bridge_skills()
        system = build_system_prompt(
            mode=self.mode,
            project_block=self.project.prompt_block(),
            extra_skills=self._compose_extra(extra_skills),
        )

        if session_id:
            self.session_id = session_id
            row = await self.store.get_session(session_id)
            if not row:
                raise ValueError(f"session not found: {session_id}")
            loaded = await self.store.load_messages(session_id)
            non_sys = [m for m in loaded if m.get("role") != "system"]
            self.messages = [{"role": "system", "content": system}] + non_sys
            snap = row.get("llm_snapshot")
            if snap:
                try:
                    self.llm_snapshot = {**self.llm_snapshot, **json.loads(snap)}
                except json.JSONDecodeError:
                    pass
            if row.get("plan_json"):
                try:
                    pdata = json.loads(row["plan_json"])
                    if pdata and pdata.get("plan"):
                        self.plan_gate.plan = PlanDocument.model_validate(pdata["plan"])
                        self.plan_gate.state = PlanState(pdata.get("state") or "idle")
                        self.plan_gate.approved = bool(pdata.get("approved"))
                except Exception:  # noqa: BLE001
                    pass
            # restore token counters from session row
            try:
                tin = int(row.get("tokens_input") or 0)
                tout = int(row.get("tokens_output") or 0)
                self.usage_totals["prompt_tokens"] = max(int(self.usage_totals.get("prompt_tokens") or 0), tin)
                self.usage_totals["completion_tokens"] = max(
                    int(self.usage_totals.get("completion_tokens") or 0), tout
                )
                self.usage_totals["total_tokens"] = max(
                    int(self.usage_totals.get("total_tokens") or 0), tin + tout
                )
            except (TypeError, ValueError):
                pass
            if self.compressor and row.get("compress_count"):
                # compressor events not restored; count is DB truth for display
                pass
            self.mode = row.get("mode") or self.mode
            if self.tools:
                tool_mode = "build" if self.mode == "always" else self.mode
                self.tools.set_mode(tool_mode)
                self.tools.session_id = self.session_id
        else:
            self.session_id = await self.store.create_session(
                project_root=str(self.project.root),
                mode=self.mode,
                title=title or "code session",
                llm_snapshot=self.llm_snapshot,
                agent=self.mode,
            )
            self.messages = [{"role": "system", "content": system}]
            await self.store.append_message(self.session_id, "system", system)
            if self.tools:
                self.tools.session_id = self.session_id
        # per-session tool output offload (Anthropic-strict microcompact)
        if self.compressor and self.session_id:
            from takton_code.config import home_dir

            self.compressor.offload_dir = home_dir() / "tool-outputs" / self.session_id
            self.compressor.session_id = self.session_id
            self.compressor.archive_dir = home_dir() / "archives"

        self.emit(
            "session_ready",
            session_id=self.session_id,
            mode=self.mode,
            slug=(await self.store.get_session(self.session_id) or {}).get("slug"),
        )
        return self.session_id  # type: ignore[return-value]

    async def _load_bridge_skills(self) -> str:
        if not (self.bridge and getattr(self.bridge, "enabled", False)):
            return ""
        try:
            skills = await self.bridge.list_skills()
            if not skills:
                return ""
            return "\n".join(
                f"- {s.name}: {s.description}"
                + (f"\n{s.prompt_injection}" if getattr(s, "prompt_injection", None) else "")
                for s in skills[:30]
            )
        except Exception as e:  # noqa: BLE001
            return f"(bridge skills unavailable: {e})"

    def request_cancel(self) -> None:
        self.cancel_event.set()
        self.emit("cancel_requested")

    def clear_cancel(self) -> None:
        self.cancel_event = asyncio.Event()

    def steer(self, text: str) -> None:
        """Inject guidance into in-flight turn (Claude-style)."""
        try:
            self.steer_queue.put_nowait(text)
            self.emit("steer", content=text)
        except asyncio.QueueFull:
            pass

    async def enqueue(self, text: str) -> int:
        assert self.session_id
        qid = await self.store.enqueue_prompt(self.session_id, text)
        self.emit("queue", action="enqueue", id=qid, content=text)
        return qid

    async def list_queue(self) -> list[dict[str, Any]]:
        assert self.session_id
        return await self.store.list_queue(self.session_id)

    async def _persist_messages(self) -> None:
        assert self.session_id
        await self.store.replace_messages(self.session_id, self.messages)

    async def _save_plan_state(self) -> None:
        assert self.session_id
        await self.store.update_session(
            self.session_id,
            plan_json=json.dumps(self.plan_gate.to_dict(), ensure_ascii=False),
            mode=self.mode,
            agent=self.mode,
        )

    async def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            mode = "build"
        # always is write-capable alias of build for permission UX
        self.mode = mode
        tool_mode = "build" if mode == "always" else mode
        if self.tools:
            self.tools.set_mode(tool_mode)
        if self.permission_gate:
            self.permission_gate.set_mode(mode)
            if mode == "always":
                self.permission_gate.profile = "always"
                self.permission_gate.rules = rules_for_profile("always")
        system = build_system_prompt(
            mode=mode,
            project_block=self.project.prompt_block(),
            extra_skills=self._compose_extra(""),
        )
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0] = {"role": "system", "content": system}
        else:
            self.messages.insert(0, {"role": "system", "content": system})
        if self.session_id:
            await self.store.update_session(self.session_id, mode=mode, agent=mode)
            await self._persist_messages()
        self.emit("mode", mode=mode)

    def cycle_mode(self) -> str:
        """Grok-style cycle: build → plan → always → build."""
        return cycle_permission_mode(self.mode if self.mode in ("build", "plan", "always") else "build")

    def _accumulate_usage(self, usage: dict[str, Any] | None) -> None:
        if not usage:
            return
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            try:
                self.usage_totals[k] = int(self.usage_totals.get(k) or 0) + int(usage.get(k) or 0)
            except (TypeError, ValueError):
                pass
        # llama.cpp / some servers use alternative keys
        try:
            if not usage.get("prompt_tokens") and usage.get("input_tokens") is not None:
                self.usage_totals["prompt_tokens"] = int(self.usage_totals.get("prompt_tokens") or 0) + int(
                    usage.get("input_tokens") or 0
                )
            if not usage.get("completion_tokens") and usage.get("output_tokens") is not None:
                self.usage_totals["completion_tokens"] = int(
                    self.usage_totals.get("completion_tokens") or 0
                ) + int(usage.get("output_tokens") or 0)
        except (TypeError, ValueError):
            pass
        if not self.usage_totals.get("total_tokens"):
            self.usage_totals["total_tokens"] = int(self.usage_totals.get("prompt_tokens") or 0) + int(
                self.usage_totals.get("completion_tokens") or 0
            )
        self.emit("usage", usage=usage, totals=dict(self.usage_totals))

    async def _persist_usage(self) -> None:
        if not self.session_id:
            return
        await self.store.update_session(
            self.session_id,
            tokens_input=int(self.usage_totals.get("prompt_tokens") or 0),
            tokens_output=int(self.usage_totals.get("completion_tokens") or 0),
            compress_count=int(self.compressor.compress_count if self.compressor else 0),
        )

    async def _maybe_compress(
        self, *, force: bool = False, reason: str = "threshold", aggressive_tools: bool = False
    ) -> None:
        assert self.compressor and self.session_id
        before = estimate_messages(self.messages)
        soft = self.compressor.meter.should_microcompact(self.messages)
        hard = force or self.compressor.meter.should_compress(self.messages)
        if not soft and not hard and not aggressive_tools:
            fixed = ensure_anthropic_strict(self.messages)
            if fixed != self.messages:
                self.messages = fixed
            return

        # thrashing: block middle summary unless forced overflow/manual
        block_middle = bool(getattr(self, "thrashing", None) and self.thrashing.active)
        if block_middle and reason not in ("api_overflow", "manual") and not force:
            self.emit(
                "thrashing",
                active=True,
                message="compact thrashing — microcompact only until cooldown",
                **self.thrashing.status(),
            )

        # advanced RAG assist before hard middle drop
        self.compressor.rag_snippet = ""
        if (
            hard
            and not block_middle
            and bool(getattr(self.settings_llm, "rag_compact", False))
            and self.bridge
            and getattr(self.bridge, "enabled", False)
        ):
            q = ""
            for m in reversed(self.messages):
                if m.get("role") == "user" and isinstance(m.get("content"), str):
                    q = str(m.get("content") or "")[:400]
                    break
            if q:
                self.compressor.rag_snippet = await rag_assist_summary(self.bridge, query=q)

        prev_count = self.compressor.compress_count
        self.messages = self.compressor.compress(
            self.messages,
            force=force or hard,
            reason=reason,
            aggressive_tools=aggressive_tools or reason == "api_overflow",
            block_middle=block_middle and reason not in ("api_overflow", "manual"),
        )
        self.messages = ensure_anthropic_strict(self.messages)
        errs = validate_tool_integrity(self.messages)
        if errs:
            self.emit("compress_integrity_warn", errors=errs[:5])
            self.messages = ensure_anthropic_strict(self.messages)
        after = estimate_messages(self.messages)

        # record thrashing on hard middle events
        last_reason = ""
        if self.compressor.events:
            last_reason = self.compressor.events[-1].reason
        if "+middle" in last_reason or reason in ("api_overflow", "manual"):
            kind = "api_overflow" if reason == "api_overflow" else (
                "manual_hard" if reason == "manual" else "middle"
            )
            if self.thrashing.record(kind=kind):
                self.emit("thrashing", active=True, **self.thrashing.status())

        if self.compressor.compress_count == prev_count and not force and after >= before:
            await self._persist_messages()
            return
        await self.store.update_session(
            self.session_id, compress_count=self.compressor.compress_count
        )
        self.emit(
            "compress",
            before_tokens=before,
            after_tokens=after,
            count=self.compressor.compress_count,
            reason=reason,
            integrity_ok=not validate_tool_integrity(self.messages),
            archive=getattr(self.compressor, "last_archive_path", None),
            thrashing=self.thrashing.active,
            compact_mode=self.compressor.compact_mode,
        )
        await self._persist_messages()
        await self._persist_usage()

    def context_meter(self) -> dict[str, Any]:
        assert self.compressor
        return build_context_meter(
            self.messages,
            context_window=int(self.compressor.meter.context_window),
            threshold_percent=float(self.compressor.meter.threshold_percent),
            usage_totals=dict(self.usage_totals),
            compress_count=self.compressor.compress_count,
            thrashing=self.thrashing.status(),
            mode=str(self.compressor.compact_mode),
            archive_path=getattr(self.compressor, "last_archive_path", None),
        )

    async def _llm_chat(self, tools: list[dict[str, Any]] | None) -> LLMResponse:
        """Stream when enabled; Anthropic-strict messages; overflow → compact+retry once."""

        def on_delta(ev: dict[str, Any]) -> None:
            self.emit(ev.get("type") or "delta", **{k: v for k, v in ev.items() if k != "type"})

        # always send pair-safe messages
        self.messages = ensure_anthropic_strict(self.messages)

        async def _once() -> LLMResponse:
            if self.stream:
                return await collect_stream(
                    self.llm,
                    self.messages,
                    tools=tools,
                    on_delta=on_delta if self.on_event else None,
                    should_cancel=lambda: self.cancel_event.is_set(),
                )
            return await self.llm.chat(self.messages, tools=tools)

        try:
            return await _once()
        except Exception as e:  # noqa: BLE001
            if not is_context_overflow_error(e):
                raise
            self.emit("context_overflow", error=str(e)[:300])
            await self._maybe_compress(force=True, reason="api_overflow", aggressive_tools=True)
            self.messages = ensure_anthropic_strict(self.messages)
            if validate_tool_integrity(self.messages):
                self.messages = ensure_anthropic_strict(self.messages)
            self.emit(
                "compress_retry",
                after_tokens=estimate_messages(self.messages),
                integrity_ok=not validate_tool_integrity(self.messages),
            )
            return await _once()

    def _bridge_loop_enabled_flag(self) -> bool:
        if not (self.bridge and getattr(self.bridge, "enabled", False)):
            return False
        for obj in (self.settings_agent, getattr(self, "settings_llm", None)):
            if obj is None:
                continue
            if hasattr(obj, "use_desktop_agent_loop"):
                return bool(getattr(obj, "use_desktop_agent_loop"))
            br = getattr(obj, "bridge", None)
            if br is not None and hasattr(br, "use_desktop_agent_loop"):
                return bool(br.use_desktop_agent_loop)
        return bool(getattr(self, "use_bridge_agent_loop", False))

    async def _should_use_bridge_agent_loop(self, text: str) -> bool:
        if not self._bridge_loop_enabled_flag():
            return False
        if not text or text.startswith("/"):
            return False
        try:
            h = await self.bridge.health()
            return bool(h.get("ok"))
        except Exception:
            return False

    async def _run_turn_via_bridge(
        self, user_text: str, *, force_mode: str | None = None
    ) -> TurnResult:
        """Delegate one turn to Desktop NexusAgentLoop via bridge."""
        from takton_code.bridge.protocol import AgentTurnRequest

        mode = force_mode or self.mode or "build"
        self.emit("bridge_agent_turn", mode=mode, session_id=self.bridge_session_id)
        turn_id = f"turn_{uuid.uuid4().hex[:12]}"
        await self._emit_part(part_step_start(0), turn_id=turn_id)

        req = AgentTurnRequest(
            message=user_text,
            session_id=self.bridge_session_id,
            mode=mode,
            project_root=str(self.project.root) if self.project else None,
            title="takton-code",
        )
        res = await self.bridge.agent_turn(req)
        if getattr(res, "session_id", None):
            self.bridge_session_id = res.session_id

        final = (getattr(res, "final_text", None) or "") if res else ""
        err = getattr(res, "error", None) if res else "no response"
        ok = bool(getattr(res, "ok", False)) and not err

        if final:
            await self._emit_part(part_text(final, role_hint="assistant"), turn_id=turn_id)
            self.messages.append({"role": "user", "content": user_text})
            self.messages.append({"role": "assistant", "content": final})
            if self.session_id:
                try:
                    await self.store.append_message(self.session_id, "user", user_text)
                    await self.store.append_message(self.session_id, "assistant", final)
                except Exception:
                    pass

        await self._emit_part(part_step_finish(0, reason="bridge"), turn_id=turn_id)
        self.emit(
            "bridge_agent_done",
            ok=ok,
            session_id=self.bridge_session_id,
            error=err,
        )
        return TurnResult(
            ok=ok,
            final_text=final,
            mode=mode,
            error=None if ok else (err or "bridge agent turn failed"),
            turn_id=turn_id,
            parts=list(self.turn_parts),
        )

    async def run_turn(self, user_text: str, *, force_mode: str | None = None) -> TurnResult:
        async with self._lock:
            return await self._run_turn_unlocked(user_text, force_mode=force_mode)

    async def _run_turn_unlocked(self, user_text: str, *, force_mode: str | None = None) -> TurnResult:
        assert self.tools and self.diff and self.compressor and self.session_id
        self.clear_cancel()
        self._running = True
        self.turn_parts = []
        turn_id = f"turn_{uuid.uuid4().hex[:12]}"
        text = user_text.strip()
        if not text:
            self._running = False
            return TurnResult(ok=False, final_text="", mode=self.mode, error="empty input")

        if text.startswith("/"):
            handled = await self._handle_slash(text)
            if handled is not None:
                self._running = False
                return handled

        # Desktop bridge agent loop (TUI → NexusAgentLoop)
        if await self._should_use_bridge_agent_loop(text):
            try:
                result = await self._run_turn_via_bridge(text, force_mode=force_mode)
                self._running = False
                return result
            except Exception as e:  # noqa: BLE001
                self.emit("bridge_agent_fallback", error=str(e)[:300])
                # fall through to local loop

        if force_mode:
            await self.set_mode(force_mode)
        elif self.mode == "build" and self.plan_gate.state in (
            PlanState.IDLE,
            PlanState.DONE,
            PlanState.CANCELLED,
        ):
            if should_auto_plan(
                text,
                auto_plan_complex=self.settings_agent.auto_plan_complex,
                simple_max_chars=self.settings_agent.simple_task_max_chars,
            ):
                await self.set_mode("plan")
                self.plan_gate.start_planning()
                await self._save_plan_state()

        self.diff.begin_turn()
        # snapshot all files that will be touched — baseline empty; tools call snapshot_before
        expanded = expand_at_refs(text, self.project.root)
        if expanded != text:
            self.emit("at_expand", original=text[:200], expanded_chars=len(expanded))
        # multimodal: local image paths → OpenAI vision content parts
        from takton_code.compat.backend_core import build_user_content, content_for_storage

        allow_img = bool(getattr(self.settings_agent, "allow_images", True))
        max_img = int(getattr(self.settings_agent, "max_images_per_message", 4) or 4)
        user_content = build_user_content(
            expanded,
            self.project.root,
            enabled=allow_img,
            max_images=max_img,
        )
        if not isinstance(user_content, str):
            self.emit(
                "image_attach",
                count=sum(1 for p in user_content if isinstance(p, dict) and p.get("type") == "image_url"),
            )
        self.messages.append({"role": "user", "content": user_content})
        store_text = content_for_storage(user_content)
        row_id = await self.store.append_message(self.session_id, "user", store_text)
        await self._emit_part(part_text(text, role_hint="user"), turn_id=turn_id)
        # Claude: leaf snapshot on user message (edits accumulate into turn edit point)
        if self.file_history and bool(getattr(self.settings_agent, "file_checkpointing", True)):
            try:
                msg_id = f"msg_{uuid.uuid4().hex[:12]}"
                await self.file_history.mark_user_leaf(
                    self.session_id,
                    turn_id=turn_id,
                    message_id=msg_id,
                    message_row_id=int(row_id),
                    label=(text[:60] + "…") if len(text) > 60 else text,
                )
            except Exception:  # noqa: BLE001
                pass
        self.emit("user", content=text)

        err: str | None = None
        final_text = ""
        iterations = 0
        interrupted = False
        max_iter = int(self.settings_agent.max_iterations)
        if getattr(self, "doom_loop", None):
            self.doom_loop.reset_turn()

        try:
            while iterations < max_iter:
                if self.cancel_event.is_set():
                    interrupted = True
                    final_text = final_text or "(interrupted)"
                    break

                # drain steer messages
                while not self.steer_queue.empty():
                    try:
                        s = self.steer_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    note = f"[STEER from user mid-turn]\n{s}"
                    self.messages.append({"role": "user", "content": note})
                    await self.store.append_message(self.session_id, "user", note)
                    await self._emit_part(part_text(note, role_hint="steer"), turn_id=turn_id)

                await self._maybe_compress()
                iterations += 1
                await self._emit_part(part_step_start(iterations), turn_id=turn_id)
                self.emit("iteration", n=iterations)

                tools = self.tools.openai_tools()
                try:
                    chat_task = asyncio.create_task(self._llm_chat(tools or None))
                    cancel_task = asyncio.create_task(self.cancel_event.wait())
                    done, _pending = await asyncio.wait(
                        {chat_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if cancel_task in done and not chat_task.done():
                        chat_task.cancel()
                        try:
                            await chat_task
                        except (asyncio.CancelledError, Exception):  # noqa: BLE001
                            pass
                        cancel_task.cancel()
                        interrupted = True
                        final_text = final_text or "(interrupted during LLM call)"
                        break
                    cancel_task.cancel()
                    try:
                        await cancel_task
                    except asyncio.CancelledError:
                        pass
                    resp: LLMResponse = await chat_task
                    self._accumulate_usage(getattr(resp, "usage", None))
                except asyncio.CancelledError:
                    interrupted = True
                    final_text = final_text or "(interrupted)"
                    break
                except Exception as e:  # noqa: BLE001
                    err = f"LLM error: {e}"
                    self.emit("error", message=err)
                    await self._checkpoint(status="llm_error", extra={"error": err})
                    self._running = False
                    self.emit(
                        "turn_end",
                        text=final_text or "",
                        ok=False,
                        interrupted=interrupted,
                        turn_id=turn_id,
                        iterations=iterations,
                        error=err,
                    )
                    self.emit("idle", session_id=self.session_id, turn_id=turn_id, ok=False, error=err)
                    return TurnResult(
                        ok=False,
                        final_text=final_text,
                        mode=self.mode,
                        interrupted=interrupted,
                        compress_count=self.compressor.compress_count,
                        iterations=iterations,
                        error=err,
                        turn_id=turn_id,
                        parts=list(self.turn_parts),
                    )

                if resp.reasoning_content:
                    await self._emit_part(part_reasoning(resp.reasoning_content[:4000]), turn_id=turn_id)

                if self.cancel_event.is_set():
                    interrupted = True
                    if resp.content:
                        final_text = resp.content
                    break

                if resp.tool_calls:
                    a_msg: dict[str, Any] = {
                        "role": "assistant",
                        "content": resp.content if resp.content else None,
                        "tool_calls": resp.tool_calls,
                    }
                    self.messages.append(a_msg)
                    await self.store.append_message(
                        self.session_id, "assistant", resp.content, tool_calls=resp.tool_calls
                    )
                    if resp.content:
                        await self._emit_part(part_text(resp.content, role_hint="assistant"), turn_id=turn_id)
                    self.emit("assistant_tools", tool_calls=resp.tool_calls, content=resp.content or "")

                    for tc in resp.tool_calls:
                        if self.cancel_event.is_set():
                            interrupted = True
                            break
                        fn = tc.get("function") or {}
                        name = fn.get("name") or ""
                        raw_args = fn.get("arguments") or "{}"
                        tc_id = tc.get("id") or f"call_{uuid.uuid4().hex[:8]}"
                        await self._emit_part(part_tool_start(name, tc_id, raw_args), turn_id=turn_id)
                        self.emit("tool_start", name=name, arguments=raw_args)

                        # doom-loop: same tool+args N times → ask/block unless always
                        if getattr(self, "doom_loop", None) and self.doom_loop.record(name, raw_args):
                            self.emit("doom_loop", **self.doom_loop.status())
                            d: str = "ask"
                            if self.permission_gate:
                                d = self.permission_gate.check(
                                    "doom_loop", {"tool": name, "arguments": raw_args}
                                )
                            allow_doom = False
                            if d == "allow":
                                allow_doom = True
                                self.doom_loop.clear_trip()
                            elif d == "ask" and self.permission_broker:
                                reply = await self.permission_broker.require(
                                    "doom_loop",
                                    {"tool": name, "arguments": raw_args, "_streak": self.doom_loop.streak},
                                )
                                if reply in ("allow", "always"):
                                    allow_doom = True
                                    self.doom_loop.clear_trip()
                                    if reply == "always" and self.permission_gate:
                                        self.permission_gate.add_session_allow(name)
                            if not allow_doom and d != "allow":
                                result = (
                                    f"ERROR: doom_loop blocked repeated tool {name} "
                                    f"(streak={self.doom_loop.streak}). Vary arguments or ask user."
                                )
                                ok = False
                                await self._emit_part(
                                    part_tool_end(name, tc_id, result, ok=ok), turn_id=turn_id
                                )
                                self.emit("tool_end", name=name, ok=ok, result=result[:500])
                                tmsg = {
                                    "role": "tool",
                                    "tool_call_id": tc_id,
                                    "name": name,
                                    "content": result,
                                }
                                self.messages.append(tmsg)
                                await self.store.append_message(
                                    self.session_id,
                                    "tool",
                                    result,
                                    tool_call_id=tc_id,
                                    name=name,
                                )
                                continue

                        # snapshot files about to change for undo
                        await self._maybe_snapshot_for_tool(name, raw_args, turn_id)

                        result = await self.tools.execute(name, raw_args)
                        ok = not str(result).startswith("ERROR")
                        # immediate tool payload discipline (keep pair, shrink content)
                        result_for_ctx = result
                        max_c = int(getattr(self.compressor, "max_tool_chars", 4000) or 4000)
                        if isinstance(result, str) and len(result) > max_c:
                            from takton_code.context.compressor import CLEARED_TOOL_RESULT
                            from pathlib import Path as _P

                            od = getattr(self.compressor, "offload_dir", None)
                            path_note = ""
                            if od is not None:
                                try:
                                    _P(od).mkdir(parents=True, exist_ok=True)
                                    fp = _P(od) / f"live_{tc_id}.txt"
                                    fp.write_text(result, encoding="utf-8", errors="replace")
                                    path_note = f"\n[full output: {fp}]"
                                except Exception:
                                    pass
                            result_for_ctx = (
                                result[:max_c]
                                + f"\n…[trimmed {len(result) - max_c} chars]"
                                + path_note
                            )
                        await self._emit_part(part_tool_end(name, tc_id, result, ok=ok), turn_id=turn_id)
                        self.emit("tool_end", name=name, result_preview=str(result)[:500])
                        if name == "todo_write" and ok and self.session_id:
                            try:
                                rows = await self.store.list_todos(self.session_id)
                                self.emit("todos", items=rows)
                            except Exception:
                                pass

                        # record after for diff engine already done in tools
                        tmsg = {
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "name": name,
                            "content": result_for_ctx,
                        }
                        self.messages.append(tmsg)
                        await self.store.append_message(
                            self.session_id, "tool", result_for_ctx, tool_call_id=tc_id, name=name
                        )

                    await self._emit_part(
                        part_step_finish(iterations, reason="tool_calls"), turn_id=turn_id
                    )
                    if iterations % max(1, int(self.settings_agent.checkpoint_every)) == 0:
                        await self._checkpoint(status="running")
                    continue

                final_text = (resp.content or "").strip()
                if resp.reasoning_content and not final_text:
                    self.messages.append({"role": "assistant", "content": resp.reasoning_content[:2000]})
                    self.messages.append(
                        {
                            "role": "user",
                            "content": "Please provide the final answer or tool calls with user-visible content.",
                        }
                    )
                    continue

                self.messages.append({"role": "assistant", "content": final_text})
                await self.store.append_message(self.session_id, "assistant", final_text)
                await self._emit_part(part_text(final_text, role_hint="assistant"), turn_id=turn_id)
                await self._emit_part(part_step_finish(iterations, reason="stop"), turn_id=turn_id)
                self.emit("assistant", content=final_text)

                if self.mode == "plan" and final_text:
                    plan = PlanGate.parse_plan_markdown(final_text)
                    self.plan_gate.submit_plan(plan)
                    await self._save_plan_state()
                    await self.store.set_todos(
                        self.session_id,
                        [{"content": s.title, "status": "pending"} for s in plan.steps],
                    )
                    self.emit("plan_ready", plan=plan.model_dump())
                break

            if iterations >= max_iter and not interrupted and not final_text:
                final_text = f"(stopped: max iterations {max_iter})"
                self.messages.append({"role": "assistant", "content": final_text})
                await self.store.append_message(self.session_id, "assistant", final_text)

            # update summary stats from diff
            changes = self.diff.end_turn_summary()
            adds = dels = files = 0
            for ch in self.diff.turn_changes:
                files += 1
                if ch.op == "create":
                    adds += (ch.after or "").count("\n") + 1
                elif ch.op == "delete":
                    dels += (ch.before or "").count("\n") + 1
                else:
                    # rough
                    adds += max(0, (ch.after or "").count("\n") - (ch.before or "").count("\n"))
            row = await self.store.get_session(self.session_id)
            if row:
                await self.store.update_session(
                    self.session_id,
                    summary_additions=int(row.get("summary_additions") or 0) + adds,
                    summary_deletions=int(row.get("summary_deletions") or 0) + dels,
                    summary_files=int(row.get("summary_files") or 0) + files,
                    status="interrupted" if interrupted else "active",
                    tokens_input=int(self.usage_totals.get("prompt_tokens") or 0),
                    tokens_output=int(self.usage_totals.get("completion_tokens") or 0),
                    compress_count=int(self.compressor.compress_count),
                )

            if interrupted:
                await self._checkpoint(
                    status="interrupted", extra={"final_preview": final_text[:500], "turn_id": turn_id}
                )
            else:
                await self.store.clear_checkpoint(self.session_id)

            await self._persist_messages()
            result = TurnResult(
                ok=not interrupted,
                final_text=final_text,
                mode=self.mode,
                interrupted=interrupted,
                compress_count=self.compressor.compress_count,
                iterations=iterations,
                changes_summary=changes,
                plan_state=self.plan_gate.state.value,
                turn_id=turn_id,
                parts=list(self.turn_parts),
            )
            # lifecycle events for TUI / Desktop bridge (success AND interrupt)
            self.emit(
                "assistant_final",
                text=final_text or "",
                ok=result.ok,
                interrupted=interrupted,
                turn_id=turn_id,
                iterations=iterations,
            )
            self.emit(
                "turn_end",
                text=final_text or "",
                ok=result.ok,
                interrupted=interrupted,
                turn_id=turn_id,
                iterations=iterations,
                compress_count=self.compressor.compress_count,
                error=err,
            )
            self.emit(
                "idle",
                session_id=self.session_id,
                turn_id=turn_id,
                ok=result.ok,
                interrupted=interrupted,
            )
            return result
        finally:
            self._running = False
            # belt-and-suspenders: if early return paths skipped idle, still clear running
            # (early returns below must also emit — see helpers)

    async def _maybe_snapshot_for_tool(self, name: str, raw_args: Any, turn_id: str) -> None:
        assert self.session_id and self.diff
        if name not in ("file_write", "edit_file", "apply_patch"):
            return
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
        except json.JSONDecodeError:
            args = {}
        paths: list[str] = []
        if name in ("file_write", "edit_file") and args.get("path"):
            paths.append(str(args["path"]))
        # apply_patch: best-effort parse paths from diff headers
        if name == "apply_patch" and args.get("patch"):
            for line in str(args["patch"]).splitlines():
                if line.startswith("+++ b/") or line.startswith("+++ "):
                    p = line.split(maxsplit=1)[-1].strip()
                    if p.startswith("b/"):
                        p = p[2:]
                    if p and p != "/dev/null":
                        paths.append(p)
        for p in paths:
            try:
                path = self.diff.resolve(p)
                rel = self.diff.rel_of(path)
                if self.file_history:
                    await self.file_history.snapshot_before_edit(self.session_id, turn_id, rel)
                else:
                    content = None
                    if path.is_file():
                        content = path.read_text(encoding="utf-8", errors="replace")
                    await self.store.save_file_snapshot(self.session_id, turn_id, rel, content)
                self.diff.snapshot_before(str(path))
            except Exception:  # noqa: BLE001
                pass

    async def undo_last_turn(self) -> str:
        """Claude double-esc: rewind one history point (prefer multi-point history)."""
        assert self.session_id
        if self.file_history:
            res = await self.file_history.rewind(self.session_id, steps=1)
            if not res.get("ok") and res.get("error"):
                # fallback legacy
                pass
            else:
                msg = f"rewind {res.get('point_id')} ({res.get('label')}):\n" + "\n".join(
                    res.get("restored") or []
                )
                if res.get("errors"):
                    msg += "\nerrors:\n" + "\n".join(res["errors"])
                self.emit("undo", turn_id=res.get("point_id"), files=res.get("restored") or [])
                self.emit("rewind", **res)
                return msg or "rewound (no file changes in checkpoint)"

        turn_id = await self.store.latest_turn_id(self.session_id)
        if not turn_id:
            return "nothing to undo (no file snapshots)"
        snaps = await self.store.load_turn_snapshots(self.session_id, turn_id)
        if not snaps:
            return "nothing to undo"
        restored = []
        for s in snaps:
            path = Path(self.project.root) / s["path"]
            if s["content"] is None:
                if path.exists():
                    path.unlink()
                    restored.append(f"deleted {s['path']}")
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(s["content"], encoding="utf-8")
                restored.append(f"restored {s['path']}")
        msg = "undo:\n" + "\n".join(restored)
        self.emit("undo", turn_id=turn_id, files=restored)
        return msg

    async def rewind_to(
        self,
        point_id: str | None = None,
        *,
        steps: int = 1,
        scope: str = "code",
        dry_run: bool = False,
        force: bool = False,
        only_paths: list[str] | None = None,
        focus_path: str | None = None,
    ) -> str:
        """Claude /rewind + partial paths + patch focus."""
        assert self.session_id and self.file_history
        scope_n = scope if scope in ("code", "conversation", "both") else "code"
        res = await self.file_history.rewind(
            self.session_id,
            point_id,
            steps=steps,
            scope=scope_n,  # type: ignore[arg-type]
            dry_run=dry_run,
            force=force,
            only_paths=only_paths,
            focus_path=focus_path,
        )
        self.emit("rewind", **res)
        self._last_rewind = res
        if not res.get("ok") and res.get("error"):
            return f"rewind failed: {res['error']}"

        if dry_run:
            return res.get("side_summary") or f"PREVIEW {res.get('point_id')}"

        parts: list[str] = [
            f"rewound to {res.get('point_id')} ({res.get('label') or res.get('kind')}) scope={scope_n}"
        ]
        if only_paths:
            parts.append(f"partial files: {', '.join(only_paths[:12])}")
        if scope_n in ("code", "both"):
            lines = res.get("restored") or []
            parts.append("\n".join(lines) if lines else "(no file changes)")
            if res.get("skipped"):
                parts.append("skipped:\n" + "\n".join(res["skipped"]))
            if res.get("errors"):
                parts.append("errors:\n" + "\n".join(res["errors"]))

        if scope_n in ("conversation", "both"):
            n = await self._truncate_conversation_to_point(res)
            parts.append(
                f"conversation truncated ({n} messages removed)"
                if n
                else "conversation: no truncate marker"
            )

        if res.get("side_summary"):
            parts.append("")
            parts.append(res["side_summary"])
        return "\n".join(parts)

    def focus_rewind_patch(self, which: str | int | None = None) -> str:
        """Cycle / select focused unified diff in last rewind payload for side panel."""
        from takton_code.compat.backend_core import format_rewind_side_panel

        res = self._last_rewind
        if not res:
            return "no rewind payload — run /rewind first"
        udiffs = res.get("unified_diffs") or []
        if not udiffs:
            return "no unified diffs in last rewind"
        n = len(udiffs)
        fi = int(res.get("focus_index") or 0)
        if which is None or which == "" or str(which).lower() in ("next", "n", "]"):
            fi = (fi + 1) % n
        elif str(which).lower() in ("prev", "p", "previous", "["):
            fi = (fi - 1) % n
        elif isinstance(which, int) or (isinstance(which, str) and which.isdigit()):
            fi = max(0, min(n - 1, int(which)))
        else:
            key = str(which).replace("\\", "/").lstrip("./")
            found = None
            for i, u in enumerate(udiffs):
                if key in str(u.get("path") or "").replace("\\", "/"):
                    found = i
                    break
            if found is None:
                return f"patch not found: {which}"
            fi = found
        res["focus_index"] = fi
        res["side_summary"] = format_rewind_side_panel(res, preview=bool(res.get("dry_run")))
        res["side_summary_focus"] = format_rewind_side_panel(
            res, preview=bool(res.get("dry_run")), focus_only=True
        )
        self._last_rewind = res
        self.emit("rewind_focus", index=fi, path=udiffs[fi].get("path"))
        return res["side_summary_focus"]

    async def unrewind(self) -> str:
        assert self.session_id and self.file_history
        res = await self.file_history.unrewind(self.session_id)
        self.emit("unrewind", **res)
        self._last_rewind = {**(self._last_rewind or {}), **res, "kind": "unrewind"}
        if not res.get("ok"):
            return f"unrewind failed: {res.get('error')}"
        return res.get("side_summary") or "unrewind ok"

    async def list_unrewind(self) -> str:
        assert self.session_id and self.file_history
        rows = await self.file_history.list_redo(self.session_id)
        if not rows:
            return "(redo stack empty)"
        lines = ["redo stack (newest first):"]
        for r in rows:
            lines.append(
                f"  {r['id']}  files={r.get('file_count')}  point={r.get('point_id')}  {r.get('label')}"
            )
        lines.append("\n/unrewind  — pop & restore pre-rewind disk state")
        return "\n".join(lines)

    async def list_hunks(self, path: str | None = None) -> str:
        from takton_code.compat.backend_core import hunks_summary, parse_unified_hunks

        res = self._last_rewind or {}
        udiffs = res.get("unified_diffs") or []
        if not udiffs:
            return "no unified diffs — /rewind preview first"
        fi = int(res.get("focus_index") or 0)
        if path:
            key = path.replace("\\", "/").lstrip("./")
            for i, u in enumerate(udiffs):
                if key in str(u.get("path") or ""):
                    fi = i
                    break
        fi = max(0, min(fi, len(udiffs) - 1))
        u = udiffs[fi]
        hunks = parse_unified_hunks(u.get("patch") or "")
        res["focus_index"] = fi
        self._last_rewind = res
        return hunks_summary(hunks, str(u.get("path") or ""))

    async def apply_hunks_cmd(self, spec: str) -> str:
        """spec: '0,2,3' or 'all' using focused patch from last rewind."""
        from takton_code.compat.backend_core import parse_unified_hunks

        assert self.session_id and self.file_history
        res = self._last_rewind or {}
        udiffs = res.get("unified_diffs") or []
        if not udiffs:
            return "no unified diffs — run /rewind … preview first"
        fi = int(res.get("focus_index") or 0)
        fi = max(0, min(fi, len(udiffs) - 1))
        u = udiffs[fi]
        path = str(u.get("path") or "")
        patch = u.get("patch") or ""
        hunks = parse_unified_hunks(patch)
        if not hunks:
            return "no hunks"
        spec = (spec or "").strip().lower()
        if spec in ("all", "*"):
            indices = list(range(len(hunks)))
        else:
            indices = []
            for part in spec.replace(" ", ",").split(","):
                part = part.strip()
                if not part:
                    continue
                try:
                    indices.append(int(part))
                except ValueError:
                    return f"bad hunk index: {part}"
        out = await self.file_history.apply_hunks(
            self.session_id, path, indices, patch=patch, push_redo=True
        )
        self.emit("hunk_apply", **out)
        if out.get("side_summary"):
            self._last_rewind = {**(self._last_rewind or {}), "side_summary": out["side_summary"]}
        if not out.get("ok"):
            return f"hunk apply failed: {out.get('error')}"
        return out.get("side_summary") or f"applied {indices} on {path}"

    async def _truncate_conversation_to_point(self, res: dict[str, Any]) -> int:
        """Keep system + messages up through the checkpoint user leaf (precise row id)."""
        assert self.session_id
        row_id = res.get("truncate_to_message_row_id")
        if row_id is not None:
            try:
                row_id = int(row_id)
            except (TypeError, ValueError):
                row_id = None

        if row_id is not None:
            # DB: delete messages after this row
            n = await self.store.truncate_messages_after(self.session_id, keep_until_id=row_id)
            # Reload memory from DB (drop internal _row_id)
            loaded = await self.store.load_messages(self.session_id, with_ids=False)
            # Ensure system prompt preserved if DB lost it somehow
            if loaded and loaded[0].get("role") == "system":
                self.messages = loaded
            elif self.messages and self.messages[0].get("role") == "system":
                sys_m = self.messages[0]
                self.messages = [sys_m] + [m for m in loaded if m.get("role") != "system"]
            else:
                self.messages = loaded
            return int(n)

        # Fallback heuristic: drop last user+assistant pair
        if not self.messages:
            return 0
        sys_msgs = [m for m in self.messages if m.get("role") == "system"]
        non_sys = [m for m in self.messages if m.get("role") != "system"]
        if not non_sys:
            return 0
        keep_non = max(0, len(non_sys) - 2)
        before = len(self.messages)
        self.messages = sys_msgs + non_sys[:keep_non]
        removed = before - len(self.messages)
        if removed:
            try:
                await self.store.truncate_messages_after(
                    self.session_id, keep_count=len(self.messages)
                )
            except Exception:
                pass
            await self._persist_messages()
        return removed

    async def list_checkpoints(self, limit: int = 30) -> str:
        assert self.session_id and self.file_history
        pts = await self.file_history.list_points(self.session_id, limit=limit)
        if not pts:
            return "(no checkpoints — edit files or /checkpoint)"
        lines = []
        for i, p in enumerate(pts):
            ts = time.strftime("%H:%M:%S", time.localtime(p.created_at))
            mid = (p.message_id or "-")[:8]
            lines.append(
                f"{i:>2}. {p.id}  {ts}  [{p.kind}]  files={p.file_count}  msg={mid}  {p.label}"
            )
        lines.append("\nRestore (Claude-parity scopes):")
        lines.append("  /rewind                         # last point, code only")
        lines.append("  /rewind <id>")
        lines.append("  /rewind <id> scope=both         # code + conversation")
        lines.append("  /rewind <id> scope=conversation")
        lines.append("  /rewind steps=2")
        lines.append("  /rewind <id> preview            # dry-run diff stats")
        lines.append("  Esc Esc = /rewind (code)")
        return "\n".join(lines)

    async def create_named_checkpoint(self, label: str = "manual") -> str:
        assert self.session_id and self.file_history
        from takton_code.agent.autoloop import _list_tracked_paths

        paths = await _list_tracked_paths(self)
        pt = await self.file_history.create_point(
            self.session_id,
            label=label or "manual",
            kind="manual",
            paths=paths[:300],
        )
        self.emit("history_point", point=pt.to_dict())
        return f"checkpoint {pt.id}  files={pt.file_count}  label={pt.label}"

    async def run_autoloop(
        self,
        goal: str,
        *,
        max_fix_rounds: int | None = None,
        auto_approve_plan: bool = False,
        run_tests: bool = True,
    ) -> Any:
        from takton_code.agent.autoloop import run_autoloop

        return await run_autoloop(
            self,
            goal,
            max_fix_rounds=max_fix_rounds
            if max_fix_rounds is not None
            else self.autoloop_max_fix,
            auto_approve_plan=auto_approve_plan,
            run_tests=run_tests,
            create_checkpoints=True,
        )

    async def approve_plan_and_build(self, user_note: str = "") -> TurnResult:
        if self.plan_gate.state != PlanState.PLAN_READY or not self.plan_gate.plan:
            return TurnResult(ok=False, final_text="", mode=self.mode, error="no plan ready")
        self.plan_gate.approve()
        await self.set_mode("build")
        await self._save_plan_state()
        plan = self.plan_gate.plan
        steps = "\n".join(f"{i+1}. {s.title}" for i, s in enumerate(plan.steps))
        prompt = (
            "Plan approved. Execute it now in build mode.\n"
            f"Title: {plan.title}\nSteps:\n{steps}\n"
            f"Test plan: {plan.test_plan or self.project.test_command or 'run relevant tests'}\n"
        )
        if user_note:
            prompt += f"\nUser note: {user_note}\n"
        prompt += "Implement with tools, then run tests if code changed."
        return await self.run_turn(prompt, force_mode="build")

    async def continue_after_interrupt(self, note: str = "请继续") -> TurnResult:
        cp = await self.store.load_checkpoint(self.session_id or "")
        prefix = "Continue the previous interrupted task. Do not redo completed work."
        if cp:
            prefix += f"\nCheckpoint: {json.dumps(cp, ensure_ascii=False)[:2000]}"
        return await self.run_turn(f"{prefix}\n{note}")

    async def drain_queue_once(self) -> TurnResult | None:
        assert self.session_id
        item = await self.store.dequeue_prompt(self.session_id)
        if not item:
            return None
        self.emit("queue", action="dequeue", id=item["id"])
        return await self.run_turn(item["content"])

    async def _checkpoint(self, status: str, extra: dict[str, Any] | None = None) -> None:
        assert self.session_id and self.compressor and self.diff
        payload = {
            "status": status,
            "mode": self.mode,
            "plan": self.plan_gate.to_dict(),
            "compress_count": self.compressor.compress_count,
            "message_count": len(self.messages),
            "tokens": estimate_messages(self.messages),
            "changes": self.diff.to_dict(),
            "llm_snapshot": self.llm_snapshot,
            "extra": extra or {},
            "ts": time.time(),
        }
        await self.store.save_checkpoint(self.session_id, payload)
        self.emit("checkpoint", status=status)

    async def _handle_slash(self, text: str) -> TurnResult | None:
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        help_text = (
            "Commands:\n"
            "  /plan /build /ask /explore /always   mode (Tab cycles build→plan→always)\n"
            "  /approve /reject            plan gate\n"
            "  /diff /undo /revert <path>  changes\n"
            "  /checkpoint [label]         named file checkpoint (Claude-style)\n"
            "  /checkpoints                list checkpoints\n"
            "  /rewind [id|steps=N]        restore files to a checkpoint\n"
            "  /autoloop <goal>            plan→build→test→fix loop\n"
            "  /test /check /compress /status /usage /inspect\n"
            "  /continue /stop /queue /enqueue <msg>\n"
            "  /todo                       session todos\n"
            "  /worktree [list|status]     git worktrees\n"
            "  /model [local|name|show]     模型（浅层，对标 openclaw）\n"
            "  /model set url=.. model=..\n"
            "  /new /sessions /export /fork /title <t>\n"
            "  @path                       attach file contents into prompt\n"
            "  /help /exit\n"
            "Keys (TUI): Tab=mode  Ctrl+C=stop  Ctrl+;=queue  Esc Esc=rewind  Ctrl+O=diff\n"
            "While running: plain text = STEER only; /enqueue msg = queue next turn\n"
            "CLI: --autoloop --yes-build  |  models | setup\n"
            "Ecosystem (skills/MCP/tools/RAG): Takton Desktop bridge --bridge"
        )

        if cmd in ("/help", "/?"):
            return TurnResult(ok=True, final_text=help_text, mode=self.mode)

        if cmd == "/plan":
            await self.set_mode("plan")
            self.plan_gate.start_planning()
            await self._save_plan_state()
            if arg:
                return await self.run_turn(arg, force_mode="plan")
            return TurnResult(ok=True, final_text="Plan mode on (read-only).", mode=self.mode)

        if cmd == "/build":
            await self.set_mode("build")
            if arg:
                return await self.run_turn(arg, force_mode="build")
            return TurnResult(ok=True, final_text="Build mode on.", mode=self.mode)

        if cmd == "/always":
            await self.set_mode("always")
            if arg:
                return await self.run_turn(arg, force_mode="always")
            return TurnResult(
                ok=True,
                final_text="Always-approve mode on (writes auto-allowed; Grok-style).",
                mode=self.mode,
            )

        if cmd == "/ask":
            await self.set_mode("ask")
            if arg:
                return await self.run_turn(arg, force_mode="ask")
            return TurnResult(ok=True, final_text="Ask mode on.", mode=self.mode)

        if cmd == "/explore":
            await self.set_mode("explore")
            if arg:
                return await self.run_turn(arg, force_mode="explore")
            return TurnResult(ok=True, final_text="Explore mode on (read-only search).", mode=self.mode)

        if cmd == "/approve":
            return await self.approve_plan_and_build(arg)

        if cmd == "/reject":
            self.plan_gate.reject()
            await self.set_mode("plan")
            await self._save_plan_state()
            return TurnResult(ok=True, final_text="Plan rejected.", mode=self.mode)

        if cmd == "/diff":
            assert self.diff
            d = self.diff.all_diffs()
            self.emit("diff", content=d[:8000])
            return TurnResult(ok=True, final_text=d or "(no diffs)", mode=self.mode)

        if cmd == "/undo":
            msg = await self.undo_last_turn()
            return TurnResult(ok=True, final_text=msg, mode=self.mode)

        if cmd in ("/checkpoint", "/chk"):
            msg = await self.create_named_checkpoint(arg.strip() or "manual")
            return TurnResult(ok=True, final_text=msg, mode=self.mode)

        if cmd in ("/checkpoints", "/history"):
            msg = await self.list_checkpoints()
            return TurnResult(ok=True, final_text=msg, mode=self.mode)

        if cmd == "/rewind":
            point_id = None
            steps = 1
            scope = "code"
            dry_run = False
            force = False
            only_paths: list[str] | None = None
            focus_path = None
            tokens = arg.split()
            for t in tokens:
                tl = t.lower()
                if tl.startswith("steps=") or tl.startswith("n="):
                    try:
                        steps = max(1, int(t.split("=", 1)[1]))
                    except ValueError:
                        steps = 1
                elif tl.startswith("scope="):
                    scope = t.split("=", 1)[1].strip().lower()
                elif tl.startswith("files=") or tl.startswith("only="):
                    raw = t.split("=", 1)[1]
                    only_paths = [p.strip() for p in raw.split(",") if p.strip()]
                elif tl.startswith("focus="):
                    focus_path = t.split("=", 1)[1].strip()
                elif tl in ("preview", "dry", "dry-run", "--preview"):
                    dry_run = True
                elif tl in ("force", "--force"):
                    force = True
                elif tl in ("both", "code", "conversation"):
                    scope = tl
                elif t.startswith("chk_") or (len(t) >= 8 and not t.startswith("-") and "=" not in t):
                    point_id = t
            msg = await self.rewind_to(
                point_id,
                steps=steps,
                scope=scope,
                dry_run=dry_run,
                force=force,
                only_paths=only_paths,
                focus_path=focus_path,
            )
            return TurnResult(ok=True, final_text=msg, mode=self.mode)

        if cmd == "/patch":
            which = arg.strip() or "next"
            body = self.focus_rewind_patch(which)
            return TurnResult(ok=True, final_text=body, mode=self.mode)

        if cmd in ("/unrewind", "/redo"):
            msg = await self.unrewind()
            return TurnResult(ok=True, final_text=msg, mode=self.mode)

        if cmd in ("/redo-list", "/unrewind-list"):
            msg = await self.list_unrewind()
            return TurnResult(ok=True, final_text=msg, mode=self.mode)

        if cmd == "/hunk":
            sub = arg.strip()
            if not sub or sub.lower() in ("list", "ls", "show"):
                msg = await self.list_hunks()
                return TurnResult(ok=True, final_text=msg, mode=self.mode)
            parts = sub.split(maxsplit=1)
            if parts[0].lower() == "list":
                path = parts[1] if len(parts) > 1 else None
                msg = await self.list_hunks(path)
                return TurnResult(ok=True, final_text=msg, mode=self.mode)
            if parts[0].lower() in ("apply", "a"):
                spec = parts[1] if len(parts) > 1 else "all"
                msg = await self.apply_hunks_cmd(spec)
                return TurnResult(ok=True, final_text=msg, mode=self.mode)
            # bare indices
            msg = await self.apply_hunks_cmd(sub)
            return TurnResult(ok=True, final_text=msg, mode=self.mode)

        if cmd == "/autoloop":
            if not arg.strip():
                return TurnResult(
                    ok=False,
                    final_text="",
                    mode=self.mode,
                    error="usage: /autoloop <goal>   (optional: --yes to auto-approve plan)",
                )
            goal = arg.strip()
            auto_yes = False
            if goal.endswith(" --yes") or goal.startswith("--yes "):
                auto_yes = True
                goal = goal.replace("--yes", "").strip()
            res = await self.run_autoloop(
                goal,
                auto_approve_plan=auto_yes
                or bool(getattr(self.settings_agent, "autoloop_auto_approve", False)),
            )
            body = json.dumps(res.to_dict(), ensure_ascii=False, indent=2)
            return TurnResult(
                ok=res.ok,
                final_text=body,
                mode=self.mode,
                error=res.error,
                plan_state=res.plan_state,
            )

        if cmd in ("/auto-rules", "/autorules"):
            from takton_code.agent.auto_classify import (
                clear_rules_cache,
                ensure_default_rules_file,
                format_rules_summary,
                load_rules,
            )
            from takton_code.config import home_dir

            sub = (arg or "").strip().lower()
            if sub in ("init", "write", "ensure"):
                p = ensure_default_rules_file()
                return TurnResult(ok=True, final_text=f"wrote/ensured {p}", mode=self.mode)
            if sub in ("reload", "refresh"):
                clear_rules_cache()
            rs = load_rules(project_root=self.project.root, force_reload=sub in ("reload", "refresh"))
            body = format_rules_summary(rs) + f"\n\nedit: {home_dir() / 'auto_rules.toml'}"
            body += f"\nproject overlay: {self.project.root / '.takton' / 'auto_rules.toml'}"
            body += "\nenv: TAKTON_CODE_AUTO_RULES=<path>"
            return TurnResult(ok=True, final_text=body, mode=self.mode)

        if cmd == "/revert":
            assert self.diff
            if not arg:
                return TurnResult(ok=False, final_text="", mode=self.mode, error="usage: /revert path")
            return TurnResult(ok=True, final_text=self.diff.revert(arg.strip()), mode=self.mode)

        if cmd == "/test":
            assert self.tools
            out = await self.tools.run_tests({"command": arg or None})
            return TurnResult(ok=True, final_text=out, mode=self.mode)

        if cmd == "/check":
            # Grok --check: run a verification turn on current diff / last task
            assert self.diff
            summary = self.diff.end_turn_summary()
            if "no file changes" in summary:
                summary = self.diff.all_diffs()[:4000]
            prompt = (
                "Verify the recent work in this repo.\n"
                f"Diff summary:\n{summary or '(no recorded diffs — inspect git status and run tests)'}\n"
                f"{CHECK_SUFFIX}"
            )
            if arg:
                prompt = arg + "\n" + prompt
            return await self.run_turn(prompt)

        if cmd in ("/compress", "/compact"):
            # manual hard compact resets thrashing cooldown path but records as manual_hard
            if getattr(self, "thrashing", None):
                # allow middle on explicit user compact
                self.compressor.block_middle = False  # type: ignore[union-attr]
            await self._maybe_compress(force=True, reason="manual")
            assert self.compressor
            meter = self.context_meter()
            return TurnResult(
                ok=True,
                final_text=(
                    f"Compressed count={self.compressor.compress_count}\n"
                    + format_context_meter(meter)
                ),
                mode=self.mode,
                compress_count=self.compressor.compress_count,
            )

        if cmd in ("/context", "/ctx", "/meter"):
            meter = self.context_meter()
            return TurnResult(
                ok=True,
                final_text=format_context_meter(meter) + "\n" + json.dumps(meter, ensure_ascii=False, indent=2),
                mode=self.mode,
            )

        if cmd == "/continue":
            return await self.continue_after_interrupt(arg or "请继续")

        if cmd == "/usage":
            assert self.compressor
            st = {
                "context_meter": self.context_meter(),
                "llm_usage_totals": self.usage_totals,
                "model": self.llm_snapshot.get("model"),
                "compress_count": self.compressor.compress_count,
                "messages": len(self.messages),
                "thrashing": self.thrashing.status(),
            }
            return TurnResult(ok=True, final_text=json.dumps(st, ensure_ascii=False, indent=2), mode=self.mode)

        if cmd == "/todo":
            rows = await self.store.list_todos(self.session_id or "")
            if not rows:
                return TurnResult(ok=True, final_text="(no todos)", mode=self.mode)
            body = "\n".join(f"[{r.get('status')}] {r.get('content')}" for r in rows)
            self.emit("todos", items=rows)
            return TurnResult(ok=True, final_text=body, mode=self.mode)

        if cmd == "/agent":
            from takton_code.agent.agents_fs import agents_summary, get_agent

            a = arg.strip()
            if not a or a in ("list", "ls"):
                return TurnResult(
                    ok=True,
                    final_text=agents_summary(self.project.root),
                    mode=self.mode,
                )
            if a.startswith("clear"):
                await self._rebuild_system_prompt()
                return TurnResult(ok=True, final_text="custom agent cleared", mode=self.mode)
            ad = get_agent(self.project.root, a)
            if not ad:
                return TurnResult(
                    ok=False,
                    final_text="",
                    mode=self.mode,
                    error=f"agent not found: {a}",
                )
            await self._rebuild_system_prompt(extra_agent=ad.prompt_append())
            if ad.mode:
                await self.set_mode(ad.mode)
            return TurnResult(ok=True, final_text=f"agent → {ad.name}", mode=self.mode)

        if cmd in ("/rules", "/reload-rules"):
            await self._rebuild_system_prompt()
            from takton_code.agent.auto_classify import clear_rules_cache, load_rules

            clear_rules_cache()
            try:
                load_rules(project_root=self.project.root, force_reload=True)
            except Exception:
                pass
            info = self.project.to_inspect()
            return TurnResult(
                ok=True,
                final_text=(
                    "reloaded project context + auto_rules\n"
                    f"CODE={info.get('has_code_md')} AGENTS={info.get('has_agents_md')} "
                    f"CLAUDE={info.get('has_claude_md')}"
                ),
                mode=self.mode,
            )

        if cmd == "/memory":
            from takton_code.agent import memory_local as mem

            sub = arg.strip()
            if not sub or sub in ("show", "list", "cat"):
                body = mem.read_memory() or "(empty — enable agent.local_memory=true and /memory add …)"
                return TurnResult(ok=True, final_text=body, mode=self.mode)
            if sub in ("on", "enable"):
                # runtime only; suggest config
                self.settings_agent.local_memory = True  # type: ignore[attr-defined]
                await self._rebuild_system_prompt()
                return TurnResult(ok=True, final_text="local_memory ON (session); persist via config", mode=self.mode)
            if sub in ("off", "disable"):
                self.settings_agent.local_memory = False  # type: ignore[attr-defined]
                await self._rebuild_system_prompt()
                return TurnResult(ok=True, final_text="local_memory OFF (session)", mode=self.mode)
            if sub.startswith("add "):
                note = sub[4:].strip()
                p = mem.append_memory(note)
                if bool(getattr(self.settings_agent, "local_memory", False)):
                    await self._rebuild_system_prompt()
                return TurnResult(ok=True, final_text=f"appended → {p}", mode=self.mode)
            if sub in ("clear", "reset"):
                mem.clear_memory()
                return TurnResult(ok=True, final_text="memory cleared", mode=self.mode)
            return TurnResult(
                ok=False,
                final_text="",
                mode=self.mode,
                error="usage: /memory [show|on|off|add <note>|clear]",
            )

        if cmd == "/pr":
            from takton_code.project.pr_checkout import checkout_pr, gh_available

            pr = arg.strip()
            if not pr:
                return TurnResult(
                    ok=False,
                    final_text="",
                    mode=self.mode,
                    error="usage: /pr <number|url>  (requires gh CLI)",
                )
            if not gh_available():
                return TurnResult(ok=False, final_text="", mode=self.mode, error="gh not in PATH")
            res = checkout_pr(self.project.root, pr)
            if not res.get("ok"):
                return TurnResult(
                    ok=False,
                    final_text=res.get("output") or "",
                    mode=self.mode,
                    error=res.get("error") or f"exit {res.get('exit_code')}",
                )
            await self._rebuild_system_prompt()
            return TurnResult(ok=True, final_text=res.get("output") or "pr checked out", mode=self.mode)

        if cmd == "/status":
            assert self.compressor
            sess = await self.store.get_session(self.session_id or "")
            st = {
                "session_id": self.session_id,
                "slug": (sess or {}).get("slug"),
                "title": (sess or {}).get("title"),
                "mode": self.mode,
                "plan": self.plan_gate.to_dict(),
                "tokens": self.compressor.meter.status(self.messages),
                "usage_totals": self.usage_totals,
                "compress_count": self.compressor.compress_count,
                "messages": len(self.messages),
                "queue": await self.list_queue(),
                "llm_snapshot": self.llm_snapshot,
                "bridge": bool(getattr(self.bridge, "enabled", False)),
            }
            return TurnResult(ok=True, final_text=json.dumps(st, ensure_ascii=False, indent=2), mode=self.mode)

        if cmd == "/inspect":
            from takton_code.bridge.protocol import BRIDGE_ROUTES

            info: dict[str, Any] = {
                "project": self.project.to_inspect(),
                "llm": self.llm_snapshot,
                "mode": self.mode,
                "permission_cycle": "build → plan → always",
                "stream": self.stream,
                "subagents": bool(getattr(self.settings_agent, "enable_subagents", True)),
                "bridge_enabled": bool(getattr(self.bridge, "enabled", False)),
                "bridge_routes": BRIDGE_ROUTES,
                "ecosystem": "Takton Desktop bridge (skills/MCP/tools/RAG) — not local copies",
                "session": await self.store.get_session(self.session_id or ""),
                "usage_totals": self.usage_totals,
            }
            try:
                from takton_code.compat.backend_core import inspect_worktree_state

                info["worktrees"] = inspect_worktree_state(self.project.main_repo or self.project.root)
            except Exception as e:  # noqa: BLE001
                info["worktrees"] = {"error": str(e)}
            if self.bridge and getattr(self.bridge, "enabled", False):
                try:
                    info["bridge_health"] = await self.bridge.health()
                except Exception as e:  # noqa: BLE001
                    info["bridge_health"] = {"error": str(e)}
                for label, coro in (
                    ("desktop_models", self.bridge.list_models()),
                    ("desktop_skills", self.bridge.list_skills()),
                    ("desktop_tools", self.bridge.list_tools()),
                    ("desktop_mcp", self.bridge.list_mcp()),
                ):
                    try:
                        items = await coro
                        info[label] = {
                            "count": len(items),
                            "sample": [
                                getattr(x, "id", None) or getattr(x, "name", str(x)) for x in items[:12]
                            ],
                        }
                    except Exception as e:  # noqa: BLE001
                        info[label] = {"error": str(e)}
            else:
                info["desktop_hint"] = "bridge off — run with --bridge or start Desktop on :8090"
            return TurnResult(
                ok=True,
                final_text=json.dumps(info, ensure_ascii=False, indent=2, default=str),
                mode=self.mode,
            )

        if cmd == "/stop":
            self.request_cancel()
            return TurnResult(ok=True, final_text="cancel requested", mode=self.mode, interrupted=True)

        if cmd == "/enqueue":
            if not arg:
                return TurnResult(ok=False, final_text="", mode=self.mode, error="usage: /enqueue message")
            qid = await self.enqueue(arg)
            return TurnResult(ok=True, final_text=f"queued #{qid}", mode=self.mode)

        if cmd == "/queue":
            q = await self.list_queue()
            if not q:
                return TurnResult(ok=True, final_text="(queue empty)", mode=self.mode)
            body = "\n".join(f"#{i['id']}: {i['content'][:80]}" for i in q)
            return TurnResult(ok=True, final_text=body, mode=self.mode)

        if cmd == "/title" and arg and self.session_id:
            await self.store.update_session(self.session_id, title=arg.strip())
            return TurnResult(ok=True, final_text=f"title → {arg.strip()}", mode=self.mode)

        if cmd == "/fork" and self.session_id:
            nid = await self.store.fork_session(self.session_id)
            # copy todos
            try:
                todos = await self.store.list_todos(self.session_id)
                if todos:
                    await self.store.set_todos(
                        nid,
                        [{"content": t.get("content"), "status": t.get("status")} for t in todos],
                    )
            except Exception:
                pass
            self.emit("session_fork", from_id=self.session_id, to_id=nid)
            return TurnResult(
                ok=True,
                final_text=f"forked session {nid}\n(switch with sessions dashboard or -s {nid})",
                mode=self.mode,
            )

        if cmd == "/export" and self.session_id:
            from takton_code.session.export_fmt import write_export

            fmt = "json"
            a = arg.strip().lower()
            if a in ("md", "markdown"):
                fmt = "md"
            elif a in ("jsonl", "jsonlines"):
                fmt = "jsonl"
            elif a in ("json", ""):
                fmt = "json"
            elif a:
                # path or format
                if a.endswith(".md"):
                    fmt = "md"
                elif a.endswith(".jsonl"):
                    fmt = "jsonl"
            data = await self.store.export_session(self.session_id)
            path = write_export(Path(self.project.root), self.session_id, data, fmt=fmt)
            return TurnResult(ok=True, final_text=f"exported {path}", mode=self.mode)

        if cmd in ("/model", "/models"):
            # OpenClaw-style shallow model surface inside session
            from takton_code.config import apply_settings_json, load_settings
            from takton_code.llm.provider import build_llm_provider
            from takton_code.settings.models_guide import (
                PRESETS,
                apply_llm_patch,
                apply_preset,
                current_snapshot,
                format_status_table_rows,
            )

            arg_s = arg.strip()
            if not arg_s or arg_s in ("show", "status"):
                lines = [f"{k}: {v}" for k, v in format_status_table_rows()]
                lines.append("")
                lines.append("presets: " + ", ".join(PRESETS.keys()))
                lines.append("usage:")
                lines.append("  /model local              套用预设")
                lines.append("  /model <model名>         只改模型名")
                lines.append("  /model set url=... model=... key=...")
                return TurnResult(ok=True, final_text="\n".join(lines), mode=self.mode)

            if arg_s.startswith("set "):
                kv: dict[str, str] = {}
                for part in arg_s[4:].split():
                    if "=" in part:
                        k, v = part.split("=", 1)
                        kv[k.strip().lower()] = v.strip()
                apply_llm_patch(
                    base_url=kv.get("url") or kv.get("base_url"),
                    model=kv.get("model"),
                    api_key=kv.get("key") or kv.get("api_key"),
                )
            elif arg_s.lower() in PRESETS:
                apply_preset(arg_s.lower())
            else:
                apply_llm_patch(model=arg_s)

            st = apply_settings_json(load_settings())
            self.settings_llm = st.llm
            await self.llm.close()
            use_bridge = bool(
                st.bridge.enabled
                and st.bridge.use_desktop_models
                and getattr(self.bridge, "enabled", False)
            )
            self.llm = build_llm_provider(
                base_url=st.llm.base_url,
                api_key=st.llm.api_key,
                model=st.llm.model,
                temperature=st.llm.temperature,
                max_tokens=st.llm.max_tokens,
                bridge=self.bridge,
                use_bridge=use_bridge,
            )
            self.llm_snapshot = {
                "provider": "bridge" if use_bridge else "openai_compatible",
                "base_url": st.llm.base_url if not use_bridge else st.bridge.base_url,
                "model": st.llm.model,
                "temperature": st.llm.temperature,
                "max_tokens": st.llm.max_tokens,
                "context_window": st.llm.context_window,
            }
            if self.session_id:
                await self.store.update_session(
                    self.session_id,
                    llm_snapshot_json=json.dumps(self.llm_snapshot, ensure_ascii=False),
                )
            snap = current_snapshot(st)
            return TurnResult(
                ok=True,
                final_text=(
                    f"model → {snap['model']}\n"
                    f"base_url → {snap['base_url']}\n"
                    f"bridge → {snap['bridge_enabled']}"
                ),
                mode=self.mode,
            )

        if cmd == "/sessions":
            rows = await self.store.list_sessions(20)
            lines = [
                f"{r.get('slug') or r['id'][:8]}  {r.get('mode')}  {r.get('title')}  {r.get('status')}"
                for r in rows
            ]
            return TurnResult(ok=True, final_text="\n".join(lines) or "(none)", mode=self.mode)

        if cmd == "/worktree":
            from takton_code.compat.backend_core import WorktreeError, inspect_worktree_state, list_worktrees

            root = self.project.main_repo or self.project.root
            sub = arg.strip().split(maxsplit=1)
            action = (sub[0] if sub else "list").lower()
            rest = sub[1] if len(sub) > 1 else ""
            try:
                if action in ("list", "ls", ""):
                    items = list_worktrees(root)
                    lines = [
                        f"{'*' if i.path == str(self.project.root) else ' '} {i.name:20} {i.branch or '-':20} {i.path}"
                        for i in items
                    ]
                    hdr = f"main={root}\ncurrent_wt={self.project.worktree_name or '(main)'}\n"
                    return TurnResult(ok=True, final_text=hdr + ("\n".join(lines) or "(none)"), mode=self.mode)
                if action == "status":
                    return TurnResult(
                        ok=True,
                        final_text=json.dumps(inspect_worktree_state(root), ensure_ascii=False, indent=2),
                        mode=self.mode,
                    )
                return TurnResult(
                    ok=False,
                    final_text="",
                    mode=self.mode,
                    error="usage: /worktree [list|status]  (create via CLI: takton-code -w name)",
                )
            except WorktreeError as e:
                return TurnResult(ok=False, final_text="", mode=self.mode, error=str(e))

        return None
