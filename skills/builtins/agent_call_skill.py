"""
Agent Call Skill - 调用其他 Agent
当前为桩实现，用于多 Agent 协作场景

⚠️ 重要：本 Skill 预留了防止无限递归调用的安全骨架
（禁止调用自身 / 禁止调用环 / 限制最大调用深度）。
未来实现真实的子 Agent 调度逻辑时，必须保留并正确传递
`_caller_agent` / `_call_chain` 参数，否则会导致 Agent 相互调用
造成无限递归、资源耗尽（栈溢出/内存耗尽/无限 LLM 调用费用）。
"""

from ..base import BaseSkill

MAX_CALL_DEPTH = 3


class AgentCallSkill(BaseSkill):
    """Agent 调用 Skill"""

    name = "agent_call"
    description = (
        "当当前 Agent 无法处理某类任务时，"
        "调用此工具将任务转交给其他专业 Agent（如 Coder、Researcher、Writer 等）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "description": "目标 Agent 名称，如 Coder / Researcher / Writer / PM",
            },
            "task": {
                "type": "string",
                "description": "要分配给目标 Agent 的任务描述",
            },
            "context": {
                "type": "string",
                "description": "相关上下文信息",
                "default": "",
            },
        },
        "required": ["agent", "task"],
    }

    async def execute(
        self,
        agent: str,
        task: str,
        context: str = "",
        _caller_agent: str | None = None,
        _call_chain: list[str] | None = None,
    ) -> str:
        """调用其他 Agent（桩实现，含递归防护骨架）"""
        call_chain = list(_call_chain or [])

        # 1. 禁止调用自身
        if _caller_agent and agent == _caller_agent:
            return f"[Error] Agent '{agent}' 不能调用自身，已阻止潜在的无限递归。"

        # 2. 禁止调用链中出现环
        if agent in call_chain:
            return f"[Error] 检测到调用环：{' -> '.join(call_chain + [agent])}，已阻止。"

        # 3. 限制最大调用深度
        if len(call_chain) >= MAX_CALL_DEPTH:
            return f"[Error] 已达到最大 Agent 调用深度 {MAX_CALL_DEPTH}，已阻止继续递归。"

        return (
            f"[Agent Call Stub]\nTarget: {agent}\nTask: {task}\n"
            f"Context: {context[:200] if context else 'None'}\n"
            f"⚠️ 这是桩实现。多 Agent 协作需要额外的消息队列或内部 API 机制。"
        )
