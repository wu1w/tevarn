"""当前时间 / 时区 — 小白最常用的基础能力。

Windows 常缺 IANA tzdata（ZoneInfoNotFoundError），本实现：
1. 优先 zoneinfo（有 tzdata 包时完整）
2. UTC 用 datetime.timezone.utc
3. 常见时区用固定 UTC 偏移兜底，保证工具永不因缺库炸掉
"""

from __future__ import annotations

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from ..base import BaseSkill

# Windows 无 tzdata 时的固定偏移（不含夏令时；够日常「现在几点」）
_FIXED_OFFSETS: dict[str, timedelta] = {
    "UTC": timedelta(0),
    "GMT": timedelta(0),
    "Z": timedelta(0),
    "Etc/UTC": timedelta(0),
    "Asia/Shanghai": timedelta(hours=8),
    "Asia/Chongqing": timedelta(hours=8),
    "Asia/Harbin": timedelta(hours=8),
    "Asia/Hong_Kong": timedelta(hours=8),
    "Asia/Taipei": timedelta(hours=8),
    "Asia/Singapore": timedelta(hours=8),
    "Asia/Tokyo": timedelta(hours=9),
    "Asia/Seoul": timedelta(hours=9),
    "Europe/London": timedelta(hours=0),
    "Europe/Paris": timedelta(hours=1),
    "Europe/Berlin": timedelta(hours=1),
    "America/New_York": timedelta(hours=-5),
    "America/Los_Angeles": timedelta(hours=-8),
    "America/Chicago": timedelta(hours=-6),
}


def _resolve_tz(name: str) -> tuple[dt_timezone, str, str]:
    """返回 (tzinfo, display_name, note)。note 非空表示走了兜底。"""
    raw = (name or "Asia/Shanghai").strip() or "Asia/Shanghai"
    key = raw
    # 优先 IANA / zoneinfo（装了 tzdata 时最准）
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(key), key, ""
    except Exception:
        pass

    # UTC 特判（Windows 无 tzdata 时 ZoneInfo('UTC') 也会炸）
    if key.upper() in ("UTC", "GMT", "Z", "ETC/UTC"):
        return dt_timezone.utc, "UTC", ""

    # 固定偏移表
    for cand in (key, key.replace(" ", "_")):
        if cand in _FIXED_OFFSETS:
            off = _FIXED_OFFSETS[cand]
            label = cand
            note = "fixed-offset fallback (install tzdata for full IANA zones)"
            return dt_timezone(off), label, note

    # UTC±N / GMT±N
    import re

    m = re.fullmatch(r"(?:UTC|GMT)?\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?", key, re.I)
    if m:
        sign = 1 if m.group(1) == "+" else -1
        hours = int(m.group(2))
        mins = int(m.group(3) or 0)
        off = timedelta(hours=sign * hours, minutes=sign * mins)
        label = f"UTC{m.group(1)}{hours:02d}:{mins:02d}"
        return dt_timezone(off), label, ""

    # 最终兜底：东八区（产品默认用户时区）
    return (
        dt_timezone(timedelta(hours=8)),
        "Asia/Shanghai",
        f"unknown zone {raw!r}; fell back to UTC+08:00 (install tzdata for IANA)",
    )


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
        tz, display, note = _resolve_tz(str(tz_name))
        now = datetime.now(tz)
        utc = datetime.now(dt_timezone.utc)
        lines = [
            f"时区: {display}",
            f"本地时间: {now.strftime('%Y-%m-%d %H:%M:%S %A')}",
            f"ISO: {now.isoformat()}",
            f"UTC: {utc.strftime('%Y-%m-%d %H:%M:%S')}",
        ]
        if note:
            lines.append(f"note: {note}")
        return "\n".join(lines)
