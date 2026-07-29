"""编制内权限：员工干活不弹主人确认窗。

产品纪律：
- 员工（workforce）工具调用由 **CEO 编制策略** 裁决（Identity.capabilities + 映射），
  禁止刷一堆「允许一次」给人点——安全通知太多等于没有安全。
- 主人只拍板：项目节点 / 方向 / 策略（clarify、plan、goal），不批每一次 glob/grep。
"""

from __future__ import annotations

import logging
from typing import Any, Literal

logger = logging.getLogger(__name__)

StewardDecision = Literal["allow", "deny"]

# 高危工具：员工即使有 command 能力，也只在编制内放行；绝不弹主人
_HIGH_RISK_TOOLS = frozenset({
    "command", "bash", "shell", "python", "process", "remote_exec",
    "desktop_click", "desktop_type", "desktop_open_app",
})


def is_workforce_context(arguments: dict[str, Any] | None = None, *, agent_key: str = "") -> bool:
    """是否员工工单执行上下文（非主人主会话）。"""
    args = arguments or {}
    if args.get("_workforce") is True or str(args.get("_workforce") or "").lower() in {
        "1", "true", "yes",
    }:
        return True
    key = str(args.get("_agent_key") or agent_key or "")
    if key.startswith("wf:"):
        return True
    mode = str(args.get("_chat_mode") or "").lower()
    if mode == "workforce":
        return True
    # 有编制身份 id 且 agent_key 标明员工
    if args.get("_identity_id") and key.startswith(("wf:", "sub:")):
        return True
    return False


def is_human_strategy_surface(tool_name: str) -> bool:
    """真正需要主人决策的入口（策略/节点），不是工具审批。"""
    return tool_name in {
        "clarify",
        "manage_goal",
        "autopilot",
        # plan 相关若有独立工具可加
    }


async def steward_decide_tool(
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    *,
    identity_capabilities: list[str] | None = None,
) -> tuple[StewardDecision, str]:
    """CEO 编制策略裁决员工工具调用。

    Returns:
        (allow|deny, reason)
    """
    from backend.agent.grant_store import tool_matches_crew_caps

    caps = identity_capabilities
    if caps is None:
        caps = _caps_from_args(arguments or {})

    # 无能力档案：保守只放行只读检索类（避免裸奔）
    if not caps:
        from backend.kernel.approval_rules import _LOW_RISK_CAPS

        if tool_name in _LOW_RISK_CAPS or tool_matches_crew_caps(
            tool_name, ["file_rw", "web_search"]
        ):
            # 默认编制兜底：读写文件 + 搜索（与 hire 默认一致）
            return "allow", "steward:default_roster_caps"
        return "deny", "steward:no_capabilities"

    if tool_matches_crew_caps(tool_name, caps):
        return "allow", f"steward:within_identity_caps tool={tool_name}"

    # 不在编制内 → 不弹主人；记一笔待 CEO grant_caps 的请求
    from backend.agent.grant_store import crew_cap_for_tool

    needed = crew_cap_for_tool(tool_name) or tool_name
    args = arguments or {}
    iid = str(args.get("_identity_id") or "").strip()
    iname = str(args.get("_identity_name") or "").strip()
    job_id = str(args.get("_inbox_item_id") or args.get("_job_id") or "").strip() or None
    steward_sid = str(args.get("_steward_session_id") or "").strip() or None
    req_id = ""
    if iid:
        try:
            from backend.kernel.cap_requests import record_cap_request

            rec = record_cap_request(
                identity_id=iid,
                identity_name=iname,
                tool=tool_name,
                needed_cap=needed,
                reason=f"outside caps; have={list(caps)[:16]}",
                inbox_item_id=job_id,
                steward_session_id=steward_sid,
            )
            req_id = str(rec.get("id") or "")
        except Exception as e:
            logger.debug("record_cap_request skip: %s", e)

    hint = (
        f"steward:outside_identity_caps tool={tool_name} need_cap={needed} "
        f"have={list(caps)[:12]}。"
        f"请 CEO 用 crew_steward action=grant_caps name=<员工> capabilities=[\"{needed}\"] "
        f"扩权后可 reassign 重派；不要让主人点弹窗批每一次工具。"
    )
    if req_id:
        hint += f" pending_grant={req_id}"
    return "deny", hint


def _caps_from_args(arguments: dict[str, Any]) -> list[str] | None:
    raw = arguments.get("_identity_capabilities")
    if isinstance(raw, (list, tuple, set)):
        return [str(x) for x in raw if str(x).strip()]
    return None


async def load_identity_capabilities(identity_id: str | None) -> list[str] | None:
    if not identity_id:
        return None
    try:
        from backend.kernel import get_kernel
        import uuid as _u

        reg = getattr(get_kernel(), "identity_registry", None)
        if reg is None:
            return None
        ident = await reg.get(_u.UUID(str(identity_id)))
        if ident is None:
            return None
        return list(ident.capabilities or [])
    except Exception as e:
        logger.debug("load identity caps skip: %s", e)
        return None
