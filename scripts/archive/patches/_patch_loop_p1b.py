"""Patch loop.py: wall-clock, LLM retry, continue-phrase resume, softer FK persist."""
from pathlib import Path
import re

path = Path(r"E:/项目/taktonl-0.1.0/backend/agent/loop.py")
text = path.read_text(encoding="utf-8")

# imports
if "from backend.agent.robust import" not in text:
    # after existing imports
    anchor = "from backend.core.config import settings\n"
    if anchor not in text:
        raise SystemExit("settings import not found")
    text = text.replace(
        anchor,
        anchor
        + "from backend.agent.robust import is_continue_phrase, is_transient_llm_error\n",
        1,
    )

# wall clock + continue phrase after logger start of _run_locked
if "agent_max_duration_seconds" not in text or "_deadline" not in text:
    old = '''        logger.info(f"Agent loop started for session {session_id}, mode={mode}")
        logger.info(f"DEBUG_START: should_stop={self._should_stop}")

        # 处理附件内容注入
'''
    new = '''        logger.info(f"Agent loop started for session {session_id}, mode={mode}")
        logger.info(f"DEBUG_START: should_stop={self._should_stop}")

        import time as _time
        _max_dur = float(getattr(settings, "agent_max_duration_seconds", 0) or 0)
        _deadline = (_time.monotonic() + _max_dur) if _max_dur > 0 else None

        # 「请继续」→ 自动接 Goal/checkpoint 续跑
        if is_continue_phrase(user_input):
            try:
                from backend.agent.resume import build_resume_prompt
                from backend.agent.goal_state import get_goal, load_goal_from_db

                await load_goal_from_db(session_id)
                rp = await build_resume_prompt(session_id)
                if rp:
                    user_input = rp
                    if get_goal(session_id) is not None:
                        mode = "goal"
                    logger.info("Continue-phrase expanded to resume prompt for %s", session_id)
            except Exception as e:
                logger.warning("continue-phrase resume expand failed: %s", e)

        # 处理附件内容注入
'''
    if old not in text:
        raise SystemExit("run_locked start not found")
    text = text.replace(old, new, 1)

# deadline check inside iteration loop after stop check
stop_block = '''            if self._should_stop:
                logger.info(f"Agent loop stopped by signal for session {session_id}")
                if accumulated_content:
                    final_content = accumulated_content
                else:
                    final_content = final_content or "[Stopped] Generation was cancelled"
                break
'''
deadline_block = '''            if self._should_stop:
                logger.info(f"Agent loop stopped by signal for session {session_id}")
                if accumulated_content:
                    final_content = accumulated_content
                else:
                    final_content = final_content or "[Stopped] Generation was cancelled"
                break

            if _deadline is not None and _time.monotonic() > _deadline:
                logger.warning(
                    "Agent wall-clock deadline reached (%.0fs) for session %s",
                    _max_dur,
                    session_id,
                )
                final_content = accumulated_content or (
                    f"[提示] 已达单次运行时间上限 ({_max_dur:.0f}s)。"
                    "可发送「请继续」或 POST /api/sessions/{id}/resume 续跑。"
                )
                break
'''
if "wall-clock deadline" not in text:
    if stop_block not in text:
        raise SystemExit("stop block not found")
    text = text.replace(stop_block, deadline_block, 1)

# LLM call with retry - wrap the try for chat stream
# Find the try that has async for chunk in llm_service.chat
old_llm = '''            try:
                # 调试日志：content 可能是 None（assistant+tool_calls），不能 len(None)
                def _msg_chars(m: dict[str, Any]) -> int:
                    c = m.get("content")
                    if c is None:
                        return 0
                    if isinstance(c, str):
                        return len(c)
                    if isinstance(c, list):
                        try:
                            return len(json.dumps(c, ensure_ascii=False))
                        except Exception:
                            return 0
                    return len(str(c))

                logger.info(
                    f"Sending {len(messages)} messages to LLM "
                    f"(total chars: {sum(_msg_chars(m) for m in messages)})"
                )
                async for chunk in llm_service.chat(
                    messages, tools=tools if tools else None, stream=True
                ):
'''

# Instead of full replace, inject retry around LLM failure handling
old_fail = '''            except Exception as e:
                logger.error(f"LLM chat error in iteration {iteration + 1}: {e}")
                # 向前端推送错误信息
                await self._push_status(session_id, "error", f"LLM 调用失败: {e}")
                final_content = f"[Error] LLM service failed: {e}"
                break
'''
new_fail = '''            except Exception as e:
                logger.error(f"LLM chat error in iteration {iteration + 1}: {e}")
                _attempts = int(getattr(settings, "agent_llm_retry_attempts", 3) or 1)
                _retried = getattr(self, "_llm_fail_streak", 0) + 1
                self._llm_fail_streak = _retried
                if (
                    _retried < _attempts
                    and is_transient_llm_error(e)
                    and not self._should_stop
                ):
                    import asyncio as _aio

                    delay = min(8.0, 0.8 * (2 ** (_retried - 1)))
                    await self._push_status(
                        session_id,
                        "thinking",
                        f"LLM 瞬断，{_retried}/{_attempts} 次重试…",
                    )
                    await _aio.sleep(delay)
                    continue
                self._llm_fail_streak = 0
                await self._push_status(session_id, "error", f"LLM 调用失败: {e}")
                final_content = f"[Error] LLM service failed: {e}"
                break
'''
if old_fail not in text:
    raise SystemExit("llm fail block not found")
text = text.replace(old_fail, new_fail, 1)

# reset fail streak on successful stream start - after successful loop without exception
# add after tool_calls collection begins success path
if "self._llm_fail_streak = 0" not in text.split("if tool_calls:")[0][-800:]:
    # insert after stream ends successfully before tool_calls check
    marker = "            # 判断是否有 tool calls\n            if tool_calls:\n"
    if marker in text:
        text = text.replace(
            marker,
            "            # 本轮 LLM 成功，重置失败计数\n            self._llm_fail_streak = 0\n\n" + marker,
            1,
        )

# softer FK on assistant tool_calls persist
old_persist_a = '''                except Exception as e:
                    logger.warning(f"Failed to persist assistant tool_calls message: {e}")
'''
new_persist_a = '''                except Exception as e:
                    msg = str(e)
                    if "FOREIGN KEY" in msg or "IntegrityError" in msg:
                        logger.warning(
                            "Skip persist assistant tool_calls (session missing?): %s", e
                        )
                    else:
                        logger.warning(f"Failed to persist assistant tool_calls message: {e}")
'''
if old_persist_a in text:
    text = text.replace(old_persist_a, new_persist_a, 1)

old_persist_t = '''                    except Exception as e:
                        logger.warning(f"Failed to persist tool result message: {e}")
'''
new_persist_t = '''                    except Exception as e:
                        msg = str(e)
                        if "FOREIGN KEY" in msg or "IntegrityError" in msg:
                            logger.warning(
                                "Skip persist tool result (session missing?): %s", e
                            )
                        else:
                            logger.warning(f"Failed to persist tool result message: {e}")
'''
if old_persist_t in text:
    text = text.replace(old_persist_t, new_persist_t, 1)

# init llm_fail_streak in __init__
if "_llm_fail_streak" not in text.split("def stop")[0]:
    text = text.replace(
        "        self._should_stop = False\n",
        "        self._should_stop = False\n        self._llm_fail_streak = 0\n",
        1,
    )

path.write_text(text, encoding="utf-8")
import py_compile

py_compile.compile(str(path), doraise=True)
print("loop robustness patch OK")
