"""Auto-write identity experience memory when a work order completes.

完整门禁收口到 CrewMemoryWriter；本模块保持 inbox 兼容入口。
默认 auto_distill=false → complete 路径不写；手动 API 走 force=True。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def record_job_experience(
    *,
    identity_id: Any,
    instruction: str,
    result: str,
    process_id: str | None = None,
    status: str = "done",
    force: bool = False,
    approved_by: str | None = None,
) -> Any | None:
    """Best-effort: 委托 CrewMemoryWriter。永不向调用方抛业务异常。"""
    if not identity_id:
        return None
    try:
        from backend.kernel.crew_memory import get_crew_memory_writer

        writer = get_crew_memory_writer()
        return await writer.maybe_distill_from_job(
            identity_id=identity_id,
            instruction=instruction,
            result=result,
            process_id=process_id,
            status=status,
            force=force,
            approved_by=approved_by,
        )
    except Exception as e:
        logger.debug("experience_sink skipped: %s", e)
        return None
