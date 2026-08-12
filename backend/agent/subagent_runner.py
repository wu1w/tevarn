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
    workspace_root: str | None = None,
    worktree_name: str | None = None,
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

    # 构造子 loop：直接 repo（不依赖 FastAPI dependencies，保持 L2 无 Adapter）
    from backend.agent import NexusAgentLoop
    from backend.repositories.context_repo import (
        AsyncContextFlowRepository,
        AsyncCtxItemRepository,
    )
    from backend.repositories.message_repo import AsyncMessageRepository
    from backend.repositories.notification_repo import AsyncNotificationRepository
    from backend.repositories.session_repo import AsyncSessionRepository
    from backend.repositories.task_repo import AsyncTaskRepository

    child = NexusAgentLoop(
        session_repo=AsyncSessionRepository(),
        message_repo=AsyncMessageRepository(),
        task_repo=AsyncTaskRepository(),
        ctx_item_repo=AsyncCtxItemRepository(),
        context_flow_repo=AsyncContextFlowRepository(),
        ws_manager=ws_manager,
        user_id=user_id,
        notification_repo=AsyncNotificationRepository(),
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
    child._run_origin = "subagent"  # Phase 2.2 显式 origin
    # Kernel（审计修复）：父 kernel 进程 id——子进程 create_process(parent_id=...)
    # 时按父能力集 narrow。注意 parent_run_id 是 run 记录 id，不是 kernel 进程 id，
    # 两者不可混用（此前混用导致 parent 查找落空、子进程实为无父顶级进程）。
    if parent_kernel_process_id:
        child._parent_kernel_process_id = parent_kernel_process_id
    # Intent：子代理默认最小只读能力；显式 capabilities / allow_risky 可放宽
    try:
        raw_caps = getattr(sub_agent, "capabilities", None)
        cap_list = [str(c) for c in (raw_caps or [])] if raw_caps else []
        allow_risky = bool(getattr(sub_agent, "allow_risky", False))
        child._intent_declaration = {
            "goal": (goal or f"subagent:{name}").strip()[:500] or f"subagent:{name}",
            "capabilities": cap_list,
            "constraints": {
                "allow_risky": allow_risky,
                "ttl_seconds": int(_subagent_timeout()),
            },
        }
        # 预置进程能力为空列表时 create_process 会走 intent 挂载；
        # 若 sub_agent 声明了 capabilities，同步进 process options
        if cap_list:
            child._pending_kernel_options = {
                **(getattr(child, "_pending_kernel_options", None) or getattr(child, "_kernel_process_options", None) or {}),
                "capabilities": cap_list,
                "intent": child._intent_declaration,
            }
        else:
            child._pending_kernel_options = {
                **(getattr(child, "_pending_kernel_options", None) or getattr(child, "_kernel_process_options", None) or {}),
                "capabilities": [],  # 显式空 → 再由 intent 填 grantable
                "intent": child._intent_declaration,
            }
    except Exception as e:
        logger.debug("subagent intent prep: %s", e)
    try:
        child.max_iterations = max(
            1, int(getattr(sub_agent, "max_iterations", 0) or 12)
        )
    except Exception:
        child.max_iterations = 12

    # audit-fix(#3)：子代理上下文隔离——此前子 loop 复用父 session_id，
    # 导致子代理中间工具消息落进父会话历史（上下文污染/膨胀）。
    # messages.session_id 对 sessions.id 有外键约束，故新建一条轻量 session
    # 记录（独立 uuid → message store 查不到历史即全新上下文）；Session 模型
    # 无 is_subagent 列，不改 schema，标记写进 config JSON 并继承父会话配置。
    child_sid: uuid.UUID = sid
    _child_session_ok = False
    try:
        _child_repo = AsyncSessionRepository()
        _new_sid = uuid.uuid4()
        _parent_cfg: dict[str, Any] = {}
        _parent_user_id: Any = user_id
        try:
            _parent = await _child_repo.get_by_id(sid)
            if _parent is not None:
                if isinstance(getattr(_parent, "config", None), dict):
                    _parent_cfg = dict(_parent.config)
                if _parent_user_id is None:
                    _parent_user_id = getattr(_parent, "user_id", None)
        except Exception as _pe:
            logger.debug("subagent parent session read skip: %s", _pe)
        if _parent_user_id is not None:
            _child_cfg = dict(_parent_cfg)
            _child_cfg["is_subagent"] = True
            _child_cfg["parent_session_id"] = str(sid)
            if workspace_root:
                _child_cfg["workspace_root"] = str(workspace_root)
                _child_cfg["file_browser_root"] = str(workspace_root)
                _child_cfg["cwd"] = str(workspace_root)
                _child_cfg["worktree_isolated"] = True
                if worktree_name:
                    _child_cfg["worktree_name"] = str(worktree_name)
            await _child_repo.create(
                {
                    "id": _new_sid,
                    "user_id": _parent_user_id,
                    "config": _child_cfg,
                }
            )
            child_sid = _new_sid
            _child_session_ok = True
        else:
            logger.warning(
                "subagent child session skipped: no user_id (parent=%s)", sid
            )
    except Exception as _ce:
        # 创建失败回退父 session（保持旧行为，不阻断委派）
        logger.warning(
            "subagent child session create failed, fallback to parent session: %s",
            _ce,
        )
        child_sid = sid

    logger.info(
        "subagent mini-run start: agent=%s key=%s depth=%s model_ref=%s sid=%s",
        name, child._agent_key, depth, getattr(sub_agent, "model_ref", ""),
        f"{child_sid}"[:8] + ("(isolated)" if _child_session_ok else "(shared)"),
    )
    try:
        result = await asyncio.wait_for(
            child.run(child_sid, prompt, attachments=None, mode="subagent", _nested=True),
            timeout=_subagent_timeout(),
        )
    except asyncio.TimeoutError:
        logger.warning("subagent %s timed out after %ss", name, _subagent_timeout())
        return f"[Timeout] 子代理 «{name}» 执行超过 {_subagent_timeout():.0f}s，已终止"
    except Exception as e:
        logger.error("subagent %s run failed: %s", name, e)
        return f"[Error] 子代理 «{name}» 执行失败: {e}"

    return f"[delegate_task -> {name}]\n{result or '(empty response)'}"
