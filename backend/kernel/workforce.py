"""PLAN 阶段 0.6：workforce 装配与日报聚合。

装配关系：kernel（审计链）→ InboxService + IdentityRegistry（0.4.2 已有）
→ WorkforceDispatcher（唤醒执行）。lifespan 启动时 init + 拉起 dispatcher。

日报（「你不在的这段时间」）：聚合时间窗内的工单统计、各身份产出、
token 消耗、拦截/提权——首页不再面对空白对话框。
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_inbox_singleton: Any | None = None
_dispatcher_singleton: Any | None = None


def init_workforce(kernel: Any, session_factory: Any, settings: Any) -> tuple[Any, Any]:
    """装配 inbox + dispatcher（幂等）。返回 (inbox, dispatcher)。"""
    global _inbox_singleton, _dispatcher_singleton
    if _inbox_singleton is None:
        from backend.kernel.inbox import InboxService

        _inbox_singleton = InboxService(
            kernel,
            session_factory,
            max_pending=int(getattr(settings, "agent_inbox_max_pending", 200)),
        )
    if _dispatcher_singleton is None and kernel.identity_registry is not None:
        from backend.kernel.dispatcher import WorkforceDispatcher

        _dispatcher_singleton = WorkforceDispatcher(
            kernel,
            _inbox_singleton,
            kernel.identity_registry,
            session_factory,
            poll_seconds=float(getattr(settings, "agent_dispatcher_poll_seconds", 10)),
            item_timeout=float(getattr(settings, "agent_inbox_item_timeout", 600)),
        )
    return _inbox_singleton, _dispatcher_singleton


def get_workforce_inbox() -> Any | None:
    return _inbox_singleton


def get_workforce_dispatcher() -> Any | None:
    return _dispatcher_singleton


def reset_workforce_for_tests() -> None:
    global _inbox_singleton, _dispatcher_singleton, _evolution_singleton
    _inbox_singleton = None
    _dispatcher_singleton = None
    _evolution_singleton = None


_evolution_singleton: Any | None = None


def init_evolution(kernel: Any, session_factory: Any) -> Any:
    """装配备受控进化引擎（幂等）。"""
    global _evolution_singleton
    if _evolution_singleton is None and kernel.identity_registry is not None:
        from backend.kernel.evolution_engine import EvolutionEngine

        _evolution_singleton = EvolutionEngine(
            kernel, kernel.identity_registry, session_factory
        )
    return _evolution_singleton


def get_evolution_engine() -> Any | None:
    return _evolution_singleton


def _norm_uuid_hex(raw: str) -> str:
    return (raw or "").replace("-", "").lower().strip()


def _display_name_for_process_key(
    key: str,
    *,
    name_by_id: dict[str, str],
    name_by_sub: dict[str, str],
) -> str | None:
    """进程 identity_key → 人类可读名。解析不了的工程噪音返回 None（UI 不展示）。

    常见 key：
    - main：主会话（对用户无意义，隐藏）
    - sub:{sub_agent_uuid}：旧子代理 / 技能包运行
    - wf:{identity_uuid}：编制员工工单运行
    """
    k = (key or "").strip()
    if not k or k == "main":
        return None
    if k.startswith("wf:"):
        iid = _norm_uuid_hex(k[3:])
        return name_by_id.get(iid)
    if k.startswith("sub:"):
        sid = _norm_uuid_hex(k[4:])
        return name_by_sub.get(sid)
    # 已经是人名
    if ":" not in k and len(k) < 64:
        return k
    return None


async def build_org_view(session_factory: Any) -> dict[str, Any]:
    """汇报线观察 + 组织预算聚合（PLAN 0.7）。

    数据源是 kernel_processes 的 parent 链。对外只返回**员工姓名**边，
    绝不把 sub:uuid / main 等内部 key 直接甩给侧栏。
    """
    from sqlalchemy import select

    from backend.models.agent_identity import AgentIdentity, KernelProcessRecord

    async with session_factory() as session:
        procs = list((await session.execute(select(KernelProcessRecord))).scalars().all())
        idents = list((await session.execute(select(AgentIdentity))).scalars().all())

    name_by_id: dict[str, str] = {}
    name_by_sub: dict[str, str] = {}
    for i in idents:
        name_by_id[_norm_uuid_hex(str(i.id))] = i.name
        if i.sub_agent_id:
            name_by_sub[_norm_uuid_hex(str(i.sub_agent_id))] = i.name

    def label(key: str) -> str | None:
        return _display_name_for_process_key(
            key, name_by_id=name_by_id, name_by_sub=name_by_sub
        )

    # 终态进程预算已随工单结束释放；UI「当前预算」只计在跑进程。
    # 历史累计单独给出，避免「做完一单预算条永远 100%」。
    _TERMINAL = frozenset(
        {
            "completed",
            "failed",
            "killed",
            "interrupted",
            "cancelled",
            "dead",
            "done",
        }
    )

    by_key: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str], int] = {}
    pid_to_key: dict[str, str] = {}
    for p in procs:
        pid_to_key[p.process_id] = p.identity_key
    for p in procs:
        disp = label(p.identity_key) or p.identity_key
        entry = by_key.setdefault(
            disp,
            {
                "identity_key": disp,
                "raw_key": p.identity_key,
                "runs": 0,
                "live_runs": 0,
                # 当前在跑用量（预算条用这个）
                "tokens_used": 0,
                # 历史累计（含已结束工单，仅观测）
                "tokens_used_lifetime": 0,
                "token_budget": None,
                "token_budget_live": None,
                "children": {},
            },
        )
        used = int(p.tokens_used or 0)
        st = str(getattr(p, "state", "") or "").lower()
        live = st not in _TERMINAL and not getattr(p, "ended_at", None)
        entry["runs"] += 1
        entry["tokens_used_lifetime"] += used
        if live:
            entry["live_runs"] += 1
            entry["tokens_used"] += used
            if p.token_budget is not None:
                entry["token_budget_live"] = (
                    entry["token_budget_live"] or 0
                ) + int(p.token_budget)
        # 兼容旧字段：token_budget 优先展示在跑进程预算
        if live and p.token_budget is not None:
            entry["token_budget"] = entry["token_budget_live"]
        elif entry["token_budget"] is None and p.token_budget is not None:
            # 无在跑时不把历史预算加总成「当前顶」
            pass
        if p.parent_process_id:
            parent_key = pid_to_key.get(p.parent_process_id)
            if parent_key:
                parent_name = label(parent_key)
                child_name = label(p.identity_key)
                # 只保留双方都能解析成员工名的边（过滤 main→sub 噪音）
                if parent_name and child_name and parent_name != child_name:
                    edges[(parent_name, child_name)] = (
                        edges.get((parent_name, child_name), 0) + 1
                    )

    reports_to = [
        {"manager": parent, "worker": child, "delegations": count}
        for (parent, child), count in sorted(edges.items(), key=lambda kv: -kv[1])
    ]
    # agents 列表也只保留有人名的
    agents = [
        a
        for a in by_key.values()
        if a.get("identity_key")
        and not str(a["identity_key"]).startswith(("sub:", "wf:", "main"))
    ]
    return {
        "agents": sorted(
            agents,
            key=lambda a: (
                -int(a.get("tokens_used") or 0),
                -int(a.get("tokens_used_lifetime") or 0),
            ),
        ),
        "reports_to": reports_to,
        "total_processes": len(procs),
        "live_processes": sum(
            1
            for p in procs
            if str(getattr(p, "state", "") or "").lower() not in _TERMINAL
            and not getattr(p, "ended_at", None)
        ),
    }


async def build_daily_report(
    kernel: Any,
    inbox: Any,
    *,
    hours: int = 24,
    identity_id: str | None = None,
) -> dict[str, Any]:
    """workforce 工作汇报（日报数据）。

    「你不在的这段时间发生了什么」——不问用户翻日志，直接给答案。
    identity_id 非空时只汇总该员工（各联系人会话内容应不同）。
    """
    since = time.time() - hours * 3600
    scope_id = (identity_id or "").strip() or None

    if scope_id:
        # 单员工：按人拉工单再聚合 stats
        all_for_ident = await inbox.list_items(identity_id=scope_id, limit=100)
        # since 过滤（list 可能无 since 时用 created_at / finished）
        def _in_window(it: Any) -> bool:
            fa = getattr(it, "finished_at", None)
            if fa is not None:
                try:
                    return float(fa) >= since
                except Exception:
                    pass
            ca = getattr(it, "created_at", None)
            if ca is not None:
                try:
                    import datetime as _dt

                    if isinstance(ca, _dt.datetime):
                        ts = ca.timestamp()
                    else:
                        ts = float(ca)
                    return ts >= since
                except Exception:
                    return True
            return True

        scoped = [i for i in all_for_ident if _in_window(i)]
        stats: dict[str, int] = {}
        for i in scoped:
            st = str(getattr(i, "status", "") or "unknown")
            stats[st] = stats.get(st, 0) + 1
        recent_done = [i for i in scoped if i.status == "done"][:20]
        recent_failed = [i for i in scoped if i.status == "failed"][:10]
    else:
        stats = await inbox.stats(since_ts=since)
        recent_done = await inbox.list_items(status="done", limit=20, since_ts=since)
        recent_failed = await inbox.list_items(status="failed", limit=10, since_ts=since)

    # 名字映射（汇报卡片展示）
    name_by_id: dict[str, str] = {}
    try:
        reg = getattr(kernel, "identity_registry", None)
        if reg is not None:
            for ident in await reg.list(status=None):
                name_by_id[str(ident.id)] = ident.name
    except Exception:
        pass

    # 按身份聚合产出
    by_identity: dict[str, dict[str, Any]] = {}
    for item in recent_done:
        key = str(item.identity_id)
        if scope_id and key != scope_id:
            continue
        entry = by_identity.setdefault(
            key,
            {
                "done": 0,
                "failed": 0,
                "latest_results": [],
                "name": name_by_id.get(key, ""),
            },
        )
        entry["done"] += 1
        if len(entry["latest_results"]) < 3 and item.result:
            entry["latest_results"].append(item.result[:500])
    for item in recent_failed:
        key = str(item.identity_id)
        if scope_id and key != scope_id:
            continue
        entry = by_identity.setdefault(
            key,
            {
                "done": 0,
                "failed": 0,
                "latest_results": [],
                "name": name_by_id.get(key, ""),
            },
        )
        entry["failed"] = int(entry.get("failed") or 0) + 1

    # 时间窗内 kernel 事件（全队；单员工时仅记提示）
    events = kernel.events(limit=1000)
    window_events = [e for e in events if e.ts >= since]
    kind_counts: dict[str, int] = {}
    denials = 0
    for e in window_events:
        kind_counts[e.kind] = kind_counts.get(e.kind, 0) + 1
        if e.kind == "mediation" and e.detail.get("allowed") is False:
            denials += 1

    pending_escalations = len(kernel.list_escalations(status="pending"))

    def _item_dict(i: Any, *, failed: bool = False) -> dict[str, Any]:
        iid = str(i.identity_id)
        base = {
            "id": str(i.id),
            "identity_id": iid,
            "identity_name": name_by_id.get(iid, ""),
            "source": i.source,
            "instruction": (i.instruction or "")[:200],
            "finished_at": i.finished_at,
        }
        if failed:
            base["error"] = (i.error or "")[:300]
        else:
            base["result"] = (i.result or "")[:500]
        return base

    return {
        "hours": hours,
        "since_ts": since,
        "identity_id": scope_id,
        "identity_name": name_by_id.get(scope_id or "", "") if scope_id else None,
        "inbox": {
            "stats": stats,
            "total": sum(stats.values()),
            "recent_done": [_item_dict(i) for i in recent_done[:10]],
            "recent_failed": [
                _item_dict(i, failed=True) for i in recent_failed[:5]
            ],
        },
        "by_identity": by_identity,
        "kernel": {
            "event_kinds": kind_counts if not scope_id else {},
            "mediation_denials": denials if not scope_id else 0,
            "pending_escalations": pending_escalations if not scope_id else 0,
        },
    }
