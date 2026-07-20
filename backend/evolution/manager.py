"""EvolutionManager v0.1.1 — HAEE-style orchestrator (P1–P4)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from backend.evolution import store
from backend.evolution.config import ENGINE_VERSION, get_evolution_config, set_evolution_config
from backend.evolution.evaluator import evaluate_criteria
from backend.evolution.gates import run_gates
from backend.evolution.improver import (
    classify_failures,
    propose_from_task_outcome,
    propose_skill_from_failure,
    propose_tool_draft,
)

logger = logging.getLogger(__name__)

_SEEDED = False

SEED_TASKS: list[dict[str, Any]] = [
    {
        "name": "smoke-health",
        "domain": "ops",
        "description": "本机 API 健康检查",
        "criteria": [
            {"type": "http_ok", "url": "http://127.0.0.1:8000/api/health", "contains": "ok"},
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
        "criteria": [{"type": "file_exists", "path": "backend/skills/registry.py"}],
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
            store.create_asset(
                kind="task",
                name=t["name"],
                summary=t.get("description") or t["name"],
                source="seed",
                status="active",
                content="",
                gen=0,
                meta={"domain": t.get("domain"), "engine": ENGINE_VERSION},
            )
        _SEEDED = True

    def status(self) -> dict[str, Any]:
        self.ensure_seeded()
        cfg = get_evolution_config()
        st = store.stats()
        st["recent_runs"] = store.recent_runs(10)
        st["tasks"] = len(store.list_tasks())
        st["engine_version"] = ENGINE_VERSION
        st["clusters"] = store.list_clusters(20)
        st["config"] = {
            "enabled": cfg.enabled,
            "mode": cfg.mode,
            "auto_apply_skills": cfg.auto_apply_skills,
            "auto_apply_tools": cfg.auto_apply_tools,
            "from_tasks": cfg.from_tasks,
            "from_cron": cfg.from_cron,
            "auto_observe": cfg.auto_observe,
            "auto_create_tools": cfg.auto_create_tools,
            "curator_enabled": cfg.curator_enabled,
            "observe_nudge_level": cfg.observe_nudge_level,
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
        if ok and name:
            try:
                store.bump_use(name, kind="skill")
                store.bump_use(name, kind="tool")
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

        # P4 observe
        observe_info = None
        try:
            from backend.evolution.observer import record_observation

            observe_info = record_observation(
                session_id,
                tools=tools,
                final_content=final_content or "",
                user_input=user_input or "",
            )
        except Exception as e:
            logger.warning("observe failed: %s", e)

        failures = classify_failures(
            tool_trace=tools, final_content=final_content or "", eval_failures=[]
        )

        # auto-observe may return a proposal to materialize
        if observe_info and observe_info.get("proposal") and cfg.observe_nudge_level != "approve":
            prop = observe_info["proposal"]
            assets = await self._materialize_proposals(
                [prop],
                session_id=session_id,
                score=0.85,
                baseline=0.5,
                force_skill=True,
            )
            return {
                "observe": observe_info,
                "assets": assets,
                "source": "auto_observe",
            }

        if cfg.mode == "on_failure" and not failures:
            return {"skipped": True, "reason": "no_failure", "observe": observe_info}

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
            return {"skipped": True, "reason": "no_failure", "observe": observe_info}

        proposals: list[dict[str, Any]] = [
            propose_skill_from_failure(
                user_input=user_input or "",
                failure_codes=failures,
                tool_trace=tools,
                final_content=final_content or "",
                source_label="turn",
            )
        ]
        if cfg.auto_create_tools:
            proposals.append(
                propose_tool_draft(
                    user_input=user_input or "",
                    failure_codes=failures,
                    tool_trace=tools,
                )
            )

        score = 1.0 if not failures else max(0.0, 1.0 - 0.15 * len(failures))
        assets = await self._materialize_proposals(
            proposals, session_id=session_id, score=score, baseline=0.5
        )

        store.add_run(
            session_id=session_id,
            task_id=None,
            score=score,
            status="improved" if any(a.get("applied") for a in assets) else "drafted",
            failure_codes=failures,
            detail={"assets": [a.get("id") for a in assets if a.get("id")]},
        )
        return {
            "assets": assets,
            "failures": failures,
            "observe": observe_info,
            "engine": ENGINE_VERSION,
        }

    async def run_from_task_outcome(
        self,
        *,
        task_name: str,
        success: bool,
        detail: str = "",
        failure_codes: list[str] | None = None,
        tool_trace: list[dict[str, Any]] | None = None,
        session_id: str | None = None,
        source: str = "task",
        criteria_summary: str = "",
    ) -> dict[str, Any] | None:
        """P1: cron / external task finished → evolution assets."""
        cfg = get_evolution_config()
        if not cfg.enabled:
            return None
        if source == "cron" and not cfg.from_cron:
            return None
        if source == "task" and not cfg.from_tasks:
            return None

        self.ensure_seeded()
        codes = list(failure_codes or ([] if success else ["task_failed"]))
        skill = propose_from_task_outcome(
            task_name=task_name,
            success=success,
            detail=detail,
            failure_codes=codes,
            tool_trace=tool_trace,
            criteria_summary=criteria_summary,
        )
        proposals = [skill]
        if cfg.auto_create_tools and not success:
            proposals.append(
                propose_tool_draft(
                    user_input=f"cron/task {task_name}",
                    failure_codes=codes,
                    tool_trace=tool_trace,
                )
            )
        score = 1.0 if success else 0.85  # 失败也过 G3 门槛，仍以 draft/active 策略控制 apply
        assets = await self._materialize_proposals(
            proposals,
            session_id=session_id or f"{source}:{task_name}",
            score=score,
            baseline=0.5,
        )
        store.add_run(
            session_id=session_id,
            task_id=None,
            score=score,
            status="pass" if success else "fail",
            failure_codes=codes,
            detail={"source": source, "task_name": task_name, "assets": [a.get("id") for a in assets]},
        )
        return {"ok": True, "assets": assets, "source": source, "task_name": task_name}

    async def run_task(
        self,
        name: str,
        session_id: str | None = None,
        *,
        context: dict[str, Any] | None = None,
        improve: bool = True,
    ) -> dict[str, Any]:
        """Evaluate a registered task; optionally propose skill on fail (full cycle)."""
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
        out: dict[str, Any] = {"ok": True, "task": name, **result}
        if improve and result["score"] < 0.8:
            evolved = await self.run_from_task_outcome(
                task_name=name,
                success=False,
                detail=str(result)[:1500],
                failure_codes=result.get("failure_codes") or ["eval_fail"],
                session_id=session_id,
                source="task",
                criteria_summary=task.get("description") or "",
            )
            out["evolution"] = evolved
        return out

    async def run_curator(self, dry_run: bool = False) -> dict[str, Any]:
        from backend.evolution.curator import run_curator

        return run_curator(dry_run=dry_run)

    async def _materialize_proposals(
        self,
        proposals: list[dict[str, Any]],
        *,
        session_id: str,
        score: float,
        baseline: float,
        force_skill: bool = False,
    ) -> list[dict[str, Any]]:
        cfg = get_evolution_config()
        out: list[dict[str, Any]] = []
        for proposal in proposals:
            kind = proposal.get("kind") or "skill"
            if kind == "tool" and not cfg.auto_create_tools and not force_skill:
                continue

            # P2 dedupe: patch similar instead of new spam
            similar = store.find_similar_asset(
                kind=kind,
                name=proposal["name"],
                summary=proposal.get("summary") or "",
                threshold=cfg.dedupe_similarity,
            )
            if similar and similar.get("gen", 0) >= cfg.max_skill_gen:
                out.append({"skipped": True, "reason": "max_gen", "name": proposal["name"]})
                continue
            if similar and float(similar.get("_similarity") or 0) >= 0.9:
                # reuse name to bump gen
                proposal["name"] = similar["name"]

            gate = run_gates(
                name=proposal["name"],
                content=proposal.get("content") or "",
                summary=proposal.get("summary") or "",
                score=score,
                baseline_score=baseline,
                kind=kind,
            )

            applied = False
            status = "draft"
            if gate["ok"]:
                if kind == "skill" and cfg.auto_apply_skills:
                    status = "active"
                    applied = True
                elif kind == "tool" and cfg.auto_apply_tools:
                    status = "active"
                    applied = True
                else:
                    status = "draft"

            asset = store.create_asset(
                kind=kind,
                name=proposal["name"],
                summary=proposal.get("summary") or "",
                source="auto",
                status=status,
                content=proposal.get("content") or "",
                session_id=session_id,
                last_score=score,
                meta={
                    **(proposal.get("meta") or {}),
                    "gates": gate["gates"],
                    "auto_applied": applied,
                    "engine": ENGINE_VERSION,
                    "deduped_from": similar["id"] if similar else None,
                },
            )

            # Always mirror to Skills list; enabled only when applied
            try:
                from backend.evolution.skill_sync import upsert_skill_from_asset

                await upsert_skill_from_asset(
                    name=proposal["name"],
                    summary=proposal.get("summary") or proposal["name"],
                    content=proposal.get("content") or "",
                    asset_id=asset.get("id"),
                    kind=kind,
                    enabled=bool(applied),
                )
            except Exception as e:
                logger.warning("skill mirror failed: %s", e)

            if applied and kind == "skill":
                if cfg.write_skill_files:
                    self._write_skill_file(proposal)
            # applied tool already handled by upsert enabled=True

            out.append({**asset, "applied": applied, "gate": gate})
            logger.info(
                "evolution asset kind=%s name=%s status=%s applied=%s",
                kind,
                proposal["name"],
                status,
                applied,
            )
        return out

    def _write_skill_file(self, proposal: dict[str, Any]) -> None:
        try:
            cfg = get_evolution_config()
            d = cfg.resolve_skills_dir()
            path = d / f"{proposal['name']}.md"
            path.write_text(proposal.get("content") or "", encoding="utf-8")
        except Exception as e:
            logger.warning("write skill file failed: %s", e)

    async def _apply_skill_to_db(
        self, proposal: dict[str, Any], asset_id: str | None = None
    ) -> None:
        try:
            from backend.evolution.skill_sync import upsert_skill_from_asset

            await upsert_skill_from_asset(
                name=proposal["name"],
                summary=proposal.get("summary") or proposal["name"],
                content=proposal.get("content") or "",
                asset_id=asset_id,
                kind=proposal.get("kind") or "skill",
                enabled=True,
            )
        except Exception as e:
            logger.warning("auto-apply skill sync failed: %s", e)


def _safe_args(args: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in list(args.items())[:20]:
        if k in {"token", "api_key", "password", "authorization"}:
            out[k] = "***"
        else:
            out[k] = str(v)[:200]
    return out


_manager: EvolutionManager | None = None


def get_evolution_manager() -> EvolutionManager:
    global _manager
    if _manager is None:
        _manager = EvolutionManager()
    return _manager
