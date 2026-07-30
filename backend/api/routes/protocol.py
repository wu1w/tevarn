"""互操作协议 API：Agent Card · A2A-lite 工单 · 治理/内核面导出。

前缀挂在 /kernel/protocol（与 kernel 路由同前缀域，便于鉴权一致）。
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.kernel import get_kernel
from backend.schemas.user import UserRead

from ..dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kernel/protocol", tags=["protocol"])


class A2ATaskBody(BaseModel):
    """A2A-lite 任务体（也接受简化 instruction 字段）。"""

    message_id: str | None = None
    role: str = "user"
    parts: list[dict[str, Any]] | None = None
    instruction: str | None = None
    content: str | None = None
    text: str | None = None
    identity_id: str | None = None
    identity_name: str | None = None
    priority: int = 0
    source: str = "a2a"
    metadata: dict[str, Any] | None = None


def _identity_registry():
    return get_kernel().identity_registry


def _product_version() -> str:
    from backend.core.version import product_version

    return product_version()


@router.get("/manifest")
async def get_protocol_manifest(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """协议清单：版本、心智三词、互操作端点、non-goals。"""
    from backend.kernel.protocol_spec import protocol_manifest

    return protocol_manifest()


@router.get("/concepts")
async def get_product_concepts(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """产品心智（员工/工单/审批）机器可读 + 工程名映射。"""
    from backend.kernel.protocol_spec import (
        LEGACY_TERM_MAP,
        PRODUCT_CONCEPTS,
        PROTOCOL_VERSION,
    )

    return {
        "kind": "product_concepts",
        "protocol_version": PROTOCOL_VERSION,
        "concepts": PRODUCT_CONCEPTS,
        "legacy_term_map": LEGACY_TERM_MAP,
        "spine": ["employee", "job", "approval"],
        "primary_path_zh": "对话 CEO → 员工/工单 → 需要时审批",
        "primary_path_en": "Chat with steward → employee jobs → approve when needed",
    }


@router.get("/governance")
async def get_governance(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    include_live_rules: bool = False,
):
    """治理骨架：红线、策略预设、内核面。"""
    from backend.kernel.governance import build_governance_export

    live = None
    if include_live_rules:
        try:
            from backend.kernel.approval_rules import load_approval_rules

            live = await load_approval_rules()
        except Exception as e:
            logger.debug("live rules: %s", e)
    return build_governance_export(include_live_rules=include_live_rules, live_rules=live)


@router.get("/surface")
async def get_kernel_surface(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """可研究级 kernel surface 导出。"""
    from backend.kernel.governance import build_kernel_surface_export

    return build_kernel_surface_export()


@router.get("/agent-cards")
async def list_agent_cards(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    status: str | None = "active",
):
    """导出全部（或 active）员工为 Agent Card 列表。"""
    from backend.kernel.protocol_spec import identity_to_agent_card

    reg = _identity_registry()
    if reg is None:
        raise HTTPException(status_code=503, detail="identity layer disabled")
    version = _product_version()
    cards = []
    for ident in await reg.list(status=status):
        mem = []
        try:
            mem = await reg.current_memory(ident.id)
        except Exception:
            mem = []
        cards.append(identity_to_agent_card(ident, memory_entries=mem, version=version).to_dict())
    return {"kind": "agent_card_list", "total": len(cards), "cards": cards}


@router.get("/agent-cards/{identity_id}")
async def get_agent_card(
    identity_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """单个员工 Agent Card。"""
    from backend.kernel.protocol_spec import identity_to_agent_card

    reg = _identity_registry()
    if reg is None:
        raise HTTPException(status_code=503, detail="identity layer disabled")
    try:
        ident = await reg.get(identity_id)
    except Exception:
        ident = None
    if ident is None:
        # 尝试按名
        for i in await reg.list(status=None):
            if str(i.id) == identity_id or i.name == identity_id:
                ident = i
                break
    if ident is None:
        raise HTTPException(status_code=404, detail="identity not found")
    mem = []
    try:
        mem = await reg.current_memory(ident.id)
    except Exception:
        mem = []
    return identity_to_agent_card(ident, memory_entries=mem, version=_product_version()).to_dict()


@router.post("/a2a/tasks")
async def submit_a2a_task(
    body: A2ATaskBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """A2A-lite：外部/内部以标准信封投递 → Inbox 工单。

    成功返回工单 id；员工不存在/停用返回 4xx 人话错误。
    """
    from backend.kernel.protocol_spec import parse_task_envelope
    from backend.kernel.workforce import get_workforce_inbox

    raw = body.model_dump(exclude_none=True)
    if body.metadata:
        raw["metadata"] = body.metadata
    try:
        env = parse_task_envelope(raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    inbox = get_workforce_inbox()
    if inbox is None:
        raise HTTPException(
            status_code=503,
            detail="收件箱未启用。请打开 workforce dispatcher 后再投递 A2A 工单。",
        )
    reg = _identity_registry()
    if reg is None:
        raise HTTPException(status_code=503, detail="identity layer disabled")

    identity_id = env.identity_id
    if not identity_id and env.identity_name:
        for i in await reg.list(status=None):
            if i.name == env.identity_name:
                identity_id = str(i.id)
                break
    if not identity_id:
        raise HTTPException(
            status_code=404,
            detail=f"找不到员工 «{env.identity_name or '?'}»。请先入编再派工单。",
        )

    item = await inbox.enqueue(
        identity_id,
        env.text,
        source="api",
        source_ref=env.message_id,
        payload=dict(env.metadata or {}),
        priority=env.priority,
    )
    if item is None:
        raise HTTPException(
            status_code=400,
            detail="工单被拒收（员工停用/溢出）。请检查员工状态或清理队列。",
        )
    return {
        "ok": True,
        "kind": "a2a_task_accepted",
        "message_id": env.message_id,
        "inbox_item_id": str(item.id),
        "identity_id": str(item.identity_id),
        "status": item.status,
        "product_concept": "job",
        "note": "Mapped to Inbox job; dispatcher will claim when free",
    }
