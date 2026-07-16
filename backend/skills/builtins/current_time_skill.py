"""当前时间 / 时区 — 小白最常用的基础能力。"""

from datetime import datetime, timezone as dt_timezone
from zoneinfo import ZoneInfo

from ..base import BaseSkill


class CurrentTimeSkill(BaseSkill):
    name = "current_time"
    description = (
        "查询当前日期和时间。"
        "当用户问「现在几点」「今天几号」「北京时间」时调用。"
        "适合不懂技术的用户：无需命令行。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": "IANA 时区，默认 Asia/Shanghai。例：Asia/Shanghai、UTC、America/New_York",
                "default": "Asia/Shanghai",
            },
        },
        "required": [],
    }

    async def execute(self, timezone: str = "Asia/Shanghai", **kwargs) -> str:
        tz_name = timezone or kwargs.get("timezone_name") or "Asia/Shanghai"
        try:
            tz = ZoneInfo(str(tz_name))
        except Exception:
            tz = ZoneInfo("Asia/Shanghai")
            tz_name = "Asia/Shanghai"
        now = datetime.now(tz)
        utc = datetime.now(dt_timezone.utc)
        return (
            f"时区: {tz_name}\n"
            f"本地时间: {now.strftime('%Y-%m-%d %H:%M:%S %A')}\n"
            f"ISO: {now.isoformat()}\n"
            f"UTC: {utc.strftime('%Y-%m-%d %H:%M:%S')}"
        )
