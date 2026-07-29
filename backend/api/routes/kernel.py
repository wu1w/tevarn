"""Agent Kernel 观测 API（阶段 1/W2）。

Security Console 数据源：当前进程树（能力/预算/状态）+ 中介审计事件。
只读接口——进程生命周期由 loop 驱动，不接受外部写操作。

0.4.1：新增提权交互端点（escalations）——这是控制台唯一的写入口，
对应「用户授权是唯一合法的能力扩大通道」。
"""

from __future__ import annotations

import logging
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..dependencies import get_current_user
from backend.kernel import get_kernel
from backend.schemas.user import UserRead

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kernel", tags=["kernel"])

_REPORT_READ_KEY = "workforce_report_read_at"


class StopJobBody(BaseModel):
    """E4 统一停止：至少填 inbox_item_id 或 process_id。"""

    inbox_item_id: str | None = Field(None, description="工单 id")
    process_id: str | None = Field(None, description="kernel process id")
    reason: str = Field("stopped by user", max_length=500)


@router.get("/processes")
async def list_processes(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    include_terminal: bool = Query(False),
):
    """进程列表。

    shared_state=True 时：内存进程 + DB 档案合并（多 worker 观测前提）。
    本 worker 内存中的 live 进程优先（状态更鲜活）。
    """
    kernel = get_kernel()
    live = {p.id: p.to_dict() for p in kernel.list_processes(include_terminal=True)}
    shared = True
    try:
        from backend.core.config import settings
        shared = bool(getattr(settings, "agent_kernel_shared_state", True))
    except Exception:
        shared = True

    db_error = None
    if shared:
        try:
            from sqlalchemy import select
            from backend.database import AsyncSessionLocal
            from backend.models.agent_identity import KernelProcessRecord

            async with AsyncSessionLocal() as session:
                rows = (await session.execute(select(KernelProcessRecord))).scalars().all()
            for r in rows:
                if r.process_id in live:
                    continue  # 内存优先
                if not include_terminal and r.state in (
                    "completed", "failed", "killed", "interrupted", "exited", "done",
                ):
                    continue
                live[r.process_id] = {
                    "id": r.process_id,
                    "identity": r.identity_key,
                    "session_id": r.session_id,
                    "parent_id": r.parent_process_id,
                    "capabilities": r.capabilities,
                    "token_budget": r.token_budget,
                    "tokens_used": r.tokens_used or 0,
                    "state": r.state,
                    "created_at": r.started_at or 0,
                    "started_at": r.started_at,
                    "ended_at": r.ended_at,
                    "exit_reason": r.exit_reason,
                    "source": "db",
                }
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("list_processes DB merge failed: %s", e)
            db_error = str(e)

    procs = list(live.values())
    if not include_terminal:
        terminal = {"completed", "failed", "killed", "interrupted", "exited", "done", "error"}
        procs = [p for p in procs if p.get("state") not in terminal]
    out = {
        "enabled": True,
        "processes": procs,
        "total": len(procs),
        "shared_state": shared,
    }
    if db_error:
        out["db_warning"] = db_error
    return out


@router.get("/processes/{process_id}")
async def get_process(
    process_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    kernel = get_kernel()
    proc = kernel.get_process(process_id)
    if proc is None:
        raise HTTPException(status_code=404, detail=f"process not found: {process_id}")
    data = proc.to_dict()
    # 不向客户端返回 HMAC signature（防离线伪造材料外泄）
    if proc.token is not None:
        tok = proc.token.to_dict(sign=False)
        tok.pop("signature", None)
        data["token"] = tok
    return data


@router.get("/events")
async def list_events(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    process_id: str | None = Query(None),
    kind: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    kernel = get_kernel()
    events = kernel.events(process_id=process_id, kind=kind, limit=limit)
    return {
        "events": [e.to_dict() for e in events],
        "total": len(events),
    }


# ── 提权交互（0.4.1）──────────────────────────────────────────


@router.get("/escalations")
async def list_escalations(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    status: str | None = Query(None),
):
    """提权列表。shared_state 时合并 DB 中的 pending（跨 worker 可见）。"""
    kernel = get_kernel()
    by_id = {r.id: r.to_dict() for r in kernel.list_escalations(status=None)}
    shared = True
    try:
        from backend.core.config import settings
        shared = bool(getattr(settings, "agent_kernel_shared_state", True))
    except Exception:
        shared = True
    db_error = None
    if shared:
        try:
            from sqlalchemy import select
            from backend.database import AsyncSessionLocal
            from backend.models.agent_identity import KernelEscalationRecord

            async with AsyncSessionLocal() as session:
                q = select(KernelEscalationRecord)
                if status:
                    q = q.where(KernelEscalationRecord.status == status)
                rows = (await session.execute(q)).scalars().all()
            for r in rows:
                if r.escalation_id in by_id:
                    continue
                by_id[r.escalation_id] = {
                    "id": r.escalation_id,
                    "process_id": r.process_id,
                    "capabilities": r.capabilities or [],
                    "reason": r.reason or "",
                    "status": r.status,
                    "created_at": r.created_at_ts or 0,
                    "resolved_at": r.resolved_at,
                    "resolved_by": r.resolved_by,
                    "source": "db",
                }
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("list_escalations DB merge failed: %s", e)
            db_error = str(e)
    reqs = list(by_id.values())
    if status is not None:
        reqs = [r for r in reqs if r.get("status") == status]
    reqs.sort(key=lambda r: float(r.get("created_at") or 0), reverse=True)
    out = {
        "escalations": reqs,
        "total": len(reqs),
        "shared_state": shared,
    }
    if db_error:
        out["db_warning"] = db_error
    return out


@router.post("/escalations/{request_id}/approve")
async def approve_escalation(
    request_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    kernel = get_kernel()
    # 跨 worker：先把 DB 中的 pending 水合进本进程内存
    await kernel.ensure_escalation_loaded(request_id)
    try:
        req = await kernel.approve_escalation(request_id, by=str(current_user.id))
    except ValueError as e:
        # 必须 4xx：前端 axios 只把非 2xx 当失败，200+error 会假成功
        raise HTTPException(status_code=400, detail=str(e)) from e
    return req.to_dict()


@router.post("/escalations/{request_id}/deny")
async def deny_escalation(
    request_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    kernel = get_kernel()
    await kernel.ensure_escalation_loaded(request_id)
    try:
        req = await kernel.deny_escalation(request_id, by=str(current_user.id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return req.to_dict()


# ── 身份系统（0.5 编制与档案）───────────────────────────────────


def _identity_registry():
    reg = get_kernel().identity_registry
    return reg


def _ident_dict(i) -> dict:
    return {
        "id": str(i.id),
        "name": i.name,
        "role": i.role,
        "status": i.status,
        "capabilities": i.capabilities,
        "credit_score": i.credit_score,
        "default_token_budget": i.default_token_budget,
        "sub_agent_id": str(i.sub_agent_id) if i.sub_agent_id else None,
        "created_at": i.created_at.isoformat() if i.created_at else None,
        "archived_at": i.archived_at.isoformat() if i.archived_at else None,
        "meta": i.meta or {},
    }


def _memory_dict(m) -> dict:
    return {
        "id": str(m.id),
        "identity_id": str(m.identity_id),
        "kind": m.kind,
        "content": m.content,
        "source": m.source,
        "approved_by": m.approved_by,
        "version": m.version,
        "superseded_by": str(m.superseded_by) if m.superseded_by else None,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


@router.get("/identities")
async def list_identities(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    status: str | None = Query(None),
):
    reg = _identity_registry()
    if reg is None:
        raise HTTPException(status_code=503, detail="identity layer disabled")
    # 多租户：默认只列当前用户的身份；单用户模式附带 orphan（user_id 为空的历史行）
    from backend.core.config import settings

    include_orphan = bool(getattr(settings, "single_user_mode", True))
    items = await reg.list(
        status=status,
        user_id=current_user.id,
        include_orphan=include_orphan,
    )
    return {"identities": [_ident_dict(i) for i in items], "total": len(items)}


@router.post("/identities")
async def create_identity(
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """创建员工 Identity。

    招聘向导（hire）可传：
    - create_skill_pack=true：自动创建 SubAgent 技能包并挂 sub_agent_id（1:1）
    - persona / duty / initial_memory：写入 Identity Memory（system 源）
    """
    reg = _identity_registry()
    if reg is None:
        raise HTTPException(status_code=503, detail="identity layer disabled")
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    role = str(body.get("role") or "").strip()
    caps = body.get("capabilities")
    if caps is not None and not isinstance(caps, list):
        caps = list(caps) if caps else []
    meta = dict(body.get("meta") or {})
    persona = str(body.get("persona") or meta.get("persona") or "").strip()
    duty = str(body.get("duty") or meta.get("duty") or "").strip()
    initial_memory = str(
        body.get("initial_memory") or body.get("init_memory") or ""
    ).strip()
    if persona:
        meta.setdefault("persona", persona)
    if duty:
        meta.setdefault("duty", duty)

    # Hire → Identity：可选自动生成「技能包」SubAgent，关系 1:1 写死在 sub_agent_id
    sub_agent_id = body.get("sub_agent_id")
    create_skill_pack = bool(body.get("create_skill_pack", False))
    if create_skill_pack and not sub_agent_id:
        try:
            from backend.core.config import settings
            from backend.repositories.sub_agent_repo import AsyncSubAgentRepository

            model_ref = (
                str(body.get("model_ref") or "").strip()
                or str(getattr(settings, "default_llm_model", "") or "").strip()
                or str(getattr(settings, "llm_model", "") or "").strip()
                or "default"
            )
            prompt_parts = [
                f"You are {name}" + (f", {role}." if role else "."),
            ]
            if persona:
                prompt_parts.append(f"Persona: {persona}")
            if duty:
                prompt_parts.append(f"Duty: {duty}")
            prompt_parts.append(
                "You are a member of this AI workforce. Be concise, report progress, escalate risks."
            )
            pack_repo = AsyncSubAgentRepository()
            pack = await pack_repo.create(
                {
                    "name": name,
                    "description": role or f"Skill pack for identity {name}",
                    "icon": "👤",
                    "model_ref": model_ref[:128],
                    "system_prompt": "\n".join(prompt_parts),
                    "enabled_toolsets": list(caps or []),
                    "max_iterations": 20,
                    "temperature": 0.3,
                    "enabled": True,
                    "user_id": current_user.id,
                    "is_builtin": False,
                }
            )
            sub_agent_id = pack.id
            meta["skill_pack"] = "sub_agent"
            meta["source"] = meta.get("source") or "hire_wizard"
        except Exception as e:
            logger.warning("hire skill pack create failed (identity still created): %s", e)

    if not meta.get("source") and (create_skill_pack or persona or duty):
        meta["source"] = "hire_wizard"

    try:
        ident = await reg.create(
            name,
            role=role,
            capabilities=caps,
            default_token_budget=body.get("default_token_budget"),
            user_id=current_user.id,
            meta=meta,
            sub_agent_id=sub_agent_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    # 人格/职责/初始记忆落入 Identity Memory（招聘结果可追溯，不只塞 meta）
    for kind, content in (
        ("persona", persona),
        ("duty", duty),
        ("preference", initial_memory),
    ):
        if not content:
            continue
        try:
            await reg.add_memory(
                ident.id, kind, content, source="system", approved_by=str(current_user.id)
            )
        except Exception as e:
            logger.warning("hire identity memory %s skipped: %s", kind, e)

    out = _ident_dict(ident)
    out["skill_pack_linked"] = bool(ident.sub_agent_id)
    return out


@router.post("/identities/{identity_id}/transition")
async def transition_identity(
    identity_id: str,
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """状态机：suspend / resume / archive（解雇，终态不可逆）。

    action 别名：fire / dismiss / terminate → archive
    """
    reg = _identity_registry()
    if reg is None:
        raise HTTPException(status_code=503, detail="identity layer disabled")
    action = str(body.get("action") or "").strip().lower()
    if action in ("fire", "dismiss", "terminate", "解雇"):
        action = "archive"
    by = str(current_user.id)
    try:
        if action == "suspend":
            ident = await reg.suspend(identity_id, by=by)
        elif action == "resume":
            ident = await reg.resume(identity_id, by=by)
        elif action == "archive":
            ident = await reg.archive(identity_id, by=by)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"unknown action {action!r}（suspend/resume/archive|fire）",
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _ident_dict(ident)


@router.patch("/identities/{identity_id}")
@router.post("/identities/{identity_id}/profile")
async def update_identity_profile(
    identity_id: str,
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """改名 / 职位(role) / 默认 token 预算。

    body:
      - name?: str
      - role?: str  （职位/职责标题）
      - default_token_budget?: int | null
      - persona?: str  → Identity Memory persona（可选）
      - duty?: str     → Identity Memory duty（可选）
    """
    reg = _identity_registry()
    if reg is None:
        raise HTTPException(status_code=503, detail="identity layer disabled")

    kwargs: dict = {"by": str(current_user.id)}
    if "name" in body:
        kwargs["name"] = body.get("name")
    if "role" in body:
        kwargs["role"] = body.get("role")
    if "default_token_budget" in body:
        kwargs["default_token_budget"] = body.get("default_token_budget")

    try:
        ident = await reg.update_profile(identity_id, **kwargs)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # 可选：同步人格/职责记忆
    persona = body.get("persona")
    duty = body.get("duty")
    try:
        if isinstance(persona, str) and persona.strip():
            # supersede 现有 persona 或新增
            cur = await reg.current_memory(identity_id, kind="persona")
            if cur:
                await reg.supersede_memory(
                    cur[0].id, persona.strip(), approved_by=str(current_user.id)
                )
            else:
                await reg.add_memory(
                    identity_id,
                    "persona",
                    persona.strip(),
                    source="manual",
                    approved_by=str(current_user.id),
                )
        if isinstance(duty, str) and duty.strip():
            cur = await reg.current_memory(identity_id, kind="duty")
            if cur:
                await reg.supersede_memory(
                    cur[0].id, duty.strip(), approved_by=str(current_user.id)
                )
            else:
                await reg.add_memory(
                    identity_id,
                    "duty",
                    duty.strip(),
                    source="manual",
                    approved_by=str(current_user.id),
                )
    except Exception as e:
        logger.warning("identity memory profile sync: %s", e)

    return _ident_dict(ident)


@router.post("/identities/{identity_id}/capabilities")
async def set_identity_capabilities(
    identity_id: str,
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """权限档案变更——全程审计（禁止静默改权）。

    body.mode:
      - replace（默认）：整表替换 capabilities
      - grant：合并扩权（CEO 动态赋权）
      - revoke：收回部分能力
    """
    reg = _identity_registry()
    if reg is None:
        raise HTTPException(status_code=503, detail="identity layer disabled")
    mode = str(body.get("mode") or "replace").strip().lower()
    caps_in = body.get("capabilities")
    if not isinstance(caps_in, list) and mode == "replace":
        raise HTTPException(status_code=400, detail="capabilities must be a list")
    try:
        if mode in ("grant", "add", "merge"):
            ident0 = await reg.get(identity_id)
            if ident0 is None:
                raise ValueError(f"未知身份 {identity_id}")
            old = list(ident0.capabilities or [])
            add = [str(c).strip() for c in (caps_in or []) if str(c).strip()]
            # tools[] → cap mapping
            tools = body.get("tools") if isinstance(body.get("tools"), list) else []
            try:
                from backend.agent.grant_store import crew_cap_for_tool

                for t in tools:
                    cap = crew_cap_for_tool(str(t))
                    if cap:
                        add.append(cap)
            except Exception:
                pass
            merged = list(old)
            for c in add:
                if c not in merged:
                    merged.append(c)
            ident = await reg.set_capabilities(
                identity_id, merged, by=f"api_grant:{current_user.id}"
            )
            try:
                from backend.kernel.cap_requests import mark_granted_for_identity

                mark_granted_for_identity(
                    identity_id, caps=add, tools=[str(t) for t in tools], by="api"
                )
            except Exception:
                pass
        elif mode in ("revoke", "remove"):
            ident0 = await reg.get(identity_id)
            if ident0 is None:
                raise ValueError(f"未知身份 {identity_id}")
            drop = {str(c).strip() for c in (caps_in or []) if str(c).strip()}
            new = [c for c in (ident0.capabilities or []) if c not in drop]
            ident = await reg.set_capabilities(
                identity_id, new, by=f"api_revoke:{current_user.id}"
            )
        else:
            ident = await reg.set_capabilities(
                identity_id, caps_in, by=str(current_user.id)
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _ident_dict(ident)


@router.get("/cap-requests")
async def list_cap_requests(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    identity_id: str | None = Query(None),
    limit: int = Query(40, ge=1, le=100),
):
    """待 CEO 处理的员工扩权请求（工具被 steward 拒时自动登记）。"""
    from backend.kernel.cap_requests import list_pending

    items = list_pending(identity_id=identity_id, limit=limit)
    return {"items": items, "total": len(items)}


@router.get("/identities/{identity_id}/memory")
async def get_identity_memory(
    identity_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    kind: str | None = Query(None),
):
    reg = _identity_registry()
    if reg is None:
        raise HTTPException(status_code=503, detail="identity layer disabled")
    try:
        items = await reg.current_memory(identity_id, kind=kind)
    except ValueError as e:
        # 非法 UUID 等 → 404，避免前端当成「记忆接口挂了」
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"memory": [_memory_dict(m) for m in items], "total": len(items)}


@router.post("/identities/{identity_id}/memory")
async def add_identity_memory(
    identity_id: str,
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    reg = _identity_registry()
    if reg is None:
        raise HTTPException(status_code=503, detail="identity layer disabled")
    # 招聘向导等前端自定义 source 归一到合法枚举
    raw_source = str(body.get("source") or "manual").strip() or "manual"
    source_alias = {
        "hire-wizard": "system",
        "hire": "system",
        "wizard": "system",
        "onboarding": "system",
    }
    source = source_alias.get(raw_source, raw_source)
    try:
        entry = await reg.add_memory(
            identity_id,
            str(body.get("kind") or ""),
            str(body.get("content") or ""),
            source=source,
            approved_by=body.get("approved_by") or (
                str(current_user.id) if source == "manual" else None
            ),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _memory_dict(entry)


@router.post("/identities/{identity_id}/memory/{entry_id}/supersede")
async def supersede_identity_memory(
    identity_id: str,
    entry_id: str,
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    reg = _identity_registry()
    if reg is None:
        raise HTTPException(status_code=503, detail="identity layer disabled")
    try:
        entry = await reg.supersede_memory(
            entry_id,
            str(body.get("content") or ""),
            approved_by=str(body.get("approved_by") or current_user.id),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _memory_dict(entry)


@router.post("/identities/{identity_id}/memory/{entry_id}/retire")
async def retire_identity_memory(
    identity_id: str,
    entry_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    body: dict | None = None,
):
    """废止记忆：tombstone supersede，Assembler 不再注入。"""
    from backend.kernel.crew_memory import get_crew_memory_writer

    reg = _identity_registry()
    if reg is None:
        raise HTTPException(status_code=503, detail="identity layer disabled")
    payload = body or {}
    try:
        writer = get_crew_memory_writer(reg)
        entry = await writer.retire(
            entry_id,
            approved_by=str(payload.get("approved_by") or current_user.id),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _memory_dict(entry)


@router.post("/identities/{identity_id}/memory/preview")
async def preview_identity_memory_inject(
    identity_id: str,
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """预览将注入到 prompt 的记忆块（派活前可解释）。"""
    from backend.kernel.crew_memory import get_crew_memory_assembler

    reg = _identity_registry()
    if reg is None:
        raise HTTPException(status_code=503, detail="identity layer disabled")
    instruction = str(body.get("instruction") or "")
    mode = str(body.get("mode") or "preview")
    if mode not in ("workforce", "chat", "preview", "compact"):
        mode = "preview"
    try:
        asm = get_crew_memory_assembler(reg)
        result = await asm.build_inject_block(
            identity_id, instruction, mode=mode  # type: ignore[arg-type]
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {
        "header": result.header,
        "body": result.body,
        "text": f"{result.header}\n{result.body}",
        "entries_used": [
            {
                "id": e.id,
                "kind": e.kind,
                "version": e.version,
                "chars": e.chars,
            }
            for e in result.entries_used
        ],
        "truncated": result.truncated,
        "token_estimate": result.token_estimate,
        "mode": result.mode,
    }


@router.post("/identities/{identity_id}/memory/distill-from-item")
async def distill_memory_from_item(
    identity_id: str,
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """从已完成工单手动沉淀 experience（force，走审批人=当前用户）。"""
    from backend.kernel.crew_memory import get_crew_memory_writer
    from backend.kernel.workforce import get_workforce_inbox

    reg = _identity_registry()
    if reg is None:
        raise HTTPException(status_code=503, detail="identity layer disabled")
    item_id = str(body.get("inbox_item_id") or body.get("item_id") or "").strip()
    if not item_id:
        raise HTTPException(status_code=400, detail="inbox_item_id required")

    inbox = get_workforce_inbox()
    if inbox is None:
        raise HTTPException(status_code=503, detail="inbox disabled")

    items = await inbox.list_items(identity_id=identity_id, limit=200)
    item = next((i for i in items if str(i.id) == item_id), None)
    if item is None:
        # 放宽：不限 identity 再找一次
        items2 = await inbox.list_items(limit=300)
        item = next((i for i in items2 if str(i.id) == item_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="inbox item not found")
    if str(item.identity_id) != str(identity_id):
        raise HTTPException(status_code=400, detail="item does not belong to identity")

    status = str(getattr(item, "status", "") or "")
    if status not in ("done", "completed"):
        raise HTTPException(
            status_code=400,
            detail=f"only done jobs can distill (status={status})",
        )

    writer = get_crew_memory_writer(reg)
    entry = await writer.maybe_distill_from_job(
        identity_id=identity_id,
        instruction=str(getattr(item, "instruction", "") or ""),
        result=str(getattr(item, "result", "") or ""),
        process_id=str(getattr(item, "process_id", "") or "") or None,
        status="done",
        force=True,
        approved_by=str(current_user.id),
        source="manual",
    )
    if entry is None:
        raise HTTPException(
            status_code=400,
            detail="distill skipped (failure markers or empty result)",
        )
    return _memory_dict(entry)


# ── LLM 调度观测 ─────────────────────────────────────────────


@router.get("/scheduler/status")
async def scheduler_status(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """LLM 公平调度：在飞 / 排队 / 配额。"""
    from backend.kernel.llm_scheduler import get_llm_admission

    return get_llm_admission().status()


# ── 收件箱与日报（0.6 自主运转）─────────────────────────────────


@router.post("/inbox")
async def enqueue_inbox_item(
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """手动派活：给员工投递工单（人话错误，便于 FE 直接展示）。"""
    from backend.core.config import settings
    from backend.kernel.workforce import get_workforce_inbox

    inbox = get_workforce_inbox()
    if inbox is None:
        # 503：服务未装配，不是「空列表」
        hint = (
            "收件箱服务未启用。请确认 agent_kernel_persistence 与 agent_dispatcher_enabled 为 true，"
            "或设置 TAKTON_AIOS_PROFILE=aios-dev 后重启后端。"
        )
        if not bool(getattr(settings, "agent_dispatcher_enabled", True)):
            hint = "派活器已关闭（agent_dispatcher_enabled=false）。打开后重启即可派工单。"
        raise HTTPException(status_code=503, detail=hint)
    reg = _identity_registry()
    if reg is None:
        raise HTTPException(
            status_code=503,
            detail="员工编制层未启用（identity layer）。请打开 agent_kernel_enabled / persistence 后重试。",
        )
    iid = str(body.get("identity_id") or "").strip()
    if not iid:
        raise HTTPException(status_code=400, detail="请先选择要派活的员工（identity_id 不能为空）")
    instruction = str(body.get("instruction") or "").strip()
    if not instruction:
        raise HTTPException(status_code=400, detail="工单指令不能为空，请写清要员工做什么")

    ident = await reg.get(iid)
    if ident is None:
        raise HTTPException(
            status_code=404,
            detail="找不到该员工。可能已被归档，或列表已过期——请刷新员工列表后重选。",
        )
    if getattr(ident, "status", None) and ident.status != "active":
        raise HTTPException(
            status_code=400,
            detail=f"员工「{ident.name}」当前状态为 {ident.status}，无法接单。请先复职（resume）再派活。",
        )
    # 归属校验：有 user_id 的身份只能由主人投递
    if ident.user_id is not None and str(ident.user_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail=f"无权向员工「{ident.name}」派活（归属他人）")

    source = str(body.get("source") or "manual").strip() or "manual"
    # UI 派活统一记 manual（inbox 允许 cron/webhook/api/manual）
    if source not in ("cron", "webhook", "api", "manual"):
        source = "manual"
    try:
        item = await inbox.enqueue(
            iid,
            instruction,
            source=source,
            payload=body.get("payload"),
            priority=int(body.get("priority") or 0),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if item is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"工单被拒收：员工「{ident.name}」可能已停用/归档，"
                "或收件箱溢出策略丢弃了本单。请检查员工状态与队列深度。"
            ),
        )
    return {
        "id": str(item.id),
        "status": item.status,
        "identity_id": str(item.identity_id),
        "identity_name": ident.name,
        "message": f"已派给「{ident.name}」，状态 {item.status}（dispatcher 将自动领取）",
    }


@router.get("/inbox")
async def list_inbox_items(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    identity_id: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    from backend.kernel.workforce import get_workforce_inbox

    inbox = get_workforce_inbox()
    if inbox is None:
        raise HTTPException(
            status_code=503,
            detail="收件箱未启用。打开 dispatcher/persistence 或 TAKTON_AIOS_PROFILE=aios-dev 后重启。",
        )
    items = await inbox.list_items(identity_id=identity_id, status=status, limit=limit)
    return {
        "items": [
            {
                "id": str(i.id),
                "identity_id": str(i.identity_id),
                "source": i.source,
                "instruction": i.instruction[:300],
                "status": i.status,
                "attempts": i.attempts,
                "result": (i.result or "")[:500],
                "error": (i.error or "")[:300],
                "process_id": getattr(i, "process_id", None),
                "created_at": i.created_at.isoformat() if i.created_at else None,
                "finished_at": i.finished_at,
            }
            for i in items
        ],
        "total": len(items),
    }


@router.get("/inbox/dead")
async def list_dead_letters(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    limit: int = Query(50, ge=1, le=200),
):
    """死信台：达最大重试仍失败的工单（status=dead，兼容 failed）。"""
    from backend.kernel.workforce import get_workforce_inbox

    inbox = get_workforce_inbox()
    if inbox is None:
        raise HTTPException(status_code=503, detail="收件箱未启用")
    dead = await inbox.list_items(status="dead", limit=limit)
    failed = await inbox.list_items(status="failed", limit=limit)
    # 合并去重
    seen: set[str] = set()
    items = []
    for i in list(dead) + list(failed):
        sid = str(i.id)
        if sid in seen:
            continue
        seen.add(sid)
        items.append(i)
    items = items[:limit]
    return {
        "items": [
            {
                "id": str(i.id),
                "identity_id": str(i.identity_id),
                "instruction": (i.instruction or "")[:400],
                "status": i.status,
                "attempts": i.attempts,
                "error": (i.error or "")[:500],
                "process_id": getattr(i, "process_id", None),
                "finished_at": i.finished_at,
            }
            for i in items
        ],
        "total": len(items),
    }


@router.post("/inbox/{item_id}/requeue")
async def requeue_inbox_item(
    item_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """死信/失败工单重放 → pending，dispatcher 会再次领取。"""
    from backend.kernel.workforce import get_workforce_inbox

    inbox = get_workforce_inbox()
    if inbox is None:
        raise HTTPException(status_code=503, detail="收件箱未启用")
    item = await inbox.requeue(item_id, reset_attempts=True)
    if item is None:
        raise HTTPException(status_code=404, detail="工单不存在")
    return {
        "id": str(item.id),
        "status": item.status,
        "attempts": item.attempts,
        "message": "已重放为 pending，dispatcher 将自动领取",
    }


@router.post("/inbox/{item_id}/discard")
async def discard_inbox_item(
    item_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """丢弃死信（dropped）。"""
    from backend.kernel.workforce import get_workforce_inbox

    inbox = get_workforce_inbox()
    if inbox is None:
        raise HTTPException(status_code=503, detail="收件箱未启用")
    ok = await inbox.discard_dead(item_id)
    if not ok:
        raise HTTPException(status_code=400, detail="仅 dead/failed 可丢弃，或工单不存在")
    return {"discarded": True, "id": item_id}


@router.get("/jobs/running")
async def list_running_jobs(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """统一「现在在跑什么」：claimed 工单 + 非终态 kernel process。"""
    from backend.kernel.workforce import get_workforce_inbox

    kernel = get_kernel()
    inbox = get_workforce_inbox()
    claimed = []
    if inbox is not None:
        for i in await inbox.list_items(status="claimed", limit=50):
            pid = getattr(i, "process_id", None)
            claimed.append(
                {
                    "kind": "inbox",
                    "id": str(i.id),
                    "job_id": str(i.id),  # Run 关联：工单 ID
                    "identity_id": str(i.identity_id),
                    "instruction": (i.instruction or "")[:200],
                    "status": i.status,
                    "process_id": pid,
                    "attempts": i.attempts,
                    # 统一关联模型（chat/inbox/process）
                    "run_ref": {
                        "job_id": str(i.id),
                        "process_id": str(pid) if pid else None,
                        "identity_id": str(i.identity_id),
                        "source": "inbox",
                    },
                }
            )
    live_procs = []
    for p in kernel.list_processes(include_terminal=False):
        d = p.to_dict() if hasattr(p, "to_dict") else {}
        st = d.get("state") or getattr(p, "state", "")
        if st in ("completed", "failed", "killed", "interrupted", "exited", "done"):
            continue
        meta = d.get("meta") or getattr(p, "meta", None) or {}
        if not isinstance(meta, dict):
            meta = {}
        job_id = meta.get("inbox_item_id") or meta.get("job_id")
        identity_id = meta.get("identity_id")
        pid = d.get("id") or getattr(p, "id", None)
        live_procs.append(
            {
                "kind": "process",
                "id": pid,
                "process_id": pid,
                "identity": d.get("identity") or getattr(p, "identity_key", None),
                "state": st,
                "session_id": d.get("session_id"),
                "tokens_used": d.get("tokens_used", 0),
                "job_id": job_id,
                "identity_id": identity_id,
                "run_ref": {
                    "process_id": str(pid) if pid else None,
                    "job_id": str(job_id) if job_id else None,
                    "session_id": d.get("session_id"),
                    "identity_id": str(identity_id) if identity_id else None,
                    "source": "kernel_process",
                },
            }
        )
    return {
        "inbox_claimed": claimed,
        "processes": live_procs,
        "total": len(claimed) + len(live_procs),
        "run_model": "job_id|process_id|session_id|identity_id",
    }


@router.get("/workspace/brief")
async def workspace_brief(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    hours: int = Query(24, ge=1, le=24 * 14),
):
    """AI 公司「晨报」聚合：完成/失败/待办/在跑/待批 + 最近产出。

    供驾驶舱 Workspace 与 CLI 一请求消费；不替代明细 API。
    """
    import time as _time

    from backend.kernel.workforce import build_daily_report, get_workforce_inbox

    kernel = get_kernel()
    inbox = get_workforce_inbox()
    reg = _identity_registry()
    if reg is not None and (
        not hasattr(kernel, "identity_registry") or kernel.identity_registry is None
    ):
        try:
            kernel.identity_registry = reg  # type: ignore[attr-defined]
        except Exception:
            pass

    crew = []
    if reg is not None:
        try:
            for ident in await reg.list(status="active"):
                crew.append(
                    {
                        "id": str(ident.id),
                        "name": ident.name,
                        "role": ident.role,
                        "status": ident.status,
                    }
                )
        except Exception:
            crew = []

    report: dict[str, Any] = {}
    if inbox is not None:
        try:
            report = await build_daily_report(kernel, inbox, hours=hours, identity_id=None)
        except Exception as e:
            logger.debug("workspace brief report: %s", e)
            report = {}

    stats = (report.get("inbox") or {}).get("stats") or {}
    done = int(stats.get("done") or 0)
    failed = int(stats.get("failed") or 0) + int(stats.get("dead") or 0)
    pending = int(stats.get("pending") or 0)
    claimed = int(stats.get("claimed") or 0)

    live_jobs = 0
    live_names: list[str] = []
    if inbox is not None:
        try:
            for i in await inbox.list_items(status="claimed", limit=20):
                live_jobs += 1
                nm = next((c["name"] for c in crew if c["id"] == str(i.identity_id)), None)
                if nm and nm not in live_names:
                    live_names.append(nm)
        except Exception:
            pass
    for p in kernel.list_processes(include_terminal=False):
        d = p.to_dict() if hasattr(p, "to_dict") else {}
        st = d.get("state") or ""
        if st in ("completed", "failed", "killed", "interrupted", "exited", "done"):
            continue
        ident = d.get("identity") or ""
        if ident and ident not in live_names and not str(ident).startswith("sub:"):
            live_names.append(str(ident)[:32])

    esc_pending = len(kernel.list_escalations(status="pending"))
    evo_pending = 0
    try:
        from backend.kernel.workforce import get_evolution_engine

        eng = get_evolution_engine()
        if eng is not None and hasattr(eng, "list_proposals"):
            props = await eng.list_proposals(status="pending")
            evo_pending = len(props or [])
    except Exception:
        evo_pending = 0

    recent_done = (report.get("inbox") or {}).get("recent_done") or []
    recent_failed = (report.get("inbox") or {}).get("recent_failed") or []

    return {
        "kind": "workspace_brief",
        "hours": hours,
        "ts": _time.time(),
        "headline": {
            "crew_active": len(crew),
            "jobs_done": done,
            "jobs_failed": failed,
            "jobs_pending": pending,
            "jobs_running": max(claimed, live_jobs),
            "approvals_pending": esc_pending + evo_pending,
            "escalations_pending": esc_pending,
            "evolution_pending": evo_pending,
        },
        "running_employees": live_names[:12],
        "recent_done": recent_done[:8],
        "recent_failed": recent_failed[:5],
        "crew": crew[:24],
        "product_concepts": ["employee", "job", "approval"],
        "narrative": {
            "zh": (
                f"近 {hours}h：完成 {done} 单 · 失败/死信 {failed} · "
                f"在跑 {max(claimed, live_jobs)} · 待批 {esc_pending + evo_pending}"
            ),
            "en": (
                f"Last {hours}h: {done} done · {failed} failed · "
                f"{max(claimed, live_jobs)} running · {esc_pending + evo_pending} awaiting you"
            ),
        },
    }


@router.post("/jobs/stop")
async def stop_job(
    body: StopJobBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """E4 统一停止语义：取消 process + agent loop + 工单 cancelled。

    优先走 dispatcher.cancel_job；无 dispatcher 时仍能 kill process / cancel 工单。
    """
    from backend.kernel.workforce import get_workforce_dispatcher, get_workforce_inbox

    item_id = (body.inbox_item_id or "").strip() or None
    process_id = (body.process_id or "").strip() or None
    if not item_id and not process_id:
        raise HTTPException(
            status_code=400,
            detail="请提供 inbox_item_id 或 process_id（至少一个）",
        )

    reason = (body.reason or "stopped by user").strip()[:500]
    disp = get_workforce_dispatcher()
    if disp is not None:
        result = await disp.cancel_job(
            item_id=item_id, process_id=process_id, reason=reason
        )
        return {"ok": bool(result.get("ok")), **result}

    # 降级：无 dispatcher 时直接 kill + cancel
    out: dict[str, Any] = {
        "ok": False,
        "inbox_item_id": item_id,
        "process_id": process_id,
        "loop_stopped": False,
        "task_cancelled": False,
        "process_killed": False,
        "inbox_cancelled": False,
        "reason": reason,
        "fallback": True,
    }
    kernel = get_kernel()
    if process_id:
        try:
            ended = await kernel.end_process(
                process_id, state="killed", reason=reason
            )
            out["process_killed"] = ended is not None
        except Exception as e:
            logger.debug("stop_job end_process: %s", e)
    inbox = get_workforce_inbox()
    if item_id and inbox is not None:
        try:
            cancelled = await inbox.cancel(
                item_id, reason=reason, process_id=process_id
            )
            out["inbox_cancelled"] = cancelled is not None
        except Exception as e:
            logger.debug("stop_job inbox.cancel: %s", e)
    out["ok"] = bool(out["process_killed"] or out["inbox_cancelled"])
    return out


async def _load_report_read_at() -> float | None:
    try:
        from backend.repositories.setting_repo import AsyncSettingRepository

        row = await AsyncSettingRepository().get_by_key(_REPORT_READ_KEY)
        if row is None or row.value is None:
            return None
        return float(row.value)
    except Exception as e:
        logger.debug("report read_at load: %s", e)
        return None


@router.get("/workforce/report")
async def workforce_report(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    hours: int = Query(24, ge=1, le=24 * 90),
    identity_id: str | None = Query(None, description="只看该员工的汇报"),
    identity_name: str | None = Query(None, description="按员工名过滤（与 id 二选一）"),
):
    """「你不在的这段时间」——workforce 工作汇报。

    不传 identity → 全队；传 identity_id/name → 单员工（各聊天会话内容应不同）。
    含 marked_read_at / has_unread（日报一键已读）。
    """
    from backend.kernel.workforce import build_daily_report, get_workforce_inbox

    inbox = get_workforce_inbox()
    if inbox is None:
        raise HTTPException(
            status_code=503,
            detail="日报依赖收件箱服务。请启用 workforce dispatcher 后重试。",
        )
    scope = (identity_id or "").strip() or None
    name = (identity_name or "").strip()
    if not scope and name:
        reg = _identity_registry()
        if reg is not None:
            for ident in await reg.list(status=None):
                if ident.name == name:
                    scope = str(ident.id)
                    break
    kernel = get_kernel()
    # 给 report 拼名字用
    if not hasattr(kernel, "identity_registry") or getattr(kernel, "identity_registry", None) is None:
        try:
            kernel.identity_registry = _identity_registry()  # type: ignore[attr-defined]
        except Exception:
            pass
    report = await build_daily_report(kernel, inbox, hours=hours, identity_id=scope)
    read_at = await _load_report_read_at()
    report["marked_read_at"] = read_at
    # 有完成/失败且晚于已读时间 → 未读
    has_unread = False
    try:
        recent = list(report.get("inbox", {}).get("recent_done") or []) + list(
            report.get("inbox", {}).get("recent_failed") or []
        )
        if read_at is None:
            has_unread = len(recent) > 0 or int(
                (report.get("inbox") or {}).get("stats", {}).get("done") or 0
            ) > 0
        else:
            for it in recent:
                fa = it.get("finished_at")
                if fa is not None and float(fa) > float(read_at):
                    has_unread = True
                    break
    except Exception:
        has_unread = False
    report["has_unread"] = has_unread
    return report


@router.post("/workforce/report/read")
async def mark_workforce_report_read(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """日报/周报一键已读：写入 settings.workforce_report_read_at。"""
    now = time.time()
    try:
        from backend.repositories.setting_repo import AsyncSettingRepository

        await AsyncSettingRepository().upsert(
            _REPORT_READ_KEY,
            now,
            category="workforce",
            description="日报最后已读时间（unix ts）",
        )
    except Exception as e:
        logger.warning("mark report read failed: %s", e)
        raise HTTPException(status_code=500, detail=f"无法写入已读状态: {e}") from e
    return {"ok": True, "marked_read_at": now}


@router.post("/workforce/seed-template-crew")
async def seed_template_crew_api(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """编制为空时一键预置模板员工（小白/研究员/工程师，幂等）。"""
    from backend.scripts.seed_template_crew import seed_template_crew

    reg = _identity_registry()
    if reg is None:
        raise HTTPException(status_code=503, detail="身份注册表未启用")
    result = await seed_template_crew(reg)
    return result


@router.get("/policy/decisions")
async def list_policy_decisions(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    limit: int = Query(100, ge=1, le=500),
    outcome: str | None = Query(None, description="allow|deny|escalate"),
):
    """0.5.2 权限一张网：policy.decision 事件只读列表。

    who / what / outcome / source 来自 kernel 事件流（与 mediation 并存）。
    """
    kernel = get_kernel()
    events = kernel.events(kind="policy.decision", limit=limit)
    items = []
    for e in events:
        d = e.to_dict() if hasattr(e, "to_dict") else dict(e)
        detail = d.get("detail") or d.get("data") or {}
        if isinstance(detail, dict):
            oc = detail.get("outcome")
            if outcome and oc != outcome:
                continue
            items.append(
                {
                    "ts": d.get("ts"),
                    "process_id": d.get("process_id"),
                    "hash": d.get("hash"),
                    "who": detail.get("who"),
                    "what": detail.get("what"),
                    "action": detail.get("action"),
                    "target": detail.get("target"),
                    "outcome": oc,
                    "reason": detail.get("reason"),
                    "source": detail.get("source", "kernel"),
                }
            )
        else:
            items.append(d)
    return {"decisions": items, "total": len(items)}


@router.post("/backup/export")
async def export_aios_backup(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """0.6 L4：一键导出编制/工单/记忆/审计摘要（私有线防盘挂）。

    返回 JSON 快照（不含明文密钥）。大库时条目有上限截断。
    """
    import time as _time

    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.core.config import settings

    kernel = get_kernel()
    reg = _identity_registry()
    from backend.kernel.workforce import get_workforce_inbox

    inbox = get_workforce_inbox()

    identities_out: list[dict] = []
    memories_out: list[dict] = []
    if reg is not None:
        for ident in await reg.list(status=None):
            identities_out.append(
                {
                    "id": str(ident.id),
                    "name": ident.name,
                    "role": getattr(ident, "role", "") or "",
                    "status": getattr(ident, "status", "active"),
                    "capabilities": list(getattr(ident, "capabilities", None) or []),
                    "default_token_budget": getattr(ident, "default_token_budget", None),
                }
            )
            try:
                mems = await reg.current_memory(ident.id)
                for m in mems[:50]:
                    memories_out.append(
                        {
                            "identity_id": str(ident.id),
                            "identity_name": ident.name,
                            "kind": m.kind,
                            "content": (m.content or "")[:2000],
                            "source": getattr(m, "source", None),
                            "version": getattr(m, "version", 1),
                        }
                    )
            except Exception as e:
                logger.debug("backup memory skip %s: %s", ident.name, e)

    inbox_out: list[dict] = []
    if inbox is not None:
        for st in ("pending", "claimed", "done", "failed", "dead"):
            for i in await inbox.list_items(status=st, limit=100):
                inbox_out.append(
                    {
                        "id": str(i.id),
                        "identity_id": str(i.identity_id),
                        "status": i.status,
                        "instruction": (i.instruction or "")[:500],
                        "attempts": i.attempts,
                        "process_id": getattr(i, "process_id", None),
                        "result": (getattr(i, "result", None) or "")[:500]
                        if st in ("done", "failed", "dead")
                        else None,
                        "error": (getattr(i, "error", None) or "")[:300]
                        if st in ("failed", "dead")
                        else None,
                    }
                )

    # 会话摘要（仅当前用户，截断）
    sessions_out: list[dict] = []
    try:
        from backend.models.session import Session as ChatSession

        async with AsyncSessionLocal() as session:
            q = (
                select(ChatSession)
                .where(ChatSession.user_id == current_user.id)
                .order_by(ChatSession.updated_at.desc())
                .limit(200)
            )
            rows = list((await session.execute(q)).scalars().all())
            for s in rows:
                cfg = getattr(s, "config", None) or {}
                sessions_out.append(
                    {
                        "id": str(s.id),
                        "status": str(getattr(s, "status", "") or ""),
                        "identity_hint": (cfg.get("identity") or "")[:120]
                        if isinstance(cfg, dict)
                        else "",
                        "source_hint": (cfg.get("source") or cfg.get("kind") or "")
                        if isinstance(cfg, dict)
                        else "",
                        "updated_at": str(getattr(s, "updated_at", "") or ""),
                    }
                )
    except Exception as e:
        logger.warning("backup sessions: %s", e)

    # 审计尾部
    events_tail = []
    try:
        for e in kernel.events(limit=200):
            events_tail.append(e.to_dict() if hasattr(e, "to_dict") else dict(e))
    except Exception as e:
        logger.debug("backup events: %s", e)

    audit_path = None
    try:
        store = getattr(kernel, "_audit_store", None) or getattr(kernel, "audit_store", None)
        if store is not None:
            audit_path = getattr(store, "path", None)
    except Exception:
        pass

    db_url = str(getattr(settings, "db_url", "") or "")
    # 脱敏：去掉可能的密码
    if "@" in db_url and "://" in db_url:
        try:
            scheme, rest = db_url.split("://", 1)
            if "@" in rest:
                creds, hostpart = rest.rsplit("@", 1)
                db_url_safe = f"{scheme}://***@{hostpart}"
            else:
                db_url_safe = db_url
        except Exception:
            db_url_safe = "redacted"
    else:
        db_url_safe = db_url

    return {
        "exported_at": _time.time(),
        "exported_by": str(current_user.id),
        "version": "0.4.6-alpha-backup-v1",
        "db_url_hint": db_url_safe,
        "audit_path": audit_path,
        "identities": identities_out,
        "identity_memories": memories_out,
        "inbox_items": inbox_out,
        "sessions": sessions_out,
        "kernel_events_tail": events_tail,
        "counts": {
            "identities": len(identities_out),
            "memories": len(memories_out),
            "inbox": len(inbox_out),
            "sessions": len(sessions_out),
            "events": len(events_tail),
        },
    }


# ── 项目组（企业 IM 群：派活聚合进度）──────────────────────────


@router.get("/project-groups")
async def list_project_groups(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models.project_group import ProjectGroup

    async with AsyncSessionLocal() as session:
        q = (
            select(ProjectGroup)
            .where(
                (ProjectGroup.user_id == current_user.id)
                | (ProjectGroup.user_id.is_(None))
            )
            .order_by(ProjectGroup.updated_at.desc())
            .limit(limit)
        )
        if status:
            q = q.where(ProjectGroup.status == status)
        rows = list((await session.execute(q)).scalars().all())
    return {
        "groups": [_project_group_summary(g) for g in rows],
        "total": len(rows),
    }


@router.post("/project-groups")
async def create_project_group(
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """创建项目组：title + members[{id,name}] + tasks[{inbox_item_id,identity_id,name}]。"""
    from backend.database import AsyncSessionLocal
    from backend.models.project_group import ProjectGroup

    title = str(body.get("title") or "").strip() or "项目组"
    members = body.get("members") if isinstance(body.get("members"), list) else []
    tasks = body.get("tasks") if isinstance(body.get("tasks"), list) else []
    created_by = str(body.get("created_by") or "user")
    async with AsyncSessionLocal() as session:
        g = ProjectGroup(
            user_id=current_user.id,
            title=title[:200],
            status="open",
            created_by=created_by[:32],
            members=members,
            tasks=tasks,
            summary=str(body.get("summary") or "")[:2000],
            meta=body.get("meta") if isinstance(body.get("meta"), dict) else {},
        )
        session.add(g)
        await session.commit()
        await session.refresh(g)
        return _project_group_detail(g)


@router.get("/project-groups/{group_id}")
async def get_project_group(
    group_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """项目组详情：成员 + 各工单最新状态（从 inbox 实时拼）。"""
    import uuid as _u
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models.project_group import ProjectGroup
    from backend.kernel.workforce import get_workforce_inbox

    try:
        gid = _u.UUID(str(group_id))
    except Exception as e:
        raise HTTPException(status_code=400, detail="invalid group id") from e

    async with AsyncSessionLocal() as session:
        g = (
            await session.execute(select(ProjectGroup).where(ProjectGroup.id == gid))
        ).scalar_one_or_none()
        if g is None:
            raise HTTPException(status_code=404, detail="project group not found")

    detail = _project_group_detail(g)
    inbox = get_workforce_inbox()
    task_views = []
    # 按 identity 缓存近期工单，避免全表扫
    cache: dict[str, list] = {}
    for t in list(g.tasks or []):
        iid = str(t.get("inbox_item_id") or "")
        ident_id = str(t.get("identity_id") or "")
        row = {
            "inbox_item_id": iid,
            "identity_id": ident_id,
            "identity_name": str(t.get("identity_name") or t.get("name") or ""),
            "status": "unknown",
            "instruction": "",
            "result": "",
            "error": "",
            "finished_at": None,
        }
        if inbox is not None and (ident_id or iid):
            try:
                key = ident_id or "_all"
                if key not in cache:
                    cache[key] = await inbox.list_items(
                        identity_id=ident_id or None, limit=50
                    )
                hit = next((x for x in cache[key] if str(x.id) == iid), None)
                if hit is None and not ident_id:
                    if "_all" not in cache:
                        cache["_all"] = await inbox.list_items(limit=100)
                    hit = next((x for x in cache["_all"] if str(x.id) == iid), None)
                if hit:
                    row["status"] = hit.status
                    row["instruction"] = (hit.instruction or "")[:400]
                    row["result"] = (hit.result or "")[:1500]
                    row["error"] = (hit.error or "")[:400]
                    row["finished_at"] = hit.finished_at
                    if not row["identity_id"]:
                        row["identity_id"] = str(hit.identity_id)
            except Exception:
                pass
        task_views.append(row)
    detail["task_views"] = task_views
    stats: dict[str, int] = {}
    for tv in task_views:
        st = tv.get("status") or "unknown"
        stats[st] = stats.get(st, 0) + 1
    detail["progress"] = stats
    return detail


@router.post("/project-groups/{group_id}/tasks")
async def attach_project_group_tasks(
    group_id: str,
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """向项目组追加工单引用。"""
    import uuid as _u
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models.project_group import ProjectGroup

    try:
        gid = _u.UUID(str(group_id))
    except Exception as e:
        raise HTTPException(status_code=400, detail="invalid group id") from e
    tasks_in = body.get("tasks") if isinstance(body.get("tasks"), list) else []
    async with AsyncSessionLocal() as session:
        g = (
            await session.execute(select(ProjectGroup).where(ProjectGroup.id == gid))
        ).scalar_one_or_none()
        if g is None:
            raise HTTPException(status_code=404, detail="project group not found")
        cur = list(g.tasks or [])
        seen = {str(t.get("inbox_item_id")) for t in cur}
        for t in tasks_in:
            iid = str(t.get("inbox_item_id") or "")
            if not iid or iid in seen:
                continue
            cur.append(
                {
                    "inbox_item_id": iid,
                    "identity_id": str(t.get("identity_id") or ""),
                    "identity_name": str(t.get("identity_name") or t.get("name") or ""),
                }
            )
            seen.add(iid)
        g.tasks = cur
        if isinstance(body.get("members"), list) and body["members"]:
            g.members = body["members"]
        await session.commit()
        await session.refresh(g)
        return _project_group_detail(g)


def _project_group_summary(g) -> dict:
    tasks = list(g.tasks or [])
    members = list(g.members or [])
    return {
        "id": str(g.id),
        "title": g.title,
        "status": g.status,
        "created_by": g.created_by,
        "member_count": len(members),
        "task_count": len(tasks),
        "summary": (g.summary or "")[:200],
        "created_at": g.created_at.isoformat() if g.created_at else None,
        "updated_at": g.updated_at.isoformat() if g.updated_at else None,
    }


def _project_group_detail(g) -> dict:
    d = _project_group_summary(g)
    d["members"] = list(g.members or [])
    d["tasks"] = list(g.tasks or [])
    d["meta"] = g.meta if isinstance(g.meta, dict) else {}
    return d


# ── 受控进化（0.7）───────────────────────────────────────────


def _proposal_dict(p) -> dict:
    return {
        "id": str(p.id),
        "identity_id": str(p.identity_id),
        "kind": p.kind,
        "title": p.title,
        "rationale": p.rationale,
        "payload": p.payload or {},
        "status": p.status,
        "resolved_by": p.resolved_by,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "applied_at": p.applied_at,
        "rolled_back_at": p.rolled_back_at,
    }


@router.get("/evolution/proposals")
async def list_evolution_proposals(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    identity_id: str | None = Query(None),
    status: str | None = Query(None),
):
    from backend.kernel.workforce import get_evolution_engine

    eng = get_evolution_engine()
    if eng is None:
        raise HTTPException(status_code=503, detail="evolution engine 未启用")
    items = await eng.list_proposals(identity_id=identity_id, status=status)
    return {"proposals": [_proposal_dict(p) for p in items], "total": len(items)}


@router.post("/evolution/analyze")
async def run_evolution_analyze(
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """手动触发述职分析（生成 pending 建议，永不自动应用）。"""
    from backend.kernel.workforce import get_evolution_engine

    eng = get_evolution_engine()
    if eng is None:
        raise HTTPException(status_code=503, detail="evolution engine 未启用")
    try:
        proposals = await eng.analyze(str(body.get("identity_id") or ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"generated": len(proposals), "proposals": [_proposal_dict(p) for p in proposals]}


@router.post("/evolution/proposals/{proposal_id}/approve")
async def approve_evolution(
    proposal_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    from backend.kernel.workforce import get_evolution_engine

    eng = get_evolution_engine()
    if eng is None:
        raise HTTPException(status_code=503, detail="evolution engine 未启用")
    try:
        p = await eng.approve(proposal_id, by=str(current_user.id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"apply failed: {e}") from e
    return _proposal_dict(p)


@router.post("/evolution/proposals/{proposal_id}/reject")
async def reject_evolution(
    proposal_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    from backend.kernel.workforce import get_evolution_engine

    eng = get_evolution_engine()
    if eng is None:
        raise HTTPException(status_code=503, detail="evolution engine 未启用")
    try:
        p = await eng.reject(proposal_id, by=str(current_user.id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _proposal_dict(p)


@router.post("/evolution/proposals/{proposal_id}/rollback")
async def rollback_evolution(
    proposal_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    from backend.kernel.workforce import get_evolution_engine

    eng = get_evolution_engine()
    if eng is None:
        raise HTTPException(status_code=503, detail="evolution engine 未启用")
    try:
        p = await eng.rollback(proposal_id, by=str(current_user.id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _proposal_dict(p)


@router.get("/workforce/org")
async def workforce_org(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """汇报线观察 + 组织预算聚合（从 parent 链涌现，只读视图）。"""
    from backend.database import AsyncSessionLocal
    from backend.kernel.workforce import build_org_view

    return await build_org_view(AsyncSessionLocal)


@router.get("/approval-rules")
async def get_approval_rules_api(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """只读：当前生效的审批规则（与前端 settings/approval_rules 同源）。"""
    from backend.kernel.approval_rules import load_approval_rules

    rules = await load_approval_rules()
    return {"rules": rules, "total": len(rules)}
