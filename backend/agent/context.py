"""
上下文管理器

负责从 CtxItemRepository 读取 5 层上下文、Token 计算和滑动窗口截断。
系统提示词组装委托给 system_prompt.py 的三层架构（Stable/Context/Volatile）。
"""

import logging
import uuid
from typing import Any

from backend.repositories import CtxItemRepository
from backend.agent.system_prompt import (
    build_system_prompt,
    merge_prompt_parts,
    DEFAULT_IDENTITY,
)

logger = logging.getLogger(__name__)

# 5 层上下文按优先级排序（越靠前越基础）
SCOPE_PRIORITY = ["system", "user", "project", "session", "knowledge"]


class ContextManager:
    """
    上下文管理器

    管理 LLM 消息列表的组装、长度控制。
    系统提示词使用三层架构：
    - Stable：身份 + 核心行为准则（不可配置）
    - Context：用户自定义人格 + 上下文文件 + 平台/模式提示（可配置）
    - Volatile：记忆 + 时间戳（每轮重建）
    """

    def __init__(
        self,
        ctx_item_repo: CtxItemRepository | None = None,
        max_messages: int = 50,
        max_tokens: int | None = None,
    ):
        self.ctx_item_repo = ctx_item_repo
        self.max_messages = max_messages
        self.max_tokens = max_tokens

    async def build_messages(
        self,
        session_id: uuid.UUID,
        user_input: str,
        history: list[dict[str, Any]],
        fallback_config: dict[str, Any] | None = None,
        mode: str = "default",
        platform: str | None = None,
        tools_enabled: list[str] | None = None,
        model: str | None = None,
    ) -> tuple[list[dict[str, Any]], list[tuple[str, str]], int]:
        """
        组装完整的 messages 列表。

        Args:
            session_id: 会话 ID
            user_input: 当前用户输入
            history: 历史消息列表
            fallback_config: 旧版配置（identity/sys_prompt/agent_md）
            mode: 运行模式
            platform: 消息平台（qqbot/telegram/discord 等）
            tools_enabled: 已启用的工具列表
            model: 当前模型名

        Returns:
            (messages, accessed_items, total_tokens)
        """
        accessed_items: list[tuple[str, str]] = []

        # ── 收集三层参数 ──
        identity = None
        user_system_prompt = None
        context_files = None
        memory_block = None

        # 尝试从 CtxItemRepository 读取上下文
        if self.ctx_item_repo is not None:
            try:
                ctx_data = await self._collect_ctx_items(session_id)
                identity = ctx_data.get("identity")
                user_system_prompt = ctx_data.get("user_system_prompt")
                context_files = ctx_data.get("context_files")
                memory_block = ctx_data.get("memory_block")
                accessed_items = ctx_data.get("accessed_items", [])
            except Exception as e:
                logger.warning(f"CtxItem read failed, using fallback: {e}")

        # Fallback：从旧版配置提取
        if not identity and fallback_config:
            identity = fallback_config.get("identity") or None
        if not user_system_prompt and fallback_config:
            sys_prompt = fallback_config.get("sys_prompt", "")
            agent_md = fallback_config.get("agent_md", "")
            parts = []
            if sys_prompt:
                parts.append(sys_prompt)
            if agent_md:
                parts.append(agent_md)
            if parts:
                user_system_prompt = "\n\n".join(parts)

        # 文件驱动记忆
        if not memory_block:
            try:
                from backend.agent.file_context import load_workspace_memory_bundle
                mem_block, _meta = load_workspace_memory_bundle()
                if mem_block:
                    memory_block = mem_block
            except Exception:
                pass

        # ── 组装系统提示词 ──
        package_snippets: list[dict[str, str]] = []
        try:
            from backend.packages.session_packages import get_session_attached_packages
            from backend.packages.loader import resolve_attached_snippets

            attached = await get_session_attached_packages(session_id)
            package_snippets = await resolve_attached_snippets(attached)
        except Exception as e:
            logger.debug("package snippets skipped: %s", e)

        parts = build_system_prompt(
            identity=identity,
            tools_enabled=tools_enabled,
            model=model,
            user_system_prompt=user_system_prompt,
            context_files=context_files,
            platform=platform,
            mode=mode if mode != "default" else None,
            memory_block=memory_block,
            session_id=str(session_id),
        )
        # 挂载包片段并入 Context 层（不污染 Stable）
        if package_snippets:
            pkg_block_parts = []
            for sn in package_snippets:
                name = sn.get("name") or "package"
                icon = sn.get("icon") or "📦"
                body = (sn.get("content") or "").strip()
                if body:
                    pkg_block_parts.append(f"### {icon} {name}\n{body}")
            if pkg_block_parts:
                pkg_block = "# Attached Takton Packages\n" + "\n\n".join(pkg_block_parts)
                ctx = parts.get("context") or ""
                parts["context"] = (ctx + "\n\n" + pkg_block).strip() if ctx else pkg_block

        system_content = merge_prompt_parts(parts)

        # 分层报告挂到实例，供 API 读取最近一次组装
        try:
            from backend.agent.system_layers import build_system_layers_report

            self.last_system_layers = build_system_layers_report(
                parts=parts,
                identity=identity,
                user_system_prompt=user_system_prompt,
                context_files=context_files,
                package_snippets=package_snippets,
                platform=platform,
                mode=mode if mode != "default" else None,
                memory_block=memory_block,
                model=model,
                session_id=str(session_id),
            )
        except Exception as e:
            logger.debug("system layers report skipped: %s", e)
            self.last_system_layers = None

        # ── 组装 messages ──
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_content}
        ]

        # 历史消息截断
        truncated = self._truncate_history(history)
        messages.extend(truncated)

        # 当前用户输入
        messages.append({"role": "user", "content": user_input})

        total_tokens = self.estimate_tokens(messages)
        return messages, accessed_items, total_tokens

    async def _collect_ctx_items(
        self, session_id: uuid.UUID
    ) -> dict[str, Any]:
        """从 CtxItemRepository 收集上下文数据。"""
        result: dict[str, Any] = {
            "identity": None,
            "user_system_prompt": None,
            "context_files": None,
            "memory_block": None,
            "accessed_items": [],
        }
        accessed_items: list[tuple[str, str]] = []

        # 读取 system / user / project / knowledge 层
        context_parts: list[str] = []
        for scope in ["system", "user", "project", "knowledge"]:
            items = await self.ctx_item_repo.list_by_session(
                session_id=None, scope=scope, limit=100,
            )
            session_items = await self.ctx_item_repo.list_by_session(
                session_id=session_id, scope=scope, limit=100,
            )
            all_items = items + session_items

            if all_items:
                section_lines: list[str] = []
                for item in all_items:
                    # identity 特殊处理：取第一个 system scope 的 identity 项
                    if scope == "system" and item.key == "identity" and not result["identity"]:
                        result["identity"] = item.value
                    else:
                        section_lines.append(f"[{item.key}]\n{item.value}")
                    accessed_items.append((scope, item.key))

                if section_lines:
                    context_parts.append(
                        f"## {scope.upper()} CONTEXT\n" + "\n\n".join(section_lines)
                    )

        # session 层的 memory/doc
        session_ctx_items = await self.ctx_item_repo.list_by_session(
            session_id=session_id, scope="session", limit=100,
        )
        if session_ctx_items:
            session_lines = []
            for item in session_ctx_items:
                if item.kind in ("memory", "doc"):
                    session_lines.append(f"[{item.key}]\n{item.value}")
                    accessed_items.append(("session", item.key))
            if session_lines:
                context_parts.append(
                    "## SESSION CONTEXT\n" + "\n\n".join(session_lines)
                )

        if context_parts:
            result["context_files"] = "\n\n".join(context_parts)
        result["accessed_items"] = accessed_items
        return result

    def _truncate_history(
        self, history: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """截断历史消息，保留最新的消息。"""
        from backend.core.config import settings

        ctx_win = int(getattr(settings, "context_window", 128_000) or 128_000)
        dynamic_max = max(self.max_messages, (ctx_win * 60 // 100) // 300)

        available_slots = dynamic_max - 2
        if len(history) <= available_slots:
            return history

        truncated = history[-available_slots:]
        logger.info(
            f"History truncated: {len(history)} -> {len(truncated)} (max={dynamic_max})"
        )
        return truncated

    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        """估算 Token 数量。"""
        from backend.agent.token_meter import TokenMeter
        from backend.core.config import settings

        meter = TokenMeter(
            context_window=int(getattr(settings, "context_window", 128_000) or 128_000)
        )
        return meter.estimate_messages(messages)
