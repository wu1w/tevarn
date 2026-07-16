"""
Send Email Skill - 发送邮件
当前为桩实现，后续可接入 SMTP / SendGrid / AWS SES
"""

from ..base import BaseSkill


class SendEmailSkill(BaseSkill):
    """发送邮件 Skill"""

    name = "send_email"
    description = (
        "当需要向用户或其他人发送邮件通知、报告或摘要时，"
        "调用此工具发送邮件。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "收件人邮箱地址",
            },
            "subject": {
                "type": "string",
                "description": "邮件主题",
            },
            "body": {
                "type": "string",
                "description": "邮件正文（支持 Markdown）",
            },
        },
        "required": ["to", "subject", "body"],
    }

    async def execute(self, to: str, subject: str, body: str, **kwargs) -> str:
        """发送邮件（桩实现）"""
        # 兼容 Agent Loop 注入的 user_id / _session_id 等元数据，忽略即可
        return (
            f"[Email Stub]\nTo: {to}\nSubject: {subject}\nBody: {body[:200]}...\n"
            f"⚠️ 这是桩实现。请配置 SMTP 或接入邮件发送服务。"
        )
