"""
Calendar Read Skill - 读取本地 ICS/JSON 日历（与 calendar 工具共享存储）
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..base import BaseSkill


class CalendarReadSkill(BaseSkill):
    """日历读取 Skill"""

    name = "calendar_read"
    description = (
        "读取日程安排、会议时间。与 calendar 工具共用 ~/.takton/calendar。"
        "写操作请用 calendar action=create|update|delete。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "date": {
                "type": "string",
                "description": "日期（YYYY-MM-DD），默认为今天",
            },
            "days": {
                "type": "integer",
                "description": "查询天数（默认 7）",
                "default": 7,
            },
        },
        "required": [],
    }

    async def execute(self, date: str = "", days: int = 7, **kwargs: Any) -> str:
        try:
            local_today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
        except Exception:
            local_today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        target = date or local_today
        try:
            from backend.tools.builtins.wave_a_tools import CalendarTool

            tool = CalendarTool()
            return await tool.execute(action="list", date=target, days=int(days or 7))
        except Exception as e:
            return f"[Error] calendar_read failed: {e}"
