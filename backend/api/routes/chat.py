"""OpenAI-compatible chat completions API (SSE stream)"""
import asyncio
import json
import logging
import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.agent import NexusAgentLoop
from backend.api.dependencies import (
    get_current_user, get_session_repo, get_message_repo,
    get_ctx_item_repo, get_context_flow_repo, get_task_repo, get_notification_repo,
)
from backend.schemas.user import UserRead

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["Chat"])


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "default"
    messages: list[ChatMessage]
    stream: bool = True
    session_id: Optional[str] = None


@router.post("/chat/completions")
async def chat_completion(
    data: ChatCompletionRequest,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    session_repo=Depends(get_session_repo),
    message_repo=Depends(get_message_repo),
    task_repo=Depends(get_task_repo),
    ctx_item_repo=Depends(get_ctx_item_repo),
    context_flow_repo=Depends(get_context_flow_repo),
    notification_repo=Depends(get_notification_repo),
):
    """OpenAI-compatible /v1/chat/completions endpoint with SSE streaming."""
    # Auto-create or get session
    if data.session_id:
        try:
            sid = uuid.UUID(data.session_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid session_id")
    else:
        uid = uuid.UUID(current_user.id) if isinstance(current_user.id, str) else current_user.id
        session = await session_repo.create({"user_id": uid, "config": {}})
        sid = session.id

    user_messages = [m for m in data.messages if m.role == "user"]
    if not user_messages:
        raise HTTPException(status_code=400, detail="No user message found")
    user_input = user_messages[-1].content

    uid = uuid.UUID(current_user.id) if isinstance(current_user.id, str) else current_user.id
    agent = NexusAgentLoop(
        session_repo=session_repo, message_repo=message_repo,
        task_repo=task_repo, ctx_item_repo=ctx_item_repo,
        context_flow_repo=context_flow_repo, ws_manager=None,
        notification_repo=notification_repo, user_id=uid,
    )
    from backend.core.config import settings as app_settings
    agent.max_iterations = int(getattr(app_settings, "agent_max_iterations", 25) or 25)

    async def event_stream():
        try:
            result = await agent.run(sid, user_input, mode="default")
            chunk = {
                "id": f"chatcmpl-{uuid.uuid4().hex}",
                "object": "chat.completion.chunk",
                "created": int(asyncio.get_event_loop().time()),
                "model": "takton",
                "choices": [{
                    "index": 0,
                    "delta": {"role": "assistant", "content": result or ""},
                    "finish_reason": "stop",
                }]
            }
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            logger.exception(f"Chat completion error: {e}")
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
