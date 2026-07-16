"""
Context 路由
上下文管理 API：CtxItem CRUD、Token 统计、优化、访问流
严格对应前端 demo 中的所有功能

注意：
1. CtxItem 数据模型未包含 user_id 字段，全局项（session_id 为空）为共享资源；
   会话专属项（session_id 不为空）通过下方 `_ensure_session_owned` 校验会话归属，
   防止越权访问他人会话的上下文项/访问流。
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.core.unit_of_work import UnitOfWork
from backend.repositories import CtxItemRepository, ContextFlowRepository, SessionRepository
from backend.schemas.context import (
    ContextFlowCreate,
    ContextFlowRead,
    ContextOptimizeResult,
    ContextSearchQuery,
    ContextStats,
    CtxItemCreate,
    CtxItemPinToggle,
    CtxItemRead,
    CtxItemUpdate,
)
from backend.schemas.user import UserRead

from ..dependencies import get_current_user, get_ctx_item_repo, get_context_flow_repo, get_session_repo, require_admin

router = APIRouter(prefix="/context", tags=["Context"])


async def _ensure_session_owned(
    session_id: uuid.UUID | None,
    current_user: UserRead,
    session_repo: SessionRepository,
) -> None:
    """校验 session_id 归属当前用户（session_id 为空表示全局资源，无需校验）"""
    if session_id is None:
        return
    session = await session_repo.get_by_id(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if getattr(session, "user_id", None) and session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")


# ────────────────── CtxItem CRUD ──────────────────

@router.get("/items", response_model=list[CtxItemRead])
async def list_ctx_items(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[CtxItemRepository, Depends(get_ctx_item_repo)],
    session_repo: Annotated[SessionRepository, Depends(get_session_repo)],
    session_id: Annotated[uuid.UUID | None, Query()] = None,
    scope: Annotated[str | None, Query()] = None,
    kind: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query()] = None,
    hide_pinned: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """
    列出上下文项
    支持按 session、scope、kind 过滤，支持全文搜索
    """
    await _ensure_session_owned(session_id, current_user, session_repo)
    items = await repo.list_by_session(
        session_id=session_id,
        scope=scope,
        kind=kind,
        search=q,
        hide_pinned=hide_pinned,
        limit=limit,
        offset=offset,
        user_id=current_user.id,
    )
    return items


@router.post("/items", response_model=CtxItemRead, status_code=201)
async def create_ctx_item(
    data: CtxItemCreate,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """创建上下文项（归属校验与创建在同一事务）"""
    async with UnitOfWork() as uow:
        await _ensure_session_owned(data.session_id, current_user, uow.sessions)
        payload = data.model_dump()
        if data.session_id is None and not current_user.is_superuser:
            raise HTTPException(status_code=403, detail="Only admin can create global context items")
        payload["user_id"] = current_user.id if data.session_id is not None else None
        return await uow.ctx_items.create(payload)


@router.get("/items/{item_id}", response_model=CtxItemRead)
async def get_ctx_item(
    item_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[CtxItemRepository, Depends(get_ctx_item_repo)],
    session_repo: Annotated[SessionRepository, Depends(get_session_repo)],
):
    """获取单个上下文项"""
    item = await repo.get_by_id(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="CtxItem not found")
    await _ensure_session_owned(item.session_id, current_user, session_repo)
    return item


@router.put("/items/{item_id}", response_model=CtxItemRead)
async def update_ctx_item(
    item_id: uuid.UUID,
    data: CtxItemUpdate,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """更新上下文项（value、key、tokens、ttl、origin）"""
    async with UnitOfWork() as uow:
        item = await uow.ctx_items.get_by_id(item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="CtxItem not found")
        await _ensure_session_owned(item.session_id, current_user, uow.sessions)
        if item.session_id is None and not current_user.is_superuser:
            raise HTTPException(status_code=403, detail="Only admin can update global context items")
        update_data = data.model_dump(exclude_unset=True)
        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        item = await uow.ctx_items.update(item_id, update_data)
        if item is None:
            raise HTTPException(status_code=404, detail="CtxItem not found")
        return item


@router.delete("/items/{item_id}")
async def delete_ctx_item(
    item_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """删除上下文项"""
    async with UnitOfWork() as uow:
        item = await uow.ctx_items.get_by_id(item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="CtxItem not found")
        await _ensure_session_owned(item.session_id, current_user, uow.sessions)
        if item.session_id is None and not current_user.is_superuser:
            raise HTTPException(status_code=403, detail="Only admin can delete global context items")
        success = await uow.ctx_items.delete(item_id)
        if not success:
            raise HTTPException(status_code=404, detail="CtxItem not found")
        return {"deleted": True}


@router.post("/items/{item_id}/pin", response_model=CtxItemRead)
async def toggle_pin(
    item_id: uuid.UUID,
    data: CtxItemPinToggle,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """切换上下文项的 pinned 状态"""
    async with UnitOfWork() as uow:
        item = await uow.ctx_items.get_by_id(item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="CtxItem not found")
        await _ensure_session_owned(item.session_id, current_user, uow.sessions)
        if item.session_id is None and not current_user.is_superuser:
            raise HTTPException(status_code=403, detail="Only admin can pin global context items")
        item = await uow.ctx_items.toggle_pin(item_id, data.pinned)
        if item is None:
            raise HTTPException(status_code=404, detail="CtxItem not found")
        return item


# ────────────────── Stats & Optimize ──────────────────

@router.get("/stats", response_model=ContextStats)
async def get_context_stats(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[CtxItemRepository, Depends(get_ctx_item_repo)],
    session_repo: Annotated[SessionRepository, Depends(get_session_repo)],
    session_id: Annotated[uuid.UUID | None, Query()] = None,
):
    """获取 Token 统计（对应前端 Token budget donut / By scope bars）"""
    await _ensure_session_owned(session_id, current_user, session_repo)
    if session_id is None and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Only admin can view global context stats")
    stats = await repo.get_stats(session_id)
    return ContextStats(
        total_tokens=stats.get("total_tokens", 0),
        pinned_tokens=stats.get("pinned_tokens", 0),
        session_tokens=stats.get("session_tokens", 0),
        rag_tokens=stats.get("rag_tokens", 0),
        by_scope=stats.get("by_scope", {}),
        item_count=stats.get("item_count", 0),
        context_window=stats.get("context_window", 200_000),
    )


@router.post("/optimize", response_model=ContextOptimizeResult)
async def optimize_context(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[CtxItemRepository, Depends(get_ctx_item_repo)],
    session_repo: Annotated[SessionRepository, Depends(get_session_repo)],
    session_id: Annotated[uuid.UUID | None, Query()] = None,
    threshold: Annotated[float, Query(ge=0.1, le=1.0)] = 0.7,
):
    """
    执行上下文优化（对应前端 "optimize now" 按钮）
    - 裁剪非 pinned 的 stale session messages
    - 合并摘要过期的 doc 项
    """
    await _ensure_session_owned(session_id, current_user, session_repo)
    if session_id is None and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Only admin can optimize global context")
    result = await repo.optimize(session_id, threshold)
    return ContextOptimizeResult(
        saved_tokens=result.get("saved_tokens", 0),
        pruned_count=result.get("pruned_count", 0),
        summarized_count=result.get("summarized_count", 0),
    )


# ────────────────── Context Flows ──────────────────

@router.get("/flows", response_model=list[ContextFlowRead])
async def list_context_flows(
    session_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[ContextFlowRepository, Depends(get_context_flow_repo)],
    session_repo: Annotated[SessionRepository, Depends(get_session_repo)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """分页获取会话的上下文访问流"""
    await _ensure_session_owned(session_id, current_user, session_repo)
    flows = await repo.list_by_session(session_id, limit, offset)
    return flows


@router.get("/flows/recent", response_model=list[ContextFlowRead])
async def get_recent_flows(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[ContextFlowRepository, Depends(get_context_flow_repo)],
    session_repo: Annotated[SessionRepository, Depends(get_session_repo)],
    session_id: Annotated[uuid.UUID | None, Query()] = None,
    hours: Annotated[int, Query(ge=1, le=168)] = 1,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    """获取最近 N 小时的访问流（对应前端 Recent flows）。
    未指定 session_id 时，仅返回当前用户名下会话的访问流，避免跨用户数据泄露。"""
    if session_id is not None:
        await _ensure_session_owned(session_id, current_user, session_repo)
        flows = await repo.get_recent_flows(session_id, hours, limit)
        return flows

    my_sessions = await session_repo.list_by_user(current_user.id)
    my_session_ids = {s.id for s in my_sessions}
    all_flows = await repo.get_recent_flows(None, hours, limit * max(len(my_session_ids), 1))
    return [f for f in all_flows if f.session_id in my_session_ids][:limit]


@router.post("/flows", response_model=ContextFlowRead, status_code=201)
async def create_context_flow(
    data: ContextFlowCreate,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """记录一次上下文访问流（归属校验与写入在同一事务）"""
    async with UnitOfWork() as uow:
        await _ensure_session_owned(data.session_id, current_user, uow.sessions)
        return await uow.context_flows.create_flow(
            session_id=data.session_id,
            agent=data.agent,
            scope=data.scope,
            keys=data.keys,
            tokens=data.tokens,
        )


@router.get("/engine-status")
async def context_engine_status(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """上下文引擎状态（TokenMeter + pipeline 层开关）。"""
    from backend.agent.context_engine import get_context_engine
    from backend.core.config import settings

    eng = get_context_engine()
    st = eng.get_status()
    st["context_compress_model"] = getattr(settings, "context_compress_model", "") or ""
    st["llm_model"] = getattr(settings, "llm_model", "") or ""
    return st


@router.get("/system-layers")
async def get_system_layers(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    session_id: Annotated[uuid.UUID | None, Query()] = None,
    mode: Annotated[str, Query()] = "default",
    platform: Annotated[str | None, Query()] = None,
):
    """本轮 system prompt 分层可视化（core / profile / packages / dynamic / volatile）。

    不跑完整 agent loop，仅按当前会话配置重建分层快照。
    """
    from backend.agent.context import ContextManager
    from backend.agent.system_layers import build_system_layers_report
    from backend.agent.system_prompt import build_system_prompt
    from backend.core.config import settings
    from backend.packages.loader import resolve_attached_snippets
    from backend.packages.session_packages import get_session_attached_packages
    from backend.repositories.context_repo import AsyncCtxItemRepository

    sid = session_id
    identity = None
    user_system_prompt = None
    context_files = None
    memory_block = None
    package_snippets: list[dict[str, str]] = []
    dynamic: list[dict] = []

    ctx_mgr = ContextManager(ctx_item_repo=AsyncCtxItemRepository())
    if sid is not None:
        try:
            data = await ctx_mgr._collect_ctx_items(sid)
            identity = data.get("identity")
            user_system_prompt = data.get("user_system_prompt")
            context_files = data.get("context_files")
            memory_block = data.get("memory_block")
        except Exception:
            pass
        try:
            attached = await get_session_attached_packages(sid)
            package_snippets = await resolve_attached_snippets(attached)
            if attached:
                dynamic.append(
                    {
                        "kind": "packages",
                        "summary": f"attached={len(attached)}: {', '.join(attached[:8])}",
                    }
                )
        except Exception:
            pass

    if not memory_block:
        try:
            from backend.agent.file_context import load_workspace_memory_bundle

            mem_block, _meta = load_workspace_memory_bundle()
            if mem_block:
                memory_block = mem_block
        except Exception:
            pass

    model = getattr(settings, "llm_model", "") or ""
    parts = build_system_prompt(
        identity=identity,
        tools_enabled=["file_read", "command", "memory"],  # 示意启用工具准则
        model=model,
        user_system_prompt=user_system_prompt,
        context_files=context_files,
        platform=platform,
        mode=mode if mode and mode != "default" else None,
        memory_block=memory_block,
        session_id=str(sid) if sid else None,
    )
    if package_snippets:
        pkg_block_parts = []
        for sn in package_snippets:
            name = sn.get("name") or "package"
            icon = sn.get("icon") or "📦"
            body = (sn.get("content") or "").strip()
            if body:
                pkg_block_parts.append(f"### {icon} {name}\n{body}")
        if pkg_block_parts:
            pkg_block = "# Attached Takton Packages\n" + "\n\n".join(pkg_block_parts)
            ctx = parts.get("context") or ""
            parts["context"] = (ctx + "\n\n" + pkg_block).strip() if ctx else pkg_block

    # 提示动态层：实际 cluster/rag 在 loop 注入；此处给出说明位
    dynamic.append(
        {
            "kind": "note",
            "summary": "RAG / 集群名册 / Goal 状态在 agent loop 运行时追加到 messages，不写入 Stable 核心。",
        }
    )

    report = build_system_layers_report(
        parts=parts,
        identity=identity,
        user_system_prompt=user_system_prompt,
        context_files=context_files,
        package_snippets=package_snippets,
        platform=platform,
        mode=mode if mode != "default" else None,
        memory_block=memory_block,
        dynamic_injections=dynamic,
        model=model,
        session_id=str(sid) if sid else None,
    )
    report["session_id"] = str(sid) if sid else None
    report["mode"] = mode
    return report


@router.post("/compact")
async def context_manual_compact(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    session_id: Annotated[uuid.UUID | None, Query()] = None,
    focus_topic: Annotated[str | None, Query()] = None,
):
    """
    手动触发压缩（MVP：对当前引擎状态做 dry 状态返回）。
    完整对话压缩在 agent loop 内自动执行；此接口供仪表盘探测。
    """
    from backend.agent.context_engine import get_context_engine

    eng = get_context_engine()
    return {
        "ok": True,
        "session_id": str(session_id) if session_id else None,
        "focus_topic": focus_topic,
        "engine": eng.get_status(),
        "note": "Live message compaction runs inside the agent loop; use chat to trigger L1/L3/L5.",
    }
