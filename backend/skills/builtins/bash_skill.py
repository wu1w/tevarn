"""Bash Skill - 本地 Shell 命令执行

安全模型（v3，与 command 工具统一）：默认放开（python/pip/npm/管道/&& 均可），
仅真正危险的操作触发前端弹窗确认。危险检测复用 executors 的统一实现。
"""

from ...services.tools.executors import execute_command
from ..base import BaseSkill


class BashSkill(BaseSkill):
    """Shell 命令执行 Skill（与 command 工具同一安全模型）"""

    name = "bash"
    description = (
        "在本地环境中执行 shell 命令（默认放开，支持 python/pip/npm/管道等）。"
        "可用于安装依赖、运行脚本、查看文件、构建项目等；危险操作会请求用户确认。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的 shell 命令",
            },
            "timeout": {
                "type": "integer",
                "description": "超时时间（秒，默认 30）",
                "default": 30,
            },
        },
        "required": ["command"],
    }

    async def execute(self, command: str, timeout: int = 30, **kwargs) -> str:
        """委托给统一的 execute_command（含危险检测 + 前端确认）。

        kwargs 里 Agent Loop 注入了 _session_id / _ws_manager / user_id，
        透传给 execute_command 用于危险操作确认。
        """
        try:
            from backend.tools.permissions import resolve_agent_workspace_root

            base = resolve_agent_workspace_root()
        except Exception:
            base = ""
        # 透传 loop 注入的全部 meta（_kernel_process_id 等），避免沙箱/确认丢失
        arguments = {
            k: v
            for k, v in kwargs.items()
            if str(k).startswith("_") or k in ("cwd", "working_dir", "background")
        }
        arguments["command"] = command
        arguments["timeout"] = timeout
        if base and not arguments.get("cwd"):
            arguments["cwd"] = base
        config = {"base_path": base} if base else {}
        return await execute_command(config, arguments)
