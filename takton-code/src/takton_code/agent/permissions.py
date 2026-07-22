"""Per-tool permission gate — aligned with OpenCode last-match + Grok permission-mode.

Local evidence (2026-07, this machine):
- OpenCode `debug agent build|plan`: permission list last-match; plan has edit:deny;
  external_directory ask; doom_loop ask; *.env read ask.
- Grok: default|acceptEdits|auto|dontAsk|bypassPermissions|plan;
  always-approve still blocks plan-mode edits; headless prompt → cancel to model;
  deny rules still apply under bypassPermissions.
- Claude JSONL: permission-mode events; file-history-snapshot; queue-operation.
"""

from __future__ import annotations

import fnmatch
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

Decision = Literal["allow", "deny", "ask"]
Reply = Literal["allow", "deny", "always"]

# OpenCode-style permission *keys* (not only raw tool names)
# Local build agent uses keys: *, doom_loop, external_directory, read, edit, bash, task, ...
PERM_EDIT = "edit"
PERM_BASH = "bash"
PERM_READ = "read"
PERM_EXTERNAL = "external_directory"
PERM_DOOM = "doom_loop"
PERM_TASK = "task"


@dataclass
class PermissionRule:
    """One rule. LAST matching rule wins (OpenCode docs + binary skill text)."""

    key: str  # permission key or tool name or *
    decision: Decision
    pattern: str = "*"  # path/command glob; * = any


# Map Takton tools → OpenCode-like permission keys
TOOL_TO_KEY: dict[str, str] = {
    "file_write": PERM_EDIT,
    "edit_file": PERM_EDIT,
    "apply_patch": PERM_EDIT,
    "run_shell": PERM_BASH,
    "run_tests": PERM_BASH,
    "git_commit": PERM_BASH,
    "git_status": PERM_READ,
    "git_diff": PERM_READ,
    "file_read": PERM_READ,
    "grep": PERM_READ,
    "glob": PERM_READ,
    "todo_write": "todowrite",
    "todo_list": PERM_READ,
    "spawn_subagent": PERM_TASK,
    "doom_loop": PERM_DOOM,
    "desktop_invoke_tool": "desktop",
    "desktop_rag_search": PERM_READ,
    "list_desktop_skills": PERM_READ,
    "list_desktop_mcp": PERM_READ,
}


def _default_cautious() -> list[PermissionRule]:
    # broad first → narrow last (last match wins)
    return [
        PermissionRule("*", "allow"),
        PermissionRule(PERM_DOOM, "ask"),
        PermissionRule(PERM_EXTERNAL, "ask"),
        PermissionRule(PERM_READ, "allow"),
        PermissionRule(PERM_READ, "ask", pattern="*.env"),
        PermissionRule(PERM_READ, "ask", pattern="*.env.*"),
        PermissionRule(PERM_READ, "allow", pattern="*.env.example"),
        PermissionRule(PERM_EDIT, "allow"),
        PermissionRule(PERM_BASH, "ask"),
        PermissionRule("git_commit", "ask"),
        PermissionRule("desktop", "ask"),
        PermissionRule(PERM_TASK, "allow"),
    ]


def _default_accept_edits() -> list[PermissionRule]:
    # Grok acceptEdits: auto-approve file edits; shell still ask
    return [
        PermissionRule("*", "allow"),
        PermissionRule(PERM_EDIT, "allow"),
        PermissionRule(PERM_BASH, "ask"),
        PermissionRule("git_commit", "ask"),
        PermissionRule("desktop", "ask"),
        PermissionRule(PERM_EXTERNAL, "ask"),
        PermissionRule(PERM_DOOM, "ask"),
    ]


def _default_dont_ask() -> list[PermissionRule]:
    # Grok dontAsk: deny anything without explicit allow (CI/headless safe)
    return [
        PermissionRule("*", "deny"),
        PermissionRule(PERM_READ, "allow"),
        PermissionRule("grep", "allow"),
        PermissionRule("glob", "allow"),
        PermissionRule("git_status", "allow"),
        PermissionRule("git_diff", "allow"),
        PermissionRule("run_tests", "allow"),
        PermissionRule(PERM_EDIT, "allow"),  # edits allowed if you want CI to code; shell denied
    ]


def _default_free() -> list[PermissionRule]:
    # bypassPermissions-ish: allow all; deny rules still can be appended by user
    return [PermissionRule("*", "allow")]


def _default_plan() -> list[PermissionRule]:
    # OpenCode plan agent: edit deny after allow *
    return [
        PermissionRule("*", "allow"),
        PermissionRule(PERM_EDIT, "deny"),
        PermissionRule(PERM_BASH, "deny"),  # plan stays read-only for shell side-effects
        PermissionRule("git_commit", "deny"),
        PermissionRule(PERM_TASK, "allow"),  # explore subagent ok
        PermissionRule(PERM_READ, "allow"),
        PermissionRule(PERM_READ, "ask", pattern="*.env"),
        PermissionRule(PERM_EXTERNAL, "ask"),
    ]


def rules_for_profile(profile: str) -> list[PermissionRule]:
    p = (profile or "cautious").lower().replace("-", "").replace("_", "")
    if p in ("free", "always", "bypass", "bypasspermissions"):
        return _default_free()
    if p in ("acceptedits",):
        return _default_accept_edits()
    if p in ("dontask", "denybydefault"):
        return _default_dont_ask()
    if p in ("plan",):
        return _default_plan()
    if p in ("auto", "automode"):
        # Claude auto: start from cautious, classifier softens/asks
        return _default_cautious()
    return _default_cautious()


def _match_name(name: str, pattern: str) -> bool:
    if pattern == "*":
        return True
    return name == pattern or fnmatch.fnmatch(name, pattern)


def _match_path(path: str | None, pattern: str) -> bool:
    if pattern == "*" or not path:
        return True
    path = path.replace("\\", "/")
    return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path.split("/")[-1], pattern)


@dataclass
class PermissionGate:
    profile: str = "cautious"
    mode: str = "build"
    project_root: Path | None = None
    rules: list[PermissionRule] = field(default_factory=_default_cautious)
    session_allows: set[str] = field(default_factory=set)
    turn_allow_all: bool = False

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        # Grok pit: Always-approve ON still blocks plan-mode file edits
        if mode in ("plan", "ask", "explore"):
            # keep profile but overlay plan deny semantics via check()
            pass

    def add_session_allow(self, tool: str) -> None:
        self.session_allows.add(tool)
        key = TOOL_TO_KEY.get(tool, tool)
        self.session_allows.add(key)

    def _extract_path(self, tool: str, arguments: dict[str, Any] | None) -> str | None:
        args = arguments or {}
        for k in ("path", "file", "filepath"):
            if args.get(k):
                return str(args[k])
        return None

    def _is_external(self, path: str | None) -> bool:
        if not path or not self.project_root:
            return False
        try:
            p = Path(path)
            if not p.is_absolute():
                p = (self.project_root / p).resolve()
            else:
                p = p.resolve()
            p.relative_to(self.project_root.resolve())
            return False
        except Exception:
            return True

    def check(self, tool: str, arguments: dict[str, Any] | None = None) -> Decision:
        args = arguments or {}
        key = TOOL_TO_KEY.get(tool, tool)
        path = self._extract_path(tool, args)
        external = self._is_external(path)

        # Grok pit: always-approve does NOT unlock plan-mode edits
        if self.mode in ("plan", "ask", "explore"):
            if key == PERM_EDIT or tool in (
                "file_write",
                "edit_file",
                "apply_patch",
                "git_commit",
            ):
                return "deny"
            if tool == "run_shell" or key == PERM_BASH:
                return "deny"

        if tool in self.session_allows or key in self.session_allows or self.turn_allow_all:
            return "allow"

        matched: Decision | None = None
        for rule in self.rules:
            applies = False
            if rule.key == PERM_EXTERNAL:
                # only when path escapes project root
                applies = external and (
                    rule.pattern == "*" or _match_path(path, rule.pattern)
                )
            elif rule.key == "*":
                applies = True
            elif rule.key in (tool, key) or _match_name(tool, rule.key) or _match_name(key, rule.key):
                # inside-repo path patterns (*.env etc.)
                if external:
                    # outside repo: do not let in-repo read/edit allows shadow external_directory
                    # (OpenCode treats external_directory as its own gate)
                    applies = False
                elif rule.pattern == "*" or _match_path(path, rule.pattern):
                    applies = True
            if applies:
                matched = rule.decision

        # if external and never hit an external rule, default ask (OpenCode-ish safe)
        if external and matched is None:
            return "ask"
        if external:
            # re-walk only * + external so last external/default wins cleanly
            matched = None
            for rule in self.rules:
                if rule.key == "*":
                    matched = rule.decision
                elif rule.key == PERM_EXTERNAL and (
                    rule.pattern == "*" or _match_path(path, rule.pattern)
                ):
                    matched = rule.decision
            base_ext = matched or "ask"
            return self._maybe_auto(base_ext, tool, args)

        base = matched or "allow"
        return self._maybe_auto(base, tool, args)

    def _maybe_auto(self, base: Decision, tool: str, args: dict[str, Any]) -> Decision:
        p = (self.profile or "").lower().replace("-", "").replace("_", "")
        if p not in ("auto", "automode"):
            return base
        try:
            from takton_code.agent.auto_classify import apply_auto_classifier

            dec, _reason = apply_auto_classifier(
                base,
                tool,
                args,
                enabled=True,
                project_root=self.project_root,
            )
            return dec  # type: ignore[return-value]
        except Exception:
            return base

    def summarize(self, tool: str, arguments: dict[str, Any] | None) -> str:
        args = arguments or {}
        if tool == "run_shell":
            return f"bash: {str(args.get('command') or '')[:160]}"
        if tool in ("file_write", "edit_file", "apply_patch"):
            return f"edit: {args.get('path') or str(args.get('patch', ''))[:80]}"
        if tool == "git_commit":
            return f"git_commit: {args.get('message') or ''}"[:160]
        if self._is_external(self._extract_path(tool, args)):
            return f"external_directory: {self._extract_path(tool, args)}"
        return f"{tool} {str(args)[:120]}"


@dataclass
class PendingPermission:
    request_id: str
    tool: str
    arguments: dict[str, Any]
    summary: str
    future: Any
    created_at: float = field(default_factory=time.time)


class PermissionBroker:
    """Async ask/reply — Grok headless: cancel to model; interactive: Future."""

    def __init__(
        self,
        gate: PermissionGate,
        emit: Callable[..., None] | None = None,
        timeout_sec: float = 300.0,
        headless: bool = False,
    ) -> None:
        self.gate = gate
        self.emit = emit
        self.timeout_sec = timeout_sec
        self.headless = headless
        self.pending: dict[str, PendingPermission] = {}

    def _emit(self, typ: str, **payload: Any) -> None:
        if not self.emit:
            return
        try:
            self.emit(typ, **payload)
        except TypeError:
            self.emit({"type": typ, **payload})  # type: ignore[call-arg]

    async def require(self, tool: str, arguments: dict[str, Any] | None = None) -> Reply | Decision:
        import asyncio

        args = arguments or {}
        decision = self.gate.check(tool, args)
        if decision == "allow":
            return "allow"
        if decision == "deny":
            return "deny"

        # Grok pit: headless never blocks on stdin — cancel and report to model
        if self.headless:
            self._emit(
                "permission_resolved",
                tool=tool,
                decision="deny",
                reason="headless_prompt_cancelled",
                summary=self.gate.summarize(tool, args),
            )
            return "deny"

        request_id = f"perm_{uuid.uuid4().hex[:12]}"
        summary = self.gate.summarize(tool, args)
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[Reply] = loop.create_future()
        self.pending[request_id] = PendingPermission(
            request_id=request_id,
            tool=tool,
            arguments=args,
            summary=summary,
            future=fut,
        )
        self._emit(
            "permission_request",
            request_id=request_id,
            tool=tool,
            arguments=args,
            summary=summary,
        )
        try:
            reply: Reply = await asyncio.wait_for(fut, timeout=self.timeout_sec)
        except asyncio.TimeoutError:
            reply = "deny"
            self._emit(
                "permission_resolved",
                request_id=request_id,
                tool=tool,
                decision="deny",
                reason="timeout",
            )
        finally:
            self.pending.pop(request_id, None)

        if reply == "always":
            self.gate.add_session_allow(tool)
            reply_eff: Reply = "allow"
        else:
            reply_eff = reply
        self._emit(
            "permission_resolved",
            request_id=request_id,
            tool=tool,
            decision="always" if reply == "always" else reply_eff,
        )
        return reply_eff

    def answer(self, request_id: str, decision: Reply) -> bool:
        item = self.pending.get(request_id)
        if not item:
            return False
        if not item.future.done():
            item.future.set_result(decision)
        return True

    def answer_latest(self, decision: Reply) -> bool:
        if not self.pending:
            return False
        item = max(self.pending.values(), key=lambda p: p.created_at)
        return self.answer(item.request_id, decision)
