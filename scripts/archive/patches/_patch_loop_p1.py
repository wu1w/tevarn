"""P1 patches for agent/loop.py: soft input, segments, checkpoint, mid-loop L1 every N."""
from pathlib import Path
import re

path = Path(r"E:/项目/taktonl-0.1.0/backend/agent/loop.py")
text = path.read_text(encoding="utf-8")

# --- soft large input after hard truncate ---
soft_block = '''        _soft = int(getattr(settings, "agent_large_input_soft_chars", 32_000) or 0)
        if _soft > 0 and len(enriched_input) > _soft:
            head_n = max(1000, _soft // 2)
            tail_n = max(1000, _soft - head_n)
            omitted = len(enriched_input) - head_n - tail_n
            if omitted > 0:
                enriched_input = (
                    enriched_input[:head_n]
                    + f"\\n\\n…[系统: 大输入中间省略 {omitted} 字符，保留头尾]…\\n\\n"
                    + enriched_input[-tail_n:]
                )
                logger.info(
                    "Soft-truncated large input to head+tail (~%s chars) session=%s",
                    len(enriched_input),
                    session_id,
                )
'''

if "agent_large_input_soft_chars" not in text:
    marker = '            enriched_input = (\n                enriched_input[:_max_in]\n                + f"\\n\\n[系统: 输入过长已截断至 {_max_in} 字符]"\n            )\n'
    if marker not in text:
        # try alternate
        m = re.search(
            r"enriched_input = \(\s*enriched_input\[:_max_in\].*?字符\]\"\s*\)\n",
            text,
            re.S,
        )
        if not m:
            # insert after hard cap if block
            m2 = re.search(
                r"if _max_in > 0 and len\(enriched_input\) > _max_in:.*?\n            \)\n",
                text,
                re.S,
            )
            if not m2:
                raise SystemExit("hard cap block not found")
            text = text[: m2.end()] + soft_block + text[m2.end() :]
        else:
            text = text[: m.end()] + soft_block + text[m.end() :]
    else:
        text = text.replace(marker, marker + soft_block, 1)

# --- replace for-iteration with segmented budget ---
old_for = "        for iteration in range(self.max_iterations):\n"
if "agent_auto_continue_max_segments" not in text.split("for iteration in range")[0][-500:]:
    new_for = '''        # 分段预算：单段 max_iterations，可自动续多段（Goal / 长任务）
        _auto_cont = bool(getattr(settings, "agent_auto_continue", True))
        _max_seg = int(getattr(settings, "agent_auto_continue_max_segments", 5) or 1)
        if not _auto_cont:
            _max_seg = 1
        _seg_size = max(1, int(self.max_iterations))
        _total_iters = _seg_size * max(1, _max_seg)
        _checkpoint_every = int(getattr(settings, "agent_checkpoint_every", 5) or 5)
        _l1_every = int(getattr(settings, "agent_midloop_l1_every", 3) or 3)
        _tool_rounds = 0
        _segment = 0

        for _global_iter in range(_total_iters):
            iteration = _global_iter % _seg_size
            # 段边界（非首段）：checkpoint + 注入续跑提示
            if _global_iter > 0 and iteration == 0:
                _segment += 1
                try:
                    from backend.agent.checkpoint import save_checkpoint
                    from backend.agent.goal_state import get_goal, save_goal_to_db

                    g_chk = get_goal(session_id) if goal_mode else None
                    # 非 goal 且未要求续跑则结束
                    if not goal_mode and not _auto_cont:
                        break
                    if goal_mode and g_chk is not None and g_chk.is_complete():
                        break
                    await save_checkpoint(
                        session_id,
                        segment=_segment,
                        iteration=_global_iter,
                        mode=mode,
                        note="auto-continue segment boundary",
                        extra={"goal_complete": bool(g_chk and g_chk.is_complete())},
                    )
                    if goal_mode:
                        await save_goal_to_db(session_id)
                    await self._push_status(
                        session_id,
                        "thinking",
                        f"自动续跑第 {_segment + 1}/{_max_seg} 段…",
                    )
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "【系统自动续跑】上一轮次段已用尽，请从断点继续，"
                                "不要重复已完成工作。"
                                + (
                                    "\\n" + g_chk.summary_for_llm()
                                    if g_chk and not g_chk.is_complete()
                                    else ""
                                )
                            ),
                        }
                    )
                except Exception as e:
                    logger.warning("auto-continue segment setup failed: %s", e)

'''
    if old_for not in text:
        raise SystemExit("for iteration line not found")
    text = text.replace(old_for, new_for, 1)

# Fix max iterations log line to use _seg_size / _total
text = text.replace(
    'f"Iteration {iteration + 1}/{self.max_iterations} for session {session_id}"',
    'f"Iteration {iteration + 1}/{_seg_size} (seg {_segment + 1}, global {_global_iter + 1}/{_total_iters}) for session {session_id}"',
)

# goal nudge compare against segment size
text = text.replace(
    "and iteration < self.max_iterations - 1:",
    "and iteration < _seg_size - 1:",
)

# --- after tool round midloop: always L1 every N rounds ---
old_mid = '''                # 工具轮后：L1 截断 + 超阈值再 pipeline，防止中段爆炸
                try:
                    from backend.agent.context_engine import get_context_engine
                    from backend.agent.context_compress import compress_history_if_needed

                    eng = get_context_engine()
                    if hasattr(eng, "_l1_budget"):
                        messages, _n = eng._l1_budget(messages)  # type: ignore[attr-defined]
                    if eng.should_compress_preflight(messages):
                        messages, mid_meta = await compress_history_if_needed(
                            messages,
                            session_id=session_id,
                            threshold=float(
                                getattr(settings, "context_threshold_percent", 0.72) or 0.72
                            ),
                        )
                        if mid_meta.get("compressed"):
                            await self._push_status(
                                session_id,
                                "optimizing",
                                f"工具轮后上下文压缩 layers={mid_meta.get('layers')}",
                            )
                except Exception as e:
                    logger.debug("mid-loop context pipeline skipped: %s", e)
'''
new_mid = '''                # 工具轮后：L1 周期性截断 + 超阈值 pipeline + checkpoint
                _tool_rounds += 1
                try:
                    from backend.agent.context_engine import get_context_engine
                    from backend.agent.context_compress import compress_history_if_needed

                    eng = get_context_engine()
                    do_l1 = (_l1_every > 0 and _tool_rounds % _l1_every == 0) or eng.should_compress_preflight(messages)
                    if do_l1 and hasattr(eng, "_l1_budget"):
                        messages, _n = eng._l1_budget(messages)  # type: ignore[attr-defined]
                    if eng.should_compress_preflight(messages) or eng.should_compress():
                        messages, mid_meta = await compress_history_if_needed(
                            messages,
                            session_id=session_id,
                            threshold=float(
                                getattr(settings, "context_threshold_percent", 0.72) or 0.72
                            ),
                        )
                        if mid_meta.get("compressed"):
                            await self._push_status(
                                session_id,
                                "optimizing",
                                f"工具轮后上下文压缩 layers={mid_meta.get('layers')}",
                            )
                except Exception as e:
                    logger.debug("mid-loop context pipeline skipped: %s", e)
                if _checkpoint_every > 0 and _tool_rounds % _checkpoint_every == 0:
                    try:
                        from backend.agent.checkpoint import save_checkpoint
                        from backend.agent.goal_state import get_goal, save_goal_to_db

                        await save_checkpoint(
                            session_id,
                            segment=_segment,
                            iteration=_global_iter + 1,
                            mode=mode,
                            note=f"tool_round={_tool_rounds}",
                        )
                        if goal_mode:
                            await save_goal_to_db(session_id)
                    except Exception as e:
                        logger.debug("mid-loop checkpoint skipped: %s", e)
'''
if old_mid not in text:
    # already partially different - try looser
    if "工具轮后：L1" not in text:
        raise SystemExit("midloop block not found")
    print("WARN: midloop exact block missing, skip replace")
else:
    text = text.replace(old_mid, new_mid, 1)

# --- max iterations else branch: clearer + clear checkpoint on success path ---
old_else = '''        else:
            # 达到最大迭代次数
            logger.warning(f"Max iterations ({self.max_iterations}) reached for session {session_id}")
            final_content = accumulated_content or (
                f"[提示] 已达最大工具轮次 ({self.max_iterations})，任务可能未完成。"
                "可继续发送「请继续」让 Agent 接着做，或在配置中提高 agent_max_iterations。"
            )
            if goal_mode:
                from backend.agent.goal_state import get_goal

                g = get_goal(session_id)
                if g and not g.is_complete():
                    final_content += (
                        "\\n\\n---\\n**Goal 进度**\\n```\\n"
                        + g.summary_for_llm()
                        + "\\n```\\n可发送「请继续」恢复 Goal 模式推进。"
                    )
'''
new_else = '''        else:
            # 用尽全部分段预算
            logger.warning(
                "Max iteration budget (%s segs x %s) reached for session %s",
                _max_seg,
                _seg_size,
                session_id,
            )
            final_content = accumulated_content or (
                f"[提示] 已达最大工具轮次预算 ({_max_seg}×{_seg_size})，任务可能未完成。"
                "可发送「请继续」或调用 /api/sessions/{id}/resume 续跑。"
            )
            if goal_mode:
                from backend.agent.goal_state import get_goal, save_goal_to_db

                g = get_goal(session_id)
                if g and not g.is_complete():
                    final_content += (
                        "\\n\\n---\\n**Goal 进度**\\n```\\n"
                        + g.summary_for_llm()
                        + "\\n```\\n可发送「请继续」恢复 Goal 模式推进。"
                    )
                    try:
                        await save_goal_to_db(session_id)
                        from backend.agent.checkpoint import save_checkpoint

                        await save_checkpoint(
                            session_id,
                            segment=_segment,
                            iteration=_total_iters,
                            mode=mode,
                            note="budget_exhausted",
                        )
                    except Exception:
                        pass
'''
if old_else in text:
    text = text.replace(old_else, new_else, 1)
else:
    print("WARN: else max-iter block not exact")

# clear checkpoint on clean completion before persist final
if "clear_checkpoint" not in text:
    needle = "        # 8. 保存最终回复 + 同步 CtxItem + 状态 + 通知（同一事务）\n"
    if needle in text:
        text = text.replace(
            needle,
            '''        # 正常结束则清理 checkpoint
        try:
            from backend.agent.checkpoint import clear_checkpoint
            from backend.agent.goal_state import get_goal

            g_done = get_goal(session_id) if goal_mode else None
            if not self._should_stop and (not goal_mode or (g_done is None or g_done.is_complete())):
                await clear_checkpoint(session_id)
        except Exception:
            pass

''' + needle,
            1,
        )

path.write_text(text, encoding="utf-8")
import py_compile

py_compile.compile(str(path), doraise=True)
print("loop P1 patch OK")
