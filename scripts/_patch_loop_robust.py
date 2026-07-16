"""Patch agent/loop.py for stop-in-stream, tool timeout, input cap, goal db load."""
from pathlib import Path
import re

path = Path(r"E:/项目/taktonl-0.1.0/backend/agent/loop.py")
text = path.read_text(encoding="utf-8")

# 1) input cap
if "agent_max_user_input_chars" not in text:
    m = re.search(
        r"enriched_input = self\._build_user_input_with_attachments\([^\n]+\)\n",
        text,
    )
    if not m:
        raise SystemExit("enriched_input line not found")
    insert = (
        m.group(0)
        + """        _max_in = int(getattr(settings, "agent_max_user_input_chars", 100_000) or 100_000)
        if _max_in > 0 and len(enriched_input) > _max_in:
            logger.warning(
                "User input truncated %s -> %s chars for session %s",
                len(enriched_input), _max_in, session_id,
            )
            enriched_input = (
                enriched_input[:_max_in]
                + f"\\n\\n[系统: 输入过长已截断至 {_max_in} 字符]"
            )
"""
    )
    text = text[: m.start()] + insert + text[m.end() :]

# 2) goal load
if "load_goal_from_db" not in text:
    text = text.replace(
        "from backend.agent.goal_state import ensure_goal, get_goal",
        "from backend.agent.goal_state import ensure_goal, get_goal, load_goal_from_db, save_goal_to_db",
        1,
    )
    text = text.replace(
        'goal_iters = int(getattr(settings, "agent_goal_max_iterations", 50) or 50)',
        'goal_iters = int(getattr(settings, "agent_goal_max_iterations", 100) or 100)',
        1,
    )
    text = text.replace(
        "ensure_goal(session_id, title=enriched_input[:120], description=enriched_input[:2000])",
        "await load_goal_from_db(session_id)\n"
        "            ensure_goal(session_id, title=enriched_input[:120], description=enriched_input[:2000])",
        1,
    )

# 3) stream stop
old_stream = """                async for chunk in llm_service.chat(
                    messages, tools=tools if tools else None, stream=True
                ):
                    # 推送流式文本到前端
                    if chunk.delta:
                        accumulated_content += chunk.delta
                        await self._push_stream(
                            session_id, message_id, chunk.delta
                        )

                    # 收集 tool call
                    if chunk.tool_call:
                        tool_calls.append(chunk.tool_call)

                    # 结束标记
                    if chunk.finish_reason:
                        break
"""
new_stream = """                async for chunk in llm_service.chat(
                    messages, tools=tools if tools else None, stream=True
                ):
                    # 思考中可打断
                    if self._should_stop:
                        logger.info(
                            "Stop during LLM stream for session %s", session_id
                        )
                        break

                    # 推送流式文本到前端
                    if chunk.delta:
                        accumulated_content += chunk.delta
                        await self._push_stream(
                            session_id, message_id, chunk.delta
                        )

                    # 收集 tool call
                    if chunk.tool_call:
                        tool_calls.append(chunk.tool_call)

                    # 结束标记
                    if chunk.finish_reason:
                        break

                if self._should_stop:
                    final_content = (
                        accumulated_content
                        or final_content
                        or "[Stopped] Generation was cancelled"
                    )
                    break
"""
if old_stream not in text:
    raise SystemExit("stream block not found")
text = text.replace(old_stream, new_stream)

# 4) tool timeout on unified registry
old_exec = (
    "                            tool_result = await UnifiedToolRegistry.execute"
    "(tc.name, validated_args)\n"
)
new_exec = """                            _tool_timeout = float(
                                getattr(settings, "agent_tool_timeout_seconds", 180) or 0
                            )
                            if _tool_timeout > 0:
                                tool_result = await asyncio.wait_for(
                                    UnifiedToolRegistry.execute(tc.name, validated_args),
                                    timeout=_tool_timeout,
                                )
                            else:
                                tool_result = await UnifiedToolRegistry.execute(
                                    tc.name, validated_args
                                )
"""
if old_exec not in text:
    raise SystemExit("tool execute not found")
text = text.replace(old_exec, new_exec, 1)

# 5) TimeoutError handler near tool try
marker = '                    tool_result = ""\n                    try:'
if "except asyncio.TimeoutError:" not in text:
    idx = text.find(marker)
    if idx < 0:
        raise SystemExit("tool try marker not found")
    rest = text[idx:]
    exc_idx = rest.find("\n                    except Exception as e:")
    if exc_idx < 0:
        raise SystemExit("tool except not found")
    insert_at = idx + exc_idx
    handler = """
                    except asyncio.TimeoutError:
                        _to = float(getattr(settings, "agent_tool_timeout_seconds", 180) or 180)
                        tool_result = f"[Error] Tool '{tc.name}' timed out after {_to:.0f}s"
                        query = ""
                        logger.warning("Tool %s timed out after %ss", tc.name, _to)
"""
    text = text[:insert_at] + handler + text[insert_at:]

# 6) save goal after manage_goal push
if "await save_goal_to_db" not in text:
    needle = 'if tc.name == "manage_goal":'
    pos = text.find(needle)
    if pos > 0:
        sub = text[pos : pos + 900]
        m2 = re.search(r"await self\._push_goal_update\(session_id\)", sub)
        if m2:
            abs_end = pos + m2.end()
            text = (
                text[:abs_end]
                + "\n                            await save_goal_to_db(session_id)"
                + text[abs_end:]
            )

path.write_text(text, encoding="utf-8")
import py_compile

py_compile.compile(str(path), doraise=True)
print("loop.py patched OK")
