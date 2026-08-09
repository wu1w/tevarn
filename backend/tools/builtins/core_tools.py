"""
内置工具集合（v3.0 统一抽象）

将 backend.services.tools.executors 中的执行器包装成 BaseTool 子类，
并接入统一 ToolRegistry。

这些工具作为 BUILTIN 来源，优先级高于 DB 工具，
意味着即使数据库中存在同名的旧工具配置，也会使用这里的实现和 schema。
"""

from __future__ import annotations

from typing import Any

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
        # 取 executor 时禁止 getattr(self, "_executor")：
        # 类上挂的函数经实例访问会被 Python 绑成 bound method，多注入 self，
        # 触发 TypeError: execute_xxx() takes 2 positional arguments but 3 were given。
        if "_executor" in getattr(self, "__dict__", {}):
            executor = self.__dict__["_executor"]
        else:
            executor = type(self)._executor
        if executor is None:
            return (
                f"[Error] 工具 «{self.name}» 未绑定执行器（_executor is None）。"
                "这是 Tevarn 内部注册问题：请重启后端；若仍失败请检查 "
                "backend.tools.builtins.core_tools 是否正确 import executors。"
            )
        # 注入 workspace root 作为 base_path，而非传空 config
        config = self._get_config()
        try:
            return await executor(config, kwargs)
        except TypeError as e:
            # 仍可能是签名错配：给出可读说明，勿吞成空 [Error]
            return (
                f"[Error] 工具 «{self.name}» 调用签名不匹配（{e}）。"
                "execute_* 应为 (config, arguments) 两参数。"
            )
        except Exception as e:
            msg = str(e).strip() or type(e).__name__
            return f"[Error] 工具 «{self.name}» 执行异常（{type(e).__name__}）: {msg}"


class FileReadTool(_BuiltinToolBase):
    _executor = execute_file_read

    def __init__(self):
        super().__init__(
            name="file_read",
            description=(
                "读取工作区内文件正文，输出带行号（`行号 TAB 正文`）。改代码/查配置前必须先读再改。"
                "大文件会在行边界处停止并提示续读的 offset——看到该提示就再调一次补齐，"
                "不要基于不完整内容改代码。大文件也可先 grep/glob 定位再按 offset 精读。"
                "重要：行号是展示前缀，不属于文件内容，写 edit 的 old_text 时不要带上。"
                "失败时检查路径是否在 workspace 内、文件是否存在。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "要读取的文件路径"},
                    "offset": {
                        "type": "integer",
                        "description": "起始行号（1-based），续读大文件时用，默认 1",
                        "default": 1,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "读取行数，默认 1000",
                        "default": 1000,
                    },
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
            description=(
                "整文件写入（覆盖）。新建小文件或全量重写时用；"
                "局部修改优先用 edit/apply_patch，避免误覆盖。"
                "filepath + content 必填。写前确认路径，写后必要时再 file_read 校验。"
            ),
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
            description=(
                "在文件中精确替换一段唯一字符串（old_text→new_text）。"
                "old_text 必须在文件中唯一且与原文完全一致（含缩进）；"
                "不唯一或找不到会报错并告诉你重复出现的行号——先 file_read/grep 再改。"
                "要替换全部同名片段时显式传 replace_all=true。"
                "注意：file_read 输出的行号前缀（如 `   12\\t`）是展示用的，"
                "不属于文件内容，写 old_text 时不要带上。"
                "适合小范围修改；大块重构可用 apply_patch 或 file_write。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "文件路径"},
                    "old_text": {"type": "string", "description": "要替换的文本（须唯一）"},
                    "new_text": {"type": "string", "description": "新文本"},
                    "replace_all": {
                        "type": "boolean",
                        "description": "替换全部匹配（默认 false：多处匹配时报错）",
                        "default": False,
                    },
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
            description=(
                "按通配符列文件路径（如 **/*.py、frontend/**/*.tsx）。"
                "不知道确切路径时先 glob 再 file_read。"
                "pattern 必填；结果过多时收窄 pattern。"
            ),
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
            description=(
                "在路径下用正则搜内容（定位符号/报错/配置键）。"
                "pattern + path 必填；recursive 默认 true。"
                "找到候选后用 file_read 读上下文再 edit。"
            ),
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
                "在本机执行 shell 命令（支持管道/&&/python/pip/npm/git、多行 heredoc）。"
                "默认 cwd=workspace 根；可用 cwd 覆盖到任务目录。"
                "写大段文件请用 file_write/edit，不要用 cat 拼巨型脚本。"
                "timeout 默认120；background=true 后台跑，用 process poll/kill。"
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
                "列出本机 local 与已配对远程设备（tevarn-agent）。"
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
            description=(
                "对 URL 发 HTTP 请求（GET/POST/…）。"
                "查 API/健康检查/webhook 时用；需要完整网页正文可 browser 或 fetch_webpage。"
                "url 必填；注意超时与非 2xx 响应体。"
            ),
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
            description=(
                "在受控环境执行短 Python 片段（计算、解析、小脚本）。"
                "不要用它代替 file_write 写大段工程代码；"
                "需要系统包/长驻进程用 command。code 必填。"
            ),
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
            description=(
                "网络搜索（优先 Tavily，否则 DDG/Bing）。需要最新事实、文档、新闻时必须调用，"
                "禁止空口编造。query 必填；max_results 默认 5。"
                "与 web_search 同类；任选其一即可，勿重复空转。"
            ),
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


async def execute_result_load(config: dict[str, Any], arguments: dict[str, Any]) -> str:
    """加载 kernel result_spill 外置的完整工具结果（支持 offset 分页）。

    必须绑定调用方 process_id（loop 注入 _kernel_process_id），
    防止横向读取其它进程的 spill 全文。
    """
    hid = str(
        arguments.get("id")
        or arguments.get("handle_id")
        or arguments.get("handle")
        or ""
    ).strip()
    if not hid:
        return (
            "[Error] id is required "
            "(from [tool_result_handle id=…] spill message)"
        )
    # 兼容模型传入 "id=abc" 或整段 handle 行
    if hid.startswith("id="):
        hid = hid[3:].strip()
    if " " in hid:
        hid = hid.split()[0]
    # 只信任 loop 注入的 process，禁止模型在 arguments 里伪造
    pid = str(
        arguments.get("_kernel_process_id")
        or arguments.get("_process_id")
        or ""
    ).strip()
    if not pid:
        return (
            "[Error] result_load requires bound process "
            "(missing _kernel_process_id; call from agent loop only)"
        )
    try:
        offset = max(0, int(arguments.get("offset") or 0))
    except (TypeError, ValueError):
        offset = 0
    try:
        max_c = int(arguments.get("max_chars") or 50_000)
    except (TypeError, ValueError):
        max_c = 50_000
    max_c = max(500, min(max_c, 200_000))

    try:
        from backend.kernel import get_kernel

        k = get_kernel()
        r: dict[str, Any] = {}
        if hasattr(k, "result_load"):
            try:
                r = k.result_load(hid, process_id=pid) or {}
            except TypeError:
                # 旧签名兼容（仍传 process_id 进 _call）
                r = k.result_load(hid) or {}
        elif hasattr(k, "_acall"):
            # audit-fix: async 上下文走 _acall，避免阻塞事件循环
            r = await k._acall(
                "result_load", {"handle_id": hid, "process_id": pid}
            ) or {}
        else:
            return "[Error] kernel result_load unavailable"
        if isinstance(r, dict):
            if r.get("error"):
                return f"[Error] result_load: {r.get('error')}"
            msg = str(r.get("message") or "")
            if msg and "process" in msg.lower() and "content" not in r:
                return f"[Error] result_load: {msg}"
            # host 可能返回 {content} / {body} / {text}
            body = ""
            for key in ("content", "body", "text", "result"):
                if key in r and r[key] is not None:
                    body = str(r[key])
                    break
            if not body and r:
                import json

                body = json.dumps(r, ensure_ascii=False, default=str)

            total = len(body)
            if offset >= total:
                return (
                    f"[result_load id={hid} offset={offset} total={total}]\n"
                    "(empty — offset past end)"
                )
            chunk = body[offset : offset + max_c]
            end = offset + len(chunk)
            more = end < total
            header = (
                f"[result_load id={hid} offset={offset} end={end} "
                f"total={total} page_chars={len(chunk)}]\n"
            )
            footer = ""
            if more:
                footer = (
                    f"\n...[more available: call result_load "
                    f"id=\"{hid}\" offset={end} max_chars={max_c}]"
                )
            else:
                footer = f"\n...[end of result id={hid}]"
            return header + chunk + footer
        return str(r)
    except Exception as e:
        return f"[Error] result_load failed: {e}"


class ResultLoadTool(_BuiltinToolBase):
    """大工具结果外置后的回读（与 result_spill 配对；支持 offset 分页）。"""

    _executor = execute_result_load

    def __init__(self):
        super().__init__(
            name="result_load",
            description=(
                "加载被外置存储的完整工具结果（长文分析请分页，不要重跑原工具）。"
                "当上一条工具返回含 [tool_result_handle id=…] 时调用："
                "result_load(id=…, offset=0, max_chars=20000) 取第一页；"
                "用返回的 next offset 继续翻页。"
                "不要猜测 id；不要为「拿全文」重新执行 python/command/http。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "result handle id（来自 tool_result_handle）",
                    },
                    "handle_id": {
                        "type": "string",
                        "description": "id 的别名",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "字符偏移（分页），默认 0",
                        "default": 0,
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "本页最大字符数，默认 50000，上限 200000",
                        "default": 50000,
                    },
                },
                "required": ["id"],
            },
            risk_level=ToolRiskLevel.LOW,
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
    ResultLoadTool,
]

# ── Agent 自配置工具（v3.1） ──
# 这些工具使用新的 Tool 基类（非 BaseTool），通过独立模块注册
try:
    from backend.tools.builtins.self_config import (
        GetSystemStatus,
        ListAvailableModels,
        ManageCron,
        ManageKnowledge,
        UpdateConfig,
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
