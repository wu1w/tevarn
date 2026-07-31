"""进程访问控制：collab / sample-rss 等 API 防止任意 process_id 越权。"""
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
            }
        if hasattr(kernel, "_call"):
            r = kernel._call("get_process", {"process_id": pid}) or {}
            if isinstance(r, dict) and (r.get("id") or r.get("process_id")):
                return r
    except Exception as e:
        logger.debug("get_process_dict: %s", e)
    return None


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
