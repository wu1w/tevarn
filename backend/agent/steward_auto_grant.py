"""CEO 策略：员工提权默认由编制侧自动执行（不弹主人）。

产品纪律：
- 主人不批每一次工具；员工扩权是 **CEO/管家** 的职责。
- LLM 管家未必每轮都读到 pending_grants，故对 workforce 拦截提供
  **确定性 auto_grant**（审计 by=ceo:auto_policy），并热更新 Identity + 在跑进程。
- 高危能力受 agent_steward_auto_grant_high_risk 开关约束。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 默认同意（读/检索类）
_LOW_RISK_CAPS = frozenset({
    "file_read",
    "web_search",
    "web_extract",
    "search",
    "glob",
    "grep",
    "list_dir",
    "memory_read",
    "wiki_read",
    "knowledge_query",
    "rag_query",
    "current_time",
    "clarify",
    "notify",
    "db_read",
    "calendar",
})

# 任务连续性常需（单用户 AIOS 默认可自动）
_JOB_HIGH_CAPS = frozenset({
    "command",
    "terminal",
    "shell",
    "file_rw",
    "file_write",
    "file_edit",
    "git",
    "browser",
    "computer",
    "python",
    "process",
})

# 永不自动（破坏性 / 外发）
_NEVER_AUTO = frozenset({
    "sudo",
    "delete",
    "rm",
    "egress",
    "mcp",
    "subagent",
    "spawn",
    "*",
})


def auto_grant_enabled() -> bool:
    try:
        from backend.core.config import settings

        return bool(getattr(settings, "agent_steward_auto_grant", True))
    except Exception:
        return True


def high_risk_auto_enabled() -> bool:
    try:
        from backend.core.config import settings

        return bool(getattr(settings, "agent_steward_auto_grant_high_risk", True))
    except Exception:
        return True


def cap_eligible_for_auto(cap: str) -> bool:
    c = (cap or "").strip().lower()
    high = high_risk_auto_enabled()
    try:
        from backend.kernel_rust.client import is_rust_host_available

        if is_rust_host_available():
            from backend.kernel import get_kernel

            k = get_kernel()
            if hasattr(k, "_call"):
                r = k._call(
                    "approval_cap_eligible",
                    {"cap": c, "high_risk_auto": high},
                )
                if isinstance(r, dict) and "eligible" in r:
                    return bool(r["eligible"])
    except Exception:
        pass
    if not c or c in _NEVER_AUTO:
        return False
    if any(n in c for n in _NEVER_AUTO):
        return False
    if c in _LOW_RISK_CAPS or any(c.startswith(x) for x in ("read", "search", "list")):
        return True
    if c in _JOB_HIGH_CAPS:
        return high
    # 未知槽：仅低风险开关下不自动；高风险开时允许常见工程槽
    if high and c in {
        "use_tool_pack",
        "memory",
        "http",
        "network",
    }:
        return False  # network/http 仍人工/管家会话
    return False


async def apply_ceo_auto_grant(
    *,
    identity_id: str,
    identity_name: str = "",
    needed_cap: str,
    tool: str = "",
    inbox_item_id: str | None = None,
    reason: str = "",
) -> dict[str, Any]:
    """Merge needed_cap into Identity, mark pending, hot-refresh processes.

    Returns {ok, caps_added, merged, message}.
    """
    out: dict[str, Any] = {
        "ok": False,
        "caps_added": [],
        "merged": [],
        "message": "",
    }
    if not auto_grant_enabled():
        out["message"] = "auto_grant disabled"
        return out
    cap = (needed_cap or "").strip()
    if not cap_eligible_for_auto(cap):
        out["message"] = f"cap not eligible for auto: {cap}"
        return out
    iid = (identity_id or "").strip()
    if not iid:
        out["message"] = "no identity_id"
        return out

    try:
        import uuid as _u

        from backend.kernel import get_kernel

        reg = getattr(get_kernel(), "identity_registry", None)
        if reg is None:
            out["message"] = "no identity_registry"
            return out
        ident = await reg.get(_u.UUID(str(iid)))
        if ident is None:
            out["message"] = "identity not found"
            return out

        old = list(ident.capabilities or [])
        if cap in old:
            # already has — still mark pending granted
            try:
                from backend.kernel.cap_requests import mark_granted_for_identity

                mark_granted_for_identity(str(iid), caps=[cap], by="ceo:auto_policy")
            except Exception:
                pass
            out["ok"] = True
            out["merged"] = old
            out["message"] = f"already had {cap}"
            return out

        merged = list(old) + [cap]
        by = f"ceo:auto_policy tool={tool or '?'} {(reason or '')[:80]}"
        await reg.set_capabilities(ident.id, merged, by=by[:200])

        try:
            from backend.kernel.cap_requests import mark_granted_for_identity

            mark_granted_for_identity(str(iid), caps=[cap], by="ceo:auto_policy")
        except Exception:
            pass

        # hot-refresh live workforce processes
        hot = 0
        try:
            k = get_kernel()
            for key in (f"wf:{ident.id}", str(ident.id), f"wf:{ident.name}"):
                try:
                    procs = k.live_processes_for_identity(key) or []
                except Exception:
                    procs = []
                for p in procs:
                    try:
                        pc = list(getattr(p, "capabilities", None) or [])
                        nc = sorted(set(pc) | set(merged))
                        p.capabilities = nc  # type: ignore[misc]
                        if hasattr(k, "issue_token"):
                            try:
                                k.issue_token(str(p.id), nc)
                            except Exception:
                                pass
                        hot += 1
                    except Exception:
                        pass
        except Exception as e:
            logger.debug("auto_grant hot refresh: %s", e)

        # domain event for UI / CEO session
        try:
            from backend.kernel.domain_events import publish_sync

            publish_sync(
                "crew.cap_auto_granted",
                {
                    "identity_id": str(ident.id),
                    "identity_name": str(ident.name or identity_name or ""),
                    "cap": cap,
                    "tool": tool,
                    "inbox_item_id": inbox_item_id,
                    "hot_processes": hot,
                },
            )
        except Exception:
            pass

        out["ok"] = True
        out["caps_added"] = [cap]
        out["merged"] = merged
        out["message"] = (
            f"ceo:auto_grant +{cap} employee={ident.name} "
            f"hot_procs={hot} inbox={inbox_item_id or '-'}"
        )
        logger.info(out["message"])
        return out
    except Exception as e:
        logger.warning("apply_ceo_auto_grant failed: %s", e)
        out["message"] = str(e)
        return out


async def try_workforce_missing_cap_auto_grant(
    *,
    tool_name: str,
    identity_id: str | None,
    identity_name: str | None = None,
    inbox_item_id: str | None = None,
    steward_session_id: str | None = None,
    current_caps: list[str] | None = None,
) -> tuple[bool, list[str], str]:
    """Kernel-deny path for wf: record pending + auto-grant when eligible.

    Rust court never reaches Python ``steward_decide_tool``, so this must run
    in ``loop_tools`` *before* the hard identity deny. Returns
    ``(granted, merged_caps, note)``.
    """
    from backend.agent.grant_store import crew_cap_for_tool
    from backend.kernel.cap_requests import record_cap_request

    want = (crew_cap_for_tool(tool_name) or tool_name or "").strip()
    iid = (identity_id or "").strip()
    iname = (identity_name or "").strip()
    caps = list(current_caps or [])
    rec_id = ""
    if iid:
        try:
            rec = record_cap_request(
                identity_id=iid,
                identity_name=iname,
                tool=tool_name,
                needed_cap=want,
                reason="outside_identity_caps",
                inbox_item_id=inbox_item_id,
                steward_session_id=steward_session_id,
            )
            rec_id = str(rec.get("id") or "")
        except Exception as e:
            logger.debug("record_cap_request skip: %s", e)

    if not iid:
        return False, caps, "（无 identity_id，无法自动扩权；请 CEO grant_caps）"

    ag = await apply_ceo_auto_grant(
        identity_id=iid,
        identity_name=iname,
        needed_cap=want,
        tool=tool_name,
        inbox_item_id=inbox_item_id,
        reason="loop_tools outside_identity_caps",
    )
    if ag.get("ok"):
        merged = list(ag.get("merged") or []) or list(
            dict.fromkeys([*caps, want])
        )
        return True, merged, str(ag.get("message") or "auto_grant ok")

    pending = f"申请 {rec_id}" if rec_id else "pending_grants"
    msg = str(ag.get("message") or "auto_grant failed")
    return (
        False,
        caps,
        f"（已记 {pending}：{msg}。请 CEO 用 crew_steward action=grant_caps 处理，"
        "不要让主人逐次点允许。）",
    )


def format_pending_grants_brief(limit: int = 12) -> str:
    """System brief for CEO sessions: list open pending grants."""
    try:
        from backend.kernel.cap_requests import list_pending

        items = list_pending(limit=limit)
    except Exception:
        return ""
    if not items:
        return ""
    lines = [
        "# 待批员工提权（强制处理）",
        "下列请求**不得推给主人**。你是 CEO：立即用工具处理。",
        "对每一条：`crew_steward action=grant_caps name=<员工> "
        "capabilities=[\"<need_cap>\"] requeue=true`（有 inbox_item_id 时带上）。",
        "低风险与任务所需 command/file_rw/git **默认同意**；"
        "仅当指令明显越权（删库/外发密钥）才 deny 并说明。",
        "",
    ]
    for r in items:
        lines.append(
            f"- {r.get('identity_name') or r.get('identity_id')}: "
            f"tool={r.get('tool')} need_cap={r.get('needed_cap')} "
            f"hits={r.get('hits')} id={r.get('id')} "
            f"inbox={r.get('inbox_item_id') or '-'}"
        )
    return "\n".join(lines)
