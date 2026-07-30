"""CEO 大管家工具：对话招人 / 派活 / 班子状态（编制真源 Identity + Inbox）。

用户应通过和 CEO 对话完成组织搭建与派活，而不是在页面上来回点。
"""

from __future__ import annotations

import logging
from typing import Any

from backend.tools.base import BaseTool, ToolRiskLevel, ToolSource

logger = logging.getLogger(__name__)


# 标准编制能力（与 CAP_POOL / protocol 对齐）
_KNOWN_CAPS = frozenset(
    {
        "file_rw",
        "command",
        "web_search",
        "git",
        "browser",
        "calendar",
        "db_read",
        "notify",
        "desktop",
    }
)


class CrewStewardTool(BaseTool):
    """crew_steward — hire / list / assign / status / grant_caps …"""

    def __init__(self) -> None:
        super().__init__(
            name="crew_steward",
            description=(
                "CEO 大管家工具（编制真源）。action：\n"
                "list / hire / assign / status / results / open_project /\n"
                "grant_caps / revoke_caps / set_caps / pending_grants /\n"
                "set_budget（改员工档案默认预算）/ budgets（台账+在跑进程用量）/\n"
                "top_up（运行中给在跑进程追加 token，动态加预算，不杀进程）。\n"
                "预算：assign 可带 token_budget=本单硬顶（优先于自动抬升）；"
                "0=本单不限；大体检建议 200000–300000 或拆单。"
                "撞 Budget Exceeded 或用量>70%：优先 top_up name=… amount=300000；"
                "并 set_budget 抬档案默认，避免下一单再顶死。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "list",
                            "hire",
                            "assign",
                            "status",
                            "results",
                            "open_project",
                            "grant_caps",
                            "revoke_caps",
                            "set_caps",
                            "pending_grants",
                            "set_budget",
                            "budgets",
                            "top_up",
                        ],
                        "description": (
                            "list|hire|assign|status|results|open_project|"
                            "grant_caps|revoke_caps|set_caps|pending_grants|"
                            "set_budget|budgets|top_up"
                        ),
                    },
                    "name": {
                        "type": "string",
                        "description": "hire/assign/grant：员工姓名",
                    },
                    "names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "open_project：成员姓名列表",
                    },
                    "role": {"type": "string", "description": "hire：角色，如 CTO / 研发"},
                    "persona": {"type": "string", "description": "hire：人格"},
                    "duty": {"type": "string", "description": "hire：职责"},
                    "capabilities": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "hire/grant/set：能力 id，如 file_rw,command,web_search,git,browser"
                        ),
                    },
                    "tools": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "grant_caps：工具名列表，自动映射到能力槽（如 command, file_write）",
                    },
                    "token_budget": {
                        "type": "integer",
                        "description": (
                            "hire：档案默认预算（默认 100000）；"
                            "assign：本单预算硬顶（优先于自动抬升，0=本单不限）；"
                            "set_budget：写入员工档案默认预算（0=档案不限）；"
                            "top_up：本轮追加额度（同 amount）"
                        ),
                    },
                    "amount": {
                        "type": "integer",
                        "description": "top_up：追加 token 数量（正整数，建议 200000–500000）",
                    },
                    "process_id": {
                        "type": "string",
                        "description": "top_up：可选，指定 kernel 进程 id；默认抬该员工全部在跑进程",
                    },
                    "instruction": {
                        "type": "string",
                        "description": "assign：工单指令；grant 后 requeue 时可选覆盖",
                    },
                    "priority": {
                        "type": "integer",
                        "description": "assign：优先级，默认 0",
                    },
                    "identity_id": {
                        "type": "string",
                        "description": "员工 id（与 name 二选一）",
                    },
                    "title": {
                        "type": "string",
                        "description": "open_project：项目组名称，如「内核巡检」",
                    },
                    "project_title": {
                        "type": "string",
                        "description": "assign 时可选：自动挂到/创建同名项目组",
                    },
                    "inbox_item_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "open_project：已派工单 id 列表",
                    },
                    "inbox_item_id": {
                        "type": "string",
                        "description": "grant_caps：关联工单；requeue 时优先用此 id 重派",
                    },
                    "requeue": {
                        "type": "boolean",
                        "description": "grant_caps 后是否把原工单/新指令重新入队（默认 false）",
                    },
                    "force": {
                        "type": "boolean",
                        "description": "assign：跳过 instruction 落地校验（不推荐；仅主人明确要求）",
                    },
                    "reason": {
                        "type": "string",
                        "description": "grant/revoke/top_up/set_budget：原因备注（写入审计）",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "results/pending_grants：最多条数",
                    },
                    "include_result": {
                        "type": "boolean",
                        "description": "status：是否附带最近 done 摘要（默认 true）",
                    },
                },
                "required": ["action"],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.MEDIUM,
        )

    async def execute(self, **kwargs: Any) -> Any:
        action = str(kwargs.get("action") or "").strip().lower()
        allowed = {
            "list",
            "hire",
            "assign",
            "status",
            "results",
            "open_project",
            "grant_caps",
            "revoke_caps",
            "set_caps",
            "pending_grants",
            "set_budget",
            "budgets",
            "top_up",
        }
        if action not in allowed:
            return (
                f"[Error] 非法 action={action!r}。"
                "只允许 list|hire|assign|status|results|open_project|"
                "grant_caps|revoke_caps|set_caps|pending_grants|"
                "set_budget|budgets|top_up。"
            )
        try:
            if action == "list":
                return await self._list()
            if action == "hire":
                return await self._hire(kwargs)
            if action == "assign":
                return await self._assign(kwargs)
            if action == "open_project":
                return await self._open_project(kwargs)
            if action == "results":
                return await self._results(kwargs)
            if action == "grant_caps":
                return await self._grant_caps(kwargs)
            if action == "revoke_caps":
                return await self._revoke_caps(kwargs)
            if action == "set_caps":
                return await self._set_caps(kwargs)
            if action == "pending_grants":
                return await self._pending_grants(kwargs)
            if action == "set_budget":
                return await self._set_budget(kwargs)
            if action == "budgets":
                return await self._budgets(kwargs)
            if action == "top_up":
                return await self._top_up(kwargs)
            return await self._status(kwargs)
        except Exception as e:
            logger.exception("crew_steward failed")
            return f"[Error] crew_steward 失败: {e}"

    def _registry(self):
        from backend.kernel import get_kernel

        reg = getattr(get_kernel(), "identity_registry", None)
        if reg is None:
            raise RuntimeError("编制层未启用（identity_registry=None）")
        return reg

    async def _list(self) -> str:
        reg = self._registry()
        items = await reg.list(status=None)
        lines = []
        for i in items:
            caps = ",".join(i.capabilities or []) or "(无能力)"
            b = getattr(i, "default_token_budget", None)
            btxt = "不限" if b == 0 else (str(b) if b is not None else "默认")
            lines.append(
                f"- {i.name} id={i.id} status={i.status} role={i.role or '-'} "
                f"budget={btxt} caps=[{caps}]"
            )
        body = "\n".join(lines) if lines else "（编制为空）"
        return (
            f"员工 {len(items)} 人：\n{body}\n"
            "（改档案预算：set_budget name=… token_budget=200000；"
            "本单加码：assign 时带 token_budget=…）"
        )

    def _parse_token_budget_arg(self, kwargs: dict[str, Any], *, required: bool = False) -> int | None:
        """解析 token_budget；None=未传。0=不限。"""
        if "token_budget" not in kwargs and "budget" not in kwargs:
            if required:
                raise ValueError("需要 token_budget（正整数或 0=不限）")
            return None
        raw = kwargs.get("token_budget", kwargs.get("budget"))
        if raw is None or raw == "":
            if required:
                raise ValueError("需要 token_budget（正整数或 0=不限）")
            return None
        from backend.agent.workforce_budget import clamp_ceo_budget

        return clamp_ceo_budget(int(raw))

    async def _set_budget(self, kwargs: dict[str, Any]) -> str:
        """改员工档案 default_token_budget（持久）。"""
        try:
            budget_i = self._parse_token_budget_arg(kwargs, required=True)
        except Exception as e:
            return f"[Error] set_budget: {e}"
        assert budget_i is not None
        ident = await self._resolve_identity(kwargs)
        reg = self._registry()
        old = getattr(ident, "default_token_budget", None)
        await reg.update_profile(
            ident.id,
            default_token_budget=budget_i,
            by="ceo:set_budget",
        )
        btxt = "不限(0)" if budget_i == 0 else str(budget_i)
        otxt = "不限" if old == 0 else (str(old) if old is not None else "null")
        # 可选：顺带 requeue 一条失败/指定工单，用更高预算重跑
        requeue_note = ""
        if kwargs.get("requeue") in (True, "true", "1", 1, "yes"):
            requeue_note = await self._requeue_after_grant(
                ident,
                {
                    **kwargs,
                    "token_budget": budget_i,  # 本单也用新预算
                },
            )
            if requeue_note:
                requeue_note = f" {requeue_note}"
        return (
            f"✅ 已更新「{ident.name}」档案默认预算 {otxt} → {btxt}。"
            f"新工单自动抬升仍会在此基础上取 max；"
            f"本单硬顶请在 assign 时另传 token_budget。{requeue_note}"
        )

    async def _budgets(self, kwargs: dict[str, Any] | None = None) -> str:
        """预算台账：档案默认 + 在跑/排队工单的 payload 预算。"""
        kwargs = kwargs or {}
        reg = self._registry()
        items = await reg.list(status=None)
        from backend.agent.workforce_budget import hard_cap, suggested_token_budget
        from backend.kernel.workforce import get_workforce_inbox

        lines = [
            f"预算硬顶 hard_cap={hard_cap()}（环境 TAKTON_WORKFORCE_BUDGET_HARD_CAP）",
            "档案默认（default_token_budget）：",
        ]
        for i in items:
            if i.status != "active" and str(kwargs.get("all") or "") not in ("1", "true", "yes"):
                continue
            b = getattr(i, "default_token_budget", None)
            btxt = "不限" if b == 0 else (str(b) if b is not None else "null→fallback")
            # 示意：体检类自动抬升后大约多少
            sample = suggested_token_budget(
                base=b if b is None else int(b),
                instruction="系统健康检查 审计 backend",
                role=i.role,
                name=i.name,
            )
            lines.append(
                f"  · {i.name} [{i.status}] 档案={btxt}  "
                f"示例(体检类auto)≈{sample}"
            )

        inbox = get_workforce_inbox()
        if inbox is not None:
            lines.append("在途工单（pending/claimed）显式预算：")
            n = 0
            for st in ("claimed", "pending"):
                try:
                    rows = await inbox.list_items(status=st, limit=40)
                except Exception:
                    continue
                names = {str(x.id): x.name for x in items}
                for it in rows:
                    pl = it.payload if isinstance(it.payload, dict) else {}
                    tb = pl.get("token_budget", pl.get("budget"))
                    if tb is None and not kwargs.get("verbose"):
                        continue
                    nm = names.get(str(it.identity_id), str(it.identity_id)[:8])
                    lines.append(
                        f"  · [{st}] {nm} item={str(it.id)[:8]} "
                        f"token_budget={tb if tb is not None else '（自动）'} "
                        f"instr={(it.instruction or '')[:60]!r}"
                    )
                    n += 1
            if n == 0:
                lines.append("  （无在途显式 token_budget；可用 verbose=true 列出全部）")
                if kwargs.get("verbose") in (True, "true", "1", 1, "yes"):
                    for st in ("claimed", "pending"):
                        rows = await inbox.list_items(status=st, limit=20)
                        names = {str(x.id): x.name for x in items}
                        for it in rows:
                            nm = names.get(str(it.identity_id), str(it.identity_id)[:8])
                            lines.append(
                                f"  · [{st}] {nm} item={str(it.id)[:8]} auto "
                                f"instr={(it.instruction or '')[:50]!r}"
                            )

        # 在跑进程实时用量（CEO 动态加预算依据）
        try:
            from backend.kernel import get_kernel

            kernel = get_kernel()
            live = list(kernel.list_processes(include_terminal=False) or [])
            lines.append("在跑进程（live）：")
            if not live:
                lines.append("  （当前无非终态进程）")
            for p in live:
                used = int(getattr(p, "tokens_used", 0) or 0)
                bud = getattr(p, "token_budget", None)
                if bud is None:
                    pct = "∞"
                    btxt = "不限"
                else:
                    bud_i = int(bud)
                    pct = f"{(100.0 * used / bud_i):.0f}%" if bud_i > 0 else "?"
                    btxt = str(bud_i)
                warn = ""
                if bud is not None and int(bud) > 0 and used / int(bud) >= 0.7:
                    warn = " ⚠≥70% 建议 top_up"
                lines.append(
                    f"  · {p.identity} pid={p.id[:8]} state={p.state} "
                    f"used={used}/{btxt} ({pct}){warn}"
                )
        except Exception as e:
            lines.append(f"  （live 进程读取失败: {e}）")

        lines.append(
            "用法：set_budget name=工程师 token_budget=250000；"
            "top_up name=工程师 amount=300000 reason=长任务续航；"
            "assign name=… instruction=… token_budget=300000；"
            "Budget 失败后 requeue=true 并抬 token_budget。"
        )
        return "\n".join(lines)

    async def _top_up(self, kwargs: dict[str, Any]) -> str:
        """运行中给在跑进程追加预算（CEO 动态加预算，不杀进程）。"""
        from backend.kernel import get_kernel

        raw_amt = kwargs.get("amount", kwargs.get("token_budget"))
        if raw_amt is None:
            return "[Error] top_up 需要 amount（或 token_budget）正整数"
        try:
            from backend.agent.workforce_budget import clamp_ceo_budget

            amount = clamp_ceo_budget(int(raw_amt))
        except Exception as e:
            return f"[Error] top_up amount 无效: {e}"
        if amount is None or int(amount) <= 0:
            return "[Error] top_up amount 必须为正整数（不可 0=不限，请用大数或多次 top_up）"
        amount = int(amount)
        reason = str(kwargs.get("reason") or "ceo dynamic top_up")[:200]
        kernel = get_kernel()
        pid = str(kwargs.get("process_id") or "").strip()
        results: list[str] = []

        if pid:
            try:
                r = kernel.top_up_budget(pid, amount, by="ceo:crew_steward", reason=reason)
                results.append(
                    f"pid={pid[:8]} budget={r.get('token_budget')} "
                    f"used={r.get('tokens_used')} remaining={r.get('budget_remaining')}"
                )
            except Exception as e:
                return f"[Error] top_up 进程 {pid[:8]}: {e}"
            return f"✅ 已 top_up +{amount}：{'; '.join(results)}（reason={reason}）"

        # 按员工：抬该 identity 下全部在跑进程
        try:
            ident = await self._resolve_identity(kwargs)
        except Exception as e:
            return (
                f"[Error] top_up 需要 name=员工名 或 identity_id=… 或 process_id=…（{e}）"
            )
        keys = [f"wf:{ident.id}", str(ident.id), ident.name]
        # 编制进程 identity 键一般为 wf:{uuid}
        targets = []
        for k in keys:
            targets.extend(kernel.live_processes_for_identity(k))
        # 去重
        seen: set[str] = set()
        uniq = []
        for p in targets:
            if p.id not in seen:
                seen.add(p.id)
                uniq.append(p)
        if not uniq:
            # 宽松匹配：identity 字符串包含 uuid
            for p in kernel.list_processes(include_terminal=False):
                if str(ident.id) in str(p.identity):
                    if p.id not in seen:
                        seen.add(p.id)
                        uniq.append(p)
        if not uniq:
            return (
                f"「{ident.name}」当前无在跑进程，无需 top_up。"
                f"可用 budgets 查看；新工单请 set_budget / assign.token_budget。"
            )
        for p in uniq:
            try:
                r = kernel.top_up_budget(
                    p.id, amount, by="ceo:crew_steward", reason=reason
                )
                results.append(
                    f"{p.identity} pid={p.id[:8]} → budget={r.get('token_budget')} "
                    f"used={r.get('tokens_used')} rem={r.get('budget_remaining')}"
                )
            except Exception as e:
                results.append(f"{p.identity} pid={p.id[:8]} FAIL: {e}")
        # 顺带抬档案默认，减少下一单再顶
        try:
            reg = self._registry()
            cur = int(getattr(ident, "default_token_budget", 0) or 0)
            if cur != 0:  # 0=不限则不动
                raised = max(cur, cur + amount // 2)
                await reg.update_profile(
                    ident.id,
                    default_token_budget=raised,
                    by="ceo:top_up",
                )
                results.append(f"档案默认预算 {cur} → {raised}")
        except Exception as e:
            results.append(f"档案预算未改: {e}")
        return f"✅ top_up +{amount}（{ident.name}）：\n" + "\n".join(f"  · {x}" for x in results)

    async def _hire(self, kwargs: dict[str, Any]) -> str:
        name = str(kwargs.get("name") or "").strip()
        if not name:
            return "[Error] hire 需要 name"
        role = str(kwargs.get("role") or "").strip()
        persona = str(kwargs.get("persona") or "").strip()
        duty = str(kwargs.get("duty") or "").strip()
        caps = kwargs.get("capabilities")
        if not isinstance(caps, list) or not caps:
            caps = ["file_rw", "web_search"]
        budget = kwargs.get("token_budget")
        try:
            budget_i = int(budget) if budget is not None else 100_000
        except Exception:
            budget_i = 100_000
        if budget_i > 0:
            budget_i = max(budget_i, 100_000)  # 员工入编地板 100k

        reg = self._registry()
        sub_id = None
        try:
            from backend.models.sub_agent import SubAgent

            async with reg._session_factory() as session:  # type: ignore[attr-defined]
                pack = SubAgent(
                    name=name,
                    description=role or f"Skill pack for {name}",
                    icon="👤",
                    model_ref="default",
                    system_prompt=(
                        f"You are {name}"
                        + (f", {role}." if role else ".")
                        + (f"\nPersona: {persona}" if persona else "")
                        + (f"\nDuty: {duty}" if duty else "")
                        + "\nYou are a member of this AI workforce."
                    ),
                    enabled_toolsets=list(caps),
                    max_iterations=20,
                    temperature=0.3,
                    enabled=True,
                    is_builtin=False,
                )
                session.add(pack)
                await session.commit()
                await session.refresh(pack)
                sub_id = pack.id
        except Exception as e:
            logger.warning("crew_steward hire skill pack skip: %s", e)

        ident = await reg.create(
            name,
            role=role,
            capabilities=list(caps),
            default_token_budget=budget_i,
            sub_agent_id=sub_id,
            meta={
                "source": "crew_steward",
                "persona": persona,
                "duty": duty,
                "skill_pack": "sub_agent" if sub_id else None,
            },
        )
        if persona:
            try:
                await reg.add_memory(
                    ident.id, "persona", persona, source="system", approved_by="crew_steward"
                )
            except Exception:
                pass
        if duty:
            try:
                await reg.add_memory(
                    ident.id, "duty", duty, source="system", approved_by="crew_steward"
                )
            except Exception:
                pass
        btxt = "不限" if budget_i == 0 else str(budget_i)
        return (
            f"✅ 已入编员工「{ident.name}」id={ident.id} role={role or '-'} "
            f"档案预算={btxt}。"
            f"可用 assign 派活（可加 token_budget= 本单加码）。侧栏员工列表会同步出现。"
        )

    async def _resolve_identity(self, kwargs: dict[str, Any]):
        reg = self._registry()
        iid = str(kwargs.get("identity_id") or "").strip()
        name = str(kwargs.get("name") or "").strip()
        if iid:
            ident = await reg.get(iid)
            if ident is None:
                raise ValueError(f"找不到员工 id={iid}")
            return ident
        if not name:
            raise ValueError("需要 name 或 identity_id")
        items = await reg.list(status=None)
        for i in items:
            if i.name == name:
                return i
        raise ValueError(f"找不到在编员工「{name}」。先用 hire 创建或 list 核对姓名。")

    def _normalize_caps(
        self, capabilities: Any, tools: Any
    ) -> list[str]:
        """Merge capability ids + tool→cap mapping; drop unknowns with note."""
        from backend.agent.grant_store import crew_cap_for_tool

        out: list[str] = []
        for c in capabilities if isinstance(capabilities, list) else []:
            s = str(c).strip()
            if not s:
                continue
            if s in _KNOWN_CAPS or s == "*":
                out.append(s)
            else:
                # allow extension caps but prefer known
                mapped = crew_cap_for_tool(s)
                out.append(mapped or s)
        for t in tools if isinstance(tools, list) else []:
            cap = crew_cap_for_tool(str(t).strip())
            if cap:
                out.append(cap)
            elif str(t).strip() in _KNOWN_CAPS:
                out.append(str(t).strip())
        # dedupe preserve order
        seen: set[str] = set()
        uniq: list[str] = []
        for c in out:
            if c not in seen:
                seen.add(c)
                uniq.append(c)
        return uniq

    async def _grant_caps(self, kwargs: dict[str, Any]) -> str:
        """动态扩权：合并进 Identity.capabilities（审计 by=ceo）。"""
        try:
            ident = await self._resolve_identity(kwargs)
        except ValueError as e:
            return f"[Error] {e}"
        add = self._normalize_caps(kwargs.get("capabilities"), kwargs.get("tools"))
        if not add:
            return (
                "[Error] grant_caps 需要 capabilities 或 tools。"
                "例：capabilities=[\"command\",\"git\"] 或 tools=[\"command\",\"file_write\"]。"
                f" 已知能力：{', '.join(sorted(_KNOWN_CAPS))}"
            )
        reg = self._registry()
        old = list(ident.capabilities or [])
        merged = list(old)
        for c in add:
            if c not in merged:
                merged.append(c)
        reason = str(kwargs.get("reason") or "ceo_grant_caps").strip()[:200]
        by = f"ceo:{reason}" if reason else "ceo"
        await reg.set_capabilities(ident.id, merged, by=by)
        try:
            from backend.kernel.cap_requests import mark_granted_for_identity

            mark_granted_for_identity(
                str(ident.id), caps=add, tools=list(kwargs.get("tools") or []), by=by
            )
        except Exception:
            pass

        requeue_note = ""
        if kwargs.get("requeue") in (True, "true", "1", 1, "yes"):
            requeue_note = await self._requeue_after_grant(ident, kwargs)

        return (
            f"✅ 已给「{ident.name}」扩权：+{add}\n"
            f"原 caps={old or []}\n"
            f"现 caps={merged}\n"
            f"（审计 by={by}；下一刀工具即生效，无需重启）"
            + (f"\n{requeue_note}" if requeue_note else "")
        )

    async def _revoke_caps(self, kwargs: dict[str, Any]) -> str:
        try:
            ident = await self._resolve_identity(kwargs)
        except ValueError as e:
            return f"[Error] {e}"
        drop = self._normalize_caps(kwargs.get("capabilities"), kwargs.get("tools"))
        if not drop:
            return "[Error] revoke_caps 需要 capabilities 或 tools"
        old = list(ident.capabilities or [])
        new = [c for c in old if c not in set(drop)]
        reason = str(kwargs.get("reason") or "ceo_revoke_caps").strip()[:200]
        await self._registry().set_capabilities(
            ident.id, new, by=f"ceo:{reason}" if reason else "ceo"
        )
        return f"✅ 已收回「{ident.name}」能力 {drop}\n现 caps={new}"

    async def _set_caps(self, kwargs: dict[str, Any]) -> str:
        """整表替换能力档案。"""
        try:
            ident = await self._resolve_identity(kwargs)
        except ValueError as e:
            return f"[Error] {e}"
        new = self._normalize_caps(kwargs.get("capabilities"), kwargs.get("tools"))
        if not new and kwargs.get("capabilities") is None and kwargs.get("tools") is None:
            return "[Error] set_caps 需要 capabilities 列表（可为空表表示清空）"
        if kwargs.get("capabilities") == [] and not kwargs.get("tools"):
            new = []
        old = list(ident.capabilities or [])
        reason = str(kwargs.get("reason") or "ceo_set_caps").strip()[:200]
        await self._registry().set_capabilities(
            ident.id, new, by=f"ceo:{reason}" if reason else "ceo"
        )
        return f"✅ 已重写「{ident.name}」能力\n原 {old}\n现 {new}"

    async def _pending_grants(self, kwargs: dict[str, Any]) -> str:
        from backend.kernel.cap_requests import list_pending

        try:
            limit = int(kwargs.get("limit") or 20)
        except Exception:
            limit = 20
        iid = str(kwargs.get("identity_id") or "").strip() or None
        name = str(kwargs.get("name") or "").strip()
        if name and not iid:
            try:
                ident = await self._resolve_identity(kwargs)
                iid = str(ident.id)
            except Exception:
                pass
        items = list_pending(identity_id=iid, limit=limit)
        if not items:
            return "无待批权限请求。员工被拒权时会自动登记。"
        lines = [f"待批权限 {len(items)} 条（用 grant_caps 处理）："]
        for r in items:
            lines.append(
                f"- {r.get('id')} 员工={r.get('identity_name') or r.get('identity_id')} "
                f"tool={r.get('tool')} need={r.get('needed_cap')} "
                f"hits={r.get('hits')} job={r.get('inbox_item_id') or '-'}"
            )
        lines.append(
            "示例：crew_steward action=grant_caps name=金算 capabilities=[\"command\"] requeue=true"
        )
        return "\n".join(lines)

    async def _requeue_after_grant(self, ident: Any, kwargs: dict[str, Any]) -> str:
        """扩权后重派：优先 inbox_item_id 的 instruction，否则用 instruction 字段。"""
        from backend.kernel.workforce import get_workforce_inbox

        inbox = get_workforce_inbox()
        if inbox is None:
            return "（requeue 跳过：收件箱未启用）"
        instruction = str(kwargs.get("instruction") or "").strip()
        item_id = str(kwargs.get("inbox_item_id") or "").strip()
        if item_id and not instruction:
            try:
                # best-effort load instruction from any status list
                for st in ("dead", "failed", "done", "claimed", "pending"):
                    rows = await inbox.list_items(status=st, limit=80)
                    for it in rows:
                        if str(it.id) == item_id:
                            instruction = str(it.instruction or "")
                            break
                    if instruction:
                        break
            except Exception as e:
                logger.debug("requeue load item: %s", e)
        if not instruction:
            return "（requeue 跳过：无 instruction / inbox_item_id 内容）"
        # requeue 同样过派单校验，防止扩权后把毒工单原样打回队列
        force = kwargs.get("force") in (True, "true", "1", 1, "yes")
        requeue_warn = ""
        try:
            from backend.agent.dispatch_grounding import (
                format_block_message,
                scan_dispatch_instruction,
            )

            risk = scan_dispatch_instruction(instruction)
            if risk.severity == "block" and not force:
                return (
                    format_block_message(risk)
                    + "\n（requeue 被拒：请改写 instruction 或 force=true）"
                )
            if risk.severity in ("warn", "block"):
                requeue_warn = (
                    f"（⚠ requeue 校验 severity={risk.severity}："
                    + "; ".join(risk.reasons[:3])
                    + "）\n"
                )
        except Exception as e:
            logger.debug("requeue dispatch grounding skip: %s", e)
        steward_sid = str(kwargs.get("_session_id") or "").strip() or None
        payload: dict[str, Any] = {
            "via": "crew_steward",
            "after_grant": True,
            "prev_inbox_item_id": item_id or None,
        }
        if steward_sid:
            payload["steward_session_id"] = steward_sid
        budget_note = ""
        try:
            job_budget = self._parse_token_budget_arg(kwargs, required=False)
        except Exception as e:
            return f"（requeue 失败：token_budget {e}）"
        if job_budget is not None:
            payload["token_budget"] = job_budget
            payload["budget_source"] = "ceo_requeue"
            budget_note = f" 本单预算={job_budget if job_budget else '不限'}"
        item = await inbox.enqueue(
            ident.id,
            instruction,
            source="api",
            priority=int(kwargs.get("priority") or 5),
            payload=payload,
        )
        if item is None:
            return "（requeue 失败：入队被拒）"
        return f"{requeue_warn}已 requeue 工单 id={item.id} →「{ident.name}」{budget_note}"

    async def _assign(self, kwargs: dict[str, Any]) -> str:
        instruction = str(kwargs.get("instruction") or "").strip()
        if not instruction:
            return "[Error] assign 需要 instruction（工单内容）"
        # 派单落地校验：拦 CEO 幻觉路径/假指标/未核实「最新」污染员工
        force = kwargs.get("force") in (True, "true", "1", 1, "yes")
        try:
            from backend.agent.dispatch_grounding import (
                format_block_message,
                format_warn_message,
                scan_dispatch_instruction,
            )

            risk = scan_dispatch_instruction(instruction)
            if risk.severity == "block" and not force:
                return format_block_message(risk)
            warn_prefix = ""
            if risk.severity == "warn":
                warn_prefix = format_warn_message(risk)
            elif risk.severity == "block" and force:
                warn_prefix = (
                    "（提示·已 force 跳过 block："
                    + "; ".join(risk.reasons[:3])
                    + "）\n"
                )
        except Exception as e:
            logger.debug("dispatch grounding skip: %s", e)
            warn_prefix = ""
            risk = None  # type: ignore[assignment]

        ident = await self._resolve_identity(kwargs)
        if ident.status != "active":
            return f"[Error] 员工「{ident.name}」状态为 {ident.status}，无法接单"
        from backend.kernel.workforce import get_workforce_inbox

        inbox = get_workforce_inbox()
        if inbox is None:
            return (
                "[Error] 收件箱未启用。请确认 dispatcher/persistence 或 "
                "TAKTON_AIOS_PROFILE=aios-dev"
            )
        try:
            priority = int(kwargs.get("priority") or 0)
        except Exception:
            priority = 0
        project_title = str(kwargs.get("project_title") or kwargs.get("title") or "").strip()
        steward_sid = str(kwargs.get("_session_id") or "").strip() or None
        payload: dict[str, Any] = {"via": "crew_steward"}
        if project_title:
            payload["project_title"] = project_title
        if steward_sid:
            payload["steward_session_id"] = steward_sid
        if risk is not None:
            try:
                payload["dispatch_grounding"] = risk.to_dict()
            except Exception:
                pass
        # CEO 本单预算硬顶（优先于档案+任务类自动抬升）
        budget_note = ""
        try:
            job_budget = self._parse_token_budget_arg(kwargs, required=False)
        except Exception as e:
            return f"[Error] assign token_budget: {e}"
        if job_budget is not None:
            payload["token_budget"] = job_budget
            payload["budget_source"] = "ceo_assign"
            btxt = "不限" if job_budget == 0 else str(job_budget)
            budget_note = f" 本单预算={btxt}（CEO 指定）"
        else:
            try:
                from backend.agent.workforce_budget import resolve_job_budget

                auto_b, _src = resolve_job_budget(ident, instruction)
                if auto_b is not None:
                    budget_note = f" 本单预算≈{auto_b}（自动抬升；可加 token_budget= 覆盖）"
            except Exception:
                pass
        item = await inbox.enqueue(
            ident.id,
            instruction,
            source="api",
            priority=priority,
            payload=payload,
        )
        if item is None:
            return f"[Error] 工单被拒收（员工「{ident.name}」可能停用或队列溢出）"
        extra = ""
        if project_title:
            try:
                g = await self._ensure_project_group(
                    project_title,
                    members=[{"identity_id": str(ident.id), "name": ident.name}],
                    tasks=[
                        {
                            "inbox_item_id": str(item.id),
                            "identity_id": str(ident.id),
                            "identity_name": ident.name,
                        }
                    ],
                    steward_session_id=steward_sid,
                )
                extra = f" 已挂入项目组「{g['title']}」id={g['id']}（侧栏可看进度）。"
            except Exception as e:
                logger.warning("auto project group skip: %s", e)
                extra = " （项目组挂载失败，工单仍有效）"
        else:
            extra = " 多人分工完成后请 open_project 建项目组，便于主人看进度。"
        return (
            f"{warn_prefix}"
            f"✅ 已派给「{ident.name}」工单 {item.id} status={item.status}。"
            f"{budget_note}"
            f" Dispatcher 将自动领取执行。{extra}"
        )

    async def _open_project(self, kwargs: dict[str, Any]) -> str:
        title = str(kwargs.get("title") or kwargs.get("project_title") or "").strip()
        if not title:
            return "[Error] open_project 需要 title（项目组名称）"
        names = kwargs.get("names") if isinstance(kwargs.get("names"), list) else []
        name_one = str(kwargs.get("name") or "").strip()
        if name_one and name_one not in names:
            names = [*names, name_one]
        reg = self._registry()
        active = await reg.list(status="active")
        by_name = {i.name: i for i in active}
        members = []
        for n in names:
            n = str(n).strip()
            if not n:
                continue
            ident = by_name.get(n)
            if ident is None:
                return f"[Error] 找不到员工「{n}」，无法入组。先 list/hire。"
            members.append({"identity_id": str(ident.id), "name": ident.name})
        item_ids = kwargs.get("inbox_item_ids") if isinstance(kwargs.get("inbox_item_ids"), list) else []
        tasks = []
        for iid in item_ids:
            tasks.append({"inbox_item_id": str(iid), "identity_id": "", "identity_name": ""})
        # 无显式 item 时：把成员最近 pending/claimed 工单挂上
        if not tasks and members:
            from backend.kernel.workforce import get_workforce_inbox

            inbox = get_workforce_inbox()
            if inbox is not None:
                for m in members:
                    try:
                        for st in ("pending", "claimed", "done"):
                            items = await inbox.list_items(
                                identity_id=m["identity_id"], status=st, limit=3
                            )
                            for it in items:
                                tasks.append(
                                    {
                                        "inbox_item_id": str(it.id),
                                        "identity_id": m["identity_id"],
                                        "identity_name": m["name"],
                                    }
                                )
                    except Exception:
                        pass
        steward_sid = str(kwargs.get("_session_id") or "").strip() or None
        g = await self._ensure_project_group(
            title,
            members=members,
            tasks=tasks,
            steward_session_id=steward_sid,
        )
        return (
            f"✅ 项目组「{g['title']}」已就绪 id={g['id']} "
            f"成员={g.get('member_count', len(members))} 工单={g.get('task_count', len(tasks))}。"
            f"主人侧栏「项目组」可点开看各人进度。完成后用 results 汇总正文。"
        )

    async def _ensure_project_group(
        self,
        title: str,
        *,
        members: list[dict[str, str]],
        tasks: list[dict[str, str]],
        steward_session_id: str | None = None,
    ) -> dict[str, Any]:
        """同名 open 组合并成员/任务；否则新建。"""
        from sqlalchemy import select

        from backend.database import AsyncSessionLocal
        from backend.models.project_group import ProjectGroup

        async with AsyncSessionLocal() as session:
            rows = list(
                (
                    await session.execute(
                        select(ProjectGroup)
                        .where(ProjectGroup.status == "open")
                        .order_by(ProjectGroup.updated_at.desc())
                        .limit(40)
                    )
                )
                .scalars()
                .all()
            )
            hit = next((r for r in rows if (r.title or "") == title), None)
            base_meta: dict[str, Any] = {"via": "crew_steward"}
            if steward_session_id:
                base_meta["steward_session_id"] = steward_session_id
            if hit is None:
                g = ProjectGroup(
                    user_id=None,
                    title=title[:200],
                    status="open",
                    created_by="ceo",
                    members=members,
                    tasks=tasks,
                    summary="",
                    meta=base_meta,
                )
                session.add(g)
                await session.commit()
                await session.refresh(g)
                return {
                    "id": str(g.id),
                    "title": g.title,
                    "member_count": len(g.members or []),
                    "task_count": len(g.tasks or []),
                }
            # merge
            mem = list(hit.members or [])
            seen_m = {str(m.get("identity_id")) for m in mem}
            for m in members:
                if str(m.get("identity_id")) not in seen_m:
                    mem.append(m)
                    seen_m.add(str(m.get("identity_id")))
            tsk = list(hit.tasks or [])
            seen_t = {str(t.get("inbox_item_id")) for t in tsk}
            for t in tasks:
                if str(t.get("inbox_item_id")) not in seen_t:
                    tsk.append(t)
                    seen_t.add(str(t.get("inbox_item_id")))
            hit.members = mem
            hit.tasks = tsk
            meta = dict(hit.meta or {})
            if steward_session_id and not meta.get("steward_session_id"):
                meta["steward_session_id"] = steward_session_id
            meta.setdefault("via", "crew_steward")
            hit.meta = meta
            await session.commit()
            await session.refresh(hit)
            return {
                "id": str(hit.id),
                "title": hit.title,
                "member_count": len(hit.members or []),
                "task_count": len(hit.tasks or []),
            }

    def _name_map(self, idents: list[Any]) -> dict[str, str]:
        return {str(i.id): str(i.name or "") for i in idents}

    async def _status(self, kwargs: dict[str, Any] | None = None) -> str:
        kwargs = kwargs or {}
        reg = self._registry()
        items = await reg.list(status=None)
        active = sum(1 for i in items if i.status == "active")
        from backend.kernel.workforce import get_workforce_inbox

        inbox = get_workforce_inbox()
        pending = claimed = done = failed = dead = 0
        recent_done: list[Any] = []
        if inbox is not None:
            pending = len(await inbox.list_items(status="pending", limit=200))
            claimed = len(await inbox.list_items(status="claimed", limit=200))
            done_items = await inbox.list_items(status="done", limit=200)
            done = len(done_items)
            failed = len(await inbox.list_items(status="failed", limit=50))
            try:
                dead = len(await inbox.list_items(status="dead", limit=50))
            except Exception:
                dead = 0
            recent_done = list(done_items[:5])
        lines = [
            f"员工 total={len(items)} active={active}",
            f"工单 pending={pending} claimed={claimed} done={done} failed={failed} dead={dead}",
            f"inbox_service={'on' if inbox else 'off'}",
            "提示：要看完成正文请用 action=results（不要只看计数就向主人空口汇报）。",
        ]
        for i in items[:12]:
            lines.append(f"  · {i.name} [{i.status}] {i.role or ''}")
        include = kwargs.get("include_result")
        if include is None:
            include = True
        if include and recent_done:
            names = self._name_map(items)
            lines.append("--- 最近完成（摘要，完整正文用 results）---")
            for it in recent_done:
                nm = names.get(str(it.identity_id), str(it.identity_id)[:8])
                instr = (it.instruction or "").replace("\n", " ")[:80]
                res = (it.result or "").replace("\n", " ")[:160]
                lines.append(f"  ✓ {nm} id={str(it.id)[:8]} | {instr}")
                if res:
                    lines.append(f"    → {res}")
                else:
                    lines.append("    → (无 result 正文)")
        return "\n".join(lines)

    async def _results(self, kwargs: dict[str, Any]) -> str:
        """返回已完成工单正文，供 CEO 向主人汇总。"""
        from backend.kernel.workforce import get_workforce_inbox

        inbox = get_workforce_inbox()
        if inbox is None:
            return "[Error] 收件箱未启用"
        try:
            limit = int(kwargs.get("limit") or 8)
        except Exception:
            limit = 8
        limit = max(1, min(limit, 20))
        name_filter = str(kwargs.get("name") or "").strip()
        iid_filter = str(kwargs.get("identity_id") or "").strip()
        project_title = str(kwargs.get("project_title") or kwargs.get("title") or "").strip()

        reg = self._registry()
        idents = await reg.list(status=None)
        names = self._name_map(idents)
        by_name = {str(i.name): i for i in idents if getattr(i, "name", None)}

        if name_filter and not iid_filter:
            hit = by_name.get(name_filter)
            if hit is None:
                return f"[Error] 找不到员工「{name_filter}」"
            iid_filter = str(hit.id)

        # 可选：按项目组任务过滤
        allow_ids: set[str] | None = None
        if project_title:
            try:
                from sqlalchemy import select

                from backend.database import AsyncSessionLocal
                from backend.models.project_group import ProjectGroup

                async with AsyncSessionLocal() as session:
                    rows = list(
                        (
                            await session.execute(
                                select(ProjectGroup)
                                .order_by(ProjectGroup.updated_at.desc())
                                .limit(60)
                            )
                        )
                        .scalars()
                        .all()
                    )
                g = next((r for r in rows if (r.title or "") == project_title), None)
                if g is None:
                    return f"[Error] 找不到项目组「{project_title}」"
                allow_ids = {
                    str(t.get("inbox_item_id"))
                    for t in (g.tasks or [])
                    if t.get("inbox_item_id")
                }
            except Exception as e:
                return f"[Error] 读项目组失败: {e}"

        done_items = await inbox.list_items(
            identity_id=iid_filter or None,
            status="done",
            limit=max(limit * 3, 30),
        )
        failed_items = await inbox.list_items(
            identity_id=iid_filter or None,
            status="failed",
            limit=10,
        )
        try:
            dead_items = await inbox.list_items(
                identity_id=iid_filter or None,
                status="dead",
                limit=10,
            )
        except Exception:
            dead_items = []

        picked: list[Any] = []
        for it in list(done_items) + list(failed_items) + list(dead_items):
            if allow_ids is not None and str(it.id) not in allow_ids:
                continue
            picked.append(it)
            if len(picked) >= limit:
                break

        if not picked:
            return (
                "（无已完成工单正文可展示。"
                "若刚派单请等 dispatcher 跑完；或检查 project_title/name 过滤。）"
            )

        chunks: list[str] = [
            f"## 工单结果（共 {len(picked)} 条，供向主人汇总，勿重新派单）",
        ]
        for it in picked:
            nm = names.get(str(it.identity_id), str(it.identity_id)[:8])
            st = getattr(it, "status", "?")
            instr = (it.instruction or "").strip()
            body = (it.result or it.error or "").strip() or "（无正文）"
            # 单条正文截断，避免挤爆 CEO 上下文
            if len(body) > 6000:
                body = body[:6000] + "\n…[truncated]"
            chunks.append(
                f"### [{st}] {nm} · 工单 {it.id}\n"
                f"**任务**：{instr[:500]}\n\n"
                f"**结果**：\n{body}\n"
            )
        chunks.append(
            "—— 汇总要求：用中文给主人写一份可读报告（结论 + 分员工要点 + 风险/下一步）；"
            "不要再 hire/assign 同一批任务。"
        )
        return "\n".join(chunks)
