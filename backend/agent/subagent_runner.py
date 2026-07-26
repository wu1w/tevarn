"""真 Sub-Agent：迷你 Run（Phase 1）

与 cluster 模式「多角色草稿」（纯 LLM 并发）不同，这里是**带工具的完整迷你 Run**：
- 独立 NexusAgentLoop 实例（复用父 run 的 repos / ws / user）
- persona 生效：system_prompt 进任务提示；model_ref 经 _llm_snapshot_override 覆盖 LLM 快照
- 自己的 agent_key（sub:<id>）→ Agent Computer 沙箱 HOME 与其他 agent 互不干扰
- 自己的 AgentRun 记录（mode=subagent，meta 带 parent_run_id 溯源）
- _nested=True 旁路 session 锁（父 run 持锁等待，避免自死锁）
- 嵌套深度 / 迭代轮次 / 总超时三重防失控
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)


def snapshot_for_model_ref(model_ref: str) -> dict[str, str] | None:
    """model_ref（provider_id/model）→ LLM 快照（get_service_for_snapshot 可解析）"""
    ref = (model_ref or "").strip()
    if not ref or "/" not in ref:
        return None
    provider_id, model = ref.split("/", 1)
    provider_id, model = provider_id.strip(), model.strip()
    if not provider_id or not model:
        return None
    return {"provider": provider_id, "provider_id": provider_id, "model": model}


def _subagent_timeout() -> float:
    try:
        from backend.core.config import settings

        return float(getattr(settings, "agent_subagent_timeout_seconds", 300) or 300)
    except Exception:
        return 300.0


def _max_depth() -> int:
    try:
        from backend.core.config import settings

        return int(getattr(settings, "agent_subagent_max_depth", 1) or 1)
    except Exception:
        return 1


async def run_subagent(
    *,
    session_id: uuid.UUID | str,
    sub_agent: Any,
    goal: str,
    context: str = "",
    user_id: uuid.UUID | None = None,
    ws_manager: Any = None,
    parent_run_id: uuid.UUID | None = None,
    depth: int = 0,
    parent_kernel_process_id: str | None = None,
) -> str:
    """以子代理 persona 跑一个带工具的迷你 Run，返回结果文本。

    sub_agent: SubAgent 模型或具有 name/description/system_prompt/model_ref/
               max_iterations 属性的对象。
    """
    sid = uuid.UUID(str(session_id)) if not isinstance(session_id, uuid.UUID) else session_id
    name = str(getattr(sub_agent, "name", "agent") or "agent")
    agent_id = str(getattr(sub_agent, "id", name) or name)

    if depth >= _max_depth():
        return f"[Error] 子代理嵌套已达上限（max_depth={_max_depth()}），拒绝继续委派"

    # 组装任务提示（loop 自身体系提示词照常生效，persona 以任务形式注入）
    persona = str(getattr(sub_agent, "system_prompt", "") or "").strip()
    desc = str(getattr(sub_agent, "description", "") or "").strip()
    prompt = f"【子代理任务】你是 «{name}»"
    if desc:
        prompt += f"——{desc}"
    prompt += "\n"
    if persona:
        prompt += f"角色设定：{persona}\n"
    prompt += f"\n任务目标：{goal.strip()}\n"
    if context.strip():
        prompt += f"\n上下文：\n{context.strip()}\n"
    prompt += "\n要求：使用可用工具直接完成任务；最终回复给出精炼、可验证的结果。"

    # 构造子 loop（repos 走依赖注入单例，与父 run 同一套存储）
    from backend.agent import NexusAgentLoop
    from backend.api.dependencies import (
        get_context_flow_repo,
        get_ctx_item_repo,
        get_message_repo,
        get_notification_repo,
        get_session_repo,
        get_task_repo,
    )

    child = NexusAgentLoop(
        session_repo=await get_session_repo(),
        message_repo=await get_message_repo(),
        task_repo=await get_task_repo(),
        ctx_item_repo=await get_ctx_item_repo(),
        context_flow_repo=await get_context_flow_repo(),
        ws_manager=ws_manager,
        user_id=user_id,
        notification_repo=await get_notification_repo(),
    )
    # Agent Computer：子代理自己的沙箱身份
    child._agent_key = f"sub:{agent_id}"
    child._agent_label = name
    # LLM 快照覆盖：persona 的 model_ref 生效
    override = snapshot_for_model_ref(str(getattr(sub_agent, "model_ref", "") or ""))
    if override:
        child._llm_snapshot_override = override
    child._subagent_depth = depth + 1
    child._parent_run_id = parent_run_id
    # Kernel（审计修复）：父 kernel 进程 id——子进程 create_process(parent_id=...)
    # 时按父能力集 narrow。注意 parent_run_id 是 run 记录 id，不是 kernel 进程 id，
    # 两者不可混用（此前混用导致 parent 查找落空、子进程实为无父顶级进程）。
    if parent_kernel_process_id:
        child._parent_kernel_process_id = parent_kernel_process_id
    try:
        child.max_iterations = max(
            1, int(getattr(sub_agent, "max_iterations", 0) or 12)
        )
    except Exception:
        child.max_iterations = 12

    logger.info(
        "subagent mini-run start: agent=%s key=%s depth=%s model_ref=%s",
        name, child._agent_key, depth, getattr(sub_agent, "model_ref", ""),
    )
    try:
        result = await asyncio.wait_for(
            child.run(sid, prompt, attachments=None, mode="subagent", _nested=True),
            timeout=_subagent_timeout(),
        )
    except asyncio.TimeoutError:
        logger.warning("subagent %s timed out after %ss", name, _subagent_timeout())
        return f"[Timeout] 子代理 «{name}» 执行超过 {_subagent_timeout():.0f}s，已终止"
    except Exception as e:
        logger.error("subagent %s run failed: %s", name, e)
        return f"[Error] 子代理 «{name}» 执行失败: {e}"

    return f"[delegate_task -> {name}]\n{result or '(empty response)'}"
