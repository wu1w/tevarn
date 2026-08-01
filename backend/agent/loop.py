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

logger = logging.getLogger(__name__)


def _sanitize_tool_error(tool_name: str, exc: Exception) -> str:
    """工具错误脱敏 + 下一步建议（Phase 4.3）。

    生产模式不回传 SQL/堆栈；调试模式带详情。
    """
    import os

    if os.environ.get("TAKTON_DEBUG", "").lower() in ("1", "true", "yes"):
        return f"[Error] Failed to execute {tool_name}: {exc}"

    exc_type = type(exc).__name__
    msg = str(exc or "")[:200].lower()
    hint = _tool_error_next_step(tool_name, exc_type, msg)
    return (
        f"[Error] 工具 {tool_name} 执行失败（{exc_type}）。"
        f"{hint}"
    )


def _tool_error_next_step(tool_name: str, exc_type: str, msg_lower: str) -> str:
    """面向用户的下一步建议（不泄内部路径细节）。"""
    name = (tool_name or "").lower()
    if "permission" in msg_lower or "denied" in msg_lower or "not allowed" in msg_lower:
        return "下一步：检查权限规则/员工能力白名单，或在审批中心放行后重试。"
    if "not found" in msg_lower or "no such file" in msg_lower or "filenotfound" in exc_type.lower():
        return "下一步：用 glob/list 确认路径是否存在，或改用工作区内的相对路径。"
    if "timeout" in msg_lower or "timed out" in msg_lower:
        return "下一步：缩小命令范围、拆分长任务，或提高超时后重试。"
    if "json" in msg_lower or "decode" in msg_lower or "parse" in msg_lower:
        return "下一步：检查参数 JSON 是否合法，字段名是否与工具 schema 一致。"
    if name in ("file_read", "file_write", "edit", "glob", "grep"):
        return "下一步：确认路径在 workspace 内，必要时先 file_read/glob 再编辑。"
    if name in ("command", "run_shell", "bash", "shell", "python"):
        return "下一步：先用只读命令验证环境，避免一次执行过长管道；敏感操作需确认。"
    if "ppt" in name or name == "generate_ppt":
        return "下一步：确认已安装 python-pptx；可先生成大纲 JSON 再导出 pptx。"
    if "network" in msg_lower or "connection" in msg_lower or "http" in name:
        return "下一步：检查网络/URL 是否可达，或改用本地缓存内容。"
    return "下一步：根据工具说明调整参数后重试；仍失败可设 TAKTON_DEBUG=1 查看服务端日志。"


# 安全修复：按 session_id 的并发锁，防止同一 session 的 agent loop 竞态执行
_session_locks: dict[uuid.UUID, asyncio.Lock] = {}
_SESSION_LOCK_MAX = 1024  # 防止内存泄漏：最多保留的锁数量


def _get_session_lock(session_id: uuid.UUID) -> asyncio.Lock:
    """获取 session 级别的执行锁"""
    if session_id not in _session_locks:
        # 清理机制：超过上限时移除最早的锁（已结束的session不会再使用）
        if len(_session_locks) >= _SESSION_LOCK_MAX:
            oldest_key = next(iter(_session_locks))
            del _session_locks[oldest_key]
        _session_locks[session_id] = asyncio.Lock()
    return _session_locks[session_id]


def _remove_session_lock(session_id: uuid.UUID) -> None:
    """Session 结束后清理锁，防止内存泄漏"""
    _session_locks.pop(session_id, None)



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
        agent_name: str = "Takton",
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
        # 长链/编码任务默认允许更多工具轮次；可用 TAKTON_AGENT_MAX_ITERATIONS 覆盖
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
        self._contract_wl_ready = False
        self._contract_whitelist = None
        self._should_stop = False
        self._llm_fail_streak = 0
        self._reactive_compact_used = False

    # ── Batch3 port helpers（优先 message_store / tool_executor）─────────
    async def _save_message(
        self,
        session_id: uuid.UUID,
        role: str,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
        token_count: int | None = None,
    ):
        store = getattr(self, "message_store", None)
        if store is not None:
            return await store.save_message(
                session_id, role, content, tool_calls=tool_calls, token_count=token_count
            )
        return await self._save_message(
            session_id, role, content, tool_calls=tool_calls, token_count=token_count
        )

    async def _load_history(
        self,
        session_id: uuid.UUID,
        limit: int = 100,
        offset: int = 0,
    ):
        store = getattr(self, "message_store", None)
        if store is not None:
            return await store.get_history(session_id, limit=limit, offset=offset)
        return await self._load_history(
            session_id, limit=limit, offset=offset
        )

    async def _await_run_gate(
        self,
        kernel: Any,
        process_id: str,
        *,
        priority_class: str = "workforce",
        timeout: float = 300.0,
    ) -> None:
        """跨会话全局 RunGate — 拿到 lease 再继续执行（T2：可配置 fail-closed）。

        session 锁只保证同 session 串行；本门闩保证全局并发上限 + 优先级排队。
        """
        from backend.core.config import settings as _rg_settings

        required = bool(
            getattr(_rg_settings, "agent_kernel_run_gate_required", True)
        )
        if not hasattr(kernel, "_call"):
            if required:
                raise RuntimeError(
                    "run_gate required but kernel has no _call (host unavailable)"
                )
            return
        try:
            r = kernel._call(
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
                kernel._call(
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
        # queued — poll
        rid = str(r.get("request_id") or "")
        logger.info(
            "run_gate queued proc=%s request=%s qlen=%s",
            process_id[:8],
            rid[:8],
            r.get("queue_len"),
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            if getattr(self, "_should_stop", False):
                raise RuntimeError("run gate wait aborted: stop requested")
            await asyncio.sleep(0.05)
            try:
                polled = kernel._call("run_gate_poll", {"request_id": rid}) or {}
            except Exception as e:
                logger.debug("run_gate_poll: %s", e)
                continue
            st = polled.get("status")
            if st == "granted":
                try:
                    kernel._call(
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
                except Exception:
                    pass

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
                soft_on = (
                    bool(getattr(settings, "agent_budget_soft_renew_enabled", False))
                    and not hard_only
                ) or _interactive or (_is_wf_proc and not _wf_hard and not hard_only)
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
                            await self._push_status(
                                session_id,
                                "thinking",
                                f"预算弹性续航 +{renewed.get('amount')} "
                                f"（第 {renewed.get('renew_count')} 次），继续执行…",
                            )
                        elif _is_wf_proc and not _wf_hard:
                            # soft_renew unavailable — direct top_up for workforce
                            _n = 0
                            if isinstance(_meta_p, dict):
                                _n = int(_meta_p.get("auto_top_up_count") or 0)
                            _max = int(
                                getattr(
                                    settings, "agent_workforce_auto_top_up_max", 4
                                )
                                or 4
                            )
                            if _n < _max:
                                _add = max(
                                    int(estimated - remaining) + 50_000,
                                    int(
                                        getattr(
                                            settings,
                                            "agent_workforce_auto_top_up_min_add",
                                            150_000,
                                        )
                                        or 150_000
                                    ),
                                )
                                _add = min(_add, 500_000)
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
                                    except Exception:
                                        pass
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
        """粗估下一次 LLM 调用消耗：近期上下文输入 + 输出预留。
        字符 /3.4（与 token_meter 口径一致），只看近 20 条——
        更早的上下文会被压缩，全量计入会高估导致误刹车。"""
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
        return max(1, round(chars / 3.4)) + reserve

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
        # 安全修复：获取 session 级锁，防止同一 session 的并发竞态
        lock = _get_session_lock(session_id)
        async with lock:
            return await self._run_inner(session_id, user_input, attachments, mode, sub_agent_ids or [])

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
        # Phase 2.1/2.2：Run id 回写 loop，供 process.meta / inbox 关联
        self._agent_run_id = getattr(recorder, "run_id", None)
        # Phase 2.4：Goal 挂 Run 链
        try:
            from backend.agent.goal_state import bind_goal_run_id

            bind_goal_run_id(session_id, self._agent_run_id)
        except Exception:
            pass

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
                }
                if isinstance(proc_opts.get("meta"), dict):
                    _meta.update(proc_opts["meta"])
                    if self._agent_run_id:
                        _meta["run_id"] = str(self._agent_run_id)
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
                            except Exception:
                                pass
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
                        kernel._call(
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
                            kernel._call(
                                "isolation_set_profile",
                                {
                                    "process_id": kernel_proc.id,
                                    "profile": iso_profile,
                                },
                            )
                        except Exception:
                            pass
                        # 全局 RunGate：跨会话排队，拿到 lease 再继续（session 锁只保同会话）
                        await self._await_run_gate(
                            kernel,
                            kernel_proc.id,
                            priority_class=pclass,
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
                except Exception:
                    pass
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
                            applied = kernel._call(
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
                        # Token/tool surface sync: orchestration tools (crew_steward)
                        # must appear in process.capabilities after profile apply.
                        caps_now = list(getattr(kernel_proc, "capabilities", None) or [])
                        if "crew_steward" not in caps_now:
                            logger.warning(
                                "coding_profile applied but crew_steward missing "
                                "from token process=%s caps=%s applied=%s — "
                                "tool packs will deny orchestration",
                                kernel_proc.id[:8],
                                caps_now[:16],
                                applied,
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
            except Exception:
                pass
            if self._should_stop:
                await recorder.cancel("stopped by user")
            else:
                await recorder.finish_ok(final_summary=result or "")
            if kernel is not None and kernel_proc is not None:
                try:
                    if hasattr(kernel, "_call"):
                        kernel._call(
                            "run_gate_release", {"process_id": kernel_proc.id}
                        )
                        try:
                            kernel._call(
                                "run_release", {"process_id": kernel_proc.id}
                            )
                        except Exception:
                            pass
                except Exception:
                    pass
                await kernel.end_process(
                    kernel_proc.id,
                    state="killed" if self._should_stop else "completed",
                    reason="stopped by user" if self._should_stop else None,
                )
            return result
        except Exception as e:
            try:
                if kernel_proc is not None:
                    recorder.set_token_used(int(getattr(kernel_proc, "tokens_used", 0) or 0))
            except Exception:
                pass
            await recorder.finish_fail(str(e))
            if kernel is not None and kernel_proc is not None:
                try:
                    if hasattr(kernel, "_call"):
                        kernel._call(
                            "run_gate_release", {"process_id": kernel_proc.id}
                        )
                        try:
                            kernel._call(
                                "run_release", {"process_id": kernel_proc.id}
                            )
                        except Exception:
                            pass
                except Exception:
                    pass
                await kernel.end_process(kernel_proc.id, state="failed", reason=str(e))
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
        logger.info(f"DEBUG_START: should_stop={self._should_stop}")

        # Durable Run：进入规划阶段
        _rc = getattr(self, "_run_recorder", None)
        if _rc is not None:
            try:
                from backend.agent.run_state import RunStatus as _RS

                await _rc.transition(_RS.PLANNING, note=f"mode={mode}")
            except Exception:
                pass

        # @device 远程执行（L1）：命中则短路，不进工具循环（phases/prologue）
        from backend.agent.phases.prologue import (
            expand_continue_phrase,
            try_device_shortcut,
        )

        _device_card = await try_device_shortcut(self, session_id, user_input, attachments)
        if _device_card is not None:
            return _device_card

        import time as _time
        _max_dur = float(getattr(settings, "agent_max_duration_seconds", 0) or 0)
        _deadline = (_time.monotonic() + _max_dur) if _max_dur > 0 else None

        # 「请继续」→ 自动接 Goal/checkpoint 续跑（phases/prologue）
        user_input, mode = await expand_continue_phrase(session_id, user_input, mode)

        # 处理附件内容注入
        enriched_input = self._build_user_input_with_attachments(user_input, attachments or [])
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
        await self._persist_user_input(session_id, enriched_input)

        # 2. 获取 Session 配置（行级锁由 Repository 实现）
        session = await self.session_repo.get_with_lock(session_id)
        if session is None:
            raise ValueError(f"Session {session_id} not found")

        config = await self.session_repo.get_config(session_id)

        # 本轮 workspace 覆盖（session.config.workspace_root|file_browser_root|cwd + allowed_roots）
        _ws_reset = lambda: None  # noqa: E731
        try:
            from backend.tools.permissions import bind_run_workspace_from_config

            _ws_reset = bind_run_workspace_from_config(config if isinstance(config, dict) else {})
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
        history_dicts: list[dict[str, Any]] = []
        for h in history:
            if h.role not in ("user", "assistant", "tool"):
                continue
            raw_content = h.content if h.content is not None else ""
            tcs = getattr(h, "tool_calls", None)
            # 严格 API：assistant 带 tool_calls 时 content 不能是 ""（须 null）
            if h.role == "assistant" and tcs and not (raw_content or "").strip():
                item: dict[str, Any] = {"role": "assistant", "content": None, "tool_calls": tcs}
            else:
                item = {"role": h.role, "content": raw_content or ""}
                if h.role == "assistant" and tcs:
                    item["tool_calls"] = tcs
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
            mode_extra.extend(["manage_goal", "autopilot"])
        if mode == "cluster":
            mode_extra.extend(
                ["crew_steward", "manage_sub_agent", "delegate_task", "agent_call"]
            )

        # 联系 CEO/管家：强制编制工具面（分析→assign 员工，不起子代理闷跑）
        _contact_agent = ""
        _is_steward_session = False
        try:
            from backend.agent.workforce_dispatch import (
                STEWARD_FORCE_TOOLS,
                is_steward_contact,
            )

            if isinstance(config, dict):
                _contact_agent = str(config.get("contact_agent") or "").strip()
            # 供 tool_hooks / 危险确认「本员工允许」使用（写入 loop 后注入工具参数）
            self._contact_agent = _contact_agent
            # 联系 TA：解析 Identity，后续 command 用编制能力短路（不再反复弹窗）
            if _contact_agent and not getattr(self, "_identity_id", None):
                try:
                    from backend.agent.grant_store import resolve_identity_id
                    from backend.agent.steward_permission import (
                        load_identity_capabilities,
                    )

                    _iid = await resolve_identity_id(contact_name=_contact_agent)
                    if _iid:
                        self._identity_id = _iid
                        self._identity_name = _contact_agent
                        _caps = await load_identity_capabilities(_iid)
                        if _caps is not None:
                            self._identity_capabilities = list(_caps)
                except Exception as _id_e:
                    logger.debug("contact identity resolve skip: %s", _id_e)
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
            if _is_steward_session:
                mode_extra.extend(STEWARD_FORCE_TOOLS)
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
            extra_packs=["crew"] if _is_steward_session else None,
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
            )
        except Exception:
            try:
                await self._push_status(
                    session_id,
                    "thinking",
                    f"场景 {scene_plan.summary()} · 工具 {len(tools)}",
                )
            except Exception:
                pass
        logger.info(
            "Loaded %s tools session=%s profile=%s scene=%s filter=%s",
            len(tools),
            session_id,
            tool_profile,
            scene_plan.summary(),
            "ALL" if enabled_tools_filter is None else len(enabled_tools_filter),
        )

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
            )
            try:
                from backend.tools.builtins.capability_tools import _load_prefs

                uid = str(self.user_id) if self.user_id else "local"
                prefs = (_load_prefs(uid).get("users") or {}).get(uid) or {}
                if prefs:
                    brief += "\nUser preferences (honor these): " + json.dumps(
                        prefs, ensure_ascii=False
                    )
            except Exception:
                pass
            messages.append({"role": "system", "content": brief})
        except Exception as e:
            logger.debug("capability inject skipped: %s", e)

        # CEO/管家编排纪律：分析需求 → crew_steward.assign 给编制员工
        if _is_steward_session:
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
                logger.info(
                    "steward orchestration injected session=%s contact=%s",
                    session_id,
                    _contact_agent,
                )
            except Exception as e:
                logger.warning("steward orchestration inject failed: %s", e)

        # Goal 模式：更高轮次 + 初始化 goal 状态
        goal_mode = mode == "goal"
        if goal_mode:
            from backend.agent.goal_state import (
                ensure_goal,
                get_goal,
                load_goal_from_db,
                save_goal_to_db,
            )

            goal_iters = int(getattr(settings, "agent_goal_max_iterations", 100) or 100)
            self.max_iterations = max(self.max_iterations, goal_iters)
            await load_goal_from_db(session_id)
            ensure_goal(session_id, title=enriched_input[:120], description=enriched_input[:2000])
            await self._push_goal_update(session_id)
            # 注入当前 goal 摘要
            g0 = get_goal(session_id)
            if g0:
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "Goal runtime status (keep updated via manage_goal + autopilot):\n"
                            + g0.summary_for_llm()
                            + "\nFor multi-step work: autopilot action=start goal=... then next/reflect/complete."
                        ),
                    }
                )

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

        # 6. 获取 LLM 服务（优先子代理覆盖快照 → 会话 LLM 快照 → 配置变更不影响本会话）
        llm_snapshot = getattr(self, "_llm_snapshot_override", None) or (
            (config or {}).get("llm") if isinstance(config, dict) else None
        )
        llm_service = LLMServiceFactory.get_service_for_snapshot(llm_snapshot)

        # 6.5 上下文引擎 pipeline（L1/L3/L5）
        try:
            from backend.agent.context_compress import compress_history_if_needed
            from backend.agent.context_engine import get_context_engine

            thr = float(getattr(settings, "context_threshold_percent", 0.72) or 0.72)
            messages, compress_meta = await compress_history_if_needed(
                messages, session_id=session_id, threshold=thr
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
                get_context_engine().update_from_response(
                    {"prompt_tokens": compress_meta.get("tokens_after")
                     or compress_meta.get("tokens_before")
                     or total_tokens}
                )
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"Context compress skipped: {e}")
            compress_meta = {}

        # 7. RAG + Wiki + 实体：按场景 injection_tier 动态控制
        strengthen_rag = bool(compress_meta.get("compressed")) or (
            total_tokens > int(getattr(settings, "context_window", 128_000) or 128_000) * 0.55
        )
        if inject_opts.get("rag"):
            messages = await self._inject_rag_context(
                messages,
                enriched_input,
                top_k=int(inject_opts.get("rag_top_k") or 3),
                strengthen=strengthen_rag and scene_plan.injection_tier == "rich",
                min_score=float(inject_opts.get("rag_min_score") or 0.58),
            )
        else:
            logger.debug("RAG skipped tier=%s", scene_plan.injection_tier)
        if inject_opts.get("wiki"):
            messages = await self._inject_wiki_context(
                messages,
                enriched_input,
                limit=int(inject_opts.get("wiki_limit") or 4),
                min_score=float(inject_opts.get("wiki_min_score") or 0.2),
            )
        else:
            logger.debug("Wiki skipped tier=%s", scene_plan.injection_tier)
        if inject_opts.get("entity"):
            try:
                from backend.services.entity_service import get_entity_service
                es = get_entity_service()
                recalled = await es.recall(
                    user_input,
                    user_id=self.user_id,
                    limit=int(inject_opts.get("entity_limit") or 3),
                )
                if recalled:
                    ctx = es.format_recall_context(recalled)
                    if ctx:
                        self._append_to_system(messages, ctx)
            except Exception as e:
                logger.debug("entity recall skipped: %s", e)
        else:
            logger.debug("entity skipped tier=%s", scene_plan.injection_tier)

        # 8. Agent Loop
        final_content = ""
        _sft_tools: list = []  # SFT usage log buffer
        accumulated_content = ""
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
        _completion_followups = 0
        _tools_used_run: list[str] = []
        self._reactive_compact_used = False
        _multi_source_pending = False
        _suppress_content_stream = False
        _segment = 0
        _empty_reply_retries = 0
        _empty_reply_max = int(getattr(settings, "agent_empty_reply_retries", 2) or 2)
        _tool_repeat_guard = ToolRepeatGuard(
            max_repeat=int(getattr(settings, "agent_tool_repeat_max", 3) or 3)
        )
        _force_final_no_tools = False
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
                    _k_iter._call(
                        "iteration_set_budget",
                        {"process_id": _kpid_iter, "max_total": _total_iters},
                    )
            except Exception:
                pass
        _turn_retry = TurnRetryState()
        _budget_grace_call = False
        _loop_exit_reason = ""
        _snapshot_every = max(
            1, int(getattr(settings, "agent_process_snapshot_every", 10) or 10)
        )

        for _global_iter in range(_total_iters + 1):  # +1 允许 grace 终答
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
                        _ic = _k_iter._call(
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
                except Exception:
                    pass
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
                        _k_snap._call(
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
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "【系统自动续跑】上一轮次段已用尽，请从断点继续，"
                                "不要重复已完成工作。"
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
            except Exception:
                pass

            # 更新状态：thinking
            await self._push_status(
                session_id, "thinking", f"思考中 · 第 {iteration + 1} 轮"
            )

            # 调用 LLM（流式，phases/llm_round）
            from backend.agent.phases.llm_round import run_llm_round

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
            tool_calls = _lr.tool_calls
            if _lr.force_final_no_tools is not None:
                _force_final_no_tools = _lr.force_final_no_tools
            if _lr.action == "continue":
                continue
            if _lr.action == "break":
                final_content = _lr.final_content
                break

            # 判断是否有 tool calls
            if tool_calls:
                # 将 assistant 的回复（含 tool calls）追加到 messages
                # content 用 None 兼容部分严格 API（空字符串 + tool_calls 会被拒）
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": accumulated_content if accumulated_content else None,
                }
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

                # 持久化中间 assistant（含 tool_calls），便于跨轮续跑
                try:
                    await self._save_message(
                        session_id,
                        "assistant",
                        accumulated_content or "",
                        tool_calls=assistant_tool_calls,
                    )
                except Exception as e:
                    msg = str(e)
                    if "FOREIGN KEY" in msg or "IntegrityError" in msg:
                        logger.warning(
                            "Skip persist assistant tool_calls (session missing?): %s", e
                        )
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
                    user_input=user_input,
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
                _suppress_content_stream = _tr_state.suppress_content_stream
                _multi_source_pending = _tr_state.multi_source_pending
                _timid_read_streak = _tr_state.timid_read_streak
                _timid_write_streak = _tr_state.timid_write_streak
                _tool_rounds = _tr_state.tool_rounds
                _last_tool_round_count = _tr_state.last_tool_round_count
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
                final_content = _nr.final_content
                break
        else:
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
                        "\n\n---\n**Goal 进度**\n```\n"
                        + g.summary_for_llm()
                        + "\n```\n可发送「请继续」恢复 Goal 模式推进。"
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
                            run_id=str(_rc.run_id) if _rc is not None and _rc.run_id else None,
                        )
                    except Exception:
                        pass

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
        self.last_exit_reason = _loop_exit_reason or "completed"
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
            )
        ):
            try:
                from backend.agent.exit_reasons import format_exit_user_message

                note = format_exit_user_message(
                    self.last_exit_reason, process_id=_kpid_iter or None
                )
                if note and note not in final_content:
                    final_content = f"{final_content.rstrip()}\n\n——\n{note}"
            except Exception:
                pass
        return final_content

    # ─────────── P0 helpers ───────────
