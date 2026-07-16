"""
configure_takton — 对话配置 / 使用 Takton 全功能。

AI 时代：用户应尽量通过对话框完成配置与使用。
本 Skill 内置完整产品手册，不依赖向量 RAG；也可引导 update_config 等工具。
"""

from __future__ import annotations

import json
from typing import Any

from backend.content.product_handbook import (
    PRODUCT_HANDBOOK,
    SETTINGS_CATALOG,
    handbook_as_kb_docs,
    resolve_topic,
)

from ..base import BaseSkill

_LOW_RISK = {c["key"] for c in SETTINGS_CATALOG if c["risk"] == "low"}
_HIGH_RISK = {c["key"] for c in SETTINGS_CATALOG if c["risk"] == "high"}


class ConfigureTaktonSkill(BaseSkill):
    name = "configure_takton"
    description = (
        "【Takton 官方配置与使用助手】当用户要配置或使用本产品任意功能时优先调用："
        "模型/API、Embedding/RAG/知识库、远程设备@、通道、工具技能、定时、工作流、"
        "MCP、上下文压缩、Wiki、安全、开箱清单。"
        "action=guide 返回某模块完整说明；"
        "action=status 读当前系统状态；"
        "action=list_settings 列出可对话修改的配置键；"
        "action=set_setting 写入配置（高风险需 confirm=true）；"
        "action=checklist 开箱步骤；"
        "action=search 在手册中关键词检索。"
        "用户说「怎么配」「对话框改设置」「教我用 Takton」必须用本 skill。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "guide",
                    "status",
                    "list_settings",
                    "set_setting",
                    "checklist",
                    "search",
                    "topics",
                ],
                "description": "操作类型，默认 guide",
                "default": "guide",
            },
            "topic": {
                "type": "string",
                "description": (
                    "guide 主题：overview/models/embedding_rag/devices/channels/"
                    "tools_skills/cron/workflows/mcp_profiles/context/wiki/security/"
                    "dialog_cheatsheet/checklist；也可用中文别名如 模型、设备、知识库"
                ),
            },
            "query": {
                "type": "string",
                "description": "search 时的关键词",
            },
            "key": {
                "type": "string",
                "description": "set_setting 时的配置键，如 temperature、max_tokens",
            },
            "value": {
                "type": "string",
                "description": "set_setting 时的值",
            },
            "confirm": {
                "type": "boolean",
                "description": "高风险配置（API Key/URL）需 true 表示用户已确认",
                "default": False,
            },
        },
        "required": [],
    }

    async def execute(
        self,
        action: str = "guide",
        topic: str | None = None,
        query: str | None = None,
        key: str | None = None,
        value: str | None = None,
        confirm: bool = False,
        **kwargs: Any,
    ) -> str:
        action = (action or "guide").strip().lower()

        if action == "topics":
            lines = ["可用手册主题："]
            for k, item in PRODUCT_HANDBOOK.items():
                lines.append(f"- `{k}`: {item['title']}")
            lines.append("中文也可：模型、设备、知识库、通道、定时、工作流、安全、清单…")
            return "\n".join(lines)

        if action == "checklist":
            return PRODUCT_HANDBOOK["checklist"]["body"]

        if action == "list_settings":
            lines = [
                "可通过对话修改的配置键（set_setting）：",
                "| key | 风险 | 说明 |",
                "|-----|------|------|",
            ]
            for c in SETTINGS_CATALOG:
                lines.append(f"| `{c['key']}` | {c['risk']} | {c['desc']} |")
            lines.append(
                "\n低风险可直接改；high 必须用户明确确认后 confirm=true。\n"
                "也可让用户去页面：Agent → 配置 / 系统 → 设置。"
            )
            return "\n".join(lines)

        if action == "search":
            q = (query or topic or "").strip()
            if not q:
                return "请提供 query，例如：设备、Qdrant、temperature"
            hits: list[str] = []
            ql = q.lower()
            for k, item in PRODUCT_HANDBOOK.items():
                blob = (item["title"] + "\n" + item["body"]).lower()
                if ql in blob or ql in k:
                    # excerpt
                    idx = blob.find(ql)
                    start = max(0, idx - 40)
                    excerpt = item["body"][start : start + 200].replace("\n", " ")
                    hits.append(f"### {item['title']} (`{k}`)\n…{excerpt}…")
            if not hits:
                return f"手册中未找到「{q}」。可 action=topics 看目录，或 guide 指定 topic。"
            return f"关键词「{q}」命中 {len(hits)} 节：\n\n" + "\n\n".join(hits[:8])

        if action == "status":
            return await self._status()

        if action == "set_setting":
            if not key:
                return "set_setting 需要 key。先 list_settings 查看可用键。"
            if value is None or value == "":
                return "set_setting 需要 value。"
            return await self._set_setting(key.strip(), str(value), bool(confirm))

        # guide
        tid = resolve_topic(topic)
        item = PRODUCT_HANDBOOK[tid]
        footer = (
            "\n\n---\n"
            "接下来你可以：\n"
            "- 说「改 temperature=0.2」之类让我 set_setting\n"
            "- 说「系统状态」看 status\n"
            "- 说「开箱清单」逐步配置\n"
            f"- 其它模块 topic 例如 devices / models / channels（当前：`{tid}`）\n"
        )
        return f"# {item['title']}\n\n{item['body']}{footer}"

    async def _status(self) -> str:
        try:
            from backend.tools.builtins.self_config import GetSystemStatus

            tool = GetSystemStatus()
            res = await tool.execute()
            data = getattr(res, "data", None) or {}
            msg = getattr(res, "message", "") or ""
            return (
                "【Takton 系统状态】\n"
                f"{msg}\n\n"
                f"```json\n{json.dumps(data, ensure_ascii=False, indent=2, default=str)}\n```\n"
                "需要改配置请说明键和值；高风险项需你确认。"
            )
        except Exception as e:
            return f"读取状态失败: {e}\n请打开「配置」页查看，或检查后端日志。"

    async def _set_setting(self, key: str, value: str, confirm: bool) -> str:
        # parse bool-ish
        v = value
        if key in ("rag_enabled", "context_enable_l1", "context_enable_l3", "context_enable_l5"):
            if str(value).lower() in ("1", "true", "yes", "on", "是"):
                v = "true"
            elif str(value).lower() in ("0", "false", "no", "off", "否"):
                v = "false"

        if key in _HIGH_RISK and not confirm:
            return (
                f"⚠️ `{key}` 属于高风险配置（API 凭证或服务地址）。\n"
                f"将要写入的值：`{value[:80]}{'…' if len(value) > 80 else ''}`\n"
                "请用户明确回复「确认修改」后，再以 confirm=true 调用 set_setting。"
            )
        try:
            from backend.tools.builtins.self_config import UpdateConfig

            tool = UpdateConfig()
            res = await tool.execute(key=key, value=v, confirm=confirm or key in _LOW_RISK)
            ok = getattr(res, "success", False)
            msg = getattr(res, "message", "")
            data = getattr(res, "data", None)
            if not ok and isinstance(data, dict) and data.get("needs_confirmation"):
                return msg
            return f"{'✅' if ok else '❌'} {msg}\n建议再说一次「系统状态」核对。"
        except Exception as e:
            return f"写入失败: {e}"


# re-export for seed helpers
def product_kb_docs() -> list[dict[str, str]]:
    return handbook_as_kb_docs()
