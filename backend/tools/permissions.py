"""
工具权限管理

统一处理所有工具（包括 skill/mcp/db）的权限校验：
- 路径白名单
- 危险操作确认
- 用户禁用/启用

工作区根解析（优先级从高到低）：
1. 构造参数 workspace_root
2. 本轮 run 上下文（session config: workspace_root / file_browser_root / cwd）
3. 环境变量 TEVARN_FILE_BROWSER_ROOT
4. settings.file_browser_root（相对路径相对「项目根」解析）
5. workspace 服务用户绑定 get_root("default")
6. 自动探测项目根（含 backend/ 的目录）或 cwd
"""
from __future__ import annotations

import logging
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator

from backend.core.config import get_tevarn_home
from backend.tools.base import BaseTool, ToolRiskLevel

logger = logging.getLogger(__name__)

# User paste "E:\项目\guardian" / "D:/work/foo" — auto-allow as extra root for this run.
_WIN_ABS_PATH_RE = re.compile(
    r"(?P<p>[A-Za-z]:[\\/](?:[^\s\"'<>|*?\r\n\\/]+[\\/])*[^\s\"'<>|*?\r\n\\/]*)"
)
_UNC_ABS_PATH_RE = re.compile(
    r"(?P<p>\\\\[^\s\"'<>|*?\r\n]+(?:\\[^\s\"'<>|*?\r\n]+)*)"
)


def extract_absolute_paths_from_user_text(text: str) -> list[str]:
    """Pull absolute filesystem paths the user mentioned (Windows-focused)."""
    t = text or ""
    if not t.strip():
        return []
    found: list[str] = []
    for rx in (_WIN_ABS_PATH_RE, _UNC_ABS_PATH_RE):
        for m in rx.finditer(t):
            raw = (m.group("p") or "").strip().rstrip(".,;:，。；：)）]")
            if not raw or len(raw) < 3:
                continue
            # drive root alone (E:\) is too broad — require at least one segment
            norm = raw.replace("/", "\\")
            parts = [x for x in norm.split("\\") if x and not x.endswith(":")]
            if len(parts) < 1 and not norm.startswith("\\\\"):
                continue
            if re.fullmatch(r"[A-Za-z]:\\?", norm):
                continue
            found.append(raw)
    # de-dupe
    out: list[str] = []
    seen: set[str] = set()
    for p in found:
        k = p.replace("/", "\\").lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(p)
    return out


def normalize_extra_root(path: str) -> str | None:
    """Resolve user-mentioned path to a directory root for allowlist."""
    if not path:
        return None
    try:
        p = Path(str(path).strip()).expanduser()
        if not p.is_absolute():
            return None
        # Prefer existing path; if file, allow parent dir
        try:
            if p.exists():
                p = p.resolve()
                if p.is_file():
                    p = p.parent
                return str(p)
        except OSError:
            pass
        # Not existing yet — still allow the path prefix if parent exists
        try:
            parent = p.parent
            if parent.exists():
                return str(p.resolve() if p.exists() else p)
        except OSError:
            pass
        return str(p)
    except Exception:
        return None

# 本轮 Agent run 覆盖（session 可配 workspace_root / cwd）
_run_workspace_root: ContextVar[str | None] = ContextVar("tevarn_run_workspace_root", default=None)
_run_extra_roots: ContextVar[tuple[str, ...] | None] = ContextVar(
    "tevarn_run_extra_roots", default=None
)


def get_run_workspace_root() -> str | None:
    return _run_workspace_root.get()


def get_run_extra_roots() -> list[str]:
    extra = _run_extra_roots.get()
    return list(extra) if extra else []


def host_data_roots() -> list[str]:
    """始终允许的宿主数据根（不依赖 session extra_roots）。

    Agent 沙箱 HOME 在 workspace/.computers/<agent>/home，但宿主
    ``~/.tevarn`` 与 ``%APPDATA%/tevarn`` 是真实记忆/技能/日志落点。
    不放行则 file_read 会 path:workspace 拒掉，自检永远失败。
    """
    roots: list[str] = []
    try:
        from backend.agent._tevarn_paths import home_dir, host_home

        roots.append(str(home_dir().resolve()))
        roots.append(str((host_home() / ".tevarn").resolve()))
        # 不要放行整个用户主目录——只放行 Tevarn 数据树
    except Exception:
        try:
            p = get_tevarn_home()
            roots.append(str(p.resolve()))
        except OSError:
            pass
    # 桌面端数据树：%APPDATA%/tevarn（含 data/workspace）
    for env_key in ("APPDATA", "LOCALAPPDATA"):
        base = (os.environ.get(env_key) or "").strip()
        if not base:
            continue
        try:
            roots.append(str((Path(base) / "tevarn").resolve()))
        except OSError:
            continue
    # 开发机常见：显式开发仓路径
    for env_key in ("TEVARN_DEV_ROOT", "TEVARN_REPO_ROOT"):
        raw = (os.environ.get(env_key) or "").strip()
        if raw:
            try:
                roots.append(str(Path(raw).expanduser().resolve()))
            except OSError:
                pass
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for r in roots:
        k = r.replace("/", "\\").lower() if os.name == "nt" else r
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def rewrite_host_path_into_workspace(path: str) -> str:
    """Normalize tool paths so Rust path:workspace and Python gates agree.

    Essential bug (prod logs): model writes
      C:\\Users\\…\\AppData\\Roaming\\tevarn\\data\\workspace\\foo.md
    Rust court treats absolute paths with starts_with(workspace) and often
    **denies** (Win canonicalize / root mismatch). Relative ``foo.md`` works.

    Strategy:
    1. Relative paths: leave as-is.
    2. Absolute path **inside** agent workspace → **relative** path (preferred).
    3. Absolute under ~/.tevarn / host data → rewrite via junction, then (2).
    """
    raw = (path or "").strip()
    if not raw:
        return path
    try:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            return path
        try:
            p = p.resolve()
        except OSError:
            p = Path(os.path.abspath(str(p)))
    except Exception:
        return path

    # --- Prefer: strip workspace root to relative (fixes absolute-in-workspace deny) ---
    try:
        ws = Path(resolve_agent_workspace_root())
        try:
            ws_r = ws.resolve()
        except OSError:
            ws_r = Path(os.path.abspath(str(ws)))
        try:
            rel_ws = p.relative_to(ws_r)
            rel_s = str(rel_ws).replace("\\", "/")
            if rel_s in (".", ""):
                return "."
            # If "workspace" is wrongly home (C:\Users\x), do not return
            # AppData/Roaming/tevarn/data/workspace/... as relative — strip known suffix.
            marker = "appdata/roaming/tevarn/data/workspace/"
            low = rel_s.lower().replace("\\", "/")
            if marker in low:
                return rel_s[low.index(marker) + len(marker) :] or "."
            marker2 = "tevarn/data/workspace/"
            if marker2 in low:
                return rel_s[low.index(marker2) + len(marker2) :] or "."
            return rel_s
        except ValueError:
            pass
        # Also match un-resolved forms (different drive letter case, etc.)
        try:
            p_abs = os.path.normcase(os.path.abspath(str(p)))
            w_abs = os.path.normcase(os.path.abspath(str(ws)))
            if p_abs == w_abs:
                return "."
            prefix = w_abs.rstrip("\\/") + os.sep
            if p_abs.startswith(prefix):
                rel_s = p_abs[len(prefix) :].replace("\\", "/")
                low = rel_s.lower()
                for marker in (
                    "appdata/roaming/tevarn/data/workspace/",
                    "tevarn/data/workspace/",
                ):
                    if marker in low:
                        return rel_s[low.index(marker) + len(marker) :] or "."
                return rel_s
        except Exception:
            pass
    except Exception:
        pass

    # Desktop default workspace absolute (even if resolve_agent_workspace_root differs)
    try:
        appdata = os.environ.get("APPDATA") or ""
        if appdata:
            desk_ws = Path(appdata) / "tevarn" / "data" / "workspace"
            try:
                desk_r = desk_ws.resolve()
            except OSError:
                desk_r = Path(os.path.abspath(str(desk_ws)))
            try:
                rel_d = str(p.relative_to(desk_r)).replace("\\", "/")
                return "." if rel_d in (".", "") else rel_d
            except ValueError:
                pass
    except Exception:
        pass

    # --- Host ~/.tevarn → workspace junction (existing) ---
    try:
        from backend.agent._tevarn_paths import home_dir, host_home

        host_tevarn = home_dir().resolve()
        candidates = [host_tevarn, (host_home() / ".tevarn").resolve()]
    except Exception:
        candidates = []
        try:
            candidates.append((get_tevarn_home()).resolve())
        except OSError:
            return path

    # Roaming app data tree (desktop install): %APPDATA%/tevarn/...
    try:
        appdata = os.environ.get("APPDATA") or ""
        if appdata:
            candidates.append((Path(appdata) / "tevarn").resolve())
    except Exception:
        pass

    rel: str | None = None
    for ht in candidates:
        try:
            rel = str(p.relative_to(ht)).replace("\\", "/")
            break
        except ValueError:
            continue
    if rel is None:
        return path

    # data/workspace/... under APPDATA/tevarn → relative under workspace
    if rel.startswith("data/workspace/") or rel == "data/workspace":
        sub = rel[len("data/workspace/") :] if rel.startswith("data/workspace/") else ""
        return sub if sub else "."

    ws = resolve_agent_workspace_root()
    # Prefer main computer junction; fall back to any existing .computers/*/home/.tevarn
    candidates_j: list[Path] = [
        Path(ws) / ".computers" / "main" / "home" / ".tevarn",
    ]
    try:
        computers = Path(ws) / ".computers"
        if computers.is_dir():
            for child in computers.iterdir():
                j = child / "home" / ".tevarn"
                if j.exists():
                    candidates_j.append(j)
    except OSError:
        pass

    for j in candidates_j:
        try:
            if j.exists() or j.is_symlink() or j.is_dir():
                target = (j / rel).resolve() if rel not in (".", "") else j.resolve()
                # Prefer relative if still under workspace
                try:
                    return str(target.relative_to(Path(ws).resolve())).replace("\\", "/")
                except Exception:
                    return str(target)
        except OSError:
            continue
    # Ensure junction then retry
    try:
        from backend.agent._tevarn_paths import ensure_sandbox_tevarn_link, home_dir

        main_home = Path(ws) / ".computers" / "main" / "home"
        ensure_sandbox_tevarn_link(main_home, home_dir())
        j = main_home / ".tevarn"
        if j.exists():
            target = (j / rel).resolve() if rel not in (".", "") else j.resolve()
            try:
                return str(target.relative_to(Path(ws).resolve())).replace("\\", "/")
            except Exception:
                return str(target)
    except Exception:
        pass
    return path


def normalize_tool_path_args(arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Rewrite filepath/path/… in-place for tool calls (court + executor)."""
    args = dict(arguments or {})
    for k in (
        "filepath",
        "path",
        "file",
        "file_path",
        "directory",
        "dir",
        "base_path",
        "database",
    ):
        v = args.get(k)
        if isinstance(v, str) and v.strip():
            args[k] = rewrite_host_path_into_workspace(v.strip())
    return args


@contextmanager
def run_workspace_context(
    root: str | None = None,
    extra_roots: list[str] | None = None,
) -> Iterator[None]:
    """在 Agent.run 期间覆盖默认 workspace 与额外允许根。"""
    tokens: list = []
    if root:
        r = Path(root).expanduser()
        if not r.is_absolute():
            r = Path(detect_project_root()) / r
        r = r.resolve()
        r.mkdir(parents=True, exist_ok=True)
        tokens.append((_run_workspace_root, _run_workspace_root.set(str(r))))
        logger.info("run_workspace_context root=%s", r)
    if extra_roots is not None:
        cleaned: list[str] = []
        for e in extra_roots:
            if not e:
                continue
            ep = Path(str(e)).expanduser()
            if not ep.is_absolute():
                ep = Path(detect_project_root()) / ep
            try:
                ep = ep.resolve()
            except OSError:
                continue
            cleaned.append(str(ep))
        tokens.append((_run_extra_roots, _run_extra_roots.set(tuple(cleaned))))
        logger.info("run_workspace_context extra_roots=%s", cleaned)
    try:
        yield
    finally:
        for var, tok in reversed(tokens):
            var.reset(tok)


def detect_project_root(start: str | None = None) -> str:
    """向上查找含 backend/ 或 pyproject.toml 的项目根；找不到则 cwd。"""
    cur = Path(start or os.getcwd()).expanduser().resolve()
    if cur.is_file():
        cur = cur.parent
    for p in [cur, *cur.parents]:
        if (p / "backend").is_dir() and (
            (p / "pyproject.toml").is_file()
            or (p / "backend" / "main.py").is_file()
            or (p / "package.json").is_file()
        ):
            return str(p)
        # 单 backend 仓
        if (p / "main.py").is_file() and p.name == "backend":
            return str(p.parent)
    return str(cur)


def resolve_agent_workspace_root(explicit: str | None = None) -> str:
    """解析 Agent 文件/命令工具使用的工作区根（绝对路径）。"""
    if explicit:
        root = Path(explicit).expanduser()
        if not root.is_absolute():
            root = Path(detect_project_root()) / root
        root = root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        return str(root)

    run_root = _run_workspace_root.get()
    if run_root:
        return str(Path(run_root).resolve())

    env = (os.environ.get("TEVARN_FILE_BROWSER_ROOT") or "").strip()
    if env:
        root = Path(env).expanduser()
        if not root.is_absolute():
            root = Path(detect_project_root()) / root
        root = root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        return str(root)

    # settings.file_browser_root
    try:
        from backend.core.config import settings

        fb = (getattr(settings, "file_browser_root", None) or "").strip()
    except Exception:
        fb = ""

    if fb:
        root = Path(fb).expanduser()
        if not root.is_absolute():
            # 相对路径：相对项目根，而不是含糊 cwd
            root = Path(detect_project_root()) / root
        root = root.resolve()
        # "." → 项目根；"workspace" → 项目下 sandbox（自动创建）
        root.mkdir(parents=True, exist_ok=True)
        return str(root)

    # 用户绑定的专业模式根目录（bind 使用真实 user_id，不可写死 default）
    try:
        from backend.workspace.service import get_any_root, get_root

        bound = get_root("default")
        if bound is None:
            bound = get_any_root()
        if bound is not None:
            return str(bound)
    except Exception:
        pass

    root = Path(detect_project_root())
    root.mkdir(parents=True, exist_ok=True)
    return str(root)



def bind_run_workspace_from_config(
    config: dict[str, Any] | None,
    user_text: str | None = None,
) -> Any:
    """从 session config + 用户消息中的绝对路径绑定本轮 workspace；返回 reset 回调。"""
    cfg = config if isinstance(config, dict) else {}
    root = (
        cfg.get("workspace_root")
        or cfg.get("file_browser_root")
        or cfg.get("cwd")
        or cfg.get("work_dir")
        or (os.environ.get("TEVARN_TASK_ROOT") or "").strip()
        or None
    )
    extra = cfg.get("allowed_roots") or cfg.get("extra_workspace_roots") or []
    if isinstance(extra, str):
        extra = [extra]
    # 若只设了 TASK_ROOT 且 env 已有 FILE_BROWSER_ROOT 不同，把 task 作为 root
    tokens: list = []
    if root:
        r = Path(str(root)).expanduser()
        if not r.is_absolute():
            r = Path(detect_project_root()) / r
        r = r.resolve()
        r.mkdir(parents=True, exist_ok=True)
        tokens.append((_run_workspace_root, _run_workspace_root.set(str(r))))
        logger.info("session workspace_root override=%s", r)
    # 始终并入宿主数据根 + session extra + 用户消息里点名的绝对路径
    cleaned: list[str] = list(host_data_roots())
    if extra:
        for e in extra:
            if not e:
                continue
            ep = Path(str(e)).expanduser()
            if not ep.is_absolute():
                ep = Path(detect_project_root()) / ep
            try:
                cleaned.append(str(ep.resolve()))
            except OSError:
                continue
    mentioned = extract_absolute_paths_from_user_text(user_text or "")
    for raw in mentioned:
        nr = normalize_extra_root(raw)
        if nr:
            cleaned.append(nr)
    if cleaned:
        # de-dupe
        seen: set[str] = set()
        uniq: list[str] = []
        for c in cleaned:
            k = c.replace("/", "\\").lower() if os.name == "nt" else c
            if k in seen:
                continue
            seen.add(k)
            uniq.append(c)
        tokens.append((_run_extra_roots, _run_extra_roots.set(tuple(uniq))))
        logger.info(
            "session extra_roots=%s user_mentioned=%s",
            uniq,
            mentioned or [],
        )
    # 确保沙箱 junction 存在，便于 path rewrite
    try:
        from backend.agent._tevarn_paths import ensure_sandbox_tevarn_link, home_dir

        ws = root or resolve_agent_workspace_root()
        ensure_sandbox_tevarn_link(Path(str(ws)) / ".computers" / "main" / "home", home_dir())
    except Exception:
        pass

    def _reset() -> None:
        for var, tok in reversed(tokens):
            try:
                var.reset(tok)
            except Exception:
                pass

    return _reset



class ToolPermissionManager:
    """工具权限管理器"""

    def __init__(self, workspace_root: str | None = None):
        self.workspace_root = resolve_agent_workspace_root(workspace_root)
        logger.debug("ToolPermissionManager workspace_root=%s", self.workspace_root)

    def _resolve_path(self, path: str) -> str:
        """解析路径为绝对路径（相对则拼 workspace_root）。"""
        if os.path.isabs(path):
            return os.path.abspath(path)
        return os.path.abspath(os.path.join(self.workspace_root, path))

    def is_path_allowed(self, path: str, allowed_paths: list[str] | None = None) -> bool:
        """
        检查路径是否允许访问。
        如果 allowed_paths 为 None，默认 workspace_root + 本轮 extra + 宿主数据根。
        """
        # 宿主 ~/.tevarn 等 → 优先改写到 workspace junction 再判定
        path = rewrite_host_path_into_workspace(path)
        if allowed_paths is not None:
            paths = list(allowed_paths)
        else:
            paths = [self.workspace_root, *get_run_extra_roots(), *host_data_roots()]
        # de-dupe
        seen: set[str] = set()
        uniq: list[str] = []
        for x in paths:
            k = str(x)
            if k not in seen:
                seen.add(k)
                uniq.append(k)
        paths = uniq
        target = self._resolve_path(path)
        try:
            target_p = Path(target).resolve()
        except OSError:
            target_p = Path(target)
        for allowed in paths:
            try:
                allowed_abs = Path(os.path.abspath(allowed)).resolve()
            except OSError:
                allowed_abs = Path(os.path.abspath(allowed))
            try:
                target_p.relative_to(allowed_abs)
                return True
            except ValueError:
                continue
        return False

    def check_tool_permission(
        self,
        tool: BaseTool,
        arguments: dict[str, Any],
    ) -> tuple[bool, str]:
        """
        统一权限检查入口。

        返回：(is_allowed, reason)
        """
        if not tool.enabled:
            return False, f"Tool '{tool.name}' is disabled"

        # 路径类权限检查
        if tool.allowed_paths is not None:
            path_keys = ["filepath", "path", "file", "directory", "dir", "base_path", "database"]
            for key in path_keys:
                if key in arguments and isinstance(arguments[key], str):
                    if not self.is_path_allowed(arguments[key], tool.allowed_paths):
                        return (
                            False,
                            f"Path '{arguments[key]}' is outside allowed directories: "
                            f"{tool.allowed_paths}",
                        )
        else:
            # 无显式 allowed_paths 时，对文件操作类工具做默认 workspace 边界检查
            file_tools = {
                "file_read",
                "file_write",
                "edit",
                "glob",
                "grep",
                "sqlite_query",
                "apply_patch",
            }
            if tool.name in file_tools:
                path_keys = ["filepath", "path", "file", "directory", "dir", "base_path", "database"]
                for key in path_keys:
                    if key in arguments and isinstance(arguments[key], str):
                        if not self.is_path_allowed(arguments[key]):
                            return (
                                False,
                                f"Path '{arguments[key]}' is outside workspace root: "
                                f"{self.workspace_root}",
                            )

        return True, ""

    def needs_confirmation(self, tool: BaseTool, arguments: dict[str, Any]) -> bool:
        """判断是否需要用户确认。S7: 只读默认不确认。"""
        name = str(getattr(tool, "name", "") or "")
        try:
            from backend.agent.progress_guard import READ_ONLY_TOOLS
            if name in READ_ONLY_TOOLS:
                return False
        except Exception:
            if name in {"file_read", "grep", "glob", "web_search", "search", "current_time", "doc_read"}:
                return False
        if tool.requires_confirmation:
            if name in {"file_read", "web_search", "grep", "glob"}:
                return False
            return True
        if tool.risk_level == ToolRiskLevel.DANGEROUS:
            dangerous_params = ["content", "new_text", "body", "code", "command"]
            if any(p in arguments for p in dangerous_params):
                return True
        return False


def get_default_allowed_paths() -> list[str]:
    """获取默认允许路径"""
    workspace = resolve_agent_workspace_root()
    uploads = os.environ.get("TEVARN_UPLOADS_DIR") or os.path.join(
        detect_project_root(), "uploads"
    )
    uploads = os.path.abspath(uploads)
    roots = [workspace, uploads, *host_data_roots()]
    seen: set[str] = set()
    out: list[str] = []
    for r in roots:
        k = str(r).replace("/", "\\").lower() if os.name == "nt" else str(r)
        if k in seen:
            continue
        seen.add(k)
        out.append(str(r))
    return out
