"""
Nexus Agent Loop
极简 Agent 核心循环，自主实现 User -> LLM -> Tool Call -> 执行 -> LLM -> 回复
集成 CtxItem 上下文系统、ContextFlow 记录、Task 进度追踪、Auto Optimize、TTL 清理
支持用户隔离、跨设备同步、消息通知
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from backend.agent.iteration_budget import IterationBudget
from backend.agent.loop_base import AgentLoopBase
from backend.agent.robust import (
    ToolRepeatGuard,
)
from backend.agent.turn_retry import TurnRetryState
from backend.core.config import settings
from backend.database import get_db_context
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
from backend.repositories.context_repo import (
    AsyncCtxItemRepository,
)
from backend.repositories.message_repo import AsyncMessageRepository
from backend.repositories.notification_repo import AsyncNotificationRepository
from backend.repositories.session_repo import AsyncSessionRepository
from backend.repositories.skill_repo import AsyncSkillRepository
from backend.repositories.task_repo import AsyncTaskRepository
from backend.repositories.tool_repo import AsyncToolRepository
from backend.schemas.ws import MemoryUpdated, StatusUpdate, StreamDelta
from backend.services.llm import LLMServiceFactory
from backend.services.tools import ToolRegistry
from backend.skills import SkillRegistry

from .context import ContextManager

logger = logging.getLogger(__name__)


def _sanitize_tool_error(tool_name: str, exc: Exception) -> str:
    """工具错误脱敏：生产模式不回传 SQL/堆栈，调试模式带详情。"""
    import os

    if os.environ.get("TAKTON_DEBUG", "").lower() in ("1", "true", "yes"):
        return f"[Error] Failed to execute {tool_name}: {exc}"
    # 提取异常类型名，不带内部细节
    exc_type = type(exc).__name__
    return (
        f"[Error] 工具 {tool_name} 执行失败（{exc_type}）。"
        f"请检查服务端日志获取详情，或设 TAKTON_DEBUG=1 查看完整错误。"
    )


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



class NexusAgentLoop(AgentLoopBase):
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

    async def _execute_registered_tool(self, name: str, arguments: dict[str, Any]):
        """统一工具执行入口 → ToolExecutorPort（默认 RegistryToolExecutor）。"""
        # Durable Run：注入 recorder，permission 交互确认可切 WAITING 状态
        arguments = dict(arguments or {})
        arguments.setdefault("_run_recorder", getattr(self, "_run_recorder", None))
        # Agent Computer：agent 身份（主 Agent=main；子代理 loop 实例可自带 key/label）
        arguments.setdefault("_agent_key", getattr(self, "_agent_key", "main"))
        arguments.setdefault("_agent_label", getattr(self, "_agent_label", ""))
        # 联系员工会话：注入 contact 名 + identity id/caps（本员工允许后短路弹窗）
        contact = str(getattr(self, "_contact_agent", "") or "").strip()
        if contact:
            arguments.setdefault("_contact_agent", contact)
            arguments.setdefault("_identity_name", contact)
        if getattr(self, "_identity_id", None):
            arguments.setdefault("_identity_id", str(self._identity_id))
        if getattr(self, "_identity_name", None):
            arguments.setdefault("_identity_name", str(self._identity_name))
        caps = getattr(self, "_identity_capabilities", None)
        if caps is not None:
            arguments.setdefault("_identity_capabilities", list(caps))
        # 编制员工：贯穿工具权限 / 危险命令 / 提权路径
        if getattr(self, "_workforce", False) or str(
            getattr(self, "_agent_key", "") or ""
        ).startswith("wf:"):
            arguments["_workforce"] = True
            if getattr(self, "_identity_id", None):
                arguments.setdefault("_identity_id", str(self._identity_id))
            if getattr(self, "_identity_name", None):
                arguments.setdefault("_identity_name", str(self._identity_name))
            if caps is not None:
                arguments.setdefault("_identity_capabilities", list(caps))
            if getattr(self, "_inbox_item_id", None):
                arguments.setdefault("_inbox_item_id", str(self._inbox_item_id))
            # 员工不走主人确认通道
            arguments["_ws_manager"] = None
        # 真 Sub-Agent：嵌套深度（delegate_task 防失控）
        arguments.setdefault("_subagent_depth", getattr(self, "_subagent_depth", 0))
        # Skill 契约：已挂载包声明 tools 白名单时的执行边界拦截
        blocked = await self._contract_tool_block_reason(name, arguments)
        if blocked:
            return blocked
        # ── Agent Kernel（阶段 1/W3）：所有工具调用经 kernel.mediate 中介 ──
        # 兼容模式进程（capabilities=None）放行+记录；显式能力集/令牌未授权 →
        # 返回工具级权限错误（反馈给模型，不炸掉整个 run）。
        kernel_proc = getattr(self, "_kernel_process", None)
        if kernel_proc is not None:
            arguments.setdefault("_kernel_process_id", kernel_proc.id)
            from backend.kernel import KernelPermissionError, get_kernel

            try:
                await get_kernel().mediate(
                    kernel_proc.id, "tool_call", name, args=arguments
                )
            except KernelPermissionError as e:
                logger.warning(
                    "kernel 拦截工具调用 tool=%s proc=%s: %s", name, kernel_proc.id, e
                )
                # 员工工单：不向主人刷提权单。编制内 → 静默并入进程能力；
                # 编制外 → 直接拒，提示改权限看板（CEO 策略），不弹审批队列。
                agent_key = str(getattr(self, "_agent_key", "") or "")
                is_wf = agent_key.startswith("wf:") or bool(
                    getattr(self, "_workforce", False)
                )
                if is_wf:
                    try:
                        from backend.agent.grant_store import tool_matches_crew_caps
                        from backend.agent.steward_permission import (
                            load_identity_capabilities,
                        )

                        caps = list(getattr(self, "_identity_capabilities", None) or [])
                        if not caps:
                            caps = (
                                await load_identity_capabilities(
                                    str(getattr(self, "_identity_id", "") or "") or None
                                )
                            ) or []
                        if tool_matches_crew_caps(name, caps):
                            # 抽象 cap 已覆盖工具；静默把工具名并入进程集（不建提权单）
                            try:
                                k = get_kernel()
                                proc = k._resolve_process(kernel_proc.id)
                                if proc is not None and proc.capabilities is not None:
                                    if name not in proc.capabilities:
                                        proc.capabilities = sorted(
                                            set(proc.capabilities) | {name}
                                        )
                                        if hasattr(k, "_persist_process"):
                                            k._persist_process(proc)
                                        if hasattr(k, "_share_process"):
                                            k._share_process(proc)
                                        logger.info(
                                            "steward silent expand tool=%s proc=%s",
                                            name,
                                            kernel_proc.id,
                                        )
                            except Exception as se:
                                logger.debug("steward silent expand skip: %s", se)
                            try:
                                await get_kernel().mediate(
                                    kernel_proc.id, "tool_call", name, args=arguments
                                )
                            except KernelPermissionError as e2:
                                return (
                                    f"Error: Kernel 权限拒绝——{e2}。"
                                    "（编制策略已尝试扩权仍失败；请 CEO 在权限看板检查该员工能力）"
                                )
                            # mediate 过了则继续往下执行工具（fall through）
                        else:
                            return (
                                f"Error: 编制策略拒绝工具 «{name}»（不在员工能力档案内）。"
                                "请主人让 CEO 在权限看板扩权，不要对每一次工具点「允许」。"
                            )
                    except Exception as se:
                        logger.debug("workforce steward escalate path: %s", se)
                        return (
                            f"Error: Kernel 权限拒绝——{e}。"
                            "员工路径不向主人发起提权审批。"
                        )
                else:
                    # 主人主会话：可自动发起提权进审批台
                    esc_note = ""
                    if bool(getattr(settings, "agent_kernel_auto_escalate", True)):
                        try:
                            req = await get_kernel().request_escalation(
                                kernel_proc.id,
                                [name],
                                reason=f"工具调用被能力集拦截：{name}",
                            )
                            esc_note = (
                                f"（已自动发起权限申请 {req.id}，"
                                "用户在权限控制台批准后即可重试；请勿重复调用本工具）"
                            )
                        except ValueError:
                            pass
                    return f"Error: Kernel 权限拒绝——{e}{esc_note}"
        # ── 重复搜索软干预（0.4.4：研究任务收敛刹车）──
        # 同 run 内同查询重复：第 2 次结果前附提醒；第 3 次起直接拒绝执行，
        # 强制模型基于已有信息总结（prompt 层刹车之外的工程层兜底）。
        repeat_verdict = self._search_repeat_verdict(name, arguments)
        if repeat_verdict == "block":
            logger.info("重复搜索拦截 tool=%s query=%s", name, str(arguments)[:120])
            total = int(getattr(self, "_search_total_calls", 0) or 0)
            max_run = int(getattr(settings, "agent_search_max_per_run", 8) or 8)
            if max_run > 0 and total > max_run:
                return (
                    f"Error: 本轮研究已累计搜索 {total} 次（上限 {max_run}）。"
                    "继续搜索收益极低——请立即基于已收集内容总结交付；"
                    "缺口请在答案中显式列出，勿再调用搜索类工具。"
                )
            return (
                "Error: 检测到同一/近似查询已执行 3 次以上——继续重复搜索不会带来新信息。"
                "请立即基于已收集的内容总结交付；如有未覆盖的缺口，在答案中显式注明，"
                "或改用**角度完全不同**的新查询（而非同义改写）。"
            )
        repeat_prefix = (
            "[提醒] 该查询此前已执行过，结果大概率相同。若本次结果无新增事实，"
            "请停止继续搜索并进入总结阶段。\n\n" if repeat_verdict == "warn" else ""
        )
        ex = getattr(self, "tool_executor", None)
        if ex is not None:
            result = await ex.execute(name, arguments)
            return repeat_prefix + result if repeat_prefix and isinstance(result, str) else result
        from backend.tools.registry import ToolRegistry as UnifiedToolRegistry

        result = await UnifiedToolRegistry.execute(name, arguments)
        return repeat_prefix + result if repeat_prefix and isinstance(result, str) else result

    # ── 重复搜索检测（收敛刹车 + 全局预算 + 近似同义）──────────

    _SEARCH_TOOL_NAMES = frozenset({
        "web_search", "x_search", "search", "websearch",
        "web_extract", "web_fetch", "fetch_url", "fetch_webpage",
        "browse_page", "open_page", "tavily_search", "duckduckgo_search",
    })

    def _search_repeat_verdict(self, name: str, arguments: dict[str, Any]) -> str | None:
        """返回 None（放行）/ "warn" / "block"。

        1) 单 run 搜索总次数 > agent_search_max_per_run → block
        2) 精确/词序归一指纹：第 2 次 warn，第 3 次起 block
        3) 与历史 query 词集 Jaccard ≥ 阈值 → 同一桶
        """
        if not bool(getattr(settings, "agent_search_repeat_guard", True)):
            return None
        if name not in self._SEARCH_TOOL_NAMES:
            return None
        query = str(
            arguments.get("query")
            or arguments.get("q")
            or arguments.get("url")
            or arguments.get("search_term")
            or ""
        ).strip().lower()

        import hashlib

        max_run = int(getattr(settings, "agent_search_max_per_run", 8) or 8)

        if not query:
            total = int(getattr(self, "_search_total_calls", 0) or 0) + 1
            self._search_total_calls = total
            if max_run > 0 and total > max_run:
                return "block"
            return None

        tokens = [
            tok
            for tok in query.replace(",", " ").replace("，", " ").replace("、", " ").split()
            if tok
        ]
        normalized = " ".join(sorted(tokens))
        fp = hashlib.sha1(f"{name}:{normalized}".encode("utf-8")).hexdigest()[:12]

        jaccard_thr = float(getattr(settings, "agent_search_similar_jaccard", 0.72) or 0.72)
        token_set = set(tokens)
        seen_sets: list = getattr(self, "_search_token_sets", None) or []
        matched_fp = None
        if token_set and jaccard_thr > 0:
            for old_fp, old_set in seen_sets:
                if not old_set:
                    continue
                inter = len(token_set & old_set)
                union = len(token_set | old_set) or 1
                if inter / union >= jaccard_thr:
                    matched_fp = old_fp
                    break
        if matched_fp is None:
            seen_sets.append((fp, token_set))
            self._search_token_sets = seen_sets[-40:]
            use_fp = fp
        else:
            use_fp = matched_fp

        counter = getattr(self, "_search_fp_counter", None)
        if counter is None:
            counter = {}
            self._search_fp_counter = counter
        count = counter.get(use_fp, 0) + 1
        counter[use_fp] = count

        total = int(getattr(self, "_search_total_calls", 0) or 0) + 1
        self._search_total_calls = total

        if max_run > 0 and total > max_run:
            return "block"
        if count >= 3:
            return "block"
        if count == 2 or (max_run > 0 and total >= max(3, max_run - 2)):
            return "warn"
        return None

    # ── Kernel iteration gate（Phase 2：Alpha Review #1 融合）──────────

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
        # 2) 事前预算检查
        if (
            bool(getattr(settings, "agent_kernel_budget_precheck", True))
            and proc.token_budget is not None
        ):
            remaining = proc.budget_remaining
            if remaining is not None:
                estimated = self._estimate_next_call_tokens(messages)
                if remaining < estimated:
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

    async def _get_rag_service(self):
        """懒加载 RAG 服务。未配 Embedding+Qdrant 时为 Null（本地模式）。"""
        if self._rag_service is None:
            try:
                from backend.services.rag.capability import use_vector_rag
                from backend.services.rag.factory import RAGServiceFactory

                # 本地模式也返回 Null 实例，避免反复探测；向量模式返回 Qdrant
                self._rag_service = RAGServiceFactory.get_service()
                if not use_vector_rag():
                    # 标记：自动注入路径会再检查 capability
                    pass
            except Exception as e:
                logger.warning(f"RAG service unavailable: {e}")
        return self._rag_service

    def _append_to_system(self, messages: list[dict[str, Any]], block: str) -> None:
        if not block or not block.strip():
            return
        found = False
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "system":
                messages[i]["content"] = (messages[i].get("content") or "") + "\n\n" + block
                found = True
                break
        if not found:
            messages.insert(0, {"role": "system", "content": block})

    async def _inject_rag_context(
        self,
        messages: list[dict[str, Any]],
        user_input: str,
        *,
        top_k: int = 3,
        strengthen: bool = False,
        min_score: float | None = None,
    ) -> list[dict[str, Any]]:
        """向量 RAG 自动注入：仅 Embedding+Qdrant 就绪时生效（默认本地模式跳过）。"""
        from backend.services.rag.capability import get_rag_status

        st = get_rag_status()
        if not st.auto_inject:
            logger.debug("RAG auto-inject skipped: %s", st.reason[:100])
            return messages

        rag = await self._get_rag_service()
        if rag is None:
            return messages

        k = top_k * 2 if strengthen else top_k
        try:
            context = await rag.search_knowledge_base(
                user_input,
                top_k=k,
                user_id=str(self.user_id) if self.user_id else None,
                min_score=min_score,
            )
            # Null 实现会返回“不可用”文案 — 不应注入
            if context and context.strip() and "知识库检索不可用" not in context:
                logger.info(
                    f"Injected RAG context ({len(context)} chars) top_k={k} for: {user_input[:50]}"
                )
                self._append_to_system(messages, f"# 相关知识（RAG）\n{context}")
        except Exception as e:
            logger.warning(f"RAG context injection failed (degraded to local): {e}")

        # ── Workforce 身份记忆召回（Alpha Review #4）──
        # 工单执行中按当前输入检索身份记忆（prompt 硬注入之外的执行期召回：
        # 中期任务上下文漂移后，相关经验/方法论仍能按当前输入浮现）
        agent_key = getattr(self, "_agent_key", "") or ""
        if agent_key.startswith("wf:"):
            try:
                identity_id = agent_key[3:]
                mem_docs = await rag.search_identity_memory(
                    user_input, identity_id, top_k=3
                )
                if mem_docs:
                    block = "# 身份记忆召回（与当前输入相关）\n" + "\n".join(
                        f"- [{(d.payload or {}).get('kind', 'memory')}] {d.text}"
                        for d in mem_docs
                    )
                    self._append_to_system(messages, block)
                    logger.info(
                        "Injected identity memory recall (%d docs) for wf:%s",
                        len(mem_docs), identity_id[:8],
                    )
            except Exception as e:
                logger.debug("identity memory recall skipped: %s", e)

        return messages

    async def _inject_wiki_context(
        self,
        messages: list[dict[str, Any]],
        user_input: str,
        *,
        limit: int = 6,
        min_score: float = 0.2,
    ) -> list[dict[str, Any]]:
        """把 Wiki 图谱中匹配的实体摘要拼进 system（简单相关度门槛）。"""
        q = (user_input or "").strip()
        if len(q) < 2:
            return messages
        try:
            from backend.repositories.wiki_repo import AsyncWikiEntityRepository

            repo = AsyncWikiEntityRepository()
            ents = await repo.search(q) or []
            if not ents:
                return messages
            lim = max(1, min(int(limit or 6), 12))
            q_low = q.lower()
            q_tokens = {t for t in q_low.replace("/", " ").replace("-", " ").split() if len(t) >= 2}

            def _score(ent: object) -> float:
                name = str(getattr(ent, "name", "") or "")
                desc = str(getattr(ent, "description", "") or "")
                hay = f"{name} {desc}".lower()
                if not hay.strip():
                    return 0.0
                sc = 0.0
                if name and name.lower() in q_low:
                    sc += 0.7
                if q_low and name.lower() and name.lower() in q_low:
                    sc += 0.2
                # token overlap
                n_toks = {t for t in hay.replace(",", " ").split() if len(t) >= 2}
                if q_tokens and n_toks:
                    inter = q_tokens & n_toks
                    sc += 0.5 * (len(inter) / max(1, len(q_tokens)))
                # CJK bigram soft
                for i in range(max(0, len(q) - 1)):
                    bg = q[i : i + 2]
                    if bg.strip() and bg in hay:
                        sc += 0.08
                return sc

            ranked = sorted((( _score(e), e) for e in ents), key=lambda x: -x[0])
            kept = [(s, e) for s, e in ranked if s >= float(min_score)][:lim]
            if not kept:
                logger.info(
                    "Wiki inject skipped: all below min_score=%.2f (candidates=%s)",
                    min_score,
                    len(ents),
                )
                return messages
            lines = ["# Wiki 图谱相关实体"]
            for s, e in kept:
                lines.append(
                    f"- **{e.name}** ({getattr(e, 'entity_type', 'concept')})"
                    + (f"：{e.description}" if e.description else "")
                    + f" (rel={s:.2f})"
                )
            self._append_to_system(messages, "\n".join(lines))
            logger.info("Injected %s wiki entities for query", len(kept))
        except Exception as e:
            logger.debug("Wiki inject skipped: %s", e)
        return messages

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
                # 0.6：workforce 派遣可显式指定进程选项（身份权限档案/默认预算）——
                # 异步唤醒的 agent 以其「编制」内的权限与预算运行
                proc_opts = getattr(self, "_kernel_process_options", None) or {}
                if proc_opts.get("capabilities") is not None:
                    caps = list(proc_opts["capabilities"])
                elif bool(getattr(settings, "agent_kernel_explicit_capabilities", False)):
                    try:
                        from backend.tools.registry import ToolRegistry

                        caps = sorted(ToolRegistry._tools.keys()) or None
                    except Exception as e:
                        logger.debug("能力快照失败，降级兼容模式: %s", e)
                        caps = None
                _meta = {
                    "mode": mode,
                    "parent_run_id": str(parent_run_id) if parent_run_id else None,
                }
                if isinstance(proc_opts.get("meta"), dict):
                    _meta.update(proc_opts["meta"])
                kernel_proc = await kernel.create_process(
                    agent_key or "main",
                    session_id=str(session_id),
                    parent_id=parent_pid,
                    capabilities=caps,
                    token_budget=proc_opts.get("token_budget"),
                    meta=_meta,
                )
                await kernel.mark_running(kernel_proc.id)
                self._kernel_process = kernel_proc
            except Exception as e:
                # Kernel 装配失败不阻断对话（显式降级并告警，非静默）
                logger.warning("kernel create_process 失败，退回无 kernel 路径: %s", e)
                kernel_proc = None
                self._kernel_process = None
        try:
            result = await self._run_locked(
                session_id, user_input, attachments, mode, sub_agent_ids
            )
            if self._should_stop:
                await recorder.cancel("stopped by user")
            else:
                await recorder.finish_ok(final_summary=result or "")
            if kernel is not None and kernel_proc is not None:
                await kernel.end_process(
                    kernel_proc.id,
                    state="killed" if self._should_stop else "completed",
                    reason="stopped by user" if self._should_stop else None,
                )
            return result
        except Exception as e:
            await recorder.finish_fail(str(e))
            if kernel is not None and kernel_proc is not None:
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
        logger.info(
            "Loaded %s tools session=%s profile=%s scene=%s filter=%s",
            len(tools),
            session_id,
            tool_profile,
            scene_plan.summary(),
            "ALL" if enabled_tools_filter is None else len(enabled_tools_filter),
        )
        try:
            await self._push_status(
                session_id,
                "thinking",
                f"场景 {scene_plan.summary()} · 工具 {len(tools)}",
            )
        except Exception:
            pass

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
        _turn_retry = TurnRetryState()
        _budget_grace_call = False
        _loop_exit_reason = ""

        for _global_iter in range(_total_iters + 1):  # +1 允许 grace 终答
            # ── Kernel 仲裁点（Phase 2）：挂起等待 / 事前预算 / 调度让出。
            # 放在预算 consume 之前——挂起等待不该消耗 iteration 配额。
            _gate = await self._kernel_iteration_gate(session_id, messages)
            if _gate == "stop":
                _loop_exit_reason = "kernel_gate_stop"
                break
            if _gate == "budget":
                _loop_exit_reason = "kernel_budget_precheck"
                final_content = (
                    "[Budget Exceeded] 进程 token 预算不足以支撑下一次 LLM 调用，"
                    "运行已事前中断（避免一次性烧穿预算）。"
                    "本工单未完成实质检查；可提高预算、收窄范围或拆小任务后重试。"
                    "禁止用报告框架/预期结果冒充结论。"
                )
                await self._push_status(
                    session_id, "thinking", "预算不足，运行已事前中断"
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
                    f"迭代预算已用尽 ({_iter_budget.used}/{_iter_budget.max_total})，生成最终回复…",
                )
                logger.info(
                    "Iteration budget grace session=%s used=%s",
                    session_id,
                    _iter_budget.snapshot(),
                )
            elif not _iter_budget.consume():
                _loop_exit_reason = "budget_exhausted"
                break

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
        self.last_exit_reason = _loop_exit_reason
        return final_content

    # ─────────── P0 helpers ───────────

    def _looks_like_structured_report(self, text: str) -> bool:
        """已是完整报表/审计稿（Markdown 结构），不应再被「多信源合并」压成短摘要。"""
        if not text or len(text) < 350:
            return False
        score = 0
        headers = text.count("\n## ") + text.count("\n### ") + text.count("## ")
        if headers >= 2:
            score += 2
        if text.count("\n- ") + text.count("\n* ") + text.count("\n1.") >= 6:
            score += 1
        if text.count("|") >= 6 and text.count("\n") >= 6:
            score += 1
        report_kw = (
            "审计", "报告", "严重", "风险", "建议", "结论", "发现",
            "Critical", "High", "Medium", "Low", "汇总", "报表",
        )
        if sum(1 for k in report_kw if k in text) >= 2:
            score += 2
        if len(text) >= 1200 and headers >= 1:
            score += 1
        return score >= 3

    def _looks_like_multi_answer(self, text: str) -> bool:
        """启发式：模型把多个信源原样并列（非结构化报表）。"""
        if not text or len(text) < 80:
            return False
        # 完整报表常有多级 ##，不能再当「答案1/2/3」
        if self._looks_like_structured_report(text):
            return False
        markers = [
            "答案1", "答案 1", "答案一", "【答案", "来源1", "来源 1",
            "信源1", "根据工具", "工具1", "结果1", "方案一", "方案1",
            "### 答案", "## 答案", "Answer 1", "Source 1",
            "weather 返回", "web_search 返回", "如下多个",
        ]
        hits = sum(1 for m in markers if m in text)
        if hits >= 1:
            return True
        if text.count("根据") >= 3 and text.count("\n\n") >= 3:
            return True
        return False

    async def _maybe_aggregate_multi_source(
        self,
        *,
        llm_service: Any,
        session_id: uuid.UUID,
        user_input: str,
        draft: str,
        tool_rounds: int,
        last_tool_count: int,
        multi_pending: bool,
    ) -> str:
        """多工具/多信源场景下再调用一次 LLM（无 tools）聚合成单一用户可读答复。

        注意：CEO/审计等「已经写好的长报表」禁止再压缩——历史上 last_tool_count>=2
        会把流式 3k 字报表收成几百字干巴摘要。
        """
        if not draft or not str(draft).strip():
            return draft

        # 已是结构化交付物：直接落库，保留用户看到的流式报表
        if self._looks_like_structured_report(draft) and not multi_pending:
            logger.info(
                "multi-source skip structured report session=%s draft=%s",
                session_id,
                len(draft),
            )
            return draft

        # 仅在「真·多答案并列」或显式 multi_pending 时合并
        # 不再因 last_tool_count>=2 无脑触发（拉 tools 再写报告是常态）
        need = bool(multi_pending) or self._looks_like_multi_answer(draft)
        if not need:
            return draft
        if len(draft) < 120 and not multi_pending:
            return draft

        await self._push_status(session_id, "thinking", "正在合并多信源结果…")
        try:
            await self._emit_progress(
                "thinking",
                "正在把多个工具结果合并成一份答复…",
            )
        except Exception:
            pass

        sys_p = (
            "你是结果编辑器。用户只应看到一份连贯答复。\n"
            "任务：把「草稿」改写为单一最终答案。\n"
            "规则：\n"
            "- 若草稿已是完整报告/审计/Markdown 结构（多级标题、列表、表格），"
            "  **原样保留结构与要点**，只去明显重复，禁止压成两三段摘要；\n"
            "- 仅当草稿是「答案1/答案2」式并列时才合并；\n"
            "- 保留关键事实，去掉重复；\n"
            "- 禁止答案1/2/3 并列输出；\n"
            "- 冲突时选更具体、更新、更一致的说法，可一句说明；\n"
            "- 不要提及内部工具名堆砌；\n"
            "- 使用用户语言（通常为中文）；\n"
            "- 只输出最终正文，不要前言。"
        )
        user_block = (
            "用户问题：\n"
            + str(user_input or "")
            + "\n\n草稿（可能含多信源重复）：\n"
            + str(draft)[:12000]
        )
        msgs = [
            {"role": "system", "content": sys_p},
            {"role": "user", "content": user_block},
        ]
        out = ""
        try:
            async for chunk in llm_service.chat(msgs, tools=None, stream=True):
                if self._should_stop:
                    break
                if chunk.delta:
                    out += chunk.delta
                if chunk.finish_reason:
                    break
        except Exception as e:
            logger.warning("aggregate LLM failed: %s", e)
            return draft

        out = (out or "").strip()
        if len(out) < 8:
            return draft
        # 防收缩：聚合后明显变短则丢弃（流式长文被收成干巴摘要）
        if len(draft) >= 400 and len(out) < max(120, int(len(draft) * 0.5)):
            logger.info(
                "multi-source rejected shrink session=%s draft=%s -> out=%s (keep draft)",
                session_id,
                len(draft),
                len(out),
            )
            return draft
        logger.info(
            "multi-source aggregated for session %s: draft=%s -> out=%s",
            session_id,
            len(draft),
            len(out),
        )
        return out


    async def _check_auto_optimize(
        self,
        session_id: uuid.UUID,
        config: dict[str, Any],
        total_tokens: int,
    ) -> None:
        """P0-3: 检查是否触发自动优化"""
        if self.ctx_item_repo is None:
            return
        auto_optimize = config.get("auto_optimize", True)
        threshold = config.get("optimize_threshold", 0.7)
        context_window = int(getattr(settings, "context_window", 128_000) or 128_000)

        usage_ratio = total_tokens / max(1, context_window)
        if auto_optimize and usage_ratio > threshold:
            logger.info(
                f"Auto optimize triggered for session {session_id}: "
                f"{usage_ratio:.1%} > {threshold:.1%}"
            )
            try:
                result = await self.ctx_item_repo.optimize(
                    session_id=session_id, threshold=threshold
                )
                logger.info(f"Auto optimize result: {result}")
                await self._push_status(
                    session_id,
                    "optimizing",
                    f"Auto-optimized: freed {result.get('saved_tokens', 0)} tokens",
                )
            except Exception as e:
                logger.warning(f"Auto optimize failed: {e}")

    def _validate_tool_args(self, schema: dict | None, arguments: dict) -> dict:
        """使用 JSON Schema 校验 tool call 参数。

        始终返回新 dict，避免在原始 tc.arguments 上注入 _ws_manager 等
        导致 WS ToolEvent.model_dump 无法序列化 ConnectionManager。
        """
        base = dict(arguments) if isinstance(arguments, dict) else {}
        if not schema:
            return base
        try:
            from jsonschema import ValidationError, validate

            validate(instance=base, schema=schema)
        except ImportError:
            pass  # jsonschema未安装时跳过校验
        except ValidationError as e:
            raise ValueError(f"Invalid tool arguments: {e.message}") from e
        return base

    async def _load_tools(
        self,
        session_id: uuid.UUID,
        enabled_skills: list[str] | None,
        enabled_tools_filter: list[str] | None = None,
        user_input: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        v3.0: 从统一 ToolRegistry 加载工具 schema。

        注意：为了兼容旧 session config，这里同时处理：
        - enabled_skills: 旧配置中的 skill 列表，会映射为工具名称过滤
        - enabled_tools_filter: 旧配置中的 tools 列表
        """
        # 合并名称过滤：旧配置中的 skills 和 tools 都是工具名称
        enabled_names = set()
        if enabled_skills is not None:
            enabled_names.update(enabled_skills)
        if enabled_tools_filter is not None:
            enabled_names.update(enabled_tools_filter)

        # 如果都是 ALL（None）表示不过滤
        filter_names = list(enabled_names) if enabled_names else None

        try:
            from backend.tools.registry import ToolRegistry as UnifiedToolRegistry

            tools = UnifiedToolRegistry.get_tools_schema(filter_names)
            logger.info(
                f"Loaded {len(tools)} unified tools for session {session_id} "
                f"(filter={filter_names})"
            )
        except Exception as e:
            logger.warning(f"Failed to load unified tools: {e}, falling back to old method")
            # 兼容旧方式
            tools = SkillRegistry.get_tools_schema(enabled_skills)
            seen_names = {
                (t.get("function") or {}).get("name")
                for t in tools
                if (t.get("function") or {}).get("name")
            }

            try:
                skill_repo = AsyncSkillRepository()
                active_skills = await skill_repo.get_active_skills()
                for skill in active_skills:
                    if skill.is_builtin:
                        continue
                    if enabled_skills is not None and skill.name not in enabled_skills:
                        continue
                    if skill.name in seen_names:
                        continue
                    tools.append({
                        "type": "function",
                        "function": {
                            "name": skill.name,
                            "description": skill.description or "",
                            "parameters": skill.schema or {"type": "object", "properties": {}},
                        },
                    })
                    seen_names.add(skill.name)
            except Exception as e2:
                logger.warning(f"Failed to load custom skills from DB: {e2}")

            try:
                tool_repo = AsyncToolRepository()
                active_tools = await tool_repo.get_active_tools()
                if enabled_tools_filter is not None:
                    active_tools = [t for t in active_tools if t.name in enabled_tools_filter]
                tool_schemas = ToolRegistry.get_tools_schema(active_tools)
                for ts in tool_schemas:
                    name = (ts.get("function") or {}).get("name")
                    if name and name in seen_names:
                        continue
                    tools.append(ts)
                    if name:
                        seen_names.add(name)
            except Exception as e2:
                logger.warning(f"Failed to load tools from DB: {e2}")

        # 合并 tool-def CtxItem（系统级工具定义）
        if self.ctx_item_repo is not None:
            try:
                tool_defs = await self.ctx_item_repo.list_by_session(
                    session_id=None,
                    scope="system",
                    kind="tool-def",
                    limit=50,
                )
                for td in tool_defs:
                    tools.append({
                        "type": "function",
                        "function": {
                            "name": td.key,
                            "description": td.value[:200],
                            "parameters": {"type": "object", "properties": {}},
                        },
                    })
            except Exception as e:
                logger.warning(f"Failed to load tool-def CtxItems: {e}")

        # Desktop 工具：仅当全量模式或过滤名单显式包含时兜底注入（默认 core 不塞）
        try:
            filter_set = set(enabled_tools_filter) if enabled_tools_filter is not None else None
            if filter_set is None or any(n.startswith("desktop_") for n in filter_set):
                from backend.services.desktop.tools import (
                    DesktopClickTool,
                    DesktopOpenAppTool,
                    DesktopReadFileTool,
                    DesktopScreenshotTool,
                    DesktopScrollTool,
                    DesktopTypeTool,
                    DesktopWriteFileTool,
                )
                desktop_tools = [
                    DesktopScreenshotTool(),
                    DesktopClickTool(),
                    DesktopTypeTool(),
                    DesktopOpenAppTool(),
                    DesktopScrollTool(),
                    DesktopReadFileTool(),
                    DesktopWriteFileTool(),
                ]
                existing_names = {
                    (t.get("function") or {}).get("name")
                    for t in tools
                    if (t.get("function") or {}).get("name")
                }
                for dt in desktop_tools:
                    if filter_set is not None and dt.name not in filter_set:
                        continue
                    if dt.name not in existing_names:
                        tools.append(dt.to_json_schema())
                        logger.info(f"Ensured desktop tool: {dt.name}")
        except Exception as e:
            logger.warning(f"Failed to ensure desktop tools: {e}")

        return tools

    async def _record_flow(
        self,
        session_id: uuid.UUID,
        agent: str,
        accessed_items: list[tuple[str, str]],
        tokens: int,
    ) -> None:
        """记录上下文访问流"""
        if self.context_flow_repo is None:
            return

        # 按 scope 分组
        scope_keys: dict[str, list[str]] = {}
        for scope, key in accessed_items:
            scope_keys.setdefault(scope, []).append(key)

        for scope, keys in scope_keys.items():
            try:
                await self.context_flow_repo.create_flow(
                    session_id=session_id,
                    agent=agent,
                    scope=scope,
                    keys=keys,
                    tokens=tokens,
                )
            except Exception as e:
                logger.warning(f"Failed to record context flow: {e}")

    # ─────────── Auto Cluster Analysis ───────────

    async def _analyze_task_complexity(self, user_input: str) -> float:
        """
        自动分析任务复杂度，返回 0.0-1.0 的分数
        
        高复杂度指标：
        - 多步骤/多领域任务
        - 需要代码 + 分析 + 文档等多种能力
        - 涉及比较、评估、设计等复杂认知
        """
        input_lower = user_input.lower()
        score = 0.0
        
        # 长度指标（长任务通常更复杂）
        if len(user_input) > 200:
            score += 0.2
        elif len(user_input) > 100:
            score += 0.1
        
        # 多步骤关键词（仅实义词；连词类虚词如「和/与/以及/同时」误伤率过高，已移除）
        multi_step_keywords = [
            "分析", "比较", "对比", "评估", "设计", "架构", "规划",
            "实现", "开发", "创建", "构建", "优化", "改进",
            "研究", "调查", "探索", "深入", "详细",
            "多个", "几个", "一系列", "批量", "综合",
        ]
        keyword_count = sum(1 for kw in multi_step_keywords if kw in input_lower)
        score += min(keyword_count * 0.15, 0.4)
        
        # 技术复杂度关键词
        tech_keywords = [
            "代码", "编程", "算法", "数据库", "api", "系统",
            "python", "javascript", "java", "c++", "sql",
            "前端", "后端", "全栈", "部署", "测试", "调试",
            "机器学习", "ai", "模型", "训练", "推理",
            "网络", "安全", "加密", "协议", "服务器",
        ]
        tech_count = sum(1 for kw in tech_keywords if kw in input_lower)
        score += min(tech_count * 0.1, 0.3)
        
        # 输出要求关键词
        output_keywords = [
            "报告", "文档", "方案", "计划", "教程", "指南",
            "总结", "分析结果", "建议", "推荐", "最佳实践",
        ]
        output_count = sum(1 for kw in output_keywords if kw in input_lower)
        score += min(output_count * 0.1, 0.2)
        
        # 问句数量（多个问题通常更复杂）
        question_marks = input_lower.count("?") + input_lower.count("？")
        if question_marks >= 3:
            score += 0.2
        elif question_marks >= 2:
            score += 0.1
        
        # 限制在 0-1 范围
        return min(max(score, 0.0), 1.0)

    async def _auto_create_sub_agents(self, user_input: str, complexity: float) -> list[dict]:
        """
        根据任务内容自动创建子代理配置（复用主会话LLM）
        
        返回子代理信息列表，每个包含:
        - id, name, icon, description, model_ref, system_prompt
        """
        input_lower = user_input.lower()
        sub_agents = []
        
        # 根据任务内容推断需要的专业角色
        roles = []
        
        # 代码/编程相关
        if any(kw in input_lower for kw in ["代码", "编程", "python", "javascript", "java", "c++", "sql", "算法", "调试", "开发", "实现", "bug", "错误", "修复"]):
            roles.append({
                "name": "coder",
                "icon": "💻",
                "description": "专业的编程和代码分析助手",
                "system_prompt": "你是一个专业的编程助手，擅长代码编写、调试和架构设计。请提供具体、可运行的代码示例，并解释关键设计决策。",
            })
        
        # 分析/研究相关
        if any(kw in input_lower for kw in ["分析", "研究", "调查", "比较", "对比", "评估", "数据", "统计", "趋势"]):
            roles.append({
                "name": "analyst",
                "icon": "📊",
                "description": "数据分析和研究专家",
                "system_prompt": "你是一个数据分析专家，擅长逻辑推理、数据解读和趋势分析。请提供结构化的分析框架和清晰的结论。",
            })
        
        # 文档/写作相关
        if any(kw in input_lower for kw in ["报告", "文档", "总结", "写作", "文案", "教程", "指南", "说明"]):
            roles.append({
                "name": "writer",
                "icon": "📝",
                "description": "技术文档和写作专家",
                "system_prompt": "你是一个技术写作专家，擅长将复杂概念转化为清晰易懂的文档。请注重结构、可读性和实用性。",
            })
        
        # 设计/架构相关
        if any(kw in input_lower for kw in ["设计", "架构", "规划", "方案", "系统", "框架", "模式"]):
            roles.append({
                "name": "architect",
                "icon": "🏗️",
                "description": "系统架构和设计专家",
                "system_prompt": "你是一个系统架构师，擅长高层设计、技术选型和架构决策。请考虑可扩展性、可维护性和最佳实践。",
            })
        
        # 通用/默认角色（如果没有匹配到专业角色）
        if not roles:
            roles.append({
                "name": "researcher",
                "icon": "🔍",
                "description": "综合研究和信息整合助手",
                "system_prompt": "你是一个研究助手，擅长信息收集、整理和综合。请提供全面、准确的信息，并标注关键发现。",
            })
            roles.append({
                "name": "critic",
                "icon": "🎯",
                "description": "质量评估和优化建议专家",
                "system_prompt": "你是一个质量评估专家，擅长发现潜在问题、提出改进建议和优化方案。请保持批判性思维，注重细节。",
            })
        
        # 根据复杂度决定子代理数量（最多3个）
        num_agents = min(len(roles), 2 + int(complexity * 2), 3)
        selected_roles = roles[:num_agents]
        
        # 构建子代理配置（复用主会话LLM，不单独配置模型）
        for i, role in enumerate(selected_roles):
            sub_agents.append({
                "id": f"auto-{role['name']}-{i}",
                "name": role["name"],
                "icon": role["icon"],
                "description": role["description"],
                "model_ref": "default",  # 复用主会话LLM配置
                "system_prompt": role["system_prompt"],
            })
        
        logger.info(
            "Auto-created %d sub-agents for task: %s",
            len(sub_agents),
            [a["name"] for a in sub_agents]
        )
        
        return sub_agents

    # ─────────── Cluster Parallel Execution ───────────

    async def _execute_cluster_parallel(
        self,
        user_input: str,
        sub_agents: list[dict],
        session_id: uuid.UUID,
    ) -> str | None:
        """
        真·并行集群执行
        
        使用 asyncio.gather 同时调用多个子代理，然后聚合结果
        """
        if len(sub_agents) < 2:
            return None
        
        logger.info(f"Starting parallel cluster execution with {len(sub_agents)} agents")
        
        # 推送进度：开始集群执行
        await self._emit_progress("cluster_start", f"启动 {len(sub_agents)} 个角色并行生成草稿...")
        
        try:
            from backend.agent.cluster_aggregator import (
                AggregationStrategy,
            )
            from backend.agent.cluster_executor import get_cluster_executor
            
            # 构建子任务
            sub_tasks = []
            for i, agent in enumerate(sub_agents):
                sub_tasks.append({
                    "id": f"agent-{i}",
                    "name": agent["name"],
                    "description": agent["description"],
                    "prompt": f"""用户请求：{user_input}

请根据你的专长给出回答。保持简洁，突出你的专业视角。""",
                    "agent_config": {
                        "agent_id": agent["id"],
                        "name": agent["name"],
                        "model_ref": agent["model_ref"],
                        "system_prompt": agent["system_prompt"],
                        "icon": agent["icon"],
                    },
                    "depends_on": [],
                    "metadata": {"original_index": i},
                })
            
            # 获取执行器
            executor = get_cluster_executor()
            
            # 定义进度回调（同步包装，兼容 executor 的调用方式）
            def progress_callback(task_id: str, progress: int, message: str):
                # 创建任务异步执行，避免阻塞 executor
                asyncio.create_task(self._emit_progress("cluster_progress", f"{message} ({progress}%)"))
            
            # 并行执行
            result = await executor.execute(
                task_description=user_input,
                sub_tasks=sub_tasks,
                aggregation_strategy=AggregationStrategy.SYNTHESIZE,
                progress_callback=progress_callback,
            )
            
            # 构建聚合结果
            if result.status.value == "completed":
                # 格式化各代理回复
                agent_responses = []
                for st in result.sub_tasks:
                    if st.status.value == "completed" and st.result:
                        agent_name = st.name
                        agent_icon = next((a["icon"] for a in sub_agents if a["name"] == agent_name), "🤖")
                        response_text = st.result.get("result", "") if isinstance(st.result, dict) else str(st.result)
                        agent_responses.append(f"{agent_icon} **{agent_name}**：{response_text}")
                
                # 添加聚合结果
                aggregated = result.aggregated_result
                if isinstance(aggregated, dict) and "synthesized" in aggregated:
                    final_text = f"""【多角色草稿汇总】

{chr(10).join(agent_responses)}

---

**综合结论**：
{aggregated['synthesized']}"""
                else:
                    final_text = f"""【多角色草稿汇总】

{chr(10).join(agent_responses)}"""
                
                # 推送完成事件
                await self._emit_progress("cluster_complete", "多角色草稿汇总完成")

                # 保存结果
                await self._persist_final_response(session_id, final_text)

                # 关键：cluster 路径在 run() 第 570 行提前 return，会跳过尾部统一的
                # idle 推送；若不在这里补推，前端气泡会一直停在「思考中」，
                # 直到用户手动停止才触发 idle 落盘。必须在 return 前显式恢复 idle。
                await self._push_status(session_id, "idle", "Ready")

                return final_text
            else:
                error_msg = f"多角色草稿执行失败: {result.error or '未知错误'}"
                await self._emit_progress("cluster_error", error_msg)
                # 失败路径同样会提前 return（见 run() 第 570 行），需补推状态避免前端卡「思考中」
                await self._push_status(session_id, "error", error_msg)
                return f"[多角色草稿] {error_msg}"
                
        except Exception as e:
            logger.error(f"Cluster parallel execution failed: {e}")
            await self._emit_progress("cluster_error", f"集群执行异常: {e}")
            return None  # 降级到单 LLM 模式

    # ─────────── WebSocket push helpers ───────────

    async def _emit_progress(self, kind: str, text: str) -> None:
        """推送通道/外部进度（不含工具细节）。失败静默，不影响主循环。"""
        sink = self.progress_sink
        if not sink or not text or not str(text).strip():
            return
        try:
            await sink(kind, str(text).strip())
        except Exception as e:
            logger.debug("progress_sink failed: %s", e)

    async def _push_status(
        self, session_id: uuid.UUID, state: str, detail: str
    ) -> None:
        """推送状态：优先 EventSinkPort，回落 ws_manager。"""
        sink = getattr(self, "event_sink", None)
        if sink is not None:
            try:
                await sink.push_status(session_id, state, detail or "")
                return
            except Exception as e:
                logger.debug("event_sink.push_status failed: %s", e)
        if self.ws_manager:
            await self.ws_manager.broadcast(
                session_id,
                StatusUpdate(
                    session_id=session_id,
                    state=state,
                    detail=detail,
                ).model_dump(mode="json"),
            )
        if state == "error" and detail:
            await self._emit_progress("error", detail)

    async def _push_stream(
        self,
        session_id: uuid.UUID,
        message_id: uuid.UUID,
        delta: str,
    ) -> None:
        """推送流式文本：优先 EventSinkPort，回落 ws_manager。"""
        sink = getattr(self, "event_sink", None)
        if sink is not None:
            try:
                # 兼容 message_id 关键字或位置
                try:
                    await sink.push_stream_delta(
                        session_id, delta, message_id=message_id
                    )
                except TypeError:
                    await sink.push_stream_delta(session_id, delta)
                return
            except Exception as e:
                logger.debug("event_sink.push_stream_delta failed: %s", e)
        if self.ws_manager:
            await self.ws_manager.broadcast(
                session_id,
                StreamDelta(
                    session_id=session_id,
                    message_id=message_id,
                    content=delta,
                ).model_dump(mode="json"),
            )

    async def _push_tool_event(
        self,
        session_id: uuid.UUID,
        *,
        phase: str,
        tool_call_id: str,
        name: str,
        arguments: dict[str, Any] | None = None,
        status: str = "running",
        result: str | None = None,
    ) -> None:
        """推送工具调用开始/结束事件，供前端实时渲染 tool 卡片"""
        if not self.ws_manager:
            return
        try:
            from backend.schemas.ws import ToolEvent

            # 结果截断，避免 WS 帧过大
            res = result
            if not isinstance(res, str) and res is not None:
                res = str(res)
            if isinstance(res, str) and len(res) > 8000:
                res = res[:8000] + "\n…[truncated]"

            # 只推送可 JSON 化的参数；剥离 _ws_manager 等私有注入
            safe_args = self._jsonable_tool_args(arguments)

            await self.ws_manager.broadcast(
                session_id,
                ToolEvent(
                    session_id=session_id,
                    phase=phase,  # type: ignore[arg-type]
                    tool_call_id=tool_call_id,
                    name=name,
                    arguments=safe_args,
                    status=status,  # type: ignore[arg-type]
                    result=res,
                ).model_dump(mode="json"),
            )
        except Exception as e:
            logger.warning(f"Failed to push tool_event: {e}")

    async def _maybe_push_screenshot(
        self,
        session_id: uuid.UUID,
        tool_name: str,
        tool_result: str,
    ) -> None:
        """从截图工具结果提取图像：支持 path 落盘 / data URL / base64，推送 WS。"""
        if not self.ws_manager:
            return
        try:
            import base64
            import json as _json
            import os
            import re
            from pathlib import Path

            b64: str | None = None
            image_url = ""
            mime = "image/png"
            raw = str(tool_result)

            # 1) data:image/...;base64,...（完整，非 omitted 截断）
            m = re.search(r"(data:image/([^;]+);base64,)([A-Za-z0-9+/=\s]{200,})", raw)
            if m and "...[omitted]" not in raw[m.start() : m.end() + 20]:
                mime = m.group(2) or "image/png"
                b64 = m.group(3).replace("\n", "").replace(" ", "")

            data_obj: dict | None = None
            if not b64:
                try:
                    data_obj = _json.loads(raw)
                except Exception:
                    # 工具结果可能是 "ok\n{json}"
                    mjson = re.search(r"(\{[\s\S]*\})\s*$", raw)
                    if mjson:
                        try:
                            data_obj = _json.loads(mjson.group(1))
                        except Exception:
                            data_obj = None

            path = None
            if isinstance(data_obj, dict):
                img = data_obj.get("image") or (data_obj.get("data") or {}).get("image")
                if isinstance(img, str) and len(img) > 200 and "omitted" not in img[:40]:
                    if img.startswith("data:image"):
                        mm = re.match(r"data:image/([^;]+);base64,(.+)", img, re.S)
                        if mm:
                            mime = mm.group(1)
                            b64 = mm.group(2).replace("\n", "")
                    else:
                        b64 = img
                path = (
                    data_obj.get("path")
                    or (data_obj.get("data") or {}).get("path")
                    or data_obj.get("filepath")
                )

            if not path:
                mp = re.search(
                    r"(?:path|filepath).{0,24}(/[\w./\\-]+\.(?:png|jpe?g|webp))",
                    raw,
                    re.I,
                )
                if mp:
                    path = mp.group(1)

            # 2) 落盘路径 → 读文件 + 生成可访问 URL
            if path and isinstance(path, str) and os.path.isfile(path):
                p = Path(path)
                if p.suffix.lower() in {".jpg", ".jpeg"}:
                    mime = "image/jpeg"
                elif p.suffix.lower() == ".webp":
                    mime = "image/webp"
                else:
                    mime = "image/png"
                # URL：经 /api/desktop/shots/{filename}
                image_url = f"/api/desktop/shots/{p.name}"
                if not b64:
                    try:
                        raw_bytes = p.read_bytes()
                        # WS 体积保护：>1.8MB 只走 URL
                        if len(raw_bytes) <= 1_800_000:
                            b64 = base64.b64encode(raw_bytes).decode("ascii")
                    except OSError:
                        pass

            if not b64 and not image_url:
                return

            from datetime import datetime, timezone

            from backend.schemas.ws import ScreenshotEvent

            payload_b64 = ""
            if b64:
                payload_b64 = (
                    b64
                    if b64.startswith("data:")
                    else f"data:{mime};base64,{b64}"
                )

            await self.ws_manager.broadcast(
                session_id,
                ScreenshotEvent(
                    session_id=session_id,
                    image_base64=payload_b64,
                    image_url=image_url or "",
                    tool_name=tool_name,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                ).model_dump(mode="json"),
            )
        except Exception as e:
            logger.debug(f"Screenshot push skipped: {e}")

    @staticmethod
    def _jsonable_tool_args(arguments: dict[str, Any] | None) -> dict[str, Any]:
        """过滤不可 JSON 序列化 / 内部注入字段，供 WS 与落库。"""
        if not isinstance(arguments, dict):
            return {}
        out: dict[str, Any] = {}
        skip_keys = {"ws_manager", "connection_manager"}
        for k, v in arguments.items():
            ks = str(k)
            if ks.startswith("_") or ks in skip_keys:
                continue
            if "ConnectionManager" in type(v).__name__:
                continue
            try:
                out[ks] = json.loads(json.dumps(v, default=str, ensure_ascii=False))
            except Exception:
                out[ks] = str(v)[:500]
        return out

    async def _push_task_update(
        self,
        session_id: uuid.UUID,
        task_id: Any,
        progress: int,
        status: str,
        message: str,
    ) -> None:
        """推送任务进度更新到前端"""
        if self.ws_manager:
            await self.ws_manager.broadcast(
                session_id,
                {
                    "type": "task_update",
                    "session_id": str(session_id),
                    "task_id": str(task_id),
                    "progress": progress,
                    "status": status,
                    "message": message,
                },
            )

    async def _push_memory_updated(
        self, session_id: uuid.UUID, diff: str
    ) -> None:
        """P0-6: 推送长期记忆更新通知"""
        if self.ws_manager:
            await self.ws_manager.broadcast(
                session_id,
                MemoryUpdated(
                    session_id=session_id,
                    type="memory_updated",
                    diff=diff,
                ).model_dump(mode="json"),
            )

    async def _push_goal_update(self, session_id: uuid.UUID) -> None:
        """推送 Goal / Todo 进度到前端面板"""
        if not self.ws_manager:
            return
        try:
            from backend.agent.goal_state import get_goal

            g = get_goal(session_id)
            payload = {
                "type": "goal_update",
                "session_id": str(session_id),
                "goal": g.to_dict() if g else None,
            }
            await self.ws_manager.broadcast(session_id, payload)
        except Exception as e:
            logger.warning(f"Failed to push goal_update: {e}")
    # ─────────── Transactional persistence helpers ───────────

    async def _persist_user_input(
        self, session_id: uuid.UUID, enriched_input: str
    ) -> None:
        """原子化保存用户输入：TTL 清理 + Message + CtxItem"""
        if self.message_repo is None or self.ctx_item_repo is None:
            return
        async with get_db_context() as db:
            msg_repo = AsyncMessageRepository(db)
            ctx_repo = AsyncCtxItemRepository(db)
            await ctx_repo.prune_by_ttl(session_id=session_id, ttl="session")
            await msg_repo.save_message(session_id, "user", enriched_input)
            await ctx_repo.create({
                "session_id": session_id,
                "scope": "session",
                "kind": "message",
                "key": f"user_{int(datetime.now(timezone.utc).timestamp() * 1000)}",
                "value": enriched_input,
                "tokens": max(8, round(len(enriched_input) / 3.4)),
                "pinned": False,
                "ttl": "session",
                "origin": f"agent:{self.agent_name}",
            })

    async def _persist_tool_start(
        self, session_id: uuid.UUID, tool_name: str
    ) -> uuid.UUID | None:
        """原子化创建 Tool 任务并置为 running/50%。

        session 若已被删除（换库/前端删会话/map 过期），不再抛崩整轮 agent，
        仅跳过任务进度落库。
        """
        if self.task_repo is None:
            return None
        try:
            async with get_db_context() as db:
                # 先确认 session 仍在，避免 FK 炸穿整个 channel 回复
                sess_repo = AsyncSessionRepository(db)
                if await sess_repo.get_by_id(session_id) is None:
                    logger.warning(
                        "Skip tool task: session %s missing when starting %s",
                        session_id,
                        tool_name,
                    )
                    return None
                task_repo = AsyncTaskRepository(db)
                task = await task_repo.create_task(
                    session_id=session_id,
                    name=f"skill:{tool_name}",
                    description=f"Executing skill '{tool_name}'",
                )
                await task_repo.update_progress(task.id, progress=50, status="running")
                await task_repo.append_log(
                    task.id, {"level": "info", "message": f"Running {tool_name}"}
                )
                return task.id
        except Exception as e:
            logger.warning("Failed to persist tool start for %s: %s", tool_name, e)
            return None

    async def _persist_tool_completion(
        self,
        session_id: uuid.UUID,
        task_id: uuid.UUID | None,
        tool_name: str,
        tool_result: str,
        query: str = "",
    ) -> None:
        """原子化完成 Tool 任务：100% + 日志 + 可选 RAG CtxItem"""
        if self.task_repo is None:
            return
        async with get_db_context() as db:
            task_repo = AsyncTaskRepository(db)
            if task_id is not None:
                await task_repo.update_progress(
                    task_id, progress=100, status="completed"
                )
                await task_repo.append_log(
                    task_id, {"level": "info", "message": f"Result length: {len(tool_result)}"}
                )
            if tool_name == "search_knowledge_base" and self.ctx_item_repo is not None:
                ctx_repo = AsyncCtxItemRepository(db)
                await ctx_repo.create({
                    "session_id": session_id,
                    "scope": "knowledge",
                    "kind": "rag",
                    "key": f"rag_query_{int(datetime.now(timezone.utc).timestamp())}",
                    "value": f"Query: {query}\n\n{tool_result}",
                    "tokens": max(8, round(len(tool_result) / 3.4)),
                    "pinned": False,
                    "origin": "rag_skill",
                })

    async def _persist_tool_failure(
        self,
        task_id: uuid.UUID | None,
        tool_name: str,
        error: str,
    ) -> None:
        """原子化标记 Tool 任务失败"""
        if self.task_repo is None or task_id is None:
            return
        async with get_db_context() as db:
            task_repo = AsyncTaskRepository(db)
            await task_repo.update_progress(task_id, progress=0, status="failed")
            await task_repo.append_log(
                task_id, {"level": "error", "message": error}
            )

    async def _persist_final_response(
        self, session_id: uuid.UUID, final_content: str
    ) -> None:
        """原子化保存最终回复：Message + CtxItem + Session 状态 + 通知"""
        text = (final_content or "").strip()
        if not text:
            text = (
                "（本轮未生成可见正文：可能只调用了工具且后续未总结。"
                "请再发一条消息，或点「请继续」。若持续空白，可检查设备/RAG 相关工具是否报错。）"
            )
        async with get_db_context() as db:
            msg_repo = AsyncMessageRepository(db)
            ctx_repo = AsyncCtxItemRepository(db)
            session_repo = AsyncSessionRepository(db)

            token_estimate = max(8, round(len(text) / 3.4))
            await msg_repo.save_message(session_id, "assistant", text, token_count=token_estimate)
            if self.ctx_item_repo is not None:
                await ctx_repo.create({
                    "session_id": session_id,
                    "scope": "session",
                    "kind": "message",
                    "key": f"assistant_{int(datetime.now(timezone.utc).timestamp() * 1000)}",
                    "value": text,
                    "tokens": token_estimate,
                    "pinned": False,
                    "ttl": "session",
                    "origin": f"agent:{self.agent_name}",
                })
            await session_repo.update(
                session_id,
                {"status": "idle", "updated_at": datetime.now(timezone.utc)},
            )
            if self.notification_repo is not None and self.user_id is not None:
                await AsyncNotificationRepository(db).create({
                    "user_id": self.user_id,
                    "type": "message",
                    "title": "New assistant message",
                    "content": text[:200],
                    "data": {"session_id": str(session_id)},
                    "source_id": str(session_id),
                })

    def _build_user_input_with_attachments(
        self, user_input: str, attachments: list[dict[str, Any]]
    ) -> str:
        """将附件内容注入到用户输入中"""
        if not attachments:
            return user_input

        parts = [user_input]
        for i, att in enumerate(attachments, 1):
            filename = att.get("filename", f"附件{i}")
            text_content = att.get("text_content")
            file_type = att.get("type", "unknown")
            file_url = att.get("url", "")

            parts.append(f"\n\n[附件 {i}: {filename}]")
            if text_content:
                # 文本文件直接附内容
                content_preview = text_content[:8000]
                if len(text_content) > 8000:
                    content_preview += "\n...（内容已截断）"
                parts.append(content_preview)
            elif file_type in {"jpg", "jpeg", "png", "gif", "webp", "bmp", "svg"}:
                parts.append(f"[图片文件] {file_url}")
            else:
                parts.append(f"[文件类型: {file_type}] {file_url}")

        return "\n".join(parts)

