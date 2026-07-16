import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class WorkflowExecutionRead(BaseModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    trigger: str
    status: str
    error: Optional[str] = None
    result: Optional[dict[str, Any]] = None
    started_at: datetime
    finished_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    invoked_by: Optional[uuid.UUID] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
