"""PLAN 阶段 0.6：Workforce Dispatcher（唤醒执行器）。

「休眠-唤醒-续作」的实现：
- 休眠：无常驻 agent 进程——无事时什么都不跑（天然零成本休眠）
- 唤醒：inbox 有工单 → 为对应身份创建 kernel 进程（编制内权限/预算）
- 续作：身份有专属 workforce session（历史对话即 Episodic 上下文）+
  Identity Memory 注入 prompt（人格/职责/方法论常驻）

红线：
- 唤醒路径全程过 kernel.mediate（capabilities=身份权限档案）+
  token 预算扣减——异步入口不得绕过权限与预算（PLAN §3.f）
- 编制内串行：同一身份同时在手一单（InboxService.claim_next 保证）
- 单工单超时熔断（agent_inbox_item_timeout 秒）
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from backend.core.config import settings

logger = logging.getLogger(__name__)

_DEFAULT_ITEM_TIMEOUT = 600.0


class WorkforceDispatcher:
    """工单派遣器：扫描 inbox → 唤醒身份 → 执行 → 回写。"""

    def __init__(
        self,
        kernel: Any,
        inbox: Any,
        registry: Any,
        session_factory: Any,
        *,
        poll_seconds: float = 10.0,
        item_timeout: float = _DEFAULT_ITEM_TIMEOUT,
        executor: Any = None,
    ) -> None:
        self._kernel = kernel
        self._inbox = inbox
        self._registry = registry
        self._session_factory = session_factory
        self._poll_seconds = max(1.0, float(poll_seconds))
        self._item_timeout = max(30.0, float(item_timeout))
        # 执行器注入点：None = 生产路径（NexusAgentLoop 真 LLM）；
        # 测试/定制场景注入 async fn(ident, item, kernel_process_id, kernel) -> str
        self._executor = executor
        self._busy: set[str] = set()  # 在手单的身份 id（编制内串行）
        self._running = False
        # Worker 池（Alpha Review #2）：identity_id → 长生命周期 loop 实例
        self._workers: dict[str, Any] = {}

    async def run_forever(self) -> None:
        """后台主循环（lifespan _spawn_bg 拉起）。"""
        self._running = True
        logger.info("workforce dispatcher started (poll=%.0fs)", self._poll_seconds)
        while self._running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("dispatcher tick 失败（下一轮继续）: %s", e)
            try:
                await asyncio.sleep(self._poll_seconds)
            except asyncio.CancelledError:
                raise

    async def stop(self) -> None:
        self._running = False

    async def _worker_for(self, ident: Any) -> Any:
        """WorkforceWorker 池（Alpha Review #2）：per-identity 长生命周期
        loop 实例——repo 引用 / RAG 懒加载缓存 / context_manager 跨工单复用，
        不再每单重新装配。

        安全性：dispatcher 以 busy_identity_ids 保证同身份同时只派一单
        （tick 内串行），故 per-identity 单实例无并发冲突；
        run 级状态由 _reset_run_state() 在每次派发前显式归零。
        身份归档/能力大改后调用 evict_worker 释放。"""
        key = str(ident.id)
        loop = self._workers.get(key)
        if loop is None:
            from backend.agent import NexusAgentLoop
            from backend.api.dependencies import (
                get_context_flow_repo,
                get_ctx_item_repo,
                get_message_repo,
                get_notification_repo,
                get_session_repo,
                get_task_repo,
            )

            loop = NexusAgentLoop(
                session_repo=await get_session_repo(),
                message_repo=await get_message_repo(),
                task_repo=await get_task_repo(),
                ctx_item_repo=await get_ctx_item_repo(),
                context_flow_repo=await get_context_flow_repo(),
                ws_manager=None,
                user_id=ident.user_id,
                notification_repo=await get_notification_repo(),
            )
            self._workers[key] = loop
            logger.info("workforce worker 上岗 ident=%s name=%s", key[:8], ident.name)
        return loop

    def evict_worker(self, identity_id: Any) -> None:
        """身份归档/能力大改后释放其 worker（下次派发重新装配）。"""
        self._workers.pop(str(identity_id), None)

    def _effective_budget(self, ident: Any) -> int | None:
        """有效预算：身份默认预算优先；未设置时给兜底——
        异步无人值守场景的最后防线（刹车全失灵时 budget 硬顶中断，
        防止研究型工单无限烧 token）。PLAN 红线：异步入口不得绕过
        权限与预算——「无预算」本身就是一种绕过。0 = 显式不限。"""
        if ident.default_token_budget is not None:
            return ident.default_token_budget
        try:
            fallback = int(getattr(settings, "agent_workforce_fallback_budget", 50000))
        except Exception:
            fallback = 50000
        return fallback if fallback > 0 else None

    async def _build_memory_block(
        self, ident: Any, instruction: str, memory_entries: list[Any]
    ) -> tuple[str, str]:
        """身份记忆注入块构造（Alpha Review #4）。返回 (标题, 正文)。

        条目 ≤ 阈值：全量硬注入（人格/职责需常驻）。
        条目 > 阈值：按工单相关性检索 top-k；检索不可用回落全量截断。
        """
        full_max = int(getattr(settings, "agent_identity_memory_full_inject_max", 8) or 8)
        header = "## 你的身份记忆（长期人格/职责/方法论）"
        if len(memory_entries) <= full_max:
            text = "\n".join(
                f"- [{m.kind}] {m.content}" for m in memory_entries
            ) or "（暂无身份记忆）"
            return header, text
        retrieved = await self._retrieve_identity_memory(
            ident, instruction, top_k=full_max
        )
        if retrieved:
            n = retrieved.count("\n") + 1
            return (
                f"## 你的相关身份记忆（共 {len(memory_entries)} 条，"
                f"按本工单相关性召回 {n} 条）"
            ), retrieved
        # 检索不可用：回落全量截断（保头——人格/职责通常在前）
        text = "\n".join(
            f"- [{m.kind}] {m.content}" for m in memory_entries
        )[:4000]
        return header, text

    async def _retrieve_identity_memory(
        self, ident: Any, query: str, *, top_k: int = 8
    ) -> str | None:
        """检索式身份记忆召回（Alpha Review #4）：向量模式可用时按
        工单相关性 top-k；不可用/无结果返回 None（调用方回落全量截断）。"""
        try:
            from backend.services.rag.capability import use_vector_rag

            if not use_vector_rag():
                return None
            from backend.services.rag.factory import RAGServiceFactory

            rag = RAGServiceFactory.get_service()
            docs = await rag.search_identity_memory(
                query, str(ident.id), top_k=top_k
            )
            if not docs:
                return None
            return "\n".join(
                f"- [{(d.payload or {}).get('kind', 'memory')}] {d.text}" for d in docs
            )
        except Exception as e:
            logger.debug("身份记忆检索跳过: %s", e)
            return None

    async def tick(self, *, wait: bool = False) -> int:
        """扫描一轮，派发所有可派工单。返回派发数。
        wait=True 时等待本轮派发的工单全部完成（测试/同步场景）。"""
        dispatched = 0
        tasks: list[asyncio.Task] = []
        while True:
            item = await self._inbox.claim_next(busy_identity_ids=set(self._busy))
            if item is None:
                break
            self._busy.add(str(item.identity_id))
            tasks.append(asyncio.create_task(self._run_item_guarded(item)))
            dispatched += 1
        if wait and tasks:
            await asyncio.gather(*tasks)
        return dispatched

    async def _run_item_guarded(self, item: Any) -> None:
        try:
            await asyncio.wait_for(self._run_item(item), timeout=self._item_timeout)
        except asyncio.TimeoutError:
            logger.warning("工单 %s 超时（%.0fs）", item.id, self._item_timeout)
            await self._inbox.fail(item.id, f"执行超时（{self._item_timeout:.0f}s）")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("工单 %s 执行异常: %s", item.id, e)
            await self._inbox.fail(item.id, str(e))
        finally:
            self._busy.discard(str(item.identity_id))

    async def _run_item(self, item: Any) -> None:
        """唤醒身份执行一单。全程 kernel 中介 + 预算扣减。

        进程归属：executor 路径由 dispatcher 建进程（审计锚点）；
        生产 loop 路径由 loop._run_inner 建进程（带编制选项），
        dispatcher 回读进程 id 关联工单。
        """
        ident = await self._registry.get(item.identity_id)
        if ident is None or ident.status != "active":
            await self._inbox.fail(item.id, "身份不存在或已停用")
            return

        if self._executor is not None:
            kernel_proc = await self._kernel.create_process(
                f"wf:{ident.name}",
                session_id=None,
                capabilities=list(ident.capabilities) if ident.capabilities is not None else None,
                token_budget=self._effective_budget(ident),
                meta={"inbox_item_id": str(item.id), "identity_id": str(ident.id),
                      "source": item.source},
            )
            await self._kernel.mark_running(kernel_proc.id)
            try:
                result = await self._executor(ident, item, kernel_proc.id, self._kernel)
            except Exception as e:
                await self._kernel.end_process(kernel_proc.id, state="failed", reason=str(e)[:200])
                raise
            await self._kernel.end_process(kernel_proc.id, state="completed")
            await self._inbox.complete(item.id, result, process_id=kernel_proc.id)
            return

        result, proc_id = await self._execute_with_loop(ident, item)
        await self._inbox.complete(item.id, result, process_id=proc_id)

    async def _execute_with_loop(self, ident: Any, item: Any) -> tuple[str, str | None]:
        """构造 prompt（身份记忆注入）+ 跑一轮 loop（复用身份的专属 session 续作）。
        返回 (结果文本, kernel 进程 id)——进程由 loop 创建（带编制选项）。"""
        # Identity Memory（Alpha Review #4）：条目少 → 全量硬注入（人格/职责
        # 需要常驻）；条目超阈值 → 按工单相关性检索 top-k 注入（防 prompt 膨胀），
        # 检索不可用回落全量截断
        memory_entries = await self._registry.current_memory(ident.id)
        memory_header, memory_text = await self._build_memory_block(
            ident, item.instruction, memory_entries
        )

        prompt = (
            f"【工作任务】你是 «{ident.name}»"
            + (f"——{ident.role}" if ident.role else "")
            + f"\n\n{memory_header}\n{memory_text}\n"
            + f"\n## 本次工单（来源：{item.source}）\n{item.instruction.strip()}\n"
        )
        if item.payload:
            import json

            prompt += f"\n附加上下文：{json.dumps(item.payload, ensure_ascii=False)[:2000]}\n"
        prompt += "\n要求：使用可用工具直接完成任务；最终回复给出精炼、可验证的结果。"

        session_id = await self._workforce_session_id(ident)

        # Worker 池复用（Alpha Review #2）：同身份跨工单共享 loop 实例，
        # run 级状态显式归零后再上岗（防跨工单泄漏红线）
        loop = await self._worker_for(ident)
        loop._reset_run_state()
        loop._agent_key = f"wf:{ident.id}"
        loop._agent_label = ident.name
        # 编制内权限/预算注入进程创建（loop._run_inner 读取）
        loop._kernel_process_options = {
            "capabilities": list(ident.capabilities) if ident.capabilities is not None else None,
            "token_budget": self._effective_budget(ident),
        }
        result = await loop.run(session_id, prompt, attachments=None, mode="workforce")
        proc = getattr(loop, "_kernel_process", None)
        return (result or "(empty response)", proc.id if proc is not None else None)

    async def _workforce_session_id(self, ident: Any) -> uuid.UUID:
        """身份专属 session（续作载体）：首次创建，之后复用——
        同一身份的历史对话都在一个 session 里（Episodic 上下文）。"""
        from backend.models.agent_identity import AgentIdentity
        from sqlalchemy import select

        existing = (ident.meta or {}).get("workforce_session_id")
        if existing:
            try:
                return uuid.UUID(str(existing))
            except ValueError:
                pass
        from backend.api.dependencies import get_session_repo

        repo = await get_session_repo()
        session = await repo.create(
            user_id=ident.user_id,
            title=f"workforce:{ident.name}",
        )
        sid = session.id if hasattr(session, "id") else uuid.UUID(str(session))
        async with self._session_factory() as s:
            row = (
                await s.execute(select(AgentIdentity).where(AgentIdentity.id == ident.id))
            ).scalar_one_or_none()
            if row is not None:
                row.meta = {**(row.meta or {}), "workforce_session_id": str(sid)}
                await s.commit()
        return sid
