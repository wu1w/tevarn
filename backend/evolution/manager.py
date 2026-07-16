"""EvolutionManager — orchestrates seed tasks, evaluate, improve, auto-apply."""

from __future__ import annotations

import logging
from typing import Any

from backend.evolution import store
from backend.evolution.config import get_evolution_config, set_evolution_config
from backend.evolution.evaluator import evaluate_criteria
from backend.evolution.gates import run_gates
from backend.evolution.improver import classify_failures, propose_skill_from_failure

logger = logging.getLogger(__name__)

_SEEDED = False


SEED_TASKS: list[dict[str, Any]] = [
    {
        "name": "smoke-health",
        "domain": "ops",
        "description": "本机 API 健康检查",
        "criteria": [
            {
                "type": "http_ok",
                "url": "http://127.0.0.1:8090/api/health",
                "contains": "ok",
            }
        ],
    },
    {
        "name": "static-frontend",
        "domain": "ops",
        "description": "前端静态资源存在",
        "criteria": [
            {
                "type": "file_exists",
                "path": "backend/static/index.html",
            }
        ],
    },
    {
        "name": "evolution-module",
        "domain": "dev",
        "description": "进化模块源码在位",
        "criteria": [
            {"type": "file_exists", "path": "backend/evolution/manager.py"},
            {
                "type": "content_match",
                "path": "backend/evolution/manager.py",
                "contains": "EvolutionManager",
            },
        ],
    },
    {
        "name": "skills-registry",
        "domain": "dev",
        "description": "技能注册表文件存在",
        "criteria": [
            {"type": "file_exists", "path": "backend/skills/registry.py"},
        ],
    },
    {
        "name": "remote-device-optional",
        "domain": "remote",
        "description": "若已配对设备 remote-pc 则执行 hostname（未配对则跳过）",
        "criteria": [
            {
                "type": "remote_exec",
                "device": "remote-pc",
                "command": "hostname",
                "optional": True,
                "skip_if_missing": True,
            }
        ],
    },
    {
        "name": "llm-judge-sample",
        "domain": "quality",
        "description": "用 LLM 评审一段固定样例是否像完整中文答复（可关 llm_judge）",
        "criteria": [
            {
                "type": "llm_judge",
                "rubric": "内容应是通顺的中文，包含明确结论，不是空话或纯错误栈。",
                "subject": "今日上海多云，气温约 18–24°C，东南风，空气良好。出门建议薄外套。",
                "optional": True,
            }
        ],
    },
]


class EvolutionManager:
    def __init__(self) -> None:
        self._turn_tools: dict[str, list[dict[str, Any]]] = {}

    def ensure_seeded(self) -> None:
        global _SEEDED
        store.ensure_store()
        if _SEEDED:
            return
        for t in SEED_TASKS:
            store.upsert_task(
                name=t["name"],
                domain=t.get("domain") or "general",
                description=t.get("description") or "",
                criteria=t["criteria"],
                source="seed",
            )
            # register seed as visible asset (not deletable)
            store.create_asset(
                kind="task",
                name=t["name"],
                summary=t.get("description") or t["name"],
                source="seed",
                status="active",
                content="",
                gen=0,
                meta={"domain": t.get("domain")},
            )
        _SEEDED = True

    def status(self) -> dict[str, Any]:
        self.ensure_seeded()
        cfg = get_evolution_config()
        st = store.stats()
        st["recent_runs"] = store.recent_runs(10)
        st["tasks"] = len(store.list_tasks())
        st["config"] = {
            "enabled": cfg.enabled,
            "mode": cfg.mode,
            "auto_apply_skills": cfg.auto_apply_skills,
            "llm_judge": cfg.llm_judge,
            "max_iterations": cfg.max_iterations,
        }
        return st

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        set_evolution_config(enabled=enabled)
        return self.status()

    def set_auto_apply(self, auto_apply: bool) -> dict[str, Any]:
        set_evolution_config(auto_apply_skills=auto_apply)
        return self.status()

    def record_tool(
        self,
        session_id: str,
        *,
        name: str,
        arguments: dict[str, Any] | None,
        result: str,
        ok: bool,
    ) -> None:
        cfg = get_evolution_config()
        if not cfg.enabled:
            return
        self.ensure_seeded()
        bucket = self._turn_tools.setdefault(session_id, [])
        bucket.append(
            {
                "name": name,
                "arguments": _safe_args(arguments or {}),
                "result": (result or "")[:2000],
                "ok": ok,
            }
        )
        # usage count for known evolution skills
        if ok and name:
            try:
                store.bump_use(name, kind="skill")
            except Exception:
                pass

    async def on_turn_final(
        self,
        session_id: str,
        *,
        user_input: str,
        final_content: str,
    ) -> dict[str, Any] | None:
        cfg = get_evolution_config()
        if not cfg.enabled:
            return None
        if cfg.mode == "off" or cfg.mode == "manual":
            return None

        self.ensure_seeded()
        tools = self._turn_tools.pop(session_id, [])
        try:
            store.append_trajectory(session_id, turn=0, tools=tools)
        except Exception as e:
            logger.warning("trajectory append failed: %s", e)

        failures = classify_failures(
            tool_trace=tools, final_content=final_content or "", eval_failures=[]
        )

        # on_failure mode: only act when failures detected
        if cfg.mode == "on_failure" and not failures:
            return {"skipped": True, "reason": "no_failure"}

        # optional light task: smoke only when always
        task_result = None
        if cfg.mode == "always":
            task = store.get_task("smoke-health")
            if task:
                task_result = await evaluate_criteria(
                    task["criteria"],
                    context={"final_content": final_content, "user_input": user_input},
                )
                store.bump_task_run("smoke-health")
                store.add_run(
                    session_id=session_id,
                    task_id=task["id"],
                    score=task_result["score"],
                    status="pass" if task_result["score"] >= 0.8 else "fail",
                    failure_codes=task_result["failure_codes"],
                    detail=task_result,
                )
                if task_result["failure_codes"]:
                    failures.extend(task_result["failure_codes"])

        if not failures and cfg.mode == "on_failure":
            return {"skipped": True, "reason": "no_failure"}

        proposal = propose_skill_from_failure(
            user_input=user_input or "",
            failure_codes=failures,
            tool_trace=tools,
            final_content=final_content or "",
        )

        score = 1.0 if not failures else max(0.0, 1.0 - 0.15 * len(failures))
        gate = run_gates(
            name=proposal["name"],
            content=proposal["content"],
            summary=proposal["summary"],
            score=score,
            baseline_score=0.5,
        )

        status = "draft"
        applied = False
        if gate["ok"] and cfg.auto_apply_skills:
            status = "active"
            applied = True
        elif gate["ok"] and not cfg.auto_apply_skills:
            status = "draft"
        else:
            status = "draft"

        asset = store.create_asset(
            kind="skill",
            name=proposal["name"],
            summary=proposal["summary"],
            source="auto",
            status=status,
            content=proposal["content"],
            session_id=session_id,
            last_score=score,
            meta={
                "failure_codes": failures,
                "gates": gate["gates"],
                "auto_applied": applied,
            },
        )

        if applied:
            await self._apply_skill_to_db(proposal, asset_id=asset.get("id"))

        store.add_run(
            session_id=session_id,
            task_id=None,
            score=score,
            status="improved" if applied else "drafted",
            failure_codes=failures,
            detail={"asset_id": asset["id"], "gates": gate},
        )

        logger.info(
            "evolution turn: asset=%s status=%s applied=%s failures=%s",
            asset["id"],
            status,
            applied,
            failures,
        )
        return {
            "asset": asset,
            "applied": applied,
            "failures": failures,
            "gate": gate,
        }

    async def run_task(
        self,
        name: str,
        session_id: str | None = None,
        *,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.ensure_seeded()
        task = store.get_task(name)
        if not task:
            return {"ok": False, "error": f"task not found: {name}"}
        result = await evaluate_criteria(task["criteria"], context=context)
        store.bump_task_run(name)
        store.add_run(
            session_id=session_id,
            task_id=task["id"],
            score=result["score"],
            status="pass" if result["score"] >= 0.8 else "fail",
            failure_codes=result["failure_codes"],
            detail=result,
        )
        return {"ok": True, "task": name, **result}

    async def _apply_skill_to_db(self, proposal: dict[str, Any], asset_id: str | None = None) -> None:
        """Write playbook into ToolRegistry (+ best-effort skills table)."""
        try:
            from backend.evolution.runtime_tools import register_evolved_tool

            register_evolved_tool(
                name=proposal["name"],
                description=proposal.get("summary") or proposal["name"],
                body=proposal.get("content") or "",
                asset_id=asset_id,
                enabled=True,
            )
        except Exception as e:
            logger.warning("register evolution tool failed: %s", e)

        try:
            from backend.repositories.skill_repo import AsyncSkillRepository

            repo = AsyncSkillRepository()
            existing = await repo.get_skill_by_name(proposal["name"])
            payload = {
                "name": proposal["name"],
                "description": (proposal.get("summary") or "")[:500],
                "schema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "用户问题或上下文",
                        }
                    },
                },
                "enabled": True,
                "is_builtin": False,
                "handler": "python",
                "handler_config": {
                    "evolution": True,
                    "body": (proposal.get("content") or "")[:8000],
                    "kind": "evolved_playbook",
                },
            }
            if existing:
                await repo.update(existing.id, payload)
            else:
                await repo.create(payload)
        except Exception as e:
            logger.warning(
                "auto-apply skill to DB failed (tool still registered if possible): %s", e
            )


def _safe_args(args: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in list(args.items())[:20]:
        if k in {"token", "api_key", "password", "authorization"}:
            out[k] = "***"
        else:
            s = str(v)
            out[k] = s[:200]
    return out


_manager: EvolutionManager | None = None


def get_evolution_manager() -> EvolutionManager:
    global _manager
    if _manager is None:
        _manager = EvolutionManager()
    return _manager
