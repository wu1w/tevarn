"""@device dispatch: run command on paired takton-agent and format reply card."""

from __future__ import annotations

import uuid
from typing import Any

from backend.services.remote.agent_router import parse_device_command
from backend.services.remote.transport import RemoteAgentError, transport_from_device_config


async def try_handle_at_device(user_id: uuid.UUID, message: str) -> str | None:
    """If message is `@name ...`, execute on device and return card text; else None."""
    parsed = parse_device_command(message)
    if parsed is None:
        return None

    from backend.api.dependencies import get_device_repo
    from backend.api.routes.devices import resolve_device_by_name

    repo = await get_device_repo()
    device = await resolve_device_by_name(repo, user_id, parsed.device_name)
    if device is None:
        return (
            f"🤖 @{parsed.device_name}\n"
            f"────────────────\n"
            f"❌ 未找到设备「{parsed.device_name}」。\n"
            f"请先在 /devices 配对 takton-agent（POST /api/devices/pair）。"
        )

    body = parsed.body.strip()
    # 约定：默认当 exec；list:path / read:path 前缀
    tr = transport_from_device_config(device.config or {})
    tr.timeout_s = 45.0
    try:
        if body.lower().startswith("list:") or body.lower().startswith("ls "):
            path = body.split(":", 1)[1].strip() if ":" in body else body[3:].strip() or "."
            result = await tr.call("file.list", {"path": path or "."})
            return _card(device.name, f"📁 list {path or '.'}", result, latency=None)
        if body.lower().startswith("read:"):
            path = body.split(":", 1)[1].strip()
            result = await tr.call("file.read", {"path": path})
            content = (result or {}).get("content", "")
            if len(content) > 2500:
                content = content[:2500] + "\n…[truncated]"
            return _card(device.name, f"📄 read {path}", content, latency=None)
        # exec
        result = await tr.call("exec.run", {"command": body})
        ping = None
        try:
            tr.timeout_s = 8.0
            ping = await tr.ping()
        except Exception:
            pass
        latency = (ping or {}).get("latency_ms") if isinstance(ping, dict) else None
        return _card_exec(device.name, body, result, latency=latency)
    except RemoteAgentError as e:
        return (
            f"🤖 @{device.name}\n"
            f"────────────────\n"
            f"❌ 连接/执行失败: {e.message}\n"
            f"请检查 agent 是否在线、token 是否正确。"
        )


def _card(name: str, title: str, payload: Any, latency: int | None) -> str:
    import json

    if isinstance(payload, (dict, list)):
        body = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        body = str(payload)
    if len(body) > 3500:
        body = body[:3500] + "\n…[truncated]"
    lat = f" · 延迟: {latency}ms" if latency is not None else ""
    return f"🤖 @{name} · {title}{lat}\n────────────────\n{body}"


def _card_exec(name: str, command: str, result: dict, latency: int | None) -> str:
    code = result.get("exit_code")
    out = (result.get("stdout") or "").strip()
    err = (result.get("stderr") or "").strip()
    parts = [f"$ {command}", f"exit={code}"]
    if out:
        parts.append(out)
    if err:
        parts.append(f"[stderr]\n{err}")
    body = "\n".join(parts)
    if len(body) > 3500:
        body = body[:3500] + "\n…[truncated]"
    ok = "✅" if code == 0 else "⚠️"
    lat = f" · 延迟: {latency}ms" if latency is not None else ""
    return f"🤖 @{name} 执行结果 {ok}{lat}\n────────────────\n{body}"
