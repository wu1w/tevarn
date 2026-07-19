"""P0–P2 运维向工具：终端会话、远程引导、委派、clarify、session_search、apply_patch。"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any

from backend.tools.base import BaseTool, ToolRiskLevel, ToolSource

logger = logging.getLogger(__name__)

# session_id -> cwd
_SHELL_CWD: dict[str, str] = {}


def _sid(kwargs: dict[str, Any]) -> str:
    return str(kwargs.get("_session_id") or kwargs.get("session_id") or "default")


def _uid(kwargs: dict[str, Any]) -> str | None:
    v = kwargs.get("_user_id") or kwargs.get("user_id")
    return str(v) if v else None


class ShellSessionTool(BaseTool):
    """持久 cwd 的终端会话（P0）。"""

    def __init__(self) -> None:
        super().__init__(
            name="shell_session",
            description=(
                "本机终端会话：保持工作目录。action=pwd|cd|run|reset。"
                "run 在会话 cwd 下执行命令；比单次 command 更适合连续运维。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["pwd", "cd", "run", "reset"],
                        "default": "run",
                    },
                    "path": {"type": "string", "description": "cd 目标路径"},
                    "command": {"type": "string", "description": "run 要执行的命令"},
                    "timeout": {"type": "integer", "default": 120},
                    "background": {"type": "boolean", "default": False},
                },
                "required": [],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.HIGH,
        )

    async def execute(self, **kwargs: Any) -> Any:
        from backend.services.tools.executors import execute_command

        action = str(kwargs.get("action") or "run").lower()
        sid = _sid(kwargs)
        default_cwd = os.getcwd()
        cwd = _SHELL_CWD.get(sid, default_cwd)

        if action == "pwd":
            return f"session={sid}\ncwd={cwd}"
        if action == "reset":
            _SHELL_CWD.pop(sid, None)
            return f"session={sid} cwd reset to process default ({default_cwd})"
        if action == "cd":
            path = (kwargs.get("path") or "").strip()
            if not path:
                return "[Error] path required for cd"
            new = path if os.path.isabs(path) else os.path.abspath(os.path.join(cwd, path))
            if not os.path.isdir(new):
                return f"[Error] not a directory: {new}"
            _SHELL_CWD[sid] = new
            return f"cwd -> {new}"
        # run
        cmd = (kwargs.get("command") or "").strip()
        if not cmd:
            return "[Error] command required for run"
        # support leading cd foo && bar
        m = re.match(r"^cd\s+([^\s&|]+)\s*&&\s*(.+)$", cmd.strip())
        if m:
            rel, rest = m.group(1), m.group(2)
            new = rel if os.path.isabs(rel) else os.path.abspath(os.path.join(cwd, rel))
            if os.path.isdir(new):
                _SHELL_CWD[sid] = new
                cwd = new
                cmd = rest
        return await execute_command(
            {"working_dir": cwd},
            {
                "command": cmd,
                "cwd": cwd,
                "timeout": kwargs.get("timeout", 120),
                "background": bool(kwargs.get("background")),
                "_ws_manager": kwargs.get("_ws_manager"),
                "_session_id": kwargs.get("_session_id"),
            },
        )


class DeviceOnboardTool(BaseTool):
    """远程设备开箱引导（P0）。"""

    def __init__(self) -> None:
        super().__init__(
            name="device_onboard",
            description=(
                "远程设备开箱：action=guide|discover|status。"
                "说明如何安装/配对 takton-agent；discover 扫描局域网；status 看已配对设备。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["guide", "discover", "status"],
                        "default": "guide",
                    },
                    "timeout_ms": {"type": "integer", "default": 2500},
                },
                "required": [],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.SAFE,
        )

    async def execute(self, **kwargs: Any) -> Any:
        action = str(kwargs.get("action") or "guide").lower()
        if action == "guide":
            return (
                "# 远程设备开箱（takton-agent）\n"
                "1. 在目标机器安装并启动 takton-agent（L1），记下 host:port 与 token。\n"
                "2. 本机 UI 打开 /devices，或 API：\n"
                "   POST /api/devices/pair  {\"name\":\"remote-pc\",\"host\":\"192.168.x.x\",\"port\":7xxx,\"token\":\"...\"}\n"
                "3. 验证：list_devices_tool 或 device_onboard action=status\n"
                "4. 执行：remote_exec device=remote-pc command=\"hostname\"\n"
                "   或在对话里：@remote-pc hostname\n"
                "5. 本机无需配对，直接 remote_exec device=local 或 command 工具。\n"
                "\n"
                "Discover: device_onboard action=discover（mDNS 扫描局域网 agent）\n"
            )
        if action == "discover":
            try:
                from backend.services.remote.mdns import browse_agents

                found = await browse_agents(timeout_ms=int(kwargs.get("timeout_ms") or 2500))
                if not found:
                    return "No agents discovered on LAN (mDNS). Start takton-agent or pair manually."
                return json.dumps(found, ensure_ascii=False, indent=2)
            except Exception as e:
                return f"[Error] discover failed: {e}"
        # status
        from backend.services.tools.executors import execute_list_devices

        return await execute_list_devices({}, {"_user_id": _uid(kwargs)})


class PlaywrightDoctorTool(BaseTool):
    """Playwright 可用性探测/安装指引（P1）。"""

    def __init__(self) -> None:
        super().__init__(
            name="playwright_doctor",
            description=(
                "检查 Playwright/Chromium 是否可用；action=status|install_hint|"
                "try_launch。install_hint 给出本机安装命令（不自动全局改系统）。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["status", "install_hint", "try_launch"],
                        "default": "status",
                    }
                },
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.LOW,
        )

    async def execute(self, **kwargs: Any) -> Any:
        action = str(kwargs.get("action") or "status").lower()
        has_mod = False
        try:
            import playwright  # noqa: F401

            has_mod = True
            ver = getattr(playwright, "__version__", "?")
        except Exception as e:
            ver = str(e)

        if action == "install_hint":
            py = sys_executable()
            return (
                f"Playwright module: {'OK '+str(ver) if has_mod else 'MISSING'}\n"
                f"Install (current interpreter):\n"
                f"  \"{py}\" -m pip install playwright\n"
                f"  \"{py}\" -m playwright install chromium\n"
                "Then browser action=navigate|snapshot|click will use real automation; "
                "otherwise falls back to fetch HTML."
            )

        if action == "try_launch":
            if not has_mod:
                return "[FAIL] playwright package not installed. Use action=install_hint."
            try:
                from playwright.async_api import async_playwright

                pw = await async_playwright().start()
                browser = await pw.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.goto("https://example.com", wait_until="domcontentloaded", timeout=20000)
                title = await page.title()
                await browser.close()
                await pw.stop()
                return f"[OK] Chromium launched. title={title}"
            except Exception as e:
                return (
                    f"[FAIL] launch error: {e}\n"
                    "Often need: python -m playwright install chromium"
                )

        return (
            f"playwright_package={'yes' if has_mod else 'no'} version={ver}\n"
            f"tip: playwright_doctor action=try_launch | install_hint"
        )


def sys_executable() -> str:
    import sys

    return sys.executable


class DelegateTaskTool(BaseTool):
    """委派子任务给子代理/集群（P1）。"""

    def __init__(self) -> None:
        super().__init__(
            name="delegate_task",
            description=(
                "把子任务委派给子代理。action=list_agents|run。"
                "run 需要 goal；可选 agent_id/name。用于拆分编码/调研等并行工作。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list_agents", "run"],
                        "default": "run",
                    },
                    "goal": {"type": "string", "description": "子任务目标"},
                    "agent_id": {"type": "string"},
                    "agent_name": {"type": "string"},
                    "context": {"type": "string"},
                },
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.MEDIUM,
        )

    async def execute(self, **kwargs: Any) -> Any:
        action = str(kwargs.get("action") or "run").lower()
        try:
            from backend.repositories.sub_agent_repo import AsyncSubAgentRepository
        except Exception:
            try:
                from backend.repositories.sub_agent_repo import SubAgentRepository as AsyncSubAgentRepository
            except Exception as e:
                return f"[Error] subagent repo unavailable: {e}"

        repo = AsyncSubAgentRepository()
        agents = []
        try:
            if hasattr(repo, "list_enabled"):
                agents = await repo.list_enabled()  # type: ignore
            elif hasattr(repo, "list_all"):
                agents = await repo.list_all()  # type: ignore
            elif hasattr(repo, "list"):
                agents = await repo.list()  # type: ignore
        except Exception as e:
            return f"[Error] list agents failed: {e}"

        if action == "list_agents":
            if not agents:
                return "No sub-agents configured. Create at /profiles."
            lines = []
            for a in agents:
                lines.append(
                    f"- id={getattr(a,'id', '?')} name={getattr(a,'name','?')} "
                    f"enabled={getattr(a,'enabled', True)} model={getattr(a,'model_ref', getattr(a,'model',''))}"
                )
            return "Sub-agents:\n" + "\n".join(lines)

        goal = (kwargs.get("goal") or "").strip()
        if not goal:
            return "[Error] goal required for run"

        # pick agent
        target = None
        aid = (kwargs.get("agent_id") or "").strip()
        aname = (kwargs.get("agent_name") or "").strip()
        for a in agents or []:
            if aid and str(getattr(a, "id", "")) == aid:
                target = a
                break
            if aname and str(getattr(a, "name", "")).lower() == aname.lower():
                target = a
                break
        if target is None and agents:
            target = agents[0]
        if target is None:
            return "[Error] no sub-agent available. Create one in /profiles first."

        # try cluster quick path or simple LLM complete with agent prompt
        ctx = (kwargs.get("context") or "").strip()
        prompt = f"You are sub-agent «{getattr(target,'name','agent')}».\nGoal: {goal}\n"
        if ctx:
            prompt += f"Context:\n{ctx}\n"
        prompt += "Complete the goal and return a concise result."

        try:
            from backend.services.llm.factory import LLMServiceFactory

            svc = LLMServiceFactory.get_service()
            # chat_complete if exists
            if hasattr(svc, "chat_complete"):
                text = await svc.chat_complete(
                    [{"role": "user", "content": prompt}],
                    temperature=0.3,
                )
            else:
                chunks = []
                async for ch in svc.chat([{"role": "user", "content": prompt}]):
                    c = getattr(ch, "content", None) or getattr(ch, "delta", None) or ""
                    if c:
                        chunks.append(c)
                text = "".join(chunks)
            return (
                f"[delegate_task -> {getattr(target,'name', target)}]\n"
                f"{text or '(empty response)'}"
            )
        except Exception as e:
            return f"[Error] delegate_task LLM failed: {e}"


class ClarifyTool(BaseTool):
    """向用户提问并等待回答（P2）。"""

    def __init__(self) -> None:
        super().__init__(
            name="clarify",
            description=(
                "向用户提出一个必须确认的问题并等待回答（WS 弹窗）。"
                "用于歧义决策；超时默认取消。question 必填；options 可选最多 4 个。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "可选选项（最多 4 个）",
                    },
                    "timeout": {"type": "integer", "default": 60},
                },
                "required": ["question"],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.LOW,
        )

    async def execute(self, **kwargs: Any) -> Any:
        from backend.services import confirm_manager

        q = (kwargs.get("question") or "").strip()
        if not q:
            return "[Error] question required"
        opts = kwargs.get("options") or []
        if isinstance(opts, list) and opts:
            opt_txt = "\n".join(f"- {o}" for o in opts[:4])
            cmd = f"{q}\nOptions:\n{opt_txt}"
        else:
            cmd = q
        approved = await confirm_manager.request_confirmation(
            kwargs.get("_ws_manager"),
            kwargs.get("_session_id"),
            title="需要你的确认",
            command=cmd,
            reason="clarify",
            timeout=float(kwargs.get("timeout") or 60),
        )
        # resolve_confirmation only returns bool; map to yes/no
        return "User approved." if approved else "User denied or timed out."


class SessionSearchTool(BaseTool):
    """搜索历史会话消息（P2）。"""

    def __init__(self) -> None:
        super().__init__(
            name="session_search",
            description=(
                "在用户历史会话消息中搜索关键词。query 必填；"
                "limit 默认 10。返回 session_id / role / 摘要片段。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                    "session_id": {
                        "type": "string",
                        "description": "可选，限定单个会话",
                    },
                },
                "required": ["query"],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.SAFE,
        )

    async def execute(self, **kwargs: Any) -> Any:
        query = (kwargs.get("query") or "").strip()
        if not query:
            return "[Error] query required"
        limit = max(1, min(int(kwargs.get("limit") or 10), 50))
        sid = (kwargs.get("session_id") or "").strip()
        uid = _uid(kwargs)

        try:
            from backend.database import AsyncSessionLocal
        except Exception as e:
            return await self._fallback_sqlite(query, limit, sid, uid, err=str(e))

        try:
            async with AsyncSessionLocal() as db:
                from sqlalchemy import text

                sql = """
                    SELECT m.session_id, m.role, m.content, m.created_at
                    FROM messages m
                    JOIN sessions s ON s.id = m.session_id
                    WHERE m.content LIKE :q
                """
                params: dict[str, Any] = {"q": f"%{query}%"}
                if sid:
                    sql += " AND cast(m.session_id as text) = :sid"
                    params["sid"] = sid
                if uid:
                    sql += " AND cast(s.user_id as text) = :uid"
                    params["uid"] = uid
                sql += " ORDER BY m.created_at DESC LIMIT :lim"
                params["lim"] = limit
                rows = (await db.execute(text(sql), params)).fetchall()
                if not rows:
                    return f"No messages matched «{query}»."
                lines = []
                for r in rows:
                    content = (r[2] or "")[:180].replace("\n", " ")
                    lines.append(f"- session={r[0]} role={r[1]} at={r[3]} :: {content}")
                return "\n".join(lines)
        except Exception as e:
            return await self._fallback_sqlite(query, limit, sid, uid, err=str(e))

    async def _fallback_sqlite(
        self, query: str, limit: int, sid: str, uid: str | None, err: str = ""
    ) -> str:
        import sqlite3

        # try common db paths
        cands = [
            Path("backend/takton.db"),
            Path("takton.db"),
            Path(os.path.expandvars(r"%APPDATA%/takton/data/takton.db")),
            Path(os.path.expanduser("~/.takton/data/takton.db")),
        ]
        dbp = next((p for p in cands if p.exists()), None)
        if not dbp:
            return f"[Error] session_search failed ({err}); no local takton.db found"

        def _run() -> str:
            con = sqlite3.connect(str(dbp))
            try:
                sql = "SELECT session_id, role, content, created_at FROM messages WHERE content LIKE ?"
                args: list[Any] = [f"%{query}%"]
                if sid:
                    sql += " AND session_id=?"
                    args.append(sid)
                sql += " ORDER BY created_at DESC LIMIT ?"
                args.append(limit)
                rows = con.execute(sql, args).fetchall()
            finally:
                con.close()
            if not rows:
                return f"No messages matched «{query}»."
            lines = []
            for r in rows:
                content = (r[2] or "")[:180].replace("\n", " ")
                lines.append(f"- session={r[0]} role={r[1]} at={r[3]} :: {content}")
            return "\n".join(lines)

        return await asyncio.to_thread(_run)


class ApplyPatchTool(BaseTool):
    """简易 multi-hunk patch（P2，对标 coding agent patch）。"""

    def __init__(self) -> None:
        super().__init__(
            name="apply_patch",
            description=(
                "对工作区文件做精确替换补丁。传 filepath + old_text + new_text；"
                "或 patches 数组 [{filepath, old_text, new_text}, ...]。"
                "old_text 必须在文件中唯一出现。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "filepath": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                    "patches": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "filepath": {"type": "string"},
                                "old_text": {"type": "string"},
                                "new_text": {"type": "string"},
                            },
                        },
                    },
                },
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.HIGH,
            requires_confirmation=False,
        )

    async def execute(self, **kwargs: Any) -> Any:
        from backend.services.tools.executors import execute_edit
        from backend.tools.permissions import ToolPermissionManager

        mgr = ToolPermissionManager()
        base = {"base_path": mgr.workspace_root}
        patches = kwargs.get("patches")
        if not patches:
            patches = [
                {
                    "filepath": kwargs.get("filepath"),
                    "old_text": kwargs.get("old_text"),
                    "new_text": kwargs.get("new_text"),
                }
            ]
        if not isinstance(patches, list) or not patches:
            return "[Error] provide filepath/old_text/new_text or patches[]"

        results = []
        for i, p in enumerate(patches):
            if not isinstance(p, dict):
                results.append(f"[{i}] invalid patch object")
                continue
            fp, ot, nt = p.get("filepath"), p.get("old_text"), p.get("new_text")
            if not fp or ot is None or nt is None:
                results.append(f"[{i}] missing filepath/old_text/new_text")
                continue
            r = await execute_edit(base, {"filepath": fp, "old_text": ot, "new_text": nt})
            results.append(f"[{i}] {fp}: {r}")
        return "\n".join(results)


class VisionAnalyzeTool(BaseTool):
    """用当前 LLM 做图片理解（P2，有 vision 的模型才有效）。"""

    def __init__(self) -> None:
        super().__init__(
            name="vision_analyze",
            description=(
                "分析图片。image_path 为本地路径或 http(s) URL；question 为问题。"
                "依赖当前模型的多模态能力；不支持时返回明确错误。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "image_path": {"type": "string"},
                    "question": {"type": "string", "default": "Describe this image."},
                },
                "required": ["image_path"],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.LOW,
        )

    async def execute(self, **kwargs: Any) -> Any:
        import base64
        from pathlib import Path

        path = (kwargs.get("image_path") or "").strip()
        question = (kwargs.get("question") or "Describe this image.").strip()
        if not path:
            return "[Error] image_path required"

        # build data url
        if path.startswith("http://") or path.startswith("https://"):
            url = path
            content = [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": url}},
            ]
        else:
            p = Path(path)
            if not p.exists():
                return f"[Error] file not found: {path}"
            raw = p.read_bytes()
            if len(raw) > 8_000_000:
                return "[Error] image too large (>8MB)"
            mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
            b64 = base64.b64encode(raw).decode("ascii")
            content = [
                {"type": "text", "text": question},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                },
            ]

        try:
            from backend.services.llm.factory import LLMServiceFactory

            svc = LLMServiceFactory.get_service()
            messages = [{"role": "user", "content": content}]
            if hasattr(svc, "chat_complete"):
                try:
                    text = await svc.chat_complete(messages, temperature=0.2)
                except Exception as e:
                    return (
                        f"[Error] vision call failed: {e}\n"
                        "Current model/provider may not support image_url content."
                    )
            else:
                return "[Error] LLM service has no chat_complete for vision payloads"
            return text or "(empty vision response)"
        except Exception as e:
            return f"[Error] vision_analyze: {e}"


AGENT_OPS_TOOL_CLASSES = [
    ShellSessionTool,
    DeviceOnboardTool,
    PlaywrightDoctorTool,
    DelegateTaskTool,
    ClarifyTool,
    SessionSearchTool,
    ApplyPatchTool,
    VisionAnalyzeTool,
]
