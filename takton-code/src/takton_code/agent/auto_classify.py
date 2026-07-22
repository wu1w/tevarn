"""Lightweight auto-mode permission classifier — configurable rules.

Claude auto mode uses a cloud LLM classifier. Takton uses local heuristics
plus optional TOML rules (no phone-home).

Load order (later overrides / extends):
  1. built-in defaults
  2. ~/.takton-code/auto_rules.toml
  3. <project>/.takton/auto_rules.toml
  4. TAKTON_CODE_AUTO_RULES path if set
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Verdict = Literal["allow", "ask", "deny"]

DEFAULT_RULES_TOML = """\
# Takton Code auto-mode rules (local, no cloud)
# Docs: deny > ask > allow within each match; first matching custom rule wins by order
# for tool-specific overrides; shell patterns scanned deny-first then ask.

[settings]
allow_risk_max = 0.4
ask_escalate_min = 0.6

# Shell command regex → deny
[[deny]]
on = "command"
pattern = '''\\brm\\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)'''

[[deny]]
on = "command"
pattern = '''\\bmkfs\\b'''

[[deny]]
on = "command"
pattern = '''\\bdd\\s+if='''

[[deny]]
on = "command"
pattern = '''\\b(curl|wget)\\b.*\\|\\s*(ba)?sh'''

[[deny]]
on = "command"
pattern = '''\\bgit\\s+push\\s+.*--force'''

[[deny]]
on = "command"
pattern = '''\\bgit\\s+reset\\s+--hard'''

[[deny]]
on = "command"
pattern = '''\\bDROP\\s+(TABLE|DATABASE)\\b'''

# Shell command regex → ask
[[ask]]
on = "command"
pattern = '''\\bsudo\\b'''

[[ask]]
on = "command"
pattern = '''\\bchmod\\s+777\\b'''

[[ask]]
on = "command"
pattern = '''\\b(npm\\s+publish|pip\\s+install|uv\\s+pip\\s+install)\\b'''

[[ask]]
on = "command"
pattern = '''\\b(curl|wget|scp|rsync)\\b'''

[[ask]]
on = "command"
pattern = '''\\bgit\\s+(push|commit)\\b'''

# Path edits
[[ask]]
on = "path"
pattern = '''.*\\.env$'''

[[ask]]
on = "path"
pattern = '''.*\\.(pem|key)$'''

[[ask]]
on = "path"
pattern = '''.*/\\.ssh/.*'''

# Tool-level
[[allow]]
on = "tool"
name = "run_tests"

[[allow]]
on = "tool"
name = "file_read"

[[ask]]
on = "tool"
name = "git_commit"

[[ask]]
on = "tool"
name = "desktop_invoke_tool"
"""


@dataclass
class ClassifyResult:
    decision: Verdict
    reason: str
    risk: float  # 0..1


@dataclass
class RuleSet:
    deny_command: list[str] = field(default_factory=list)
    ask_command: list[str] = field(default_factory=list)
    ask_path: list[str] = field(default_factory=list)
    allow_tools: set[str] = field(default_factory=set)
    ask_tools: set[str] = field(default_factory=set)
    deny_tools: set[str] = field(default_factory=set)
    allow_risk_max: float = 0.4
    ask_escalate_min: float = 0.6
    sources: list[str] = field(default_factory=list)


_CACHED: RuleSet | None = None
_CACHED_KEY: str = ""
_RELOAD_COUNT: int = 0
_LAST_RELOAD_INFO: str = ""


def _parse_toml_rules(text: str, source: str) -> RuleSet:
    rs = RuleSet(sources=[source])
    data = tomllib.loads(text)
    settings = data.get("settings") or {}
    rs.allow_risk_max = float(settings.get("allow_risk_max", 0.4))
    rs.ask_escalate_min = float(settings.get("ask_escalate_min", 0.6))

    def _add_list(key: str, decision: Verdict) -> None:
        for item in data.get(key) or []:
            if not isinstance(item, dict):
                continue
            on = str(item.get("on") or "command").lower()
            if on == "command" and item.get("pattern"):
                if decision == "deny":
                    rs.deny_command.append(str(item["pattern"]))
                elif decision == "ask":
                    rs.ask_command.append(str(item["pattern"]))
            elif on == "path" and item.get("pattern"):
                rs.ask_path.append(str(item["pattern"]))
            elif on == "tool" and item.get("name"):
                name = str(item["name"]).lower()
                if decision == "allow":
                    rs.allow_tools.add(name)
                elif decision == "ask":
                    rs.ask_tools.add(name)
                elif decision == "deny":
                    rs.deny_tools.add(name)

    _add_list("deny", "deny")
    _add_list("ask", "ask")
    _add_list("allow", "allow")
    return rs


def _merge(base: RuleSet, extra: RuleSet) -> RuleSet:
    return RuleSet(
        deny_command=list(base.deny_command) + list(extra.deny_command),
        ask_command=list(base.ask_command) + list(extra.ask_command),
        ask_path=list(base.ask_path) + list(extra.ask_path),
        allow_tools=set(base.allow_tools) | set(extra.allow_tools),
        ask_tools=set(base.ask_tools) | set(extra.ask_tools),
        deny_tools=set(base.deny_tools) | set(extra.deny_tools),
        allow_risk_max=extra.allow_risk_max if extra.sources else base.allow_risk_max,
        ask_escalate_min=extra.ask_escalate_min if extra.sources else base.ask_escalate_min,
        sources=list(base.sources) + list(extra.sources),
    )


def ensure_default_rules_file(home: Path | None = None) -> Path:
    from takton_code.config import home_dir

    h = Path(home) if home else home_dir()
    path = h / "auto_rules.toml"
    if not path.exists():
        h.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_RULES_TOML, encoding="utf-8")
    return path


def load_rules(
    *,
    project_root: Path | None = None,
    home: Path | None = None,
    force_reload: bool = False,
) -> RuleSet:
    """Load rules with mtime-based hot reload (no watcher process needed)."""
    global _CACHED, _CACHED_KEY, _RELOAD_COUNT, _LAST_RELOAD_INFO
    from takton_code.config import home_dir

    h = Path(home) if home else home_dir()
    paths: list[Path] = []
    user = h / "auto_rules.toml"
    paths.append(user)
    if project_root:
        paths.append(Path(project_root) / ".takton" / "auto_rules.toml")
    env_p = os.environ.get("TAKTON_CODE_AUTO_RULES", "").strip()
    if env_p:
        paths.append(Path(env_p).expanduser())

    key_parts = []
    for p in paths:
        try:
            mtime = p.stat().st_mtime if p.is_file() else 0
        except OSError:
            mtime = 0
        key_parts.append(f"{p}:{mtime}")
    key = "|".join(key_parts)

    if not force_reload and _CACHED is not None and key == _CACHED_KEY:
        return _CACHED

    # ensure user file exists with defaults
    if not user.exists():
        ensure_default_rules_file(h)

    rs = _parse_toml_rules(DEFAULT_RULES_TOML, "builtin")
    loaded_files: list[str] = []
    for p in paths:
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
            extra = _parse_toml_rules(text, str(p))
            rs = _merge(rs, extra)
            loaded_files.append(str(p))
        except Exception:
            continue

    reloaded = _CACHED is not None or force_reload
    _CACHED = rs
    _CACHED_KEY = key
    if reloaded or _RELOAD_COUNT == 0:
        _RELOAD_COUNT += 1
        import time

        _LAST_RELOAD_INFO = (
            f"reload#{_RELOAD_COUNT} @ {time.strftime('%H:%M:%S')} "
            f"files={loaded_files or ['builtin-only']}"
        )
    return rs


def rules_reload_info() -> str:
    return _LAST_RELOAD_INFO or "(not loaded yet)"


def clear_rules_cache() -> None:
    global _CACHED, _CACHED_KEY
    _CACHED = None
    _CACHED_KEY = ""


def _match_any(text: str, patterns: list[str]) -> str | None:
    for pat in patterns:
        try:
            if re.search(pat, text, re.I):
                return pat
        except re.error:
            continue
    return None


def classify_tool_call(
    tool: str,
    arguments: dict[str, Any] | None = None,
    *,
    rules: RuleSet | None = None,
    project_root: Path | None = None,
) -> ClassifyResult:
    args = arguments or {}
    name = (tool or "").lower()
    rs = rules or load_rules(project_root=project_root)

    # Tool-level deny/allow/ask first
    if name in rs.deny_tools:
        return ClassifyResult("deny", f"tool deny rule: {name}", 0.95)
    if name in rs.allow_tools:
        return ClassifyResult("allow", f"tool allow rule: {name}", 0.1)
    if name in rs.ask_tools:
        return ClassifyResult("ask", f"tool ask rule: {name}", 0.55)

    # Built-in read-only baseline
    if name in (
        "file_read",
        "grep",
        "glob",
        "git_status",
        "git_diff",
        "todo_list",
        "list_desktop_skills",
        "list_desktop_mcp",
        "desktop_rag_search",
    ):
        return ClassifyResult("allow", "read-only tool", 0.05)

    if name in ("todo_write",):
        return ClassifyResult("allow", "session todo", 0.1)

    if name in ("file_write", "edit_file", "apply_patch"):
        path = str(args.get("path") or "").replace("\\", "/")
        hit = _match_any(path, rs.ask_path)
        if hit:
            return ClassifyResult("ask", f"path rule: {hit}", 0.7)
        return ClassifyResult("allow", "source edit", 0.25)

    if name in ("run_shell", "run_tests", "git_commit"):
        cmd = str(args.get("command") or args.get("message") or "")
        if name == "run_tests":
            return ClassifyResult("allow", "test runner", 0.2)
        if name == "git_commit":
            return ClassifyResult("ask", "git commit", 0.55)
        hit = _match_any(cmd, rs.deny_command)
        if hit:
            return ClassifyResult("deny", f"deny pattern: {hit}", 0.95)
        hit = _match_any(cmd, rs.ask_command)
        if hit:
            return ClassifyResult("ask", f"ask pattern: {hit}", 0.65)
        if not cmd.strip():
            return ClassifyResult("ask", "empty command", 0.5)
        return ClassifyResult("allow", "benign shell", 0.35)

    if name == "spawn_subagent":
        return ClassifyResult("allow", "subagent", 0.3)

    if name.startswith("desktop_"):
        return ClassifyResult("ask", "desktop bridge invoke", 0.5)

    return ClassifyResult("ask", "unknown tool", 0.5)


def apply_auto_classifier(
    base: Literal["allow", "deny", "ask"],
    tool: str,
    arguments: dict[str, Any] | None,
    *,
    enabled: bool,
    project_root: Path | None = None,
    rules: RuleSet | None = None,
) -> tuple[Literal["allow", "deny", "ask"], str | None]:
    if not enabled:
        return base, None
    if base == "deny":
        return "deny", "rule deny"
    rs = rules or load_rules(project_root=project_root)
    c = classify_tool_call(tool, arguments, rules=rs, project_root=project_root)
    if c.decision == "deny":
        return "deny", c.reason
    if base == "allow":
        if c.decision == "ask" and c.risk >= rs.ask_escalate_min:
            return "ask", c.reason
        if c.decision == "deny":
            return "deny", c.reason
        return "allow", c.reason
    # base ask
    if c.decision == "allow" and c.risk <= rs.allow_risk_max:
        return "allow", c.reason
    if c.decision == "deny":
        return "deny", c.reason
    return "ask", c.reason


def format_rules_summary(rs: RuleSet | None = None) -> str:
    r = rs or load_rules()
    lines = [
        f"sources: {', '.join(r.sources) or 'builtin'}",
        f"deny_command={len(r.deny_command)} ask_command={len(r.ask_command)} "
        f"ask_path={len(r.ask_path)}",
        f"tools allow={sorted(r.allow_tools)} ask={sorted(r.ask_tools)} deny={sorted(r.deny_tools)}",
        f"thresholds allow_risk_max={r.allow_risk_max} ask_escalate_min={r.ask_escalate_min}",
        f"hot-reload: mtime-watched on each classify; {rules_reload_info()}",
    ]
    return "\n".join(lines)
