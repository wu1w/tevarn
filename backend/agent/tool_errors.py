"""Tool error sanitization — extracted from the agent loop conductor.

Keeps loop.py free of error-message policy. Pure helpers; no I/O.
Internal next-step hints are English (model-facing).
"""

from __future__ import annotations


def tool_error_next_step(tool_name: str, exc_type: str, msg_lower: str) -> str:
    """Model-facing next-step hint (no internal path secrets)."""
    name = (tool_name or "").lower()
    if "notimplemented" in exc_type.lower() or "notimplemented" in msg_lower:
        return (
            "Next: restart the backend so ToolRegistry reloads; use command/python "
            "rather than unimplemented aliases."
        )
    if "permission" in msg_lower or "denied" in msg_lower or "not allowed" in msg_lower:
        return (
            "Next: check permission rules / workforce capability allowlist, "
            "or approve in the permission center and retry."
        )
    if "超出" in msg_lower or "cwd" in msg_lower and (
        "workspace" in msg_lower or "允许" in msg_lower
    ):
        return (
            "Next: set cwd inside the workspace, or configure TEVARN_DEV_ROOT / "
            "session workspace_root."
        )
    if (
        "not found" in msg_lower
        or "no such file" in msg_lower
        or "filenotfound" in exc_type.lower()
    ):
        return (
            "Next: glob/list to confirm the path, or use a workspace-relative path."
        )
    if "timeout" in msg_lower or "timed out" in msg_lower:
        return "Next: shrink the command scope, split the task, or raise timeout and retry."
    if "json" in msg_lower or "decode" in msg_lower or "parse" in msg_lower:
        return (
            "Next: check argument JSON and field names against the tool schema."
        )
    if name in ("file_read", "file_write", "edit", "glob", "grep"):
        return (
            "Next: confirm the path is inside the workspace; file_read/glob first if needed."
        )
    if name in ("command", "run_shell", "bash", "shell", "python"):
        return (
            "Next: on Windows try `cmd /c echo ok` / `where python`; "
            "for python tool pass short code=; do not send empty command."
        )
    if "ppt" in name or name == "generate_ppt":
        return (
            "Next: ensure python-pptx is installed; outline JSON then export pptx."
        )
    if "network" in msg_lower or "connection" in msg_lower or "http" in name:
        return "Next: check network/URL reachability, or use local cached content."
    return "Next: inspect backend logs for this tool name and retry."


def sanitize_tool_error(tool_name: str, exc: Exception) -> str:
    """Desensitize tool errors + next-step hint.

    Production mode does not return SQL/stack; debug mode includes detail.
    Never return a bare ``[Error]`` or exception class name alone (agents
    misread that as an unregistered executor).
    """
    import os

    exc_type = type(exc).__name__
    raw = str(exc or "").strip()
    if os.environ.get("TEVARN_DEBUG", "").lower() in ("1", "true", "yes"):
        return f"[Error] Failed to execute {tool_name}: {exc_type}: {raw or '(no message)'}"

    msg = raw[:200].lower()
    hint = tool_error_next_step(tool_name, exc_type, msg)
    if exc_type == "NotImplementedError" or "notimplemented" in msg:
        return (
            f"[Error] Tool {tool_name} execution path not ready ({exc_type}"
            f"{(': ' + raw) if raw else ''}). "
            f"{hint}"
        )
    return (
        f"[Error] Tool {tool_name} failed ({exc_type}"
        f"{(': ' + raw[:120]) if raw else ''}). "
        f"{hint}"
    )


# Back-compat aliases (loop / tests historically used leading underscore)
_sanitize_tool_error = sanitize_tool_error
_tool_error_next_step = tool_error_next_step
