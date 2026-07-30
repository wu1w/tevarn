"""Run 生命周期门面（Phase 2.1）

唯一允许改 AgentRun.status 的业务入口：validate → repo.update。
origin 推断、对外粗粒度 status 映射也集中在此。

设计见 docs/design/RUN_UNIFICATION.md。
"""
from __future__ import annotations

import logging
import uuid
from enum import Enum
from typing import Any

from backend.agent.run_state import (
    TERMINAL_STATES,
    IllegalTransitionError,
    RunStatus,
    can_transition,
    validate_transition,
)

logger = logging.getLogger(__name__)


class RunOrigin(str, Enum):
    CHAT = "chat"
    INBOX = "inbox"
    CRON = "cron"
    CLUSTER = "cluster"
    SUBAGENT = "subagent"
    HEADLESS = "headless"


# 合法 origin 集合（存库用字符串）
ORIGINS: frozenset[str] = frozenset(o.value for o in RunOrigin)


def public_status(internal: str | RunStatus) -> str:
    """细粒度内部 status → 对外粗粒度（API/UI）。"""
    s = internal.value if isinstance(internal, RunStatus) else str(internal or "")
    if s in ("created",):
        return "pending"
    if s in ("planning", "executing", "verifying"):
        return "running"
    if s in ("waiting",):
        return "waiting_approval"
    if s in ("interrupted", "suspended"):
        return "suspended"
    if s in ("done", "failed", "cancelled"):
        return s
    return s or "pending"


def infer_origin(
    *,
    mode: str | None = None,
    meta: dict[str, Any] | None = None,
    parent_run_id: uuid.UUID | str | None = None,
    explicit: str | None = None,
) -> str:
    """从 mode/meta 推断 origin；显式值优先。"""
    if explicit:
        e = str(explicit).strip().lower()
        if e in ORIGINS:
            return e
    m = meta if isinstance(meta, dict) else {}
    mo = str(m.get("origin") or "").strip().lower()
    if mo in ORIGINS:
        return mo
    if m.get("cluster_run_id") or m.get("cluster_id") or m.get("cluster_task_id"):
        return RunOrigin.CLUSTER.value
    # cron 优先于 inbox_item（cron 投递也会带 inbox_item_id）
    if str(m.get("source") or "").lower() == "cron" or m.get("cron_job_id"):
        return RunOrigin.CRON.value
    if m.get("inbox_item_id") or m.get("inbox_id"):
        return RunOrigin.INBOX.value
    md = str(mode or "").strip().lower()
    if md in ("cluster", "cluster_sub"):
        return RunOrigin.CLUSTER.value
    if md == "workforce":
        return RunOrigin.INBOX.value
    if md == "subagent" or parent_run_id:
        return RunOrigin.SUBAGENT.value
    if md in ("headless", "ci", "script"):
        return RunOrigin.HEADLESS.value
    if str(m.get("agent_key") or "").startswith("headless"):
        return RunOrigin.HEADLESS.value
    return RunOrigin.CHAT.value


def normalize_parent_run_id(
    raw: uuid.UUID | str | None,
    meta: dict[str, Any] | None = None,
) -> uuid.UUID | None:
    if raw is not None:
        try:
            return raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw))
        except (ValueError, TypeError):
            pass
    m = meta if isinstance(meta, dict) else {}
    pr = m.get("parent_run_id")
    if pr:
        try:
            return pr if isinstance(pr, uuid.UUID) else uuid.UUID(str(pr))
        except (ValueError, TypeError):
            return None
    return None


def normalize_identity_id(
    raw: uuid.UUID | str | None,
    meta: dict[str, Any] | None = None,
) -> uuid.UUID | None:
    if raw is not None:
        try:
            return raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw))
        except (ValueError, TypeError):
            pass
    m = meta if isinstance(meta, dict) else {}
    for k in ("identity_id", "agent_identity_id"):
        v = m.get(k)
        if v:
            try:
                return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))
            except (ValueError, TypeError):
                continue
    return None


async def transition_run(
    run_id: uuid.UUID,
    *,
    src: RunStatus | str,
    dst: RunStatus | str,
    extra: dict[str, Any] | None = None,
) -> RunStatus | None:
    """校验并写入 status（及 extra 字段）。非法迁移返回 None 不抛。"""
    try:
        dst_s = validate_transition(src, dst)
    except IllegalTransitionError as e:
        logger.warning("run_lifecycle.transition skipped: %s", e)
        return None
    if dst_s == RunStatus(src) if not isinstance(src, RunStatus) else src:
        # same state — still allow extra updates
        if not extra:
            return dst_s
    try:
        from backend.repositories.agent_run_repo import AsyncAgentRunRepository

        data: dict[str, Any] = {"status": dst_s.value}
        if extra:
            data.update(extra)
        repo = AsyncAgentRunRepository()
        await repo.update_run(run_id, data)
        return dst_s
    except Exception as e:
        logger.warning("run_lifecycle.transition_run failed run=%s: %s", run_id, e)
        return None


def is_terminal(status: str | RunStatus) -> bool:
    try:
        s = status if isinstance(status, RunStatus) else RunStatus(str(status))
    except ValueError:
        return False
    return s in TERMINAL_STATES


async def ensure_bookkeeping_session(
    user_id: uuid.UUID | str | None,
    *,
    kind: str = "cluster",
) -> uuid.UUID:
    """为无 chat 会话的路径（cluster 等）提供可挂载的 session_id。

    每用户每种 kind 复用同一 bookkeeping session，避免刷屏。
    user_id 为空时创建匿名 bookkeeping 会话（单用户模式可用）。
    """
    from backend.repositories.session_repo import AsyncSessionRepository

    sessions = AsyncSessionRepository()
    uid: uuid.UUID | None
    try:
        uid = (
            user_id
            if isinstance(user_id, uuid.UUID)
            else (uuid.UUID(str(user_id)) if user_id else None)
        )
    except (ValueError, TypeError):
        uid = None

    if uid is None:
        # 单用户：取第一个用户
        try:
            from sqlalchemy import select

            from backend.database import AsyncSessionLocal
            from backend.models.user import User

            async with AsyncSessionLocal() as db:
                r = await db.execute(select(User).limit(1))
                u = r.scalar_one_or_none()
                if u is not None:
                    uid = u.id
        except Exception:
            uid = None

    if uid is not None:
        try:
            existing = await sessions.list_by_user(uid)
            for s in existing or []:
                cfg = getattr(s, "config", None) or {}
                if isinstance(cfg, dict) and cfg.get("_bookkeeping") == kind:
                    return s.id
        except Exception:
            pass

    if uid is None:
        raise RuntimeError("ensure_bookkeeping_session: no user available")

    s = await sessions.create(
        {
            "user_id": uid,
            "config": {
                "identity": f"{kind}-bookkeeping",
                "skills": [],
                "_bookkeeping": kind,
            },
        }
    )
    return s.id


def build_create_payload(
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    mode: str = "default",
    input_summary: str = "",
    meta: dict[str, Any] | None = None,
    origin: str | None = None,
    identity_id: uuid.UUID | str | None = None,
    parent_run_id: uuid.UUID | str | None = None,
    token_limit: int = 0,
    token_used: int = 0,
    status: str = RunStatus.CREATED.value,
    started_at: Any = None,
) -> dict[str, Any]:
    """构造 create_run 字典（含 origin / identity / parent / budget）。"""
    m = dict(meta or {})
    pr = normalize_parent_run_id(parent_run_id, m)
    iid = normalize_identity_id(identity_id, m)
    org = infer_origin(mode=mode, meta=m, parent_run_id=pr, explicit=origin)
    # 列权威：meta 里同步一份便于旧读者
    m["origin"] = org
    if pr is not None:
        m["parent_run_id"] = str(pr)
    if iid is not None:
        m["identity_id"] = str(iid)
    payload: dict[str, Any] = {
        "session_id": session_id,
        "user_id": user_id,
        "status": status,
        "mode": mode or "default",
        "origin": org,
        "identity_id": iid,
        "parent_run_id": pr,
        "input_summary": (input_summary or "")[:512],
        "token_limit": int(token_limit or 0),
        "token_used": int(token_used or 0),
        "meta": m or None,
    }
    if started_at is not None:
        payload["started_at"] = started_at
    return payload


__all__ = [
    "RunOrigin",
    "ORIGINS",
    "public_status",
    "infer_origin",
    "normalize_parent_run_id",
    "normalize_identity_id",
    "transition_run",
    "is_terminal",
    "build_create_payload",
    "can_transition",
    "validate_transition",
    "RunStatus",
    "IllegalTransitionError",
    "TERMINAL_STATES",
]
