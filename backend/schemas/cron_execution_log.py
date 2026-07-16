import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class CronExecutionLogRead(BaseModel):
    id: uuid.UUID
    cron_job_id: uuid.UUID
    status: str
    error: Optional[str] = None
    output: Optional[str] = None
    started_at: datetime
    finished_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
