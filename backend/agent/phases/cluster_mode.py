"""cluster 模式 phase（loop 拆分 Phase 1）

从 loop.py _run_locked 抽出的集群编排块（行为冻结见 tests/test_loop_freeze.py，
cluster 路径另由 tests/test_subagent_cluster.py / test_cluster_idle_push.py 锁定）：
- 显式 cluster 模式 / sub_agent_ids → 载入子代理 roster
- agent_auto_cluster 开启时按复杂度自动建团
- ≥2 个子代理 → 真·并行草稿扇出（命中直接返回汇总结果）
- 否则降级为单 LLM 协调者（roster 注入 messages）
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)


async def prepare_cluster_mode(
    loop: Any,
    *,
    session_id: uuid.UUID,
    user_input: str,
    mode: str,
    sub_agent_ids: list[str],
    messages: list[dict[str, Any]],
) -> str | None:
    """集群模式处理；命中并行执行返回最终结果，否则 None"""
    from backend.core.config import settings

    # 集群模式：注入所选子代理人物设定（协调者视角）
    cluster_mode = mode == "cluster" or bool(sub_agent_ids)
    sub_agents_info: list[dict] = []  # 存储子代理信息用于并行执行

    # 自动集群：默认关闭（agent_auto_cluster=false）；仅显式 cluster 模式或配置打开
    auto_cluster = False
    auto_cluster_enabled = bool(getattr(settings, "agent_auto_cluster", False))
    if (
        auto_cluster_enabled
        and not cluster_mode
        and mode == "default"
    ):
        complexity_score = await loop._analyze_task_complexity(user_input)
        if complexity_score >= 0.8:
            auto_cluster = True
            cluster_mode = True
            logger.info(
                "Auto-cluster mode ACTIVATED: complexity=%.2f, task='%s'",
                complexity_score, user_input[:50]
            )
            sub_agents_info = await loop._auto_create_sub_agents(user_input, complexity_score)
            if not sub_agents_info:
                auto_cluster = False
                cluster_mode = False
                logger.info("Auto-cluster: no sub-agents created, fallback to single agent")
    elif not cluster_mode and mode == "default":
        logger.debug("Auto-cluster skipped (agent_auto_cluster=false)")

    if cluster_mode and (sub_agent_ids or auto_cluster):
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

                # 存储子代理信息
                sub_agents_info.append({
                    "id": str(aid),
                    "name": agent_row.name,
                    "icon": agent_row.icon or "🤖",
                    "description": agent_row.description or "",
                    "model_ref": agent_row.model_ref,
                    "system_prompt": prompt,
                })

                roster_lines.append(
                    f"### {agent_row.icon or '🤖'} {agent_row.name}\n"
                    f"- 任务名称: {agent_row.name}\n"
                    f"- 职责: {agent_row.description or '（无）'}\n"
                    f"- 模型: {agent_row.model_ref}\n"
                    f"- 系统提示词:\n{prompt or '（未配置）'}"
                )

            # v0.2: 真·并行集群执行
            if len(sub_agents_info) >= 2:
                logger.info(
                    "Cluster mode: executing %s sub-agents in PARALLEL",
                    len(sub_agents_info),
                )

                # 使用集群执行器并行执行
                cluster_result = await loop._execute_cluster_parallel(
                    user_input=user_input,
                    sub_agents=sub_agents_info,
                    session_id=session_id,
                )

                if cluster_result:
                    return cluster_result

            # 兼容模式：单 LLM 协调者
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

    return None
