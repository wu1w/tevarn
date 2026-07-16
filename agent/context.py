"""
上下文管理器
负责从 CtxItemRepository 读取 5 层上下文、Token 计算和滑动窗口截断
保留旧版四维度配置作为 fallback
"""

import logging
import uuid
from typing import Any

from backend.repositories import CtxItemRepository

logger = logging.getLogger(__name__)

# 5 层上下文按优先级排序（越靠前越基础）
SCOPE_PRIORITY = ["system", "user", "project", "session", "knowledge"]


class ContextManager:
    """
    上下文管理器

    管理 LLM 消息列表的组装、长度控制。
    支持两种模式：
    1. CtxItem 模式：从 CtxItemRepository 读取 5 层上下文（system/user/project/session/knowledge）
    2. Fallback 模式：使用旧版四维度配置（identity/sys_prompt/agent_md）
    """

    def __init__(
        self,
        ctx_item_repo: CtxItemRepository | None = None,
        max_messages: int = 50,
        max_tokens: int | None = None,
    ):
        """
        Args:
            ctx_item_repo: CtxItem 仓库（可选，为 None 时回退到 fallback 模式）
            max_messages: 最大保留消息数量（含 system prompt）
            max_tokens: 最大 Token 数量（预留，当前未启用精确计算）
        """
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
    ) -> tuple[list[dict[str, Any]], list[tuple[str, str]], int]:
        """
        组装完整的 messages 列表

        优先从 CtxItemRepository 读取 5 层上下文，失败或无数据时回退到 fallback_config。

        Args:
            session_id: 会话 ID
            user_input: 当前用户输入
            history: 历史消息列表（来自 Message 表）
            fallback_config: 旧版四维度配置，用于回退
            mode: 运行模式 (default/deepthink/search)

        Returns:
            messages: 完整的 LLM messages 列表
            accessed_items: 访问的 (scope, key) 列表，用于 ContextFlow 记录
            total_tokens: 估算的总 token 数
        """
        accessed_items: list[tuple[str, str]] = []

        # 尝试从 CtxItemRepository 读取上下文
        if self.ctx_item_repo is not None:
            try:
                return await self._build_from_ctx_items(
                    session_id, user_input, history, mode
                )
            except Exception as e:
                logger.warning(
                    f"Failed to build messages from CtxItem (fallback to legacy config): {e}"
                )

        # Fallback：使用旧版四维度配置
        messages = self._build_from_fallback(
            fallback_config or {}, history, user_input, mode
        )
        total_tokens = self.estimate_tokens(messages)
        return messages, accessed_items, total_tokens

    async def _build_from_ctx_items(
        self,
        session_id: uuid.UUID,
        user_input: str,
        history: list[dict[str, Any]],
        mode: str = "default",
    ) -> tuple[list[dict[str, Any]], list[tuple[str, str]], int]:
        """从 CtxItemRepository 读取 5 层上下文组装 messages"""
        accessed_items: list[tuple[str, str]] = []
        system_parts: list[str] = []

        # 1. 读取 system / user / project / knowledge 层（全局 + 会话特定）
        for scope in ["system", "user", "project", "knowledge"]:
            items = await self.ctx_item_repo.list_by_session(
                session_id=None,  # 全局项
                scope=scope,
                limit=100,
            )
            # 同时读取会话特定的项
            session_items = await self.ctx_item_repo.list_by_session(
                session_id=session_id,
                scope=scope,
                limit=100,
            )
            all_items = items + session_items

            if all_items:
                section_lines: list[str] = []
                for item in all_items:
                    section_lines.append(f"[{item.key}]\n{item.value}")
                    accessed_items.append((scope, item.key))

                scope_label = scope.upper()
                system_parts.append(
                    f"## {scope_label} CONTEXT\n" + "\n\n".join(section_lines)
                )

        # 2. 读取 session 层的 memory/doc（作为额外的上下文补充，不是对话历史）
        session_ctx_items = await self.ctx_item_repo.list_by_session(
            session_id=session_id,
            scope="session",
            limit=100,
        )
        if session_ctx_items:
            session_lines = []
            for item in session_ctx_items:
                if item.kind in ("memory", "doc"):
                    session_lines.append(f"[{item.key}]\n{item.value}")
                    accessed_items.append(("session", item.key))
            if session_lines:
                system_parts.append(
                    "## SESSION CONTEXT\n" + "\n\n".join(session_lines)
                )

        # 3. 组装 system message
        system_content = "\n\n".join(system_parts)
        if not system_content.strip():
            # 没有任何 CtxItem，降级到默认 system prompt
            system_content = "You are a helpful assistant."

        # 根据模式追加系统提示
        system_content = self._apply_mode_prompt(system_content, mode)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_content}
        ]

        # 4. 历史消息截断
        truncated = self._truncate_history(history)
        messages.extend(truncated)

        # 5. 当前用户输入
        messages.append({"role": "user", "content": user_input})

        total_tokens = self.estimate_tokens(messages)
        return messages, accessed_items, total_tokens

    def _build_from_fallback(
        self,
        config: dict[str, Any],
        history: list[dict[str, Any]],
        user_input: str,
        mode: str = "default",
    ) -> list[dict[str, Any]]:
        """使用旧版四维度配置组装 messages"""
        identity = config.get("identity", "You are a helpful assistant.")
        sys_prompt = config.get("sys_prompt", "")
        agent_md = config.get("agent_md", "")

        system_content = self._build_system_content(
            identity, sys_prompt, agent_md
        )
        system_content = self._apply_mode_prompt(system_content, mode)

        messages = [{"role": "system", "content": system_content}]

        truncated = self._truncate_history(history)
        messages.extend(truncated)

        messages.append({"role": "user", "content": user_input})

        return messages

    def _build_system_content(
        self, identity: str, sys_prompt: str, agent_md: str
    ) -> str:
        """组装旧版 system prompt"""
        parts = [identity]
        if sys_prompt:
            parts.append(f"\n## 行为准则\n{sys_prompt}")
        if agent_md:
            parts.append(f"\n## 个人上下文\n{agent_md}")
        return "\n".join(parts)

    def _apply_mode_prompt(self, system_content: str, mode: str) -> str:
        """根据运行模式追加系统提示"""
        mode_prompts = {
            "deepthink": (
                "\n\n[思考模式：深度思考]\n"
                "请对用户的每个问题进行逐步深入分析。\n"
                "把推理过程写在 <thinking>...</thinking> 标签内（用户界面可折叠），"
                "最终结论写在标签外，结构清晰。\n"
                "1. 拆解问题维度 2. 分析可能性与约束 3. 推理验证 4. 给出结论\n"
            ),
            "search": (
                "\n\n[思考模式：联网搜索]\n"
                "当用户询问需要最新信息、实时数据或你不确定的内容时，"
                "请主动使用 web_search 工具搜索最新信息，然后基于搜索结果回答。"
                "请明确标注信息来源。"
            ),
            "ppt": (
                "\n\n[思考模式：制作PPT]\n"
                "用户想要制作一份PPT演示文稿。请：\n"
                "1. 分析用户需求，确定PPT的主题、目标受众和核心要点\n"
                "2. 使用 generate_ppt skill 生成专业的PPT文件\n"
                "3. 向用户确认PPT的结构和内容方向\n"
                "4. 生成完成后提供下载链接"
            ),
            "report": (
                "\n\n[思考模式：生成报告]\n"
                "用户想要生成一份专业报告。请：\n"
                "1. 分析用户需求，确定报告类型和核心内容\n"
                "2. 使用 generate_report skill 生成结构化报告\n"
                "3. 报告应包含执行摘要、背景、分析、结论和建议\n"
                "4. 生成完成后提供下载链接"
            ),
            "goal": (
                "\n\n[模式：Goal / 自主任务]\n"
                "你正在执行一个可能需要多步工具调用的复杂目标。必须：\n"
                "1. 先用 manage_goal(action=create|set_todos) 拆解为可执行 todo 列表\n"
                "2. 每次只推进 1～3 个 todo，完成或受阻时用 update_todo 更新状态\n"
                "3. 在给出最终答复前，确认所有 todo 已 done，并 manage_goal(action=complete)\n"
                "4. 未完成前不要结束；若缺信息可 block 并说明需要什么\n"
                "5. 思考过程放在 <thinking>...</thinking> 中（可折叠展示），最终答复放在标签外\n"
                "6. 需要图表时用 ```mermaid 代码块；代码用带语言标记的 fenced code block\n"
            ),
        }
        if mode in mode_prompts:
            system_content += mode_prompts[mode]
        if mode in ("default", "search", "ppt", "report"):
            system_content += (
                "\n\n当任务较复杂时，可将内部推理放在 <thinking>...</thinking> 中，"
                "最终对用户可见的答案放在标签外。需要流程图/架构图时优先使用 mermaid 代码块。"
            )
        return system_content

    def _truncate_history(
        self, history: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """
        截断历史消息，保留最新的消息

        预留 Token 计算接口，当前按消息数量截断
        """
        # 为 system + user_input 预留 2 个位置
        available_slots = self.max_messages - 2
        if len(history) <= available_slots:
            return history

        # 保留最新的消息
        truncated = history[-available_slots:]
        logger.info(
            f"History truncated: {len(history)} -> {len(truncated)} messages"
        )
        return truncated

    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        """
        估算 Token 数量
        预留接口，后续接入 tiktoken 或对应模型的 tokenizer
        """
        # 简化估算：1 token ≈ 3.4 字符（与前端 demo 保持一致）
        total_chars = sum(
            len(m.get("content", "")) for m in messages
        )
        return max(8, round(total_chars / 3.4))
