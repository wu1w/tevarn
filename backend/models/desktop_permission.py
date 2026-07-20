"""
Desktop Permission 数据模型
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, String, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from backend.models.base import Base


class DesktopPermission(Base):
    """桌面权限模型"""
    
    __tablename__ = "desktop_permissions"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    operation = Column(String(50), nullable=False)  # screenshot, click, type, etc.
    app_name = Column(String(255), nullable=True)   # 特定应用，NULL 表示所有应用
    level = Column(String(20), nullable=False)      # allow_once, allow_session, always_allow
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    # 索引
    __table_args__ = (
        Index('idx_desktop_permissions_user_operation', 'user_id', 'operation', 'app_name'),
    )
    
    def to_dict(self):
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "operation": self.operation,
            "app_name": self.app_name,
            "level": self.level,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
