"""PLAN 阶段 0.7：受控进化引擎（PLAN_AI_WORKFORCE §3.d）。

成长的是 Agent 本身，不是工具箱。进化输入是 Episodic 工作记录
（inbox 工单 + kernel 事件），产出是**述职报告式建议**。

红线（不可绕过）：
- 分析器只产 pending 建议，永远不落应用——本模块不存在任何
  auto_apply 路径、配置项、环境变量后门
- approve 是人工动作（API 带 resolved_by）；应用后 payload.before
  保留回滚点；rollback 恢复 before 状态
- 全生命周期事件进哈希链（process_id="identity:<uuid>"）

分析器是规则化的（无 LLM）：机器可验证、可复现、可单测——
三个月验收「代际进步可测量」要求分析过程本身也是可测量的。
"""

from __future__ import annotations

import logging
import time
import uuid as _uuid
from typing import Any

from sqlalchemy import select

logger = logging.getLogger(__name__)

PROPOSAL_KINDS = ("memory_distill", "tool_deprecate", "caps_adjust", "planner_tune")

# 分析阈值（规则参数——可调但只影响「是否生成建议」，不影响「是否生效」）
_MIN_SAMPLES = 5            # 工具统计最小样本
_DEPRECATE_DENIAL_RATE = 0.5  # 工具拒绝率阈值
_CAPS_ADJUST_APPROVALS = 2  # 同能力 escalation 获批次数阈值
_DISTILL_MIN_DONE = 5       # SOP 沉淀最小完成单数
_DISTILL_MIN_SUCCESS = 0.8  # SOP 沉淀最小成功率
_PLANNER_TUNE_FAIL_RATE = 0.3  # planner 调整建议的失败率阈值

# settings 键名映射（Alpha Review #3：阈值参数化——不同身份工作模式
# 不同，统一硬编码不合理；读配置而非读常量，常量仅作默认值兜底）
_SETTING_KEYS = {
    "_MIN_SAMPLES": "agent_evolution_min_samples",
    "_DEPRECATE_DENIAL_RATE": "agent_evolution_deprecate_denial_rate",
    "_CAPS_ADJUST_APPROVALS": "agent_evolution_caps_adjust_approvals",
    "_DISTILL_MIN_DONE": "agent_evolution_distill_min_done",
    "_DISTILL_MIN_SUCCESS": "agent_evolution_distill_min_success",
    "_PLANNER_TUNE_FAIL_RATE": "agent_evolution_planner_tune_fail_rate",
}


def _threshold(const_name: str):
    """读 settings 中的演化阈值；未配置/异常时回退模块常量默认值。"""
    default = globals()[const_name]
    try:
        from backend.core.config import settings

        return getattr(settings, _SETTING_KEYS[const_name], default)
    except Exception:
        return default


class EvolutionEngine:
    """受控进化引擎。由 workforce 装配（kernel + registry + inbox）。"""

    def __init__(self, kernel: Any, registry: Any, session_factory: Any) -> None:
        self._kernel = kernel
        self._registry = registry
        self._session_factory = session_factory

    def _emit(self, kind: str, identity_id: Any, detail: dict[str, Any]) -> None:
        self._kernel._emit(kind, f"identity:{identity_id}", detail)

    def _event_belongs_to_identity(self, event: Any, identity_id: Any) -> bool:
        """事件是否归属该身份（防跨身份污染分析）。

        判定顺序：
        1. detail.identity_id
        2. process_id 形如 identity:<uuid>
        3. 进程 meta.identity_id
        均无则 **不计入** 身份级规则（caps_adjust / tool_deprecate）——
        宁可漏报，不可串味。
        """
        want = str(identity_id)
        detail = getattr(event, "detail", None) or {}
        if detail.get("identity_id"):
            return str(detail["identity_id"]) == want
        pid = str(getattr(event, "process_id", "") or "")
        if pid.startswith("identity:"):
            return pid.split(":", 1)[1] == want
        try:
            proc = self._kernel.get_process(pid)
        except Exception:
            proc = None
        if proc is not None:
            meta = proc.meta or {}
            if meta.get("identity_id"):
                return str(meta["identity_id"]) == want
        return False

    # ── 分析器（规则化述职报告）────────────────────────────────

    async def analyze(self, identity_id: Any) -> list[Any]:
        """分析身份的工作记录，生成 pending 建议（可重复调用，
        同 kind 已有 pending 时跳过——不刷屏）。"""
        from backend.models.agent_identity import AgentInboxItem

        iid = _uuid.UUID(str(identity_id))
        ident = await self._registry.get(iid)
        if ident is None:
            raise ValueError(f"未知身份 {identity_id}")

        async with self._session_factory() as session:
            items = list(
                (
                    await session.execute(
                        select(AgentInboxItem).where(AgentInboxItem.identity_id == iid)
                    )
                ).scalars().all()
            )
        done = [i for i in items if i.status == "done"]
        failed = [i for i in items if i.status == "failed"]
        total_finished = len(done) + len(failed)
        success_rate = len(done) / total_finished if total_finished else 0.0

        proposals: list[Any] = []

        # 规则 1：SOP 沉淀（memory_distill）——干得又多又好 → 方法论该进档案
        if len(done) >= _threshold("_DISTILL_MIN_DONE") and success_rate >= _threshold("_DISTILL_MIN_SUCCESS"):
            recent = [i.instruction[:80] for i in done[-5:]]
            p = await self._create_if_no_pending(
                iid,
                kind="memory_distill",
                title=f"沉淀工作方法论（{len(done)} 单，成功率 {success_rate:.0%}）",
                rationale=(
                    f"该身份已完成 {len(done)} 单（失败 {len(failed)} 单，成功率 "
                    f"{success_rate:.0%}），达到方法论沉淀阈值。"
                    f"近期工单：{'；'.join(recent)}。建议将高频任务模式固化为 SOP。"
                ),
                payload={
                    "memory_kind": "methodology",
                    "content": (
                        f"经 {len(done)} 单实践验证的工作模式"
                        f"（成功率 {success_rate:.0%}）：高频任务类型——"
                        + "；".join(recent)
                    ),
                    "stats": {"done": len(done), "failed": len(failed),
                              "success_rate": round(success_rate, 4)},
                },
            )
            if p:
                proposals.append(p)

        # 规则 2：失败率过高（planner_tune）——该检讨工作方式
        if total_finished >= _threshold("_DISTILL_MIN_DONE") and success_rate < (1 - _threshold("_PLANNER_TUNE_FAIL_RATE")):
            errors = [ (i.error or "")[:80] for i in failed[-3:] ]
            p = await self._create_if_no_pending(
                iid,
                kind="planner_tune",
                title=f"工作方式检讨（失败率 {1 - success_rate:.0%}）",
                rationale=(
                    f"完成 {len(done)} 单 / 失败 {len(failed)} 单，失败率 "
                    f"{1 - success_rate:.0%} 超过阈值 {_threshold('_PLANNER_TUNE_FAIL_RATE'):.0%}。"
                    f"近期失败原因：{'；'.join(errors) or '无'}。"
                    f"建议调整 planner 偏好（缩小任务范围/增加验证步骤）。"
                ),
                payload={
                    "planner_prefs": {"max_task_scope": "narrow", "verify_steps": True},
                    "before": {"planner_prefs": (ident.meta or {}).get("planner_prefs")},
                    "stats": {"done": len(done), "failed": len(failed)},
                },
            )
            if p:
                proposals.append(p)

        # 规则 3：escalation 获批模式（caps_adjust）——反复获批的能力该入编制。
        # 必须按 identity 过滤，禁止跨身份串统计
        approved_caps: dict[str, int] = {}
        for e in self._kernel.events(kind="escalation_approved", limit=1000):
            if not self._event_belongs_to_identity(e, iid):
                continue
            for cap in (e.detail.get("capabilities") or []):
                approved_caps[cap] = approved_caps.get(cap, 0) + 1
        for cap, count in approved_caps.items():
            if count >= _threshold("_CAPS_ADJUST_APPROVALS") and cap not in (ident.capabilities or []):
                p = await self._create_if_no_pending(
                    iid,
                    kind="caps_adjust",
                    title=f"能力「{cap}」并入编制（已获批 {count} 次）",
                    rationale=(
                        f"能力「{cap}」通过提权申请获批 {count} 次——反复临时授权"
                        f"说明它是本职工作所需。建议并入身份权限档案，减少审批摩擦。"
                    ),
                    payload={
                        "add_capabilities": [cap],
                        "before": {"capabilities": ident.capabilities},
                    },
                )
                if p:
                    proposals.append(p)

        # 规则 4：工具淘汰（tool_deprecate）——mediation 拒绝率过高的能力
        denials: dict[str, int] = {}
        attempts: dict[str, int] = {}
        for e in self._kernel.events(kind="mediation", limit=1000):
            if not self._event_belongs_to_identity(e, iid):
                continue
            target = str(e.detail.get("target") or "")
            if not target:
                continue
            attempts[target] = attempts.get(target, 0) + 1
            if e.detail.get("allowed") is False:
                denials[target] = denials.get(target, 0) + 1
        for target, n in attempts.items():
            if n >= _threshold("_MIN_SAMPLES") and target in (ident.capabilities or []):
                rate = denials.get(target, 0) / n
                if rate >= _threshold("_DEPRECATE_DENIAL_RATE"):
                    p = await self._create_if_no_pending(
                        iid,
                        kind="tool_deprecate",
                        title=f"淘汰能力「{target}」（拒绝率 {rate:.0%}）",
                        rationale=(
                            f"能力「{target}」共 {n} 次调用其中 {denials.get(target, 0)} 次"
                            f"被拒（{rate:.0%}）——该能力在编制内但持续无法有效使用，"
                            f"建议移出权限档案，保持编制精干。"
                        ),
                        payload={
                            "remove_capabilities": [target],
                            "before": {"capabilities": ident.capabilities},
                            "stats": {"attempts": n, "denials": denials.get(target, 0)},
                        },
                    )
                    if p:
                        proposals.append(p)

        return proposals

    async def _create_if_no_pending(self, iid: Any, **fields: Any) -> Any | None:
        from backend.models.agent_identity import AgentEvolutionProposal

        async with self._session_factory() as session:
            existing = (
                await session.execute(
                    select(AgentEvolutionProposal).where(
                        AgentEvolutionProposal.identity_id == iid,
                        AgentEvolutionProposal.kind == fields["kind"],
                        AgentEvolutionProposal.status == "pending",
                    )
                )
            ).scalars().first()
            if existing is not None:
                return None
            p = AgentEvolutionProposal(
                identity_id=iid,
                kind=fields["kind"],
                title=fields["title"],
                rationale=fields["rationale"],
                payload=fields.get("payload") or {},
            )
            session.add(p)
            await session.commit()
            await session.refresh(p)
        self._emit("evolution_proposed", iid, {
            "proposal_id": str(p.id), "kind": p.kind, "title": p.title,
        })
        return p

    # ── 审批 / 应用 / 回滚（人工动作，无自动路径）──────────────────

    async def _get(self, proposal_id: Any) -> Any:
        from backend.models.agent_identity import AgentEvolutionProposal

        async with self._session_factory() as session:
            p = (
                await session.execute(
                    select(AgentEvolutionProposal).where(
                        AgentEvolutionProposal.id == _uuid.UUID(str(proposal_id))
                    )
                )
            ).scalar_one_or_none()
            if p is None:
                raise ValueError(f"未知进化建议 {proposal_id}")
            return p

    async def approve(self, proposal_id: Any, *, by: str) -> Any:
        """批准并立即应用。应用失败 → 状态回 pending 并记录错误。"""
        p = await self._get(proposal_id)
        if p.status != "pending":
            raise ValueError(f"建议状态 {p.status}，仅 pending 可批准")
        await self._set_status(p, "approved", by=by)
        self._emit("evolution_approved", p.identity_id, {
            "proposal_id": str(p.id), "kind": p.kind, "by": by,
        })
        try:
            await self._apply(p, by=by)
        except Exception as e:
            logger.error("进化应用失败 %s: %s", p.id, e)
            await self._set_status(p, "pending")  # 应用失败回 pending（可重试）
            raise
        return await self._get(proposal_id)

    async def reject(self, proposal_id: Any, *, by: str) -> Any:
        p = await self._get(proposal_id)
        if p.status != "pending":
            raise ValueError(f"建议状态 {p.status}，仅 pending 可拒绝")
        await self._set_status(p, "rejected", by=by)
        self._emit("evolution_rejected", p.identity_id, {
            "proposal_id": str(p.id), "kind": p.kind, "by": by,
        })
        return await self._get(proposal_id)

    async def rollback(self, proposal_id: Any, *, by: str) -> Any:
        """回滚：按 payload.before 恢复应用前状态。"""
        p = await self._get(proposal_id)
        if p.status != "applied":
            raise ValueError(f"建议状态 {p.status}，仅 applied 可回滚")
        before = (p.payload or {}).get("before") or {}
        if p.kind == "memory_distill":
            # 记忆回滚：正规 supersede 到 tombstone（禁止 self-supersede 脏链）
            entry_id = (p.payload or {}).get("applied_entry_id")
            if entry_id:
                from backend.models.agent_identity import IdentityMemoryEntry

                async with self._session_factory() as session:
                    entry = (
                        await session.execute(
                            select(IdentityMemoryEntry).where(
                                IdentityMemoryEntry.id == _uuid.UUID(str(entry_id))
                            )
                        )
                    ).scalar_one_or_none()
                    if entry is not None and entry.superseded_by is None:
                        tomb = IdentityMemoryEntry(
                            identity_id=entry.identity_id,
                            kind=entry.kind,
                            content="[rolled_back]",
                            source="system",
                            approved_by=f"rollback:{by}",
                            version=int(entry.version or 1) + 1,
                        )
                        session.add(tomb)
                        await session.flush()
                        entry.superseded_by = tomb.id
                        # tombstone 自身立即被标记为失效（不进入 current_memory）
                        tomb.superseded_by = tomb.id
                        await session.commit()
                # RAG 清旧版
                try:
                    from backend.services.rag.capability import use_vector_rag

                    if use_vector_rag():
                        from backend.services.rag.factory import RAGServiceFactory

                        rag = RAGServiceFactory.get_service()
                        await rag.delete_identity_memory(str(entry_id))
                except Exception as e:
                    logger.debug("evolution rollback RAG delete skip: %s", e)
        elif p.kind in ("caps_adjust", "tool_deprecate"):
            await self._registry.set_capabilities(
                p.identity_id, before.get("capabilities"), by=f"rollback:{by}"
            )
        elif p.kind == "planner_tune":
            await self._write_meta_prefs(p.identity_id, before.get("planner_prefs"))
        await self._set_status(p, "rolled_back", by=by)
        self._emit("evolution_rolled_back", p.identity_id, {
            "proposal_id": str(p.id), "kind": p.kind, "by": by,
        })
        return await self._get(proposal_id)

    # ── 应用器 ─────────────────────────────────────────────────

    async def _apply(self, p: Any, *, by: str) -> None:
        payload = p.payload or {}
        if p.kind == "memory_distill":
            entry = await self._registry.add_memory(
                p.identity_id,
                str(payload.get("memory_kind") or "methodology"),
                str(payload.get("content") or p.title),
                source="distilled",
                approved_by=by,  # 审批人即应用人（红线：distilled 必有 approved_by）
            )
            payload["applied_entry_id"] = str(entry.id)
            await self._update_payload(p, payload)
        elif p.kind == "caps_adjust":
            ident = await self._registry.get(p.identity_id)
            merged = sorted(set(ident.capabilities or []) | set(payload.get("add_capabilities") or []))
            await self._registry.set_capabilities(p.identity_id, merged, by=f"evolution:{by}")
        elif p.kind == "tool_deprecate":
            ident = await self._registry.get(p.identity_id)
            remaining = sorted(
                set(ident.capabilities or []) - set(payload.get("remove_capabilities") or [])
            )
            await self._registry.set_capabilities(p.identity_id, remaining, by=f"evolution:{by}")
        elif p.kind == "planner_tune":
            await self._write_meta_prefs(p.identity_id, payload.get("planner_prefs"))
        else:
            raise ValueError(f"未知建议类型 {p.kind}")
        await self._set_status(p, "applied", by=by, applied=True)
        self._emit("evolution_applied", p.identity_id, {
            "proposal_id": str(p.id), "kind": p.kind, "by": by,
        })

    async def _write_meta_prefs(self, identity_id: Any, prefs: Any) -> None:
        from backend.models.agent_identity import AgentIdentity

        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(AgentIdentity).where(AgentIdentity.id == _uuid.UUID(str(identity_id)))
                )
            ).scalar_one_or_none()
            if row is not None:
                meta = dict(row.meta or {})
                if prefs is None:
                    meta.pop("planner_prefs", None)
                else:
                    meta["planner_prefs"] = prefs
                row.meta = meta
                await session.commit()

    async def _update_payload(self, p: Any, payload: dict) -> None:
        from backend.models.agent_identity import AgentEvolutionProposal

        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(AgentEvolutionProposal).where(AgentEvolutionProposal.id == p.id)
                )
            ).scalar_one_or_none()
            if row is not None:
                row.payload = payload
                await session.commit()

    async def _set_status(self, p: Any, status: str, *, by: str | None = None, applied: bool = False) -> None:
        from backend.models.agent_identity import AgentEvolutionProposal

        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(AgentEvolutionProposal).where(AgentEvolutionProposal.id == p.id)
                )
            ).scalar_one_or_none()
            if row is None:
                return
            row.status = status
            if by is not None:
                row.resolved_by = by
                row.resolved_at = time.time()
            if applied:
                row.applied_at = time.time()
            if status == "rolled_back":
                row.rolled_back_at = time.time()
            await session.commit()

    async def list_proposals(self, *, identity_id: Any | None = None, status: str | None = None) -> list[Any]:
        from backend.models.agent_identity import AgentEvolutionProposal

        async with self._session_factory() as session:
            q = select(AgentEvolutionProposal).order_by(AgentEvolutionProposal.created_at.desc())
            if identity_id is not None:
                q = q.where(AgentEvolutionProposal.identity_id == _uuid.UUID(str(identity_id)))
            if status is not None:
                q = q.where(AgentEvolutionProposal.status == status)
            return list((await session.execute(q)).scalars().all())
