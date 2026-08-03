"""Shared prompt-cache breakpoint helpers (Anthropic-style cache_control).

Used by AnthropicService and optionally by OpenAI-compatible providers that
accept Anthropic-like content blocks (Qwen explicit, MiniMax explicit).
"""

from __future__ import annotations

from typing import Any


def mark_cache_breakpoint(blocks: list[dict[str, Any]]) -> None:
    """Mark the last block with ephemeral cache_control (in-place)."""
    if blocks:
        blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}


def _mark_message_content_cache(msg: dict[str, Any]) -> bool:
    """Convert/mark message content with cache_control. Returns True if marked."""
    content = msg.get("content")
    if isinstance(content, str):
        if not content.strip():
            return False
        msg["content"] = [
            {
                "type": "text",
                "text": content,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        return True
    if isinstance(content, list) and content:
        msg["content"] = list(content)
        mark_cache_breakpoint(msg["content"])
        return True
    return False


def apply_anthropic_style_cache(
    payload: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    max_breakpoints: int = 4,
    enabled: bool = True,
    mark_tools: bool = True,
) -> None:
    """Attach cache_control to system / tools / penultimate message.

    Works for:
    - Anthropic Messages API (system as str → list of text blocks)
    - Compatible APIs that accept content-block cache_control on messages
    """
    if not enabled:
        return

    breakpoints = 0

    # 1. system (Anthropic top-level field)
    sys_text = payload.get("system")
    if isinstance(sys_text, str) and sys_text.strip() and breakpoints < max_breakpoints:
        payload["system"] = [
            {
                "type": "text",
                "text": sys_text,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        breakpoints += 1
    elif isinstance(sys_text, list) and sys_text and breakpoints < max_breakpoints:
        mark_cache_breakpoint(sys_text)
        breakpoints += 1
    else:
        # OpenAI-compatible: first system role message
        for m in messages:
            if isinstance(m, dict) and m.get("role") == "system":
                if breakpoints < max_breakpoints and _mark_message_content_cache(m):
                    breakpoints += 1
                break

    # 2. tools — last tool definition (Anthropic tool objects; skip for OpenAI function tools)
    tools_payload = payload.get("tools")
    if (
        mark_tools
        and isinstance(tools_payload, list)
        and tools_payload
        and breakpoints < max_breakpoints
    ):
        # Prefer sorted order for stable prefix when callers didn't sort yet
        try:
            if all(isinstance(t, dict) and t.get("name") for t in tools_payload):
                tools_payload.sort(key=lambda t: str(t.get("name") or ""))
                payload["tools"] = tools_payload
        except Exception:
            pass
        mark_cache_breakpoint(tools_payload)
        breakpoints += 1

    # 3. history — penultimate message (leave latest turn uncached)
    # Prefer largest non-final content block when penultimate is tiny
    if len(messages) >= 2 and breakpoints < max_breakpoints:
        target = messages[-2]
        if isinstance(target, dict) and _mark_message_content_cache(target):
            breakpoints += 1


def apply_openai_prompt_cache_key(
    payload: dict[str, Any],
    *,
    cache_key: str | None,
    enabled: bool = True,
) -> None:
    """Inject OpenAI-style prompt_cache_key when supported."""
    if not enabled or not cache_key:
        return
    key = str(cache_key).strip()[:64]
    if key:
        payload["prompt_cache_key"] = key


def strip_cache_control(obj: Any) -> Any:
    """Recursively remove cache_control keys (fallback after 400)."""
    if isinstance(obj, dict):
        return {
            k: strip_cache_control(v)
            for k, v in obj.items()
            if k != "cache_control"
        }
    if isinstance(obj, list):
        return [strip_cache_control(x) for x in obj]
    return obj


def reorder_tools_before_system_messages(
    payload: dict[str, Any],
) -> None:
    """No-op for OpenAI chat payload shape (tools is sibling of messages).

    Documented for MiniMax: static tools + system first in *content* order.
    Callers should already put system messages first; this ensures tools key
    exists before messages in JSON serialization when possible.
    """
    if "tools" not in payload or "messages" not in payload:
        return
    # Rebuild payload key order: model, tools, messages, then rest
    ordered: dict[str, Any] = {}
    for k in ("model", "tools", "messages"):
        if k in payload:
            ordered[k] = payload[k]
    for k, v in payload.items():
        if k not in ordered:
            ordered[k] = v
    payload.clear()
    payload.update(ordered)
