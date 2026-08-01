"""进程访问控制：API 归属校验，防止 process_id 横向越权。"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_process_dict(kernel: Any, process_id: str) -> dict[str, Any] | None:
    pid = str(process_id or "").strip()
    if not pid:
        return None
    try:
        if hasattr(kernel, "get_process"):
            p = kernel.get_process(pid)
            if p is None:
                return None
            if hasattr(p, "to_dict"):
                return p.to_dict()
            return {
                "id": getattr(p, "id", pid),
                "session_id": getattr(p, "session_id", None),
                "identity": getattr(p, "identity", None),
                "state": str(getattr(p, "state", "") or ""),
                "meta": dict(getattr(p, "meta", None) or {}),
            }
        if hasattr(kernel, "_call"):
            r = kernel._call("get_process", {"process_id": pid}) or {}
            if isinstance(r, dict) and (r.get("id") or r.get("process_id")):
                return r
    except Exception as e:
        logger.debug("get_process_dict: %s", e)
    return None


def _single_user_mode() -> bool:
    try:
        from backend.core.config import settings

        return bool(getattr(settings, "single_user_mode", True))
    except Exception:
        return True


def assert_process_accessible(
    kernel: Any,
    process_id: str,
    *,
    session_id: str | None = None,
    identity_id: str | None = None,
    require_live: bool = False,
    require_session: bool = False,
) -> dict[str, Any]:
    """校验进程存在，并匹配 session / identity。

    require_session=True 时：
      - 必须提供 session_id
      - 若进程绑定了 session，必须一致
      - 若进程无 session（编制/后台）：仅当 identity 以 wf: 开头或提供 matching identity 时放行

    Raises:
        ValueError: 不存在或无权
    """
    p = get_process_dict(kernel, process_id)
    if not p:
        raise ValueError(f"process not found: {process_id}")
    if require_live:
        st = str(p.get("state") or "").lower()
        if st in ("completed", "failed", "killed", "exited", "terminal"):
            raise ValueError(f"process not live: {process_id} state={st}")

    psid = str(p.get("session_id") or "").strip()
    pid_ident = str(p.get("identity") or p.get("identity_id") or "").strip()
    sid = str(session_id or "").strip()
    iid = str(identity_id or "").strip()

    if require_session:
        if not sid:
            # 无 session 的编制进程可用 identity 放行
            if pid_ident.startswith("wf:") or pid_ident.startswith("workforce"):
                if iid and iid != pid_ident:
                    raise ValueError("process identity mismatch (forbidden)")
                return p
            raise ValueError("session_id required for this process")
        if psid:
            if psid != sid:
                raise ValueError("process session mismatch (forbidden)")
        else:
            # 调用方给了 session，但进程无绑定：拒绝（防把后台进程挂到任意会话）
            if not (pid_ident.startswith("wf:") or pid_ident.startswith("workforce")):
                raise ValueError("process has no session binding (forbidden)")
    else:
        # 宽松模式：仅当双方都提供时校验
        if sid and psid and psid != sid:
            raise ValueError("process session mismatch (forbidden)")
        if iid and pid_ident and pid_ident != iid:
            raise ValueError("process identity mismatch (forbidden)")
    return p


async def assert_user_owns_process(
    kernel: Any,
    process_id: str,
    user_id: Any,
    *,
    require_live: bool = False,
) -> dict[str, Any]:
    """校验当前用户是否可操作该进程（多用户安全）。

    规则（任一命中即放行）：
    1. single_user_mode=True → 本机单用户放行（仍校验存在）
    2. process.meta.user_id / owner_id / owner_user_id == user
    3. process.session_id 属于该用户（查 sessions 表）
    4. identity 为 wf:{uuid} 且编制档案 owner/user 匹配
    5. 进程无任何归属字段且 single_user 已关 → 拒绝（fail-closed）

    Raises:
        ValueError: 不存在
        PermissionError: 无权
    """
    p = get_process_dict(kernel, process_id)
    if not p:
        raise ValueError(f"process not found: {process_id}")
    if require_live:
        st = str(p.get("state") or "").lower()
        if st in ("completed", "failed", "killed", "exited", "terminal", "done"):
            raise ValueError(f"process not live: {process_id} state={st}")

    uid = str(user_id or "").strip()
    if not uid:
        raise PermissionError("user required")

    if _single_user_mode():
        return p

    meta = p.get("meta") if isinstance(p.get("meta"), dict) else {}
    for key in ("user_id", "owner_id", "owner_user_id", "ceo_user_id"):
        mid = str(meta.get(key) or "").strip()
        if mid and mid == uid:
            return p

    # session 归属
    psid = str(p.get("session_id") or meta.get("session_id") or "").strip()
    if psid:
        try:
            import uuid as _uuid

            from backend.database import AsyncSessionLocal
            from backend.models.session import Session as SessionModel
            from sqlalchemy import select

            sid = _uuid.UUID(psid)
            async with AsyncSessionLocal() as db:
                row = (
                    await db.execute(select(SessionModel).where(SessionModel.id == sid))
                ).scalar_one_or_none()
            if row is not None:
                owner = str(getattr(row, "user_id", "") or "")
                if owner and owner == uid:
                    return p
                if owner and owner != uid:
                    raise PermissionError("process session not owned by user")
        except PermissionError:
            raise
        except Exception as e:
            logger.debug("session ownership lookup skip: %s", e)

    # 编制工单：identity wf:{id}
    ident = str(p.get("identity") or p.get("identity_id") or "").strip()
    if ident.startswith("wf:"):
        iid = ident[3:].strip()
        try:
            import uuid as _uuid

            from backend.database import AsyncSessionLocal
            from backend.models.agent_identity import AgentIdentity
            from sqlalchemy import select

            aid = _uuid.UUID(iid)
            async with AsyncSessionLocal() as db:
                row = (
                    await db.execute(select(AgentIdentity).where(AgentIdentity.id == aid))
                ).scalar_one_or_none()
            if row is not None:
                for attr in ("owner_user_id", "user_id", "created_by"):
                    ow = str(getattr(row, attr, "") or "")
                    if ow and ow == uid:
                        return p
                # 编制默认归本机管理员：无 owner 字段时单用户已处理；多用户无 owner → 拒绝
                if not any(
                    str(getattr(row, a, "") or "")
                    for a in ("owner_user_id", "user_id", "created_by")
                ):
                    raise PermissionError("workforce process has no owner binding")
        except PermissionError:
            raise
        except Exception as e:
            logger.debug("identity ownership lookup skip: %s", e)

    # 无任何可证明归属 → fail-closed
    raise PermissionError(f"process not owned by user: {process_id[:12]}")


async def assert_user_owns_identity(
    identity_id: Any,
    user_id: Any,
) -> Any:
    """校验当前用户是否可操作该编制身份（多用户安全）。

    与 assert_user_owns_process 对称：
    1. single_user_mode=True → 存在即放行
    2. identity.user_id / owner_user_id / created_by == user
    3. 多用户且无归属字段 → fail-closed
    4. 归属他人 → PermissionError

    identity_id 可带 ``wf:`` 前缀（会剥掉）。

    Returns:
        AgentIdentity ORM 行（存在时）

    Raises:
        ValueError: 不存在
        PermissionError: 无权
    """
    uid = str(user_id or "").strip()
    if not uid:
        raise PermissionError("user required")

    iid_raw = str(identity_id or "").strip()
    if iid_raw.startswith("wf:"):
        iid_raw = iid_raw[3:].strip()
    if not iid_raw:
        raise ValueError("identity_id required")

    try:
        import uuid as _uuid

        from backend.database import AsyncSessionLocal
        from backend.models.agent_identity import AgentIdentity
        from sqlalchemy import select

        aid = _uuid.UUID(iid_raw)
    except Exception as e:
        raise ValueError(f"identity not found: {iid_raw}") from e

    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(select(AgentIdentity).where(AgentIdentity.id == aid))
        ).scalar_one_or_none()
    if row is None:
        raise ValueError(f"identity not found: {iid_raw}")

    if _single_user_mode():
        return row

    for attr in ("owner_user_id", "user_id", "created_by"):
        ow = str(getattr(row, attr, "") or "")
        if ow and ow == uid:
            return row

    has_owner = any(
        str(getattr(row, a, "") or "")
        for a in ("owner_user_id", "user_id", "created_by")
    )
    if not has_owner:
        raise PermissionError("identity has no owner binding")
    raise PermissionError(f"identity not owned by user: {iid_raw[:12]}")


def ownership_http_exc(err: Exception) -> tuple[int, str]:
    """Map ownership errors to HTTP status."""
    if isinstance(err, PermissionError):
        return 403, str(err)
    if isinstance(err, ValueError):
        msg = str(err)
        if "not found" in msg.lower():
            return 404, msg
        return 400, msg
    return 500, str(err)
