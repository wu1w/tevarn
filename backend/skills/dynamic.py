"""
动态 Skill 执行器
用于执行数据库中用户自定义的 Skill（HTTP 调用 / Python 脚本）
"""

import json
from typing import Any

import aiohttp

from backend.core.config import settings
from backend.core.net_safety import UnsafeURLError, validate_public_url
from backend.services.workflow_engine import WorkflowEngine

from .base import BaseSkill


class DynamicSkill(BaseSkill):
    """基于数据库配置的动态 Skill"""

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: str,
        handler_config: dict[str, Any],
    ):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.handler = handler
        self.handler_config = handler_config

    @classmethod
    def from_db(cls, skill: Any) -> "DynamicSkill":
        return cls(
            name=skill.name,
            description=skill.description or "",
            parameters=skill.schema or {"type": "object", "properties": {}},
            handler=skill.handler or "http",
            handler_config=skill.handler_config or {},
        )

    @classmethod
    def list_all(cls) -> list["DynamicSkill"]:
        """从数据库加载所有用户自定义 skill"""
        # 同步加载：用于启动时注册到 ToolRegistry
        # 如果处于异步上下文，应由上层调用 from_db 后的列表传入
        try:
            import asyncio

            from backend.repositories.skill_repo import AsyncSkillRepository
            repo = AsyncSkillRepository()
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 异步上下文中不能同步运行，返回空列表，由上层处理
                return []
            skills = loop.run_until_complete(repo.get_active_skills())
            return [cls.from_db(s) for s in skills if not getattr(s, "is_builtin", True)]
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"DynamicSkill.list_all failed: {e}")
            return []

    async def execute(self, **kwargs: Any) -> str:
        # ── Agent Kernel（阶段 1/W3）：dynamic skill 执行同样经中介 ──
        # _kernel_process_id 由 loop._execute_registered_tool 注入；
        # 工作流等非 loop 路径调用时为空 → 跳过（该路径有自己的工作流权限）。
        proc_id = kwargs.pop("_kernel_process_id", None)
        if proc_id:
            from backend.kernel import KernelPermissionError, get_kernel

            try:
                await get_kernel().mediate(
                    str(proc_id), "skill_exec", self.name, args=kwargs
                )
            except KernelPermissionError as e:
                # 0.4.1 提权交互：skill 不在进程能力集 → 自动发起申请
                esc_note = ""
                try:
                    from backend.core.config import settings as _s

                    if bool(getattr(_s, "agent_kernel_auto_escalate", True)):
                        req = await get_kernel().request_escalation(
                            str(proc_id), [self.name],
                            reason=f"dynamic skill 执行被能力集拦截：{self.name}",
                        )
                        esc_note = (
                            f"（已自动发起权限申请 {req.id}，"
                            "用户在权限控制台批准后即可重试）"
                        )
                except ValueError:
                    pass
                return f"[Error] Kernel 权限拒绝——{e}{esc_note}"
        if self.handler == "http":
            return await self._run_http(kwargs)
        if self.handler == "python":
            return await self._run_python(kwargs)
        return f"[Error] unsupported handler '{self.handler}'"

    async def _run_http(self, arguments: dict[str, Any]) -> str:
        url = self.handler_config.get("url", "")
        method = (self.handler_config.get("method") or "GET").upper()
        headers = self.handler_config.get("headers", {})
        timeout = float(self.handler_config.get("timeout") or 30)

        if not url:
            return "[Error] HTTP handler missing url"

        try:
            validate_public_url(url)
        except UnsafeURLError as e:
            return f"[Security Blocked] {e}"

        try:
            # 安全修复：禁用重定向，防止SSRF绕过
            # 同时限制响应大小，防止恶意服务器通过巨大响应耗尽内存
            MAX_BODY_SIZE = 500_000
            async with aiohttp.ClientSession() as session:
                request_kwargs: dict[str, Any] = {
                    "headers": headers,
                    "timeout": aiohttp.ClientTimeout(total=timeout),
                    "allow_redirects": False,
                }
                if method in ("POST", "PUT", "PATCH"):
                    request_kwargs["json"] = arguments
                async with session.request(method, url, **request_kwargs) as resp:
                    if resp.status in (301, 302, 303, 307, 308):
                        return f"[Security Blocked] Redirects are not followed (status {resp.status}, location={resp.headers.get('Location', '')})"
                    content = await resp.content.read(MAX_BODY_SIZE)
                    text = content.decode("utf-8", errors="replace")
                    if len(content) >= MAX_BODY_SIZE:
                        text += "\n\n[Response truncated at 500KB]"
                    result = {
                        "status": resp.status,
                        "body": text[:4000],
                    }
                    return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            return f"[Error] HTTP request failed: {e}"

    async def _run_python(self, arguments: dict[str, Any]) -> str:
        # 安全：Python 代码执行默认禁用，需通过环境变量 ENABLE_PYTHON_EXECUTION=true 开启
        if not settings.enable_python_execution:
            return (
                "[Security Blocked] Python Skill execution is disabled. "
                "Set ENABLE_PYTHON_EXECUTION=true to enable (not recommended for production)."
            )

        code = self.handler_config.get("code", "")
        if not code:
            return "[Error] Python handler missing code"

        engine = WorkflowEngine()
        try:
            engine._validate_code_ast(code)
            output = await engine._run_code_in_subprocess(
                code=code,
                input_data=arguments,
                context_data={},
                timeout=10,
                label=f"<skill:{self.name}>",
                # 阶段 2：动态生成的 skill 默认进入更强隔离（auto=bwrap 有则用）
                sandbox=str(getattr(settings, "agent_skill_sandbox", "auto") or "auto"),
            )
        except Exception as e:
            return f"[Error] Python execution failed: {e}"

        if output.get("error"):
            return f"[Error] {output['error']}\n{output.get('stderr', '')}".strip()
        result = output.get("result")
        if result is None:
            result = output.get("stdout", "")
        if isinstance(result, str):
            return result[:4000]
        return json.dumps(result, ensure_ascii=False)[:4000]
