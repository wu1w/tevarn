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

正确示例：
1. 先 grep/file_read 确认真实模块名（不要编 orchestrator.py）
2. `crew_steward action=list` / `budgets`
3. `assign name=工程师 instruction=在 backend/kernel 下审计… token_budget=250000`
4. 完成后 `action=results` → 向主人写汇总（有 failed 就明说）
5. 撞预算：`set_budget` 或 更高 `token_budget` + requeue
6. 权限不足：`pending_grants` → `grant_caps … requeue=true`
"""


# 管家会话强制挂上的工具（在 profile 白名单之上）
STEWARD_FORCE_TOOLS: tuple[str, ...] = (
    "crew_steward",
    "delegate_task",
    "agent_call",
    "clarify",
)
