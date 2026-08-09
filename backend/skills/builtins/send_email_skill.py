"""
Send Email Skill - 发送邮件
优先读 settings / 环境变量 SMTP 配置；未配置时返回明确错误（不再假装已发送）。
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any

from ..base import BaseSkill

logger = logging.getLogger(__name__)


def _smtp_config() -> dict[str, Any]:
    """合并 env + settings + config。"""
    cfg: dict[str, Any] = {
        "host": os.environ.get("TEVARN_SMTP_HOST") or os.environ.get("SMTP_HOST") or "",
        "port": int(os.environ.get("TEVARN_SMTP_PORT") or os.environ.get("SMTP_PORT") or "587"),
        "user": os.environ.get("TEVARN_SMTP_USER") or os.environ.get("SMTP_USER") or "",
        "password": os.environ.get("TEVARN_SMTP_PASSWORD") or os.environ.get("SMTP_PASSWORD") or "",
        "from_addr": os.environ.get("TEVARN_SMTP_FROM") or os.environ.get("SMTP_FROM") or "",
        "use_tls": (os.environ.get("TEVARN_SMTP_TLS") or os.environ.get("SMTP_TLS") or "1")
        not in ("0", "false", "False", "no"),
    }
    try:
        from backend.core.config import settings

        cfg["host"] = cfg["host"] or str(getattr(settings, "smtp_host", "") or "")
        cfg["port"] = int(getattr(settings, "smtp_port", None) or cfg["port"] or 587)
        cfg["user"] = cfg["user"] or str(getattr(settings, "smtp_user", "") or "")
        cfg["password"] = cfg["password"] or str(getattr(settings, "smtp_password", "") or "")
        cfg["from_addr"] = cfg["from_addr"] or str(getattr(settings, "smtp_from", "") or "")
        if hasattr(settings, "smtp_use_tls"):
            cfg["use_tls"] = bool(settings.smtp_use_tls)
    except Exception:
        pass
    # DB runtime settings（若有）
    try:
        from backend.core import runtime_settings as rs

        get = getattr(rs, "get_setting_sync", None) or getattr(rs, "get_sync", None)
        if callable(get):
            for key, dest in (
                ("smtp_host", "host"),
                ("smtp_port", "port"),
                ("smtp_user", "user"),
                ("smtp_password", "password"),
                ("smtp_from", "from_addr"),
            ):
                v = get(key)
                if v not in (None, ""):
                    cfg[dest] = int(v) if dest == "port" else str(v)
    except Exception:
        pass
    if not cfg["from_addr"] and cfg["user"] and "@" in cfg["user"]:
        cfg["from_addr"] = cfg["user"]
    return cfg


class SendEmailSkill(BaseSkill):
    """发送邮件 Skill"""

    name = "send_email"
    description = (
        "当需要向用户或其他人发送邮件通知、报告或摘要时，"
        "调用此工具发送邮件。需配置 SMTP（环境变量 TEVARN_SMTP_* 或设置项）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "收件人邮箱地址（多个用逗号分隔）",
            },
            "subject": {
                "type": "string",
                "description": "邮件主题",
            },
            "body": {
                "type": "string",
                "description": "邮件正文（纯文本；Markdown 将按文本发送）",
            },
        },
        "required": ["to", "subject", "body"],
    }

    async def execute(self, to: str, subject: str, body: str, **kwargs: Any) -> str:
        """发送邮件（真实 SMTP）"""
        to = (to or "").strip()
        subject = (subject or "").strip()
        body = body or ""
        if not to or not subject:
            return "[Error] to 与 subject 必填"

        cfg = _smtp_config()
        if not cfg["host"]:
            return (
                "[Error] 未配置 SMTP。请设置环境变量 "
                "TEVARN_SMTP_HOST / TEVARN_SMTP_PORT / TEVARN_SMTP_USER / "
                "TEVARN_SMTP_PASSWORD / TEVARN_SMTP_FROM，或在设置中写入同名项。"
            )
        from_addr = cfg["from_addr"] or cfg["user"]
        if not from_addr:
            return "[Error] 缺少发件人地址（TEVARN_SMTP_FROM 或 SMTP 用户）"

        recipients = [x.strip() for x in to.replace(";", ",").split(",") if x.strip()]
        if not recipients:
            return "[Error] 收件人列表为空"

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = ", ".join(recipients)
        msg.set_content(body)

        def _send() -> None:
            port = int(cfg["port"] or 587)
            if cfg["use_tls"] and port == 465:
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(cfg["host"], port, context=context, timeout=30) as server:
                    if cfg["user"]:
                        server.login(cfg["user"], cfg["password"] or "")
                    server.send_message(msg)
            else:
                with smtplib.SMTP(cfg["host"], port, timeout=30) as server:
                    server.ehlo()
                    if cfg["use_tls"]:
                        context = ssl.create_default_context()
                        server.starttls(context=context)
                        server.ehlo()
                    if cfg["user"]:
                        server.login(cfg["user"], cfg["password"] or "")
                    server.send_message(msg)

        try:
            import asyncio

            await asyncio.to_thread(_send)
        except Exception as e:
            logger.exception("send_email failed")
            return f"[Error] 发送失败: {e}"

        return f"OK email sent to {', '.join(recipients)} subject={subject!r}"
