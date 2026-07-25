"""_run_locked 序章 phase（loop 拆分 Phase 1）

从 loop.py 抽出的两个自包含前置块（行为冻结见 tests/test_loop_freeze.py）：
- try_device_shortcut：@device 远程执行短路（命中则不进工具循环）
- expand_continue_phrase：「请继续」→ 自动接 Goal/checkpoint 续跑
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)


async def try_device_shortcut(
    loop: Any,
    session_id: uuid.UUID,
    user_input: str,
    attachments: list[dict[str, Any]] | None,
) -> str | None:
    """@device 远程执行（L1）：命中则短路返回卡片文本，否则 None"""
    if not (loop.user_id and user_input and "@" in user_input):
        return None
    try:
        from backend.services.remote.dispatch import try_handle_at_device

        card = await try_handle_at_device(loop.user_id, user_input)
        if card is None:
            return None
        try:
            await loop._persist_user_input(session_id, user_input, attachments)
        except Exception as e:
            logger.warning("persist user input (@device) failed: %s", e)
        try:
            await loop._persist_final_response(session_id, card)
        except Exception as e:
            logger.warning("persist final response (@device) failed: %s", e)
            # fallback plain save
            try:
                await loop._save_message(session_id, "assistant", card)
            except Exception as e2:
                logger.error("fallback save assistant message failed: %s", e2)
        await loop._push_status(session_id, "idle", "remote device command done")
        return card
    except Exception as e:
        logger.warning("@device dispatch failed: %s", e)
        return None


async def expand_continue_phrase(
    session_id: uuid.UUID,
    user_input: str,
    mode: str,
) -> tuple[str, str]:
    """「请继续」→ 自动接 Goal/checkpoint 续跑；返回 (user_input, mode)"""
    from backend.agent.robust import is_continue_phrase

    if not is_continue_phrase(user_input):
        return user_input, mode
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
    return user_input, mode
