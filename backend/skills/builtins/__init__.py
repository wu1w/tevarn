"""
内置 Skill 模块
导入此模块会自动注册所有内置 Skill
"""

from . import (
    agent_call_skill,
    bash_skill,
    beginner_help_skill,
    calendar_read_skill,
    configure_takton_skill,
    current_time_skill,
    device_status_skill,
    fetch_webpage_skill,
    generate_ppt_skill,
    generate_report_skill,
    http_get_skill,
    manage_goal_skill,
    rag_skill,
    send_email_skill,
    weather_skill,
    web_search_skill,
    wiki_search_skill,
)

__all__ = [
    "rag_skill",
    "bash_skill",
    "web_search_skill",
    "http_get_skill",
    "calendar_read_skill",
    "send_email_skill",
    "agent_call_skill",
    "generate_ppt_skill",
    "generate_report_skill",
    "manage_goal_skill",
    "wiki_search_skill",
    "current_time_skill",
    "weather_skill",
    "fetch_webpage_skill",
    "device_status_skill",
    "beginner_help_skill",
    "configure_takton_skill",
]
