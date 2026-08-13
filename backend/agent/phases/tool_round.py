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
import re
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
    # 工具空转：相同指纹连续轮次
    thrash_streak: int = 0
    last_tool_fingerprint: str = ""
    # ABAB 交替空转（file_write helper → command → 再写脚本…）
    last_tool_name_sig: str = ""
    alternate_thrash_streak: int = 0
    # audit-fix(#5)：同一工具名连续失败熔断（不论参数）
    last_failed_tool: str = ""
    same_tool_fail_streak: int = 0
    # Outer/inner timeouts — force final after N (default 2), don't wait thrash×2
    timeout_fail_streak: int = 0
    # User asked to write files but model only explores (glob/command/read)
    explore_only_streak: int = 0
    write_intent_hard_nudge: bool = False
    # Toolchain diagnosis thrash (where cargo / rustup / Missing manifest / _diag)
    rust_diag_streak: int = 0
    # P0/P1 progress guard
    deliver_mode: bool = False  # file_read cap / pure-read → write+cargo only
    pure_read_streak: int = 0
    rounds_since_manage_goal: int = 0
    rounds_since_write: int = 0
    result_load_same_streak: int = 0
    last_result_handle: str = ""
    # cargo compile fail → must write before another check
    cargo_fix_streak: int = 0
    must_write_before_cargo: bool = False
    cargo_error_paths: str = ""  # comma-joined
    cargo_error_class: str = ""  # compile_source | path_env | …
    # simple-session turn: block re-adding crew via use_tool_pack
    simple_turn: bool = False


# T1：可安全并发的只读风险等级。写类/命令类一律串行，避免「并发读 + 写同一文件」竞态。
_PARALLEL_SAFE_RISK = frozenset({"safe", "low"})


def _risk_name(tool: Any) -> str:
    rl = getattr(tool, "risk_level", None)
    return str(getattr(rl, "value", rl) or "").lower()


async def _await_with_timeout_cleanup(coro: Any, timeout: float) -> Any:
    """wait_for 超时后显式 cancel + await 清理（L2-H1）。

    裸 wait_for 在超时路径上可能留下仍在跑的子任务回调；重试/下一工具调用
    前必须等取消完成，避免竞态写共享状态。
    """
    task = asyncio.ensure_future(coro)
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except asyncio.TimeoutError:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        raise
    except BaseException:
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        raise


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
                _contact = str(getattr(loop, "_contact_agent", "") or "").strip()
                if _contact:
                    args.setdefault("_contact_agent", _contact)
                    args.setdefault("_identity_name", _contact)
                if getattr(loop, "_identity_id", None):
                    args.setdefault("_identity_id", str(loop._identity_id))
                if getattr(loop, "_inbox_item_id", None):
                    args.setdefault("_inbox_item_id", str(loop._inbox_item_id))
                if timeout > 0:
                    return (
                        await _await_with_timeout_cleanup(
                            loop._execute_registered_tool(tc.name, args),
                            timeout,
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
    return {tc.id: res for (tc, _), res in zip(tools, results, strict=True)}


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
    from backend.agent.robust import tool_call_signature
    from backend.agent.run_state import RunStatus as _RS
    from backend.agent.tool_result_contract import (
        is_tool_error,
        normalize_tool_result,
    )
    from backend.agent.turn_retry import RetryKind
    from backend.repositories.skill_repo import AsyncSkillRepository
    from backend.skills import SkillRegistry
    from backend.skills.dynamic import DynamicSkill

    _rc = getattr(loop, "_run_recorder", None)
    messages = state.messages

    # 编制扇出上限：实测一轮 7–10 个 crew_steward 空转；超出合成结果，仍回 tool 消息。
    # PR4: default max_orch=1; Rust loop_guard is authoritative for workers.
    _capped_results: dict[str, str] = {}
    # Hard gate: simple/solo turn never executes dispatch or goal tools even if schema leaked.
    # Does NOT require tool_call_id — synthesize a stable key when missing.
    if state.simple_turn:
        try:
            from backend.agent.simple_intent import (
                SIMPLE_NOTE_MARKER,
                SOLO_STRIP_TOOLS,
            )

            for tc in tool_calls or []:
                _tn = str(getattr(tc, "name", "") or "")
                if _tn not in SOLO_STRIP_TOOLS:
                    continue
                _cid = str(getattr(tc, "id", "") or "").strip()
                if not _cid:
                    _cid = f"simple-deny-{_tn}-{id(tc)}"
                    try:
                        tc.id = _cid
                    except Exception:
                        pass
                _capped_results[_cid] = (
                    f"[simple_turn] tool '{_tn}' denied — "
                    "answer in-session only (no crew/manage_goal/okr)."
                )
                logger.warning(
                    "simple_turn hard-deny tool=%s id=%s session=%s",
                    _tn,
                    _cid,
                    session_id,
                )
            # If the whole round was only stripped tools, nudge model to final answer
            # (do NOT force_final — web_search etc. may still be needed next round).
            if tool_calls and all(
                str(getattr(tc, "name", "") or "") in SOLO_STRIP_TOOLS
                for tc in tool_calls
            ):
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            f"{SIMPLE_NOTE_MARKER} Orchestration/goal tools were denied this turn. "
                            "Answer from existing info or file_read/web_search/current_time; "
                            "do not call crew/delegate/manage_goal again."
                        ),
                    }
                )
        except Exception as _st_e:
            logger.debug("simple_turn hard-deny skip: %s", _st_e)
    try:
        from backend.agent.decisive import orchestration_cap_results
        from backend.agent.progress_guard import soft_open_mode as _so_orch

        # Soft-open: generous; hard mode still uses settings default (≥8) not 1
        if _so_orch():
            _max_orch = int(
                getattr(settings, "agent_max_orch_tools_per_round", 24) or 24
            )
            _max_orch = max(_max_orch, 24)
        else:
            _max_orch = int(
                getattr(settings, "agent_max_orch_tools_per_round", 8) or 8
            )
            _max_orch = max(_max_orch, 8)
        _orch_skip = orchestration_cap_results(tool_calls, max_orch=_max_orch)
        _capped_results = {**_capped_results, **_orch_skip}
        if _orch_skip:
            logger.warning(
                "orchestration cap: skipped %s crew/delegate calls (max=%s) session=%s",
                len(_orch_skip),
                _max_orch,
                session_id,
            )
    except Exception as _cap_e:
        logger.debug("orchestration cap skipped: %s", _cap_e)

    # PR1–PR4: Rust loop_guard begin_round + per-tool pre checks
    _kproc_lg = getattr(loop, "_kernel_process", None)
    _kpid_lg = str(getattr(_kproc_lg, "id", "") or "") or ""
    # audit-fix(#6)：guard 不可用（无 kernel 进程）时 fail-closed——workforce
    # 会话默认禁止编排类工具（delegate_task/crew_steward 等），非 workforce 保持现状。
    if (
        bool(getattr(settings, "agent_loop_guard_enabled", True))
        and not _kpid_lg
    ):
        try:
            _wf_no_guard = bool(
                getattr(loop, "_workforce", False)
                or str(getattr(loop, "_agent_key", "") or "").startswith("wf:")
            )
            if _wf_no_guard:
                from backend.agent.decisive import is_orchestration_tool as _is_orch_fc

                for tc in tool_calls or []:
                    _cid_fc = str(getattr(tc, "id", "") or "")
                    _tn_fc = str(getattr(tc, "name", "") or "")
                    if (
                        _cid_fc
                        and _cid_fc not in _capped_results
                        and _is_orch_fc(_tn_fc)
                    ):
                        _capped_results[_cid_fc] = (
                            "[LoopGuard] guard unavailable (no kernel process): "
                            f"orchestration tool '{_tn_fc}' denied for workforce "
                            "session (fail-closed)."
                        )
                        logger.warning(
                            "loop_guard fail-closed: blocked orch tool=%s session=%s",
                            _tn_fc,
                            session_id,
                        )
        except Exception as _fc_e:
            logger.debug("loop_guard fail-closed skip: %s", _fc_e)
    if bool(getattr(settings, "agent_loop_guard_enabled", True)) and _kpid_lg:
        try:
            from backend.agent.loop_guard_bridge import (
                begin_round,
                force_final_message,
            )
            from backend.agent.loop_guard_bridge import (
                pre_tool as lg_pre_tool,
            )

            _names = [
                str(getattr(tc, "name", None) or "")
                for tc in (tool_calls or [])
            ]
            # audit-fix: sync kernel RPC → to_thread，避免阻塞事件循环
            _br = await asyncio.to_thread(begin_round, _kpid_lg, _names)
            if isinstance(_br, dict) and _br.get("status") == "force_final":
                _br_code = str(_br.get("code") or "max_tool_rounds")
                # Soft-open / product default: orch thrash must not hard-stop steward dispatch
                _soft_orch_ff = False
                try:
                    from backend.agent.progress_guard import soft_open_mode as _so_br
                    from backend.core.config import settings as _st_br

                    if _so_br() and _br_code in (
                        "orch_window_thrash",
                        "crew_total_cap",
                        "orch_per_round_cap",
                    ):
                        _soft_orch_ff = True
                    # Default agent_orch_window_force_final=False: always soft for window thrash
                    # even when non-goal soft_open is off (main cause of "派单被系统节流")
                    if (
                        not bool(
                            getattr(_st_br, "agent_orch_window_force_final", False)
                        )
                        and _br_code == "orch_window_thrash"
                    ):
                        _soft_orch_ff = True
                except Exception:
                    pass
                if _soft_orch_ff:
                    from backend.agent.loop_decision import soft_orch_window
                    _sm = soft_orch_window(str(_br_code or "")).as_system_message()
                    if _sm:
                        messages.append(_sm)
                    logger.info(
                        "loop_guard begin_round soft-open skip force_final process=%s %s",
                        _kpid_lg[:8],
                        _br,
                    )
                else:
                    state.force_final_no_tools = True
                    try:
                        loop.last_exit_reason = _br_code
                    except Exception:
                        pass
                    messages.append(
                        {
                            "role": "system",
                            "content": force_final_message(
                                _br_code,
                                str(_br.get("reason") or ""),
                            ),
                        }
                    )
                    logger.warning(
                        "loop_guard begin_round force_final process=%s %s",
                        _kpid_lg[:8],
                        _br,
                    )
                    # Block all tools this round (Claude max_turns style)
                    for tc in tool_calls or []:
                        _cid = str(getattr(tc, "id", "") or "")
                        if _cid:
                            _capped_results[_cid] = (
                                f"[LoopGuard] {_br.get('code')}: tools blocked — "
                                "write final answer only."
                            )
            else:
                for tc in tool_calls or []:
                    _cid = str(getattr(tc, "id", "") or "")
                    if not _cid or _cid in _capped_results:
                        continue
                    _args = getattr(tc, "arguments", None)
                    if isinstance(_args, str):
                        try:
                            _args = json.loads(_args)
                        except Exception:
                            _args = {"_raw": _args}
                    if not isinstance(_args, dict):
                        _args = {}
                    _pt = await asyncio.to_thread(  # audit-fix: sync RPC → to_thread
                        lg_pre_tool,
                        _kpid_lg,
                        str(getattr(tc, "name", "") or ""),
                        _args,
                    )
                    if isinstance(_pt, dict) and _pt.get("status") == "block":
                        _pt_code = str(_pt.get("code") or "")
                        # Soft-open or relaxed product defaults: steward crew/orch caps soft-allow
                        # (still ban worker_orch_banned — workers must not re-dispatch)
                        try:
                            from backend.agent.progress_guard import (
                                soft_open_mode as _so_pt,
                            )
                            from backend.core.config import settings as _st_pt

                            _soft_pt = _so_pt()
                            # When soft_open off but window force_final disabled, still soft
                            # crew/orch per-round caps for main chat (not worker ban).
                            if not _soft_pt and not bool(
                                getattr(_st_pt, "agent_orch_window_force_final", False)
                            ):
                                _soft_pt = _pt_code in (
                                    "crew_total_cap",
                                    "orch_per_round_cap",
                                )
                            if _soft_pt and _pt_code in (
                                "crew_total_cap",
                                "orch_per_round_cap",
                                "orch_per_round_zero",
                            ):
                                logger.info(
                                    "loop_guard soft-open allow orch block code=%s tool=%s",
                                    _pt_code,
                                    getattr(tc, "name", ""),
                                )
                                continue
                        except Exception:
                            pass
                        _msg_pt = str(
                            _pt.get("message")
                            or f"[LoopGuard] blocked {getattr(tc, 'name', '')}"
                        )
                        _capped_results[_cid] = _msg_pt
                        # P0: file_read cap → deliver-only mode
                        try:
                            from backend.agent.progress_guard import (
                                deliver_mode_nudge,
                                filter_names_deliver_only,
                                filter_tools_deliver_only,
                                is_file_read_cap_message,
                            )
                            from backend.agent.progress_guard import (
                                soft_open_mode as _so_fr,
                            )
                            from backend.core.config import settings as _st_dm

                            if (
                                not _so_fr()
                                and bool(
                                    getattr(
                                        _st_dm,
                                        "agent_file_read_cap_deliver_mode",
                                        False,
                                    )
                                )
                                and (
                                    _pt.get("code") == "max_file_reads"
                                    or is_file_read_cap_message(_msg_pt)
                                )
                            ):
                                try:
                                    from backend.agent.progress_guard import (
                                        should_arm_deliver_mode as _should_dm,
                                    )

                                    _arm_dm = _should_dm(
                                        str(user_input or ""),
                                        reason="file_read_cap",
                                    )
                                except Exception:
                                    _arm_dm = True
                                if _arm_dm:
                                    try:
                                        from backend.agent.progress_guard import (
                                            soft_open_mode as _so_frc,
                                        )

                                        _so = bool(_so_frc())
                                    except Exception:
                                        _so = False
                                    if _so:
                                        # Soft-open: nudge only, never strip tools
                                        messages.append(
                                            {
                                                "role": "system",
                                                "content": deliver_mode_nudge(),
                                            }
                                        )
                                        logger.info(
                                            "file_read cap soft-nudge (no deliver strip) "
                                            "session=%s",
                                            session_id,
                                        )
                                    else:
                                        state.deliver_mode = True
                                        state.tools = filter_tools_deliver_only(
                                            state.tools
                                        )
                                        if isinstance(
                                            state.enabled_tools_filter, list
                                        ):
                                            state.enabled_tools_filter = (
                                                filter_names_deliver_only(
                                                    state.enabled_tools_filter
                                                )
                                            )
                                        messages.append(
                                            {
                                                "role": "system",
                                                "content": deliver_mode_nudge(),
                                            }
                                        )
                                        logger.warning(
                                            "deliver_mode ON (file_read cap) session=%s",
                                            session_id,
                                        )
                                else:
                                    logger.info(
                                        "skip deliver_mode on file_read cap "
                                        "(review-only task) session=%s",
                                        session_id,
                                    )
                        except Exception as _dm_e:
                            logger.debug("deliver_mode arm skip: %s", _dm_e)
                        logger.info(
                            "loop_guard pre_tool block tool=%s code=%s process=%s",
                            getattr(tc, "name", ""),
                            _pt.get("code"),
                            _kpid_lg[:8],
                        )
                    elif isinstance(_pt, dict) and _pt.get("status") == "force_final":
                        state.force_final_no_tools = True
                        _capped_results[_cid] = str(
                            _pt.get("message")
                            or force_final_message(str(_pt.get("code") or ""))
                        )
        except Exception as _lge:
            logger.debug("loop_guard pre-round skip: %s", _lge)

    # Consume proactive bg cargo_fix arm (from loop inject before LLM)
    try:
        from backend.agent.progress_guard import (
            cargo_fix_nudge as _cfn_cons,
        )
        from backend.agent.progress_guard import (
            consume_session_cargo_fix as _cons_cf,
        )

        _cf_pending = _cons_cf(str(session_id))
        if _cf_pending and _cf_pending.get("must_write"):
            state.must_write_before_cargo = True
            try:
                from backend.agent.progress_guard import soft_open_mode as _so_cf

                if not _so_cf():
                    state.deliver_mode = True
            except Exception:
                state.deliver_mode = True
            _ps = list(_cf_pending.get("paths") or [])
            if _ps:
                state.cargo_error_paths = ",".join(_ps[:5])
            # nudge may already be in messages from inject; reinforce once
            if not any(
                isinstance(m, dict)
                and m.get("role") == "system"
                and "编译失败·强制改代码" in str(m.get("content") or "")
                for m in (messages or [])[-6:]
            ):
                messages.append(
                    {"role": "system", "content": _cfn_cons(_ps)}
                )
            logger.info(
                "consume session cargo_fix source=%s paths=%s session=%s",
                _cf_pending.get("source"),
                state.cargo_error_paths[:60],
                session_id,
            )
    except Exception as _cons_e:
        logger.debug("consume cargo_fix skip: %s", _cons_e)

    # Deliver / cargo-fix gates BEFORE execute (state from previous round)
    # Soft-open mode: skip hard walls entirely (model free; only later soft nudges).
    try:
        from backend.agent.progress_guard import (
            blocked_with_next as _bwn,
        )
        from backend.agent.progress_guard import (
            command_from_tool as _cmd_pre,
        )
        from backend.agent.progress_guard import (
            extract_tool_args as _args_pre,
        )
        from backend.agent.progress_guard import (
            is_cargo_verify_command as _is_cvc_pre,
        )
        from backend.agent.progress_guard import (
            is_deliver_allowed_command as _is_dac_pre,
        )
        from backend.agent.progress_guard import (
            is_deliver_allowed_grep as _is_dag_pre,
        )
        from backend.agent.progress_guard import (
            is_diag_junk_path as _is_junk_pre,
        )
        from backend.agent.progress_guard import (
            is_probe_overwrite as _is_probe_pre,
        )
        from backend.agent.progress_guard import (
            is_progress_write as _is_pw_pre,
        )
        from backend.agent.progress_guard import (
            soft_open_mode as _soft_open,
        )

        _dm = bool(getattr(state, "deliver_mode", False)) and not _soft_open()
        _mw = bool(getattr(state, "must_write_before_cargo", False)) and not _soft_open()
        for _tc in tool_calls or []:
            _cid = str(getattr(_tc, "id", "") or "")
            _nm = str(getattr(_tc, "name", "") or "")
            if not _cid or _cid in _capped_results:
                continue
            # Always block junk path writes (not just deliver)
            if _nm in ("file_write", "edit", "apply_patch", "desktop_write_file"):
                _a = _args_pre(_tc)
                _p = str(
                    _a.get("path")
                    or _a.get("filepath")
                    or _a.get("file")
                    or _a.get("file_path")
                    or ""
                )
                if _p and _is_junk_pre(_p):
                    _capped_results[_cid] = _bwn(
                        f"[Blocked] Diagnostic junk path write denied: {_p}.",
                        "junk_write",
                    )
                    continue
                if _mw and not _is_pw_pre(_nm, _a):
                    _capped_results[_cid] = _bwn(
                        "[Blocked] After cargo compile failure, edit product sources. "
                        f"Targets: {getattr(state, 'cargo_error_paths', '') or 'error --> path'}.",
                        "must_write_blocks_cargo",
                        paths=str(getattr(state, "cargo_error_paths", "") or ""),
                    )
                    continue
                # Block thrash probe clobber of lib.rs when cargo_fix is armed
                if _mw and _nm == "file_write":
                    _body = str(
                        _a.get("content") or _a.get("text") or _a.get("code") or ""
                    )
                    if _p and _is_probe_pre(_p, _body):
                        _capped_results[_cid] = _bwn(
                            f"[Blocked] Probe-like overwrite of product source: {_p}.",
                            "probe_overwrite",
                        )
                        continue
            if _soft_open() or not (_dm or _mw):
                continue
            if _nm == "python" and _dm:
                _capped_results[_cid] = _bwn(
                    "[Blocked] deliver mode: no python dump.",
                    "deliver_blocks_shell",
                )
                continue
            if _nm == "file_read" and _dm:
                _capped_results[_cid] = _bwn(
                    "[Blocked] deliver mode: no file_read.",
                    "deliver_blocks_read",
                )
                continue
            if _nm == "glob" and _dm:
                _capped_results[_cid] = _bwn(
                    "[Blocked] deliver mode: no glob scan.",
                    "deliver_blocks_read",
                )
                continue
            # Whole-file grep thrash (.* / ^ / [\s\S]) blocked in deliver
            if _nm == "grep" and (_dm or _mw):
                _ga = _args_pre(_tc)
                _gpat = str(_ga.get("pattern") or _ga.get("query") or "")
                _gpath = str(_ga.get("path") or _ga.get("glob") or "")
                if not _is_dag_pre(_gpat, _gpath):
                    _capped_results[_cid] = _bwn(
                        "[Blocked] deliver mode: no whole-file grep "
                        "(patterns like .* / ^ / [\\s\\S]).",
                        "whole_file_grep",
                    )
                    continue
            if _nm != "command":
                continue
            _cmd = _cmd_pre(_tc)
            if _mw and _is_cvc_pre(_cmd):
                _capped_results[_cid] = _bwn(
                    "[Blocked] Compile errors unfixed: no cargo check/build yet."
                    + (
                        f" Targets: {getattr(state, 'cargo_error_paths', '')}"
                        if getattr(state, "cargo_error_paths", "")
                        else ""
                    ),
                    "must_write_blocks_cargo",
                    paths=str(getattr(state, "cargo_error_paths", "") or ""),
                )
                continue
            if _dm and not _is_dac_pre(_cmd):
                _capped_results[_cid] = _bwn(
                    "[Blocked] deliver mode allows only cargo check/build/test/clean "
                    "and git status/diff.",
                    "deliver_blocks_shell",
                )
    except Exception as _pre_dm:
        logger.debug("deliver pre-gate skip: %s", _pre_dm)

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
            # 编制 cap：不进真实执行
            if str(getattr(tc, "id", "") or "") in _capped_results:
                tool_result = _capped_results[str(tc.id)]
                query = ""
            # T1：只读工具已在本轮开始时并发跑完，这里直接取结果；
            # 异常原样重抛，交给下面既有的 TimeoutError / Exception 处理分支，
            # 保证并行与串行的失败语义完全一致。
            elif tc.id in prefetched:
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
                validated_args["_run_origin"] = str(
                    getattr(loop, "_run_origin", None) or "chat"
                )
                validated_args["_agent_key"] = str(
                    getattr(loop, "_agent_key", None) or "main"
                )
                # 联系员工：危险确认「本员工允许」需要 identity
                _contact = str(getattr(loop, "_contact_agent", "") or "").strip()
                if _contact:
                    validated_args.setdefault("_contact_agent", _contact)
                    validated_args.setdefault("_identity_name", _contact)
                if getattr(loop, "_identity_id", None):
                    validated_args.setdefault("_identity_id", str(loop._identity_id))
                if getattr(loop, "_identity_name", None):
                    validated_args.setdefault("_identity_name", str(loop._identity_name))
                # 编制员工上下文（dispatcher 写入 loop 属性）→ steward 裁决，不弹主人
                if getattr(loop, "_workforce", False) or str(
                    getattr(loop, "_agent_key", "") or ""
                ).startswith("wf:"):
                    validated_args["_workforce"] = True
                    validated_args["_agent_key"] = getattr(loop, "_agent_key", "wf:")
                    validated_args["_run_origin"] = "inbox"
                    if getattr(loop, "_identity_id", None):
                        validated_args["_identity_id"] = str(loop._identity_id)
                    if getattr(loop, "_identity_name", None):
                        validated_args["_identity_name"] = str(loop._identity_name)
                    caps = getattr(loop, "_identity_capabilities", None)
                    if caps is not None:
                        validated_args["_identity_capabilities"] = list(caps)
                    # 员工无前端确认通道：禁止误走 interactive 弹窗
                    validated_args["_ws_manager"] = None
                _tool_timeout = float(
                    getattr(settings, "agent_tool_timeout_seconds", 180) or 0
                )
                # Cap per-call by outer budget so tool-internal timeout never
                # schedules work the agent loop will cancel first.
                if _tool_timeout > 0 and isinstance(validated_args, dict):
                    try:
                        _req = float(
                            validated_args.get("timeout")
                            or validated_args.get("timeout_seconds")
                            or 0
                        )
                    except (TypeError, ValueError):
                        _req = 0.0
                    if _req > 0:
                        validated_args["timeout"] = max(
                            1, int(min(_req, _tool_timeout - 2))
                        )
                    elif tc.name in ("command", "python", "shell_session"):
                        # Explicit default under outer ceiling
                        validated_args.setdefault(
                            "timeout",
                            max(15, int(min(90, _tool_timeout - 5))),
                        )
                if _tool_timeout > 0:
                    tool_result = await _await_with_timeout_cleanup(
                        loop._execute_registered_tool(tc.name, validated_args),
                        _tool_timeout,
                    )
                else:
                    tool_result = await loop._execute_registered_tool(tc.name, validated_args)
                query = (
                    tc.arguments.get("query", "")
                    if tc.name == "search_knowledge_base"
                    else ""
                )
            else:
                # 兼容旧方式：SkillRegistry / DB skill —— 仍必须经 Kernel 门控。
                # Hardening：禁止 skill.execute / Registry.execute 绕过 mediate。
                skill = SkillRegistry.get_skill(tc.name)
                if skill is not None:
                    validated_args = loop._validate_tool_args(skill.parameters, tc.arguments)
                    if loop.user_id is not None:
                        validated_args["user_id"] = str(loop.user_id)
                        validated_args["_user_id"] = str(loop.user_id)
                    validated_args["_session_id"] = str(session_id)
                    validated_args["_chat_mode"] = str(mode or "default")
                    validated_args["_ws_manager"] = loop.ws_manager
                    # 注入编制 / 身份 / process，与主路径一致
                    if getattr(loop, "_workforce", False) or str(
                        getattr(loop, "_agent_key", "") or ""
                    ).startswith("wf:"):
                        validated_args["_workforce"] = True
                        validated_args["_agent_key"] = getattr(loop, "_agent_key", "wf:")
                        validated_args["_ws_manager"] = None
                    _kproc = getattr(loop, "_kernel_process", None)
                    if _kproc is not None:
                        validated_args["_kernel_process_id"] = _kproc.id
                    from backend.kernel.tool_gate import enforce_tool_gate

                    validated_args, _gate_err = await enforce_tool_gate(
                        tc.name, validated_args
                    )
                    if _gate_err:
                        tool_result = _gate_err
                    else:
                        tool_result = await skill.execute(**validated_args)
                    query = ""
                else:
                    # 尝试执行数据库中的自定义 Skill / Tool
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
                        if getattr(loop, "_workforce", False) or str(
                            getattr(loop, "_agent_key", "") or ""
                        ).startswith("wf:"):
                            validated_args["_workforce"] = True
                            validated_args["_ws_manager"] = None
                        _kproc = getattr(loop, "_kernel_process", None)
                        if _kproc is not None:
                            validated_args["_kernel_process_id"] = _kproc.id
                        from backend.repositories.tool_repo import AsyncToolRepository

                        tool_repo = AsyncToolRepository()
                        db_tool = await tool_repo.get_tool_by_name(tc.name)
                        if db_tool is not None and db_tool.enabled:
                            # 走 Registry（内含 tool_gate）；参数用 validated 而非裸 tc.arguments
                            tool_result = await UnifiedToolRegistry.execute(
                                tc.name, validated_args
                            )
                            query = ""
                        else:
                            # 仅有 skill 元数据、无 Tool 行：门控后走 dynamic 执行
                            # （仍不得绕过 mediate）
                            from backend.kernel.tool_gate import enforce_tool_gate

                            validated_args, _gate_err = await enforce_tool_gate(
                                tc.name, validated_args
                            )
                            if _gate_err:
                                tool_result = _gate_err
                            else:
                                try:
                                    tool_result = await dynamic.execute(**validated_args)
                                except Exception as _de:
                                    tool_result = (
                                        f"[Error] Tool '{tc.name}' not found or disabled "
                                        f"({type(_de).__name__}: {_de})"
                                    )
                            query = ""
                    else:
                        tool_result = f"[Error] Tool '{tc.name}' not found or disabled"
                        query = ""

            # 工具结果契约：统一 str / 截断 / 空结果；P0.5 大结果外置句柄
            _max_tr = int(getattr(settings, "max_tool_result_length", 12_000) or 12_000)
            _tname = getattr(tc, "name", "") or ""
            _kproc = getattr(loop, "_kernel_process", None)
            _kpid = str(getattr(_kproc, "id", "") or "") or None
            _raw_before_trunc = tool_result
            tool_result = normalize_tool_result(
                tool_result, tool_name=_tname, process_id=_kpid
            )
            # 全局硬顶（settings）仍生效；但 TOOL_RESULT_BUDGET 里显式给出的
            # 更高的 per-tool 预算优先（T3：file_read 已按行边界自分页并给出续读
            # offset，若在此被通用上限二次 head+tail 拼接，模型会拿到断裂视图）。
            from backend.agent.tool_result_contract import TOOL_RESULT_BUDGET

            _cap = max(_max_tr, int(TOOL_RESULT_BUDGET.get(_tname, 0) or 0))
            _was_truncated = False
            if _cap and len(tool_result) > _cap:
                from backend.agent.tool_result_contract import truncate_for_llm
                tool_result = truncate_for_llm(_tname, tool_result, budget=_cap)
                _was_truncated = True
            if (
                isinstance(_raw_before_trunc, str)
                and isinstance(tool_result, str)
                and len(tool_result) < len(_raw_before_trunc)
            ):
                _was_truncated = True
            if isinstance(tool_result, str) and (
                "omitted for LLM" in tool_result
                or "chars omitted" in tool_result
                or "persisted-output" in tool_result
            ):
                _was_truncated = True
            # PR3: note truncated file_read paths in Rust loop_guard
            if bool(getattr(settings, "agent_loop_guard_enabled", True)) and _kpid:
                try:
                    from backend.agent.loop_guard_bridge import post_tool as lg_post

                    await asyncio.to_thread(  # audit-fix: sync RPC → to_thread
                        lg_post,
                        _kpid,
                        _tname,
                        args_dict if isinstance(args_dict, dict) else {},
                        result=str(tool_result)[:4000],
                        truncated=_was_truncated,
                    )
                except Exception:
                    pass
            # shell 安全拦截 / 127：记入重试分类，并提示改用 file_write 或修 cwd
            try:
                from backend.agent.turn_retry import RetryKind as _RK
                from backend.agent.turn_retry import classify_tool_result
                _ck = classify_tool_result(str(tool_result))
                if _ck is not None:
                    _act = turn_retry.note_and_decide(
                        _ck, detail=f"{getattr(tc,'name', '')}:{(str(tool_result))[:80]}"
                    )
                    if str(tool_result).startswith("[Security Blocked]"):
                        from backend.agent.loop_decision import security_blocked
                        _msg = security_blocked().as_system_message()
                        if _msg:
                            messages.append(_msg)
                    elif _ck == _RK.TOOL_TRANSIENT and "127" in str(tool_result):
                        from backend.agent.loop_decision import command_not_found
                        _msg = command_not_found().as_system_message()
                        if _msg:
                            messages.append(_msg)
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
                duration_ms=(_time.monotonic() - _tc_t0) * 1000,
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

            # manage_goal 结果推送到前端 Goal 面板（落库已在 skill 内完成）
            if tc.name == "manage_goal":
                await loop._push_goal_update(session_id)
                try:
                    from backend.agent.goal_state import save_goal_to_db as _save_goal

                    # 双保险：skill 已写穿；此处幂等再刷一次防旁路调用
                    await _save_goal(session_id)
                except Exception as e:
                    logger.debug("save_goal_to_db skipped: %s", e)
            # 串行工具间让出一轮事件循环（多 session 并发更顺，不改 loop 结构）
            try:
                await asyncio.sleep(0)
            except Exception:
                pass
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
            # UI must leave "running" — success/exception paths push end; timeout did not.
            try:
                await loop._push_tool_event(
                    session_id,
                    phase="end",
                    tool_call_id=tc.id,
                    name=tc.name,
                    arguments=args_dict if isinstance(args_dict, dict) else {},
                    status="failed",
                    result=tool_result,
                    duration_ms=(_time.monotonic() - _tc_t0) * 1000,
                )
            except Exception:
                pass
            if task_id is not None:
                try:
                    await loop._push_task_update(
                        session_id, task_id, 0, "failed", tool_result[:200]
                    )
                except Exception:
                    pass
            # Fast force-final: consecutive timeouts are the most expensive thrash.
            try:
                state.timeout_fail_streak = int(
                    getattr(state, "timeout_fail_streak", 0) or 0
                ) + 1
                _to_cap = max(
                    1,
                    int(getattr(settings, "agent_tool_timeout_force_final", 2) or 2),
                )
                if state.timeout_fail_streak >= _to_cap:
                    state.force_final_no_tools = True
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                f"[tool_timeout_force_final] Tool '{tc.name}' timed out "
                                f"{state.timeout_fail_streak}×. Stop calling long-running "
                                "tools; answer with what you have, or use background/"
                                "narrower commands."
                            ),
                        }
                    )
                    logger.warning(
                        "tool timeout force_final name=%s streak=%s session=%s",
                        tc.name,
                        state.timeout_fail_streak,
                        session_id,
                    )
            except Exception:
                pass

        except Exception as e:
            logger.error(f"Tool execution error: {e}")
            from backend.agent.tool_errors import sanitize_tool_error

            tool_result = sanitize_tool_error(tc.name, e)
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
                duration_ms=(_time.monotonic() - _tc_t0) * 1000,
            )

        # Durable Run：记录工具步骤（成功/失败/超时统一在此落库）
        if _rc is not None:
            try:
                from backend.agent.tool_result_contract import is_tool_error as _is_terr

                _tr_str = str(tool_result)
                _tc_ok = not _is_terr(_tr_str)
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

        # audit-fix(#5)：同一工具名连续失败计数（不论参数）——成功/换工具即清零
        try:
            _tn_fail = str(getattr(tc, "name", "") or "")
            _res_s = str(tool_result or "")
            if is_tool_error(_res_s):
                if _tn_fail and _tn_fail == state.last_failed_tool:
                    state.same_tool_fail_streak = int(state.same_tool_fail_streak or 0) + 1
                else:
                    state.last_failed_tool = _tn_fail
                    state.same_tool_fail_streak = 1
                # Inner command/python timeouts return [Timeout] without raising
                # asyncio.TimeoutError — still count toward timeout force_final.
                _is_to = (
                    _res_s.lstrip().startswith("[Timeout]")
                    or "timed out after" in _res_s.lower()
                    or "exceeded" in _res_s.lower()
                    and "terminat" in _res_s.lower()
                )
                if _is_to:
                    # Outer TimeoutError path already +1; avoid double-count when
                    # message also says timed out after (same branch sets tool_result).
                    if "Tool '" in _res_s and "timed out after" in _res_s:
                        pass  # already counted in except TimeoutError
                    else:
                        state.timeout_fail_streak = int(
                            getattr(state, "timeout_fail_streak", 0) or 0
                        ) + 1
                        _to_cap = max(
                            1,
                            int(
                                getattr(settings, "agent_tool_timeout_force_final", 2)
                                or 2
                            ),
                        )
                        if (
                            state.timeout_fail_streak >= _to_cap
                            and not state.force_final_no_tools
                        ):
                            state.force_final_no_tools = True
                            messages.append(
                                {
                                    "role": "system",
                                    "content": (
                                        f"[tool_timeout_force_final] Tool '{tc.name}' "
                                        f"timed out {state.timeout_fail_streak}×. "
                                        "Answer with available results."
                                    ),
                                }
                            )
            else:
                state.last_failed_tool = ""
                state.same_tool_fail_streak = 0
                state.timeout_fail_streak = 0
        except Exception:
            pass

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
                    "Session gone (FK) on tool result — stop run session=%s: %s",
                    session_id,
                    e,
                )
                try:
                    loop._should_stop = True
                except Exception:
                    pass
                try:
                    from backend.api.websocket import manager as ws_manager

                    ws_manager.end_run_snapshot(session_id)
                except Exception:
                    pass
            else:
                logger.warning(f"Failed to persist tool result message: {e}")

        _tr_s = str(tool_result or "")
        if _tr_s.startswith(("[Hook Blocked]", "[permission deny]", "[Orchestration cap]")):
            logger.info(
                "Skill %s blocked/capped, result length: %s",
                tc.name,
                len(_tr_s),
            )
        else:
            logger.info(
                "Skill %s executed, result length: %s",
                tc.name,
                len(_tr_s),
            )

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

    # 多路大结果外置后：引导 result_load + 总结，降低 DeepSeek 等再搜/DSML 泄漏
    try:
        from backend.agent.progress_guard import extract_result_handle as _ext_hid

        _handles: list[str] = []
        _search_n = 0
        for _m in messages[-max(16, len(tool_calls) * 3) :]:
            if not isinstance(_m, dict) or _m.get("role") != "tool":
                continue
            _c = str(_m.get("content") or "")
            _nm = str(_m.get("name") or "")
            if any(
                x in _nm.lower()
                for x in ("search", "tavily", "web_", "fetch", "extract", "scrape")
            ):
                _search_n += 1
            _hid = _ext_hid(_c)
            if _hid:
                _handles.append(_hid)
        _uniq = list(dict.fromkeys(_handles))
        if len(_uniq) >= 2 and _search_n >= 2:
            _ids = ", ".join(f'`{h}`' for h in _uniq[:4])
            messages.append(
                {
                    "role": "system",
                    "content": (
                        f"[Result paging · multi-search] 本轮有 {len(_uniq)} 个外置大结果"
                        f"（handles: {_ids}）。"
                        "请优先 `result_load` 分页读取最相关的 1–2 个 handle，"
                        "然后用中文给出可执行结论；"
                        "**不要**用近乎重复的 query 再调 web_search/mcp_*_search，"
                        "也不要把 tool 写成 DSML / 正文标签。"
                    ),
                }
            )
            logger.info(
                "multi-search spill converge handles=%s session=%s",
                len(_uniq),
                session_id,
            )
    except Exception as _ms_e:
        logger.debug("multi-search spill converge skip: %s", _ms_e)

    # ── Rust toolchain diagnosis thrash (where/dir/rustup/_diag 复读) ──
    try:
        from backend.agent.decisive import tool_names_from_calls as _tn_rd

        _rd_names = _tn_rd(tool_calls)
        _rd_blob = " ".join(
            str(getattr(tc, "result", None) or getattr(tc, "output", None) or "")[:400]
            for tc in (tool_calls or [])
        )
        # also pull from messages just appended (tool role)
        for _m in messages[-max(12, len(tool_calls) * 2) :]:
            if isinstance(_m, dict) and _m.get("role") == "tool":
                _rd_blob += " " + str(_m.get("content") or "")[:500]
        _is_rust_diag = bool(
            re.search(
                r"(?i)Missing manifest|RUSTUP_HOME|\[Blocked\].{0,40}rustup|"
                r"\[Blocked\].{0,40}cargo|where\s+cargo|_cargo_check|_diag_rust|"
                r"rustup default|toolchain.*msvc|\.rustup\\toolchains",
                _rd_blob,
            )
        ) or bool(
            re.search(
                r"(?i)(_cargo_|_diag_|_reinstall_rust|vcvars|rustup)",
                " ".join(
                    str(getattr(tc, "arguments", None) or getattr(tc, "args", None) or "")
                    for tc in (tool_calls or [])
                ),
            )
        )
        _only_cmd_proc = bool(_rd_names) and all(
            n in ("command", "process", "python", "file_write") for n in _rd_names
        )
        _wrote_src = any(
            n in ("file_write", "edit", "apply_patch") for n in _rd_names
        ) and not re.search(
            r"(?i)_cargo_|_diag_|_reinstall|_hello\.|_t\.rs",
            " ".join(
                str(getattr(tc, "arguments", None) or getattr(tc, "args", None) or "")
                for tc in (tool_calls or [])
            ),
        )
        if _wrote_src:
            state.rust_diag_streak = 0
        elif _is_rust_diag and _only_cmd_proc:
            state.rust_diag_streak = int(getattr(state, "rust_diag_streak", 0) or 0) + 1
            if state.rust_diag_streak == 1:
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "[Stop toolchain thrash] Job already pins scoop cargo + MSVC. "
                            "Avoid where/dir/.rustup/rustup/_diag/_cargo_*.bat/RUSTUP_*. "
                            "Next: `cargo check -p <crate>` or file_write for compile errors."
                        ),
                    }
                )
                logger.warning(
                    "rust_diag soft stop streak=%s session=%s",
                    state.rust_diag_streak,
                    session_id,
                )
            # Soft-open: only force_final after 4 pure diag rounds (was 2 — cut
            # mid-implementation when cargo check / env noise co-occurred).
            if state.rust_diag_streak >= 4 and not state.force_final_no_tools:
                state.force_final_no_tools = True
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "[rust/cargo env thrash] Many env-diagnostic rounds. "
                            "No tools this turn: short summary of last cargo error and "
                            "files already edited; no more diagnostic scripts or force-final essays."
                        ),
                    }
                )
                logger.warning(
                    "rust_diag force_final streak=%s session=%s",
                    state.rust_diag_streak,
                    session_id,
                )
            elif state.rust_diag_streak >= 2:
                logger.warning(
                    "rust_diag soft stop streak=%s session=%s",
                    state.rust_diag_streak,
                    session_id,
                )
        elif not _is_rust_diag:
            # decay slowly when doing real work
            if int(getattr(state, "rust_diag_streak", 0) or 0) > 0 and _wrote_src:
                state.rust_diag_streak = 0
    except Exception as _rd_e:
        logger.debug("rust_diag guard skip: %s", _rd_e)

    # ── Write-intent: flexible coding focus (NO hard write-only lock) ─
    # Hard WRITE_ONLY strips caused 复读卡死. Soft policy only:
    #  - nudge to write after pure-explore streaks
    #  - optionally demote web/crew (keep file_read/command/python)
    #  - never force_final text-only for write intent
    #  - re-apply flex if already focused (pack expand may re-add noise)
    _write_intent_active = False
    try:
        from backend.agent.write_intent import (
            EXPLORE_TOOLS,
            WRITE_TOOLS,
            filter_names_coding_flex,
            filter_tools_coding_flex,
            is_write_intent,
            write_intent_nudge_text,
        )

        _write_intent_active = is_write_intent(user_input or "")
        if _write_intent_active:
            _names = []
            try:
                from backend.agent.decisive import tool_names_from_calls as _tn

                _names = _tn(tool_calls)
            except Exception:
                _names = [str(getattr(t, "name", "") or "") for t in tool_calls]
            _wrote = any(n in WRITE_TOOLS for n in _names)
            _only_explore = bool(_names) and all(
                n in EXPLORE_TOOLS or n in ("crew_steward", "clarify", "manage_goal")
                for n in _names
            )
            # Pure multi-read without write also counts toward explore streak
            _only_read = bool(_names) and all(
                n in ("file_read", "doc_read", "glob", "grep") for n in _names
            )
            if _wrote:
                state.explore_only_streak = 0
                # keep flex focus after first write (still demote web/crew noise)
            elif _only_explore or _only_read:
                state.explore_only_streak = int(
                    getattr(state, "explore_only_streak", 0) or 0
                ) + 1
                if state.explore_only_streak == 2:
                    messages.append(
                        {
                            "role": "system",
                            "content": write_intent_nudge_text(soft=True),
                        }
                    )
                    logger.info(
                        "write_intent soft nudge streak=%s session=%s",
                        state.explore_only_streak,
                        session_id,
                    )
                # Soft focus after 3 explore-only rounds: drop web/crew only
                if state.explore_only_streak >= 3:
                    state.force_final_no_tools = False
                    state.write_intent_hard_nudge = True
                    before_n = len(state.tools or [])
                    state.tools = filter_tools_coding_flex(state.tools)
                    state.enabled_tools_filter = filter_names_coding_flex(
                        state.enabled_tools_filter
                        if isinstance(state.enabled_tools_filter, list)
                        else None
                    )
                    # Escalate nudge text every 3 pure-explore rounds
                    messages.append(
                        {
                            "role": "system",
                            "content": write_intent_nudge_text(soft=False),
                        }
                    )
                    logger.info(
                        "write_intent CODING_FLEX tools %s→%s streak=%s session=%s",
                        before_n,
                        len(state.tools or []),
                        state.explore_only_streak,
                        session_id,
                    )
            # Already focused: keep coding-flex applied every round
            if getattr(state, "write_intent_hard_nudge", False):
                state.force_final_no_tools = False
                state.tools = filter_tools_coding_flex(state.tools)
                state.enabled_tools_filter = filter_names_coding_flex(
                    state.enabled_tools_filter
                    if isinstance(state.enabled_tools_filter, list)
                    else None
                )
    except Exception as _wi_e:
        logger.debug("write_intent guard skip: %s", _wi_e)

    # ── Progress guard (cargo-fix → write, pure-read, deliver shell lock) ──
    try:
        from backend.agent.decisive import tool_names_from_calls as _tn_pg
        from backend.agent.progress_guard import (
            READ_ONLY_TOOLS as _PG_READ,
        )
        from backend.agent.progress_guard import (
            SCAN_TOOLS as _PG_SCAN,
        )
        from backend.agent.progress_guard import (
            cargo_fix_nudge as _cf_nudge,
        )
        from backend.agent.progress_guard import (
            command_from_tool as _cmd_from,
        )
        from backend.agent.progress_guard import (
            deliver_mode_nudge as _dm_nudge,
        )
        from backend.agent.progress_guard import (
            extract_result_handle as _ext_h,
        )
        from backend.agent.progress_guard import (
            extract_tool_args as _ext_args,
        )
        from backend.agent.progress_guard import (
            filter_names_deliver_only as _fn_del,
        )
        from backend.agent.progress_guard import (
            filter_tools_deliver_only as _ft_del,
        )
        from backend.agent.progress_guard import (
            ignored_nudge_action as _nudge_act,
        )
        from backend.agent.progress_guard import (
            is_cargo_compile_failure as _is_cf,
        )
        from backend.agent.progress_guard import (
            is_cargo_verify_command as _is_cvc,
        )
        from backend.agent.progress_guard import (
            is_file_read_cap_message as _is_fr_cap,
        )
        from backend.agent.progress_guard import (
            is_progress_write as _is_pw,
        )
        from backend.agent.progress_guard import (
            is_shell_probe_command as _is_sp,
        )
        from backend.agent.progress_guard import (
            manage_goal_cadence_nudge as _mg_nudge,
        )
        from backend.agent.progress_guard import (
            no_write_progress_nudge as _nw_nudge,
        )
        from backend.agent.progress_guard import (
            parse_cargo_error_paths as _parse_cpaths,
        )
        from backend.agent.progress_guard import (
            pure_read_nudge as _pr_nudge,
        )
        from backend.agent.progress_guard import (
            result_load_nudge as _rl_nudge,
        )
        from backend.core.config import settings as _st_pg

        _pnames = _tn_pg(tool_calls)
        _had_manage = "manage_goal" in _pnames
        _had_result_load = "result_load" in _pnames

        # Real source writes only (not _snap dumps)
        _wrote_pg = False
        for _tc in tool_calls or []:
            _n = str(getattr(_tc, "name", "") or "")
            if _is_pw(_n, _ext_args(_tc)):
                _wrote_pg = True
                break

        _only_read_pg = bool(_pnames) and all(
            n in _PG_READ or n in _PG_SCAN for n in _pnames
        )
        # Shell probes / dump commands count as empty-progress "read"
        _probe_cmds = 0
        _cargo_cmds = 0
        _junk_writes = 0
        for _tc in tool_calls or []:
            _nm = str(getattr(_tc, "name", "") or "")
            if _nm in ("file_write", "edit", "apply_patch"):
                if not _is_pw(_nm, _ext_args(_tc)):
                    _junk_writes += 1
            if _nm != "command":
                continue
            _cmd = _cmd_from(_tc)
            if _is_cvc(_cmd):
                _cargo_cmds += 1
            elif _is_sp(_cmd):
                _probe_cmds += 1

        if _wrote_pg:
            state.pure_read_streak = 0
            state.rounds_since_write = 0
            state.cargo_fix_streak = 0
            state.must_write_before_cargo = False
            state.cargo_error_paths = ""
        else:
            state.rounds_since_write = int(
                getattr(state, "rounds_since_write", 0) or 0
            ) + 1
            # junk write / pure-read / shell probe = empty progress
            if _only_read_pg or _probe_cmds > 0 or _junk_writes > 0:
                state.pure_read_streak = int(
                    getattr(state, "pure_read_streak", 0) or 0
                ) + 1

        if _had_manage:
            state.rounds_since_manage_goal = 0
        else:
            state.rounds_since_manage_goal = int(
                getattr(state, "rounds_since_manage_goal", 0) or 0
            ) + 1

        # Tool result blob (this round) — include process poll tails
        _blob_pg = ""
        for _m in messages[-max(24, len(tool_calls) * 3 + 4) :]:
            if isinstance(_m, dict) and _m.get("role") == "tool":
                _blob_pg += "\n" + str(_m.get("content") or "")[:6000]

        try:
            from backend.agent.progress_guard import soft_open_mode as _so_fr2

            _allow_fr_del = not _so_fr2()
        except Exception:
            _allow_fr_del = True
        if (
            _allow_fr_del
            and _is_fr_cap(_blob_pg)
            and bool(getattr(_st_pg, "agent_file_read_cap_deliver_mode", False))
        ):
            state.deliver_mode = True

        # Cargo compile failure → force write
        # CRITICAL: auto-bg cargo surfaces via process poll, not command tool
        try:
            from backend.agent.progress_guard import (
                is_bg_cargo_compile_failure as _is_bg_cf,
            )
            from backend.agent.progress_guard import (
                is_bg_cargo_success as _is_bg_ok,
            )
        except Exception:
            _is_bg_cf = lambda _t: False  # type: ignore
            _is_bg_ok = lambda _t: False  # type: ignore

        _process_cargo_fail = "process" in _pnames and _is_bg_cf(_blob_pg)
        # Suppress re-arm if this pid already notified via bg_complete inject
        if _process_cargo_fail:
            try:
                from backend.agent.progress_guard import (
                    mark_bg_notified as _mbn,
                )
                from backend.agent.progress_guard import (
                    parse_bg_process_id as _pbid,
                )

                _pid_poll = _pbid(_blob_pg)
                if _pid_poll and not _mbn(str(session_id), _pid_poll):
                    # already notified — keep must_write if set, skip streak++/nudge
                    _process_cargo_fail = False
                    if not getattr(state, "must_write_before_cargo", False):
                        # ensure gate still on if inject already armed state
                        state.must_write_before_cargo = True
                        state.deliver_mode = True
            except Exception:
                pass
        _process_cargo_ok = "process" in _pnames and _is_bg_ok(_blob_pg)
        # is_cargo_compile_failure is narrowed to compile_source (E0xxx / could not compile)
        _cargo_fail = (_cargo_cmds > 0 and _is_cf(_blob_pg)) or _process_cargo_fail
        _cargo_cls = ""
        try:
            from backend.agent.progress_facade import classify_cargo_error as _cls_cargo

            if _cargo_cmds > 0 or "process" in _pnames:
                _cargo_cls = _cls_cargo(_blob_pg)
        except Exception:
            _cargo_cls = "compile_source" if _cargo_fail else ""

        if _cargo_fail:
            state.cargo_error_class = _cargo_cls or "compile_source"
            state.cargo_fix_streak = int(
                getattr(state, "cargo_fix_streak", 0) or 0
            ) + 1
            state.must_write_before_cargo = True
            try:
                from backend.agent.progress_guard import soft_open_mode as _so_carm

                if not _so_carm():
                    state.deliver_mode = True
            except Exception:
                state.deliver_mode = True
            _paths = _parse_cpaths(_blob_pg)
            if _paths:
                state.cargo_error_paths = ",".join(_paths[:5])
            # Dedup with bg_complete inject / later poll
            try:
                from backend.agent.progress_guard import (
                    mark_bg_notified as _mbn2,
                )
                from backend.agent.progress_guard import (
                    parse_bg_process_id as _pbid2,
                )

                _pid_arm = _pbid2(_blob_pg)
                if _pid_arm:
                    _mbn2(str(session_id), _pid_arm)
            except Exception:
                pass
            messages.append(
                {
                    "role": "system",
                    "content": _cf_nudge(
                        (state.cargo_error_paths or "").split(",")
                        if state.cargo_error_paths
                        else _paths
                    ),
                }
            )
            logger.warning(
                "cargo_fix arm streak=%s bg=%s paths=%s session=%s",
                state.cargo_fix_streak,
                _process_cargo_fail,
                (state.cargo_error_paths or "")[:80],
                session_id,
            )
        elif _cargo_cls == "path_env" and (_cargo_cmds > 0 or "process" in _pnames):
            # Wrong cwd / missing manifest: soft redirect, NO must_write gate
            state.cargo_error_class = "path_env"
            try:
                from backend.agent.progress_guard import path_env_cargo_nudge as _pen

                messages.append({"role": "system", "content": _pen()})
            except Exception:
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "[Cargo path/workspace] Run cargo check at the project "
                            "anchor cwd; do not randomly rewrite .rs just to pass a gate."
                        ),
                    }
                )
            logger.info("cargo path_env (no must_write) session=%s", session_id)
        elif (_cargo_cmds > 0 or _process_cargo_ok) and not _is_cf(_blob_pg):
            if re.search(r"(?i)Finished|exit=0|\[Exit 0|status=done\s+exit=0", _blob_pg):
                state.cargo_fix_streak = 0
                state.must_write_before_cargo = False
                state.cargo_error_class = ""

        # result_load thrash on same handle
        _hid = _ext_h(_blob_pg)
        if _had_result_load and _hid:
            if _hid == (getattr(state, "last_result_handle", "") or ""):
                state.result_load_same_streak = int(
                    getattr(state, "result_load_same_streak", 0) or 0
                ) + 1
            else:
                state.result_load_same_streak = 1
                state.last_result_handle = _hid
            _rl_cap = max(
                2, int(getattr(_st_pg, "agent_result_load_thrash_after", 3) or 3)
            )
            if state.result_load_same_streak >= _rl_cap:
                messages.append(
                    {
                        "role": "system",
                        "content": _rl_nudge(_hid)
                        + " 立刻 file_write/edit 修编译错误，停止分页。",
                    }
                )
        elif not _had_result_load:
            state.result_load_same_streak = 0

        # Pure-read / probe imbalance
        _pr_after = max(
            1, int(getattr(_st_pg, "agent_pure_read_nudge_after", 2) or 2)
        )
        _pr_del = max(
            _pr_after + 1,
            int(getattr(_st_pg, "agent_pure_read_deliver_after", 4) or 4),
        )
        if (
            state.pure_read_streak >= _pr_after
            and not state.force_final_no_tools
            and (
                state.pure_read_streak == _pr_after
                or state.pure_read_streak % 2 == 0
            )
        ):
            messages.append(
                {
                    "role": "system",
                    "content": _pr_nudge(streak=state.pure_read_streak),
                }
            )
        # Soft-open: never arm deliver (was arm→clear dead path). Hard profile only.
        try:
            from backend.agent.progress_guard import soft_open_mode as _so_arm

            _soft_open_now = bool(_so_arm())
        except Exception:
            _soft_open_now = False
        if state.pure_read_streak >= _pr_del and not _soft_open_now:
            try:
                from backend.agent.progress_guard import should_arm_deliver_mode as _sad

                if _sad(str(user_input or ""), reason="pure_read"):
                    state.deliver_mode = True
                else:
                    logger.info(
                        "skip deliver_mode pure_read (review-only) session=%s",
                        session_id,
                    )
            except Exception:
                state.deliver_mode = True

        # Wire dead counter: no real write for N rounds
        _nw = max(2, int(getattr(_st_pg, "agent_no_write_nudge_after", 3) or 3))
        _nw_grace = max(1, int(getattr(_st_pg, "agent_no_write_force_after", 4) or 4))
        _rsw = int(getattr(state, "rounds_since_write", 0) or 0)
        _nw_act = _nudge_act(
            current=_rsw, first_at=_nw, grace=_nw_grace, even_only=True
        )
        if _nw_act != "none" and not state.force_final_no_tools:
            if _nw_act == "force_final":
                state.force_final_no_tools = True
                try:
                    loop.last_exit_reason = "no_write_ignored"
                except Exception:
                    pass
                try:
                    from backend.agent.loop_decision import force_final as _ff_nw

                    _note = _ff_nw("no_write_ignored").as_system_message()
                except Exception:
                    _note = None
                messages.append(
                    _note
                    or {
                        "role": "system",
                        "content": (
                            "[Controller] No file writes after repeated nudges. "
                            "Stop tools and answer the user now."
                        ),
                    }
                )
                logger.info(
                    "no_write force_final rounds=%s session=%s",
                    _rsw,
                    session_id,
                )
            else:
                _arm_nw = True
                try:
                    from backend.agent.progress_guard import (
                        should_arm_deliver_mode as _sad2,
                    )

                    _arm_nw = _sad2(str(user_input or ""), reason="no_write")
                except Exception:
                    _arm_nw = True
                # Soft-open: nudge only — do not arm deliver_mode (no strip)
                if _arm_nw and not _soft_open_now:
                    state.deliver_mode = True
                messages.append(
                    {
                        "role": "system",
                        "content": _nw_nudge(rounds=_rsw),
                    }
                )
                logger.info(
                    "no_write progress nudge rounds=%s deliver=%s soft_open=%s session=%s",
                    _rsw,
                    bool(getattr(state, "deliver_mode", False)),
                    _soft_open_now,
                    session_id,
                )

        # manage_goal cadence — only in goal_mode (casual Q&A must not be nagged)
        _mg_every = max(
            3, int(getattr(_st_pg, "agent_manage_goal_cadence_rounds", 5) or 5)
        )
        if (
            goal_mode
            and state.rounds_since_manage_goal >= _mg_every
            and not state.force_final_no_tools
        ):
            messages.append({"role": "system", "content": _mg_nudge()})
            state.rounds_since_manage_goal = 0
            logger.info("manage_goal cadence nudge session=%s", session_id)

        # Apply deliver-only tool strip — hard profile only (soft-open never arms)
        try:
            from backend.agent.progress_guard import soft_open_mode as _so_strip

            _hard_strip = not _so_strip()
        except Exception:
            _hard_strip = True
        if _hard_strip and (
            getattr(state, "deliver_mode", False)
            or getattr(state, "must_write_before_cargo", False)
        ):
            # Ignored-nudge force_final must not be undone by deliver strip.
            if getattr(loop, "last_exit_reason", "") not in (
                "no_write_ignored",
                "converge_ignored",
            ):
                state.force_final_no_tools = False
            state.deliver_mode = True
            state.tools = _ft_del(state.tools)
            state.enabled_tools_filter = _fn_del(
                state.enabled_tools_filter
                if isinstance(state.enabled_tools_filter, list)
                else None
            )
            if state.pure_read_streak == _pr_del:
                messages.append({"role": "system", "content": _dm_nudge()})
    except Exception as _pg_e:
        logger.debug("progress_guard skip: %s", _pg_e)

    # Soft-open: high-step converge reminder only (no ban)
    try:
        from backend.agent.progress_guard import (
            converge_nudge_text as _cnv,
        )
        from backend.agent.progress_guard import (
            ignored_nudge_action as _cnv_act,
        )
        from backend.agent.progress_guard import (
            soft_open_mode as _so_cnv,
        )
        from backend.core.config import settings as _st_cnv

        if not state.force_final_no_tools:
            _after = max(6, int(getattr(_st_cnv, "agent_converge_nudge_after", 16) or 16))
            _every = max(4, int(getattr(_st_cnv, "agent_converge_nudge_every", 10) or 10))
            _grace = max(1, int(getattr(_st_cnv, "agent_converge_force_after", 2) or 2))
            # tool_rounds is completed count from prior rounds (incremented at end)
            _tr = int(getattr(state, "tool_rounds", 0) or 0) + 1
            _act = _cnv_act(
                current=_tr, first_at=_after, grace=_grace, every=_every
            )
            if _act == "nudge" and _so_cnv():
                messages.append(
                    {
                        "role": "system",
                        "content": _cnv(tool_rounds=_tr),
                    }
                )
                logger.info(
                    "converge soft nudge rounds=%s session=%s",
                    _tr,
                    session_id,
                )
            elif _act == "force_final":
                state.force_final_no_tools = True
                try:
                    loop.last_exit_reason = "converge_ignored"
                except Exception:
                    pass
                try:
                    from backend.agent.loop_decision import force_final as _ff_c

                    _note = _ff_c("converge_ignored").as_system_message()
                except Exception:
                    _note = None
                messages.append(
                    _note
                    or {
                        "role": "system",
                        "content": (
                            "[Controller] Converge nudge was ignored. "
                            "Stop tools and answer the user now."
                        ),
                    }
                )
                logger.info(
                    "converge force_final rounds=%s session=%s",
                    _tr,
                    session_id,
                )
    except Exception as _cnv_e:
        logger.debug("converge nudge skip: %s", _cnv_e)

    # 果断化：单轮仅 1 个只读工具 → 提示下轮并行/开改
    # Skip timid after timeouts OR write-intent — timid pushes more tools and worsens thrash.
    try:
        from backend.agent.decisive import (
            batch_read_nudge_text,
            batch_write_nudge_text,
            is_timid_read_round,
            is_timid_write_round,
            tool_names_from_calls,
        )

        _tnames = tool_names_from_calls(tool_calls)
        # Alternating single-tool rounds (write script / run / write / run) already
        # look like "复读"; timid nudges push MORE single tools and make it worse.
        _alt = int(getattr(state, "alternate_thrash_streak", 0) or 0)
        _skip_timid = (
            int(getattr(state, "timeout_fail_streak", 0) or 0) > 0
            or _write_intent_active
            or _alt >= 2
            or (
                len(_tnames) == 1
                and _tnames[0] in ("process", "command", "file_write")
                and _alt >= 1
            )
        )
        if (
            is_timid_read_round(_tnames, tool_calls)
            and not state.force_final_no_tools
            and not _skip_timid
        ):
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
            # 连续 4 轮单点窥探：硬收束（soft-open 下仅软提醒）
            if state.timid_read_streak >= 4:
                try:
                    from backend.agent.progress_guard import soft_open_mode as _so_tm
                    from backend.core.config import settings as _st_tm

                    _hard_tm = (
                        not _so_tm()
                        and bool(getattr(_st_tm, "agent_timid_force_final", True))
                    )
                except Exception:
                    _hard_tm = True
                if _hard_tm:
                    state.force_final_no_tools = True
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "[Read thrash] Many single-read turns. "
                                "No tools this turn: short conclusion/gaps in the user's language."
                            ),
                        }
                    )
                else:
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "[Read-heavy] Many single-read turns. "
                                "Prefer batched reads or edit/file_write; tools still allowed."
                            ),
                        }
                    )
                logger.warning(
                    "timid read %s streak=%s session=%s",
                    "force_final" if state.force_final_no_tools else "soft",
                    state.timid_read_streak,
                    session_id,
                )
        elif (
            is_timid_write_round(_tnames)
            and not state.force_final_no_tools
            and not _skip_timid
        ):
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
        elif _skip_timid:
            logger.info(
                "timid nudge suppressed (timeout_streak=%s write_intent=%s) session=%s",
                getattr(state, "timeout_fail_streak", 0),
                _write_intent_active,
                session_id,
            )
        else:
            state.timid_read_streak = 0
            state.timid_write_streak = 0
    except Exception as _dec_e:
        logger.debug("decisive nudge skipped: %s", _dec_e)

    # 工具空转：连续相同指纹 → 强制收束（禁止再工具）
    # 编制/result_load 主导轮用 family 指纹（参数不同也会收），阈值略宽以免误杀
    try:
        from backend.agent.decisive import (
            family_bucket,
            thrash_fingerprint,
            thrash_force_final_text,
        )
        from backend.agent.decisive import tool_names_from_calls as _tnfc2

        fam = family_bucket(tool_calls)
        fp = thrash_fingerprint(tool_calls, use_family_bucket=True)
        _tnames2 = _tnfc2(tool_calls)
        _only_process = bool(_tnames2) and all(n == "process" for n in _tnames2)
        # process poll of still-running bg job is not thrash — need more polls
        _bg_running = False
        if _only_process:
            try:
                for m in reversed(messages[-6:]):
                    if m.get("role") != "tool":
                        continue
                    body = str(m.get("content") or "")
                    if "status=running" in body or "status=running" in body.lower():
                        _bg_running = True
                        break
                    if "[bg " in body and "running" in body.lower():
                        _bg_running = True
                        break
            except Exception:
                pass
        if fam == "mcp_ops":
            force_after = max(
                2,
                int(getattr(settings, "agent_mcp_ops_thrash_force_after", 5) or 5),
            )
        elif fam:
            force_after = max(
                2,
                int(getattr(settings, "agent_orch_thrash_force_final", 3) or 3),
            )
        elif _only_process and _bg_running:
            # cargo test can run many minutes — never thrash-force on poll-while-running
            force_after = max(
                12, int(getattr(settings, "agent_process_poll_thrash", 16) or 16)
            )
        else:
            # Default 3: large docs need 2+ offset file_read rounds; 2 was false thrash.
            force_after = max(
                2, int(getattr(settings, "agent_tool_thrash_force_final", 3) or 3)
            )
        prev_fp = (state.last_tool_fingerprint or "").strip()
        if _only_process and _bg_running:
            # Legitimate wait — do not accumulate thrash_streak toward force_final
            state.thrash_streak = 0
            state.last_tool_fingerprint = fp
        elif prev_fp and fp == prev_fp:
            state.thrash_streak = int(state.thrash_streak or 0) + 1
            state.last_tool_fingerprint = fp
        else:
            state.thrash_streak = 0
            state.last_tool_fingerprint = fp

        # ABAB alternate thrash: file_write helper ↔ command (visible 复读)
        _sig = "|".join(sorted({str(n) for n in _tnames2 if n})) if _tnames2 else ""
        _prev_sig = (getattr(state, "last_tool_name_sig", "") or "").strip()
        if (
            _sig
            and _prev_sig
            and _sig != _prev_sig
            and len(_tnames2) == 1
            and _sig in ("command", "file_write", "python", "process")
            and _prev_sig in ("command", "file_write", "python", "process")
        ):
            state.alternate_thrash_streak = int(
                getattr(state, "alternate_thrash_streak", 0) or 0
            ) + 1
        elif _sig and _sig == _prev_sig:
            # same single-tool family — leave alternate alone; exact thrash handles it
            pass
        else:
            state.alternate_thrash_streak = 0
        if _sig:
            state.last_tool_name_sig = _sig

        _alt_cap = max(
            4, int(getattr(settings, "agent_alternate_thrash_force_final", 6) or 6)
        )
        try:
            from backend.agent.progress_guard import soft_open_mode as _so_th

            _hard_thrash = not _so_th() and bool(
                getattr(settings, "agent_thrash_force_final", True)
            )
        except Exception:
            _hard_thrash = bool(getattr(settings, "agent_thrash_force_final", True))
        # P0-4：仅配置微 loop / 显式 override 在 soft_open 下硬停
        try:
            _micro = bool(getattr(loop, "_config_micro_loop", None))
            _ov = getattr(loop, "_thrash_force_final_override", None) is True
            if _ov or _micro:
                if fam == "mcp_ops" or fam == "" or _ov:
                    _hard_thrash = True
        except Exception:
            pass
        if (
            int(getattr(state, "alternate_thrash_streak", 0) or 0) >= _alt_cap
            and not state.force_final_no_tools
        ):
            if _hard_thrash:
                state.force_final_no_tools = True
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "[Alternate thrash] Many write-script → command → rewrite loops. "
                            "No tools this turn: short blocker + paths already on disk; "
                            "no more _cargo_*.py / _diag_*.py or long lists."
                        ),
                    }
                )
            else:
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "[Pace] Many write-script → run-command alternations. "
                            "Prefer editing product sources and verifying; tools still allowed."
                        ),
                    }
                )
            logger.warning(
                "alternate thrash %s streak=%s sig=%s→%s session=%s",
                "force_final" if state.force_final_no_tools else "soft",
                state.alternate_thrash_streak,
                _prev_sig,
                _sig,
                session_id,
            )

        try:
            _cmd_n = sum(1 for x in _tnames2 if x == "command")
            if (
                _cmd_n * 2 >= len(_tnames2)
                and _cmd_n > 0
                and bool(getattr(loop, "_config_micro_loop", None))
            ):
                _cfa = max(
                    2,
                    int(getattr(settings, "agent_command_family_force_after", 5) or 5),
                )
                force_after = min(force_after, _cfa)
        except Exception:
            pass

        # force_after=2 → streak>=1 即第 2 轮相同；force_after=3 → streak>=2 即第 3 轮
        if (
            prev_fp
            and fp == prev_fp
            and state.thrash_streak >= max(1, force_after - 1)
            and not state.force_final_no_tools
        ):
            # cargo/shell family → deliver; must_write ONLY if real compile_source
            # soft-open: soft nudge only, never strip tools / hard stop
            if fam in ("cargo_verify", "shell_probe"):
                if _hard_thrash:
                    state.deliver_mode = True
                    _mw_ok = False
                    try:
                        from backend.core.config import settings as _st_ft

                        _gate = bool(
                            getattr(
                                _st_ft,
                                "agent_family_thrash_must_write_only_source",
                                True,
                            )
                        )
                    except Exception:
                        _gate = True
                    if fam == "cargo_verify":
                        if _gate:
                            _mw_ok = (
                                str(getattr(state, "cargo_error_class", "") or "")
                                == "compile_source"
                            )
                        else:
                            _mw_ok = True
                    state.must_write_before_cargo = _mw_ok
                    state.force_final_no_tools = False
                    try:
                        from backend.agent.progress_guard import (
                            filter_names_deliver_only as _fnd2,
                        )
                        from backend.agent.progress_guard import (
                            filter_tools_deliver_only as _ftd2,
                        )

                        state.tools = _ftd2(state.tools)
                        if isinstance(state.enabled_tools_filter, list):
                            state.enabled_tools_filter = _fnd2(
                                state.enabled_tools_filter
                            )
                    except Exception:
                        pass
                messages.append(
                    {
                        "role": "system",
                        "content": thrash_force_final_text(family=fam)
                        if _hard_thrash
                        else (
                            f"[Pace] Repeated {fam} rounds. "
                            "Prefer editing sources before another check; any tool still OK."
                        ),
                    }
                )
                logger.warning(
                    "family thrash fam=%s hard=%s streak=%s session=%s",
                    fam,
                    _hard_thrash,
                    state.thrash_streak,
                    session_id,
                )
            elif fam == "process_poll":
                # Hard throttle is in process_registry; soft nudge only
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "[process thrash] Wait for [bg_complete]; do not spam poll.\n"
                            "NEXT:\n"
                            "1) file_write/edit to progress\n"
                            "2) wait for bg_complete\n"
                            "3) avoid Still-running empty polls"
                        ),
                    }
                )
                logger.info(
                    "process_poll thrash soft session=%s streak=%s",
                    session_id,
                    state.thrash_streak,
                )
            # Write-intent: thrash → soft coding focus, NEVER strip reads/command.
            # Hard write-only lock caused 复读卡死; keep full coding toolkit.
            elif _write_intent_active:
                try:
                    from backend.agent.write_intent import (
                        filter_names_coding_flex as _fnw,
                    )
                    from backend.agent.write_intent import (
                        filter_tools_coding_flex as _ftw,
                    )
                    from backend.agent.write_intent import (
                        write_intent_nudge_text as _win,
                    )

                    state.force_final_no_tools = False
                    state.write_intent_hard_nudge = True
                    state.tools = _ftw(state.tools)
                    state.enabled_tools_filter = _fnw(
                        state.enabled_tools_filter
                        if isinstance(state.enabled_tools_filter, list)
                        else None
                    )
                    messages.append(
                        {
                            "role": "system",
                            "content": _win(soft=False),
                        }
                    )
                except Exception:
                    pass
                logger.warning(
                    "tool thrash→coding_flex fp=%s streak=%s session=%s",
                    fp,
                    state.thrash_streak,
                    session_id,
                )
            elif _only_process and _bg_running:
                # Never force_final on poll-while-running (cargo test can be long).
                state.force_final_no_tools = False
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "[Background still running] [bg_complete] will inject when done.\n"
                            "NEXT:\n"
                            "1) Wait for bg_complete (preferred) — do not spam poll\n"
                            "2) Or poll once every ≥12s if you must\n"
                            "3) file_write/edit / manage_goal if you can progress in parallel\n"
                            "This is NOT a doom loop."
                        ),
                    }
                )
                logger.info(
                    "process poll thrash deferred (bg running) streak=%s session=%s",
                    state.thrash_streak,
                    session_id,
                )
            else:
                if _hard_thrash:
                    state.force_final_no_tools = True
                    try:
                        if not getattr(loop, "last_exit_reason", None) or loop.last_exit_reason in (
                            "",
                            "completed",
                            None,
                        ):
                            loop.last_exit_reason = "thrash"
                            loop.last_exit_detail = {
                                "code": "thrash",
                                "fingerprint": str(fp)[:120],
                                "streak": int(state.thrash_streak or 0),
                            }
                    except Exception:
                        pass
                    messages.append(
                        {
                            "role": "system",
                            "content": thrash_force_final_text(
                                family=fp if fam else ""
                            ),
                        }
                    )
                else:
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "[Pace] Tool pattern is repeating. "
                                "Change approach (read different files / edit / verify). "
                                "Tools still allowed — not a ban."
                            ),
                        }
                    )
                logger.warning(
                    "tool thrash %s fp=%s streak=%s fam=%s session=%s",
                    "force_final" if state.force_final_no_tools else "soft",
                    fp,
                    state.thrash_streak,
                    fam or "-",
                    session_id,
                )
    except Exception as _th_e:
        logger.debug("tool thrash guard skipped: %s", _th_e)

    # audit-fix(#5)：同一工具名连续失败 N 次（不论参数）→ 熔断。
    # 复用上方 thrash 模式：注入 system 提示 + force_final + 计入 thrash 指纹。
    try:
        _fail_cap = max(
            2, int(getattr(settings, "agent_same_tool_fail_breaker", 4) or 4)
        )
        if (
            state.same_tool_fail_streak >= _fail_cap
            and state.last_failed_tool
            and not state.force_final_no_tools
        ):
            # Cargo compile fails → force WRITE (not text force_final)
            _is_cargo_tool = str(state.last_failed_tool) in ("command", "process")
            _blob_fail = ""
            for _m in messages[-12:]:
                if isinstance(_m, dict) and _m.get("role") == "tool":
                    _blob_fail += str(_m.get("content") or "")[:2000]
            _armed_cargo_fix = False
            try:
                from backend.agent.progress_guard import (
                    cargo_fix_nudge as _cfn,
                )
                from backend.agent.progress_guard import (
                    is_cargo_compile_failure as _icf,
                )
                from backend.agent.progress_guard import (
                    parse_cargo_error_paths as _pcp,
                )

                if _is_cargo_tool and _icf(_blob_fail):
                    try:
                        from backend.agent.progress_guard import (
                            soft_open_mode as _so_cf,
                        )

                        _soft_cf = _so_cf()
                    except Exception:
                        _soft_cf = False
                    if not _soft_cf:
                        state.must_write_before_cargo = True
                        state.deliver_mode = True
                    state.force_final_no_tools = False
                    _ps = _pcp(_blob_fail)
                    if _ps:
                        state.cargo_error_paths = ",".join(_ps[:5])
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                _cfn(_ps)
                                + (
                                    f"\n（command 已失败 {state.same_tool_fail_streak} 次 → 建议改代码）"
                                    if _soft_cf
                                    else f"\n（command 已失败 {state.same_tool_fail_streak} 次 → 强制改代码）"
                                )
                            ),
                        }
                    )
                    logger.warning(
                        "same-tool fail→cargo_fix streak=%s session=%s",
                        state.same_tool_fail_streak,
                        session_id,
                    )
                    _armed_cargo_fix = True
            except Exception as _cf_e:
                logger.debug("cargo_fix arm from fail breaker: %s", _cf_e)

            if not _armed_cargo_fix:
                state.force_final_no_tools = True
                turn_retry.note_and_decide(
                    RetryKind.THRASH,
                    detail=f"same_tool_fail:{state.last_failed_tool}x{state.same_tool_fail_streak}",
                )
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            f"[Tool breaker] {state.last_failed_tool} failed "
                            f"{state.same_tool_fail_streak} times (any args). "
                            "Change approach or stop retrying that tool; answer from "
                            "existing info in the user's language."
                        ),
                    }
                )
                logger.warning(
                    "same-tool fail breaker: tool=%s streak=%s session=%s",
                    state.last_failed_tool,
                    state.same_tool_fail_streak,
                    session_id,
                )
            state.same_tool_fail_streak = 0
            state.last_failed_tool = ""
    except Exception as _fb_e:
        logger.debug("same-tool fail breaker skipped: %s", _fb_e)

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
            # P0: simple turn must not re-arm crew/cluster/full dispatch tools mid-turn
            if state.simple_turn:
                blocked = {
                    "crew",
                    "cluster",
                    "workforce",
                    "subagent",
                    "delegate",
                    "full",
                    "*",
                    "all",
                    "everything",
                }
                packs = [p for p in packs if p not in blocked]
                if not packs:
                    logger.info(
                        "use_tool_pack: blocked crew/full expand on simple_turn"
                    )
                    continue
            new_filter = merge_tools_with_packs(state.enabled_tools_filter, packs)
            # simple_turn: never accept filter=None (ALL tools)
            if state.simple_turn and new_filter is None:
                logger.info("use_tool_pack: rejected ALL expand on simple_turn")
                continue
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
            # Re-strip dispatch+goal tools if simple_turn (merge may have re-added names)
            if state.simple_turn and state.enabled_tools_filter is not None:
                from backend.agent.simple_intent import SOLO_STRIP_TOOLS

                state.enabled_tools_filter = [
                    n
                    for n in state.enabled_tools_filter
                    if n not in SOLO_STRIP_TOOLS
                ]
            state.tools = await loop._load_tools(
                session_id,
                enabled_skills,
                state.enabled_tools_filter,
                user_input=user_input,
            )
            if state.simple_turn:
                from backend.agent.simple_intent import (
                    filter_dispatch_tools_from_schema,
                )

                state.tools = filter_dispatch_tools_from_schema(
                    state.tools, force=True, strip_goal_tools=True
                )
            # write-intent soft focus: pack expand must not re-open web/crew noise
            if getattr(state, "write_intent_hard_nudge", False):
                from backend.agent.write_intent import (
                    filter_names_coding_flex,
                    filter_tools_coding_flex,
                )

                state.tools = filter_tools_coding_flex(state.tools)
                if isinstance(state.enabled_tools_filter, list):
                    state.enabled_tools_filter = filter_names_coding_flex(
                        state.enabled_tools_filter
                    )
            # deliver mode survives pack expand
            if getattr(state, "deliver_mode", False):
                from backend.agent.progress_guard import (
                    filter_names_deliver_only,
                    filter_tools_deliver_only,
                )

                state.tools = filter_tools_deliver_only(state.tools)
                if isinstance(state.enabled_tools_filter, list):
                    state.enabled_tools_filter = filter_names_deliver_only(
                        state.enabled_tools_filter
                    )
            # K-03：pack 扩容后必须再次按进程能力裁剪（防可见性泄漏）
            try:
                from backend.agent.cap_tools import filter_tools_for_process

                state.tools = filter_tools_for_process(
                    state.tools, getattr(loop, "_kernel_process", None)
                )
            except Exception as _cf:
                logger.debug("cap re-filter after pack expand: %s", _cf)
            # Plan 未批准：扩 pack 后必须再收窄只读 schema（Court 兜底不够防 thrash）
            if getattr(loop, "_plan_mode_active", False):
                try:
                    from backend.agent.plan_intent import filter_tools_for_plan

                    state.tools = filter_tools_for_plan(state.tools)
                except Exception as _pf:
                    logger.debug("plan re-filter after pack expand: %s", _pf)
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
    # 重复工具/doom-loop 熔断（同名同参连续空转；P0.5 优先 Rust policy.doom_record）
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
        _tripped = False
        _kproc = getattr(loop, "_kernel_process", None)
        _kpid = str(getattr(_kproc, "id", "") or "")
        # Exempt: process poll while bg job still running (legitimate wait for
        # cargo test). Counting these as doom_loop caused "finished but stuck"
        # after cargo check passed and test was still compiling.
        _process_wait_exempt = False
        try:
            _only_proc = bool(_calls) and all(
                (n or "") == "process" for n, _ in _calls
            )
            if _only_proc:
                for m in reversed(messages[-10:]):
                    if not isinstance(m, dict) or m.get("role") != "tool":
                        continue
                    body = str(m.get("content") or "")
                    if (
                        "status=running" in body
                        or "still running" in body.lower()
                        or "[Poll throttle]" in body
                        or "[Blocked] process poll" in body
                    ):
                        _process_wait_exempt = True
                        break
                    # bg already done this round — do not exempt (allow normal thrash)
                    if "status=done" in body and "[bg " in body:
                        break
        except Exception:
            _process_wait_exempt = False
        if _process_wait_exempt:
            _doom_on = False
            logger.info(
                "doom_loop exempt process-poll wait session=%s",
                session_id,
            )
            # Soft steer once: do real work or wait for bg_complete
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "[Background wait] Job still running — not a doom loop.\n"
                        "Prefer: wait for [bg_complete] auto-inject, or do "
                        "file_write/edit/manage_goal. Poll at most every ≥8s; "
                        "do not spam process poll."
                    ),
                }
            )
        if _doom_on and _kpid:
            try:
                from backend.kernel import get_kernel

                _kk = get_kernel()
                for _n, _a in _calls:
                    # Skip process-poll records while waiting (kernel doom counter)
                    if (_n or "") == "process":
                        _aa = _a if isinstance(_a, dict) else {}
                        _act = str(
                            (_aa.get("action") if isinstance(_aa, dict) else "")
                            or "poll"
                        ).lower()
                        if _act in ("poll", "status", "log", ""):
                            continue
                    _args = _a if isinstance(_a, dict) else {"_raw": str(_a or "")}
                    if hasattr(_kk, "doom_record"):
                        _dr = _kk.doom_record(_kpid, _n or "tool", _args)
                    elif hasattr(_kk, "_call"):
                        # audit-fix(#10)：async 上下文改 _acall，避免阻塞事件循环
                        _dr = await _kk._acall(
                            "doom_record",
                            {
                                "process_id": _kpid,
                                "tool": _n or "tool",
                                "args": _args,
                            },
                        )
                    else:
                        _dr = None
                    if isinstance(_dr, dict) and _dr.get("status") == "doom_loop":
                        _tripped = True
                        break
            except Exception:
                _tripped = False
        if not _tripped and _doom_on:
            # Filter process-poll calls out of local ToolRepeatGuard while waiting
            _calls_for_doom = _calls
            _sigs_for_doom = _sigs
            if _process_wait_exempt:
                _calls_for_doom = []
                _sigs_for_doom = []
            else:
                # Also drop process|poll from guard when result body is running
                # (observe happens after execute — check tool messages)
                pass
            _tripped = (
                tool_repeat_guard.observe_calls(_calls_for_doom)
                if hasattr(tool_repeat_guard, "observe_calls")
                else tool_repeat_guard.observe(_sigs_for_doom)
            ) if _calls_for_doom or _sigs_for_doom else False
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
            # P0.5 R4：结构化 doom 文案 + 恢复入口
            try:
                from backend.agent.exit_reasons import describe_exit_reason

                _dx = describe_exit_reason("doom_loop")
                loop.last_exit_reason = "doom_loop"
                loop.last_exit_detail = {
                    **_dx,
                    "process_id": _kpid or None,
                    "signatures": _sigs[:8],
                }
                _status = f"{_dx['title']} — {_dx['message'][:80]}"
            except Exception:
                _status = "检测到重复工具调用，已熔断并改为直接作答…"
            await loop._push_status(session_id, "running", _status)
            try:
                from backend.agent.progress_guard import doom_loop_handoff as _dlh

                _doom_msg = _dlh(
                    deliver_mode=bool(getattr(state, "deliver_mode", False)),
                    must_write=bool(getattr(state, "must_write_before_cargo", False)),
                    cargo_paths=str(getattr(state, "cargo_error_paths", "") or ""),
                    cargo_class=str(getattr(state, "cargo_error_class", "") or ""),
                    last_tools=",".join(
                        str(getattr(tc, "name", "") or "") for tc in (tool_calls or [])[:6]
                    ),
                )
            except Exception:
                _doom_msg = (
                    "[Tool thrash trip] Same tool+args repeated; tools stopped. "
                    "Answer from existing results in the user's language; "
                    "next: change parameters/tools."
                )
            messages.append(
                {
                    "role": "system",
                    "content": _doom_msg,
                }
            )
            state.force_final_no_tools = True
            state.suppress_content_stream = False
    except Exception as _thrash_e:
        logger.debug("tool thrash guard skipped: %s", _thrash_e)
    # 仅「多 agent 编制」场景标记 multi_source_pending。
    # 旧逻辑 last_tool_count>=2 会让单 agent 多工具（读表/写报告）也被二次合并压成纯文字。
    try:
        from backend.agent.decisive import is_orchestration_tool, tool_names_from_calls

        _names = tool_names_from_calls(tool_calls)
        _orch = any(is_orchestration_tool(n) for n in _names)
        if _orch:
            state.multi_source_pending = True
            # 不 suppress_content_stream：流式保留表格/表单，收尾再决定是否轻量整理
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "[Multi-agent formatting] Crew/workers involved this turn. "
                        "Final answer in Markdown — keep tables (|), headings, lists, form fields.\n"
                        "1) Section per worker or one merged table (append rows, do not drop columns).\n"
                        "2) Do not collapse into a 2–3 paragraph prose-only summary.\n"
                        "3) Deduplicate lightly; keep details and numbers.\n"
                        "4) Do not dump tool JSON/raw logs.\n"
                        "Call more tools if needed; otherwise emit the formatted final answer "
                        "in the user's language."
                    ),
                }
            )
    except Exception as _ms_e:
        logger.debug("multi-source pending mark skipped: %s", _ms_e)
    # 工具轮后压缩：默认 L1/L3 micro；消息暴涨时偶发 L5（防 400 条历史空转）
    try:
        from backend.agent.context_compress import compress_history_if_needed
        from backend.agent.context_engine import get_context_engine

        eng = get_context_engine(session_id)
        do_l1 = (l1_every > 0 and state.tool_rounds % l1_every == 0) or eng.should_compress_preflight(messages)
        if do_l1 and hasattr(eng, "_l1_budget"):
            state.messages, _n = eng._l1_budget(messages)  # type: ignore[attr-defined]
            messages = state.messages
        soft_n = int(getattr(settings, "context_max_messages_soft", 40) or 40)
        hard_n = int(getattr(settings, "context_max_messages_hard", 72) or 72)
        l5_every = int(getattr(settings, "context_midloop_l5_every_rounds", 2) or 2)
        n_msg = len(messages)
        bloat = n_msg >= soft_n
        extreme = n_msg >= hard_n or n_msg >= max(soft_n * 2, soft_n + 20)
        allow_mid_l5 = (
            bloat
            and l5_every > 0
            and state.tool_rounds > 0
            and (
                extreme
                or state.tool_rounds % l5_every == 0
            )
        )
        # audit-fix(#1)：阈值默认引用单点常量（0.55/0.45 → 0.85/0.75）；
        # settings.context_threshold_percent 覆盖机制保留
        from backend.agent.context_engine import (
            COMPRESS_THRESHOLD,
            COMPRESS_THRESHOLD_DEEP,
        )

        thr = float(
            getattr(settings, "context_threshold_percent", COMPRESS_THRESHOLD)
            or COMPRESS_THRESHOLD
        )
        if bloat:
            thr = min(thr, COMPRESS_THRESHOLD_DEEP)
        if extreme:
            thr = min(thr, 0.35)
        need = eng.should_compress_preflight(messages) or bloat
        if need or allow_mid_l5:
            state.messages, mid_meta = await compress_history_if_needed(
                messages,
                session_id=session_id,
                threshold=thr,
                allow_l5=bool(allow_mid_l5),
                micro_only=not allow_mid_l5,
            )
            messages = state.messages
            if mid_meta.get("compressed"):
                await loop._push_status(
                    session_id,
                    "optimizing",
                    f"工具轮后压缩 layers={mid_meta.get('layers')} msgs={len(messages)}",
                )
    except Exception as e:
        logger.debug("mid-loop context pipeline skipped: %s", e)
    if checkpoint_every > 0 and state.tool_rounds % checkpoint_every == 0:
        try:
            from backend.agent.checkpoint import recorder_run_id, save_checkpoint
            from backend.agent.goal_state import get_goal, save_goal_to_db

            await save_checkpoint(
                session_id,
                segment=segment,
                iteration=global_iter + 1,
                mode=mode,
                note=f"tool_round={state.tool_rounds}",
                run_id=recorder_run_id(_rc),
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
