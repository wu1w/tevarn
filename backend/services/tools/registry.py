"""
Tool Registry
管理所有 Tool 的注册、发现、Schema 生成和执行
"""

import logging
from typing import Any

from backend.models.tool import Tool
from backend.services.tools.executors import EXECUTOR_MAP

logger = logging.getLogger(__name__)


# 内置工具的默认参数 Schema
_BUILTIN_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "browser": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "要访问的网页 URL",
            },
        },
        "required": ["url"],
    },
    "command": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的 shell 命令",
            },
            "timeout": {
                "type": "integer",
                "description": "超时时间（秒）",
                "default": 30,
            },
        },
        "required": ["command"],
    },
    "file_read": {
        "type": "object",
        "properties": {
            "filepath": {
                "type": "string",
                "description": "要读取的文件路径（相对于工作目录）",
            },
        },
        "required": ["filepath"],
    },
    "file_write": {
        "type": "object",
        "properties": {
            "filepath": {
                "type": "string",
                "description": "要写入的文件路径（相对于工作目录）",
            },
            "content": {
                "type": "string",
                "description": "要写入的文件内容",
            },
        },
        "required": ["filepath", "content"],
    },
    "http": {
        "type": "object",
        "properties": {
            "method": {
                "type": "string",
                "description": "HTTP 方法（GET/POST/PUT/DELETE）",
                "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"],
                "default": "GET",
            },
            "url": {
                "type": "string",
                "description": "请求地址（如未配置则必填）",
            },
            "headers": {
                "type": "object",
                "description": "自定义请求头",
                "default": {},
            },
            "body": {
                "type": "object",
                "description": "请求体（仅 POST/PUT/PATCH）",
                "default": {},
            },
        },
    },
    "python": {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "要执行的 Python 代码",
            },
            "timeout": {
                "type": "integer",
                "description": "超时时间（秒）",
                "default": 30,
            },
        },
        "required": ["code"],
    },
    "search": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词",
            },
            "max_results": {
                "type": "integer",
                "description": "最多返回几条结果",
                "default": 5,
            },
        },
        "required": ["query"],
    },
    "edit": {
        "type": "object",
        "properties": {
            "filepath": {
                "type": "string",
                "description": "要编辑的文件路径（相对于工作目录）",
            },
            "old_text": {
                "type": "string",
                "description": "文件中要替换的旧文本（必须完全匹配）",
            },
            "new_text": {
                "type": "string",
                "description": "用于替换的新文本",
            },
        },
        "required": ["filepath", "old_text", "new_text"],
    },
    "glob": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "文件匹配模式，如 '*.py' 或 '**/*.json'",
            },
        },
        "required": ["pattern"],
    },
    "grep": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "正则表达式搜索模式",
            },
            "path": {
                "type": "string",
                "description": "要搜索的文件或目录路径（相对于工作目录）",
            },
            "recursive": {
                "type": "boolean",
                "description": "如果是目录，是否递归搜索子目录",
                "default": True,
            },
        },
        "required": ["pattern", "path"],
    },
    "sqlite_query": {
        "type": "object",
        "properties": {
            "database": {
                "type": "string",
                "description": "SQLite 数据库文件路径（相对于工作目录）",
            },
            "query": {
                "type": "string",
                "description": "要执行的 SQL 查询语句",
            },
        },
        "required": ["database", "query"],
    },
}


class ToolRegistry:
    """Tool 注册表

    负责：
    1. 从 DB 加载 Tool 配置
    2. 生成 LLM 可用的 JSON Schema
    3. 执行工具调用
    """

    @staticmethod
    def get_tool_schema(tool: Tool) -> dict[str, Any]:
        """将 Tool 模型转换为 LLM 工具定义"""
        # 基础 schema 来自内置类型或自定义配置
        base_schema = _BUILTIN_TOOL_SCHEMAS.get(tool.type, {"type": "object", "properties": {}})

        # 自定义工具的 schema 可以覆盖
        if tool.config.get("parameters"):
            parameters = tool.config["parameters"]
        else:
            parameters = base_schema

        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": parameters,
            },
        }

    @staticmethod
    def get_tools_schema(tools: list[Tool]) -> list[dict[str, Any]]:
        """批量生成工具 schema"""
        return [ToolRegistry.get_tool_schema(t) for t in tools if t.enabled]

    @staticmethod
    async def execute_tool(tool: Tool, arguments: dict[str, Any]) -> str:
        """执行指定工具"""
        executor = EXECUTOR_MAP.get(tool.type)
        if executor is None:
            return f"[Error] Unknown tool type: {tool.type}"

        try:
            result = await executor(tool.config, arguments)
            return result
        except Exception as e:
            logger.error(f"Tool execution error ({tool.name}): {e}")
            return f"[Error] Failed to execute {tool.name}: {e}"

    @staticmethod
    def get_builtin_tools() -> list[dict[str, Any]]:
        """获取内置工具的种子数据"""
        return [
            {
                            "name": "command",
                            "description": (
                                "在本机执行 shell 命令（支持管道/&&/python/pip/npm/git）。"
                                "可选 cwd、timeout、background；后台用 process 轮询。"
                            ),
                            "type": "command",
                            "config": {"timeout": 120, "safe_mode": False},
                            "enabled": True,
                            "is_builtin": True,
                        },
                        {
                            "name": "process",
                            "description": "管理 command 后台进程：list / poll / kill",
                            "type": "process",
                            "config": {},
                            "enabled": True,
                            "is_builtin": True,
                        },
                        {
                            "name": "list_devices_tool",
                            "description": "列出本机 local 与已配对远程设备",
                            "type": "list_devices",
                            "config": {},
                            "enabled": True,
                            "is_builtin": True,
                        },
                        {
                            "name": "remote_exec",
                            "description": "在远程设备或 local 上 exec/list/read",
                            "type": "remote_exec",
                            "config": {},
                            "enabled": True,
                            "is_builtin": True,
                        },
                        {
                            "name": "browser",
                            "description": (
                                "浏览器：fetch HTML，或 Playwright navigate/snapshot/click/type（未装则降级 fetch）"
                            ),
                            "type": "browser",
                            "config": {"timeout": 30},
                            "enabled": True,
                            "is_builtin": True,
                        },
            {
                "name": "file_read",
                "description": (
                    "读取工作区内的文件内容。"
                    "可用于查看代码文件、配置文件、日志文件等。"
                ),
                "type": "file_read",
                "config": {"base_path": "./workspace"},
                "enabled": True,
                "is_builtin": True,
            },
            {
                "name": "file_write",
                "description": (
                    "向工作区内的文件写入内容。"
                    "可用于创建新文件、修改现有文件、生成报告等。"
                ),
                "type": "file_write",
                "config": {"base_path": "./workspace"},
                "enabled": True,
                "is_builtin": True,
            },
            {
                "name": "http_request",
                "description": (
                    "发送 HTTP 请求到指定 API 或 Web 服务。"
                    "支持 GET/POST/PUT/DELETE 方法，可用于调用外部 API。"
                ),
                "type": "http",
                "config": {"timeout": 30},
                "enabled": True,
                "is_builtin": True,
            },
            {
                "name": "python",
                "description": (
                    "在受限环境中执行 Python 代码片段。"
                    "可用于数据计算、文本处理、格式转换等轻量级任务。"
                ),
                "type": "python",
                "config": {"timeout": 30},
                "enabled": True,
                "is_builtin": True,
            },
            {
                "name": "search",
                "description": (
                    "使用 DuckDuckGo 搜索引擎查找网页信息。"
                    "当需要获取最新资讯、查询事实、搜索文档时调用此工具。"
                    "无需 API Key，可直接使用。"
                ),
                "type": "search",
                "config": {"max_results": 5},
                "enabled": True,
                "is_builtin": True,
            },
            {
                "name": "edit",
                "description": (
                    "精确编辑工作区内的现有文件。"
                    "通过指定旧文本和新文本，实现对文件的局部修改。"
                    "适合修改代码、配置文件、文档等。"
                ),
                "type": "edit",
                "config": {"base_path": "./workspace"},
                "enabled": True,
                "is_builtin": True,
            },
            {
                "name": "glob",
                "description": (
                    "使用通配符模式查找工作区内的文件。"
                    "支持 '*.py'、'**/*.json' 等模式，可用于快速定位文件。"
                ),
                "type": "glob",
                "config": {"base_path": "./workspace"},
                "enabled": True,
                "is_builtin": True,
            },
            {
                "name": "grep",
                "description": (
                    "在文件或目录中搜索匹配正则表达式的文本行。"
                    "可用于代码搜索、日志分析、内容查找等场景。"
                ),
                "type": "grep",
                "config": {"base_path": "./workspace"},
                "enabled": True,
                "is_builtin": True,
            },
            {
                "name": "sqlite_query",
                "description": (
                    "执行 SQLite 数据库查询。"
                    "支持 SELECT / INSERT / UPDATE / DELETE / CREATE 等 SQL 语句。"
                    "可用于数据分析、记录查询、轻量级数据存储操作。"
                ),
                "type": "sqlite_query",
                "config": {"base_path": "./workspace"},
                "enabled": True,
                "is_builtin": True,
            },
        ]
