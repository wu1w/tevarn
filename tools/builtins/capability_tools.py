"""
P0–P2 能力补齐工具：
- autopilot: 长程任务 plan/status/next/reflect/complete
- memory_pref: 跨会话用户偏好
- github_*: gh CLI 协作
- desktop_observe: 截图+视觉理解闭环
- uia_snapshot: Windows UIA/控件树
- browser 增强见 executors（会话态）
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from backend.tools.base import BaseTool, ToolRiskLevel, ToolSource

logger = logging.getLogger(__name__)

# session_id -> autopilot state
_AUTOPILOTS: dict[str, dict[str, Any]] = {}
_PREFS_DIR = Path(os.path.expanduser("~/.takton/memory"))
_PREFS_FILE = _PREFS_DIR / "user_preferences.json"


def _sid(kwargs: dict[str, Any]) -> str:
    return str(kwargs.get("_session_id") or kwargs.get("session_id") or "default")


def _uid(kwargs: dict[str, Any]) -> str:
    return str(kwargs.get("_user_id") or kwargs.get("user_id") or "local")


def _load_prefs(uid: str) -> dict[str, Any]:
    _PREFS_DIR.mkdir(parents=True, exist_ok=True)
    if not _PREFS_FILE.exists():
        return {"users": {}}
    try:
        data = json.loads(_PREFS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"users": {}}
        data.setdefault("users", {})
        return data
    except Exception:
        return {"users": {}}


def _save_prefs(data: dict[str, Any]) -> None:
    _PREFS_DIR.mkdir(parents=True, exist_ok=True)
    _PREFS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Autopilot ──────────────────────────────────────────────


class AutopilotTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(
            name="autopilot",
            description=(
                "长程自主任务引擎。接收单一目标后：plan 拆步 → status 看进度 → "
                "next 取当前应执行步骤 → reflect 记录结果/改写计划 → complete 交付。"
                "复杂多步调研/开发/运维任务优先用本工具，不要只口头规划。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["start", "plan", "status", "next", "reflect", "complete", "cancel"],
                        "description": "操作",
                    },
                    "goal": {"type": "string", "description": "总目标（start/plan）"},
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "plan 时可直接给步骤列表；省略则自动拆解",
                    },
                    "note": {"type": "string", "description": "reflect 结果/观察"},
                    "step_status": {
                        "type": "string",
                        "enum": ["done", "failed", "blocked", "skip"],
                        "description": "reflect 时当前步状态",
                    },
                    "replan_steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "reflect 失败时可重写剩余步骤",
                    },
                    "summary": {"type": "string", "description": "complete 交付摘要"},
                    "artifacts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "交付物路径列表",
                    },
                },
                "required": ["action"],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.MEDIUM,
        )

    def _state(self, sid: str) -> dict[str, Any] | None:
        return _AUTOPILOTS.get(sid)

    def _fmt(self, st: dict[str, Any]) -> str:
        steps = st.get("steps") or []
        lines = [
            f"# Autopilot «{st.get('goal', '')}»",
            f"status={st.get('status')} step={st.get('index', 0)}/{len(steps)}",
        ]
        for i, s in enumerate(steps):
            mark = {"pending": " ", "in_progress": ">", "done": "x", "failed": "!", "blocked": "B", "skip": "-"}.get(
                s.get("status"), "?"
            )
            note = f" — {s['note']}" if s.get("note") else ""
            lines.append(f"  [{mark}] {i+1}. {s.get('content')}{note}")
        if st.get("reflections"):
            lines.append("## Recent reflections")
            for r in st["reflections"][-5:]:
                lines.append(f"- {r}")
        if st.get("summary"):
            lines.append(f"## Summary\n{st['summary']}")
        if st.get("artifacts"):
            lines.append("## Artifacts")
            for a in st["artifacts"]:
                lines.append(f"- {a}")
        return "\n".join(lines)

    def _auto_plan(self, goal: str) -> list[str]:
        g = goal.strip()
        # 启发式拆解（LLM 也可通过 steps 覆盖）
        steps = [
            f"澄清目标与验收标准：{g[:80]}",
            "收集必要信息（搜索/读文件/列目录）",
            "执行核心工作（编码/生成/操作）",
            "自检结果（跑命令/读产物/修问题）",
            "汇总交付物路径与简要说明",
        ]
        low = g.lower()
        if any(k in g for k in ("调研", "搜索", "最新", "对比", "趋势")) or "research" in low:
            steps = [
                "用 web_search 检索最新公开资料（≥3 条带来源）",
                "整理要点与对比维度",
                "对照 Takton 现有能力标出差距",
                "产出结构化结论（可生成报告/PPT）",
                "自检并交付文件路径",
            ]
        if any(k in g for k in ("PPT", "ppt", "幻灯", "演示")):
            steps = [
                "确定主题大纲（8–12 页）",
                "收集/整理每页要点",
                "调用 generate_ppt 生成文件",
                "校验 pptx 可打开",
                "回复完整路径",
            ]
        if any(k in g for k in ("PR", "pull request", "github", "提交")):
            steps = [
                "git status / diff 确认改动",
                "创建分支并提交",
                "gh 创建 PR",
                "查看 CI 状态",
                "回报 PR URL",
            ]
        return steps

    async def execute(self, **kwargs: Any) -> Any:
        action = str(kwargs.get("action") or "").lower()
        sid = _sid(kwargs)
        goal = (kwargs.get("goal") or "").strip()

        if action == "start" or action == "plan":
            if not goal and action == "start":
                return "[Error] goal required for start"
            st = self._state(sid)
            if action == "plan" and st and not goal:
                goal = st.get("goal") or ""
            if not goal:
                return "[Error] goal required"
            raw_steps = kwargs.get("steps")
            if isinstance(raw_steps, list) and raw_steps:
                contents = [str(x).strip() for x in raw_steps if str(x).strip()]
            else:
                contents = self._auto_plan(goal)
            steps = [
                {"id": f"s{i+1}", "content": c, "status": "pending", "note": ""}
                for i, c in enumerate(contents)
            ]
            if steps:
                steps[0]["status"] = "in_progress"
            st = {
                "id": uuid.uuid4().hex[:10],
                "goal": goal,
                "status": "active",
                "index": 0,
                "steps": steps,
                "reflections": [],
                "summary": "",
                "artifacts": [],
                "updated_at": time.time(),
            }
            _AUTOPILOTS[sid] = st
            # 同步 manage_goal 面板
            try:
                from backend.agent.goal_state import apply_manage_goal, save_goal_to_db

                apply_manage_goal(
                    sid,
                    action="create",
                    title=goal[:80],
                    description="autopilot",
                    todos=[
                        {"id": s["id"], "content": s["content"], "status": s["status"]}
                        for s in steps
                    ],
                )
                await save_goal_to_db(sid)
            except Exception as e:
                logger.debug("goal sync: %s", e)
            return self._fmt(st) + "\n\n→ 请执行 next 指示的步骤，完成后 reflect。"

        st = self._state(sid)
        if not st:
            return "[Error] no autopilot. Call action=start with goal=..."

        if action == "status":
            return self._fmt(st)

        if action == "next":
            steps = st["steps"]
            idx = st.get("index", 0)
            while idx < len(steps) and steps[idx]["status"] in ("done", "skip"):
                idx += 1
            st["index"] = idx
            if idx >= len(steps):
                return self._fmt(st) + "\n\n所有步骤已完成 → action=complete summary=..."
            steps[idx]["status"] = "in_progress"
            cur = steps[idx]
            return (
                f"CURRENT STEP [{idx+1}/{len(steps)}]: {cur['content']}\n"
                f"Do this now with tools. When finished: autopilot action=reflect "
                f"step_status=done|failed note=..."
            )

        if action == "reflect":
            steps = st["steps"]
            idx = st.get("index", 0)
            if idx >= len(steps):
                return self._fmt(st) + "\nNothing to reflect."
            status = str(kwargs.get("step_status") or "done").lower()
            note = str(kwargs.get("note") or "").strip()
            steps[idx]["status"] = status if status in ("done", "failed", "blocked", "skip") else "done"
            steps[idx]["note"] = note
            st["reflections"].append(f"step{idx+1} {status}: {note[:200]}")
            replan = kwargs.get("replan_steps")
            if status == "failed" and isinstance(replan, list) and replan:
                # 保留已完成，替换后续
                kept = steps[: idx + 1]
                new_rest = [
                    {"id": f"r{i+1}", "content": str(c).strip(), "status": "pending", "note": ""}
                    for i, c in enumerate(replan)
                    if str(c).strip()
                ]
                st["steps"] = kept + new_rest
                st["index"] = idx + 1
                if st["index"] < len(st["steps"]):
                    st["steps"][st["index"]]["status"] = "in_progress"
            elif status in ("done", "skip"):
                st["index"] = idx + 1
                if st["index"] < len(steps):
                    steps[st["index"]]["status"] = "in_progress"
            st["updated_at"] = time.time()
            try:
                from backend.agent.goal_state import apply_manage_goal, save_goal_to_db

                apply_manage_goal(
                    sid,
                    action="update_todo",
                    todo_id=steps[idx]["id"],
                    status="done" if status == "done" else status,
                    note=note,
                )
                await save_goal_to_db(sid)
            except Exception:
                pass
            return self._fmt(st) + "\n\n→ autopilot action=next"

        if action == "complete":
            st["status"] = "completed"
            st["summary"] = str(kwargs.get("summary") or "").strip()
            arts = kwargs.get("artifacts") or []
            if isinstance(arts, list):
                st["artifacts"] = [str(a) for a in arts]
            try:
                from backend.agent.goal_state import apply_manage_goal, save_goal_to_db

                apply_manage_goal(
                    sid,
                    action="complete",
                    completion_summary=st["summary"],
                )
                await save_goal_to_db(sid)
            except Exception:
                pass
            return self._fmt(st) + "\n\n✅ Autopilot complete. Present artifacts to user."

        if action == "cancel":
            st["status"] = "cancelled"
            return self._fmt(st)

        return f"[Error] unknown action={action}"


# ── Memory preferences ─────────────────────────────────────


class MemoryPrefTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(
            name="memory_pref",
            description=(
                "跨会话用户偏好记忆。action=get|set|delete|list。"
                "set 例：key=language value=zh；key=test_cmd value=pytest -q。"
                "新会话应 get 后遵守偏好。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["get", "set", "delete", "list"],
                        "default": "list",
                    },
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                },
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.SAFE,
        )

    async def execute(self, **kwargs: Any) -> Any:
        action = str(kwargs.get("action") or "list").lower()
        uid = _uid(kwargs)
        data = _load_prefs(uid)
        users = data.setdefault("users", {})
        prefs = users.setdefault(uid, {})

        if action == "list":
            if not prefs:
                return "No preferences stored."
            return json.dumps(prefs, ensure_ascii=False, indent=2)
        if action == "get":
            k = str(kwargs.get("key") or "").strip()
            if not k:
                return json.dumps(prefs, ensure_ascii=False, indent=2)
            return json.dumps({k: prefs.get(k)}, ensure_ascii=False)
        if action == "set":
            k = str(kwargs.get("key") or "").strip()
            v = kwargs.get("value")
            if not k:
                return "[Error] key required"
            prefs[k] = v
            users[uid] = prefs
            data["users"] = users
            _save_prefs(data)
            return f"OK set {k}={v!r}"
        if action == "delete":
            k = str(kwargs.get("key") or "").strip()
            if k in prefs:
                del prefs[k]
                _save_prefs(data)
                return f"OK deleted {k}"
            return f"key not found: {k}"
        return f"[Error] unknown action={action}"


# ── GitHub ─────────────────────────────────────────────────


class GithubTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(
            name="github",
            description=(
                "GitHub 协作（需本机 gh CLI 已登录）。"
                "action=status|pr_create|pr_list|ci|diff|branch。"
                "标准软件开发协作：分支/PR/CI。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["status", "diff", "branch", "pr_create", "pr_list", "ci"],
                        "default": "status",
                    },
                    "title": {"type": "string", "description": "PR 标题"},
                    "body": {"type": "string", "description": "PR 正文"},
                    "branch": {"type": "string", "description": "分支名"},
                    "base": {"type": "string", "description": "PR base 分支", "default": "main"},
                    "cwd": {"type": "string", "description": "仓库目录"},
                },
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.HIGH,
        )

    async def _run(self, args: list[str], cwd: str | None) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd or None,
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=120)
            text = (out or b"").decode("utf-8", errors="replace")
            e = (err or b"").decode("utf-8", errors="replace")
            if proc.returncode != 0:
                return f"[exit {proc.returncode}]\n{text}\n{e}".strip()
            return (text or e or "[ok]").strip()
        except FileNotFoundError:
            return (
                "[Error] gh/git not found. Install GitHub CLI: https://cli.github.com/ "
                "then `gh auth login`."
            )
        except Exception as e:
            return f"[Error] {e}"

    async def execute(self, **kwargs: Any) -> Any:
        action = str(kwargs.get("action") or "status").lower()
        cwd = (kwargs.get("cwd") or "").strip() or None
        if cwd and not os.path.isdir(cwd):
            return f"[Error] cwd not found: {cwd}"

        if action == "status":
            return await self._run(["git", "status", "-sb"], cwd)
        if action == "diff":
            return await self._run(["git", "diff", "--stat", "HEAD"], cwd)
        if action == "branch":
            name = (kwargs.get("branch") or "").strip()
            if not name:
                return await self._run(["git", "branch", "-vv"], cwd)
            r1 = await self._run(["git", "checkout", "-b", name], cwd)
            return r1
        if action == "pr_list":
            return await self._run(["gh", "pr", "list", "--limit", "10"], cwd)
        if action == "ci":
            return await self._run(["gh", "run", "list", "--limit", "5"], cwd)
        if action == "pr_create":
            title = (kwargs.get("title") or "").strip() or "Update"
            body = (kwargs.get("body") or "").strip() or "Automated PR from Takton"
            base = (kwargs.get("base") or "main").strip()
            return await self._run(
                [
                    "gh",
                    "pr",
                    "create",
                    "--title",
                    title,
                    "--body",
                    body,
                    "--base",
                    base,
                ],
                cwd,
            )
        return f"[Error] unknown action={action}"


# ── Desktop observe (vision loop) ──────────────────────────


class DesktopObserveTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(
            name="desktop_observe",
            description=(
                "桌面感知闭环：截屏并（可选）用 vision 描述界面，给出可点击区域建议。"
                "然后用 desktop_click 坐标点击。action=screenshot|describe|uia。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["screenshot", "describe", "uia"],
                        "default": "describe",
                    },
                    "question": {
                        "type": "string",
                        "description": "describe 时的问题",
                        "default": "List main UI elements and approximate positions.",
                    },
                },
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.MEDIUM,
        )

    async def execute(self, **kwargs: Any) -> Any:
        action = str(kwargs.get("action") or "describe").lower()
        if action == "uia":
            return await _uia_snapshot()

        # screenshot via desktop tool
        try:
            from backend.services.desktop.tools import DesktopScreenshotTool

            shot = DesktopScreenshotTool()
            raw = await shot.execute()
        except Exception as e:
            return f"[Error] screenshot failed: {e}"

        if action == "screenshot":
            # truncate huge base64 in return
            s = str(raw)
            if len(s) > 500:
                return s[:500] + f"\n...[screenshot ok, total_len={len(s)}]"
            return s

        # describe with vision if possible — save temp image if base64 present
        question = str(kwargs.get("question") or "Describe the screen and list clickable UI elements with rough x,y if possible.")
        try:
            import base64
            import re as _re
            import tempfile

            m = _re.search(r"base64,([A-Za-z0-9+/=\s]+)", s if (s := str(raw)) else "")
            if not m:
                # try JSON image field
                try:
                    data = json.loads(str(raw))
                    b64 = (data.get("data") or data).get("image") if isinstance(data, dict) else None
                except Exception:
                    b64 = None
            else:
                b64 = m.group(1).replace("\n", "")
            if not b64:
                return f"[screenshot taken but no image bytes for vision]\n{str(raw)[:300]}"
            img_bytes = base64.b64decode(b64)
            tmp = Path(tempfile.gettempdir()) / f"takton_obs_{uuid.uuid4().hex[:8]}.jpg"
            tmp.write_bytes(img_bytes)
            from backend.tools.builtins.agent_ops_tools import VisionAnalyzeTool

            vis = VisionAnalyzeTool()
            desc = await vis.execute(image_path=str(tmp), question=question)
            return f"[desktop_observe] saved={tmp}\n{desc}"
        except Exception as e:
            return f"[screenshot ok; vision failed: {e}]\n{str(raw)[:400]}"


async def _uia_snapshot() -> str:
    """Best-effort Windows UIA via pywinauto."""
    def _run() -> str:
        try:
            from pywinauto import Desktop

            desk = Desktop(backend="uia")
            wins = desk.windows()
            lines = [f"Windows (UIA) count={len(wins)}:"]
            for w in wins[:25]:
                try:
                    name = w.window_text() or ""
                    rect = w.rectangle()
                    lines.append(
                        f"- «{name[:60]}» rect=({rect.left},{rect.top})-({rect.right},{rect.bottom})"
                    )
                except Exception:
                    continue
            # foreground children
            try:
                fg = desk.window(active_only=True)
                lines.append(f"\nActive: {fg.window_text()[:80]}")
                for c in fg.descendants()[:40]:
                    try:
                        cn = c.friendly_class_name()
                        ct = (c.window_text() or "")[:40]
                        if not ct and cn in ("Pane", "Document"):
                            continue
                        r = c.rectangle()
                        lines.append(f"  · {cn}: «{ct}» @({r.left},{r.top})")
                    except Exception:
                        continue
            except Exception as e:
                lines.append(f"(active tree: {e})")
            return "\n".join(lines)
        except ImportError:
            return "[Error] pywinauto not installed"
        except Exception as e:
            return f"[Error] UIA: {e}"

    return await asyncio.to_thread(_run)


class UiaSnapshotTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(
            name="uia_snapshot",
            description=(
                "Windows UI Automation 快照：列出前台窗口与可见控件文本/大致坐标。"
                "优先于盲点坐标；配合 desktop_click 使用。"
            ),
            parameters={"type": "object", "properties": {}},
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.LOW,
        )

    async def execute(self, **kwargs: Any) -> Any:
        return await _uia_snapshot()


# ── Chart / mermaid render ─────────────────────────────────


class RenderChartTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(
            name="render_chart",
            description=(
                "将 mermaid 或简单 CSV 转为可查看产物。"
                "action=mermaid 保存 .mmd 并尝试 mmdc；action=table_md 输出 markdown 表。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["mermaid", "table_md"],
                        "default": "mermaid",
                    },
                    "content": {"type": "string", "description": "mermaid 源码或 CSV"},
                    "filename": {"type": "string", "description": "输出文件名"},
                },
                "required": ["content"],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.LOW,
        )

    async def execute(self, **kwargs: Any) -> Any:
        action = str(kwargs.get("action") or "mermaid").lower()
        content = str(kwargs.get("content") or "")
        if not content.strip():
            return "[Error] content required"
        out_dir = Path("workspace")
        out_dir.mkdir(parents=True, exist_ok=True)

        if action == "table_md":
            text_md = self._csv_or_md_to_table(content)
            if text_md.startswith("[Error]"):
                return text_md
            fname = kwargs.get("filename") or "table.md"
            if not str(fname).endswith(".md"):
                fname = str(fname) + ".md"
            fp = out_dir / fname
            fp.write_text(text_md, encoding="utf-8")
            # 回读校验中文未丢
            back = fp.read_text(encoding="utf-8")
            cjk_in = sum(1 for ch in content if "\u4e00" <= ch <= "\u9fff")
            cjk_out = sum(1 for ch in back if "\u4e00" <= ch <= "\u9fff")
            note = ""
            if cjk_in and cjk_out < cjk_in:
                note = f"\n[warn] CJK chars in={cjk_in} out={cjk_out}"
            return f"OK {fp.resolve()}\n\n{back}{note}"

        name = kwargs.get("filename") or f"diagram_{uuid.uuid4().hex[:6]}.mmd"
        if not str(name).endswith(".mmd"):
            name = str(name) + ".mmd"
        fp = out_dir / name
        fp.write_text(content, encoding="utf-8")
        png = fp.with_suffix(".png")
        try:
            proc = await asyncio.create_subprocess_exec(
                "mmdc", "-i", str(fp), "-o", str(png),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=30)
            if png.exists():
                return f"OK mermaid={fp.resolve()} png={png.resolve()}"
        except FileNotFoundError:
            pass
        except Exception:
            pass
        return (
            f"OK saved mermaid source: {fp.resolve()}\n"
            "⚠️ PNG 未生成：本机未安装 mermaid-cli（mmdc）。\n"
            "配置方法：\n"
            "  1) npm i -g @mermaid-js/mermaid-cli\n"
            "  2) 确认 `mmdc -V` 可用后重试 render_chart action=mermaid\n"
            "当前可把 .mmd 嵌入 Markdown/报告，或用支持 Mermaid 的编辑器预览。"
        )

    @staticmethod
    def _csv_or_md_to_table(content: str) -> str:
        """CSV/TSV/管道表 → GitHub Markdown 表。保留全部 Unicode（含中文）。"""
        raw_lines = [ln.rstrip("\r") for ln in content.splitlines()]
        lines = [ln.strip() for ln in raw_lines if ln.strip()]
        if not lines:
            return "[Error] empty"

        # 已是 markdown 表：规范化后原样保留单元格文本
        pipe_lines = [ln for ln in lines if ln.startswith("|") or ln.count("|") >= 2]
        if len(pipe_lines) >= 2 and len(pipe_lines) >= len(lines) - 1:
            rows: list[list[str]] = []
            for ln in lines:
                s = ln.strip()
                if set(s.replace("|", "").replace(":", "").replace("-", "").strip()) == set():
                    # separator row |---|---|
                    continue
                if s.startswith("|"):
                    s = s[1:]
                if s.endswith("|"):
                    s = s[:-1]
                cells = [c.strip() for c in s.split("|")]
                rows.append(cells)
            if not rows:
                return "[Error] empty table"
            ncols = max(len(r) for r in rows)
            def pad(r: list[str]) -> list[str]:
                return r + [""] * (ncols - len(r)) if len(r) < ncols else r[:ncols]
            rows = [pad(r) for r in rows]
            md = [
                "| " + " | ".join(rows[0]) + " |",
                "| " + " | ".join(["---"] * ncols) + " |",
            ]
            for r in rows[1:]:
                md.append("| " + " | ".join(r) + " |")
            return "\n".join(md)

        def split_row(ln: str) -> list[str]:
            # 全角逗号/分号 → 半角，便于分列（不删除中文）
            ln2 = ln.replace("\uff0c", ",").replace("\uff1b", ";")
            if "\t" in ln2 and ln2.count("\t") >= max(ln2.count(","), 1):
                parts = ln2.split("\t")
            elif "," in ln2:
                parts = ln2.split(",")
            elif ";" in ln2 and ln2.count(";") >= 1:
                parts = ln2.split(";")
            elif "|" in ln2:
                s = ln2.strip()
                if s.startswith("|"):
                    s = s[1:]
                if s.endswith("|"):
                    s = s[:-1]
                parts = s.split("|")
            else:
                parts = [ln2]
            return [c.strip() for c in parts]

        rows = [split_row(ln) for ln in lines]
        header = rows[0]
        ncols = len(header) if header else 0
        if ncols == 0:
            return "[Error] empty header"

        def pad(cells: list[str]) -> list[str]:
            if len(cells) < ncols:
                return cells + [""] * (ncols - len(cells))
            return cells[:ncols]

        header = pad(header)
        md = [
            "| " + " | ".join(header) + " |",
            "| " + " | ".join(["---"] * ncols) + " |",
        ]
        for r in rows[1:]:
            md.append("| " + " | ".join(pad(r)) + " |")
        return "\n".join(md)



CAPABILITY_TOOL_CLASSES = [
    AutopilotTool,
    MemoryPrefTool,
    GithubTool,
    DesktopObserveTool,
    UiaSnapshotTool,
    RenderChartTool,
]
