"""
消息通道适配器包

每个平台一个适配器，负责实际的连接、消息收发。
"""

from .base import BaseChannelAdapter
from .qqbot import QQBotAdapter
from .telegram import TelegramAdapter
from .discord import DiscordAdapter
from .wecom import WeComAdapter
from .slack import SlackAdapter
from .feishu import FeishuAdapter
from .dingtalk import DingTalkAdapter
from .signal import SignalAdapter

# 平台 → 适配器类 映射
ADAPTER_MAP = {
    "qqbot": QQBotAdapter,
    "telegram": TelegramAdapter,
    "discord": DiscordAdapter,
    "wecom": WeComAdapter,
    "slack": SlackAdapter,
    "feishu": FeishuAdapter,
    "dingtalk": DingTalkAdapter,
    "signal": SignalAdapter,
}

__all__ = [
    "BaseChannelAdapter",
    "QQBotAdapter",
    "TelegramAdapter",
    "DiscordAdapter",
    "WeComAdapter",
    "SlackAdapter",
    "FeishuAdapter",
    "DingTalkAdapter",
    "SignalAdapter",
    "ADAPTER_MAP",
]
