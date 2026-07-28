"""工具轮执行 phase（loop 拆分 Phase 1 第二刀）

从 loop.py _run_locked 抽出的「执行每个 tool call + 工具轮后处理」整块
（原 1221-1767 行）。行为冻结：tests/test_loop_freeze.py（拆分前后同绿）。

跨边界状态用 ToolRoundState 显式承载：
- 列表（messages/tools_used_run/sft_tools/trace_tool_calls）按引用共享
- 标量（force_final_no_tools 等）调用方在返回后读回
- messages/tools/enabled_tools_filter 可能被压缩/扩容**重绑定**，调用方必须读回
"""
from __future__ import annotations

import asyncio
import json
import logging
import time as _time
import uuid
from dataclasses import dataclass
from typing import Any

from backend.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ToolRoundState:
    """run_tool_round 的跨边界状态"""

    messages: list[dict[str, Any]]
    tools_used_run: list[str]
    sft_tools: list[dict[str, Any]]
    trace_tool_calls: list[dict[str, Any]]
    scene_plan: Any
    tools: list[dict[str, Any]]
    enabled_tools_filter: Any
    force_final_no_tools: bool
    suppress_content_stream: bool
    multi_source_pending: bool
    timid_read_streak: int
    timid_write_streak: int
    tool_rounds: int
    last_tool_round_count: int


# T1：可安全并发的只读风险等级。写类/命令类一律串行，避免「并发读 + 写同一文件」竞态。
_PARALLEL_SAFE_RISK = frozenset({"safe", "low"})


def _risk_name(tool: Any) -> str:
    rl = getattr(tool, "risk_level", None)
    return str(getattr(rl, "value", rl) or "").lower()


async def _prefetch_readonly_calls(
    loop: Any,
    *,
    session_id: uuid.UUID,
    mode: str,
    tool_calls: list[Any],
) -> dict[str, tuple[Any, BaseException | None]]:
    """并发执行本轮的只读工具，返回 {tool_call_id: (result, exc)}。

    system_prompt 的 PARALLEL_TOOL_CALLS 段向模型承诺「runtime executes independent
    calls concurrently」，但此前实现是纯串行 for 循环 —— 模型照做批量请求反而更慢。
    本函数兑现该承诺。

    保守策略（正确性优先于速度）：
    - 整批必须全是只读工具，混入任何写类/命令类则整批退回串行；
      否则「并发读 + 写同一文件」会读到中间态。
    - 单个调用不并发（无收益，徒增复杂度）。
    - 异常不在这里处理，原样带回给串行主体重抛，失败语义与串行完全一致。
    """
    if len(tool_calls) < 2:
        return {}
    if not bool(getattr(settings, "agent_tool_parallel", True)):
        return {}

    from backend.tools.registry import ToolRegistry as UnifiedToolRegistry

    tools = []
    for tc in tool_calls:
        tool = UnifiedToolRegistry.get(getattr(tc, "name", "") or "")
        if tool is None or _risk_name(tool) not in _PARALLEL_SAFE_RISK:
            return {}  # 有一个不安全就整批串行
        tools.append((tc, tool))

    limit = max(1, int(getattr(settings, "agent_tool_parallel_max", 5) or 5))
    sem = asyncio.Semaphore(limit)
    timeout = float(getattr(settings, "agent_tool_timeout_seconds", 180) or 0)

    # 契约白名单预热：懒加载在锁内完成，避免并发首调时白名单尚未就位
    try:
        await loop._contract_tool_block_reason(
            "__prefetch_warmup__", {"_session_id": str(session_id)}
        )
    except Exception:
        pass

    async def _run(tc: Any, tool: Any) -> tuple[Any, BaseException | None]:
        async with sem:
            try:
                args = loop._validate_tool_args(tool.parameters, tc.arguments)
                if loop.user_id is not None:
                    args["user_id"] = str(loop.user_id)
                    args["_user_id"] = str(loop.user_id)
                args["_session_id"] = str(session_id)
                args["_chat_mode"] = str(mode or "default")
                args["_ws_manager"] = loop.ws_manager
                if timeout > 0:
                    return (
                        await asyncio.wait_for(
                            loop._execute_registered_tool(tc.name, args),
                            timeout=timeout,
                        ),
                        None,
                    )
                return await loop._execute_registered_tool(tc.name, args), None
            except BaseException as e:  # 原样带回串行主体重抛
                return "", e

    t0 = _time.monotonic()
    results = await asyncio.gather(*(_run(tc, tool) for tc, tool in tools))
    logger.info(
        "parallel tool prefetch: %s calls (%s) in %.0fms",
        len(tools),
        ",".join(getattr(tc, "name", "?") for tc, _ in tools),
        (_time.monotonic() - t0) * 1000,
    )
    return {tc.id: res for (tc, _), res in zip(tools, results)}


async def run_tool_round(
    loop: Any,
    *,
    session_id: uuid.UUID,
    mode: str,
    iteration: int,
    tool_calls: list[Any],
    state: ToolRoundState,
    segment: int,
    global_iter: int,
    goal_mode: bool,
    user_input: str,
    l1_every: int,
    checkpoint_every: int,
    turn_retry: Any,
    tool_repeat_guard: Any,
    enabled_skills: Any,
) -> None:
    """执行一轮工具调用并做轮后处理（就地更新 state）"""
    from backend.agent.run_state import RunStatus as _RS
    from backend.agent.robust import tool_call_signature
    from backend.agent.turn_retry import RetryKind
    from backend.agent.tool_result_contract import normalize_tool_result
    from backend.skills import SkillRegistry
    from backend.skills.dynamic import DynamicSkill
    from backend.repositories.skill_repo import AsyncSkillRepository

    _rc = getattr(loop, "_run_recorder", None)
    messages = state.messages

    # T1：本轮只读工具先并发跑完，结果按 tool_call_id 缓存。
    # 下面的串行主体一行不动地照常走（WS 事件 / 持久化 / messages 顺序全部不变），
    # 只是执行那一步改为取预取结果 —— 把并行的风险面压到最小。
    prefetched = await _prefetch_readonly_calls(
        loop, session_id=session_id, mode=mode, tool_calls=tool_calls
    )

    # 执行每个 tool call
    for tc in tool_calls:
        # Durable Run：首个工具触发 EXECUTING；记录起始时间
        _tc_t0 = _time.monotonic()
        if _rc is not None:
            try:
                await _rc.transition(_RS.EXECUTING)
            except Exception:
                pass
        # 实时推送：工具开始
        args_dict = tc.arguments if isinstance(tc.arguments, dict) else {}
        if not isinstance(args_dict, dict):
            try:
                args_dict = (
                    json.loads(tc.arguments)
                    if isinstance(tc.arguments, str)
                    else {}
                )
            except Exception:
                args_dict = {"raw": str(tc.arguments)}
        if not isinstance(args_dict, dict):
            args_dict = {}

        await loop._push_tool_event(
            session_id,
            phase="start",
            tool_call_id=tc.id,
            name=tc.name,
            arguments=args_dict,
            status="running",
        )
        try:
            from backend.agent.tool_status import format_tool_status
            _st = format_tool_status(tc.name, args_dict if isinstance(args_dict, dict) else {})
        except Exception:
            _st = f"Executing {tc.name}..."
        await loop._push_status(
            session_id,
            "tool_executing",
            _st,
        )

        # 创建 Task（用于进度追踪）
        task_id = await loop._persist_tool_start(session_id, tc.name)
        if task_id is not None:
            await loop._push_task_update(
                session_id, task_id, 50, "running", f"Running {tc.name}"
            )

        tool_result = ""
        try:
            # v3.0: 统一从 ToolRegistry 执行工具
            from backend.tools.registry import ToolRegistry as UnifiedToolRegistry

            tool = UnifiedToolRegistry.get(tc.name)
            # T1：只读工具已在本轮开始时并发跑完，这里直接取结果；
            # 异常原样重抛，交给下面既有的 TimeoutError / Exception 处理分支，
            # 保证并行与串行的失败语义完全一致。
            if tc.id in prefetched:
                _res, _exc = prefetched.pop(tc.id)
                if _exc is not None:
                    raise _exc
                tool_result = _res
                query = (
                    tc.arguments.get("query", "")
                    if tc.name == "search_knowledge_base"
                    else ""
                )
            elif tool is not None:
                validated_args = loop._validate_tool_args(tool.parameters, tc.arguments)
                if loop.user_id is not None:
                    validated_args["user_id"] = str(loop.user_id)
                    validated_args["_user_id"] = str(loop.user_id)
                validated_args["_session_id"] = str(session_id)
                validated_args["_chat_mode"] = str(mode or "default")
                validated_args["_ws_manager"] = loop.ws_manager
                _tool_timeout = float(
                    getattr(settings, "agent_tool_timeout_seconds", 180) or 0
                )
                if _tool_timeout > 0:
                    tool_result = await asyncio.wait_for(
                        loop._execute_registered_tool(tc.name, validated_args),
                        timeout=_tool_timeout,
                    )
                else:
                    tool_result = await loop._execute_registered_tool(tc.name, validated_args)
                query = (
                    tc.arguments.get("query", "")
                    if tc.name == "search_knowledge_base"
                    else ""
                )
            else:
                # 兼容旧方式：直接查 SkillRegistry 和 DB
                skill = SkillRegistry.get_skill(tc.name)
                if skill is not None:
                    validated_args = loop._validate_tool_args(skill.parameters, tc.arguments)
                    if loop.user_id is not None:
                        validated_args["user_id"] = str(loop.user_id)
                        validated_args["_user_id"] = str(loop.user_id)
                    validated_args["_session_id"] = str(session_id)
                    validated_args["_chat_mode"] = str(mode or "default")
                    validated_args["_ws_manager"] = loop.ws_manager
                    tool_result = await skill.execute(**validated_args)
                    query = ""
                else:
                    # 尝试执行数据库中的自定义 Skill
                    skill_repo = AsyncSkillRepository()
                    db_skill = await skill_repo.get_skill_by_name(tc.name)
                    if db_skill is not None and db_skill.enabled:
                        dynamic = DynamicSkill.from_db(db_skill)
                        validated_args = loop._validate_tool_args(dynamic.parameters, tc.arguments)
                        if loop.user_id is not None:
                            validated_args["user_id"] = str(loop.user_id)
                            validated_args["_user_id"] = str(loop.user_id)
                        validated_args["_session_id"] = str(session_id)
                        validated_args["_ws_manager"] = loop.ws_manager
                        # 修复遗留 NameError：原 loop.py 此处 tool_repo 未定义
                        from backend.repositories.tool_repo import AsyncToolRepository

                        tool_repo = AsyncToolRepository()
                        db_tool = await tool_repo.get_tool_by_name(tc.name)
                        if db_tool is not None and db_tool.enabled:
                            tool_result = await UnifiedToolRegistry.execute_tool(db_tool, tc.arguments)
                            query = ""
                        else:
                            tool_result = f"[Error] Tool '{tc.name}' not found or disabled"
                            query = ""

            # 工具结果契约：统一 str / 截断 / 空结果
            _max_tr = int(getattr(settings, "max_tool_result_length", 12_000) or 12_000)
            _tname = getattr(tc, "name", "") or ""
            tool_result = normalize_tool_result(
                tool_result, tool_name=_tname
            )
            # 全局硬顶（settings）仍生效；但 TOOL_RESULT_BUDGET 里显式给出的
            # 更高的 per-tool 预算优先（T3：file_read 已按行边界自分页并给出续读
            # offset，若在此被通用上限二次 head+tail 拼接，模型会拿到断裂视图）。
            from backend.agent.tool_result_contract import TOOL_RESULT_BUDGET

            _cap = max(_max_tr, int(TOOL_RESULT_BUDGET.get(_tname, 0) or 0))
            if _cap and len(tool_result) > _cap:
                from backend.agent.tool_result_contract import truncate_for_llm
                tool_result = truncate_for_llm(_tname, tool_result, budget=_cap)
            # shell 安全拦截 / 127：记入重试分类，并提示改用 file_write 或修 cwd
            try:
                from backend.agent.turn_retry import classify_tool_result, RetryKind as _RK
                _ck = classify_tool_result(str(tool_result))
                if _ck is not None:
                    _act = turn_retry.note_and_decide(
                        _ck, detail=f"{getattr(tc,'name', '')}:{(str(tool_result))[:80]}"
                    )
                    if str(tool_result).startswith("[Security Blocked]"):
                        messages.append({
                            "role": "system",
                            "content": (
                                "上一命令被安全策略拦截。请改用 file_write/edit/apply_patch 写文件；"
                                "或使用单行 shell（避免无必要复杂注入）。不要重复同一被拦命令。"
                            ),
                        })
                    elif _ck == _RK.TOOL_TRANSIENT and "127" in str(tool_result):
                        messages.append({
                            "role": "system",
                            "content": (
                                "命令 Exit 127（未找到）。请检查 cwd 是否在任务工作区、"
                                "是否需 python -m / 完整路径；不要在错误目录重复同一命令。"
                            ),
                        })
                    if _act == "force_final":
                        state.force_final_no_tools = True
            except Exception:
                pass

            await loop._persist_tool_completion(
                session_id, task_id, tc.name, tool_result, query
            )
            if task_id is not None:
                await loop._push_task_update(
                    session_id, task_id, 100, "completed", f"Completed {tc.name}"
                )

            # 实时推送：工具成功结束
            await loop._push_tool_event(
                session_id,
                phase="end",
                tool_call_id=tc.id,
                name=tc.name,
                arguments=args_dict,
                status="completed",
                result=tool_result,
            )
            # 截图推送已退役：前端实时面板改为纯命令流终端（2026-07-26 起），
            # desktop_screenshot 工具本身保留供 agent 视觉感知，仅不再向 WS 推图。
            # 如需恢复推送，还原此行对 loop._maybe_push_screenshot 的调用。
            # TEE: 记录工具轨迹 / 使用次数
            try:
                from backend.evolution.manager import get_evolution_manager

                get_evolution_manager().record_tool(
                    str(session_id),
                    name=tc.name,
                    arguments=args_dict,
                    result=str(tool_result)[:2000],
                    ok=True,
                )
            except Exception:
                pass

            try:
                state.sft_tools.append(
                    {
                        "name": tc.name,
                        "arguments": args_dict if isinstance(args_dict, dict) else {},
                        "result": str(tool_result)[:2000],
                        "ok": True,
                    }
                )
            except Exception:
                pass
            try:
                state.trace_tool_calls.append({
                    "name": tc.name,
                    "arguments": {k: str(v)[:200] for k, v in (args_dict if isinstance(args_dict, dict) else {}).items()},
                    "result_summary": str(tool_result)[:300],
                    "status": "completed",
                    "iteration": iteration + 1,
                })
            except Exception:
                pass

            # manage_goal 结果推送到前端 Goal 面板
            if tc.name == "manage_goal":
                await loop._push_goal_update(session_id)
                try:
                    from backend.agent.goal_state import save_goal_to_db as _save_goal

                    await _save_goal(session_id)
                except Exception as e:
                    logger.debug("save_goal_to_db skipped: %s", e)
        except asyncio.TimeoutError:
            _to = float(getattr(settings, "agent_tool_timeout_seconds", 180) or 180)
            tool_result = f"[Error] Tool '{tc.name}' timed out after {_to:.0f}s"
            query = ""
            logger.warning("Tool %s timed out after %ss", tc.name, _to)
            try:
                state.sft_tools.append(
                    {
                        "name": tc.name,
                        "arguments": args_dict if isinstance(args_dict, dict) else {},
                        "result": str(tool_result)[:2000],
                        "ok": False,
                    }
                )
            except Exception:
                pass

        except Exception as e:
            logger.error(f"Tool execution error: {e}")
            from backend.agent.loop import _sanitize_tool_error

            tool_result = _sanitize_tool_error(tc.name, e)
            try:
                state.sft_tools.append(
                    {
                        "name": tc.name,
                        "arguments": args_dict if isinstance(args_dict, dict) else {},
                        "result": str(tool_result)[:2000],
                        "ok": False,
                    }
                )
            except Exception:
                pass
            await loop._persist_tool_failure(task_id, tc.name, str(e))
            if task_id is not None:
                await loop._push_task_update(
                    session_id, task_id, 0, "failed", str(e)
                )
            await loop._push_tool_event(
                session_id,
                phase="end",
                tool_call_id=tc.id,
                name=tc.name,
                arguments=args_dict if isinstance(args_dict, dict) else {},
                status="failed",
                result=tool_result,
            )

        # Durable Run：记录工具步骤（成功/失败/超时统一在此落库）
        if _rc is not None:
            try:
                _tr_str = str(tool_result)
                _tc_ok = not _tr_str.startswith(("[Error", "[Security Blocked]"))
                await _rc.tool_step(
                    tc.name,
                    args_summary=json.dumps(
                        args_dict if isinstance(args_dict, dict) else {},
                        ensure_ascii=False,
                    )[:500],
                    status="completed" if _tc_ok else "failed",
                    result_summary=_tr_str[:500],
                    duration_ms=(_time.monotonic() - _tc_t0) * 1000,
                )
            except Exception:
                pass

        # 将工具结果追加到 messages（部分 API 需要 name 字段）
        tool_msg = {
            "role": "tool",
            "tool_call_id": tc.id,
            "name": tc.name,
            "content": tool_result,
        }
        messages.append(tool_msg)

        # 持久化 tool 结果（tool_call_id 塞进 tool_calls JSON 旁路字段，保持 list 形态）
        try:
            await loop._save_message(
                session_id,
                "tool",
                tool_result,
                tool_calls=[{"tool_call_id": tc.id, "name": tc.name}],
            )
        except Exception as e:
            msg = str(e)
            if "FOREIGN KEY" in msg or "IntegrityError" in msg:
                logger.warning(
                    "Skip persist tool result (session missing?): %s", e
                )
            else:
                logger.warning(f"Failed to persist tool result message: {e}")

        logger.info(f"Skill {tc.name} executed, result length: {len(tool_result)}")

    # 有 tool 后必须继续下一轮 LLM，不能当最终回复
    logger.info(
        f"Tool round {iteration + 1} done ({len(tool_calls)} calls), continuing agent loop"
    )
    state.last_tool_round_count = len(tool_calls)
    try:
        from backend.agent.decisive import tool_names_from_calls as _tnfc
        state.tools_used_run.extend(_tnfc(tool_calls))
    except Exception:
        pass

    # 果断化：单轮仅 1 个只读工具 → 提示下轮并行/开改
    try:
        from backend.agent.decisive import (
            batch_read_nudge_text,
            batch_write_nudge_text,
            is_timid_read_round,
            is_timid_write_round,
            tool_names_from_calls,
        )

        _tnames = tool_names_from_calls(tool_calls)
        if is_timid_read_round(_tnames, tool_calls) and not state.force_final_no_tools:
            state.timid_read_streak += 1
            state.timid_write_streak = 0
            messages.append(
                {
                    "role": "system",
                    "content": batch_read_nudge_text(
                        consecutive_timid=state.timid_read_streak
                    ),
                }
            )
            logger.info(
                "timid read nudge streak=%s names=%s session=%s",
                state.timid_read_streak,
                _tnames,
                session_id,
            )
        elif is_timid_write_round(_tnames) and not state.force_final_no_tools:
            state.timid_write_streak += 1
            state.timid_read_streak = 0
            messages.append(
                {
                    "role": "system",
                    "content": batch_write_nudge_text(
                        consecutive_timid=state.timid_write_streak
                    ),
                }
            )
            logger.info(
                "timid write nudge streak=%s names=%s session=%s",
                state.timid_write_streak,
                _tnames,
                session_id,
            )
        else:
            state.timid_read_streak = 0
            state.timid_write_streak = 0
    except Exception as _dec_e:
        logger.debug("decisive nudge skipped: %s", _dec_e)

    # dynamic：use_tool_pack enable → 合并工具 schema 供后续轮次
    try:
        from backend.agent.tool_policy import merge_tools_with_packs

        expanded_any = False
        for tc in tool_calls:
            if getattr(tc, "name", None) != "use_tool_pack":
                continue
            raw_args = getattr(tc, "arguments", None) or {}
            if isinstance(raw_args, str):
                try:
                    raw_args = json.loads(raw_args)
                except Exception:
                    raw_args = {}
            if not isinstance(raw_args, dict):
                continue
            action = (raw_args.get("action") or "list").strip().lower()
            packs = raw_args.get("packs") or []
            if isinstance(packs, str):
                packs = [packs]
            if raw_args.get("pack"):
                packs = list(packs) + [raw_args.get("pack")]
            packs = [str(x).strip().lower() for x in packs if str(x).strip()]
            if action == "list" or not packs:
                continue
            new_filter = merge_tools_with_packs(state.enabled_tools_filter, packs)
            if new_filter is None and state.enabled_tools_filter is not None:
                state.enabled_tools_filter = None
                expanded_any = True
            elif new_filter is not None and new_filter != state.enabled_tools_filter:
                state.enabled_tools_filter = new_filter
                expanded_any = True
            if packs:
                for pk in packs:
                    if pk not in state.scene_plan.packs:
                        state.scene_plan.packs.append(pk)
        if expanded_any:
            state.tools = await loop._load_tools(
                session_id,
                enabled_skills,
                state.enabled_tools_filter,
                user_input=user_input,
            )
            await loop._push_status(
                session_id,
                "thinking",
                f"已扩展工具包 → {len(state.tools)} tools ({state.scene_plan.summary()})",
            )
            logger.info(
                "use_tool_pack expanded tools=%s filter=%s",
                len(state.tools),
                "ALL" if state.enabled_tools_filter is None else len(state.enabled_tools_filter),
            )
    except Exception as e:
        logger.debug("use_tool_pack expand skipped: %s", e)
    state.tool_rounds += 1
    # 重复工具/doom-loop 熔断（同名同参连续空转；Batch1 DoomLoopGuard）
    try:
        _calls = [
            (
                getattr(tc, "name", "") or "",
                getattr(tc, "arguments", None),
            )
            for tc in tool_calls
        ]
        _sigs = [
            tool_call_signature(n, a) for n, a in _calls
        ]
        _doom_on = bool(getattr(settings, "agent_doom_loop_enabled", True))
        _tripped = (
            tool_repeat_guard.observe_calls(_calls)
            if _doom_on and hasattr(tool_repeat_guard, "observe_calls")
            else tool_repeat_guard.observe(_sigs)
        )
        if _tripped:
            turn_retry.note_and_decide(
                RetryKind.THRASH, detail=",".join(_sigs)[:180]
            )
            logger.warning(
                "Tool thrash detected for session %s sigs=%s retry=%s",
                session_id,
                _sigs,
                turn_retry.snapshot(),
            )
            await loop._push_status(
                session_id,
                "thinking",
                "检测到重复工具调用，已熔断并改为直接作答…",
            )
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "【工具空转熔断】你连续多次调用了相同工具（参数几乎相同）。"
                        "禁止再调用任何工具。请仅根据已有工具结果，用自然语言直接给出最终答复。"
                    ),
                }
            )
            state.force_final_no_tools = True
            state.suppress_content_stream = False
    except Exception as _thrash_e:
        logger.debug("tool thrash guard skipped: %s", _thrash_e)
    # 多工具并行时：强制下一轮「聚合」而非并列甩多个答案
    if state.last_tool_round_count >= 2:
        state.multi_source_pending = True
        state.suppress_content_stream = True
        messages.append(
            {
                "role": "system",
                "content": (
                    "【多信源聚合】本轮有多个工具结果。请综合为一份给用户的最终中文答复：\n"
                    "1) 合并重复事实，只保留一份结论；\n"
                    "2) 数据冲突时说明取舍（时间/来源更可信者优先）；\n"
                    "3) 禁止「答案1/2/3/4」或按工具原样并排；\n"
                    "4) 不要粘贴工具 JSON/原始日志；\n"
                    "5) 结构清晰：先直接回答，必要时再补一句数据来源说明。\n"
                    "若还需工具再调；否则直接输出最终答复。"
                ),
            }
        )
    # 工具轮后：仅 L1/L3 micro（Claude Code：mid-loop 不跑 full auto-compact/L5）
    # 全量 L5 摘要只在用户回合边界 / 413 reactiveCompact 触发，避免同轮长任务被
    # 「只答最新一句」类指令打断成一拨一动。
    try:
        from backend.agent.context_engine import get_context_engine
        from backend.agent.context_compress import compress_history_if_needed

        eng = get_context_engine()
        do_l1 = (l1_every > 0 and state.tool_rounds % l1_every == 0) or eng.should_compress_preflight(messages)
        if do_l1 and hasattr(eng, "_l1_budget"):
            state.messages, _n = eng._l1_budget(messages)  # type: ignore[attr-defined]
            messages = state.messages
        # 用当前 messages 估 token，避免全局 last_prompt_tokens 跨 session 污染
        need_micro = eng.should_compress_preflight(messages)
        if need_micro:
            state.messages, mid_meta = await compress_history_if_needed(
                messages,
                session_id=session_id,
                threshold=float(
                    getattr(settings, "context_threshold_percent", 0.72) or 0.72
                ),
                allow_l5=False,
                micro_only=True,
            )
            messages = state.messages
            if mid_meta.get("compressed"):
                await loop._push_status(
                    session_id,
                    "optimizing",
                    f"工具轮后 micro 压缩 layers={mid_meta.get('layers')}",
                )
    except Exception as e:
        logger.debug("mid-loop context pipeline skipped: %s", e)
    if checkpoint_every > 0 and state.tool_rounds % checkpoint_every == 0:
        try:
            from backend.agent.checkpoint import save_checkpoint
            from backend.agent.goal_state import get_goal, save_goal_to_db

            await save_checkpoint(
                session_id,
                segment=segment,
                iteration=global_iter + 1,
                mode=mode,
                note=f"tool_round={state.tool_rounds}",
                run_id=str(_rc.run_id) if _rc is not None and _rc.run_id else None,
            )
            if goal_mode:
                await save_goal_to_db(session_id)
        except Exception as e:
            logger.debug("mid-loop checkpoint skipped: %s", e)
    # Goal 模式：工具轮后注入最新 todo 状态，便于模型自检
    if goal_mode:
        from backend.agent.goal_state import get_goal

        g = get_goal(session_id)
        if g and not g.is_complete():
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Updated goal status — continue until complete:\n"
                        + g.summary_for_llm()
                    ),
                }
            )
        elif g and g.is_complete():
            await loop._push_status(
                session_id, "thinking", "Goal completed — summarizing..."
            )
