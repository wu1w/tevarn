"""
冒烟测试专用路由
提供同步 HTTP 接口，方便后端直接调用模拟用户对话，
不走 WebSocket / SSE，直接返回完整结果 + 上下文压缩元数据 + RAG 信息。
"""

import asyncio
import json
import logging
import uuid
import time
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.agent import NexusAgentLoop
from backend.api.dependencies import (
    get_current_user,
    get_session_repo,
    get_message_repo,
    get_ctx_item_repo,
    get_context_flow_repo,
    get_task_repo,
    get_notification_repo,
)
from backend.schemas.user import UserRead

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/test", tags=["SmokeTest"])


class SmokeChatRequest(BaseModel):
    """冒烟测试对话请求"""
    message: str = Field(..., description="用户输入消息")
    session_id: Optional[str] = Field(None, description="会话ID，不传则自动创建")
    mode: str = Field("default", description="对话模式: default/goal/search/ppt/report")


class SmokeChatResponse(BaseModel):
    """冒烟测试对话响应"""
    session_id: str
    assistant_reply: str
    elapsed_seconds: float
    context_compressed: bool = False
    compress_layers: list[str] = []
    compress_dropped: int = 0
    compress_tokens_before: int = 0
    compress_tokens_after: int = 0
    rag_injected: bool = False
    rag_chars: int = 0
    error: Optional[str] = None


class SmokeHistoryResponse(BaseModel):
    """历史消息响应"""
    session_id: str
    messages: list[dict]
    total_count: int


@router.post("/chat", response_model=SmokeChatResponse)
async def smoke_chat(
    data: SmokeChatRequest,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    session_repo=Depends(get_session_repo),
    message_repo=Depends(get_message_repo),
    task_repo=Depends(get_task_repo),
    ctx_item_repo=Depends(get_ctx_item_repo),
    context_flow_repo=Depends(get_context_flow_repo),
    notification_repo=Depends(get_notification_repo),
):
    """
    冒烟测试对话接口（同步返回完整回复）

    - 自动创建或复用 session
    - 返回完整 assistant 回复
    - 附带上下文压缩元数据和 RAG 注入信息
    """
    uid = uuid.UUID(current_user.id) if isinstance(current_user.id, str) else current_user.id

    # 创建或复用 session
    if data.session_id:
        try:
            sid = uuid.UUID(data.session_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid session_id")
    else:
        session = await session_repo.create({"user_id": uid, "config": {}})
        sid = session.id

    # 初始化 Agent Loop（不传 ws_manager，纯后端调用）
    agent = NexusAgentLoop(
        session_repo=session_repo,
        message_repo=message_repo,
        task_repo=task_repo,
        ctx_item_repo=ctx_item_repo,
        context_flow_repo=context_flow_repo,
        ws_manager=None,
        notification_repo=notification_repo,
        user_id=uid,
    )

    start = time.monotonic()
    try:
        result = await agent.run(sid, data.message, mode=data.mode)
        elapsed = time.monotonic() - start
    except Exception as e:
        logger.exception(f"Smoke test chat error: {e}")
        elapsed = time.monotonic() - start
        return SmokeChatResponse(
            session_id=str(sid),
            assistant_reply="",
            elapsed_seconds=round(elapsed, 2),
            error=str(e),
        )

    # 从日志中提取压缩和 RAG 元数据
    compressed, layers, dropped, tok_before, tok_after, rag_inj, rag_ch = (
        False, [], 0, 0, 0, False, 0
    )

    # 读取最近日志提取上下文压缩信息
    try:
        log_path = "C:/Users/developer/.takton/logs/takton.log"
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()[-200:]  # 只看最近 200 行
        for line in lines:
            if str(sid) in line:
                if "上下文已压缩" in line or "Context compressed" in line:
                    compressed = True
                if "Injected RAG context" in line:
                    rag_inj = True
                    # 提取字符数: "Injected RAG context (132 chars)"
                    import re
                    m = re.search(r"\((\d+)\s*chars\)", line)
                    if m:
                        rag_ch = int(m.group(1))
    except Exception:
        pass

    return SmokeChatResponse(
        session_id=str(sid),
        assistant_reply=result or "",
        elapsed_seconds=round(elapsed, 2),
        context_compressed=compressed,
        compress_layers=layers,
        compress_dropped=dropped,
        compress_tokens_before=tok_before,
        compress_tokens_after=tok_after,
        rag_injected=rag_inj,
        rag_chars=rag_ch,
    )


@router.get("/history/{session_id}", response_model=SmokeHistoryResponse)
async def smoke_history(
    session_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    message_repo=Depends(get_message_repo),
):
    """
    获取会话历史消息（冒烟测试用）

    返回所有 user/assistant 消息，方便验证回调是否正确
    """
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session_id")

    messages = await message_repo.get_history_by_session(sid, limit=200)
    result = []
    for m in messages:
        if m.role in ("user", "assistant"):
            result.append({
                "id": str(m.id),
                "role": m.role,
                "content": (m.content or "")[:500],  # 截断防止过长
                "created_at": m.created_at.isoformat() if hasattr(m, "created_at") else "",
            })

    return SmokeHistoryResponse(
        session_id=session_id,
        messages=result,
        total_count=len(result),
    )


@router.post("/session", response_model=dict)
async def smoke_create_session(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    session_repo=Depends(get_session_repo),
):
    """创建一个新的测试会话，返回 session_id"""
    uid = uuid.UUID(current_user.id) if isinstance(current_user.id, str) else current_user.id
    session = await session_repo.create({"user_id": uid, "config": {}})
    return {"session_id": str(session.id)}


@router.get("/rag-status")
async def smoke_rag_status():
    """查看当前 RAG 状态"""
    try:
        from backend.services.rag.capability import get_rag_status
        st = get_rag_status()
        return {
            "auto_inject": st.auto_inject,
            "tool_search": st.tool_search,
            "reason": st.reason,
        }
    except Exception as e:
        return {"auto_inject": False, "tool_search": False, "reason": str(e)}


@router.get("/compress-status")
async def smoke_compress_status():
    """查看上下文压缩引擎状态"""
    try:
        from backend.agent.context_engine import get_context_engine
        from backend.core.config import settings as s
        eng = get_context_engine()
        status = eng.get_status() if hasattr(eng, 'get_status') else {}
        return {
            "engine_type": type(eng).__name__,
            "settings_context_window": getattr(s, "context_window", "MISSING"),
            "settings_threshold_percent": getattr(s, "context_threshold_percent", "MISSING"),
            "engine_status": status,
        }
    except Exception as e:
        return {"error": str(e)}


class InjectMessagesRequest(BaseModel):
    """批量注入历史消息，不调用LLM，用于快速填满上下文"""
    session_id: str = Field(..., description="目标会话ID")
    messages: list[dict] = Field(..., description="消息列表 [{role, content}, ...]")


@router.post("/inject-messages")
async def smoke_inject_messages(
    data: InjectMessagesRequest,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    message_repo=Depends(get_message_repo),
):
    """批量注入历史消息到会话，不触发LLM回复，用于快速构造上下文压力测试"""
    try:
        sid = uuid.UUID(data.session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session_id")

    created = []
    for msg in data.messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        m = await message_repo.create({
            "session_id": sid,
            "role": role,
            "content": content,
        })
        created.append({"id": str(m.id), "role": role, "content_len": len(content)})

    return {"session_id": str(sid), "injected": len(created), "details": created}
