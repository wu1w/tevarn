"""
Calendar Read Skill - 读取日历事件
当前为桩实现，后续可接入 Google Calendar / Outlook / CalDAV
"""

from datetime import datetime

from ..base import BaseSkill


class CalendarReadSkill(BaseSkill):
    """日历读取 Skill"""

    name = "calendar_read"
    description = (
        "当用户询问日程安排、会议时间或需要规划时，"
        "调用此工具读取日历事件。"
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
                "description": "查询天数（默认 1）",
                "default": 1,
            },
        },
        "required": [],
    }

    async def execute(self, date: str = "", days: int = 1) -> str:
        """读取日历事件（桩实现）"""
        target = date or datetime.now().strftime("%Y-%m-%d")
        return (
            f"[Calendar Stub] Date: {target}, Days: {days}\n"
            f"⚠️ 这是桩实现。请接入真实的日历服务（Google Calendar / Outlook / CalDAV）。"
        )
