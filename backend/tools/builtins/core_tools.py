"""
内置工具集合（v3.0 统一抽象）

将 backend.services.tools.executors 中的执行器包装成 BaseTool 子类，
并接入统一 ToolRegistry。

这些工具作为 BUILTIN 来源，优先级高于 DB 工具，
意味着即使数据库中存在同名的旧工具配置，也会使用这里的实现和 schema。
"""

from __future__ import annotations

from backend.services.tools.executors import (
    execute_browser,
    execute_command,
    execute_edit,
    execute_file_read,
    execute_file_write,
    execute_glob,
    execute_grep,
    execute_http,
    execute_list_devices,
    execute_process,
    execute_python,
    execute_remote_exec,
    execute_search,
    execute_sqlite_query,
)
from backend.tools.base import BaseTool, ToolRiskLevel, ToolSource


class _BuiltinToolBase(BaseTool):
    """内置工具基类：持有默认 schema 和 executor"""

    _executor = None

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict,
        risk_level: ToolRiskLevel = ToolRiskLevel.MEDIUM,
        requires_confirmation: bool = False,
    ):
        super().__init__(
            name=name,
            description=description,
            parameters=parameters,
            source=ToolSource.BUILTIN,
            risk_level=risk_level,
            enabled=True,
            requires_confirmation=requires_confirmation,
        )

    def _get_config(self) -> dict:
        """构建 executor 所需的 config，注入 workspace root 作为 base_path"""
        from backend.tools.permissions import ToolPermissionManager

        mgr = ToolPermissionManager()
        return {"base_path": mgr.workspace_root}

    async def execute(self, **kwargs):
        executor = type(self)._executor
        if executor is None:
            raise NotImplementedError
        # 注入 workspace root 作为 base_path，而非传空 config
        config = self._get_config()
        return await executor(config, kwargs)


class FileReadTool(_BuiltinToolBase):
    _executor = execute_file_read

    def __init__(self):
        super().__init__(
            name="file_read",
            description="读取指定文件内容",
            parameters={
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "要读取的文件路径"}
                },
                "required": ["filepath"],
            },
            risk_level=ToolRiskLevel.SAFE,
        )


class FileWriteTool(_BuiltinToolBase):
    _executor = execute_file_write

    def __init__(self):
        super().__init__(
            name="file_write",
            description="写入内容到指定文件",
            parameters={
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "要写入的文件路径"},
                    "content": {"type": "string", "description": "文件内容"},
                },
                "required": ["filepath", "content"],
            },
            risk_level=ToolRiskLevel.HIGH,
            requires_confirmation=True,
        )


class EditTool(_BuiltinToolBase):
    _executor = execute_edit

    def __init__(self):
        super().__init__(
            name="edit",
            description="在文件中精确替换字符串",
            parameters={
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "文件路径"},
                    "old_text": {"type": "string", "description": "要替换的文本"},
                    "new_text": {"type": "string", "description": "新文本"},
                },
                "required": ["filepath", "old_text", "new_text"],
            },
            risk_level=ToolRiskLevel.HIGH,
            requires_confirmation=True,
        )


class GlobTool(_BuiltinToolBase):
    _executor = execute_glob

    def __init__(self):
        super().__init__(
            name="glob",
            description="使用通配符搜索文件",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "通配符模式"}
                },
                "required": ["pattern"],
            },
            risk_level=ToolRiskLevel.SAFE,
        )


class GrepTool(_BuiltinToolBase):
    _executor = execute_grep

    def __init__(self):
        super().__init__(
            name="grep",
            description="在文件中搜索正则表达式",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "正则表达式"},
                    "path": {"type": "string", "description": "搜索路径"},
                    "recursive": {
                        "type": "boolean",
                        "description": "是否递归",
                        "default": True,
                    },
                },
                "required": ["pattern", "path"],
            },
            risk_level=ToolRiskLevel.SAFE,
        )


class CommandTool(_BuiltinToolBase):
    _executor = execute_command

    def __init__(self):
        super().__init__(
            name="command",
            description=(
                "在本机执行 shell 命令（支持管道/&&/python/pip/npm/git）。"
                "可选 cwd 工作目录、timeout（秒，默认120）、background=true 后台运行。"
                "后台任务用 process 工具 poll/kill。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "shell 命令"},
                    "cwd": {
                        "type": "string",
                        "description": "工作目录（绝对路径或可解析路径）",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数（前台，默认120，最大600）",
                        "default": 120,
                    },
                    "background": {
                        "type": "boolean",
                        "description": "true=后台执行并返回 process_id",
                        "default": False,
                    },
                    "max_output": {
                        "type": "integer",
                        "description": "stdout 最大字符数",
                        "default": 50000,
                    },
                },
                "required": ["command"],
            },
            risk_level=ToolRiskLevel.HIGH,
            requires_confirmation=False,
        )


class BrowserTool(_BuiltinToolBase):
    _executor = execute_browser

    def __init__(self):
        super().__init__(
            name="browser",
            description=(
                "浏览器工具。action=fetch 拉 HTML；navigate/snapshot/click/type/press/screenshot "
                "需 Playwright（未安装则 navigate 降级为 fetch）。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "fetch|navigate|snapshot|click|type|press|screenshot|close",
                        "default": "fetch",
                    },
                    "url": {"type": "string", "description": "网页 URL"},
                    "selector": {"type": "string", "description": "CSS 选择器（click/type）"},
                    "text": {"type": "string", "description": "输入文本（type）"},
                    "key": {"type": "string", "description": "按键（press，如 Enter）"},
                    "session": {
                        "type": "string",
                        "description": "浏览器会话名（默认 default）",
                        "default": "default",
                    },
                    "timeout": {"type": "integer", "default": 30},
                },
                "required": [],
            },
            risk_level=ToolRiskLevel.LOW,
        )


class ProcessTool(_BuiltinToolBase):
    _executor = execute_process

    def __init__(self):
        super().__init__(
            name="process",
            description="管理 command background 后台进程：list / poll / kill",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "poll", "kill"],
                        "default": "list",
                    },
                    "process_id": {"type": "string", "description": "后台进程 id"},
                },
                "required": [],
            },
            risk_level=ToolRiskLevel.MEDIUM,
        )


class ListDevicesTool(_BuiltinToolBase):
    _executor = execute_list_devices

    def __init__(self):
        super().__init__(
            name="list_devices_tool",
            description=(
                "列出本机 local 与已配对远程设备（takton-agent）。"
                "操作远程前先调用；也可用 chat @设备名 命令。"
            ),
            parameters={"type": "object", "properties": {}, "required": []},
            risk_level=ToolRiskLevel.SAFE,
        )


class RemoteExecTool(_BuiltinToolBase):
    _executor = execute_remote_exec

    def __init__(self):
        super().__init__(
            name="remote_exec",
            description=(
                "在远程设备或本机 local 上执行命令/列目录/读文件。"
                "action=exec|list|read；device=设备名或 local。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "device": {
                        "type": "string",
                        "description": "设备名，local=本机",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["exec", "list", "read"],
                        "default": "exec",
                    },
                    "command": {"type": "string", "description": "shell 命令（exec）"},
                    "path": {"type": "string", "description": "路径（list/read）"},
                    "timeout": {"type": "integer", "default": 45},
                },
                "required": ["device"],
            },
            risk_level=ToolRiskLevel.HIGH,
        )


class HttpTool(_BuiltinToolBase):
    _executor = execute_http

    def __init__(self):
        super().__init__(
            name="http",
            description="发送 HTTP 请求",
            parameters={
                "type": "object",
                "properties": {
                    "method": {
                        "type": "string",
                        "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"],
                        "default": "GET",
                    },
                    "url": {"type": "string", "description": "请求地址"},
                    "headers": {"type": "object", "default": {}},
                    "body": {"type": "object", "default": {}},
                },
                "required": ["url"],
            },
            risk_level=ToolRiskLevel.MEDIUM,
        )


class PythonTool(_BuiltinToolBase):
    _executor = execute_python

    def __init__(self):
        super().__init__(
            name="python",
            description="执行 Python 代码片段",
            parameters={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python 代码"},
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数",
                        "default": 30,
                    },
                },
                "required": ["code"],
            },
            risk_level=ToolRiskLevel.DANGEROUS,
            requires_confirmation=True,
        )


class SearchTool(_BuiltinToolBase):
    _executor = execute_search

    def __init__(self):
        super().__init__(
            name="search",
            description="网络搜索（DuckDuckGo/Bing）",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "max_results": {
                        "type": "integer",
                        "description": "最大结果数",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
            risk_level=ToolRiskLevel.LOW,
        )


class SQLiteQueryTool(_BuiltinToolBase):
    _executor = execute_sqlite_query

    def __init__(self):
        super().__init__(
            name="sqlite_query",
            description="执行 SQLite 查询",
            parameters={
                "type": "object",
                "properties": {
                    "database": {"type": "string", "description": "数据库路径"},
                    "query": {"type": "string", "description": "SQL 语句"},
                },
                "required": ["database", "query"],
            },
            risk_level=ToolRiskLevel.HIGH,
            requires_confirmation=True,
        )


# 所有内置工具类
BUILTIN_TOOL_CLASSES = [
    FileReadTool,
    FileWriteTool,
    EditTool,
    GlobTool,
    GrepTool,
    CommandTool,
    BrowserTool,
    ProcessTool,
    ListDevicesTool,
    RemoteExecTool,
    HttpTool,
    PythonTool,
    SearchTool,
    SQLiteQueryTool,
]

# ── Agent 自配置工具（v3.1） ──
# 这些工具使用新的 Tool 基类（非 BaseTool），通过独立模块注册
try:
    from backend.tools.builtins.self_config import (
        GetSystemStatus,
        UpdateConfig,
        ListAvailableModels,
        ManageKnowledge,
        ManageCron,
    )

    SELF_CONFIG_TOOLS = [
        GetSystemStatus,
        UpdateConfig,
        ListAvailableModels,
        ManageKnowledge,
        ManageCron,
    ]
except ImportError:
    SELF_CONFIG_TOOLS = []
