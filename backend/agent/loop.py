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

from backend.database import get_db_context
from backend.repositories import (
    ContextFlowRepository,
    CtxItemRepository,
    MessageRepository,
    NotificationRepository,
    SessionRepository,
    TaskRepository,
)
from backend.schemas.ws import MemoryUpdated, StatusUpdate, StreamDelta
from backend.repositories.context_repo import AsyncContextFlowRepository, AsyncCtxItemRepository
from backend.repositories.message_repo import AsyncMessageRepository
from backend.repositories.notification_repo import AsyncNotificationRepository
from backend.repositories.session_repo import AsyncSessionRepository
from backend.repositories.task_repo import AsyncTaskRepository
from backend.repositories.skill_repo import AsyncSkillRepository
from backend.repositories.tool_repo import AsyncToolRepository
from backend.services.llm import LLMServiceFactory
from backend.services.tools import ToolRegistry
from backend.skills import SkillRegistry
from backend.skills.dynamic import DynamicSkill
from backend.core.config import settings
from backend.agent.robust import is_continue_phrase, is_transient_llm_error

from .context import ContextManager

logger = logging.getLogger(__name__)

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



class NexusAgentLoop:
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
            )
            # Null 实现会返回“不可用”文案 — 不应注入
            if context and context.strip() and "知识库检索不可用" not in context:
                logger.info(
                    f"Injected RAG context ({len(context)} chars) top_k={k} for: {user_input[:50]}"
                )
                self._append_to_system(messages, f"# 相关知识（RAG）\n{context}")
        except Exception as e:
            logger.warning(f"RAG context injection failed (degraded to local): {e}")

        return messages

    async def _inject_wiki_context(
        self, messages: list[dict[str, Any]], user_input: str
    ) -> list[dict[str, Any]]:
        """把 Wiki 图谱中匹配的实体摘要拼进 system。"""
        q = (user_input or "").strip()
        if len(q) < 2:
            return messages
        try:
            from backend.repositories.wiki_repo import AsyncWikiEntityRepository

            repo = AsyncWikiEntityRepository()
            ents = await repo.search(q) or []
            if not ents:
                return messages
            lines = ["# Wiki 图谱相关实体"]
            for e in ents[:6]:
                lines.append(
                    f"- **{e.name}** ({getattr(e, 'entity_type', 'concept')})"
                    + (f"：{e.description}" if e.description else "")
                )
            self._append_to_system(messages, "\n".join(lines))
            logger.info("Injected %s wiki entities for query", min(6, len(ents)))
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
    ) -> str:
        """
        执行 Agent Loop（同一 session 并发安全：使用 asyncio.Lock 串行执行）
        """
        # 安全修复：获取 session 级锁，防止同一 session 的并发竞态
        lock = _get_session_lock(session_id)
        async with lock:
            return await self._run_locked(
                session_id, user_input, attachments, mode, sub_agent_ids or []
            )

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

        # @device 远程执行（L1）：命中则短路，不进工具循环
        if self.user_id and user_input and "@" in user_input:
            try:
                from backend.services.remote.dispatch import try_handle_at_device

                card = await try_handle_at_device(self.user_id, user_input)
                if card is not None:
                    try:
                        await self._persist_user_input(session_id, user_input, attachments)
                    except Exception as e:
                        logger.warning("persist user input (@device) failed: %s", e)
                    try:
                        await self._persist_final_response(session_id, card)
                    except Exception as e:
                        logger.warning("persist final response (@device) failed: %s", e)
                        # fallback plain save
                        try:
                            await self.message_repo.save_message(session_id, "assistant", card)
                        except Exception as e2:
                            logger.error("fallback save assistant message failed: %s", e2)
                    await self._push_status(session_id, "idle", "remote device command done")
                    return card
            except Exception as e:
                logger.warning("@device dispatch failed: %s", e)

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

        # 3. 加载历史消息（保留 tool_calls / tool_call_id，避免多轮工具链断裂）
        # 动态 limit：按 context_window 估算最大消息数，避免只加载 100 条导致压缩无法触发
        _ctx_win = int(getattr(settings, "context_window", 128_000) or 128_000)
        _est_limit = max(200, _ctx_win // 50)  # 每 50 tokens 一条消息的保守估计
        history = await self.message_repo.get_history_by_session(
            session_id, limit=_est_limit
        )
        logger.info("Loaded %d history messages for session %s (limit=%d)", len(history), session_id, _est_limit)
        history_dicts: list[dict[str, Any]] = []
        for h in history:
            if h.role not in ("user", "assistant", "tool"):
                continue
            item: dict[str, Any] = {"role": h.role, "content": h.content or ""}
            if h.role == "assistant" and getattr(h, "tool_calls", None):
                item["tool_calls"] = h.tool_calls
            # tool_call_id 可能存在 JSON tool_calls 旁路或 content 元数据中
            if h.role == "tool":
                tc_meta = getattr(h, "tool_calls", None)
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

        # 4. 组装 messages（CtxItem 优先，fallback 到四维度配置）
        messages, accessed_items, total_tokens = await self.context_manager.build_messages(
            session_id=session_id,
            user_input=enriched_input,
            history=history_for_build,
            fallback_config=config,
            mode=mode,
        )

        # 记录初始上下文访问流
        if accessed_items and self.context_flow_repo is not None:
            await self._record_flow(session_id, self.agent_name, accessed_items, total_tokens)

        # P0-3: Auto Optimize 自动触发
        await self._check_auto_optimize(session_id, config, total_tokens)

        # 5. 加载 Skills/Tools（合并后的统一 Tool 体系）
        # 关键：session config 默认 skills=[] 会被 SkillRegistry 当成「全部禁用」
        # 约定：缺省 / 空列表 / 含 "*"  → 启用全部内置 skill；显式名单才过滤
        raw_skills = config.get("skills", None)
        raw_tools = config.get("tools", None)
        if raw_skills is None or raw_skills == [] or raw_skills == ["*"]:
            enabled_skills = None  # 全部 skill
        else:
            enabled_skills = list(raw_skills)
        if raw_tools is None or raw_tools == [] or raw_tools == ["*"]:
            enabled_tools_filter: list[str] | None = None
        else:
            enabled_tools_filter = list(raw_tools)

        # 模式附加 skill（在「全部」或名单上叠加）
        mode_extra: list[str] = []
        if mode == "search":
            mode_extra.append("web_search")
        if mode == "ppt":
            mode_extra.append("generate_ppt")
        if mode == "report":
            mode_extra.append("generate_report")
        if mode == "goal":
            mode_extra.append("manage_goal")
        if mode_extra and enabled_skills is not None:
            enabled_skills = list(set(enabled_skills + mode_extra))

        tools = await self._load_tools(session_id, enabled_skills, enabled_tools_filter)
        logger.info(
            f"Loaded {len(tools)} tools for session {session_id} "
            f"(skills={'ALL' if enabled_skills is None else enabled_skills})"
        )

        # Goal 模式：更高轮次 + 初始化 goal 状态
        # Goal 模式：更高轮次 + 初始化 goal 状态
        goal_mode = mode == "goal"
        if goal_mode:
            from backend.agent.goal_state import ensure_goal, get_goal, load_goal_from_db, save_goal_to_db

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
                            "Goal runtime status (keep updated via manage_goal):\n"
                            + g0.summary_for_llm()
                        ),
                    }
                )

        # 集群模式：注入所选子代理人物设定（协调者视角）
        cluster_mode = mode == "cluster" or bool(sub_agent_ids)
        if cluster_mode and sub_agent_ids:
            try:
                from backend.repositories.sub_agent_repo import AsyncSubAgentRepository

                repo = AsyncSubAgentRepository()
                roster_lines: list[str] = []
                for aid in sub_agent_ids:
                    try:
                        agent_row = await repo.get_by_id(uuid.UUID(str(aid)))
                    except Exception:
                        agent_row = None
                    if not agent_row or not getattr(agent_row, "enabled", True):
                        continue
                    prompt = (agent_row.system_prompt or "").strip()
                    if len(prompt) > 1200:
                        prompt = prompt[:1200] + "…"
                    roster_lines.append(
                        f"### {agent_row.icon or '🤖'} {agent_row.name}\n"
                        f"- 任务名称: {agent_row.name}\n"
                        f"- 职责: {agent_row.description or '（无）'}\n"
                        f"- 模型: {agent_row.model_ref}\n"
                        f"- 系统提示词:\n{prompt or '（未配置）'}"
                    )
                if roster_lines:
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "【集群模式 Cluster Mode】你是集群协调者。用户已选择以下子代理参与本轮协作。\n"
                                "请按子代理分工推进任务：综合各自专长给出统一、可执行的结果；"
                                "需要时在回复中标明各子代理视角（如「审查员：…」「研究员：…」）。\n\n"
                                + "\n\n".join(roster_lines)
                            ),
                        }
                    )
                    logger.info(
                        "Cluster mode: injected %s sub-agents for session %s",
                        len(roster_lines),
                        session_id,
                    )
            except Exception as e:
                logger.warning("cluster roster inject failed: %s", e)

        # 6. 获取 LLM 服务
        llm_service = LLMServiceFactory.get_service()

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

        # 7. RAG + Wiki 注入（上下文紧张时加强 RAG top_k）
        strengthen_rag = bool(compress_meta.get("compressed")) or (
            total_tokens > int(getattr(settings, "context_window", 128_000) or 128_000) * 0.55
        )
        messages = await self._inject_rag_context(
            messages, enriched_input, top_k=3, strengthen=strengthen_rag
        )
        messages = await self._inject_wiki_context(messages, enriched_input)

        # 8. Agent Loop
        final_content = ""
        _sft_tools: list = []  # SFT usage log buffer
        accumulated_content = ""
        goal_nudge_count = 0

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
        _multi_source_pending = False
        _suppress_content_stream = False
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
                session_id, "thinking", f"Thinking (round {iteration + 1})..."
            )

            # 调用 LLM（流式）
            accumulated_content = ""
            accumulated_reasoning = ""
            tool_calls = []

            try:
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
                    # 思考中可打断
                    if self._should_stop:
                        logger.info(
                            "Stop during LLM stream for session %s", session_id
                        )
                        break

                    # 推送流式文本到前端
                    if chunk.delta:
                        accumulated_content += chunk.delta
                        if not _suppress_content_stream:
                            await self._push_stream(
                                session_id, message_id, chunk.delta
                            )

                    # 思考链增量（不进前端 stream，仅汇总给通道 progress）
                    rdelta = getattr(chunk, "reasoning_delta", None) or ""
                    if rdelta:
                        accumulated_reasoning += rdelta

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

            except Exception as e:
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

            # 引擎层：流式无 usage 时用粗估回写，驱动后续是否再压缩
            try:
                from backend.agent.context_engine import get_context_engine
                from backend.agent.token_meter import TokenMeter

                eng = get_context_engine()
                est = TokenMeter(
                    context_window=int(getattr(settings, "context_window", 128_000) or 128_000)
                ).estimate_messages(messages)
                eng.update_from_response({
                    "prompt_tokens": est,
                    "completion_tokens": max(8, round(len(accumulated_content or "") / 3.4)),
                })
            except Exception:
                pass

            # 本轮 LLM 成功，重置失败计数
            self._llm_fail_streak = 0

            # 通道进度：优先 reasoning，其次可见 content（不含 tool 调用细节）
            _think = (accumulated_reasoning or accumulated_content or "").strip()
            if _think:
                await self._emit_progress("thinking", _think[:1200])

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
                    await self.message_repo.save_message(
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

                # 执行每个 tool call
                for tc in tool_calls:
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

                    await self._push_tool_event(
                        session_id,
                        phase="start",
                        tool_call_id=tc.id,
                        name=tc.name,
                        arguments=args_dict,
                        status="running",
                    )
                    await self._push_status(
                        session_id,
                        "tool_executing",
                        f"Executing {tc.name}...",
                    )

                    # 创建 Task（用于进度追踪）
                    task_id = await self._persist_tool_start(session_id, tc.name)
                    if task_id is not None:
                        await self._push_task_update(
                            session_id, task_id, 50, "running", f"Running {tc.name}"
                        )

                    tool_result = ""
                    try:
                        # v3.0: 统一从 ToolRegistry 执行工具
                        from backend.tools.registry import ToolRegistry as UnifiedToolRegistry

                        tool = UnifiedToolRegistry.get(tc.name)
                        if tool is not None:
                            validated_args = self._validate_tool_args(tool.parameters, tc.arguments)
                            if self.user_id is not None:
                                validated_args["user_id"] = str(self.user_id)
                            validated_args["_session_id"] = str(session_id)
                            _tool_timeout = float(
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
                            query = (
                                tc.arguments.get("query", "")
                                if tc.name == "search_knowledge_base"
                                else ""
                            )
                        else:
                            # 兼容旧方式：直接查 SkillRegistry 和 DB
                            skill = SkillRegistry.get_skill(tc.name)
                            if skill is not None:
                                validated_args = self._validate_tool_args(skill.parameters, tc.arguments)
                                if self.user_id is not None:
                                    validated_args["user_id"] = str(self.user_id)
                                validated_args["_session_id"] = str(session_id)
                                tool_result = await skill.execute(**validated_args)
                                query = ""
                            else:
                                # 尝试执行数据库中的自定义 Skill
                                skill_repo = AsyncSkillRepository()
                                db_skill = await skill_repo.get_skill_by_name(tc.name)
                                if db_skill is not None and db_skill.enabled:
                                    dynamic = DynamicSkill.from_db(db_skill)
                                    validated_args = self._validate_tool_args(dynamic.parameters, tc.arguments)
                                    if self.user_id is not None:
                                        validated_args["user_id"] = str(self.user_id)
                                    validated_args["_session_id"] = str(session_id)
                                    tool_result = await dynamic.execute(**validated_args)
                                    query = ""
                                else:
                                    # 尝试从 ToolRegistry 执行
                                    tool_repo = AsyncToolRepository()
                                    db_tool = await tool_repo.get_tool_by_name(tc.name)
                                    if db_tool is not None and db_tool.enabled:
                                        tool_result = await ToolRegistry.execute_tool(db_tool, tc.arguments)
                                        query = ""
                                    else:
                                        tool_result = f"[Error] Tool '{tc.name}' not found or disabled"
                                        query = ""

                        # skill/tool 可能返回 None，先规范成 str
                        if tool_result is None:
                            tool_result = ""
                        elif not isinstance(tool_result, str):
                            tool_result = str(tool_result)
                        MAX_TOOL_RESULT_LENGTH = getattr(settings, "max_tool_result_length", 12_000)
                        if len(tool_result) > MAX_TOOL_RESULT_LENGTH:
                            tool_result = (
                                tool_result[:MAX_TOOL_RESULT_LENGTH]
                                + f"\n\n[截断: 结果超过 {MAX_TOOL_RESULT_LENGTH} 字符]"
                            )

                        await self._persist_tool_completion(
                            session_id, task_id, tc.name, tool_result, query
                        )
                        if task_id is not None:
                            await self._push_task_update(
                                session_id, task_id, 100, "completed", f"Completed {tc.name}"
                            )

                        # 实时推送：工具成功结束
                        await self._push_tool_event(
                            session_id,
                            phase="end",
                            tool_call_id=tc.id,
                            name=tc.name,
                            arguments=args_dict,
                            status="completed",
                            result=tool_result,
                        )
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
                            _sft_tools.append(
                                {
                                    "name": tc.name,
                                    "arguments": args_dict if isinstance(args_dict, dict) else {},
                                    "result": str(tool_result)[:2000],
                                    "ok": True,
                                }
                            )
                        except Exception:
                            pass

                        # manage_goal 结果推送到前端 Goal 面板
                        if tc.name == "manage_goal":
                            await self._push_goal_update(session_id)
                            await save_goal_to_db(session_id)

                    except asyncio.TimeoutError:
                        _to = float(getattr(settings, "agent_tool_timeout_seconds", 180) or 180)
                        tool_result = f"[Error] Tool '{tc.name}' timed out after {_to:.0f}s"
                        query = ""
                        logger.warning("Tool %s timed out after %ss", tc.name, _to)
                        try:
                            _sft_tools.append(
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
                        tool_result = f"[Error] Failed to execute {tc.name}: {e}"
                        try:
                            _sft_tools.append(
                                {
                                    "name": tc.name,
                                    "arguments": args_dict if isinstance(args_dict, dict) else {},
                                    "result": str(tool_result)[:2000],
                                    "ok": False,
                                }
                            )
                        except Exception:
                            pass
                        await self._persist_tool_failure(task_id, tc.name, str(e))
                        if task_id is not None:
                            await self._push_task_update(
                                session_id, task_id, 0, "failed", str(e)
                            )
                        await self._push_tool_event(
                            session_id,
                            phase="end",
                            tool_call_id=tc.id,
                            name=tc.name,
                            arguments=args_dict if isinstance(args_dict, dict) else {},
                            status="failed",
                            result=tool_result,
                        )

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
                        await self.message_repo.save_message(
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
                _last_tool_round_count = len(tool_calls)
                _tool_rounds += 1
                # 多工具并行时：强制下一轮「聚合」而非并列甩多个答案
                if _last_tool_round_count >= 2:
                                    _multi_source_pending = True
                                    _suppress_content_stream = True
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
                # 工具轮后：L1 周期性截断 + 超阈值 pipeline + checkpoint
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
                        await self._push_status(
                            session_id, "thinking", "Goal completed — summarizing..."
                        )
                continue

            else:
                # 没有 tool calls
                if goal_mode:
                    from backend.agent.goal_state import get_goal

                    g = get_goal(session_id)
                    incomplete = g is not None and not g.is_complete()
                    # 无 todo 也算未规划完成（允许 1 次纯文本规划后必须建 todo）
                    no_plan = g is None or (not g.todos and g.status != "completed")
                    if (incomplete or no_plan) and goal_nudge_count < 8 and iteration < _seg_size - 1:
                        goal_nudge_count += 1
                        # 把当前文本当作中间思考，要求继续
                        if accumulated_content:
                            messages.append(
                                {
                                    "role": "assistant",
                                    "content": accumulated_content,
                                }
                            )
                            # 中间内容推到流，便于 UI 展示
                            await self._push_status(
                                session_id,
                                "thinking",
                                f"Goal 未完成，继续执行 ({goal_nudge_count})…",
                            )
                        nudge = (
                            "Goal 尚未达成。请：\n"
                            "1) 若还没有 todo，调用 manage_goal 创建任务列表；\n"
                            "2) 推进未完成项并 update_todo；\n"
                            "3) 全部完成后 manage_goal(action=complete) 再给出最终总结。\n"
                            "不要在未完成时停止。"
                        )
                        if g:
                            nudge += "\n\n" + g.summary_for_llm()
                        messages.append({"role": "user", "content": nudge})
                        logger.info(
                            f"Goal nudge #{goal_nudge_count} for session {session_id}"
                        )
                        continue

                # 得到最终回复
                final_content = accumulated_content
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
                        )
                    except Exception:
                        pass

        # 正常结束则清理 checkpoint
        try:
            from backend.agent.checkpoint import clear_checkpoint
            from backend.agent.goal_state import get_goal

            g_done = get_goal(session_id) if goal_mode else None
            if not self._should_stop and (not goal_mode or (g_done is None or g_done.is_complete())):
                await clear_checkpoint(session_id)
        except Exception:
            pass

        # 7.5 多信源最终聚合（额外一次无工具 LLM，避免「四个都对」并列）
        try:
            if final_content and not self._should_stop:
                _before = final_content
                final_content = await self._maybe_aggregate_multi_source(
                    llm_service=llm_service,
                    session_id=session_id,
                    user_input=user_input,
                    draft=final_content,
                    tool_rounds=_tool_rounds,
                    last_tool_count=_last_tool_round_count,
                    multi_pending=_multi_source_pending,
                )
                if final_content and (
                    _suppress_content_stream
                    or final_content != _before
                    or _multi_source_pending
                ):
                    try:
                        await self._push_stream(session_id, uuid.uuid4(), final_content)
                    except Exception as pe:
                        logger.debug("push aggregated stream skipped: %s", pe)
                _suppress_content_stream = False
        except Exception as e:
            logger.warning("multi-source aggregate skipped: %s", e)

        # 7.6 TEE 自主进化：验收/归因/过门后 auto_apply（默认关总开关）
        try:
            from backend.evolution.config import get_evolution_config
            from backend.evolution.manager import get_evolution_manager

            if get_evolution_config().enabled and final_content and not self._should_stop:
                await get_evolution_manager().on_turn_final(
                    str(session_id),
                    user_input=user_input or "",
                    final_content=final_content or "",
                )
        except Exception as e:
            logger.warning("evolution turn hook skipped: %s", e)

        # 7.7 SFT / 使用日志（设置里开关，默认关）
        try:
            from backend.services.sft_collector import collect_if_enabled

            await collect_if_enabled(
                session_id=str(session_id),
                user_input=user_input or "",
                assistant_output=final_content or "",
                tools=list(_sft_tools),
                meta={"source": "agent_loop"},
            )
        except Exception as e:
            logger.debug("sft collect skipped: %s", e)

        # 8. 保存最终回复 + 同步 CtxItem + 状态 + 通知（同一事务）
        try:
            await self._persist_final_response(session_id, final_content)
        except Exception as e:
            logger.error(f"Failed to persist final response: {e}")
            # 兜底：至少把状态恢复为 idle
            try:
                await self.session_repo.update_status(session_id, "idle")
            except Exception as status_err:
                logger.error(f"Failed to update session status: {status_err}")

        # 9. 推送状态为 idle（无论成功或失败都恢复状态）
        await self._push_status(session_id, "idle", "Ready")

        logger.info(f"Agent loop completed for session {session_id}")
        return final_content

    # ─────────── P0 helpers ───────────

    def _looks_like_multi_answer(self, text: str) -> bool:
        """启发式：模型把多个信源原样并列。"""
        if not text or len(text) < 80:
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
        if text.count("## ") >= 3:
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
        """多工具/多信源场景下再调用一次 LLM（无 tools）聚合成单一用户可读答复。"""
        if not draft or not str(draft).strip():
            return draft

        need = bool(multi_pending) or last_tool_count >= 2 or (
            tool_rounds >= 1 and self._looks_like_multi_answer(draft)
        )
        if not need and self._looks_like_multi_answer(draft):
            need = True
        if not need:
            return draft
        if len(draft) < 120 and last_tool_count < 2 and not multi_pending:
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
            "- 保留关键事实（气温、天气、时间等），去掉重复；\n"
            "- 禁止答案1/2/3 并列；\n"
            "- 冲突时选更具体、更新、更一致的说法，可一句说明；\n"
            "- 不要提及内部工具名堆砌；可用「综合公开气象数据」一句带过；\n"
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
        """使用JSON Schema校验tool call参数"""
        if not schema:
            return arguments
        try:
            from jsonschema import validate, ValidationError
            validate(instance=arguments, schema=schema)
        except ImportError:
            pass  # jsonschema未安装时跳过校验
        except ValidationError as e:
            raise ValueError(f"Invalid tool arguments: {e.message}")
        return arguments

    async def _load_tools(
        self,
        session_id: uuid.UUID,
        enabled_skills: list[str] | None,
        enabled_tools_filter: list[str] | None = None,
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
        """推送状态更新到前端；同步 mirror 到 progress_sink（通道思考流）"""
        if self.ws_manager:
            await self.ws_manager.broadcast(
                session_id,
                StatusUpdate(
                    session_id=session_id,
                    state=state,
                    detail=detail,
                ).model_dump(mode="json"),
            )
        # 社交通道只推「真实思考内容」，不推「第 N 轮思考中」这类硬编码状态
        if state == "error" and detail:
            await self._emit_progress("error", detail)

    async def _push_stream(
        self,
        session_id: uuid.UUID,
        message_id: uuid.UUID,
        delta: str,
    ) -> None:
        """推送流式文本到前端"""
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
            if isinstance(res, str) and len(res) > 8000:
                res = res[:8000] + "\n…[truncated]"
            await self.ws_manager.broadcast(
                session_id,
                ToolEvent(
                    session_id=session_id,
                    phase=phase,  # type: ignore[arg-type]
                    tool_call_id=tool_call_id,
                    name=name,
                    arguments=arguments or {},
                    status=status,  # type: ignore[arg-type]
                    result=res,
                ).model_dump(mode="json"),
            )
        except Exception as e:
            logger.warning(f"Failed to push tool_event: {e}")

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

