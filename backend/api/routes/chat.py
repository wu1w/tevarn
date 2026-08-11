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
    get_context_flow_repo,
    get_ctx_item_repo,
    get_current_user,
    get_message_repo,
    get_notification_repo,
    get_session_repo,
    get_task_repo,
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
            raise HTTPException(status_code=400, detail="Invalid session_id") from None
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
        """True SSE: stream stream_delta as OpenAI chunks while agent runs."""
        q: asyncio.Queue = asyncio.Queue()
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"

        class _QueueWsManager:
            async def broadcast(self, session_id, message):  # noqa: ANN001
                try:
                    await q.put(message if isinstance(message, dict) else {"type": "raw", "data": message})
                except Exception:
                    pass

        agent.ws_manager = _QueueWsManager()

        async def _runner() -> None:
            try:
                result = await agent.run(sid, user_input, mode="default")
                await q.put({"type": "_done", "result": result or ""})
            except Exception as e:
                logger.exception("Chat completion error: %s", e)
                await q.put({"type": "_error", "detail": str(e)})

        task = asyncio.create_task(_runner())
        role_sent = False
        try:
            while True:
                msg = await q.get()
                mtype = (msg or {}).get("type")
                if mtype == "stream_delta":
                    content = msg.get("content") or ""
                    if not content:
                        continue
                    delta: dict = {"content": content}
                    if not role_sent:
                        delta = {"role": "assistant", "content": content}
                        role_sent = True
                    chunk = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": int(asyncio.get_event_loop().time()),
                        "model": data.model or "tevarn",
                        "choices": [{
                            "index": 0,
                            "delta": delta,
                            "finish_reason": None,
                        }],
                    }
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                elif mtype == "_done":
                    final = msg.get("result") or ""
                    if final and not role_sent:
                        chunk = {
                            "id": completion_id,
                            "object": "chat.completion.chunk",
                            "created": int(asyncio.get_event_loop().time()),
                            "model": data.model or "tevarn",
                            "choices": [{
                                "index": 0,
                                "delta": {"role": "assistant", "content": final},
                                "finish_reason": "stop",
                            }],
                        }
                        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                    else:
                        chunk = {
                            "id": completion_id,
                            "object": "chat.completion.chunk",
                            "created": int(asyncio.get_event_loop().time()),
                            "model": data.model or "tevarn",
                            "choices": [{
                                "index": 0,
                                "delta": {},
                                "finish_reason": "stop",
                            }],
                        }
                        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
                    break
                elif mtype == "_error":
                    yield f"data: {json.dumps({'error': msg.get('detail')}, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
                    break
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except Exception:
                    pass

    return StreamingResponse
return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
