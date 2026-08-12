"""Web chat slash commands (/help /stop /compact …).

Channel gateway has its own handlers; this module serves the in-app chat WS path
so typing /help in the UI actually works.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from backend.services.slash_commands import (
    TOOLSET_PRESETS,
    build_help_text,
    build_toolset_list_text,
    resolve_command,
)

logger = logging.getLogger(__name__)


async def try_handle_web_slash(
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID | str | None,
    text: str,
) -> str | None:
    """If text is a known /command, execute and return reply text; else None."""
    cmd, args = resolve_command(text or "")
    if cmd is None:
        # Unknown /foo → still intercept so it doesn't burn an LLM turn
        stripped = (text or "").strip()
        if stripped.startswith("/") and len(stripped) > 1 and not stripped.startswith("//"):
            name = stripped[1:].split()[0].lower()
            if name.isidentifier() or name.replace("-", "").replace("_", "").isalnum():
                return (
                    f"⚠️ 未知命令: /{name}\n\n" + build_help_text()
                )
        return None

    try:
        if cmd.name in ("help", "commands"):
            return build_help_text().replace(
                "Tevarn Channel 命令列表", "Tevarn 命令列表（Web 聊天）"
            )
        if cmd.name == "stop":
            return await _cmd_stop(session_id)
        if cmd.name in ("new", "reset"):
            return await _cmd_new(session_id, user_id)
        if cmd.name == "compact":
            return await _cmd_compact(session_id, args)
        if cmd.name == "status":
            return await _cmd_status(session_id)
        if cmd.name == "model":
            return await _cmd_model(session_id, args)
        if cmd.name == "tools":
            return await _cmd_tools(session_id, args)
        if cmd.name == "toolset":
            return await _cmd_toolset(session_id, args)
        if cmd.name == "goal":
            return await _cmd_goal(session_id, args)
        return f"⚠️ 命令 /{cmd.name} 尚未在 Web 聊天实现"
    except Exception as e:
        logger.exception("web slash /%s failed: %s", cmd.name, e)
        return f"❌ 命令执行失败: {e}"


async def _cmd_stop(session_id: uuid.UUID) -> str:
    try:
        from backend.api.websocket import manager as ws_manager

        stopped = False
        if ws_manager.stop_agent_loop(session_id):
            stopped = True
        await ws_manager.cancel_agent(session_id, wait=3.0)
        ws_manager.end_run_snapshot(session_id)
        try:
            ws_manager.bump_run_generation(session_id)
        except Exception:
            pass
        await ws_manager.broadcast(
            session_id,
            {"type": "status", "state": "idle", "detail": "Stopped by /stop"},
        )
        return "🛑 已停止当前运行" if stopped else "🛑 已发送停止（若无运行中任务则无操作）"
    except Exception as e:
        return f"❌ 停止失败: {e}"


async def _cmd_new(session_id: uuid.UUID, user_id: uuid.UUID | str | None) -> str:
    """Create a fresh session for the same user and tell UI to switch.

    继承当前会话的 contact_agent / identity / source，保证「联系 小白」下 /new
    仍属于该员工的会话线程；否则切走再点员工会 find-or-create 回老会话。
    """
    try:
        from backend.api.dependencies import get_session_repo
        from backend.api.websocket import manager as ws_manager

        repo = await get_session_repo()
        cur = await repo.get_by_id(session_id)
        uid = user_id
        if uid is None:
            uid = getattr(cur, "user_id", None) if cur else None
        if not uid:
            return "⚠️ 无法确定用户，请用侧栏「新建会话」"
        uid_s = uid if isinstance(uid, uuid.UUID) else uuid.UUID(str(uid))

        # 继承联系人/人设，不继承 llm 快照外的临时态（goal 等按需）
        new_cfg: dict = {}
        contact_name = ""
        if cur is not None:
            old = getattr(cur, "config", None) or {}
            if isinstance(old, dict):
                for k in (
                    "contact_agent",
                    "identity",
                    "source",
                    "sys_prompt",
                    "agent_md",
                    "skills",
                    "tools",
                    "llm",
                ):
                    if k in old and old[k] is not None:
                        new_cfg[k] = old[k]
                contact_name = str(old.get("contact_agent") or "").strip()
                # 明确标记为人与员工 1:1 线程（与 open_contact_session 一致）
                if contact_name and not new_cfg.get("source"):
                    new_cfg["source"] = "human_dm"

        new_s = await repo.create({"user_id": uid_s, "config": new_cfg})
        new_id = getattr(new_s, "id", None) or new_s
        if not isinstance(new_id, uuid.UUID):
            new_id = uuid.UUID(str(new_id))
        await ws_manager.broadcast(
            session_id,
            {
                "type": "slash_result",
                "command": "new",
                "reply": (
                    "✅ 新会话已创建"
                    + (f"（继续联系 {contact_name}）" if contact_name else "")
                ),
                "new_session_id": str(new_id),
                "contact_agent": contact_name or None,
            },
        )
        who = f" · {contact_name}" if contact_name else ""
        return f"✅ 新会话已创建{who}\n🆔 {str(new_id)[:8]}…\n（前端将自动切换）"
    except Exception as e:
        logger.exception("web /new failed: %s", e)
        return f"⚠️ 自动新建失败，请点侧栏新建会话。错误: {e}"


async def _cmd_compact(session_id: uuid.UUID, args: str) -> str:
    try:
        from backend.agent.context_compress import compress_history_if_needed
        from backend.api.dependencies import get_message_repo

        message_repo = await get_message_repo()
        history = await message_repo.get_history_by_session(session_id, limit=500)
        if not history:
            return "⚠️ 当前会话没有消息历史"

        messages: list[dict[str, Any]] = []
        for m in history:
            role = str(getattr(m, "role", None) or "user")
            content = getattr(m, "content", None) or ""
            row: dict[str, Any] = {"role": role, "content": content}
            tc = getattr(m, "tool_calls", None)
            if tc:
                row["tool_calls"] = tc
            messages.append(row)

        # force compress
        thr = 0.0 if not (args or "").strip().startswith("soft") else 0.35
        compressed, meta = await compress_history_if_needed(
            messages,
            session_id=session_id,
            threshold=thr,
            allow_l5=True,
        )
        if meta.get("compressed") or meta.get("pre_hard_drop"):
            before = meta.get("tokens_before", 0)
            after = meta.get("tokens_after", 0)
            layers = meta.get("layers") or []
            return (
                f"✅ 上下文已压缩\n"
                f"📊 tokens {before} → {after}\n"
                f"🧩 layers={layers}\n"
                f"📝 messages {len(messages)} → {len(compressed)}\n"
                f"💡 压缩作用于下一轮 agent 上下文；历史消息仍可在 UI 中查看。"
            )
        return "ℹ️ 上下文未超过阈值，无需压缩（可再聊几轮后 /compact）"
    except Exception as e:
        logger.exception("web /compact failed: %s", e)
        return f"❌ 压缩失败: {e}"


async def _cmd_status(session_id: uuid.UUID) -> str:
    from backend.core.config import settings

    lines = ["📊 Tevarn 状态", ""]
    model = getattr(settings, "llm_model", "unknown")
    provider = getattr(settings, "llm_provider", "unknown")
    lines.append(f"🤖 模型: {provider}/{model}")
    lines.append(f"📏 上下文窗口: {getattr(settings, 'context_window', 128000)}")
    lines.append(f"💬 会话: {str(session_id)[:8]}…")
    try:
        from backend.api.dependencies import get_message_repo, get_session_repo

        message_repo = await get_message_repo()
        messages = await message_repo.get_history_by_session(session_id, limit=2000)
        lines.append(f"📝 消息数: {len(messages)}")
        session_repo = await get_session_repo()
        sess = await session_repo.get_by_id(session_id)
        cfg = dict(getattr(sess, "config", None) or {}) if sess else {}
        if cfg.get("model_override"):
            lines.append(f"🔀 会话模型覆盖: {cfg['model_override']}")
        if cfg.get("tools") is not None:
            lines.append(f"🔧 工具过滤: {cfg.get('tools')}")
        if cfg.get("goal_text"):
            lines.append(f"🎯 目标: {cfg.get('goal_text')[:80]}")
    except Exception as e:
        lines.append(f"⚠️ 详情读取失败: {e}")
    try:
        from backend.api.websocket import manager as ws_manager

        running = ws_manager.has_running_agent(session_id)
        lines.append(f"🏃 Agent: {'运行中' if running else '空闲'}")
    except Exception:
        pass
    return "\n".join(lines)


async def _cmd_model(session_id: uuid.UUID, args: str) -> str:
    from backend.core.config import settings

    if not args:
        current = getattr(settings, "llm_model", "unknown")
        provider = getattr(settings, "llm_provider", "unknown")
        return f"📋 当前模型: {provider}/{current}\n💡 用法: /model <model_name>"
    try:
        from backend.api.dependencies import get_session_repo

        session_repo = await get_session_repo()
        session = await session_repo.get_by_id(session_id)
        if not session:
            return "⚠️ 会话不存在"
        config = dict(session.config or {})
        config["model_override"] = args.strip()
        # also write llm snapshot-ish keys some paths read
        config["model"] = args.strip()
        await session_repo.update(session_id, {"config": config})
        return f"✅ 本会话模型已切换为: {args.strip()}\n（下一轮对话生效；全局设置仍在「设置 → 模型」）"
    except Exception as e:
        return f"❌ 切换失败: {e}"


async def _cmd_tools(session_id: uuid.UUID, args: str) -> str:
    parts = (args or "").strip().split(maxsplit=1)
    action = (parts[0] if parts else "list").lower()
    tool_name = parts[1].strip() if len(parts) > 1 else ""

    if action in ("", "list"):
        try:
            from backend.tools.registry import ToolRegistry

            all_tools = ToolRegistry.get_all()
            names = sorted(t.name for t in all_tools if getattr(t, "enabled", True))
            preview = ", ".join(names[:40])
            more = f" …共{len(names)}个" if len(names) > 40 else ""
            return f"🔧 已加载工具 ({len(names)})\n{preview}{more}\n\n用法: /tools enable|disable <名>"
        except Exception as e:
            return f"❌ 列表失败: {e}"

    if action in ("enable", "disable"):
        if not tool_name:
            return "⚠️ 用法: /tools enable|disable <工具名>"
        return await _toggle_tool(session_id, tool_name, enable=(action == "enable"))

    return "⚠️ 用法: /tools [list | enable <名> | disable <名>]"


async def _toggle_tool(session_id: uuid.UUID, tool_name: str, *, enable: bool) -> str:
    from backend.api.dependencies import get_session_repo

    session_repo = await get_session_repo()
    session = await session_repo.get_by_id(session_id)
    if not session:
        return "⚠️ 会话不存在"
    config = dict(session.config or {})
    raw = config.get("tools", ["*"])
    if raw is None or raw == ["*"] or raw == []:
        try:
            from backend.tools.registry import ToolRegistry

            tool_list = [t.name for t in ToolRegistry.get_all() if getattr(t, "enabled", True)]
        except Exception:
            tool_list = []
    else:
        tool_list = list(raw)
    if enable:
        if tool_name not in tool_list:
            tool_list.append(tool_name)
        action_str = "启用"
    else:
        if tool_name in tool_list:
            tool_list.remove(tool_name)
        action_str = "禁用"
    config["tools"] = tool_list
    await session_repo.update(session_id, {"config": config})
    return f"✅ 已{action_str}工具: {tool_name}"


async def _cmd_toolset(session_id: uuid.UUID, args: str) -> str:
    if not args or args.strip().lower() == "list":
        return build_toolset_list_text()
    preset_name = args.strip().lower()
    preset = TOOLSET_PRESETS.get(preset_name)
    if not preset:
        return f"⚠️ 未知工具集: {preset_name}\n{build_toolset_list_text()}"
    from backend.api.dependencies import get_session_repo

    session_repo = await get_session_repo()
    session = await session_repo.get_by_id(session_id)
    if not session:
        return "⚠️ 会话不存在"
    config = dict(session.config or {})
    config["tools"] = preset["tools"] if preset["tools"] is not None else ["*"]
    await session_repo.update(session_id, {"config": config})
    tools = preset["tools"]
    if tools is None:
        tool_str = "全部启用"
    elif len(tools) == 0:
        tool_str = "无工具"
    else:
        tool_str = ", ".join(tools)
    return f"✅ 工具集: {preset_name} — {preset['description']}\n🔧 {tool_str}"


async def _cmd_goal(session_id: uuid.UUID, args: str) -> str:
    from backend.api.dependencies import get_session_repo

    session_repo = await get_session_repo()
    session = await session_repo.get_by_id(session_id)
    if not session:
        return "⚠️ 会话不存在"
    config = dict(session.config or {})
    a = (args or "").strip()
    if not a or a == "show":
        g = config.get("goal_text")
        if not g:
            return "📋 当前没有目标\n💡 /goal <描述> | /goal pause|resume|clear"
        st = "暂停" if config.get("goal_paused") else "进行中"
        return f"🎯 目标 [{st}]: {g}"
    if a == "pause":
        if not config.get("goal_text"):
            return "⚠️ 没有活跃目标"
        config["goal_paused"] = True
        await session_repo.update(session_id, {"config": config})
        return "⏸️ 目标已暂停"
    if a == "resume":
        if not config.get("goal_text"):
            return "⚠️ 没有活跃目标"
        config["goal_paused"] = False
        await session_repo.update(session_id, {"config": config})
        return "▶️ 目标已恢复"
    if a == "clear":
        config.pop("goal_text", None)
        config.pop("goal_paused", None)
        await session_repo.update(session_id, {"config": config})
        return "🗑️ 目标已清除"
    config["goal_text"] = a
    config["goal_paused"] = False
    await session_repo.update(session_id, {"config": config})
    return f"🎯 目标已设置: {a}"
