"""
Nexus Agent Loop
极简 Agent 核心循环，自主实现 User -> LLM -> Tool Call -> 执行 -> LLM -> 回复
集成 CtxItem 上下文系统、ContextFlow 记录、Task 进度追踪、Auto Optimize、TTL 清理
支持用户隔离、跨设备同步、消息通知
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from backend.agent.iteration_budget import IterationBudget
from backend.agent.loop_base import AgentLoopBase
from backend.agent.loop_cluster import LoopClusterMixin
from backend.agent.loop_io import LoopIOMixin
from backend.agent.loop_tools import LoopToolsMixin
from backend.agent.robust import (
    ToolRepeatGuard,
)
from backend.agent.turn_retry import TurnRetryState
from backend.core.config import settings
from backend.integrations.registry_tool_executor import RegistryToolExecutor
from backend.integrations.sqlalchemy_message_store import SqlAlchemyMessageStore
from backend.integrations.websocket_event_sink import WebSocketEventSink
from backend.repositories import (
    ContextFlowRepository,
    CtxItemRepository,
    MessageRepository,
    NotificationRepository,
    SessionRepository,
    TaskRepository,
)
from backend.services.llm import LLMServiceFactory

from .context import ContextManager
from .session_lock import (
    acquire_session_lock,
    get_session_lock,
    remove_session_lock,
)
from .tool_errors import sanitize_tool_error, tool_error_next_step

logger = logging.getLogger(__name__)

# Thin re-exports: keep historical ``from backend.agent.loop import _…`` working
_sanitize_tool_error = sanitize_tool_error
_tool_error_next_step = tool_error_next_step
_get_session_lock = get_session_lock
_remove_session_lock = remove_session_lock


class NexusAgentLoop(LoopIOMixin, LoopClusterMixin, LoopToolsMixin, AgentLoopBase):
    """
    Nexus Agent 核心循环

    执行流程：
    1. TTL 清理（session 过期项）
    2. 保存用户消息 + 同步到 Session CtxItem
    3. 获取 Session 配置（行级锁）
    4. 加载历史消息
    5. 组装 messages（CtxItem 5 层上下文 + fallback 四维度配置）
    6. 加载启用的 Skills（JSON Schema + tool-def CtxItem 合并）
    7. Auto Optimize 检查（token 超过阈值时自动裁剪）
    8. 循环：
       a. 调用 LLM（流式）
       b. 解析流式输出，推送给前端
       c. 判断是否有 tool calls
       d. 有 -> 创建 Task -> 执行 Skill -> 更新 Task 进度 -> 结果追加到 messages -> 继续循环
       e. 无 -> 保存最终回复，结束
    9. 保存最终回复 + 同步到 Session CtxItem
    10. 记录 ContextFlow（每轮访问了哪些 scope/key）
    11. 最大迭代次数限制（默认 5），防止死循环
    12. 跨设备同步通知（同一用户的其他设备）
    """

    def __init__(
        self,
        session_repo: SessionRepository,
        message_repo: MessageRepository,
        task_repo: TaskRepository | None = None,
        ctx_item_repo: CtxItemRepository | None = None,
        context_flow_repo: ContextFlowRepository | None = None,
        ws_manager=None,
        agent_name: str = "Tevarn",
        user_id: uuid.UUID | None = None,
        notification_repo: NotificationRepository | None = None,
        progress_sink=None,
    ):
        self.session_repo = session_repo
        self.message_repo = message_repo
        self.task_repo = task_repo
        self.ctx_item_repo = ctx_item_repo
        self.context_flow_repo = context_flow_repo
        self.ws_manager = ws_manager
        self.agent_name = agent_name
        self.user_id = user_id
        self.notification_repo = notification_repo
        # 可选：async (kind: str, text: str) -> None；仅人类可读进度/思考，不含工具细节
        self.progress_sink = progress_sink
        # Batch3a: ports (default adapters; injectable later)
        _store = SqlAlchemyMessageStore(message_repo) if message_repo is not None else None
        _tools = RegistryToolExecutor()
        _sink = WebSocketEventSink(ws_manager=ws_manager, progress_sink=progress_sink)
        AgentLoopBase.__init__(
            self,
            message_store=_store,
            event_sink=_sink,
            tool_executor=_tools,
            agent_name=agent_name,
            user_id=user_id,
        )
        self.context_manager = ContextManager(ctx_item_repo=ctx_item_repo)
        # 长链/编码任务默认允许更多工具轮次；可用 TEVARN_AGENT_MAX_ITERATIONS 覆盖
        self.max_iterations = int(getattr(settings, "agent_max_iterations", 25) or 25)
        # 停止信号
        self._should_stop = False
        self._llm_fail_streak = 0
        # RAG 服务（懒加载）
        self._rag_service = None

    def stop(self) -> None:
        """设置停止信号，Agent 会在下一次检查点时终止"""
        self._should_stop = True
        logger.info("Stop signal set for agent loop")

    def _reset_run_state(self) -> None:
        """Worker 池复用前重置 run 级状态（Alpha Review #2）。

        WorkforceWorker 池让 loop 实例跨工单存活——init 重资源
        （repo 引用、RAG 懒加载缓存、context_manager）复用是收益，
        但 run 级状态泄漏是事故。每 run 前显式归零：
        - _kernel_process/_kernel_process_options：进程归属每工单新建
        - _run_recorder：durable run 记录器每 run 新建
        - _search_fp_counter：重复搜索计数器跨工单累积会误拦截
        - _contract_wl_*：身份能力可能已变更，契约白名单重载
        - _should_stop：上一单的停止信号不能带进下一单
        """
        self._kernel_process = None
        self._kernel_process_options = None
        self._last_kernel_process_id = None
        # 编制字段由 dispatcher 在派工时重设，此处不清 _workforce/_identity_*
        # （worker 池复用同一员工；跨工单身份不变）
        self._run_recorder = None
        self._search_fp_counter = {}
        self._search_total_calls = 0  # P1：跨工单泄漏会误 ban 下一单全部搜索
        self._contract_wl_ready = False
        self._contract_whitelist = None
        self._should_stop = False
        self._llm_fail_streak = 0
        self._reactive_compact_used = False
        self._goal_complete_summary_nudged = False
        self._plan_mode_active = False
        self._headless_run = False
        try:
            from backend.agent.progress_guard import set_soft_open_for_run
            set_soft_open_for_run(None)  # reset each run
        except Exception:
            pass
        self._config_micro_loop = None
        self._thrash_force_final_override = None
        self._pseudo_tool_leak_streak = 0

    # ── Batch3 port helpers（优先 message_store / tool_executor）─────────
    async def _save_message(
        self,
        session_id: uuid.UUID,
        role: str,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
        token_count: int | None = None,
    ):
        # Ephemeral simple-session notes must never land in chat history
        try:
            from backend.agent.simple_intent import is_ephemeral_system_note

            if role == "system" and is_ephemeral_system_note(content):
                return None
        except Exception:
            pass
        store = getattr(self, "message_store", None)
        if store is not None:
            return await store.save_message(
                session_id, role, content, tool_calls=tool_calls, token_count=token_count
            )
        # audit-fix(#8)：原实现 store 为 None 时自递归（无限递归 RecursionError）。
        # 与 loop_base 同口径改 raise；调用点均有 try/except 兼容吞掉。
        raise RuntimeError("no message_store; _save_message requires message_store")

    async def _load_history(
        self,
        session_id: uuid.UUID,
        limit: int = 100,
        offset: int = 0,
    ):
        store = getattr(self, "message_store", None)
        if store is not None:
            return await store.get_history(session_id, limit=limit, offset=offset)
        # audit-fix: 原先自递归导致 RecursionError；与 _save_message 同口径 fail-fast
        raise RuntimeError("no message_store; _load_history requires message_store")

    async def _await_run_gate(
        self,
        kernel: Any,
        process_id: str,
        *,
        priority_class: str = "workforce",
        timeout: float = 300.0,
        session_id: uuid.UUID | None = None,
    ) -> None:
        """跨会话全局 RunGate — 拿到 lease 再继续执行（T2：可配置 fail-closed）。

        session 锁只保证同 session 串行；本门闩保证全局并发上限 + 优先级排队。
        排队时向 session 推 thinking 状态，手机 island 可感知。
        """
        from backend.core.config import settings as _rg_settings

        required = bool(
            getattr(_rg_settings, "agent_kernel_run_gate_required", True)
        )
        _poll = float(getattr(_rg_settings, "agent_run_gate_poll_secs", 0.15) or 0.15)
        _poll = min(max(_poll, 0.05), 1.0)

        async def _ui(detail: str) -> None:
            if session_id is None:
                return
            try:
                await self._push_status(session_id, "thinking", detail)
            except Exception:
                pass

        if not hasattr(kernel, "_call"):
            if required:
                raise RuntimeError(
                    "run_gate required but kernel has no _call (host unavailable)"
                )
            return
        try:
            # audit-fix(#10)：async 上下文改走 _acall，避免阻塞事件循环
            r = await kernel._acall(
                "run_gate_try",
                {
                    "process_id": process_id,
                    "priority_class": priority_class,
                },
            ) or {}
        except Exception as e:
            if required:
                raise RuntimeError(f"run_gate_try failed (required): {e}") from e
            logger.warning("run_gate_try skip (not required): %s", e)
            return
        status = r.get("status")
        if status == "granted":
            try:
                await kernel._acall(
                    "resource_charge",
                    {
                        "process_id": process_id,
                        "kind": "concurrency_slots",
                        "amount": 1,
                    },
                )
            except Exception as _ch:
                # H-05：并发槽扣费失败 = 资源超限，硬拒（禁止吞掉）
                logger.warning(
                    "concurrency_slots charge failed proc=%s: %s",
                    process_id[:8],
                    _ch,
                )
                raise RuntimeError(
                    f"resource_charge concurrency_slots failed: {_ch}"
                ) from _ch
            logger.info(
                "run_gate granted proc=%s class=%s",
                process_id[:8],
                priority_class,
            )
            return
        if status == "rejected":
            raise RuntimeError(
                f"run gate rejected: {r.get('reason') or r.get('code')}"
            )
        # queued — poll + user-visible status
        rid = str(r.get("request_id") or "")
        qlen = r.get("queue_len")
        logger.info(
            "run_gate queued proc=%s request=%s qlen=%s",
            process_id[:8],
            rid[:8],
            qlen,
        )
        await _ui(f"排队等待执行槽（队列 {qlen if qlen is not None else '?'}）…")
        deadline = time.time() + timeout
        last_ui = 0.0
        while time.time() < deadline:
            if getattr(self, "_should_stop", False):
                raise RuntimeError("run gate wait aborted: stop requested")
            now = time.time()
            if now - last_ui >= 2.0:
                left = max(0, int(deadline - now))
                await _ui(f"仍在排队… 约剩余等待上限 {left}s")
                last_ui = now
            await asyncio.sleep(_poll)
            try:
                polled = await kernel._acall("run_gate_poll", {"request_id": rid}) or {}
            except Exception as e:
                logger.debug("run_gate_poll: %s", e)
                continue
            st = polled.get("status")
            if st == "granted":
                try:
                    await kernel._acall(
                        "resource_charge",
                        {
                            "process_id": process_id,
                            "kind": "concurrency_slots",
                            "amount": 1,
                        },
                    )
                except Exception as _ch:
                    logger.warning(
                        "concurrency_slots charge failed after wait proc=%s: %s",
                        process_id[:8],
                        _ch,
                    )
                    raise RuntimeError(
                        f"resource_charge concurrency_slots failed: {_ch}"
                    ) from _ch
                logger.info("run_gate granted after wait proc=%s", process_id[:8])
                await _ui("已获得执行槽，继续…")
                return
            if st == "rejected":
                raise RuntimeError(
                    f"run gate rejected: {polled.get('reason') or polled.get('code')}"
                )
        raise RuntimeError("run gate wait timeout")

    async def _kernel_iteration_gate(
        self, session_id: uuid.UUID, messages: list[dict[str, Any]]
    ) -> str | None:
        """每轮 iteration 的 kernel 仲裁点（搭桥 → 融合）。

        三件事按序：
        1) suspended：进程被挂起 → 阻塞等待恢复（轮询响应 stop），
           被打断返回 "stop" 让 run 退出；
        2) 事前预算检查：预估本次 LLM 调用消耗，剩余预算不足直接中断
           （llm_round 的事后 charge 是兜底，这里是事前刹车——
           防止最后一次调用一次性烧穿预算）；
        3) 调度让出：asyncio.sleep(0)，多 run 并发时的公平性语义点
           （session lock 管同会话互斥，这里管跨 run 的调度节奏）。
        """
        proc = getattr(self, "_kernel_process", None)
        if proc is None:
            return None
        # 1) 挂起等待
        if proc.state == "suspended":
            reason = (proc.meta or {}).get("suspend_reason") or ""
            await self._push_status(
                session_id,
                "thinking",
                f"进程已挂起{('：' + reason) if reason else ''}，等待恢复…",
            )
            logger.info("kernel 进程挂起等待 proc=%s reason=%s", proc.id, reason)
            def _refresh_from_shared(p):
                # 多 worker：他进程 resume 写 Redis，本机 Event 不会 set
                try:
                    from backend.kernel import get_kernel
                    k = get_kernel()
                    fresh = k.get_process(p.id)
                    if fresh is not None and fresh is not p:
                        p.state = fresh.state
                        if fresh.state != "suspended":
                            p.resume()
                    elif fresh is p and k._shared is not None:
                        data = k._shared.get_process(p.id)
                        if data and data.get("state") != "suspended":
                            p.state = data["state"]
                            p.resume()
                except Exception as _silent_e:
                    logger.debug("suppressed: %s", _silent_e, exc_info=False)

            ok = await proc.wait_if_suspended(
                should_stop=lambda: self._should_stop,
                refresh_state=_refresh_from_shared,
            )
            if not ok:
                logger.info("挂起等待被打断 proc=%s（stop 或终态）", proc.id)
                return "stop"
            await self._push_status(session_id, "thinking", "进程已恢复，继续执行")
        # 2) 事前预算检查（不足时先弹性续航，再失败才中断）
        if (
            bool(getattr(settings, "agent_kernel_budget_precheck", True))
            and proc.token_budget is not None
        ):
            remaining = proc.budget_remaining
            if remaining is not None:
                estimated = self._estimate_next_call_tokens(messages)
                thr = float(
                    getattr(settings, "agent_budget_soft_renew_threshold", 0.85) or 0.85
                )
                used_ratio = 0.0
                if proc.token_budget and proc.token_budget > 0:
                    used_ratio = float(proc.tokens_used) / float(proc.token_budget)
                hard_only = bool(
                    getattr(settings, "agent_budget_hard_cap_only", False)
                )
                # Interactive CEO + workforce (limited): soft renew at precheck.
                # Workforce hard-cap only when agent_workforce_hard_cap_only or meta.
                _origin = str(getattr(self, "_run_origin", "") or "").lower()
                _meta_p = getattr(proc, "meta", None) or {}
                _is_wf_proc = bool(
                    isinstance(_meta_p, dict) and _meta_p.get("workforce")
                ) or str(getattr(proc, "identity", "") or "").startswith("wf:")
                _interactive = (not _is_wf_proc) and _origin in (
                    "",
                    "chat",
                    "default",
                    "goal",
                )
                _wf_hard = False
                if _is_wf_proc:
                    try:
                        _wf_hard = bool(
                            getattr(settings, "agent_workforce_hard_cap_only", False)
                        )
                    except Exception:
                        _wf_hard = False
                    if isinstance(_meta_p, dict) and _meta_p.get("hard_cap_only") in (
                        True,
                        "true",
                        1,
                        "1",
                    ):
                        _wf_hard = True
                # 产品语义：
                # - hard_cap_only：只挡「经典 soft_renew」（防无界续航）
                # - CEO / 主会话 (_interactive)：始终 chat_elastic
                # - 编制有限次 auto top_up：独立开关，默认开（比主会话更紧）
                _wf_auto_on = bool(_is_wf_proc) and (not _wf_hard) and bool(
                    getattr(settings, "agent_workforce_auto_top_up_enabled", True)
                )
                _soft_renew_on = (not hard_only) and bool(
                    getattr(settings, "agent_budget_soft_renew_enabled", False)
                )
                soft_on = bool(_interactive) or _wf_auto_on or _soft_renew_on
                need_renew = remaining < estimated or used_ratio >= thr
                if soft_on and need_renew and remaining < estimated:
                    try:
                        from backend.kernel import get_kernel

                        renewed = get_kernel().try_soft_renew_budget(
                            proc.id,
                            need=estimated,
                            reason="precheck",
                        )
                        if renewed:
                            # 刷新本地 proc 引用
                            fresh = get_kernel().get_process(proc.id)
                            if fresh is not None:
                                self._kernel_process = fresh
                                proc = fresh
                            remaining = proc.budget_remaining
                            src = renewed.get("source") or "soft_renew"
                            await self._push_status(
                                session_id,
                                "thinking",
                                f"预算弹性续航 +{renewed.get('amount')} "
                                f"（{src} 第 {renewed.get('renew_count')} 次），继续执行…",
                            )
                        elif _is_wf_proc and not _wf_hard and bool(
                            getattr(settings, "agent_workforce_auto_top_up_enabled", True)
                        ):
                            # 经典 soft_renew 不可用时：编制有限次 direct top_up
                            _n = 0
                            if isinstance(_meta_p, dict):
                                _n = int(_meta_p.get("auto_top_up_count") or 0)
                            _max = int(
                                getattr(
                                    settings, "agent_workforce_auto_top_up_max", 3
                                )
                                or 3
                            )
                            if _n < _max:
                                _add = max(
                                    int(estimated - remaining) + 50_000,
                                    int(
                                        getattr(
                                            settings,
                                            "agent_workforce_auto_top_up_min_add",
                                            100_000,
                                        )
                                        or 100_000
                                    ),
                                )
                                _add = min(_add, 400_000)
                                # 总预算不超过编制 hard_cap
                                try:
                                    _cap = int(
                                        getattr(
                                            settings,
                                            "agent_workforce_budget_hard_cap",
                                            2_000_000,
                                        )
                                        or 2_000_000
                                    )
                                    _bud = int(proc.token_budget or 0)
                                    if _cap > 0 and _bud + _add > _cap:
                                        _add = max(0, _cap - _bud)
                                except Exception as _silent_e:
                                    logger.debug("suppressed: %s", _silent_e, exc_info=False)
                                if _add > 0:
                                    get_kernel().top_up_budget(
                                        proc.id,
                                        _add,
                                        by="system:workforce_precheck",
                                        reason=f"precheck top_up n={_n + 1}",
                                    )
                                    if isinstance(_meta_p, dict):
                                        _meta_p = dict(_meta_p)
                                        _meta_p["auto_top_up_count"] = _n + 1
                                        try:
                                            proc.meta = _meta_p  # type: ignore[misc]
                                        except Exception as _silent_e:
                                            logger.debug("suppressed: %s", _silent_e, exc_info=False)
                                    fresh = get_kernel().get_process(proc.id)
                                    if fresh is not None:
                                        self._kernel_process = fresh
                                        proc = fresh
                                    remaining = proc.budget_remaining
                                    logger.info(
                                        "workforce precheck top_up proc=%s add=%s n=%s",
                                        proc.id,
                                        _add,
                                        _n + 1,
                                    )
                                    await self._push_status(
                                        session_id,
                                        "thinking",
                                        f"员工预算动态追加 +{_add}（事前检查），继续…",
                                    )
                        elif _interactive and not renewed:
                            # 双保险：try_soft_renew 已含 chat_elastic；再显式 top_up 一次
                            _n = 0
                            if isinstance(_meta_p, dict):
                                _n = int(
                                    _meta_p.get("chat_auto_top_up_count")
                                    or _meta_p.get("soft_renew_count")
                                    or 0
                                )
                            _max = int(
                                getattr(settings, "agent_chat_auto_top_up_max", 16) or 16
                            )
                            if _n < _max:
                                _add = max(
                                    int(estimated - (remaining or 0)) + 80_000,
                                    int(
                                        getattr(
                                            settings,
                                            "agent_chat_auto_top_up_min_add",
                                            250_000,
                                        )
                                        or 250_000
                                    ),
                                )
                                _add = min(_add, 800_000)
                                get_kernel().top_up_budget(
                                    proc.id,
                                    _add,
                                    by="system:chat_precheck",
                                    reason=f"chat precheck top_up n={_n + 1}",
                                )
                                fresh = get_kernel().get_process(proc.id)
                                if fresh is not None:
                                    self._kernel_process = fresh
                                    proc = fresh
                                remaining = proc.budget_remaining
                                logger.info(
                                    "chat precheck top_up proc=%s add=%s n=%s",
                                    proc.id,
                                    _add,
                                    _n + 1,
                                )
                                await self._push_status(
                                    session_id,
                                    "thinking",
                                    f"CEO 会话预算动态追加 +{_add}，继续执行…",
                                )
                    except Exception as e:
                        logger.debug("soft_renew/top_up at precheck: %s", e)
                if remaining is not None and remaining < estimated:
                    logger.warning(
                        "事前预算检查不通过 proc=%s remaining=%s estimated=%s",
                        proc.id, remaining, estimated,
                    )
                    return "budget"
        # 3) 调度让出
        await asyncio.sleep(0)
        return None

    def _estimate_next_call_tokens(self, messages: list[dict[str, Any]]) -> int:
        """粗估下一次 LLM 调用消耗：近期上下文 + 工具 schema 开销 + 输出预留。"""
        reserve = int(getattr(settings, "agent_kernel_precheck_reserve", 2000) or 2000)
        recent = messages[-20:] if len(messages) > 20 else messages
        chars = 0
        for m in recent:
            c = m.get("content")
            if isinstance(c, str):
                chars += len(c)
            elif isinstance(c, list):  # 多模态分块
                chars += sum(
                    len(str(p.get("text") or "")) for p in c if isinstance(p, dict)
                )
            # tool_calls 参数也计入
            for tc in m.get("tool_calls") or []:
                if isinstance(tc, dict):
                    fn = tc.get("function") or {}
                    chars += len(str(fn.get("name") or ""))
                    chars += len(str(fn.get("arguments") or ""))
        # 工具 schema：几十工具可达数万 token，不能忽略
        schema_est = 0
        try:
            tools = getattr(self, "_last_tools_payload", None) or getattr(
                self, "_loaded_tools_for_est", None
            )
            if isinstance(tools, list) and tools:
                # 粗估：每工具名+描述 ~80 token，上限 40k
                schema_est = min(40_000, len(tools) * 120)
            else:
                n = int(getattr(self, "_last_tools_count", 0) or 0)
                if n > 0:
                    schema_est = min(40_000, n * 120)
        except Exception:
            schema_est = 0
        return max(1, round(chars / 3.4)) + reserve + schema_est

    async def _contract_tool_block_reason(
        self, name: str, arguments: dict[str, Any]
    ) -> str | None:
        """Skill 契约 tools 白名单检查；命中拦截返回错误文案，否则 None。

        白名单 = 会话已挂载包 skill.yaml 声明的 tools 并集；无声明不过滤。
        每个 loop 实例懒加载一次（session 级，run 内不变）。

        并发安全（T1）：加载必须在锁内完成。原实现「先置 _contract_wl_loaded=True
        再 await 加载」，并发调用下第二个调用会看到标志已置位、白名单仍是 None，
        从而静默绕过契约拦截。串行时不暴露，一旦工具并行就是安全漏洞。
        """
        if not getattr(self, "_contract_wl_ready", False):
            lock = getattr(self, "_contract_wl_lock", None)
            if lock is None:
                lock = self._contract_wl_lock = asyncio.Lock()
            async with lock:
                # 双检：等锁期间可能已被别的调用加载完
                if not getattr(self, "_contract_wl_ready", False):
                    self._contract_whitelist = None
                    try:
                        sid = arguments.get("_session_id")
                        if sid:
                            from backend.packages.loader import (
                                resolve_attached_tool_whitelist,
                            )
                            from backend.packages.session_packages import (
                                get_session_attached_packages,
                            )

                            attached = await get_session_attached_packages(str(sid))
                            self._contract_whitelist = (
                                await resolve_attached_tool_whitelist(attached)
                            )
                    except Exception as e:
                        logger.debug("contract whitelist load skipped: %s", e)
                        self._contract_whitelist = None
                    # 只有真正加载完才置位
                    self._contract_wl_ready = True
        wl = getattr(self, "_contract_whitelist", None)
        if wl and name not in wl:
            return (
                f"[Skill Contract Blocked] 工具 '{name}' 不在已挂载包的 tools 白名单内"
                f"（白名单: {', '.join(sorted(wl))}）。"
                "请挂载声明该工具的包，或让包作者把它加入 skill.yaml 的 tools。"
            )
        return None

    async def run(
        self,
        session_id: uuid.UUID,
        user_input: str,
        attachments: list[dict[str, Any]] | None = None,
        mode: str = "default",
        sub_agent_ids: list[str] | None = None,
        _nested: bool = False,
    ) -> str:
        """
        执行 Agent Loop（同一 session 并发安全：使用 asyncio.Lock 串行执行）

        _nested=True：子代理迷你 Run 专用——父 run 已持有 session 锁并在等待
        子 run 完成，此时直接执行，避免自死锁。
        """
        if _nested:
            return await self._run_inner(session_id, user_input, attachments, mode, sub_agent_ids or [])
        # Session 级串行：等锁超时 + 可见状态，避免「连发消息前端像死锁」
        try:
            _lock_wait = float(
                getattr(settings, "agent_session_lock_wait_secs", 120.0) or 120.0
            )
        except Exception:
            _lock_wait = 120.0
        _lock = get_session_lock(session_id)
        if _lock.locked():
            try:
                await self._push_status(
                    session_id,
                    "thinking",
                    "上一轮仍在执行，本条消息排队中…",
                )
            except Exception:
                pass
        lock, ok = await acquire_session_lock(session_id, timeout=_lock_wait)
        if not ok:
            try:
                await self._push_status(
                    session_id,
                    "error",
                    f"会话忙：等待上一轮超过 {_lock_wait:.0f}s，请稍后再发",
                )
            except Exception:
                pass
            logger.warning(
                "session_lock wait timeout sid=%s wait=%ss",
                str(session_id)[:8],
                _lock_wait,
            )
            return (
                f"⚠️ 会话上一轮仍在执行（已等待 {_lock_wait:.0f}s）。"
                "请稍后再发，或点停止后再试。"
            )
        try:
            return await self._run_inner(
                session_id, user_input, attachments, mode, sub_agent_ids or []
            )
        finally:
            try:
                lock.release()
            except Exception:
                pass

    async def _run_inner(
        self,
        session_id: uuid.UUID,
        user_input: str,
        attachments: list[dict[str, Any]] | None,
        mode: str,
        sub_agent_ids: list[str],
    ) -> str:
        """Durable Run 包装：recorder 创建/收尾 + 调用 _run_locked"""
        _ws_reset = lambda: None  # noqa: E731
        # Durable Run（Phase 0.5.2）：一次 run() = 一条 AgentRun 记录
        from backend.agent.run_recorder import RunRecorder

        meta: dict[str, Any] = {}
        parent_run_id = getattr(self, "_parent_run_id", None)
        if parent_run_id:
            meta["parent_run_id"] = str(parent_run_id)
        agent_key = getattr(self, "_agent_key", None)
        if agent_key and agent_key != "main":
            meta["agent_key"] = agent_key
            meta["agent_label"] = getattr(self, "_agent_label", "")

        # Phase 2.1：origin / identity / parent 进 AgentRun 列（非仅 meta）
        _identity_id = getattr(self, "_identity_id", None)
        if _identity_id:
            meta["identity_id"] = str(_identity_id)
        _inbox_item = getattr(self, "_inbox_item_id", None)
        if _inbox_item:
            meta["inbox_item_id"] = str(_inbox_item)
        _origin_hint = getattr(self, "_run_origin", None)
        _token_limit = 0
        try:
            _token_limit = int(
                (getattr(self, "_kernel_process_options", None) or {}).get("token_budget")
                or getattr(self, "_token_budget", 0)
                or 0
            )
        except (TypeError, ValueError):
            _token_limit = 0

        recorder = RunRecorder(
            session_id,
            user_id=self.user_id,
            mode=mode,
            meta=meta or None,
            origin=_origin_hint,
            identity_id=_identity_id,
            parent_run_id=parent_run_id,
            token_limit=_token_limit,
        )
        self._run_recorder = recorder
        await recorder.start(input_summary=user_input or "")
        try:
            from backend.agent.run_brief import reset_brief
            from backend.agent.run_events import reset_seq, emit_run_event
            reset_brief(session_id, goal=str(user_input or "")[:500])
            reset_seq(session_id)
            await emit_run_event(
                self.ws_manager,
                session_id,
                "run.started",
                detail=(user_input or "")[:120],
                run_id=str(getattr(recorder, "run_id", "") or "") or None,
            )
            # Coding loop SM: understand → … → deliver
            try:
                from backend.agent.coding_loop import start_coding_loop, phase_label
                from backend.agent.run_events import emit_run_event as _emit_cl

                cl = start_coding_loop(
                    session_id,
                    goal=str(user_input or "")[:500],
                    user_input=(
                        ""
                        if getattr(self, "_config_micro_loop", None)
                        else str(user_input or "")
                    ),
                    mode=(
                        "default"
                        if getattr(self, "_config_micro_loop", None)
                        else str(mode or "default")
                    ),
                )
                if cl.active:
                    await _emit_cl(
                        self.ws_manager,
                        session_id,
                        "coding.phase",
                        detail=phase_label(cl.phase),
                        payload={"phase": cl.phase.value, "active": True},
                        run_id=str(getattr(recorder, "run_id", "") or "") or None,
                    )
            except Exception as _cl_e:
                logger.debug("coding_loop start skip: %s", _cl_e)
        except Exception as _rb_e:
            logger.debug("run_brief start skip: %s", _rb_e)
        # Phase 2.1/2.2：Run id 回写 loop，供 process.meta / inbox 关联
        self._agent_run_id = getattr(recorder, "run_id", None)
        # Phase 2.4：Goal 挂 Run 链
        try:
            from backend.agent.goal_state import bind_goal_run_id

            bind_goal_run_id(session_id, self._agent_run_id)
        except Exception as _silent_e:
            logger.debug("suppressed: %s", _silent_e, exc_info=False)

        # ── Agent Kernel（阶段 1/W1）：本次 run 纳入进程生命周期管理 ──
        kernel = None
        kernel_proc = None
        if bool(getattr(settings, "agent_kernel_enabled", True)):
            from backend.kernel import get_kernel

            kernel = get_kernel()
            try:
                # 父进程解析：subagent 场景显式传入的 kernel 进程 id 优先；
                # parent_run_id 是 run 记录 id（≠ kernel 进程 id），仅作兜底展示
                parent_pid = getattr(self, "_parent_kernel_process_id", None)
                # 能力显式化（审计项 #2）：开启后主进程挂注册表全集快照——
                # 与兼容模式等效放行，但使 subagent 继承/narrow 真实生效；
                # 快照失败降级 None（兼容模式）。Intent 最小权限交互落地前
                # 这是 narrowing 链路的正确前置。
                caps: list[str] | None = None
                # 0.6：workforce 派遣可显式指定进程选项（身份权限档案/默认预算）
                proc_opts = getattr(self, "_kernel_process_options", None) or {}
                require_intent = bool(
                    getattr(settings, "agent_kernel_require_intent", True)
                )
                intent_raw = (
                    getattr(self, "_intent_declaration", None)
                    or proc_opts.get("intent")
                )
                if proc_opts.get("capabilities") is not None:
                    caps = list(proc_opts["capabilities"])
                elif require_intent:
                    # P0-B：禁止静默全开 — 无显式 caps 时交给 kernel 默认只读 intent
                    caps = None
                elif bool(getattr(settings, "agent_kernel_explicit_capabilities", False)):
                    # 旧路径：注册表全集快照（仅 require_intent=false 时）
                    try:
                        from backend.tools.registry import ToolRegistry

                        caps = sorted(ToolRegistry._tools.keys()) or None
                    except Exception as e:
                        logger.debug("能力快照失败，降级兼容模式: %s", e)
                        caps = None
                # 默认 intent（主会话）：只读探索
                if intent_raw is None and require_intent and caps is None:
                    intent_raw = {
                        "goal": str(
                            getattr(
                                settings,
                                "agent_kernel_default_intent_goal",
                                "interactive chat (minimum privilege)",
                            )
                            or "interactive chat (minimum privilege)"
                        ),
                        "capabilities": [],
                        "constraints": {},
                    }
                _meta = {
                    "mode": mode,
                    "parent_run_id": str(parent_run_id) if parent_run_id else None,
                    "run_id": str(self._agent_run_id) if self._agent_run_id else None,
                    "origin": getattr(recorder, "origin", None),
                    # 多用户归属：API 用 meta.user_id 做 owner 校验
                    "user_id": str(self.user_id) if getattr(self, "user_id", None) else None,
                    "session_id": str(session_id),
                }
                if isinstance(proc_opts.get("meta"), dict):
                    _meta.update(proc_opts["meta"])
                    if self._agent_run_id:
                        _meta["run_id"] = str(self._agent_run_id)
                    if getattr(self, "user_id", None) and not _meta.get("user_id"):
                        _meta["user_id"] = str(self.user_id)
                _akey = agent_key or "main"
                if str(_akey).startswith("wf:") or _meta.get("workforce"):
                    try:
                        await kernel.retire_live_identity_processes(
                            str(_akey),
                            reason="loop create_process preflight",
                        )
                    except Exception as _re:
                        logger.debug("loop preflight retire: %s", _re)
                # Interactive / CEO chat budget: auto-allocate (not workforce job path).
                # coding_profile engineering defaults to 200k only when budget is None —
                # long steward sessions burn ~50–90k/LLM round and die after a few tools.
                _token_budget = proc_opts.get("token_budget")
                if (
                    _token_budget is None
                    and not str(_akey).startswith("wf:")
                    and not _meta.get("workforce")
                ):
                    try:
                        from backend.agent.workforce_budget import (
                            resolve_interactive_chat_budget,
                        )
                        from backend.agent.workforce_dispatch import is_steward_contact

                        _contact_early = str(
                            getattr(self, "_contact_agent", "") or ""
                        ).strip()
                        _is_steward_early = is_steward_contact(_contact_early)
                        if not _is_steward_early and self.session_repo is not None:
                            try:
                                _sess = await self.session_repo.get(session_id)
                                _cfg = (
                                    getattr(_sess, "config", None) or {}
                                    if _sess is not None
                                    else {}
                                )
                                if isinstance(_cfg, dict):
                                    _contact_early = str(
                                        _cfg.get("contact_agent") or _contact_early
                                    ).strip()
                                    _id_txt = str(_cfg.get("identity") or "")
                                    _is_steward_early = is_steward_contact(
                                        _contact_early
                                    ) or is_steward_contact(_id_txt)
                                    if _contact_early:
                                        self._contact_agent = _contact_early
                            except Exception as _silent_e:
                                logger.debug("suppressed: %s", _silent_e, exc_info=False)
                        _hist_est = 0
                        try:
                            if self.message_repo is not None:
                                _msgs = await self.message_repo.get_history_by_session(
                                    session_id, limit=200
                                )
                                _hist_est = sum(
                                    len(str(getattr(m, "content", "") or "")) // 3
                                    for m in (_msgs or [])
                                )
                        except Exception:
                            _hist_est = 0
                        _token_budget = resolve_interactive_chat_budget(
                            user_input=user_input or "",
                            is_steward=_is_steward_early,
                            history_tokens_est=_hist_est,
                        )
                        logger.info(
                            "interactive chat budget auto process_key=%s "
                            "steward=%s budget=%s hist_est=%s",
                            str(_akey)[:16],
                            _is_steward_early,
                            _token_budget,
                            _hist_est,
                        )
                    except Exception as _be:
                        logger.debug("interactive budget skip: %s", _be)
                        _token_budget = 500_000  # safer floor than profile 200k
                create_kwargs: dict = {
                    "session_id": str(session_id),
                    "parent_id": parent_pid,
                    "capabilities": caps,
                    "token_budget": _token_budget,
                    "meta": _meta,
                }
                # Rust host accepts intent= on create_process
                if intent_raw is not None and hasattr(kernel, "_call"):
                    try:
                        from backend.kernel.intent import IntentDeclaration

                        if isinstance(intent_raw, dict):
                            create_kwargs["intent"] = IntentDeclaration.from_dict(
                                intent_raw
                            ).to_dict()
                        else:
                            create_kwargs["intent"] = intent_raw.to_dict()  # type: ignore[union-attr]
                    except Exception:
                        create_kwargs["intent"] = (
                            intent_raw if isinstance(intent_raw, dict) else None
                        )
                try:
                    kernel_proc = await kernel.create_process(_akey, **create_kwargs)
                except TypeError:
                    # Python fallback AgentKernel has no intent= kw
                    create_kwargs.pop("intent", None)
                    kernel_proc = await kernel.create_process(_akey, **create_kwargs)
                await kernel.mark_running(kernel_proc.id)
                # H2-A2: production must not run with capabilities=None (compat full-open)
                try:
                    from backend.kernel.production_guard import (
                        allow_compat_full_open,
                        emit_compat_denied,
                    )

                    if (
                        getattr(kernel_proc, "capabilities", None) is None
                        and not allow_compat_full_open()
                    ):
                        emit_compat_denied(
                            kernel_proc.id,
                            "create_process_compat_none",
                            {"identity": _akey},
                        )
                        # Force default readonly intent if possible
                        try:
                            from backend.kernel.intent import (
                                IntentDeclaration,
                                apply_intent_to_process,
                            )

                            di = IntentDeclaration.from_dict(
                                {
                                    "goal": "h2 forced minimum privilege",
                                    "capabilities": [],
                                    "constraints": {},
                                }
                            )
                            apply_intent_to_process(kernel, kernel_proc.id, di)
                            fresh = kernel.get_process(kernel_proc.id)
                            if fresh is not None:
                                kernel_proc = fresh
                        except Exception as _force_e:
                            logger.error(
                                "H2: cannot force intent on None caps: %s", _force_e
                            )
                            if bool(
                                getattr(
                                    settings,
                                    "agent_kernel_fail_closed_on_create",
                                    True,
                                )
                            ):
                                raise RuntimeError(
                                    "H2: process has capabilities=None in production"
                                ) from _force_e
                except RuntimeError:
                    raise
                except Exception as _pg:
                    logger.debug("production guard post-create: %s", _pg)
                # Intent apply if not already applied at create (Python fallback / late intent)
                if intent_raw and not (
                    isinstance(getattr(kernel_proc, "meta", None), dict)
                    and (kernel_proc.meta or {}).get("intent")
                ):
                    try:
                        from backend.kernel.intent import apply_intent_to_process

                        parent_tok = None
                        if parent_pid:
                            pp = kernel.get_process(parent_pid)
                            if pp is not None:
                                parent_tok = getattr(pp, "token", None)
                        tok, dropped = apply_intent_to_process(
                            kernel,
                            kernel_proc.id,
                            intent_raw,
                            parent_token=parent_tok,
                        )
                        logger.info(
                            "intent applied process=%s granted=%s dropped=%s",
                            kernel_proc.id[:8],
                            sorted(tok.capabilities)[:12],
                            dropped[:8],
                        )
                        # refresh process view
                        fresh = kernel.get_process(kernel_proc.id)
                        if fresh is not None:
                            kernel_proc = fresh
                    except Exception as _ie:
                        logger.warning("intent apply skip: %s", _ie)
                elif intent_raw:
                    logger.info(
                        "intent on create process=%s caps=%s",
                        kernel_proc.id[:8],
                        (kernel_proc.capabilities or [])[:12],
                    )
                # P0-C：run 级调度登记 + 并发槽
                # P0-D：isolation profile
                try:
                    if hasattr(kernel, "_call"):
                        is_wf = str(_akey).startswith("wf:") or bool(
                            _meta.get("workforce")
                        )
                        pclass = "workforce" if is_wf else "foreground"
                        await kernel._acall(
                            "schedule_run",
                            {
                                "process_id": kernel_proc.id,
                                "priority_class": pclass,
                                "payload": {
                                    "session_id": str(session_id),
                                    "mode": mode,
                                },
                            },
                        )
                        iso_profile = (
                            "workforce"
                            if is_wf
                            else (
                                "read_only"
                                if str(mode or "").lower()
                                in ("plan", "ask", "explore")
                                else "interactive"
                            )
                        )
                        try:
                            await kernel._acall(
                                "isolation_set_profile",
                                {
                                    "process_id": kernel_proc.id,
                                    "profile": iso_profile,
                                },
                            )
                        except Exception as _silent_e:
                            logger.debug("suppressed: %s", _silent_e, exc_info=False)
                        # 全局 RunGate：跨会话排队，拿到 lease 再继续（session 锁只保同会话）
                        await self._await_run_gate(
                            kernel,
                            kernel_proc.id,
                            priority_class=pclass,
                            session_id=session_id,
                        )
                except Exception as _sch:
                    # T2：run_gate 硬失败向上抛；其它调度错误可降级
                    msg = str(_sch)
                    if "run gate" in msg.lower() or "run_gate" in msg.lower():
                        raise
                    logger.warning("schedule_run/run_gate soft skip: %s", _sch)
                self._kernel_process = kernel_proc
                try:
                    recorder.kernel_process_id = kernel_proc.id
                except Exception as _silent_e:
                    logger.debug("suppressed: %s", _silent_e, exc_info=False)
                # PR1–PR4: configure Rust loop_guard (max rounds / ban worker orch / crew cap)
                try:
                    if bool(getattr(settings, "agent_loop_guard_enabled", True)):
                        from backend.agent.loop_guard_bridge import (
                            build_loop_guard_config,
                            configure_for_process,
                        )

                        _meta = getattr(kernel_proc, "meta", None) or {}
                        if not isinstance(_meta, dict):
                            _meta = {}
                        _wf = bool(
                            _meta.get("workforce")
                            or getattr(self, "_workforce", False)
                            or str(getattr(self, "_run_origin", "") or "")
                            in ("inbox", "cron", "workforce")
                        )
                        # audit-fix(#9)：原实现引用未定义的 messages（NameError 被
                        # except 吞掉，instruction 恒为空串）。改用函数参数 user_input。
                        _instr = (
                            user_input.strip()
                            if isinstance(user_input, str) and user_input.strip()
                            else ""
                        )
                        _cfg = build_loop_guard_config(
                            workforce=_wf,
                            identity_name=str(
                                getattr(self, "_identity_name", None)
                                or _meta.get("identity_name")
                                or ""
                            ),
                            identity_role=None,
                            instruction=_instr
                            or str(_meta.get("instruction") or ""),
                            payload=_meta if isinstance(_meta, dict) else None,
                        )
                        # Steward full-open chat: allow crew with cap
                        if not _wf and not str(
                            getattr(self, "_agent_key", "") or ""
                        ).startswith("wf:"):
                            if _cfg.get("role_kind") in ("chat", "steward"):
                                _cfg["role_kind"] = "steward"
                                _cfg["ban_worker_orch"] = False
                                try:
                                    from backend.agent.progress_guard import (
                                        soft_open_mode as _so_crew,
                                    )

                                    _soft_crew = _so_crew()
                                except Exception:
                                    _soft_crew = True
                                # Hard mode still keeps usable headroom (was 3/1 → false throttle)
                                _crew_def = 999 if _soft_crew else 24
                                _orch_def = 24 if _soft_crew else 8
                                _cfg["max_crew_total"] = int(
                                    getattr(
                                        settings,
                                        "agent_crew_steward_max_per_run",
                                        _crew_def,
                                    )
                                    or _crew_def
                                )
                                _cfg["max_orch_per_round"] = int(
                                    getattr(
                                        settings,
                                        "agent_max_orch_tools_per_round",
                                        _orch_def,
                                    )
                                    or _orch_def
                                )
                                if _soft_crew:
                                    _cfg["max_crew_total"] = max(
                                        int(_cfg["max_crew_total"] or 0), 999
                                    )
                                    _cfg["max_orch_per_round"] = max(
                                        int(_cfg["max_orch_per_round"] or 0), 24
                                    )
                        # Goal / long coding: never let loop_guard be tighter than
                        # the iteration budget (was 40 → blocked file_write mid-scaffold).
                        try:
                            _goalish = bool(
                                goal_mode
                                or str(mode or "").lower() in ("goal", "default")
                            )
                            _iter_budget = int(
                                getattr(self, "max_iterations", 0)
                                or getattr(settings, "agent_max_iterations", 40)
                                or 40
                            )
                            if _goalish:
                                _iter_budget = max(
                                    _iter_budget,
                                    int(
                                        getattr(
                                            settings, "agent_goal_max_iterations", 100
                                        )
                                        or 100
                                    ),
                                )
                            _mr0 = int(_cfg.get("max_tool_rounds") or 0)
                            if _iter_budget > 0:
                                _cfg["max_tool_rounds"] = max(_mr0, _iter_budget)
                        except Exception as _mr_e:
                            logger.debug("loop_guard budget align skip: %s", _mr_e)
                        await asyncio.to_thread(  # audit-fix: sync RPC → to_thread
                            configure_for_process, str(kernel_proc.id), _cfg
                        )
                        # Do NOT shrink max_iterations to match guard (that capped
                        # goal runs at ~42 and hit LoopGuard mid multi-crate write).
                        logger.info(
                            "loop_guard configured process=%s role=%s max_rounds=%s ban_orch=%s",
                            str(kernel_proc.id)[:8],
                            _cfg.get("role_kind"),
                            _cfg.get("max_tool_rounds"),
                            _cfg.get("ban_worker_orch"),
                        )
                except Exception as _lg_e:
                    # 护栏配置失败会削弱 thrash/orch 早停，但 iteration budget 仍兜底
                    logger.warning(
                        "loop_guard configure failed process=%s: %s",
                        str(getattr(kernel_proc, "id", "") or "")[:8],
                        _lg_e,
                    )
                # 日用场景收窄：coding_research → 默认 engineering profile
                try:
                    scenario = str(
                        getattr(settings, "agent_default_scenario", "coding_research")
                        or "coding_research"
                    )
                    profile = str(
                        getattr(settings, "agent_default_coding_profile", "engineering")
                        or "engineering"
                    )
                    mode_l = str(mode or "").lower()
                    if scenario == "coding_research" and mode_l not in (
                        "ask",
                        "plan",
                        "explore",
                        "chat",
                    ):
                        if hasattr(kernel, "_call"):
                            applied = await kernel._acall(
                                "coding_profile_apply",
                                {
                                    "process_id": kernel_proc.id,
                                    "profile": profile,
                                },
                            )
                        elif hasattr(kernel, "coding_profile_apply"):
                            applied = kernel.coding_profile_apply(
                                kernel_proc.id, profile
                            )
                        else:
                            applied = None
                        fresh = kernel.get_process(kernel_proc.id)
                        if fresh is not None:
                            kernel_proc = fresh
                            self._kernel_process = kernel_proc
                        # Steward sessions still need crew_steward on the token.
                        # Normal solo chat no longer expects it (P0 single-agent default).
                        caps_now = list(getattr(kernel_proc, "capabilities", None) or [])
                        if "crew_steward" not in caps_now:
                            _contact_chk = str(
                                getattr(self, "_contact_agent", "") or ""
                            ).strip()
                            _want_crew = False
                            try:
                                from backend.agent.workforce_dispatch import (
                                    is_steward_contact,
                                )

                                _want_crew = is_steward_contact(_contact_chk)
                            except Exception:
                                _want_crew = False
                            if _want_crew:
                                logger.warning(
                                    "coding_profile applied but crew_steward missing "
                                    "from steward token process=%s caps=%s applied=%s",
                                    kernel_proc.id[:8],
                                    caps_now[:16],
                                    applied,
                                )
                        # CEO/管家：进程能力 + 令牌全开。
                        # 注意：coding_profile 之后 process.capabilities 已被收窄，
                        # 直接 issue_token(["*"]) 会被 kernel 以「超出进程能力集」拒绝；
                        # 必须先 apply_intent / escalate 扩进程，再挂令牌。
                        try:
                            from backend.agent.workforce_dispatch import (
                                ensure_steward_kernel_full_open_async,
                                is_steward_contact,
                            )

                            _contact = str(
                                getattr(self, "_contact_agent", "") or ""
                            ).strip()
                            _steward = is_steward_contact(_contact)
                            if not _steward and isinstance(
                                getattr(self, "config", None), dict
                            ):
                                cfg = self.config  # type: ignore[attr-defined]
                                _steward = is_steward_contact(
                                    str(cfg.get("contact_agent") or "")
                                ) or is_steward_contact(
                                    str(cfg.get("identity") or "")
                                )
                            # session config 也可能只在后面工具加载时解析；这里再查 recorder/session
                            if not _steward:
                                try:
                                    _sess = await self.session_repo.get(session_id)
                                    sc = (
                                        getattr(_sess, "config", None) or {}
                                        if _sess is not None
                                        else {}
                                    )
                                    if isinstance(sc, dict):
                                        _steward = is_steward_contact(
                                            str(sc.get("contact_agent") or "")
                                        ) or is_steward_contact(
                                            str(sc.get("identity") or "")
                                        )
                                except Exception as _silent_e:
                                    logger.debug("suppressed: %s", _silent_e, exc_info=False)
                            if _steward:
                                ok_fo = await ensure_steward_kernel_full_open_async(
                                    kernel, kernel_proc.id
                                )
                                fresh2 = kernel.get_process(kernel_proc.id)
                                if fresh2 is not None:
                                    kernel_proc = fresh2
                                    self._kernel_process = kernel_proc
                                caps_now = list(
                                    getattr(kernel_proc, "capabilities", None) or []
                                )
                                logger.info(
                                    "steward kernel full-open process=%s ok=%s caps=%s",
                                    kernel_proc.id[:8],
                                    ok_fo,
                                    caps_now[:8],
                                )
                        except Exception as _st_cap:
                            logger.warning(
                                "steward full-open token skip: %s", _st_cap
                            )
                        logger.info(
                            "scenario=%s coding_profile=%s process=%s caps=%s",
                            scenario,
                            profile,
                            kernel_proc.id[:8],
                            caps_now[:16],
                        )
                except Exception as _sc:
                    logger.warning("coding profile apply skip: %s", _sc)

                # Track host epoch so tools can rehydrate after host restart
                try:
                    self._kernel_host_epoch = int(
                        getattr(kernel, "_host_epoch", 0) or 0
                    )
                except Exception:
                    self._kernel_host_epoch = 0
            except Exception as e:
                # H-03：Agent 正式 run 要求 kernel 时不得静默退回无门控路径
                require_kernel = bool(
                    getattr(settings, "agent_kernel_enabled", True)
                ) and (
                    bool(getattr(self, "_workforce", False))
                    or bool(getattr(settings, "agent_kernel_require_intent", True))
                    or bool(getattr(settings, "agent_kernel_fail_closed_on_create", True))
                )
                if require_kernel:
                    logger.error("kernel create_process 失败（fail-closed）: %s", e)
                    raise RuntimeError(
                        f"kernel create_process failed (fail-closed): {e}"
                    ) from e
                logger.warning("kernel create_process 失败，退回无 kernel 路径: %s", e)
                kernel_proc = None
                self._kernel_process = None
        async def _release_kernel_slot(
            *, state: str, reason: str | None = None
        ) -> None:
            """释放 run_gate / 结束进程。CancelledError 路径必须走这里（P0）。"""
            if kernel is None or kernel_proc is None:
                return
            # 先回收 LLM 租约：cancel 时可能卡在 admission 排队，end_process 前也要清
            try:
                from backend.kernel.llm_admission import get_llm_admission

                await get_llm_admission().release_by_process(
                    str(getattr(kernel_proc, "id", "") or "")
                )
            except Exception as le:
                logger.debug("llm release_by_process on slot release: %s", le)
            try:
                if hasattr(kernel, "_call"):
                    try:
                        await kernel._acall(
                            "run_gate_release", {"process_id": kernel_proc.id}
                        )
                    except Exception as _silent_e:
                        logger.debug("suppressed: %s", _silent_e, exc_info=False)
                    try:
                        await kernel._acall(
                            "run_release", {"process_id": kernel_proc.id}
                        )
                    except Exception as _silent_e:
                        logger.debug("suppressed: %s", _silent_e, exc_info=False)
            except Exception as _silent_e:
                logger.debug("suppressed: %s", _silent_e, exc_info=False)
            try:
                await kernel.end_process(
                    kernel_proc.id, state=state, reason=reason
                )
            except Exception as ee:
                logger.warning(
                    "end_process after run failed proc=%s: %s",
                    getattr(kernel_proc, "id", "")[:12],
                    ee,
                )

        try:
            result = await self._run_locked(
                session_id, user_input, attachments, mode, sub_agent_ids
            )
            # 回写迭代/token 到 Run（观测台不再假零）
            try:
                if getattr(self, "last_iterations", None):
                    recorder._iterations = int(self.last_iterations)
                if kernel_proc is not None:
                    recorder.set_token_used(int(getattr(kernel_proc, "tokens_used", 0) or 0))
            except Exception as _silent_e:
                logger.debug("suppressed: %s", _silent_e, exc_info=False)
            if self._should_stop:
                await recorder.cancel("stopped by user")
            else:
                await recorder.finish_ok(final_summary=result or "")
            await _release_kernel_slot(
                state="killed" if self._should_stop else "completed",
                reason="stopped by user" if self._should_stop else None,
            )
            return result
        except asyncio.CancelledError:
            # P0：CancelledError 是 BaseException；清理段用 shield 防二次 cancel 打断漏槽
            async def _cleanup_cancel() -> None:
                try:
                    if kernel_proc is not None:
                        recorder.set_token_used(
                            int(getattr(kernel_proc, "tokens_used", 0) or 0)
                        )
                except Exception as _silent_e:
                    logger.debug("suppressed: %s", _silent_e, exc_info=False)
                try:
                    await recorder.cancel("cancelled")
                except Exception as _silent_e:
                    logger.debug("suppressed: %s", _silent_e, exc_info=False)
                await _release_kernel_slot(state="killed", reason="cancelled")

            try:
                await asyncio.shield(_cleanup_cancel())
            except Exception as ce:
                logger.warning("cancel cleanup incomplete: %s", ce)
            raise
        except Exception as e:
            # P0 审计 N1：except 退出时 Python 会 del e；shield 内闭包若引用 e 会 NameError → 槽位泄漏
            err_msg = str(e)
            async def _cleanup_fail() -> None:
                try:
                    if kernel_proc is not None:
                        recorder.set_token_used(
                            int(getattr(kernel_proc, "tokens_used", 0) or 0)
                        )
                except Exception as _silent_e:
                    logger.debug("suppressed: %s", _silent_e, exc_info=False)
                try:
                    await recorder.finish_fail(err_msg)
                except Exception as _silent_e:
                    logger.debug("suppressed: %s", _silent_e, exc_info=False)
                await _release_kernel_slot(state="failed", reason=err_msg[:500])

            try:
                await asyncio.shield(_cleanup_fail())
            except Exception as ce:
                logger.warning("fail cleanup incomplete: %s", ce)
            raise
        finally:
            self._run_recorder = None
            # 供 workforce dispatcher 在 run() 返回后取进程 id（_kernel_process 会清空）
            if kernel_proc is not None:
                self._last_kernel_process_id = getattr(kernel_proc, "id", None) or getattr(
                    kernel_proc, "process_id", None
                )
            self._kernel_process = None

    async def _run_locked(
        self,
        session_id: uuid.UUID,
        user_input: str,
        attachments: list[dict[str, Any]] | None = None,
        mode: str = "default",
        sub_agent_ids: list[str] | None = None,
    ) -> str:
        """实际的 Agent Loop 逻辑（已被外层锁保护）"""
        logger.info(f"Agent loop started for session {session_id}, mode={mode}")
        logger.debug("loop start should_stop=%s", self._should_stop)

        # Durable Run：进入规划阶段
        _rc = getattr(self, "_run_recorder", None)
        if _rc is not None:
            try:
                from backend.agent.run_state import RunStatus as _RS

                await _rc.transition(_RS.PLANNING, note=f"mode={mode}")
            except Exception as _silent_e:
                logger.debug("suppressed: %s", _silent_e, exc_info=False)

        # @device 远程执行（L1）：命中则短路，不进工具循环（phases/prologue）
        from backend.agent.phases.prologue import (
            expand_continue_phrase,
            try_config_intent_shortcut_safe,
            try_device_shortcut,
        )

        _device_card = await try_device_shortcut(self, session_id, user_input, attachments)
        if _device_card is not None:
            return _device_card

        # Config Intent：MCP key / 代理 / 切模型 / 简单模式 / OAuth 引导（轻量短路）
        _cfg_reply = await try_config_intent_shortcut_safe(
            self, session_id, user_input, attachments
        )
        if _cfg_reply is not None:
            return _cfg_reply

        # P0-1：配 MCP 未命中完整快路径 → 配置微 loop 标记
        self._config_micro_loop = None
        self._thrash_force_final_override = None
        self._pseudo_tool_leak_streak = 0
        try:
            from backend.services.config_intent import detect_mcp_micro_loop

            _ml = detect_mcp_micro_loop(user_input or "")
            if _ml:
                self._config_micro_loop = _ml
                logger.info(
                    "config micro-loop armed label=%s max_iters=%s session=%s",
                    _ml.get("label"),
                    _ml.get("max_iters"),
                    session_id,
                )
        except Exception as _ml_e:
            logger.debug("config micro-loop detect skip: %s", _ml_e)

        import time as _time
        _max_dur = float(getattr(settings, "agent_max_duration_seconds", 0) or 0)
        _deadline = (_time.monotonic() + _max_dur) if _max_dur > 0 else None

        # 「请继续」/「接着下一项」→ 自动接 Goal/checkpoint 续跑（phases/prologue）
        # Keep raw phrase for vague-work / continue classification after expand.
        _raw_user_phrase = str(user_input or "")
        user_input, mode = await expand_continue_phrase(session_id, user_input, mode)
        try:
            self._raw_user_phrase = _raw_user_phrase[:500]
            self._continue_phrase_expanded = bool(
                user_input != _raw_user_phrase and user_input
            )
        except Exception:
            self._raw_user_phrase = _raw_user_phrase[:500]
            self._continue_phrase_expanded = False

        # 处理附件内容注入
        enriched_input = self._build_user_input_with_attachments(user_input, attachments or [])
        # 供 clarify 禁用 / 工具注入直接执行意图
        try:
            self._last_user_input = str(user_input or enriched_input or "")[:8000]
        except Exception:
            self._last_user_input = str(user_input or "")[:8000]
        _max_in = int(getattr(settings, "agent_max_user_input_chars", 100_000) or 100_000)
        if _max_in > 0 and len(enriched_input) > _max_in:
            logger.warning(
                "User input truncated %s -> %s chars for session %s",
                len(enriched_input), _max_in, session_id,
            )
            enriched_input = (
                enriched_input[:_max_in]
                + f"\n\n[系统: 输入过长已截断至 {_max_in} 字符]"
            )
        _soft = int(getattr(settings, "agent_large_input_soft_chars", 32_000) or 0)
        if _soft > 0 and len(enriched_input) > _soft:
            head_n = max(1000, _soft // 2)
            tail_n = max(1000, _soft - head_n)
            omitted = len(enriched_input) - head_n - tail_n
            if omitted > 0:
                enriched_input = (
                    enriched_input[:head_n]
                    + f"\n\n…[系统: 大输入中间省略 {omitted} 字符，保留头尾]…\n\n"
                    + enriched_input[-tail_n:]
                )
                logger.info(
                    "Soft-truncated large input to head+tail (~%s chars) session=%s",
                    len(enriched_input),
                    session_id,
                )

        # 1. 保存用户消息 + TTL 清理 + 同步到 CtxItem（同一事务）
        # regenerate 路径：不再落库重复用户句
        if getattr(self, "_skip_user_persist", False):
            self._skip_user_persist = False
        else:
            # ack 带上原始 user_input，前端乐观气泡可按原文合并（enriched 可能含附件正文）
            await self._persist_user_input(
                session_id,
                enriched_input,
                display_content=user_input,
            )

        # 2. 获取 Session 配置（行级锁由 Repository 实现）
        session = await self.session_repo.get_with_lock(session_id)
        if session is None:
            self._should_stop = True
            try:
                from backend.api.websocket import manager as ws_manager

                ws_manager.end_run_snapshot(session_id)
            except Exception as _silent_e:
                logger.debug("suppressed: %s", _silent_e, exc_info=False)
            raise ValueError(f"Session {session_id} not found")

        config = await self.session_repo.get_config(session_id)

        # 本轮 workspace 覆盖（session.config + 用户消息中的绝对路径如 E:\项目\guardian）
        _ws_reset = lambda: None  # noqa: E731
        try:
            from backend.tools.permissions import bind_run_workspace_from_config

            _ws_reset = bind_run_workspace_from_config(
                config if isinstance(config, dict) else {},
                user_text=str(user_input or ""),
            )
        except Exception as _ws_e:
            logger.debug("workspace bind skipped: %s", _ws_e)

        # 3. 加载历史消息（保留 tool_calls / tool_call_id，避免多轮工具链断裂）
        # 动态 limit：按 context_window 估算最大消息数，避免只加载 100 条导致压缩无法触发
        _ctx_win = int(getattr(settings, "context_window", 128_000) or 128_000)
        _est_limit = max(200, _ctx_win // 50)  # 每 50 tokens 一条消息的保守估计
        # 编制工单：跳过历史，避免跨单上下文膨胀导致首包打穿 token 预算
        _skip_hist = bool(
            getattr(self, "_workforce_skip_history", False)
            or mode == "workforce"
        )
        if _skip_hist:
            history = []
            logger.info(
                "Skip history for workforce session %s (fresh job context)",
                session_id,
            )
        else:
            history = await self._load_history(
                session_id, limit=_est_limit
            )
            logger.info(
                "Loaded %d history messages for session %s (limit=%d)",
                len(history),
                session_id,
                _est_limit,
            )
            # Re-bind extra_roots from recent user turns (e.g. prior msg has E:\项目\guardian,
            # current is「再试下」) so path:workspace does not thrash on re-try.
            try:
                from backend.tools.permissions import bind_run_workspace_from_config

                _path_bits = [str(user_input or "")]
                for h in reversed(list(history or [])):
                    if getattr(h, "role", None) == "user" and getattr(h, "content", None):
                        _path_bits.append(str(h.content)[:4000])
                        if len(_path_bits) >= 6:
                            break
                _ws_reset = bind_run_workspace_from_config(
                    config if isinstance(config, dict) else {},
                    user_text="\n".join(_path_bits),
                )
            except Exception as _ws2_e:
                logger.debug("workspace rebind after history skip: %s", _ws2_e)
        history_dicts: list[dict[str, Any]] = []
        try:
            from backend.agent.thinking_format import strip_thinking as _strip_think
        except Exception:
            def _strip_think(x: str | None) -> str:  # type: ignore[misc]
                return (x or "")

        for h in history:
            if h.role not in ("user", "assistant", "tool"):
                continue
            raw_content = h.content if h.content is not None else ""
            # Model context: strip thinking tags + collapse force_final scare dumps
            # so auto-resume does not re-feed long 「强制收束」inventories.
            # DeepSeek V4 tools：thinking 正文需还原为 reasoning_content 字段回传。
            _reasoning_from_hist = ""
            if h.role == "assistant" and raw_content:
                try:
                    from backend.agent.thinking_format import extract_reasoning_content

                    _reasoning_from_hist = extract_reasoning_content(raw_content)
                except Exception:
                    _reasoning_from_hist = ""
                raw_content = _strip_think(raw_content)
                try:
                    from backend.agent.thinking_format import (
                        strip_force_final_scare_for_context as _strip_ff,
                    )

                    raw_content = _strip_ff(raw_content)
                except Exception:
                    pass
            tcs = getattr(h, "tool_calls", None)
            # 严格 API：assistant 带 tool_calls 时 content 不能是 ""（须 null）
            if h.role == "assistant" and tcs and not (raw_content or "").strip():
                item: dict[str, Any] = {"role": "assistant", "content": None, "tool_calls": tcs}
            else:
                item = {"role": h.role, "content": raw_content or ""}
                if h.role == "assistant" and tcs:
                    item["tool_calls"] = tcs
            if h.role == "assistant" and _reasoning_from_hist:
                item["reasoning_content"] = _reasoning_from_hist
            # tool_call_id 可能存在 JSON tool_calls 旁路或 content 元数据中
            if h.role == "tool":
                tc_meta = tcs
                if isinstance(tc_meta, dict) and tc_meta.get("tool_call_id"):
                    item["tool_call_id"] = tc_meta["tool_call_id"]
                elif isinstance(tc_meta, list) and tc_meta:
                    first = tc_meta[0] if isinstance(tc_meta[0], dict) else {}
                    if first.get("tool_call_id"):
                        item["tool_call_id"] = first["tool_call_id"]
                    elif first.get("id"):
                        item["tool_call_id"] = first["id"]
            history_dicts.append(item)

        # Codex-style pair repair before context assembly
        try:
            from backend.agent.history_normalize import normalize_history_for_llm

            history_dicts = normalize_history_for_llm(history_dicts)
        except Exception as _nh_e:
            logger.debug("normalize_history_for_llm skip: %s", _nh_e)

        # 刚写入的用户消息已在 history 末尾，build 时不再重复追加同一条
        # （context 仍会 append user_input；下面剥离 history 中与当前输入相同的尾部 user）
        if (
            history_dicts
            and history_dicts[-1].get("role") == "user"
            and (history_dicts[-1].get("content") or "") == enriched_input
        ):
            history_for_build = history_dicts[:-1]
        else:
            history_for_build = history_dicts

        # Pre-compress fat history BEFORE dumb truncate in build_messages.
        # Without this, load 5000 → keep ~500 tail with no L5 summary of the head
        # (root cause vs Grok/Codex/Hermes smooth long-context).
        try:
            _soft_pre = int(getattr(settings, "context_max_messages_soft", 40) or 40)
            if len(history_for_build) >= max(24, _soft_pre):
                from backend.agent.context_compress import compress_history_if_needed
                from backend.agent.context_engine import (
                    COMPRESS_THRESHOLD_DEEP,
                    get_context_engine,
                )

                _eng_pre = get_context_engine(session_id)
                try:
                    _eng_pre.on_session_reset()
                except Exception:
                    pass
                history_for_build, _pre_meta = await compress_history_if_needed(
                    list(history_for_build),
                    session_id=session_id,
                    threshold=min(
                        float(
                            getattr(settings, "context_threshold_percent", 0.85) or 0.85
                        ),
                        float(COMPRESS_THRESHOLD_DEEP),
                        0.55,
                    ),
                    allow_l5=True,
                    micro_only=False,
                    # Critical: never head/tail-wipe pre-build history to ~5 msgs
                    aggressive_hard_drop=False,
                )
                if (_pre_meta or {}).get("compressed") or (_pre_meta or {}).get(
                    "pre_build_soft_trim"
                ):
                    logger.info(
                        "pre-build history compress session=%s layers=%s msgs=%s "
                        "soft_trim=%s pre_hard=%s",
                        session_id,
                        (_pre_meta or {}).get("layers"),
                        len(history_for_build),
                        (_pre_meta or {}).get("pre_build_soft_trim"),
                        (_pre_meta or {}).get("pre_hard_drop"),
                    )
        except Exception as _pre_e:
            logger.debug("pre-build history compress skip: %s", _pre_e)

        # 4. 场景预判（先于 system/skill 组装，保证 prompt-skill 与工具面一致）
        from backend.agent.tool_policy import (
            infer_scene,
            injection_knobs,
        )

        _early_profile = str(
            (config or {}).get("tool_profile")
            or getattr(settings, "agent_tool_profile", "coding")
            or "coding"
        ).strip().lower()
        scene_plan = infer_scene(
            enriched_input or user_input or "",
            mode=mode or "default",
            profile=_early_profile,
        )
        inject_opts = injection_knobs(scene_plan.injection_tier)

        # 5. 组装 messages（CtxItem 优先；skill 按场景+档位注入）
        messages, accessed_items, total_tokens = await self.context_manager.build_messages(
            session_id=session_id,
            user_input=enriched_input,
            history=history_for_build,
            fallback_config=config,
            mode=mode,
            scene_packs=list(scene_plan.packs),
            prompt_skill_mode=str(inject_opts.get("skill_mode") or "auto"),
            prompt_skill_match_threshold=float(inject_opts.get("skill_threshold") or 0.95),
            prompt_skill_max_full=int(inject_opts.get("skill_max_full") or 0),
            inject_prompt_skills=bool(inject_opts.get("prompt_skills", True)),
        )

        # 记录初始上下文访问流
        if accessed_items and self.context_flow_repo is not None:
            await self._record_flow(session_id, self.agent_name, accessed_items, total_tokens)

        # P0-3: Auto Optimize 自动触发
        await self._check_auto_optimize(session_id, config, total_tokens)

        # 6. 加载 Skills/Tools — 复用预判 scene_plan，避免二次漂移
        from backend.agent.tool_policy import (
            compact_capability_brief,
            resolve_enabled_tool_names,
        )

        raw_skills = config.get("skills", None)
        raw_tools = config.get("tools", None)
        tool_profile = _early_profile

        if raw_skills is None or raw_skills == [] or raw_skills == ["*"]:
            enabled_skills = None
        else:
            enabled_skills = list(raw_skills)

        mode_extra: list[str] = []
        if mode == "search":
            mode_extra.extend(["web_search", "search", "fetch_webpage"])
        if mode == "ppt":
            mode_extra.append("generate_ppt")
        if mode == "report":
            mode_extra.extend(["generate_report", "render_chart"])
        if mode == "goal":
            from backend.agent.goal_facade import goal_mode_tool_extras

            mode_extra.extend(goal_mode_tool_extras())
        if mode == "cluster":
            mode_extra.extend(
                [
                    "crew_steward",
                    "manage_sub_agent",
                    "delegate_task",
                    "agent_call",
                    "okr_goal",
                ]
            )


        # 联系 CEO/管家：强制编制工具面（分析→assign 员工，不起子代理闷跑）
        # Solo plan/read/simple turns skip STEWARD_FORCE_TOOLS + crew pack mount.
        _contact_agent = ""
        _is_steward_session = False
        _solo_early = False
        try:
            from backend.agent.simple_intent import (
                is_solo_session_intent as _is_solo_early_fn,
                wants_team_dispatch as _wants_team_early,
            )

            _ut_early = str(user_input or enriched_input or "")
            if not _wants_team_early(_ut_early):
                _solo_early = bool(
                    _is_solo_early_fn(_ut_early, mode=mode or "default")
                )
        except Exception:
            _solo_early = False
        try:
            from backend.agent.workforce_dispatch import (
                STEWARD_FORCE_TOOLS,
                is_steward_contact,
            )

            if isinstance(config, dict):
                _contact_agent = str(config.get("contact_agent") or "").strip()
            # 供 tool_hooks / 危险确认「本员工允许」使用（写入 loop 后注入工具参数）
            self._contact_agent = _contact_agent
            _is_steward_session = is_steward_contact(_contact_agent)
            # 无 contact 时：identity 文案里带 CEO/管家也按管家编排
            if not _is_steward_session and isinstance(config, dict):
                _id_txt = str(config.get("identity") or "")
                if is_steward_contact(_id_txt) or is_steward_contact(
                    str(config.get("role") or "")
                ):
                    _is_steward_session = True
                    if not _contact_agent:
                        _contact_agent = "大管家"
                        self._contact_agent = _contact_agent
            # 联系 TA / CEO：解析 Identity，后续 command 用编制能力短路（不再反复弹窗）
            # 必须在 steward 判定之后：无 contact 的管家会话也要绑到默认 CEO 编制
            if not getattr(self, "_identity_id", None):
                try:
                    from backend.agent.grant_store import (
                        resolve_ceo_identity,
                        resolve_identity_id,
                    )
                    from backend.agent.steward_permission import (
                        load_identity_capabilities,
                    )

                    _iid = None
                    _iname = _contact_agent
                    if _contact_agent:
                        _iid = await resolve_identity_id(contact_name=_contact_agent)
                    if not _iid and _is_steward_session:
                        _ceo = await resolve_ceo_identity()
                        if _ceo:
                            _iid = str(_ceo.get("id") or "") or None
                            _iname = str(_ceo.get("name") or _contact_agent or "CEO")
                            if not _contact_agent:
                                _contact_agent = _iname
                                self._contact_agent = _contact_agent
                    if _iid:
                        self._identity_id = _iid
                        self._identity_name = _iname or _contact_agent
                        _caps = await load_identity_capabilities(_iid)
                        if _caps is not None:
                            self._identity_capabilities = list(_caps)
                        logger.info(
                            "session identity bound name=%s id=%s caps=%s steward=%s",
                            str(self._identity_name or "")[:24],
                            str(_iid)[:8],
                            list(_caps or [])[:8] if _caps is not None else None,
                            _is_steward_session,
                        )
                except Exception as _id_e:
                    logger.debug("contact identity resolve skip: %s", _id_e)
            if _is_steward_session and not _solo_early:
                mode_extra.extend(STEWARD_FORCE_TOOLS)
                # 此时才确定是 CEO/管家：扩进程能力 + 令牌全开
                # （仅 issue_token(["*"]) 会在 coding_profile 后失败）
                try:
                    from backend.agent.workforce_dispatch import (
                        ensure_steward_kernel_full_open_async,
                    )
                    from backend.kernel import get_kernel as _gk

                    kp = getattr(self, "_kernel_process", None)
                    if kp is not None and bool(
                        getattr(settings, "agent_kernel_enabled", True)
                    ):
                        k = _gk()
                        ok_fo = await ensure_steward_kernel_full_open_async(k, kp.id)
                        fresh = k.get_process(kp.id)
                        if fresh is not None:
                            self._kernel_process = fresh
                        logger.info(
                            "steward full-open (post-detect) process=%s ok=%s",
                            str(kp.id)[:8],
                            ok_fo,
                        )
                except Exception as _fo:
                    logger.warning("steward full-open post-detect skip: %s", _fo)
            elif _is_steward_session and _solo_early:
                logger.info(
                    "steward solo early: skip STEWARD_FORCE_TOOLS session=%s",
                    session_id,
                )

        except Exception as _st_e:
            logger.debug("steward session detect skipped: %s", _st_e)

        enabled_tools_filter, scene_plan = resolve_enabled_tool_names(
            mode=mode or "default",
            raw_tools=raw_tools,
            raw_skills=raw_skills,
            profile=tool_profile,
            extra=mode_extra,
            user_input=enriched_input or user_input or "",
            scene=scene_plan,
            # Solo plan/read: do not mount crew pack even on CEO contact
            extra_packs=(
                ["crew"] if (_is_steward_session and not _solo_early) else None
            ),
        )
        inject_opts = injection_knobs(scene_plan.injection_tier)
        if mode_extra and enabled_skills is not None:
            enabled_skills = list(set(list(enabled_skills) + mode_extra))

        tools = await self._load_tools(
            session_id, enabled_skills, enabled_tools_filter, user_input=user_input
        )
        # K-03 / P0：按 kernel 进程能力裁剪 LLM 可见 tools
        from backend.agent.cap_tools import filter_tools_for_process

        tools = filter_tools_for_process(
            tools, getattr(self, "_kernel_process", None)
        )
        # H2-E: thin observability — caps / visible tools on process meta + status
        try:
            kp = getattr(self, "_kernel_process", None)
            caps_n = len(getattr(kp, "capabilities", None) or []) if kp else 0
            if kp is not None and isinstance(getattr(kp, "meta", None), dict):
                kp.meta["tools_visible_count"] = len(tools)
                kp.meta["caps_count"] = caps_n
                if getattr(kp, "capabilities", None) is not None:
                    kp.meta["caps_preview"] = list(kp.capabilities)[:16]
            await self._push_status(
                session_id,
                "thinking",
                f"场景 {scene_plan.summary()} · 能力 {caps_n} · 工具 {len(tools)}",
                caps_count=caps_n,
                tools_count=len(tools),
            )
        except Exception:
            try:
                await self._push_status(
                    session_id,
                    "thinking",
                    f"场景 {scene_plan.summary()} · 工具 {len(tools)}",
                    tools_count=len(tools),
                )
            except Exception as _silent_e:
                logger.debug("suppressed: %s", _silent_e, exc_info=False)
        logger.info(
            "Loaded %s tools session=%s profile=%s scene=%s filter=%s",
            len(tools),
            session_id,
            tool_profile,
            scene_plan.summary(),
            "ALL" if enabled_tools_filter is None else len(enabled_tools_filter),
        )

        # 会话级简单模式：少工具、短循环、软提示（不硬改模型决策逻辑）
        try:
            if isinstance(config, dict) and config.get("simple_mode"):
                _sm_cap = int(config.get("simple_mode_max_iterations") or 8)
                self.max_iterations = min(int(self.max_iterations or _sm_cap), max(4, _sm_cap))
                _allow = {
                    "file_read",
                    "file_write",
                    "file_list",
                    "list_dir",
                    "web_search",
                    "search",
                    "fetch_webpage",
                    "python",
                    "command",
                    "manage_mcp",
                    "get_system_status",
                    "update_config",
                    "list_available_models",
                    "clarify",
                    "memory",
                    "memory_pref",
                }

                def _simple_keep(t: dict) -> bool:
                    fn = t.get("function") if isinstance(t.get("function"), dict) else {}
                    name = str((fn or {}).get("name") or t.get("name") or "")
                    if name in _allow:
                        return True
                    if name.startswith("mcp_") and any(
                        x in name for x in ("search", "fetch", "scrape", "web")
                    ):
                        return True
                    return False

                before_sm = len(tools or [])
                tools = [t for t in (tools or []) if isinstance(t, dict) and _simple_keep(t)]
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "【会话·简单模式】工具已收窄、循环更短。"
                            "优先直接回答或少量工具；涉及破坏性操作时先简短确认。"
                            "不要派工/编制/长链探索。用户可说「关闭简单模式」退出。"
                        ),
                    }
                )
                logger.info(
                    "simple_mode tools %s→%s max_iter=%s session=%s",
                    before_sm,
                    len(tools),
                    self.max_iterations,
                    session_id,
                )
        except Exception as _sm_e:
            logger.debug("simple_mode apply skip: %s", _sm_e)

# S1/S3/S10/S12: thin/search caps + plan mode + diff-first
        try:
            from backend.agent.tool_policy import (
                is_search_only_intent,
                is_thin_chat_intent,
                scene_max_iterations,
                THIN_CHAT_TOOLS,
                THIN_SEARCH_TOOLS,
            )
            _ui = user_input or ""
            _kind = "coding"
            if getattr(self, "_config_micro_loop", None):
                _kind = "thin"
            elif is_thin_chat_intent(_ui) and not goal_mode:
                _kind = "thin"
            elif is_search_only_intent(_ui) and not goal_mode:
                _kind = "search"
            elif goal_mode:
                _kind = "goal"
            _cap = scene_max_iterations(_kind, default=int(self.max_iterations or 40))
            if not goal_mode or _kind == "thin":
                self.max_iterations = min(int(self.max_iterations or _cap), _cap)
            try:
                from backend.core.config import settings as _st_iv
                if (
                    not goal_mode
                    and bool(getattr(_st_iv, "agent_interactive_force_thrash", True))
                    and _kind in {"thin", "search", "chat", "coding"}
                ):
                    self._thrash_force_final_override = True
            except Exception:
                pass
            # 工具面收敛：resolve 已标 thin/search surface 时不再二次裁剪 schema
            _reasons = list(getattr(scene_plan, "reasons", None) or [])
            _already_thin = any(
                r in _reasons
                for r in (
                    "thin_chat_surface",
                    "auto_thin_chat",
                    "search_thin_surface",
                    "mcp_ops:thin_surface",
                    "mcp_ops:thin+verify",
                )
            )
            if (
                _kind == "thin"
                and not getattr(self, "_config_micro_loop", None)
                and not _already_thin
            ):
                def _thin_keep(t):
                    fn = t.get("function") if isinstance(t.get("function"), dict) else {}
                    name = str((fn or {}).get("name") or t.get("name") or "")
                    return name in THIN_CHAT_TOOLS
                before = len(tools or [])
                tools = [t for t in (tools or []) if isinstance(t, dict) and _thin_keep(t)]
                if before != len(tools):
                    logger.info("auto_thin_chat tools %s→%s max_iter=%s session=%s", before, len(tools), self.max_iterations, session_id)
                    messages.append({"role": "system", "content": "【薄档】优先直接回答。需要文件/终端/搜索时用 use_tool_pack 扩容。"})
            elif _kind == "thin" and _already_thin and not getattr(self, "_config_micro_loop", None):
                messages.append({"role": "system", "content": "【薄档】优先直接回答；需要文件/终端/搜索时用 use_tool_pack 扩容。"})
            elif _kind == "search" and not _already_thin:
                def _s_keep(t):
                    fn = t.get("function") if isinstance(t.get("function"), dict) else {}
                    name = str((fn or {}).get("name") or t.get("name") or "")
                    if name in THIN_SEARCH_TOOLS:
                        return True
                    if name.startswith("mcp_") and any(x in name for x in ("search", "fetch", "scrape", "web")):
                        return True
                    return False
                before = len(tools or [])
                tools = [t for t in (tools or []) if isinstance(t, dict) and _s_keep(t)]
                if before != len(tools):
                    logger.info("search_thin tools %s→%s max_iter=%s session=%s", before, len(tools), self.max_iterations, session_id)
        except Exception as _sc_e:
            logger.debug("scene max_iters/thin apply skip: %s", _sc_e)

        try:
            from backend.agent.plan_intent import (
                filter_tools_for_plan,
                is_complex_for_auto_plan,
                is_plan_approve,
                is_plan_reject,
                is_plan_request,
                plan_system_prompt,
            )
            from backend.agent.plan_session import (
                approve_plan,
                get_gate,
                requires_plan_approval,
                start_plan,
            )
            from backend.agent.plan_gate import PlanState

            _mode_l = str(mode or "").strip().lower()
            _ui = user_input or ""
            _sid_s = str(session_id)
            _gate = get_gate(session_id=_sid_s)
            if is_plan_reject(_ui):
                try:
                    _gate.reject()
                except Exception:
                    pass
                messages.append({"role": "system", "content": "计划已驳回，请重新给出修订计划。"})
                self._plan_mode_active = True
            if is_plan_approve(_ui) and _gate.state == PlanState.PLAN_READY:
                try:
                    approve_plan(session_id=_sid_s)
                    self._plan_mode_active = False
                    messages.append({"role": "system", "content": "计划已批准，进入执行阶段。按步骤修改并验证。"})
                    logger.info("plan approved session=%s", session_id)
                except Exception as _ap_e:
                    logger.warning("plan approve failed: %s", _ap_e)
            _want_plan = (
                _mode_l == "plan"
                or is_plan_request(_ui)
                or (
                    bool(getattr(__import__("backend.core.config", fromlist=["settings"]).settings, "agent_plan_mode_auto", True))
                    and is_complex_for_auto_plan(_ui)
                    and _mode_l in {"default", "deepthink", "goal", ""}
                    and _gate.state in (PlanState.IDLE, PlanState.CANCELLED, PlanState.DONE)
                )
            )
            if _gate.state == PlanState.BUILDING:
                _want_plan = False
                self._plan_mode_active = False
            elif requires_plan_approval(session_id=_sid_s, chat_mode=_mode_l) or _want_plan:
                if _gate.state in (PlanState.IDLE, PlanState.CANCELLED, PlanState.DONE):
                    start_plan(session_id=_sid_s)
                self._plan_mode_active = True
                before = len(tools or [])
                tools = filter_tools_for_plan(tools)
                messages.append({"role": "system", "content": plan_system_prompt()})
                logger.info("plan mode armed tools %s→%s session=%s state=%s", before, len(tools or []), session_id, _gate.state)
        except Exception as _pl_e:
            logger.debug("plan mode apply skip: %s", _pl_e)

        try:
            from backend.core.config import settings as _st_df
            if bool(getattr(_st_df, "agent_diff_first", True)) and not getattr(self, "_plan_mode_active", False):
                _ui2 = (user_input or "").lower()
                _diff_keys = (
                    "修bug", "修 bug", "写代码", "改代码", "实现功能",
                    "traceback", "refactor", "apply_patch", "typeerror",
                    "编译错误", "单元测试",
                )
                _code_ctx = ("代码" in _ui2 or "函数" in _ui2 or "模块" in _ui2 or ".py" in _ui2)
                _code_act = any(k in _ui2 for k in ("修复", "重构", "实现", "重写", "patch"))
                if any(k in _ui2 for k in _diff_keys) or (_code_ctx and _code_act):
                    messages.append({"role": "system", "content": "【呈现】优先 unified diff / 变更文件列表 + 验证命令；少写过程散文。"})
        except Exception:
            pass


        # P0-1/P0-4：配置微 loop — 仅 manage_mcp 等运维工具，硬顶 iters
        try:
            _ml = getattr(self, "_config_micro_loop", None)
            if isinstance(_ml, dict) and _ml:
                from backend.core.config import settings as _st_ml

                _cap = int(
                    _ml.get("max_iters")
                    or getattr(_st_ml, "agent_config_micro_max_iterations", 5)
                    or 5
                )
                self.max_iterations = min(int(self.max_iterations or _cap), max(2, _cap))
                _allow = set(_ml.get("tools") or ()) or {
                    "manage_mcp",
                    "clarify",
                    "current_time",
                    "update_config",
                    "get_system_status",
                    "list_available_models",
                }

                def _micro_keep(t: dict) -> bool:
                    fn = t.get("function") if isinstance(t.get("function"), dict) else {}
                    name = str((fn or {}).get("name") or t.get("name") or "")
                    return name in _allow

                before_ml = len(tools or [])
                tools = [
                    t for t in (tools or []) if isinstance(t, dict) and _micro_keep(t)
                ]
                enabled_tools_filter = sorted(_allow)
                try:
                    self._thrash_force_final_override = bool(
                        getattr(_st_ml, "agent_thrash_force_final_interactive", True)
                    )
                except Exception:
                    self._thrash_force_final_override = True
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "【配置微循环】仅 manage_mcp 等运维工具，≤"
                            f"{self.max_iterations} 步。"
                            "有 Key → update env → reload → 必要时 mcp_* 验证；"
                            "无 Key → 请用户粘贴「xxx API Key：xxxx」。"
                            "禁止 command/python 探环境、禁止 web_search 调研配置；"
                            "工具调用走协议，不要写在正文。"
                        ),
                    }
                )
                logger.info(
                    "config micro-loop tools %s→%s max_iter=%s session=%s",
                    before_ml,
                    len(tools),
                    self.max_iterations,
                    session_id,
                )
        except Exception as _ml_e:
            logger.debug("config micro-loop apply skip: %s", _ml_e)

        # 直接执行意图：从 schema 去掉 clarify，避免模型「先问再做」
        # 模糊工作句（帮我弄好…）不走 strip，并注入轻量 clarify 偏好（见下）
        try:
            from backend.agent.direct_intent import (
                filter_clarify_from_tools,
                is_direct_execute_intent,
            )
            from backend.agent.vague_intent import is_vague_work_intent

            _phrase_for_clarify = str(
                getattr(self, "_raw_user_phrase", None)
                or user_input
                or enriched_input
                or ""
            )
            _vague = is_vague_work_intent(_phrase_for_clarify)
            if (
                bool(getattr(settings, "agent_disable_clarify_on_direct", True))
                and not _vague
                and not getattr(self, "_config_micro_loop", None)
            ):
                before_n = len(tools or [])
                tools = filter_clarify_from_tools(
                    tools, user_text=str(user_input or enriched_input or "")
                )
                if tools is not None and len(tools) < before_n:
                    logger.info(
                        "clarify stripped (direct intent) session=%s",
                        session_id,
                    )
                    if is_direct_execute_intent(str(user_input or "")):
                        messages.append(
                            {
                                "role": "system",
                                "content": (
                                    "[Direct execute] User asked to act on the instruction. "
                                    "No clarify/questions; use tools now and deliver results. "
                                    "Reply to the user in their language."
                                ),
                            }
                        )
            elif _vague:
                # Soft note only — does not force a tool call or change tool packs
                try:
                    from backend.agent.vague_intent import vague_work_system_note

                    messages.append(
                        {"role": "system", "content": vague_work_system_note()}
                    )
                    logger.info(
                        "vague work note injected (keep clarify) session=%s",
                        session_id,
                    )
                except Exception:
                    pass
        except Exception as _di_e:
            logger.debug("direct intent tool filter skip: %s", _di_e)

        # 写盘意图 + 附件已内嵌：首轮注入，阻止「再 file_read 同一 PRD」复读
        try:
            from backend.agent.write_intent import (
                attachment_text_already_in_input,
                is_write_intent,
                write_intent_early_system_note,
            )

            _wi_text = str(enriched_input or user_input or "")
            if is_write_intent(_wi_text):
                _att_embedded = attachment_text_already_in_input(
                    _wi_text, attachments or []
                )
                messages.append(
                    {
                        "role": "system",
                        "content": write_intent_early_system_note(
                            attachment_embedded=_att_embedded
                        ),
                    }
                )
                logger.info(
                    "write_intent early note session=%s attachment_embedded=%s",
                    session_id,
                    _att_embedded,
                )
        except Exception as _wi0_e:
            logger.debug("write_intent early note skip: %s", _wi0_e)

        # P0: solo 硬短路 — plan/read/simple 去掉派工+goal 工具，禁止进 Inbox/manage_goal
        _simple_turn = bool(_solo_early)
        try:
            from backend.agent.simple_intent import (
                SOLO_STRIP_TOOLS,
                filter_dispatch_tools_from_schema,
                is_solo_session_intent,
                simple_session_system_note,
            )

            _ut = str(user_input or enriched_input or "")
            if _simple_turn or is_solo_session_intent(_ut, mode=mode or "default"):
                _simple_turn = True
                before_n = len(tools or [])
                tools = filter_dispatch_tools_from_schema(
                    tools, user_text=_ut, mode=mode or "default", force=True
                )
                # Keep filter list in sync so use_tool_pack cannot re-add crew mid-turn
                if enabled_tools_filter is not None:
                    enabled_tools_filter = [
                        n
                        for n in enabled_tools_filter
                        if n not in SOLO_STRIP_TOOLS
                    ]
                stripped = before_n - len(tools or [])
                messages.append(
                    {"role": "system", "content": simple_session_system_note()}
                )
                logger.info(
                    "solo intent: stripped %s dispatch/goal tools session=%s",
                    stripped,
                    session_id,
                )
        except Exception as _si_e:
            logger.debug("solo intent tool filter skip: %s", _si_e)

        # 短纪律 brief + 场景/扩包提示
        try:
            tool_name_list = [
                (t.get("function") or {}).get("name")
                for t in tools
                if (t.get("function") or {}).get("name")
            ]
            brief = compact_capability_brief(
                None if enabled_tools_filter is None else tool_name_list,
                scene=scene_plan,
                user_input=str(enriched_input or user_input or ""),
            )
            try:
                from backend.tools.builtins.capability_tools import _load_prefs

                uid = str(self.user_id) if self.user_id else "local"
                prefs = (_load_prefs(uid).get("users") or {}).get(uid) or {}
                if prefs:
                    brief += "\nUser preferences (honor these): " + json.dumps(
                        prefs, ensure_ascii=False
                    )
            except Exception as _silent_e:
                logger.debug("suppressed: %s", _silent_e, exc_info=False)
            messages.append({"role": "system", "content": brief})
            # MCP 密钥交接：独立短提示（不回显密钥），压「先搜索再配」偏航
            try:
                from backend.agent.tool_policy import is_mcp_secret_handoff

                if is_mcp_secret_handoff(str(enriched_input or user_input or "")):
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "【MCP 配置】用户本轮像是在交付 API Key/密钥。"
                                "请直接 manage_mcp list → update env 写入对应键"
                                "（如 ASK_ECHO_SEARCH_INFINITY_API_KEY / TAVILY_API_KEY）→ "
                                "再用 mcp_* 自测。禁止用 web_search 或其它搜索 MCP 去调研「怎么配置」。"
                                "不要在回复中复述完整密钥。"
                            ),
                        }
                    )
            except Exception as _mcp_ops_e:
                logger.debug("mcp ops handoff hint skip: %s", _mcp_ops_e)
        except Exception as e:
            logger.debug("capability inject skipped: %s", e)

        # CEO/管家编排纪律：复杂任务才派工；简单 turn 已注入【单会话】且工具已 strip
        if _is_steward_session and not _simple_turn:
            try:
                from backend.agent.workforce_dispatch import (
                    steward_orchestration_prompt,
                )

                messages.append(
                    {
                        "role": "system",
                        "content": steward_orchestration_prompt(
                            contact_name=_contact_agent or "大管家"
                        ),
                    }
                )
                # 每轮注入待批提权，避免 CEO 看不到 pending 而不 grant
                try:
                    from backend.agent.steward_auto_grant import (
                        format_pending_grants_brief,
                    )

                    pend = format_pending_grants_brief()
                    if pend:
                        messages.append({"role": "system", "content": pend})
                except Exception as _pg:
                    logger.debug("pending grants brief skip: %s", _pg)
                logger.info(
                    "steward orchestration injected session=%s contact=%s",
                    session_id,
                    _contact_agent,
                )
            except Exception as e:
                logger.warning("steward orchestration inject failed: %s", e)
        elif _is_steward_session and _simple_turn:
            logger.info(
                "steward simple turn: skip full orchestration session=%s",
                session_id,
            )

        # Goal 模式：薄封装（promote / inject / max_iter）— 逻辑在 goal_facade
        # Casual read/summarize Q&A must NOT auto-promote into goal + manage_goal.
        from backend.agent.goal_facade import prepare_goal_runtime, resolve_goal_mode

        _origin = ""
        try:
            _origin = str(
                getattr(self, "_run_origin", None)
                or (config or {}).get("origin")
                or ""
            )
        except Exception:
            _origin = ""
        goal_mode = await resolve_goal_mode(
            session_id,
            mode,
            user_input=str(user_input or enriched_input or ""),
            origin=_origin,
        )

        # soft_open：非 goal 硬闸；goal 保留 soft（更接近 Grok 早停）
        try:
            from backend.agent.progress_guard import set_soft_open_for_run
            from backend.core.config import settings as _st_so

            _goal_only = bool(getattr(_st_so, "agent_soft_open_goal_only", True))
            _global_soft = bool(getattr(_st_so, "agent_soft_open_mode", True))
            if not _global_soft:
                set_soft_open_for_run(False)
            elif _goal_only and not goal_mode:
                set_soft_open_for_run(False)
            else:
                set_soft_open_for_run(True if goal_mode else None)
        except Exception as _so_e:
            logger.debug("set_soft_open_for_run skip: %s", _so_e)

        if goal_mode:
            self.max_iterations = await prepare_goal_runtime(
                session_id=session_id,
                messages=messages,
                enriched_input=enriched_input,
                max_iterations=self.max_iterations,
                push_goal_update=self._push_goal_update,
            )
            _ml_cap = getattr(self, "_config_micro_loop", None)
            if isinstance(_ml_cap, dict) and _ml_cap.get("max_iters"):
                self.max_iterations = min(
                    int(self.max_iterations or 5),
                    max(2, int(_ml_cap["max_iters"])),
                )
        else:
            # Non-goal: optional light note so model does not habitually manage_goal
            try:
                from backend.agent.goal_state import get_goal as _gg_ng

                _g_ng = _gg_ng(session_id)
                if _g_ng is not None and _g_ng.is_complete():
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "[Normal chat] No active Goal for this turn. "
                                "Answer the user directly (read/summarize/plan as asked). "
                                "Do **not** call manage_goal unless the user explicitly "
                                "asks about todos or wants a new goal."
                            ),
                        }
                    )
            except Exception:
                pass

        # 集群模式：注入所选子代理人物设定（协调者视角）/ 真·并行草稿扇出（phases/cluster_mode）
        from backend.agent.phases.cluster_mode import prepare_cluster_mode

        _cluster_result = await prepare_cluster_mode(
            self,
            session_id=session_id,
            user_input=user_input,
            mode=mode,
            sub_agent_ids=sub_agent_ids,
            messages=messages,
        )
        if _cluster_result:
            return _cluster_result

        # 6. 获取 LLM 服务
        # 优先：子代理/编制 override → 会话 config.llm（可 locked）→ 全局当前选型
        # 注意：旧会话快照会把用量永远记到创建时模型（如 deepseek），即使用户已在
        # 设置里切到 gpt-5.6-luna。主会话默认 follow global；仅 locked / 员工覆盖例外。
        llm_snapshot = getattr(self, "_llm_snapshot_override", None) or (
            (config or {}).get("llm") if isinstance(config, dict) else None
        )
        # OpenAI-compat / API: per-request model override
        _req_model = str(getattr(self, "_request_model", None) or "").strip()
        if _req_model:
            if isinstance(llm_snapshot, dict):
                llm_snapshot = dict(llm_snapshot)
                llm_snapshot["model"] = _req_model
            else:
                llm_snapshot = {"model": _req_model}
        _is_wf_llm = str(getattr(self, "_agent_key", "") or "").startswith("wf:") or bool(
            getattr(self, "_workforce", False)
        )
        if (
            not _is_wf_llm
            and getattr(self, "_llm_snapshot_override", None) is None
            and isinstance(llm_snapshot, dict)
            and not bool(llm_snapshot.get("locked"))
        ):
            try:
                g_model = str(getattr(settings, "llm_model", "") or "").strip()
                g_pid = str(
                    getattr(settings, "llm_catalog_provider_id", "") or ""
                ).strip()
                g_url = str(getattr(settings, "llm_base_url", "") or "").strip().rstrip(
                    "/"
                )
                s_model = str(llm_snapshot.get("model") or "").strip()
                s_pid = str(llm_snapshot.get("provider_id") or "").strip()
                s_url = str(llm_snapshot.get("base_url") or "").strip().rstrip("/")
                stale = bool(g_model) and (
                    (g_model != s_model)
                    or (g_pid and s_pid and g_pid != s_pid)
                    or (g_url and s_url and g_url != s_url)
                )
                if stale:
                    logger.info(
                        "follow global LLM (session snap stale) "
                        "session=%s/%s → global=%s/%s",
                        s_pid or "-",
                        s_model or "-",
                        g_pid or "-",
                        g_model or "-",
                    )
                    llm_snapshot = None
            except Exception as _fs:
                logger.debug("follow global LLM skip: %s", _fs)
        # 会话稳定 prompt_cache_key：同会话多轮强制同一 namespace（提高 cache_read）
        _cache_key = f"tevarn:{str(session_id).replace('-', '')[:32]}"
        if isinstance(llm_snapshot, dict):
            _snap = dict(llm_snapshot)
            _snap.setdefault("session_id", str(session_id))
            _snap["prompt_cache_key"] = _cache_key
            llm_service = LLMServiceFactory.get_service_for_snapshot(_snap)
        else:
            llm_service = LLMServiceFactory.get_service_for_snapshot(llm_snapshot)
        try:
            if hasattr(llm_service, "prompt_cache_key"):
                setattr(llm_service, "prompt_cache_key", _cache_key)
        except Exception as _silent_e:
            logger.debug("suppressed: %s", _silent_e, exc_info=False)

        # Coding / write / goal：压低 reasoning_effort。Codex high 时静默期极长，
        # 前端只显示「思考中」且 Stop 要等下一个 SSE chunk 才生效。
        try:
            from backend.services.llm.reasoning_effort import normalize_effort

            _mode_l = str(mode or "").strip().lower()
            _profile = str(
                (config or {}).get("tool_profile")
                or getattr(settings, "agent_tool_profile", "coding")
                or "coding"
            ).strip().lower()
            _writeish = False
            try:
                from backend.agent.write_intent import is_write_intent as _iwi

                _writeish = _iwi(str(user_input or enriched_input or ""))
            except Exception:
                _writeish = False
            # 「做 / 实现 / bootstrap / 落地」也算写意图（is_write_intent 偏窄）
            if not _writeish:
                import re as _re_w

                _writeish = bool(
                    _re_w.search(
                        r"(?i)(实现|落地|bootstrap|开工|改代码|写代码|执行计划|开始做)",
                        str(user_input or enriched_input or ""),
                    )
                )
            _cap_coding = bool(goal_mode) or _mode_l in (
                "goal",
                "code",
            ) or _profile in ("coding", "code", "engineer") or _writeish
            if _cap_coding:
                _cap = str(
                    getattr(settings, "agent_goal_reasoning_effort_cap", "medium")
                    or "medium"
                ).strip().lower()
                # Write/implement: prefer low to cut silent reasoning stalls
                if _writeish or _mode_l in ("goal", "code"):
                    _cap = "low" if _cap in ("medium", "high", "max", "xhigh") else _cap
                _order = ("off", "low", "minimal", "medium", "high", "max", "xhigh")
                _cur = normalize_effort(
                    getattr(getattr(llm_service, "config", None), "reasoning_effort", None)
                    or getattr(llm_service, "reasoning_effort", None)
                    or getattr(settings, "reasoning_effort", "medium"),
                    default="medium",
                )
                _cap_n = normalize_effort(_cap, default="medium")
                if (_order.index(_cur) if _cur in _order else 3) > (
                    _order.index(_cap_n) if _cap_n in _order else 3
                ):
                    for _obj in (
                        getattr(llm_service, "config", None),
                        llm_service,
                    ):
                        if _obj is None:
                            continue
                        try:
                            if hasattr(_obj, "reasoning_effort"):
                                setattr(_obj, "reasoning_effort", _cap_n)
                        except Exception:
                            pass
                    logger.info(
                        "coding reasoning_effort capped %s→%s session=%s writeish=%s",
                        _cur,
                        _cap_n,
                        str(session_id)[:8],
                        _writeish,
                    )
        except Exception as _re_cap:
            logger.debug("reasoning_effort cap skip: %s", _re_cap)

        # 本轮实际模型 → 前端状态条（避免 Picker 与真实调用不一致）
        try:
            _model = str(
                getattr(llm_service, "model", None)
                or (llm_snapshot or {}).get("model")
                or getattr(settings, "llm_model", "")
                or ""
            ).strip()
            _prov = str(
                getattr(llm_service, "provider_id", None)
                or (llm_snapshot or {}).get("provider_id")
                or ""
            ).strip()
            if _model:
                await self._push_status(
                    session_id,
                    "thinking",
                    detail=f"model={_model}",
                    model=_model,
                    provider=_prov or None,
                )
        except Exception as _silent_e:
            logger.debug("suppressed: %s", _silent_e, exc_info=False)

        # 6.5 上下文引擎 pipeline（L1/L3/L5）— per-session 隔离 thrash/L5
        try:
            from backend.agent.context_compress import compress_history_if_needed
            from backend.agent.context_engine import get_context_engine
            from backend.services.llm.provider_profiles import resolve_profile

            eng = get_context_engine(session_id)
            # 每 run 边界重置 L5 计数（同 session 多轮长任务仍可压）
            try:
                eng.on_session_reset()
            except Exception as _silent_e:
                logger.debug("suppressed: %s", _silent_e, exc_info=False)
            # L5 charge 绑定本 run 进程；摘要用会话模型快照
            try:
                kp = getattr(self, "_kernel_process", None)
                eng._charge_process_id = getattr(kp, "id", None)  # type: ignore[attr-defined]
                eng._llm_snapshot = llm_snapshot if isinstance(llm_snapshot, dict) else None  # type: ignore[attr-defined]
            except Exception as _silent_e:
                logger.debug("suppressed: %s", _silent_e, exc_info=False)
            # 按会话模型窗口 apply_profile
            try:
                snap = llm_snapshot if isinstance(llm_snapshot, dict) else {}
                model = str(snap.get("model") or getattr(settings, "llm_model", "") or "")
                base_url = str(
                    snap.get("base_url")
                    or getattr(settings, "llm_base_url", "")
                    or ""
                )
                prov = str(
                    snap.get("provider")
                    or getattr(settings, "llm_provider", "")
                    or ""
                )
                prof = resolve_profile(
                    base_url=base_url or None, model=model or None, llm_provider=prov or None
                )
                if prof is not None and hasattr(eng, "apply_profile"):
                    eng.apply_profile(prof)
            except Exception as _silent_e:
                logger.debug("suppressed: %s", _silent_e, exc_info=False)

            # audit-fix(#1)：阈值默认引用单点常量（0.55 → COMPRESS_THRESHOLD=0.85）；
            # settings.context_threshold_percent 覆盖机制保留
            from backend.agent.context_engine import (
                COMPRESS_THRESHOLD,
                COMPRESS_THRESHOLD_DEEP,
            )

            thr = float(
                getattr(settings, "context_threshold_percent", COMPRESS_THRESHOLD)
                or COMPRESS_THRESHOLD
            )
            # 超长会话更早压（深度阈值 0.45 → COMPRESS_THRESHOLD_DEEP=0.75）
            if len(messages) >= int(
                getattr(settings, "context_max_messages_soft", 48) or 48
            ):
                thr = min(thr, COMPRESS_THRESHOLD_DEEP)
            messages, compress_meta = await compress_history_if_needed(
                messages, session_id=session_id, threshold=thr, allow_l5=True
            )
            if compress_meta.get("compressed"):
                layers = compress_meta.get("layers") or []
                dropped = compress_meta.get("dropped_messages", 0)
                await self._push_status(
                    session_id,
                    "optimizing",
                    f"上下文已压缩 layers={layers} dropped={dropped}",
                )
            # seed engine meter from pre-call estimate
            try:
                eng.update_from_response(
                    {"prompt_tokens": compress_meta.get("tokens_after")
                     or compress_meta.get("tokens_before")
                     or total_tokens}
                )
            except Exception as _silent_e:
                logger.debug("suppressed: %s", _silent_e, exc_info=False)
        except Exception as e:
            logger.warning(f"Context compress skipped: {e}")
            compress_meta = {}

        # 7. RAG + Wiki + 实体：按场景 injection_tier 动态控制
        strengthen_rag = bool(compress_meta.get("compressed")) or (
            total_tokens > int(getattr(settings, "context_window", 128_000) or 128_000) * 0.55
        )
        # audit-fix(#12)：压缩(step 6.5)发生在注入之前——注入本身可能把上下文
        # 重新顶超阈值。注入完成后补一次 token 估算，超阈值则按 RAG→wiki→entity
        # 顺序递减条数重注入，直至达标或条数为 0（仅超预算时付出重注入代价）。
        _rag_k = int(inject_opts.get("rag_top_k") or 3)
        _wiki_lim = int(inject_opts.get("wiki_limit") or 4)
        _ent_lim = int(inject_opts.get("entity_limit") or 3)
        try:
            from backend.agent.context_compress import estimate_msgs_tokens as _est_inj
            from backend.agent.context_engine import COMPRESS_THRESHOLD as _INJ_THR

            _inj_budget = int(
                int(getattr(settings, "context_window", 128_000) or 128_000)
                * float(
                    getattr(settings, "context_threshold_percent", _INJ_THR)
                    or _INJ_THR
                )
            )
        except Exception:
            _inj_budget = 0  # 估算不可用则保持旧行为（注入一次，不补估算）
        _msgs_base = [dict(m) for m in messages]
        for _inj_try in range(10):
            messages = [dict(m) for m in _msgs_base]
            if inject_opts.get("rag") and _rag_k > 0:
                messages = await self._inject_rag_context(
                    messages,
                    enriched_input,
                    top_k=_rag_k,
                    strengthen=strengthen_rag and scene_plan.injection_tier == "rich",
                    min_score=float(inject_opts.get("rag_min_score") or 0.58),
                )
            else:
                logger.debug("RAG skipped tier=%s", scene_plan.injection_tier)
            if inject_opts.get("wiki") and _wiki_lim > 0:
                messages = await self._inject_wiki_context(
                    messages,
                    enriched_input,
                    limit=_wiki_lim,
                    min_score=float(inject_opts.get("wiki_min_score") or 0.2),
                )
            else:
                logger.debug("Wiki skipped tier=%s", scene_plan.injection_tier)
            if inject_opts.get("entity") and _ent_lim > 0:
                try:
                    from backend.services.entity_service import get_entity_service
                    es = get_entity_service()
                    recalled = await es.recall(
                        user_input,
                        user_id=self.user_id,
                        limit=_ent_lim,
                    )
                    if recalled:
                        ctx = es.format_recall_context(recalled)
                        if ctx:
                            self._append_to_system(messages, ctx)
                except Exception as e:
                    logger.debug("entity recall skipped: %s", e)
            else:
                logger.debug("entity skipped tier=%s", scene_plan.injection_tier)
            # 注入后补估算：未超预算 / 估算不可用 → 直接用当前结果
            _inj_over = False
            if _inj_budget:
                try:
                    _inj_over = _est_inj(messages) > _inj_budget
                except Exception:
                    _inj_over = False
            if not _inj_over:
                break
            if _rag_k > 0:
                _rag_k //= 2
            elif _wiki_lim > 0:
                _wiki_lim //= 2
            elif _ent_lim > 0:
                _ent_lim //= 2
            else:
                break
            logger.info(
                "post-inject over budget, shrink rag_k=%s wiki=%s entity=%s session=%s",
                _rag_k, _wiki_lim, _ent_lim, session_id,
            )

        # 8. Agent Loop
        final_content = ""
        _sft_tools: list = []  # SFT usage log buffer
        accumulated_content = ""
        accumulated_reasoning = ""
        goal_nudge_count = 0

        # 透明化轨迹收集
        _trace_thinking_steps: list[dict] = []
        _trace_tool_calls: list[dict] = []
        _trace_rag_sources: list[dict] = []
        _trace_start_time = __import__("time").monotonic()

        # 实体提取（异步后台，不阻塞主流程）
        try:
            from backend.services.entity_service import get_entity_service
            _es = get_entity_service()
            _extracted = await _es.extract_from_text(
                user_input, user_id=self.user_id, session_id=session_id
            )
            if _extracted:
                await _es.save_entities(_extracted, user_id=self.user_id, session_id=session_id)
        except Exception as e:
            logger.debug("entity extraction skipped: %s", e)

        # 分段预算：单段 max_iterations，可自动续多段（Goal / 长任务）
        _auto_cont = bool(getattr(settings, "agent_auto_continue", True))
        _max_seg = int(getattr(settings, "agent_auto_continue_max_segments", 5) or 1)
        if not _auto_cont:
            _max_seg = 1
        _seg_size = max(1, int(self.max_iterations))
        _total_iters = _seg_size * max(1, _max_seg)
        _checkpoint_every = int(getattr(settings, "agent_checkpoint_every", 5) or 5)
        _l1_every = int(getattr(settings, "agent_midloop_l1_every", 3) or 3)
        _tool_rounds = 0
        _last_tool_round_count = 0
        _timid_read_streak = 0
        _timid_write_streak = 0
        _thrash_streak = 0
        _last_tool_fingerprint = ""
        _last_tool_name_sig = ""
        _alternate_thrash_streak = 0
        # audit-fix(#5)：同一工具名连续失败熔断计数（跨工具轮保持）
        _last_failed_tool = ""
        _same_tool_fail_streak = 0
        # write-intent / timeout：必须跨工具轮保持，否则 explore_only 永远=1、软提示不触发
        _timeout_fail_streak = 0
        _explore_only_streak = 0
        _write_intent_hard_nudge = False
        _rust_diag_streak = 0
        # New top-level run: drop leftover session cargo_fix arm so review
        # turns do not inherit write-gate from a prior bg cargo failure.
        try:
            from backend.agent.progress_guard import consume_session_cargo_fix

            consume_session_cargo_fix(str(session_id))
        except Exception:
            pass
        _deliver_mode = False
        _pure_read_streak = 0
        _rounds_since_manage_goal = 0
        _rounds_since_write = 0
        _result_load_same_streak = 0
        _last_result_handle = ""
        _cargo_fix_streak = 0
        _must_write_before_cargo = False
        _cargo_error_paths = ""
        _completion_followups = 0
        _tools_used_run: list[str] = []
        self._reactive_compact_used = False
        self._goal_complete_summary_nudged = False
        _multi_source_pending = False
        _suppress_content_stream = False
        _segment = 0
        _empty_reply_retries = 0
        _empty_reply_max = int(getattr(settings, "agent_empty_reply_retries", 2) or 2)
        _tool_repeat_guard = ToolRepeatGuard(
            max_repeat=int(getattr(settings, "agent_tool_repeat_max", 3) or 3)
        )
        _force_final_no_tools = False
        # audit-fix(#13)：Goal 停滞检测——连续 3 轮无新工具调用且 goal 状态
        # 无变化则 force_final，避免 goal 模式空转烧满 100 轮预算
        _goal_stall_rounds = 0
        _goal_last_sig: str | None = None
        _iter_budget = IterationBudget(_total_iters)
        # P0.5：同步迭代预算到 Rust policy（权威侧可观测 / 跨重启策略）
        _kpid_iter = str(getattr(getattr(self, "_kernel_process", None), "id", "") or "")
        if _kpid_iter:
            try:
                from backend.kernel import get_kernel

                _k_iter = get_kernel()
                if hasattr(_k_iter, "iteration_set_budget"):
                    _k_iter.iteration_set_budget(_kpid_iter, _total_iters)
                elif hasattr(_k_iter, "_call"):
                    await _k_iter._acall(
                        "iteration_set_budget",
                        {"process_id": _kpid_iter, "max_total": _total_iters},
                    )
            except Exception as _silent_e:
                logger.debug("suppressed: %s", _silent_e, exc_info=False)
        _turn_retry = TurnRetryState()
        _budget_grace_call = False
        _loop_exit_reason = ""
        _snapshot_every = max(
            1, int(getattr(settings, "agent_process_snapshot_every", 10) or 10)
        )

        for _global_iter in range(_total_iters + 1):  # +1 允许 grace 终答
            # P0 control_inbox：用户 steer 在下一安全边界注入（短 controller note）
            try:
                from backend.agent.control_inbox import (
                    format_steer_block,
                    get_inbox,
                )

                _box = get_inbox(session_id)
                _steers = _box.claim_steers()
                if _steers:
                    _block = format_steer_block(_steers)
                    if _block:
                        messages.append({"role": "user", "content": _block})
                        await self._push_status(
                            session_id,
                            "thinking",
                            f"Applying user steer ({len(_steers)})…",
                        )
                        logger.info(
                            "steer injected n=%s session=%s iter=%s",
                            len(_steers),
                            str(session_id)[:8],
                            _global_iter,
                        )
                    # ack only after successful inject (crash between claim→ack re-delivers)
                    _box.ack_claimed()
            except Exception as _steer_e:
                logger.debug("steer inject skip: %s", _steer_e)

            # Coding loop: phase tick + soft controller nudge + live phase events
            try:
                from backend.agent.coding_loop import (
                    controller_nudge,
                    phase_label,
                    take_phase_event,
                    tick_iteration,
                )
                from backend.agent.run_events import emit_run_event as _emit_ph

                tick_iteration(session_id)
                _pev = take_phase_event(session_id)
                if _pev:
                    await _emit_ph(
                        self.ws_manager,
                        session_id,
                        "coding.phase",
                        detail=phase_label(_pev.get("phase") or ""),
                        payload=_pev,
                    )
                _nudge = controller_nudge(session_id)
                if _nudge:
                    messages.append({"role": "system", "content": _nudge})
            except Exception as _cln_e:
                logger.debug("coding_loop nudge skip: %s", _cln_e)

            # ── Kernel 仲裁点（Phase 2）：挂起等待 / 事前预算 / 调度让出。
            # 放在预算 consume 之前——挂起等待不该消耗 iteration 配额。
            _gate = await self._kernel_iteration_gate(session_id, messages)
            if _gate == "stop":
                _loop_exit_reason = "kernel_gate_stop"
                break
            if _gate == "budget":
                _loop_exit_reason = "kernel_budget_precheck"
                from backend.agent.exit_reasons import format_exit_user_message

                final_content = format_exit_user_message(
                    "kernel_budget_precheck",
                    process_id=_kpid_iter or None,
                )
                await self._push_status(
                    session_id,
                    "thinking",
                    "Token 预算不足，运行已事前中断（可 top_up / 缩小范围后重试）",
                )
                break
            # PR4: Rust loop_guard budget ratio (85%) → force final before hard kill
            if (
                not _force_final_no_tools
                and bool(getattr(settings, "agent_loop_guard_enabled", True))
                and _kpid_iter
            ):
                try:
                    from backend.agent.loop_guard_bridge import (
                        budget_check,
                        force_final_message,
                    )

                    _bc = await asyncio.to_thread(budget_check, _kpid_iter)  # audit-fix
                    if isinstance(_bc, dict) and _bc.get("status") == "force_final":
                        _force_final_no_tools = True
                        _ff_code = str(_bc.get("code") or "budget_ratio")
                        _loop_exit_reason = _ff_code
                        try:
                            self.last_exit_reason = _ff_code
                        except Exception:
                            pass
                        messages.append(
                            {
                                "role": "system",
                                "content": force_final_message(
                                    _ff_code,
                                    str(_bc.get("reason") or ""),
                                ),
                            }
                        )
                        # UI status: only true token ratio uses 额度措辞；勿与工具轮混淆
                        _st = (
                            "Token 额度将尽，本轮交卷中…"
                            if _ff_code in ("budget_ratio",)
                            else "本段工具轮用尽，交卷中…"
                        )
                        await self._push_status(
                            session_id,
                            "thinking",
                            _st,
                        )
                        logger.warning(
                            "loop_guard budget force_final process=%s %s",
                            _kpid_iter[:8],
                            _bc,
                        )
                except Exception as _bge:
                    logger.debug("loop_guard budget_check skip: %s", _bge)
            # 迭代预算：耗尽后最多 1 次 grace（强制无工具终答）
            if _global_iter >= _total_iters:
                if _budget_grace_call or _force_final_no_tools:
                    break
                _budget_grace_call = True
                _force_final_no_tools = True
                _loop_exit_reason = "budget_grace"
                await self._push_status(
                    session_id,
                    "thinking",
                    f"迭代预算已用尽 ({_iter_budget.used}/{_iter_budget.max_total})，"
                    "宽限终答中…（可调高 agent_max_iterations 或拆任务）",
                )
                logger.info(
                    "Iteration budget grace session=%s used=%s",
                    session_id,
                    _iter_budget.snapshot(),
                )
            elif not _iter_budget.consume():
                _loop_exit_reason = "budget_exhausted"
                break
            # P0.5：Rust 侧同步 consume；耗尽则优雅退出文案
            if _kpid_iter and not _budget_grace_call:
                try:
                    from backend.kernel import get_kernel

                    _k_iter = get_kernel()
                    if hasattr(_k_iter, "iteration_consume"):
                        _ic = _k_iter.iteration_consume(_kpid_iter)
                    elif hasattr(_k_iter, "_call"):
                        _ic = await _k_iter._acall(
                            "iteration_consume", {"process_id": _kpid_iter}
                        )
                    else:
                        _ic = None
                    if isinstance(_ic, dict) and _ic.get("status") == "exhausted":
                        _loop_exit_reason = "kernel_iteration_exhausted"
                        _budget_grace_call = True
                        _force_final_no_tools = True
                        await self._push_status(
                            session_id,
                            "thinking",
                            "内核迭代预算已耗尽，宽限终答中…（见 /kernel/policy）",
                        )
                except Exception as _silent_e:
                    logger.debug("suppressed: %s", _silent_e, exc_info=False)
            # P0.5：周期 process snapshot（恢复路径 = 快照 + tail_hash 增量）
            if (
                _kpid_iter
                and _global_iter > 0
                and _global_iter % _snapshot_every == 0
            ):
                try:
                    from backend.kernel import get_kernel

                    _k_snap = get_kernel()
                    if hasattr(_k_snap, "process_snapshot"):
                        _k_snap.process_snapshot(
                            _kpid_iter,
                            meta={
                                "iter": _global_iter,
                                "session_id": str(session_id),
                            },
                        )
                    elif hasattr(_k_snap, "_call"):
                        await _k_snap._acall(
                            "process_snapshot",
                            {
                                "process_id": _kpid_iter,
                                "meta": {
                                    "iter": _global_iter,
                                    "session_id": str(session_id),
                                },
                            },
                        )
                except Exception as _snap_e:
                    logger.debug("process_snapshot skip: %s", _snap_e)

            iteration = _global_iter % _seg_size if _seg_size else _global_iter
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
                    # Thrash/doom 段结束：勿自动开下一段（防空转续跑刷屏）
                    try:
                        if bool(
                            getattr(settings, "agent_no_autoresume_on_thrash", True)
                        ):
                            from backend.agent.goal_facade import is_thrash_exit_reason

                            _ex = str(
                                getattr(self, "last_exit_reason", "")
                                or _loop_exit_reason
                                or ""
                            )
                            if is_thrash_exit_reason(_ex):
                                logger.info(
                                    "skip auto-continue segment (thrash exit=%s) session=%s",
                                    _ex,
                                    session_id,
                                )
                                break
                    except Exception:
                        pass
                    await save_checkpoint(
                        session_id,
                        segment=_segment,
                        iteration=_global_iter,
                        mode=mode,
                        note="auto-continue segment boundary",
                        extra={"goal_complete": bool(g_chk and g_chk.is_complete())},
                        run_id=str(_rc.run_id) if _rc is not None and _rc.run_id else None,
                    )
                    if goal_mode:
                        await save_goal_to_db(session_id)
                    await self._push_status(
                        session_id,
                        "thinking",
                        f"自动续跑第 {_segment + 1}/{_max_seg} 段…",
                    )
                    # Reset LoopGuard tool_rounds for the new segment (configure
                    # replaces GuardState with tool_rounds=0). Prevents
                    # max_tool_rounds=40 blocking file_write on seg 2+.
                    try:
                        if (
                            bool(getattr(settings, "agent_loop_guard_enabled", True))
                            and _kpid_iter
                        ):
                            from backend.agent.loop_guard_bridge import (
                                build_loop_guard_config,
                                configure_for_process,
                            )

                            _cfg2 = build_loop_guard_config(
                                workforce=bool(
                                    getattr(self, "_workforce", False)
                                ),
                                identity_name=str(
                                    getattr(self, "_identity_name", None) or ""
                                ),
                                identity_role=None,
                                instruction=str(user_input or "")[:2000],
                                payload=None,
                            )
                            _cfg2["role_kind"] = "steward"
                            _cfg2["max_tool_rounds"] = max(
                                int(_cfg2.get("max_tool_rounds") or 40),
                                int(_seg_size) * max(1, int(_max_seg)),
                            )
                            await asyncio.to_thread(
                                configure_for_process, str(_kpid_iter), _cfg2
                            )
                            logger.info(
                                "loop_guard reset for segment=%s max_rounds=%s process=%s",
                                _segment,
                                _cfg2.get("max_tool_rounds"),
                                str(_kpid_iter)[:8],
                            )
                    except Exception as _lg_reset_e:
                        logger.debug("loop_guard segment reset skip: %s", _lg_reset_e)
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "[System auto-resume] Previous segment exhausted; continue from checkpoint. "
                                "Do not redo finished work. "
                                "Avoid spam process poll, retrying the same Blocked command, "
                                "and whole-file grep.\n"
                                "NEXT: edit error paths → one cargo check → manage_goal. "
                                "Reply to the user in their language."
                                + (
                                    "\n" + g_chk.summary_for_llm()
                                    if g_chk and not g_chk.is_complete()
                                    else ""
                                )
                            ),
                        }
                    )
                except Exception as e:
                    logger.warning("auto-continue segment setup failed: %s", e)

            # 停止信号检查
            if self._should_stop:
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

            message_id = uuid.uuid4()
            logger.info(
                f"Iteration {iteration + 1}/{_seg_size} (seg {_segment + 1}, global {_global_iter + 1}/{_total_iters}) for session {session_id}"
            )
            try:
                _rc = getattr(self, "_run_recorder", None)
                if _rc is not None:
                    _rc.bump_iteration(1)
            except Exception as _silent_e:
                logger.debug("suppressed: %s", _silent_e, exc_info=False)

            # 更新状态：thinking
            await self._push_status(
                session_id, "thinking", f"思考中 · 第 {iteration + 1} 轮"
            )

            # 调用 LLM（流式，phases/llm_round）
            from backend.agent.phases.llm_round import run_llm_round
            # Every sample: pair-complete history (timeout/cancel/compress orphans)
            try:
                from backend.agent.history_normalize import normalize_history_for_llm

                messages = normalize_history_for_llm(messages)
            except Exception as _nh2:
                logger.debug("mid-loop normalize_history skip: %s", _nh2)

            # BG complete proactive inject (Claude-style: harness delivers results)
            try:
                from backend.agent.progress_guard import apply_bg_completions_to_messages

                _n_bg = apply_bg_completions_to_messages(
                    str(session_id), messages, max_n=8
                )
                if _n_bg:
                    await self._push_status(
                        session_id,
                        "thinking",
                        f"后台任务完成 ×{_n_bg}，已注入结果…",
                    )
                    logger.info(
                        "bg_complete inject n=%s session=%s",
                        _n_bg,
                        str(session_id)[:8],
                    )
            except Exception as _bg_inj:
                logger.debug("bg_complete inject skip: %s", _bg_inj)

            _lr = await run_llm_round(
                self,
                session_id=session_id,
                iteration=iteration,
                messages=messages,
                tools=tools,
                llm_service=llm_service,
                message_id=message_id,
                force_final_no_tools=_force_final_no_tools,
                suppress_content_stream=_suppress_content_stream,
                final_content=final_content,
                turn_retry=_turn_retry,
                trace_thinking_steps=_trace_thinking_steps,
            )
            messages = _lr.messages
            accumulated_content = _lr.accumulated_content
            accumulated_reasoning = getattr(_lr, "accumulated_reasoning", "") or ""
            tool_calls = _lr.tool_calls
            if _lr.force_final_no_tools is not None:
                _force_final_no_tools = _lr.force_final_no_tools
            # audit-fix(#13)：Goal 停滞检测（保守：仅 goal 模式、仅计数不重构流程）
            if goal_mode and not _force_final_no_tools:
                try:
                    from backend.agent.goal_state import get_goal as _goal_get

                    _g = _goal_get(session_id)
                    _sig = ""
                    if _g is not None:
                        _sig = str(_g.status) + "|" + ",".join(
                            f"{t.id}:{t.status}" for t in (_g.todos or [])
                        )
                    if not tool_calls and _goal_last_sig is not None and _sig == _goal_last_sig:
                        _goal_stall_rounds += 1
                    else:
                        _goal_stall_rounds = 0
                    _goal_last_sig = _sig
                    if _goal_stall_rounds >= 3:
                        try:
                            from backend.agent.progress_guard import soft_open_mode as _so_gs
                            from backend.core.config import settings as _st_gs

                            _hard_gs = (
                                not _so_gs()
                                and bool(
                                    getattr(_st_gs, "agent_goal_stall_force_final", True)
                                )
                            )
                        except Exception:
                            _hard_gs = True
                        if _hard_gs:
                            _force_final_no_tools = True
                            _loop_exit_reason = "goal_stalled"
                            messages.append(
                                {
                                    "role": "system",
                                    "content": (
                                        "[Goal stall] 3 turns with no tools and no goal change. "
                                        "No tools this turn: short blocker in the user's language; "
                                        "no long inventories."
                                    ),
                                }
                            )
                        else:
                            # Soft-open: remind only; do not hard-stop the model
                            messages.append(
                                {
                                    "role": "system",
                                    "content": (
                                        "[Converge] Goal has no tool progress for several turns. "
                                        "Prefer manage_goal updates or file_write/edit + verify; "
                                        "tools still allowed. Reply to the user in their language."
                                    ),
                                }
                            )
                            _goal_stall_rounds = 0  # re-arm after soft nudge
                        logger.warning(
                            "goal stall %s rounds=%s session=%s",
                            "force_final" if _hard_gs else "soft",
                            _goal_stall_rounds if _hard_gs else 3,
                            session_id,
                        )
                except Exception as _gs_e:
                    logger.debug("goal stall check skipped: %s", _gs_e)
            if _lr.action == "continue":
                continue
            if _lr.action == "break":
                from backend.agent.thinking_format import (
                    canonicalize_thinking,
                    ensure_user_facing_final,
                    sanitize_force_final_body,
                )

                final_content = canonicalize_thinking(
                    accumulated_reasoning, _lr.final_content or accumulated_content
                )
                if _force_final_no_tools:
                    # Keep real user summaries; only collapse scare inventories.
                    # prefer_short=goal_mode used to wipe segment progress to a one-liner.
                    final_content = sanitize_force_final_body(
                        final_content,
                        goal_mode=goal_mode,
                        exit_code=_loop_exit_reason
                        or str(getattr(self, "last_exit_reason", "") or ""),
                        prefer_short=False,
                    )
                try:
                    _gs = ""
                    from backend.agent.goal_state import get_goal as _ggx

                    _gx = _ggx(session_id)
                    if (
                        goal_mode
                        and _gx is not None
                        and not _gx.is_complete()
                        and str(getattr(_gx, "status", "") or "") == "active"
                    ):
                        _gs = _gx.summary_for_llm()
                    final_content = ensure_user_facing_final(
                        final_content,
                        user_input=str(user_input or enriched_input or ""),
                        messages=list(messages or []),
                        exit_reason=_loop_exit_reason
                        or str(getattr(self, "last_exit_reason", "") or ""),
                        goal_summary=_gs,
                        tool_rounds=int(_tool_rounds or 0),
                        goal_mode=goal_mode,
                    )
                except Exception:
                    pass
                break

            # 判断是否有 tool calls
            if tool_calls:
                # 将 assistant 的回复（含 tool calls）追加到 messages
                # content 用 None 兼容部分严格 API（空字符串 + tool_calls 会被拒）
                # LLM 上下文只带可见正文；UI 持久化可附带 <thinking>
                # DeepSeek V4 thinking+tools：必须回传 reasoning_content（见官方 thinking_mode 文档）
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": accumulated_content if accumulated_content else None,
                }
                _rc = (accumulated_reasoning or "").strip()
                if _rc:
                    assistant_msg["reasoning_content"] = _rc
                assistant_tool_calls = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False)
                            if not isinstance(tc.arguments, str)
                            else tc.arguments,
                        },
                    }
                    for tc in tool_calls
                ]
                assistant_msg["tool_calls"] = assistant_tool_calls
                messages.append(assistant_msg)

                # 持久化中间 assistant（含 tool_calls + 思考块），便于跨轮续跑与 UI 回放
                try:
                    from backend.agent.thinking_format import canonicalize_thinking

                    await self._save_message(
                        session_id,
                        "assistant",
                        canonicalize_thinking(
                            accumulated_reasoning, accumulated_content or ""
                        ),
                        tool_calls=assistant_tool_calls,
                    )
                except Exception as e:
                    msg = str(e)
                    if "FOREIGN KEY" in msg or "IntegrityError" in msg:
                        logger.warning(
                            "Session gone (FK) — stop agent run session=%s: %s",
                            session_id,
                            e,
                        )
                        # 会话已删：尽快停跑，避免幽灵 run 烧预算
                        self._should_stop = True
                        try:
                            from backend.api.websocket import manager as ws_manager

                            ws_manager.end_run_snapshot(session_id)
                            await ws_manager.broadcast(
                                session_id,
                                {
                                    "type": "status",
                                    "state": "idle",
                                    "detail": "Session deleted — run stopped",
                                },
                            )
                        except Exception as _silent_e:
                            logger.debug("suppressed: %s", _silent_e, exc_info=False)
                        break
                    else:
                        logger.warning(f"Failed to persist assistant tool_calls message: {e}")

                # 执行工具轮（phases/tool_round；行为冻结 tests/test_loop_freeze.py）
                from backend.agent.phases.tool_round import (
                    ToolRoundState,
                    run_tool_round,
                )

                _tr_state = ToolRoundState(
                    messages=messages,
                    tools_used_run=_tools_used_run,
                    sft_tools=_sft_tools,
                    trace_tool_calls=_trace_tool_calls,
                    scene_plan=scene_plan,
                    tools=tools,
                    enabled_tools_filter=enabled_tools_filter,
                    force_final_no_tools=_force_final_no_tools,
                    suppress_content_stream=_suppress_content_stream,
                    multi_source_pending=_multi_source_pending,
                    timid_read_streak=_timid_read_streak,
                    timid_write_streak=_timid_write_streak,
                    tool_rounds=_tool_rounds,
                    last_tool_round_count=_last_tool_round_count,
                    thrash_streak=_thrash_streak,
                    last_tool_fingerprint=_last_tool_fingerprint,
                    last_tool_name_sig=_last_tool_name_sig,
                    alternate_thrash_streak=_alternate_thrash_streak,
                    last_failed_tool=_last_failed_tool,
                    same_tool_fail_streak=_same_tool_fail_streak,
                    timeout_fail_streak=_timeout_fail_streak,
                    explore_only_streak=_explore_only_streak,
                    write_intent_hard_nudge=_write_intent_hard_nudge,
                    rust_diag_streak=_rust_diag_streak,
                    deliver_mode=_deliver_mode,
                    pure_read_streak=_pure_read_streak,
                    rounds_since_manage_goal=_rounds_since_manage_goal,
                    rounds_since_write=_rounds_since_write,
                    result_load_same_streak=_result_load_same_streak,
                    last_result_handle=_last_result_handle,
                    cargo_fix_streak=_cargo_fix_streak,
                    must_write_before_cargo=_must_write_before_cargo,
                    cargo_error_paths=_cargo_error_paths,
                    simple_turn=_simple_turn,
                )
                await run_tool_round(
                    self,
                    session_id=session_id,
                    mode=mode,
                    iteration=iteration,
                    tool_calls=tool_calls,
                    state=_tr_state,
                    segment=_segment,
                    global_iter=_global_iter,
                    goal_mode=goal_mode,
                    user_input=str(enriched_input or user_input or ""),
                    l1_every=_l1_every,
                    checkpoint_every=_checkpoint_every,
                    turn_retry=_turn_retry,
                    tool_repeat_guard=_tool_repeat_guard,
                    enabled_skills=enabled_skills,
                )
                # 标量/重绑定读回（messages/tools/filter 可能被压缩或扩容重绑）
                messages = _tr_state.messages
                tools = _tr_state.tools
                enabled_tools_filter = _tr_state.enabled_tools_filter
                _force_final_no_tools = _tr_state.force_final_no_tools
                if _force_final_no_tools and not _loop_exit_reason:
                    _loop_exit_reason = str(
                        getattr(self, "last_exit_reason", "") or "max_tool_rounds"
                    )
                _suppress_content_stream = _tr_state.suppress_content_stream
                _multi_source_pending = _tr_state.multi_source_pending
                _timid_read_streak = _tr_state.timid_read_streak
                _timid_write_streak = _tr_state.timid_write_streak
                _tool_rounds = _tr_state.tool_rounds
                _last_tool_round_count = _tr_state.last_tool_round_count
                _thrash_streak = _tr_state.thrash_streak
                _last_tool_fingerprint = _tr_state.last_tool_fingerprint
                _last_tool_name_sig = str(
                    getattr(_tr_state, "last_tool_name_sig", "") or ""
                )
                _alternate_thrash_streak = int(
                    getattr(_tr_state, "alternate_thrash_streak", 0) or 0
                )
                _last_failed_tool = _tr_state.last_failed_tool
                _same_tool_fail_streak = _tr_state.same_tool_fail_streak
                _timeout_fail_streak = int(
                    getattr(_tr_state, "timeout_fail_streak", 0) or 0
                )
                _explore_only_streak = int(
                    getattr(_tr_state, "explore_only_streak", 0) or 0
                )
                _write_intent_hard_nudge = bool(
                    getattr(_tr_state, "write_intent_hard_nudge", False)
                )
                _rust_diag_streak = int(
                    getattr(_tr_state, "rust_diag_streak", 0) or 0
                )
                _deliver_mode = bool(getattr(_tr_state, "deliver_mode", False))
                _pure_read_streak = int(
                    getattr(_tr_state, "pure_read_streak", 0) or 0
                )
                _rounds_since_manage_goal = int(
                    getattr(_tr_state, "rounds_since_manage_goal", 0) or 0
                )
                _rounds_since_write = int(
                    getattr(_tr_state, "rounds_since_write", 0) or 0
                )
                _result_load_same_streak = int(
                    getattr(_tr_state, "result_load_same_streak", 0) or 0
                )
                _last_result_handle = str(
                    getattr(_tr_state, "last_result_handle", "") or ""
                )
                _cargo_fix_streak = int(
                    getattr(_tr_state, "cargo_fix_streak", 0) or 0
                )
                _must_write_before_cargo = bool(
                    getattr(_tr_state, "must_write_before_cargo", False)
                )
                _cargo_error_paths = str(
                    getattr(_tr_state, "cargo_error_paths", "") or ""
                )
                continue

            else:
                # 没有 tool calls（phases/no_tool_round）
                from backend.agent.phases.no_tool_round import run_no_tool_round

                _nr = await run_no_tool_round(
                    self,
                    session_id=session_id,
                    iteration=iteration,
                    seg_size=_seg_size,
                    messages=messages,
                    accumulated_content=accumulated_content,
                    accumulated_reasoning=accumulated_reasoning,
                    goal_mode=goal_mode,
                    goal_nudge_count=goal_nudge_count,
                    turn_retry=_turn_retry,
                    empty_reply_max=_empty_reply_max,
                    last_tool_round_count=_last_tool_round_count,
                    force_final_no_tools=_force_final_no_tools,
                    user_input=user_input,
                    enriched_input=enriched_input,
                    tools_used_run=_tools_used_run,
                    completion_followups=_completion_followups,
                )
                goal_nudge_count = _nr.goal_nudge_count
                _completion_followups = _nr.completion_followups
                if _nr.force_final_no_tools is not None:
                    _force_final_no_tools = _nr.force_final_no_tools
                if _nr.action == "continue":
                    continue
                from backend.agent.thinking_format import (
                    canonicalize_thinking,
                    ensure_user_facing_final,
                    sanitize_force_final_body,
                )

                # 最终答复附带本轮 reasoning，UI 折叠展示；逻辑层 empty 检查仍用 body
                final_content = canonicalize_thinking(
                    accumulated_reasoning, _nr.final_content or accumulated_content
                )
                if _force_final_no_tools:
                    # Keep real user summaries; only collapse scare inventories.
                    # prefer_short=goal_mode used to wipe segment progress to a one-liner.
                    final_content = sanitize_force_final_body(
                        final_content,
                        goal_mode=goal_mode,
                        exit_code=_loop_exit_reason
                        or str(getattr(self, "last_exit_reason", "") or ""),
                        prefer_short=False,
                    )
                try:
                    _gs2 = ""
                    from backend.agent.goal_state import get_goal as _ggx2

                    _gx2 = _ggx2(session_id)
                    if (
                        goal_mode
                        and _gx2 is not None
                        and not _gx2.is_complete()
                        and str(getattr(_gx2, "status", "") or "") == "active"
                    ):
                        _gs2 = _gx2.summary_for_llm()
                    final_content = ensure_user_facing_final(
                        final_content,
                        user_input=str(user_input or enriched_input or ""),
                        messages=list(messages or []),
                        exit_reason=_loop_exit_reason
                        or str(getattr(self, "last_exit_reason", "") or ""),
                        goal_summary=_gs2,
                        tool_rounds=int(_tool_rounds or 0),
                        goal_mode=goal_mode,
                    )
                except Exception:
                    pass
                break
        else:
            # 用尽全部分段预算（工具/迭代段，不是 token 预算）
            logger.warning(
                "Max iteration budget (%s segs x %s) reached for session %s",
                _max_seg,
                _seg_size,
                session_id,
            )
            from backend.agent.thinking_format import (
                canonicalize_thinking,
                short_segment_handoff_message,
            )

            from backend.agent.thinking_format import ensure_user_facing_final

            _raw_end = accumulated_content or (
                short_segment_handoff_message(goal_mode=goal_mode)
                + f"（{_max_seg}×{_seg_size} 段上限）"
            )
            _gsum_end = ""
            try:
                from backend.agent.goal_state import get_goal as _gg_end

                _ge = _gg_end(session_id)
                if (
                    goal_mode
                    and _ge is not None
                    and not _ge.is_complete()
                    and str(getattr(_ge, "status", "") or "") == "active"
                ):
                    _gsum_end = _ge.summary_for_llm()
            except Exception:
                pass
            _raw_end = ensure_user_facing_final(
                _raw_end,
                user_input=str(user_input or enriched_input or ""),
                messages=list(messages or []),
                exit_reason="max_segment_budget",
                goal_summary=_gsum_end,
                tool_rounds=int(_tool_rounds or 0),
                goal_mode=goal_mode,
            )
            final_content = canonicalize_thinking(
                accumulated_reasoning,
                _raw_end,
            )
            if goal_mode:
                from backend.agent.goal_state import get_goal, save_goal_to_db

                g = get_goal(session_id)
                if g and not g.is_complete():
                    # 不再把完整 goal summary 拼进 assistant 正文（续跑 prompt 会带）
                    try:
                        await save_goal_to_db(session_id)
                        from backend.agent.checkpoint import save_checkpoint

                        await save_checkpoint(
                            session_id,
                            segment=_segment,
                            iteration=_total_iters,
                            mode=mode,
                            note="segment_tool_rounds_exhausted",
                            run_id=str(_rc.run_id) if _rc is not None and _rc.run_id else None,
                        )
                    except Exception as _silent_e:
                        logger.debug("suppressed: %s", _silent_e, exc_info=False)

        # Stash messages so epilogue can synthesize a user summary if body empty
        try:
            self._last_messages_for_summary = list(messages or [])
        except Exception:
            self._last_messages_for_summary = None

        # 收尾聚合（phases/epilogue；行为冻结 tests/test_loop_freeze.py）
        from backend.agent.phases.epilogue import run_epilogue

        final_content = await run_epilogue(
            self,
            session_id=session_id,
            final_content=final_content,
            goal_mode=goal_mode,
            llm_service=llm_service,
            user_input=user_input,
            tool_rounds=_tool_rounds,
            last_tool_round_count=_last_tool_round_count,
            multi_source_pending=_multi_source_pending,
            suppress_content_stream=_suppress_content_stream,
            sft_tools=_sft_tools,
            trace_start_time=_trace_start_time,
            global_iter=_global_iter,
            trace_thinking_steps=_trace_thinking_steps,
            trace_tool_calls=_trace_tool_calls,
            trace_rag_sources=_trace_rag_sources,
            ws_reset=_ws_reset,
        )
        # 本轮实际用掉的迭代/工具轮次：bench harness 与运行诊断都需要，
        # 不暴露就只能靠翻日志反推。
        self.last_iterations = _global_iter + 1
        self.last_tool_rounds = _tool_rounds
        # 不覆盖 llm_round 等已写入的精确退出码（如 llm_stream_error）
        # 仅当尚无精确码 / 仍是 completed 占位时，才用循环级原因覆盖
        _cur = getattr(self, "last_exit_reason", None)
        if not _cur or _cur in ("", "completed", None):
            self.last_exit_reason = _loop_exit_reason or "completed"
        # 已有精确码（llm_stream_error 等）→ 保留，不二次覆盖
        # P0.5 R4：结构化退出说明挂到 loop，供 API / harness
        try:
            from backend.agent.exit_reasons import describe_exit_reason

            self.last_exit_detail = describe_exit_reason(self.last_exit_reason)
            self.last_exit_detail["process_id"] = _kpid_iter or None
        except Exception:
            self.last_exit_detail = {"code": self.last_exit_reason}
        # 非正常完成时把恢复提示并入 final（避免静默）
        if (
            final_content
            and self.last_exit_reason
            and self.last_exit_reason
            not in ("", "completed")
            and "[Budget" not in (final_content or "")[:40]
            and "迭代预算" not in (final_content or "")[:80]
            and self.last_exit_reason
            in (
                "budget_grace",
                "budget_exhausted",
                "kernel_iteration_exhausted",
                "kernel_budget_precheck",
                "doom_loop",
                "thrash",
            )
            and not (
                self.last_exit_reason in ("doom_loop", "thrash")
                and len((final_content or "").strip()) >= 80
                and not (final_content or "").strip().startswith("正在")
            )
        ):
            try:
                from backend.agent.exit_reasons import format_exit_user_message

                note = format_exit_user_message(
                    self.last_exit_reason, process_id=_kpid_iter or None
                )
                if note and note not in final_content:
                    final_content = f"{final_content.rstrip()}\n\n——\n{note}"
            except Exception as _silent_e:
                logger.debug("suppressed: %s", _silent_e, exc_info=False)
        return final_content

    # ─────────── P0 helpers ───────────
