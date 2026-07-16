"""天气查询 — 使用 Open-Meteo 免费 API，无需密钥。"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import httpx

from ..base import BaseSkill

# 常用城市中心坐标（小白不需要知道经纬度）
_CITY: dict[str, tuple[float, float]] = {
    "北京": (39.9042, 116.4074),
    "beijing": (39.9042, 116.4074),
    "上海": (31.2304, 121.4737),
    "shanghai": (31.2304, 121.4737),
    "广州": (23.1291, 113.2644),
    "guangzhou": (23.1291, 113.2644),
    "深圳": (22.5431, 114.0579),
    "shenzhen": (22.5431, 114.0579),
    "杭州": (30.2741, 120.1551),
    "hangzhou": (30.2741, 120.1551),
    "成都": (30.5728, 104.0668),
    "chengdu": (30.5728, 104.0668),
    "武汉": (30.5928, 114.3055),
    "wuhan": (30.5928, 114.3055),
    "西安": (34.3416, 108.9398),
    "xian": (34.3416, 108.9398),
    "南京": (32.0603, 118.7969),
    "nanjing": (32.0603, 118.7969),
    "重庆": (29.5630, 106.5516),
    "chongqing": (29.5630, 106.5516),
}


class WeatherSkill(BaseSkill):
    name = "weather"
    description = (
        "查询天气（今天/未来几天）。"
        "当用户问「北京天气怎么样」「明天要带伞吗」时调用。"
        "支持中国主要城市中文名；也可直接给经纬度。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "description": "城市名，如 北京、上海、深圳（推荐）",
            },
            "latitude": {"type": "number", "description": "纬度（可选）"},
            "longitude": {"type": "number", "description": "经度（可选）"},
            "days": {
                "type": "integer",
                "description": "预报天数 1-7，默认 3",
                "default": 3,
            },
        },
        "required": [],
    }

    async def execute(
        self,
        city: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        days: int = 3,
        **kwargs,
    ) -> str:
        lat, lon = latitude, longitude
        label = city or f"{lat},{lon}"
        if (lat is None or lon is None) and city:
            key = city.strip().lower()
            # try exact then lower
            hit = _CITY.get(city.strip()) or _CITY.get(key)
            if not hit:
                # geocode via open-meteo
                try:
                    async with httpx.AsyncClient(timeout=15.0) as client:
                        r = await client.get(
                            "https://geocoding-api.open-meteo.com/v1/search",
                            params={"name": city, "count": 1, "language": "zh"},
                        )
                        r.raise_for_status()
                        data = r.json()
                        results = data.get("results") or []
                        if not results:
                            return f"找不到城市「{city}」。请换个写法，如：北京、上海、深圳。"
                        lat = results[0]["latitude"]
                        lon = results[0]["longitude"]
                        label = results[0].get("name") or city
                except Exception as e:
                    return f"城市解析失败: {e}"
            else:
                lat, lon = hit
                label = city
        if lat is None or lon is None:
            return "请提供城市名（如北京）或经纬度。"

        days = max(1, min(int(days or 3), 7))
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            "&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m"
            "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
            f"&forecast_days={days}&timezone=auto"
        )
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                r = await client.get(url)
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            return f"天气查询失败: {e}"

        cur = data.get("current") or {}
        daily = data.get("daily") or {}
        lines = [
            f"地点: {label} ({lat:.2f}, {lon:.2f})",
            f"当前: {cur.get('temperature_2m')}°C, "
            f"湿度 {cur.get('relative_humidity_2m')}%, "
            f"风速 {cur.get('wind_speed_10m')} km/h, "
            f"天气码 {cur.get('weather_code')}",
            "预报:",
        ]
        dates = daily.get("time") or []
        tmax = daily.get("temperature_2m_max") or []
        tmin = daily.get("temperature_2m_min") or []
        pop = daily.get("precipitation_probability_max") or []
        for i, d in enumerate(dates):
            lines.append(
                f"  {d}: {tmin[i] if i < len(tmin) else '?'}~"
                f"{tmax[i] if i < len(tmax) else '?'}°C, "
                f"降水概率 {pop[i] if i < len(pop) else '?'}%"
            )
        lines.append("（数据来源 Open-Meteo，无需 API Key）")
        return "\n".join(lines)
