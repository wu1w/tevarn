from pathlib import Path
import ast

p = Path(__file__).resolve().parents[1] / "backend" / "agent" / "loop.py"
text = p.read_text(encoding="utf-8")

old_start = text.find('                goal_mode = mode == "goal"')
if old_start < 0:
    old_start = text.find('        goal_mode = mode == "goal"')
end = text.find("        # 6. 获取 LLM 服务", old_start)
if old_start < 0 or end < 0:
    raise SystemExit(f"markers not found: start={old_start} end={end}")

replacement = r'''        # Goal 模式：更高轮次 + 初始化 goal 状态
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

'''

new = text[:old_start] + replacement + text[end:]
ast.parse(new)
p.write_text(new, encoding="utf-8")
print("ok")
