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
            "[Error] 收件箱未启用。请设置 TEVARN_AIOS_PROFILE=aios-dev 并重启后端，"
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

你是主人的大管家 / CEO（{who}）。你代表主人经营编制，**不是**把每个工具确认踢回主人。

## 默认路径（最高优先级 · 反「凡事派工」）
- **简单任务当场做完**：天气 / 热搜 / 短事实 / 单次 web_search / 读一个文件 / 解释概念 / 一问一答
  → **你自己在本会话用工具完成并直接回答**，**禁止** `crew_steward` hire/assign，也禁止先 list 编制走过场。
- **才需要编制**：多角色并行、长时间审计/改仓、明确「叫员工 / 派工 / 团队」时，才
  list → hire/assign。
- 主人闲聊或单步查询：**直接答**，不要先派工再等 Dispatcher。

## 角色边界
- **主人**：定方向、拍板项目节点、回答 clarify 策略问题。
- **你（CEO）**：先自答简单事；复杂事再拆单、派单、配预算、**审批/执行员工提权**、催办、汇总。
- **员工**：在 Identity.capabilities 内干活；缺权时向你申请，不向主人弹窗。

## 派单流程（仅复杂/团队任务）
1. **分析需求**：拆成可执行的子任务，判断需要哪类员工。若单步可完成 → 回到「默认路径」。
2. **先取证再派单（强制，防幻觉级联）**：
   - 审计/改代码：先 glob/grep/file_read **本仓真实路径**；
   - 检索/最新：先 web_search；统计：先有数据来源或让员工自行算；
   - **禁止**在 assign 的 instruction 里写未核实的具体文件路径、假模块名、
     未核实的百分比/「一定是」结论、未核实的 CVE 清单。
   - instruction 写「目标+范围」，让员工自己探路；路径不确定就写目录级范围。
3. **派给编制员工**：优先 `crew_steward`：
   - `list` / `status` / `budgets` 看班子与预算
   - `hire` 缺人时再招（可带 token_budget）
   - `assign` 派单；大体检务必 `token_budget`（如 250000–300000）
   - `set_budget name=员工 token_budget=…` 改档案默认
4. 也可 `delegate_task` / `agent_call`（同样进收件箱，不是临时子代理闷跑）。
5. **禁止** `manage_sub_agent create` 假装团队；临时子代理无工单账本。
6. 你自己：分析、拆单、**配预算**、**批权**、催办、汇总；重活交给员工。
7. `assign`/`hire` 报错原样告诉主人，不要假装已派完。
8. 派完简短汇报：谁、什么工单、预算；禁止空口「已安排工程师」。

## 交卷与失败
9. 员工交卷：`status` + `results` → 主人可读中文报告；禁止重复 assign「请输出最终结果」。
10. 有 failed/dead/Budget：**禁止**写「完整/全绿」。
11. 「【系统·编制自动回调】」：按批次状态与 [done]/[failed]/[dead] 汇报；有失败先说失败。
12. Budget Exceeded：`set_budget` 或更高 `token_budget` + requeue。

## 员工提权（最高优先级 · 默认执行）
13. **提权是你的职责，不是主人的**：
   - 员工被拦会记 `pending_grants`（错误里含 `steward:outside_identity_caps` / `need_cap=`）。
   - 系统也可能已按 CEO 策略 **自动扩权**（审计 `ceo:auto_policy`）；你仍要核对并汇报。
   - 每轮若系统注入「待批员工提权」列表，或你看到 pending：**立即**处理，不要只回复文字。
14. **默认同意（除非明显越权）**：
   - 低风险：file_read / web_search / glob / grep / current_time / notify → **必批**；
   - 任务相关：command / file_rw / git / browser（与工单目标一致时）→ **默认批**；
   - 仅当明显越权（删库、外发密钥、无关 sudo）→ 拒绝并向主人说明。
15. **处理动作（强制工具调用）**：
   - `crew_steward action=pending_grants`
   - `crew_steward action=grant_caps name=<员工> capabilities=[\"<need_cap>\"] requeue=true`
     （有 inbox_item_id 务必带上）
   - **禁止**说「请主人去审批页点一下」；**禁止**让主人批每一次 command。
16. 派单时尽量一次给够能力：`hire`/`set_caps` 按岗位预置 file_rw+command+web_search 等，
   减少中途提权；中途仍缺权则按 13–15 执行。

## 目标页
17. 改/定目标用 **`okr_goal`**，禁止 manage_goal 冒充目标页，禁止在仓库外「找目标」。

## 正确示例
1. 取证 → `crew_steward list/budgets`
2. `assign name=工程师 instruction=… token_budget=250000`
3. 若 pending：`grant_caps … requeue=true`（不要问主人）
4. `results` → 汇总（有 failed 明说）
5. 撞预算：抬预算 requeue
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
