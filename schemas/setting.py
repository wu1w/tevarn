"""
Setting 相关 Pydantic Schema
"""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator

from backend.core.encryption import mask_setting


class SettingBase(BaseModel):
    key: str = Field(..., max_length=128)
    value: Any
    category: str = Field(default="general", max_length=32)
    description: Optional[str] = None


class SettingCreate(SettingBase):
    pass


class SettingUpdate(BaseModel):
    value: Any
    category: Optional[str] = Field(None, max_length=32)
    description: Optional[str] = None


class SettingRead(SettingBase):
    updated_at: datetime

    model_config = {"from_attributes": True}

    @model_validator(mode="after")
    def mask_sensitive_values(self):
        self.value = mask_setting(self.key, self.value)
        return self
