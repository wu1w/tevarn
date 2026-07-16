"""远程设备状态 — 配合 L1 takton-agent / @device。"""

from __future__ import annotations

import json

from ..base import BaseSkill


class DeviceStatusSkill(BaseSkill):
    name = "list_devices"
    description = (
        "列出已配对的远程/本机设备及在线状态、延迟。"
        "当用户问「我有哪些电脑」「remote-pc 在线吗」「设备连上了吗」时调用。"
        "执行命令请用对话里 @设备名 命令，或设备页。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "ping": {
                "type": "boolean",
                "description": "是否对远程 agent 做一次 ping（更准但稍慢）",
                "default": False,
            },
        },
        "required": [],
    }

    async def execute(self, ping: bool = False, user_id: str | None = None, **kwargs) -> str:
        try:
            from backend.repositories.device_repo import AsyncDeviceRepository
            from backend.services.remote.transport import RemoteAgentTransport
        except Exception as e:
            return f"设备模块不可用: {e}"

        if not user_id:
            return "缺少用户上下文，无法列出设备。"
        # user_id 可能是 str（loop 注入），统一转 UUID
        import uuid as _uuid

        if isinstance(user_id, str):
            try:
                user_id = _uuid.UUID(user_id)
            except (ValueError, AttributeError):
                return f"用户 ID 格式异常: {user_id!r}"
        repo = AsyncDeviceRepository()
        devices = await repo.list_by_user(user_id) or []
        if not devices:
            return (
                "还没有配对任何设备。\n"
                "小白步骤：打开「设备」页 → 填写 host/port/token → 配对。\n"
                "本机示例：先启动 takton-agent，再配对 127.0.0.1:19876。"
            )

        lines = [f"共 {len(devices)} 台设备："]
        for d in devices:
            cfg = d.config or {}
            host = cfg.get("host") or cfg.get("agent_url") or "?"
            ms = cfg.get("last_latency_ms")
            line = f"- {d.name} | {d.status} | type={d.device_type}"
            if ms is not None:
                line += f" | 延迟 {ms}ms"
            line += f" | {host}"
            if ping and cfg.get("host") and cfg.get("token"):
                try:
                    tr = RemoteAgentTransport(
                        host=str(cfg["host"]),
                        port=int(cfg.get("port") or 19876),
                        token=str(cfg.get("token") or ""),
                    )
                    res = await tr.ping()
                    line += f" | ping={res.get('latency_ms')}ms"
                    await repo.update(
                        d.id,
                        {
                            "status": "online",
                            "config": {**cfg, "last_latency_ms": res.get("latency_ms")},
                        },
                    )
                except Exception as e:
                    line += f" | ping失败: {e}"
            lines.append(line)
        lines.append("对话中执行远程命令示例：@remote-pc hostname")
        return "\n".join(lines)
