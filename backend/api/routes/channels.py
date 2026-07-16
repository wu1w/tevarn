"""
消息通道（Channel）API 路由

提供 CRUD + 连接测试 + 平台预设模板，兼容 Hermes 的 Platform 配置体系。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.dependencies import get_current_user, require_admin
from backend.database import get_db_context
from backend.models.channel import Channel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/channels", tags=["channels"])


# ─── 平台预设模板 ──────────────────────────────────────────────

PLATFORM_PRESETS: list[dict[str, Any]] = [
    {
        "platform": "telegram",
        "name": "Telegram Bot",
        "description": "Telegram 机器人，通过 Bot API 收发消息",
        "fields": [
            {"key": "token", "label": "Bot Token", "type": "password", "required": True, "help": "从 @BotFather 获取的 Bot Token"},
            {"key": "home_channel_id", "label": "主频道 ID", "type": "text", "required": False, "help": "默认推送频道（可选）"},
        ],
        "extra_schema": {},
    },
    {
        "platform": "discord",
        "name": "Discord Bot",
        "description": "Discord 机器人，通过 Gateway/Webhook 收发消息",
        "fields": [
            {"key": "token", "label": "Bot Token", "type": "password", "required": True, "help": "从 Discord Developer Portal 获取"},
            {"key": "home_channel_id", "label": "主频道 ID", "type": "text", "required": False, "help": "默认频道 ID（如 #general）"},
        ],
        "extra_schema": {
            "guild_id": {"label": "服务器 ID", "type": "text", "required": False},
            "typing_indicator": {"label": "显示输入状态", "type": "switch", "default": True},
        },
    },
    {
        "platform": "wecom",
        "name": "企业微信",
        "description": "企业微信应用消息，通过 Webhook/回调收发",
        "fields": [
            {"key": "api_key", "label": "Corp Secret", "type": "password", "required": True, "help": "应用的 Secret"},
            {"key": "home_channel_id", "label": "企业 ID (CorpID)", "type": "text", "required": True, "help": "企业微信企业 ID"},
        ],
        "extra_schema": {
            "agent_id": {"label": "AgentId", "type": "text", "required": True},
            "token": {"label": "回调 Token", "type": "text", "required": False},
            "encoding_aes_key": {"label": "回调 EncodingAESKey", "type": "text", "required": False},
        },
    },
    {
        "platform": "qqbot",
        "name": "QQ 机器人",
        "description": "QQ 频道/群机器人，通过官方 Bot API 收发消息",
        "fields": [
            {"key": "api_key", "label": "App Secret", "type": "password", "required": True, "help": "QQ 机器人 AppSecret"},
        ],
        "extra_schema": {
            "app_id": {"label": "App ID", "type": "text", "required": True},
            "dm_policy": {"label": "私信策略", "type": "select", "options": ["pairing", "ignore"], "default": "pairing"},
            "group_policy": {"label": "群聊策略", "type": "select", "options": ["pairing", "ignore"], "default": "pairing"},
        },
    },
    {
        "platform": "slack",
        "name": "Slack Bot",
        "description": "Slack 机器人，通过 Socket Mode/Webhook 收发消息",
        "fields": [
            {"key": "token", "label": "Bot Token (xoxb-)", "type": "password", "required": True, "help": "从 Slack App 管理页获取的 Bot Token"},
            {"key": "home_channel_id", "label": "主频道 ID", "type": "text", "required": False},
        ],
        "extra_schema": {
            "app_token": {"label": "App Token (xapp-)", "type": "password", "required": False, "help": "Socket Mode 需要的 App-Level Token"},
            "signing_secret": {"label": "Signing Secret", "type": "password", "required": False},
        },
    },
    {
        "platform": "feishu",
        "name": "飞书机器人",
        "description": "飞书自建应用机器人，通过 Webhook 收发消息",
        "fields": [
            {"key": "api_key", "label": "App Secret", "type": "password", "required": True},
        ],
        "extra_schema": {
            "app_id": {"label": "App ID", "type": "text", "required": True},
            "verification_token": {"label": "Verification Token", "type": "text", "required": False},
            "encrypt_key": {"label": "Encrypt Key", "type": "text", "required": False},
        },
    },
    {
        "platform": "signal",
        "name": "Signal",
        "description": "Signal 私信，通过 signal-cli-rest-api 收发消息",
        "fields": [],
        "extra_schema": {
            "phone_number": {"label": "手机号", "type": "text", "required": True, "help": "Signal 绑定的手机号"},
            "signal_cli_url": {"label": "signal-cli 地址", "type": "text", "required": False, "default": "http://localhost:8080", "help": "signal-cli-rest-api 的 URL"},
        },
    },
    {
        "platform": "dingtalk",
        "name": "钉钉机器人",
        "description": "钉钉自建应用机器人",
        "fields": [
            {"key": "api_key", "label": "App Secret", "type": "password", "required": True},
        ],
        "extra_schema": {
            "app_key": {"label": "App Key", "type": "text", "required": True},
            "agent_id": {"label": "AgentId", "type": "text", "required": False},
        },
    },
]


# ─── Pydantic 模型 ─────────────────────────────────────────────

class ChannelCreate(BaseModel):
    platform: str = Field(..., min_length=1, max_length=32)
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = None
    enabled: bool = False
    token: str | None = None
    api_key: str | None = None
    home_channel_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
    webhook_url: str | None = None


class ChannelUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    token: str | None = None
    api_key: str | None = None
    home_channel_id: str | None = None
    extra: dict[str, Any] | None = None
    webhook_url: str | None = None


class ChannelRead(BaseModel):
    id: str
    platform: str
    name: str
    description: str | None
    enabled: bool
    connected: bool
    home_channel_id: str | None
    extra: dict[str, Any]
    webhook_url: str | None
    last_tested_at: str | None
    last_test_result: str | None
    created_at: str | None
    updated_at: str | None
    # token/api_key 不直接返回明文，只返回是否已配置
    has_token: bool = False
    has_api_key: bool = False

    class Config:
        from_attributes = True


class ChannelTestResult(BaseModel):
    success: bool
    message: str
    detail: str | None = None


# ─── 工具函数 ──────────────────────────────────────────────────

def _channel_to_read(ch: Channel) -> ChannelRead:
    return ChannelRead(
        id=str(ch.id),
        platform=ch.platform,
        name=ch.name,
        description=ch.description,
        enabled=ch.enabled,
        connected=ch.connected,
        home_channel_id=ch.home_channel_id,
        extra=ch.extra or {},
        webhook_url=ch.webhook_url,
        last_tested_at=ch.last_tested_at,
        last_test_result=ch.last_test_result,
        created_at=str(ch.created_at) if ch.created_at else None,
        updated_at=str(ch.updated_at) if ch.updated_at else None,
        has_token=bool(ch.token),
        has_api_key=bool(ch.api_key),
    )


# ─── API 端点 ──────────────────────────────────────────────────

@router.get("/presets")
async def list_presets():
    """列出所有平台预设模板"""
    return PLATFORM_PRESETS


@router.get("")
async def list_channels(user=Depends(require_admin)):
    """列出所有已配置的消息通道"""
    async with get_db_context() as db:
        result = await db.execute(
            select(Channel).order_by(Channel.created_at.desc())
        )
        channels = result.scalars().all()
        return [_channel_to_read(ch) for ch in channels]


@router.get("/{channel_id}")
async def get_channel(
    channel_id: str,
    user=Depends(require_admin),
):
    """获取单个消息通道配置"""
    try:
        cid = uuid.UUID(channel_id)
    except ValueError:
        raise HTTPException(400, "Invalid channel ID")
    async with get_db_context() as db:
        ch = await db.get(Channel, cid)
        if not ch:
            raise HTTPException(404, "Channel not found")
        return _channel_to_read(ch)


@router.post("")
async def create_channel(
    data: ChannelCreate,
    user=Depends(require_admin),
):
    """创建消息通道"""
    async with get_db_context() as db:
        ch = Channel(
            platform=data.platform,
            name=data.name,
            description=data.description,
            enabled=data.enabled,
            token=data.token,
            api_key=data.api_key,
            home_channel_id=data.home_channel_id,
            extra=data.extra,
            webhook_url=data.webhook_url,
        )
        db.add(ch)
        await db.commit()
        await db.refresh(ch)
        logger.info("Channel created: %s (%s)", ch.name, ch.platform)
        return _channel_to_read(ch)


@router.put("/{channel_id}")
async def update_channel(
    channel_id: str,
    data: ChannelUpdate,
    user=Depends(require_admin),
):
    """更新消息通道配置"""
    try:
        cid = uuid.UUID(channel_id)
    except ValueError:
        raise HTTPException(400, "Invalid channel ID")
    async with get_db_context() as db:
        ch = await db.get(Channel, cid)
        if not ch:
            raise HTTPException(404, "Channel not found")

        update_data = data.model_dump(exclude_unset=True)
        for secret_field in ("token", "api_key"):
            if secret_field in update_data and update_data[secret_field] is None:
                del update_data[secret_field]
            elif secret_field in update_data and update_data[secret_field] == "":
                update_data[secret_field] = None

        for k, v in update_data.items():
            setattr(ch, k, v)

        await db.commit()
        await db.refresh(ch)
        logger.info("Channel updated: %s (%s)", ch.name, ch.platform)
        return _channel_to_read(ch)


@router.delete("/{channel_id}")
async def delete_channel(
    channel_id: str,
    user=Depends(require_admin),
):
    """删除消息通道"""
    try:
        cid = uuid.UUID(channel_id)
    except ValueError:
        raise HTTPException(400, "Invalid channel ID")
    async with get_db_context() as db:
        ch = await db.get(Channel, cid)
        if not ch:
            raise HTTPException(404, "Channel not found")
        await db.delete(ch)
        await db.commit()
        logger.info("Channel deleted: %s (%s)", ch.name, ch.platform)
        return {"ok": True}


@router.post("/{channel_id}/test")
async def test_channel(
    channel_id: str,
    user=Depends(require_admin),
):
    """测试消息通道连接"""
    try:
        cid = uuid.UUID(channel_id)
    except ValueError:
        raise HTTPException(400, "Invalid channel ID")
    async with get_db_context() as db:
        ch = await db.get(Channel, cid)
        if not ch:
            raise HTTPException(404, "Channel not found")

        now = datetime.now(timezone.utc).isoformat()
        result = ChannelTestResult(success=False, message="未实现")

        # ── 各平台连接测试逻辑 ──
        try:
            if ch.platform == "telegram":
                result = await _test_telegram(ch)
            elif ch.platform == "discord":
                result = await _test_discord(ch)
            elif ch.platform == "wecom":
                result = await _test_wecom(ch)
            elif ch.platform == "qqbot":
                result = await _test_qqbot(ch)
            elif ch.platform == "slack":
                result = await _test_slack(ch)
            elif ch.platform == "feishu":
                result = await _test_feishu(ch)
            elif ch.platform == "dingtalk":
                result = await _test_dingtalk(ch)
            else:
                result = ChannelTestResult(success=False, message=f"平台 {ch.platform} 暂不支持连接测试")
        except Exception as e:
            result = ChannelTestResult(success=False, message="测试失败", detail=str(e))

        # 无论成功失败，都更新测试结果
        ch.last_tested_at = now
        ch.last_test_result = f"{'✅' if result.success else '❌'} {result.message}"
        ch.connected = result.success
        await db.commit()

        return result


# ─── 各平台连接测试实现 ────────────────────────────────────────

async def _test_telegram(ch: Channel) -> ChannelTestResult:
    from backend.services.channel_adapters.telegram import TelegramAdapter
    token = ch.token or ""
    if not token:
        return ChannelTestResult(success=False, message="未配置 Bot Token")
    adapter = TelegramAdapter(channel_id="", token=token)
    ok, msg = await adapter.test_connection()
    return ChannelTestResult(success=ok, message=msg)


async def _test_discord(ch: Channel) -> ChannelTestResult:
    from backend.services.channel_adapters.discord import DiscordAdapter
    token = ch.token or ""
    if not token:
        return ChannelTestResult(success=False, message="未配置 Bot Token")
    adapter = DiscordAdapter(channel_id="", token=token)
    ok, msg = await adapter.test_connection()
    return ChannelTestResult(success=ok, message=msg)


async def _test_wecom(ch: Channel) -> ChannelTestResult:
    from backend.services.channel_adapters.wecom import WeComAdapter
    extra = ch.extra or {}
    corp_id = ch.home_channel_id or extra.get("corp_id", "")
    secret = ch.api_key or ""
    if not corp_id or not secret:
        return ChannelTestResult(success=False, message="未配置 CorpID 或 CorpSecret")
    adapter = WeComAdapter(channel_id="", corp_id=corp_id, corp_secret=secret, agent_id=extra.get("agent_id", ""))
    ok, msg = await adapter.test_connection()
    return ChannelTestResult(success=ok, message=msg)


async def _test_qqbot(ch: Channel) -> ChannelTestResult:
    from backend.services.channel_adapters.qqbot import QQBotAdapter
    extra = ch.extra or {}
    app_id = extra.get("app_id", "")
    client_secret = ch.api_key or extra.get("client_secret", "")
    if not app_id or not client_secret:
        return ChannelTestResult(success=False, message="未配置 App ID 或 App Secret")
    adapter = QQBotAdapter(app_id=app_id, client_secret=client_secret)
    ok, msg = await adapter.test_connection()
    return ChannelTestResult(success=ok, message=msg)


async def _test_slack(ch: Channel) -> ChannelTestResult:
    from backend.services.channel_adapters.slack import SlackAdapter
    token = ch.token or ""
    if not token:
        return ChannelTestResult(success=False, message="未配置 Bot Token")
    adapter = SlackAdapter(channel_id="", bot_token=token, app_token=(ch.extra or {}).get("app_token", ""))
    ok, msg = await adapter.test_connection()
    return ChannelTestResult(success=ok, message=msg)


async def _test_feishu(ch: Channel) -> ChannelTestResult:
    from backend.services.channel_adapters.feishu import FeishuAdapter
    extra = ch.extra or {}
    app_id = extra.get("app_id", "")
    secret = ch.api_key or ""
    if not app_id or not secret:
        return ChannelTestResult(success=False, message="未配置 App ID 或 App Secret")
    adapter = FeishuAdapter(channel_id="", app_id=app_id, app_secret=secret)
    ok, msg = await adapter.test_connection()
    return ChannelTestResult(success=ok, message=msg)


async def _test_dingtalk(ch: Channel) -> ChannelTestResult:
    from backend.services.channel_adapters.dingtalk import DingTalkAdapter
    extra = ch.extra or {}
    app_key = extra.get("app_key", "")
    secret = ch.api_key or ""
    if not app_key or not secret:
        return ChannelTestResult(success=False, message="未配置 AppKey 或 AppSecret")
    adapter = DingTalkAdapter(channel_id="", app_key=app_key, app_secret=secret)
    ok, msg = await adapter.test_connection()
    return ChannelTestResult(success=ok, message=msg)
