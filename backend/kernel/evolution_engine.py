"""PLAN 阶段 0.7：受控进化引擎（PLAN_AI_WORKFORCE §3.d）。

成长的是 Agent 本身，不是工具箱。进化输入是 Episodic 工作记录
（inbox 工单 + kernel 事件），产出是**述职报告式建议**。

红线（不可绕过）：
- 分析器只产 pending 建议，永远不落应用——本模块不存在任何
  auto_apply 路径、配置项、环境变量后门
- approve 是人工动作（API 带 resolved_by）；应用后 payload.before
  保留回滚点；rollback 恢复 before 状态
- 全生命周期事件进哈希链（process_id="identity:<uuid>"）

**分析权威在 Rust**（``evolution_analyze``）。Python 只做：
1. 从 SQL/事件组装 snapshot
2. 调用 Rust 分析
3. 把 draft 建议镜像为 SQL pending（UI/审批持久化）
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

    # ── 分析器（Rust 权威；Python = snapshot feeder + SQL mirror）──

    def _build_snapshot(self, ident: Any, done: list, failed: list) -> dict[str, Any]:
        """Assemble analysis snapshot for Rust evolution_analyze."""
        iid = ident.id
        approved_caps: dict[str, int] = {}
        for e in self._kernel.events(kind="escalation_approved", limit=1000):
            if not self._event_belongs_to_identity(e, iid):
                continue
            for cap in (e.detail.get("capabilities") or []):
                approved_caps[str(cap)] = approved_caps.get(str(cap), 0) + 1
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
        return {
            "identity": str(getattr(ident, "name", None) or iid),
            "identity_id": str(iid),
            "capabilities": list(ident.capabilities or []),
            "done": len(done),
            "failed": len(failed),
            "recent_done": [str(i.instruction or "")[:80] for i in done[-5:]],
            "recent_errors": [str(i.error or "")[:80] for i in failed[-3:]],
            "approved_caps": approved_caps,
            "tool_attempts": attempts,
            "tool_denials": denials,
            "thresholds": {
                "min_samples": int(_threshold("_MIN_SAMPLES")),
                "deprecate_rate": float(_threshold("_DEPRECATE_DENIAL_RATE")),
                "caps_adjust_approvals": int(_threshold("_CAPS_ADJUST_APPROVALS")),
                "distill_min_done": int(_threshold("_DISTILL_MIN_DONE")),
                "distill_min_success": float(_threshold("_DISTILL_MIN_SUCCESS")),
                "planner_tune_fail_rate": float(_threshold("_PLANNER_TUNE_FAIL_RATE")),
            },
        }

    def _rust_analyze(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        """Call Rust evolution_analyze. Empty list if host unavailable."""
        try:
            k = self._kernel
            if not hasattr(k, "_call"):
                return []
            r = k._call("evolution_analyze", {"snapshot": snapshot}) or {}
            props = r.get("proposals") if isinstance(r, dict) else None
            if isinstance(props, list):
                return [p for p in props if isinstance(p, dict)]
        except Exception as e:
            logger.warning("evolution_analyze rust call failed: %s", e)
        return []

    def _allow_offline_mirror(self) -> bool:
        """Offline rule mirror only under DEV_UNSAFE / pytest (never production)."""
        import os
        import sys

        if "pytest" in sys.modules:
            return True
        try:
            from backend.kernel.production_guard import is_dev_unsafe

            return bool(is_dev_unsafe())
        except Exception:
            return (os.environ.get("TAKTON_DEV_UNSAFE") or "").strip() in (
                "1",
                "true",
                "yes",
            )

    def _offline_mirror_props(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        """Bit-for-bit style mirror of Rust EvolutionGate::analyze for offline tests.

        Production never reaches here without DEV_UNSAFE. Keep in lockstep with
        crates/takton-kernel/src/evolution_gate.rs::analyze.
        """
        identity = str(snapshot.get("identity") or "unknown")
        caps = list(snapshot.get("capabilities") or [])
        done = int(snapshot.get("done") or 0)
        failed = int(snapshot.get("failed") or 0)
        total = done + failed
        success_rate = (done / total) if total else 0.0
        thr = snapshot.get("thresholds") or {}
        min_samples = int(thr.get("min_samples") or 5)
        deprecate_rate = float(thr.get("deprecate_rate") or 0.5)
        caps_adjust_n = int(thr.get("caps_adjust_approvals") or 2)
        distill_min = int(thr.get("distill_min_done") or 5)
        distill_success = float(thr.get("distill_min_success") or 0.8)
        planner_fail = float(thr.get("planner_tune_fail_rate") or 0.3)
        out: list[dict[str, Any]] = []
        # Rule 1 distill
        if done >= distill_min and success_rate >= distill_success:
            recent = list(snapshot.get("recent_done") or [])[:5]
            out.append({
                "id": f"offline-{_uuid.uuid4().hex[:8]}",
                "kind": "memory_distill",
                "title": f"沉淀工作方法论（{done} 单，成功率 {success_rate:.0%}）",
                "body": (
                    f"完成 {done} 单 / 失败 {failed}，成功率 {success_rate:.0%}。"
                    f"近期：{'；'.join(str(x) for x in recent)}"
                ),
                "meta": {
                    "memory_kind": "methodology",
                    "content": (
                        f"经 {done} 单实践验证的工作模式（成功率 {success_rate:.0%}）："
                        + "；".join(str(x) for x in recent)
                    ),
                    "stats": {
                        "done": done,
                        "failed": failed,
                        "success_rate": round(success_rate, 4),
                    },
                    "source": "offline_mirror",
                },
            })
        # Rule 2 planner
        if total >= distill_min and success_rate < (1.0 - planner_fail):
            errors = list(snapshot.get("recent_errors") or [])[:3]
            fail_rate = 1.0 - success_rate
            out.append({
                "id": f"offline-{_uuid.uuid4().hex[:8]}",
                "kind": "planner_tune",
                "title": f"工作方式检讨（失败率 {fail_rate:.0%}）",
                "body": (
                    f"完成 {done} / 失败 {failed}，失败率 {fail_rate:.0%}。"
                    f"错误：{'；'.join(str(x) for x in errors)}"
                ),
                "meta": {
                    "planner_prefs": {"max_task_scope": "narrow", "verify_steps": True},
                    "stats": {"done": done, "failed": failed},
                    "source": "offline_mirror",
                },
            })
        # Rule 3 caps
        approved = snapshot.get("approved_caps") or {}
        if isinstance(approved, dict):
            for cap, count in approved.items():
                if int(count or 0) >= caps_adjust_n and cap not in caps:
                    out.append({
                        "id": f"offline-{_uuid.uuid4().hex[:8]}",
                        "kind": "caps_adjust",
                        "title": f"能力「{cap}」并入编制（已获批 {count} 次）",
                        "body": f"能力 {cap} 提权获批 {count} 次，建议并入身份权限档案。",
                        "meta": {
                            "add_capabilities": [cap],
                            "before": {"capabilities": caps},
                            "source": "offline_mirror",
                        },
                    })
        # Rule 4 deprecate
        attempts = snapshot.get("tool_attempts") or {}
        denials = snapshot.get("tool_denials") or {}
        if isinstance(attempts, dict):
            for target, n in attempts.items():
                n = int(n or 0)
                if n < min_samples or target not in caps:
                    continue
                d = int((denials or {}).get(target) or 0)
                rate = d / n if n else 0.0
                if rate >= deprecate_rate:
                    out.append({
                        "id": f"offline-{_uuid.uuid4().hex[:8]}",
                        "kind": "tool_deprecate",
                        "title": f"淘汰能力「{target}」（拒绝率 {rate:.0%}）",
                        "body": f"能力 {target} 共 {n} 次调用、{d} 次被拒（{rate:.0%}）。",
                        "meta": {
                            "remove_capabilities": [target],
                            "before": {"capabilities": caps},
                            "stats": {"attempts": n, "denials": d},
                            "source": "offline_mirror",
                        },
                    })
                    break
        _ = identity  # snapshot identity used by rust for pending-kind filter
        return out

    async def _materialize_props(
        self, iid: Any, ident: Any, rust_props: list[dict[str, Any]]
    ) -> list[Any]:
        proposals: list[Any] = []
        for rp in rust_props:
            kind = str(rp.get("kind") or "")
            if kind not in PROPOSAL_KINDS:
                continue
            title = str(rp.get("title") or kind)[:200]
            body = str(rp.get("body") or "")
            meta = rp.get("meta") if isinstance(rp.get("meta"), dict) else {}
            payload = dict(meta)
            payload.setdefault("source", meta.get("source") or "rust_analyze")
            payload["rust_proposal_id"] = str(rp.get("id") or "")
            if kind == "memory_distill":
                payload.setdefault("memory_kind", "methodology")
                payload.setdefault("content", body or title)
            if kind == "planner_tune":
                payload.setdefault(
                    "planner_prefs",
                    {"max_task_scope": "narrow", "verify_steps": True},
                )
                payload.setdefault(
                    "before",
                    {"planner_prefs": (ident.meta or {}).get("planner_prefs")},
                )
            if kind == "caps_adjust":
                payload.setdefault(
                    "before", {"capabilities": list(ident.capabilities or [])}
                )
            if kind == "tool_deprecate":
                payload.setdefault(
                    "before", {"capabilities": list(ident.capabilities or [])}
                )
            p = await self._create_if_no_pending(
                iid,
                kind=kind,
                title=title,
                rationale=body or title,
                payload=payload,
            )
            if p:
                proposals.append(p)
        return proposals

    async def analyze(self, identity_id: Any) -> list[Any]:
        """Feeder: SQL snapshot → Rust evolution_analyze → SQL pending mirror.

        业务规则权威在 Rust。无 host 时：生产返回空；pytest/DEV_UNSAFE 用
        offline mirror（与 Rust 规则对齐，仅便于单测）。
        """
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
        snapshot = self._build_snapshot(ident, done, failed)
        rust_props = self._rust_analyze(snapshot)
        if not rust_props:
            if self._allow_offline_mirror():
                logger.debug(
                    "evolution_analyze: offline mirror (no rust host / empty)"
                )
                rust_props = self._offline_mirror_props(snapshot)
            else:
                logger.warning(
                    "evolution_analyze: rust host unavailable — no proposals "
                    "(production fail-closed)"
                )
                return []
        return await self._materialize_props(iid, ident, rust_props)

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

    def _mirror_rust(
        self,
        action: str,
        *,
        kind: str = "",
        title: str = "",
        body: str = "",
        identity: str | None = None,
        proposal_id: str = "",
        by: str = "user",
    ) -> None:
        """Best-effort dual-write to Rust evolution_gate (authority ledger)."""
        try:
            k = self._kernel
            if not hasattr(k, "_call"):
                return
            if action == "submit":
                k._call(
                    "evolution_submit",
                    {
                        "kind": kind or "policy",
                        "title": title[:200],
                        "body": body[:2000],
                        "identity": identity,
                        "score": 0.5,
                        "meta": {"sql_proposal_id": proposal_id},
                    },
                )
            elif action == "approve" and proposal_id:
                # Rust list may use different ids — block_auto still records policy
                k._call("evolution_block_auto", {"reason": f"sql_approve:{proposal_id}"})
            elif action == "reject" and proposal_id:
                k._call("evolution_block_auto", {"reason": f"sql_reject:{proposal_id}"})
        except Exception as e:
            logger.debug("evolution rust mirror skip: %s", e)

    async def approve(self, proposal_id: Any, *, by: str) -> Any:
        """批准并立即应用。应用失败 → 状态回 pending 并记录错误。"""
        p = await self._get(proposal_id)
        if p.status != "pending":
            raise ValueError(f"建议状态 {p.status}，仅 pending 可批准")
        # Hard policy check via Rust (auto_apply must stay false)
        try:
            pol = {}
            if hasattr(self._kernel, "_acall"):
                # audit-fix: async 上下文走 _acall，避免阻塞事件循环
                pol = await self._kernel._acall("evolution_policy") or {}
            if pol.get("auto_apply") is True:
                raise RuntimeError("evolution auto_apply must be false")
        except RuntimeError:
            raise
        except Exception:
            pass
        await self._set_status(p, "approved", by=by)
        self._emit("evolution_approved", p.identity_id, {
            "proposal_id": str(p.id), "kind": p.kind, "by": by,
        })
        self._mirror_rust(
            "approve",
            proposal_id=str(p.id),
            by=by,
            kind=str(p.kind or ""),
            title=str(getattr(p, "title", "") or p.kind),
        )
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
        self._mirror_rust("reject", proposal_id=str(p.id), by=by)
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

    async def list_proposals(
        self,
        *,
        identity_id: Any | None = None,
        status: str | None = None,
        user_id: Any | None = None,
        include_orphan: bool = False,
    ) -> list[Any]:
        """列出进化提案。

        user_id 非空时只返回归属该用户的 Identity 的提案（多租户隔离）。
        include_orphan=True：额外包含 user_id IS NULL 的历史 Identity（单用户迁移）。
        """
        from sqlalchemy import or_

        from backend.models.agent_identity import AgentEvolutionProposal, AgentIdentity

        async with self._session_factory() as session:
            q = select(AgentEvolutionProposal).order_by(
                AgentEvolutionProposal.created_at.desc()
            )
            if user_id is not None:
                uid = _uuid.UUID(str(user_id)) if not isinstance(user_id, _uuid.UUID) else user_id
                q = q.join(
                    AgentIdentity,
                    AgentEvolutionProposal.identity_id == AgentIdentity.id,
                )
                if include_orphan:
                    q = q.where(
                        or_(
                            AgentIdentity.user_id == uid,
                            AgentIdentity.user_id.is_(None),
                        )
                    )
                else:
                    q = q.where(AgentIdentity.user_id == uid)
            if identity_id is not None:
                q = q.where(
                    AgentEvolutionProposal.identity_id
                    == _uuid.UUID(str(identity_id))
                )
            if status is not None:
                q = q.where(AgentEvolutionProposal.status == status)
            return list((await session.execute(q)).scalars().all())

    async def assert_proposal_owner(
        self,
        proposal_id: Any,
        user_id: Any,
        *,
        include_orphan: bool = False,
    ) -> Any:
        """归属校验：提案所属 Identity 必须属于 user（或 orphan 单用户窗口）。"""
        from backend.models.agent_identity import AgentEvolutionProposal, AgentIdentity

        pid = _uuid.UUID(str(proposal_id))
        uid = _uuid.UUID(str(user_id)) if not isinstance(user_id, _uuid.UUID) else user_id
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(AgentEvolutionProposal, AgentIdentity)
                    .join(
                        AgentIdentity,
                        AgentEvolutionProposal.identity_id == AgentIdentity.id,
                    )
                    .where(AgentEvolutionProposal.id == pid)
                )
            ).first()
            if row is None:
                raise ValueError(f"提案不存在: {proposal_id}")
            p, ident = row
            owner = getattr(ident, "user_id", None)
            if owner is None:
                if include_orphan:
                    return p
                raise ValueError("提案所属员工无归属，拒绝跨用户操作")
            if str(owner) != str(uid):
                raise ValueError("无权操作他人员工的进化提案")
            return p
