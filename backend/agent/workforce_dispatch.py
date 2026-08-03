"""编制派活：把「交给某员工」落到 Inbox，而不是起 SubAgent 闷跑。

CEO / 管家应：分析需求 → crew_steward.assign / 本模块 → 员工 Identity 收件箱。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def find_identity_by_name_or_id(name_or_id: str) -> Any | None:
    name_or_id = (name_or_id or "").strip()
    if not name_or_id:
        return None
    try:
        from backend.kernel import get_kernel

        reg = getattr(get_kernel(), "identity_registry", None)
        if reg is None:
            return None
        # by id
        try:
            import uuid as _u

            ident = await reg.get(_u.UUID(name_or_id))
            if ident is not None:
                return ident
        except Exception:
            pass
        items = await reg.list(status="active")
        low = name_or_id.lower()
        for i in items:
            if str(i.name) == name_or_id or str(i.name).lower() == low:
                return i
            if str(i.id) == name_or_id:
                return i
        return None
    except Exception as e:
        logger.debug("find_identity failed: %s", e)
        return None


async def assign_to_employee(
    name_or_id: str,
    instruction: str,
    *,
    priority: int = 5,
    source: str = "api",
    via: str = "workforce_dispatch",
    steward_session_id: str | None = None,
    project_title: str | None = None,
) -> str:
    """向编制员工派工单。成功返回人话；失败返回 [Error]..."""
    instruction = (instruction or "").strip()
    if not instruction:
        return "[Error] 工单指令不能为空"
    ident = await find_identity_by_name_or_id(name_or_id)
    if ident is None:
        return (
            f"[Error] 编制中找不到员工 «{name_or_id}》。"
            f"请先 crew_steward action=list 或 hire 入编，"
            f"不要用 manage_sub_agent/agent_call 假装招人。"
        )
    if getattr(ident, "status", None) != "active":
        return f"[Error] 员工「{ident.name}」状态为 {ident.status}，无法接单"

    from backend.kernel.workforce import get_workforce_inbox

    inbox = get_workforce_inbox()
    if inbox is None:
        return (
            "[Error] 收件箱未启用。请设置 TAKTON_AIOS_PROFILE=aios-dev 并重启后端，"
            "确保 agent_dispatcher_enabled / persistence 打开。"
        )
    payload: dict[str, Any] = {"via": via, "assigned_name": ident.name}
    sid = (steward_session_id or "").strip()
    if sid:
        payload["steward_session_id"] = sid
    pt = (project_title or "").strip()
    if pt:
        payload["project_title"] = pt
    item = await inbox.enqueue(
        ident.id,
        instruction,
        source=source if source in ("cron", "webhook", "api", "manual") else "api",
        priority=int(priority or 0),
        payload=payload,
    )
    if item is None:
        return f"[Error] 工单被拒收（「{ident.name}」可能停用或队列溢出）"
    return (
        f"✅ 已派给编制员工「{ident.name}」工单 {item.id} status={item.status}。"
        f"Dispatcher 将按权限/预算自动领取——这不是临时子代理闷跑，"
        f"会进入员工工作记录/日报。"
    )


# 管家/CEO 名称启发式（联系会话注入编排纪律）
_STEWARD_NAME_HINTS = (
    "ceo",
    "cto",
    "管家",
    "大管家",
    "steward",
    "chief",
    "老板",
    "总裁",
    "小白",  # 默认 CEO 样例名
)


def is_steward_contact(name_or_role: str | None) -> bool:
    """判断联系对象是否应按大管家编排（分析→派员工）。"""
    s = (name_or_role or "").strip().lower()
    if not s:
        return False
    if any(h in s for h in _STEWARD_NAME_HINTS):
        return True
    # 纯 role 标记
    if s in {"ceo", "cto", "steward", "管家"}:
        return True
    return False


def steward_orchestration_prompt(*, contact_name: str = "") -> str:
    """注入 CEO/管家会话的编排纪律。"""
    who = contact_name.strip() or "你"
    return f"""# 大管家编排纪律（强制）

你是主人的大管家（{who}）。主人派活给你时，你必须：

1. **分析需求**：拆成可执行的子任务，判断需要哪类员工。
2. **先取证再派单（强制，防幻觉级联）**：
   - 审计/改代码：先 glob/grep/file_read **本仓真实路径**；
   - 检索/最新：先 web_search；统计：先有数据来源或让员工自行算；
   - **禁止**在 assign 的 instruction 里写未核实的具体文件路径、假模块名、
     未核实的百分比/「一定是」结论、未核实的 CVE 清单。
   - instruction 写「目标+范围」，让员工自己探路；路径不确定就写目录级范围。
3. **派给编制员工**：优先用工具 `crew_steward`：
   - `action=list` / `status` / `budgets` 看班子与预算
   - `action=hire` 缺人时再招（可带 token_budget=档案默认）
   - `action=assign` 派单；**大体检/全仓扫描务必带 token_budget**（如 250000–300000）
   - `action=set_budget name=员工 token_budget=200000` 改档案默认
   - assign 若被系统拒，按错误改写后重派，不要 force 糊弄。
4. 也可 `delegate_task action=run agent_name=员工名 goal=...` 或 `agent_call`——
   它们同样写入收件箱，**不是**起临时子进程闷跑。
5. **禁止**用 `manage_sub_agent create` 假装「并行团队」去闷头干活；
   临时子代理没有工单账本，credit/日报看不到。
6. 你自己只做：分析、拆单、**配预算**、催办、汇总；重活交给员工。
7. 若 `assign`/`hire` 报错，把错误原样告诉主人，不要假装已经派完。
8. 派完后简短汇报：派给了谁、各人什么工单、**本单预算多少**；不要空口说「已安排工程师」。
9. **员工交卷后的汇报（强制）**：
   - 用 `crew_steward action=status` 看进度计数；
   - 用 `crew_steward action=results`（可加 project_title / name）拉工单正文；
   - 把结果汇总成主人可读的中文报告（结论 / 分员工要点 / 风险与下一步）；
   - **禁止**在已有完整 result 时再 assign「请输出最终结果」重复派单；
   - 有 failed/dead/Budget 时**禁止**写「完整/已全部完成/全绿」。
10. 若系统以「【系统·编制自动回调】」开头推送清单：
   - 按其中的 **批次状态** 与 `[done]/[failed]/[dead]` 标签汇报；
   - 有失败必须先说失败人数与主因，再写成功要点；不要再 hire。
11. **大体检/多模块任务**：优先拆多张 assign；若必须一张大单，**必须** `token_budget≥250000`（0=本单不限，慎用）。
12. **Budget Exceeded 处理（强制）**：
   - `budgets` 看档案；`set_budget` 抬默认；再 `assign`/`requeue` 时带更高 `token_budget`；
   - 例：`assign name=backend-engineer instruction=… token_budget=300000`；
   - 或 `set_budget name=… token_budget=250000 requeue=true inbox_item_id=…`。
13. **员工因权限干不完（强制）**：
   - 工单 result/error 出现 `steward:outside_identity_caps` 或 `need_cap=` 时，
     先 `crew_steward action=pending_grants` 看待批；
   - 再 `action=grant_caps name=员工 capabilities=[\"command\"]`（或 tools=[\"command\"]）扩权；
   - 需要接着干：`grant_caps` 时加 `requeue=true`（可带 inbox_item_id）重新入队；
   - **禁止**让主人点一堆危险确认弹窗；编制改权是你的职责。
   - 可选能力：file_rw, command, web_search, git, browser, calendar, db_read, notify。
14. **经营目标 / O-KR（目标页）**：
   - 主人说「改目标 / 定目标 / 目标进度」时用工具 **`okr_goal`**（list/get/create/update）；
   - **禁止**用 manage_goal（那是会话 Todo 卡，不是目标页）；
   - **禁止**用 grep/file_read 在前端源码或仓库外路径「找目标」；
   - 改标题：`okr_goal action=update goal_id=… title=新标题`。

正确示例：
1. 先 grep/file_read 确认真实模块名（不要编 orchestrator.py）
2. `crew_steward action=list` / `budgets`
3. `assign name=工程师 instruction=在 backend/kernel 下审计… token_budget=250000`
4. 完成后 `action=results` → 向主人写汇总（有 failed 就明说）
5. 撞预算：`set_budget` 或 更高 `token_budget` + requeue
6. 权限不足：`pending_grants` → `grant_caps … requeue=true`
7. 改目标：`okr_goal action=list` → `okr_goal action=update goal_id=… title=…`
"""


# 管家会话强制挂上的工具（在 profile 白名单之上）
STEWARD_FORCE_TOOLS: tuple[str, ...] = (
    "crew_steward",
    "delegate_task",
    "agent_call",
    "clarify",
    "okr_goal",
    "manage_goal",
    "autopilot",
)

# CEO/管家 kernel 令牌：全开（*），不再被 coding profile 收成「没有 okr_goal」
# 审批员工提权是 CEO 的职责；CEO 自己不应再被主人逐工具点通过。
STEWARD_KERNEL_CAPABILITIES: tuple[str, ...] = ("*",)

# 令牌失败时的兜底能力（与 coding_profile engineering + goal 对齐）
_STEWARD_FALLBACK_CAPS: tuple[str, ...] = (
    "file_read",
    "file_write",
    "file_edit",
    "file_rw",
    "terminal",
    "command",
    "crew_steward",
    "clarify",
    "use_tool_pack",
    "current_time",
    "okr_goal",
    "manage_goal",
    "autopilot",
    "delegate_task",
    "agent_call",
    "web_search",
    "git",
    "browser",
    "computer",
    "memory",
    "notify",
)


def _token_cap_list(tok: Any) -> list[str]:
    if tok is None:
        return []
    if isinstance(tok, dict):
        raw = tok.get("capabilities") or []
        return [str(x) for x in raw]
    caps = getattr(tok, "capabilities", None)
    if caps is None:
        return []
    return [str(x) for x in caps]


def _proc_has_star_or_goals(proc: Any) -> bool:
    if proc is None:
        return False
    caps = list(getattr(proc, "capabilities", None) or [])
    tok = getattr(proc, "token", None)
    tok_caps = _token_cap_list(tok)
    bag = set(str(c) for c in caps) | set(tok_caps)
    if "*" in bag:
        return True
    if "okr_goal" in bag and "manage_goal" in bag:
        return True
    if tok is not None and callable(getattr(tok, "allows", None)):
        try:
            if tok.allows("okr_goal") and tok.allows("manage_goal"):
                return True
        except Exception:
            pass
    return False


def ensure_steward_kernel_full_open(kernel: Any, process_id: str) -> bool:
    """Expand CEO/管家 process caps + token (sync paths).

    Why UI「通过」still failed:
    - PermissionGate / escalation UI ≠ kernel process.token scope
    - After coding_profile, process.capabilities is narrow; issue_token(['*'])
      raises 超出进程能力集 and was swallowed
    - apply_intent(['*']) also fails silently: intent synthesizer **drops**
      unknown caps including ``*`` (not in DEFAULT_GRANTABLE/RISKY)

    Sync path: apply_intent with an explicit allow_risky cap list that includes
    okr_goal/manage_goal (must be grantable or we rely on async escalate).
    Prefer async helper for true ``*`` via escalate+approve.
    """
    pid = str(process_id or "").strip()
    if not pid or kernel is None:
        return False

    try:
        proc0 = kernel.get_process(pid) if hasattr(kernel, "get_process") else None
        caps0 = list(getattr(proc0, "capabilities", None) or []) if proc0 else []
        if "*" in caps0 or (
            "okr_goal" in caps0 and "manage_goal" in caps0
        ):
            # Ensure token tracks process
            try:
                if hasattr(kernel, "issue_token"):
                    kernel.issue_token(
                        pid,
                        capabilities=["*"] if "*" in caps0 else caps0,
                    )
            except Exception:
                pass
            return True
    except Exception:
        pass

    # apply_intent: request concrete caps (NOT bare * — synthesizer drops *)
    # Note: okr_goal/manage_goal are currently "unknown" to intent whitelist and
    # get dropped unless host is updated; escalate path (async) still adds them.
    try:
        if hasattr(kernel, "apply_intent"):
            # Use a wide explicit list; allow_risky unlocks terminal/file_write/…
            want = list(
                dict.fromkeys(
                    [
                        # default grantable + risky + goals + orchestration
                        "file_read",
                        "grep",
                        "glob",
                        "web_search",
                        "session_search",
                        "memory",
                        "crew_steward",
                        "clarify",
                        "use_tool_pack",
                        "current_time",
                        "terminal",
                        "file_write",
                        "file_edit",
                        "file_rw",
                        "command",
                        "browser",
                        "computer",
                        "delegate_task",
                        "okr_goal",
                        "manage_goal",
                        "autopilot",
                        "agent_call",
                        "git",
                        "notify",
                    ]
                )
            )
            kernel.apply_intent(
                pid,
                {
                    "goal": "steward/CEO full-open (owner agent)",
                    "capabilities": want,
                    "constraints": {"allow_risky": True, "steward": True},
                },
            )
            proc = kernel.get_process(pid) if hasattr(kernel, "get_process") else None
            if _proc_has_star_or_goals(proc):
                logger.info("steward full-open via apply_intent process=%s", pid[:8])
                return True
            # Partial: re-issue whatever process has (engineering may already include goals)
            pcaps = list(getattr(proc, "capabilities", None) or []) if proc else []
            if pcaps and hasattr(kernel, "issue_token"):
                try:
                    kernel.issue_token(pid, capabilities=pcaps)
                except Exception:
                    pass
    except Exception as e:
        logger.debug("steward apply_intent full-open skip: %s", e)

    try:
        proc = kernel.get_process(pid) if hasattr(kernel, "get_process") else None
        pcaps = list(getattr(proc, "capabilities", None) or []) if proc else []
        if hasattr(kernel, "issue_token") and pcaps:
            if "*" in pcaps or ("okr_goal" in pcaps and "manage_goal" in pcaps):
                kernel.issue_token(
                    pid, capabilities=["*"] if "*" in pcaps else pcaps
                )
                logger.info(
                    "steward re-issue process caps process=%s n=%s",
                    pid[:8],
                    len(pcaps),
                )
                return True
    except Exception as e:
        logger.warning("steward full-open issue_token failed process=%s: %s", pid[:8], e)
    return False


async def ensure_steward_kernel_full_open_async(kernel: Any, process_id: str) -> bool:
    """Async: escalate+approve '*' then concrete goal caps (expands process caps).

    ``approve_escalation`` merges requested caps into process.capabilities and
    re-issues the token — this is the reliable path for ``*`` and for caps that
    intent synthesizer would drop as unknown (okr_goal/manage_goal).
    """
    import inspect

    if ensure_steward_kernel_full_open(kernel, process_id):
        return True

    pid = str(process_id or "").strip()
    if not pid or kernel is None:
        return False

    async def _maybe_await(x: Any) -> Any:
        if inspect.isawaitable(x):
            return await x
        return x

    try:
        if not (
            hasattr(kernel, "request_escalation")
            and hasattr(kernel, "approve_escalation")
        ):
            return False

        # Prefer * first (court/token.allows honors *); then explicit list
        batches = (
            (["*"], "steward_full_open"),
            (list(_STEWARD_FALLBACK_CAPS), "steward_fallback_caps"),
        )
        for batch, tag in batches:
            try:
                req = await _maybe_await(
                    kernel.request_escalation(
                        pid,
                        batch,
                        reason=f"steward/CEO default authority ({tag})",
                    )
                )
            except Exception as e:
                logger.debug("steward escalate %s: %s", tag, e)
                continue
            rid = getattr(req, "id", None) if req is not None else None
            status = getattr(req, "status", "") if req is not None else ""
            if rid and status == "pending":
                await _maybe_await(
                    kernel.approve_escalation(rid, by=f"system:{tag}")
                )
            proc = (
                kernel.get_process(pid) if hasattr(kernel, "get_process") else None
            )
            if _proc_has_star_or_goals(proc):
                try:
                    pcaps = list(getattr(proc, "capabilities", None) or [])
                    if hasattr(kernel, "issue_token") and pcaps:
                        kernel.issue_token(
                            pid,
                            capabilities=["*"] if "*" in pcaps else pcaps,
                        )
                except Exception:
                    pass
                logger.info(
                    "steward full-open async escalate process=%s tag=%s caps=%s",
                    pid[:8],
                    tag,
                    list(getattr(proc, "capabilities", None) or [])[:8],
                )
                return True
    except Exception as e:
        logger.warning("steward full-open async failed process=%s: %s", pid[:8], e)

    return ensure_steward_kernel_full_open(kernel, process_id)
