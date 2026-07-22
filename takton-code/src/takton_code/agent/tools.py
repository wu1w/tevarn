"""Agent tools for coding."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from takton_code.diff.engine import DiffEngine


ToolHandler = Callable[[dict[str, Any]], Awaitable[str]]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    readonly: bool = False


def _tool_schema(spec: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
    }


class ToolRuntime:
    def __init__(
        self,
        project_root: Path,
        diff: DiffEngine,
        *,
        mode: str = "build",
        test_command: str | None = None,
        allow_git_commit: bool = True,
        allow_git_push: bool = False,
        bridge: Any | None = None,
        enable_subagent: bool = True,
        subagent_runner: Any | None = None,
        todo_store: Any | None = None,
        session_id: str | None = None,
        permission_broker: Any | None = None,
    ) -> None:
        self.root = project_root.resolve()
        self.diff = diff
        self.mode = mode
        self.test_command = test_command
        self.allow_git_commit = allow_git_commit
        self.allow_git_push = allow_git_push
        self.bridge = bridge
        self.enable_subagent = enable_subagent
        self.subagent_runner = subagent_runner  # async (agent, prompt, max_iter) -> str
        self.todo_store = todo_store
        self.session_id = session_id
        self.permission_broker = permission_broker
        self.specs: dict[str, ToolSpec] = {}
        self._register_builtin()

    def set_mode(self, mode: str) -> None:
        self.mode = mode

    def _is_readonly_mode(self) -> bool:
        # always == build with auto-approve (writes allowed)
        return self.mode in ("plan", "ask", "explore")

    def _register(self, spec: ToolSpec) -> None:
        self.specs[spec.name] = spec

    def openai_tools(self, *, readonly_only: bool | None = None) -> list[dict[str, Any]]:
        ro = readonly_only if readonly_only is not None else self._is_readonly_mode()
        out = []
        for s in self.specs.values():
            if ro and not s.readonly:
                continue
            if s.name == "spawn_subagent" and not self.enable_subagent:
                continue
            out.append(_tool_schema(s))
        return out

    def _register_builtin(self) -> None:
        self._register(
            ToolSpec(
                name="file_read",
                description="Read a UTF-8 text file under the project root.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path from project root"},
                        "offset": {"type": "integer", "description": "1-based start line", "default": 1},
                        "limit": {"type": "integer", "description": "max lines", "default": 400},
                    },
                    "required": ["path"],
                },
                handler=self.file_read,
                readonly=True,
            )
        )
        self._register(
            ToolSpec(
                name="file_write",
                description="Write full content to a file (create/overwrite). Prefer apply_patch for edits.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
                handler=self.file_write,
                readonly=False,
            )
        )
        self._register(
            ToolSpec(
                name="apply_patch",
                description="Apply a unified diff patch to one or more files under project root.",
                parameters={
                    "type": "object",
                    "properties": {
                        "patch": {"type": "string", "description": "unified diff text"},
                    },
                    "required": ["patch"],
                },
                handler=self.apply_patch,
                readonly=False,
            )
        )
        self._register(
            ToolSpec(
                name="edit_file",
                description="Exact string replacement in a file (unique old_string unless replace_all).",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_string": {"type": "string"},
                        "new_string": {"type": "string"},
                        "replace_all": {"type": "boolean", "default": False},
                    },
                    "required": ["path", "old_string", "new_string"],
                },
                handler=self.edit_file,
                readonly=False,
            )
        )
        self._register(
            ToolSpec(
                name="grep",
                description="Search file contents by regex under project root.",
                parameters={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "path": {"type": "string", "default": "."},
                        "glob": {"type": "string", "description": "optional glob filter"},
                        "max_matches": {"type": "integer", "default": 50},
                    },
                    "required": ["pattern"],
                },
                handler=self.grep,
                readonly=True,
            )
        )
        self._register(
            ToolSpec(
                name="glob",
                description="Find files by glob pattern under project root.",
                parameters={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "max_results": {"type": "integer", "default": 100},
                    },
                    "required": ["pattern"],
                },
                handler=self.glob,
                readonly=True,
            )
        )
        self._register(
            ToolSpec(
                name="run_shell",
                description="Run a shell command in project root. Avoid destructive commands.",
                parameters={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "timeout_sec": {"type": "integer", "default": 120},
                    },
                    "required": ["command"],
                },
                handler=self.run_shell,
                readonly=False,  # may have side effects; plan mode blocks non-readonly
            )
        )
        self._register(
            ToolSpec(
                name="run_tests",
                description="Run project test command. Returns exit code and output.",
                parameters={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "override test command"},
                    },
                },
                handler=self.run_tests,
                readonly=True,  # usually no source mutation
            )
        )
        self._register(
            ToolSpec(
                name="git_status",
                description="Git status --short and branch.",
                parameters={"type": "object", "properties": {}},
                handler=self.git_status,
                readonly=True,
            )
        )
        self._register(
            ToolSpec(
                name="git_diff",
                description="Show git diff (optional path).",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
                handler=self.git_diff,
                readonly=True,
            )
        )
        self._register(
            ToolSpec(
                name="git_commit",
                description="Stage given paths (or -A) and commit. Requires build mode.",
                parameters={
                    "type": "object",
                    "properties": {
                        "message": {"type": "string"},
                        "paths": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["message"],
                },
                handler=self.git_commit,
                readonly=False,
            )
        )
        # Desktop bridge tools (reserved — works when bridge enabled)
        self._register(
            ToolSpec(
                name="desktop_rag_search",
                description="Search Takton Desktop RAG/knowledge base (requires bridge).",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "top_k": {"type": "integer", "default": 5},
                    },
                    "required": ["query"],
                },
                handler=self.desktop_rag_search,
                readonly=True,
            )
        )
        self._register(
            ToolSpec(
                name="desktop_invoke_tool",
                description="Invoke a Takton Desktop/MCP tool by name (requires bridge).",
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "arguments": {"type": "object"},
                    },
                    "required": ["name"],
                },
                handler=self.desktop_invoke_tool,
                readonly=False,
            )
        )
        self._register(
            ToolSpec(
                name="list_desktop_skills",
                description="List skills from Takton Desktop bridge (requires bridge).",
                parameters={"type": "object", "properties": {}},
                handler=self.list_desktop_skills,
                readonly=True,
            )
        )
        self._register(
            ToolSpec(
                name="list_desktop_mcp",
                description="List MCP servers exposed by Takton Desktop bridge.",
                parameters={"type": "object", "properties": {}},
                handler=self.list_desktop_mcp,
                readonly=True,
            )
        )
        self._register(
            ToolSpec(
                name="todo_write",
                description="Replace session todo list (OpenCode todowrite style).",
                parameters={
                    "type": "object",
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "content": {"type": "string"},
                                    "status": {
                                        "type": "string",
                                        "description": "pending|in_progress|done",
                                    },
                                },
                            },
                        }
                    },
                    "required": ["items"],
                },
                handler=self.todo_write,
                readonly=False,
            )
        )
        self._register(
            ToolSpec(
                name="todo_list",
                description="List current session todos.",
                parameters={"type": "object", "properties": {}},
                handler=self.todo_list,
                readonly=True,
            )
        )
        self._register(
            ToolSpec(
                name="web_fetch",
                description="Fetch a public http(s) URL as text (read-only, size-capped). Prefer desktop bridge when available.",
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "max_bytes": {"type": "integer"},
                    },
                    "required": ["url"],
                },
                handler=self.web_fetch,
                readonly=True,
            )
        )
        if self.enable_subagent:
            self._register(
                ToolSpec(
                    name="spawn_subagent",
                    description=(
                        "Spawn a short nested agent. agent=explore (read-only search) or "
                        "general (can edit). Cannot nest further. Use for parallel research."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "agent": {
                                "type": "string",
                                "description": "explore | general",
                                "default": "explore",
                            },
                            "prompt": {"type": "string", "description": "Task for the subagent"},
                            "max_iterations": {"type": "integer", "default": 12},
                        },
                        "required": ["prompt"],
                    },
                    handler=self.spawn_subagent,
                    readonly=True,  # allowed in plan/explore for research; general writes inside child
                )
            )

    async def execute(self, name: str, arguments: dict[str, Any] | str) -> str:
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments else {}
            except json.JSONDecodeError:
                arguments = {"raw": arguments}
        spec = self.specs.get(name)
        if not spec:
            return f"ERROR: unknown tool {name}"
        if self._is_readonly_mode() and not spec.readonly:
            return f"ERROR: tool `{name}` is not allowed in {self.mode} mode (read-only)."
        args = arguments or {}
        if self.permission_broker is not None:
            try:
                decision = await self.permission_broker.require(name, args)
            except Exception as e:  # noqa: BLE001
                return f"ERROR: permission broker failed: {e}"
            if decision == "deny":
                return f"ERROR: permission denied for tool `{name}`"
        try:
            return await spec.handler(args)
        except PermissionError as e:
            return f"ERROR: {e}"
        except Exception as e:  # noqa: BLE001
            return f"ERROR: {type(e).__name__}: {e}"

    # --- handlers ---
    async def file_read(self, args: dict[str, Any]) -> str:
        path = self.diff.resolve(args["path"])
        if not path.is_file():
            return f"ERROR: not found: {args['path']}"
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        offset = max(1, int(args.get("offset") or 1))
        limit = max(1, min(2000, int(args.get("limit") or 400)))
        chunk = lines[offset - 1 : offset - 1 + limit]
        body = "\n".join(f"{i}|{ln}" for i, ln in enumerate(chunk, start=offset))
        return f"{args['path']} ({len(lines)} lines)\n{body}"

    async def file_write(self, args: dict[str, Any]) -> str:
        path = self.diff.resolve(args["path"])
        self.diff.snapshot_before(str(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        content = args.get("content")
        if content is None:
            return "ERROR: content required"
        path.write_text(str(content), encoding="utf-8")
        ch = self.diff.record_after(str(path))
        return f"OK wrote {args['path']} ({ch.op if ch else 'no-op'})"

    async def edit_file(self, args: dict[str, Any]) -> str:
        path = self.diff.resolve(args["path"])
        if not path.is_file():
            return f"ERROR: not found: {args['path']}"
        old = args["old_string"]
        new = args["new_string"]
        replace_all = bool(args.get("replace_all"))
        text = path.read_text(encoding="utf-8", errors="replace")
        count = text.count(old)
        if count == 0:
            return "ERROR: old_string not found"
        if count > 1 and not replace_all:
            return f"ERROR: old_string found {count} times; set replace_all=true or provide more context"
        self.diff.snapshot_before(str(path))
        path.write_text(text.replace(old, new) if replace_all else text.replace(old, new, 1), encoding="utf-8")
        ch = self.diff.record_after(str(path))
        return f"OK edited {args['path']} ({ch.op if ch else 'no-op'}, replacements={count if replace_all else 1})"

    async def apply_patch(self, args: dict[str, Any]) -> str:
        patch = args.get("patch") or ""
        if not patch.strip():
            return "ERROR: empty patch"
        # minimal unified diff applier for single/multi file
        results: list[str] = []
        files = _parse_unified_diff(patch)
        if not files:
            return "ERROR: could not parse unified diff"
        for fpath, old_lines, new_lines in files:
            path = self.diff.resolve(fpath)
            self.diff.snapshot_before(str(path))
            if new_lines is None:
                if path.exists():
                    path.unlink()
                results.append(f"deleted {fpath}")
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                # if old exists, verify loosely or just write new
                content = "".join(new_lines)
                path.write_text(content, encoding="utf-8")
                results.append(f"wrote {fpath} ({len(new_lines)} lines)")
            self.diff.record_after(str(path))
        return "OK apply_patch:\n" + "\n".join(results)

    async def grep(self, args: dict[str, Any]) -> str:
        pattern = args["pattern"]
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return f"ERROR: bad regex: {e}"
        rel = args.get("path") or "."
        base = self.diff.resolve(rel) if rel != "." else self.root
        glob_pat = args.get("glob")
        max_matches = int(args.get("max_matches") or 50)
        hits: list[str] = []
        paths: list[Path]
        if base.is_file():
            paths = [base]
        else:
            if glob_pat:
                paths = [p for p in base.rglob(glob_pat) if p.is_file()]
            else:
                paths = [p for p in base.rglob("*") if p.is_file()]
        skip_parts = {".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", "dist", "build"}
        for p in paths:
            if any(part in skip_parts for part in p.parts):
                continue
            if p.stat().st_size > 1_500_000:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    rel_s = str(p.relative_to(self.root)).replace("\\", "/")
                    hits.append(f"{rel_s}:{i}:{line[:240]}")
                    if len(hits) >= max_matches:
                        return "\n".join(hits)
        return "\n".join(hits) if hits else "(no matches)"

    async def glob(self, args: dict[str, Any]) -> str:
        pattern = args["pattern"]
        max_results = int(args.get("max_results") or 100)
        paths = []
        for p in self.root.glob(pattern):
            if p.is_file():
                paths.append(str(p.relative_to(self.root)).replace("\\", "/"))
            if len(paths) >= max_results:
                break
        if not paths:
            # try rglob if pattern has no slash magic at root
            for p in self.root.rglob(pattern):
                if any(x in p.parts for x in (".git", "node_modules", ".venv", "__pycache__")):
                    continue
                if p.is_file():
                    paths.append(str(p.relative_to(self.root)).replace("\\", "/"))
                if len(paths) >= max_results:
                    break
        return "\n".join(paths) if paths else "(no files)"

    async def run_shell(self, args: dict[str, Any]) -> str:
        cmd = args.get("command") or ""
        if not cmd.strip():
            return "ERROR: empty command"
        if _is_dangerous(cmd):
            return f"ERROR: blocked dangerous command: {cmd}"
        timeout = int(args.get("timeout_sec") or 120)
        return await _run_cmd(cmd, self.root, timeout)

    async def run_tests(self, args: dict[str, Any]) -> str:
        cmd = args.get("command") or self.test_command or "python -m pytest -q"
        return await _run_cmd(cmd, self.root, 180)

    async def git_status(self, args: dict[str, Any]) -> str:
        return await _run_cmd("git status -sb", self.root, 15)

    async def git_diff(self, args: dict[str, Any]) -> str:
        path = args.get("path") or ""
        cmd = f"git diff -- {path}" if path else "git diff"
        return await _run_cmd(cmd, self.root, 30)

    async def git_commit(self, args: dict[str, Any]) -> str:
        if not self.allow_git_commit:
            return "ERROR: git_commit disabled in settings"
        msg = args.get("message") or ""
        if not msg.strip():
            return "ERROR: message required"
        paths = args.get("paths") or []
        if paths:
            for p in paths:
                # validate path under root
                self.diff.resolve(p)
            add_cmd = "git add -- " + " ".join(f'"{p}"' for p in paths)
        else:
            add_cmd = "git add -A"
        r1 = await _run_cmd(add_cmd, self.root, 30)
        # commit
        # use subprocess list form for message safety
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "commit",
                "-m",
                msg,
                cwd=str(self.root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
            out = (out_b or b"").decode("utf-8", errors="replace")
            return f"add:\n{r1}\ncommit exit={proc.returncode}\n{out}"
        except Exception as e:  # noqa: BLE001
            return f"ERROR commit: {e}"

    async def desktop_rag_search(self, args: dict[str, Any]) -> str:
        if not self.bridge or not getattr(self.bridge, "enabled", False):
            return "ERROR: desktop bridge not enabled (reserved interface). Set bridge.enabled=true when Desktop is available."
        from takton_code.bridge.protocol import RAGQuery

        hits = await self.bridge.rag_search(RAGQuery(query=args["query"], top_k=int(args.get("top_k") or 5)))
        if not hits:
            return "(no hits)"
        return "\n\n".join(f"[{h.score}] {h.source}\n{h.content[:1000]}" for h in hits)

    async def desktop_invoke_tool(self, args: dict[str, Any]) -> str:
        if not self.bridge or not getattr(self.bridge, "enabled", False):
            return "ERROR: desktop bridge not enabled (reserved interface)."
        from takton_code.bridge.protocol import ToolInvokeRequest

        res = await self.bridge.invoke_tool(
            ToolInvokeRequest(
                name=args["name"],
                arguments=args.get("arguments") or {},
                project_root=str(self.root),
            )
        )
        if not res.ok:
            return f"ERROR: {res.error}"
        return res.output

    async def list_desktop_skills(self, args: dict[str, Any]) -> str:
        if not self.bridge or not getattr(self.bridge, "enabled", False):
            return "ERROR: desktop bridge not enabled — start Takton Desktop or --bridge."
        skills = await self.bridge.list_skills()
        if not skills:
            return "(no skills)"
        return "\n".join(f"- {s.name}: {s.description[:120]}" for s in skills)

    async def list_desktop_mcp(self, args: dict[str, Any]) -> str:
        if not self.bridge or not getattr(self.bridge, "enabled", False):
            return "ERROR: desktop bridge not enabled — MCP lives on Takton Desktop."
        try:
            servers = await self.bridge.list_mcp()
        except Exception as e:  # noqa: BLE001
            return f"ERROR: list_mcp failed: {e}"
        if not servers:
            return "(no MCP servers on desktop)"
        lines = []
        for s in servers:
            tools = ",".join(s.tools[:12]) if getattr(s, "tools", None) else ""
            lines.append(f"- {s.name} [{s.status}] tools={tools}")
        return "\n".join(lines)

    async def todo_write(self, args: dict[str, Any]) -> str:
        items = args.get("items") or []
        if self.todo_store is None or not self.session_id:
            return f"OK (ephemeral) todos={len(items)}"
        # normalize status
        norm = []
        for it in items:
            if not isinstance(it, dict):
                continue
            st = str(it.get("status") or "pending").lower()
            if st in ("completed", "complete", "done"):
                st = "done"
            elif st in ("in_progress", "doing", "active"):
                st = "in_progress"
            else:
                st = "pending"
            norm.append({"content": it.get("content") or it.get("title") or "", "status": st})
        await self.todo_store.set_todos(self.session_id, norm)
        return f"OK todos={len(norm)}\n" + "\n".join(
            f"[{x['status']}] {x['content']}" for x in norm
        )

    async def todo_list(self, args: dict[str, Any]) -> str:
        if self.todo_store is None or not self.session_id:
            return "(no todo store)"
        rows = await self.todo_store.list_todos(self.session_id)
        if not rows:
            return "(empty)"
        return "\n".join(f"[{r.get('status')}] {r.get('content')}" for r in rows)

    async def web_fetch(self, args: dict[str, Any]) -> str:
        # respect settings if parent stashed flag on runtime tools via attribute
        if getattr(self, "allow_web_fetch", True) is False:
            return "ERROR: web_fetch disabled in settings (agent.allow_web_fetch=false)"
        url = str(args.get("url") or "").strip()
        if not url.startswith("http://") and not url.startswith("https://"):
            return "ERROR: url must be http(s)"
        # prefer bridge if present
        if self.bridge and getattr(self.bridge, "enabled", False):
            try:
                from takton_code.bridge.protocol import ToolInvokeRequest

                res = await self.bridge.invoke_tool(
                    ToolInvokeRequest(name="web_fetch", arguments={"url": url}, project_root=str(self.root))
                )
                if res is not None:
                    return str(getattr(res, "output", None) or res)[:20000]
            except Exception:
                pass
        max_b = int(args.get("max_bytes") or 500_000)
        try:
            import urllib.request

            req = urllib.request.Request(
                url,
                headers={"User-Agent": "TaktonCode/0.1 (+local-readonly-fetch)"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                raw = resp.read(max_b + 1)
            if len(raw) > max_b:
                raw = raw[:max_b]
                truncated = True
            else:
                truncated = False
            text = raw.decode("utf-8", errors="replace")
            if truncated:
                text += "\n…[truncated]"
            return text[:20000]
        except Exception as e:  # noqa: BLE001
            return f"ERROR: web_fetch failed: {e}"

    async def spawn_subagent(self, args: dict[str, Any]) -> str:
        if not self.enable_subagent:
            return "ERROR: subagents disabled"
        if self.subagent_runner is None:
            return "ERROR: subagent runner not configured"
        from takton_code.agent.subagent import parse_spawn_args

        agent, prompt, max_iter = parse_spawn_args(args)
        if not prompt:
            return "ERROR: prompt required"
        return await self.subagent_runner(agent, prompt, max_iter)


_DANGEROUS = [
    r"\brm\s+-rf\s+/",
    r"\bformat\b",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r"\bshutdown\b",
    r"\breboot\b",
    r">\s*/dev/sd",
    r"git\s+push\s+.*--force",
    r"git\s+push\s+-f\b",
]


def _is_dangerous(cmd: str) -> bool:
    for p in _DANGEROUS:
        if re.search(p, cmd, re.I):
            return True
    return False


async def _run_cmd(cmd: str, cwd: Path, timeout: int) -> str:
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        out = (out_b or b"").decode("utf-8", errors="replace")
        if len(out) > 30000:
            out = out[:30000] + "\n…[truncated]"
        return f"exit={proc.returncode}\n{out}"
    except asyncio.TimeoutError:
        return f"ERROR: timeout after {timeout}s"
    except Exception as e:  # noqa: BLE001
        return f"ERROR: {e}"


def _parse_unified_diff(patch: str) -> list[tuple[str, list[str] | None, list[str] | None]]:
    """Return list of (path, old_lines|None, new_lines|None). Simplified applier: uses +++ path and hunk rebuild."""
    lines = patch.splitlines(keepends=True)
    results: list[tuple[str, list[str] | None, list[str] | None]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- "):
            # pair with +++
            minus = line[4:].strip()
            i += 1
            if i >= len(lines) or not lines[i].startswith("+++ "):
                continue
            plus = lines[i][4:].strip()
            i += 1
            path = plus[2:] if plus.startswith("b/") else plus
            if path == "/dev/null":
                path = minus[2:] if minus.startswith("a/") else minus
            # collect hunks → new file content via applying to empty/old is hard;
            # strategy: accumulate lines with + and context as new file image when possible
            new_lines: list[str] = []
            old_lines: list[str] = []
            saw_hunk = False
            while i < len(lines) and not lines[i].startswith("--- "):
                hl = lines[i]
                if hl.startswith("@@"):
                    saw_hunk = True
                    i += 1
                    continue
                if hl.startswith("+") and not hl.startswith("+++"):
                    new_lines.append(hl[1:])
                elif hl.startswith("-") and not hl.startswith("---"):
                    old_lines.append(hl[1:])
                elif hl.startswith("\\"):
                    pass
                elif hl.startswith(" "):
                    new_lines.append(hl[1:])
                    old_lines.append(hl[1:])
                elif hl.startswith("diff ") or hl.startswith("index "):
                    break
                else:
                    # unknown — stop this file
                    if saw_hunk:
                        break
                i += 1
            if plus.strip() == "/dev/null":
                results.append((path, old_lines or None, None))
            else:
                results.append((path, old_lines or None, new_lines))
            continue
        i += 1
    return results
