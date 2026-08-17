"""运行时授权记忆：危险确认的「本会话 / 本员工」放行。

PermissionGate 每次 check 都是新实例，session_allows 不能跨调用。
本模块用进程内字典保存授权，供 tool_hooks 在弹窗前短路。

scope:
  - once    —— 仅当前这次（不写入本 store）
  - session —— 本会话内该工具整类放行（whole_tool，含 command 任意首词）
  - agent   —— 写入员工 Identity.capabilities（持久）+ 会话整工具缓存
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# session_id -> set of allow signatures (e.g. "command:rm", "file_write")
_session_grants: dict[str, set[str]] = {}
# session_id -> last grant activity (unix ts); used for TTL prune
_session_grant_ts: dict[str, float] = {}
_grants_lock = threading.RLock()
_persist_loaded = False

# Default 7d; override with TEVARN_SESSION_GRANT_TTL_SECONDS (0 = no TTL)
def _grant_ttl_seconds() -> float:
    raw = (os.environ.get("TEVARN_SESSION_GRANT_TTL_SECONDS") or "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return 7.0 * 24 * 3600


def _grants_path() -> Path:
    """会话授权落盘路径（热重载 / 单机重启可恢复；非跨机权威）。"""
    base = (
        os.environ.get("TEVARN_DATA_DIR")
        or os.environ.get("TEVARN_HOME")
        or ""
    ).strip()
    if not base:
        # 与其它本机状态对齐：~/.tevarn
        home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or "."
        base = str(Path(home) / ".tevarn")
    p = Path(base) / "session_grants.json"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return p


def _ensure_loaded() -> None:
    global _persist_loaded
    if _persist_loaded:
        return
    with _grants_lock:
        if _persist_loaded:
            return
        path = _grants_path()
        try:
            if path.is_file():
                raw = json.loads(path.read_text(encoding="utf-8"))
                data = raw.get("grants") if isinstance(raw, dict) else raw
                if isinstance(data, dict):
                    now = time.time()
                    for sid, entry in data.items():
                        key = str(sid)
                        # New format: {sigs: [...], updated_at: ts}
                        if isinstance(entry, dict):
                            sigs = entry.get("sigs") or entry.get("signatures") or []
                            ts = entry.get("updated_at") or entry.get("ts") or now
                            if isinstance(sigs, list):
                                _session_grants[key] = {str(s) for s in sigs}
                                try:
                                    _session_grant_ts[key] = float(ts)
                                except (TypeError, ValueError):
                                    _session_grant_ts[key] = now
                        elif isinstance(entry, list):
                            # Legacy: sid -> [sigs]
                            _session_grants[key] = {str(s) for s in entry}
                            _session_grant_ts[key] = now
                    logger.info(
                        "session grants loaded from disk sessions=%s path=%s",
                        len(_session_grants),
                        path,
                    )
        except Exception as e:
            logger.warning("session grants load failed: %s", e)
        _persist_loaded = True
        # Drop expired entries on load (best-effort, no DB yet)
        _prune_expired_locked()


def _persist() -> None:
    """best-effort 写盘（单机桌面热重载不丢「本会话允许」）。"""
    path = _grants_path()
    try:
        grants_out: dict[str, Any] = {}
        for sid, sigs in _session_grants.items():
            grants_out[sid] = {
                "sigs": sorted(sigs),
                "updated_at": _session_grant_ts.get(sid) or time.time(),
            }
        payload = {
            "updated_at": time.time(),
            "ttl_seconds": _grant_ttl_seconds(),
            "grants": grants_out,
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=0), encoding="utf-8")
        tmp.replace(path)
    except Exception as e:
        logger.warning("session grants persist failed: %s", e)


def _prune_expired_locked() -> int:
    """Remove TTL-expired grants. Caller must hold _grants_lock."""
    ttl = _grant_ttl_seconds()
    if ttl <= 0:
        return 0
    now = time.time()
    dead = [
        sid
        for sid, ts in list(_session_grant_ts.items())
        if (now - float(ts or 0)) > ttl
    ]
    # Also drop grants with no timestamp if very large file? keep them, touch on use.
    for sid in dead:
        _session_grants.pop(sid, None)
        _session_grant_ts.pop(sid, None)
    return len(dead)

# tool name / permission key -> identity capability id (CAP_POOL)
# P0-B：权威副本在 Rust tool_catalog；此处为 fallback / 无 host 时使用。
# 启动后可通过 sync_catalog_from_kernel() 从 host 刷新。
TOOL_TO_CREW_CAP: dict[str, str] = {
    "command": "command",
    "bash": "command",
    "shell": "command",
    "python": "command",
    "process": "command",
    "remote_exec": "command",
    "terminal": "command",
    "file_read": "file_rw",
    "file_write": "file_rw",
    "file_edit": "file_rw",
    "edit": "file_rw",
    "apply_patch": "file_rw",
    "read": "file_rw",
    "write": "file_rw",
    "glob": "file_rw",
    "grep": "file_rw",
    "doc_read": "file_rw",
    "web_search": "web_search",
    "search": "web_search",
    "web_fetch": "web_search",
    "web_extract": "web_search",
    "fetch_webpage": "web_search",
    "http": "web_search",
    "http_get": "web_search",
    "browser": "browser",
    "git": "git",
    "manage_git": "git",
    "calendar": "calendar",
    "calendar_read": "calendar",
    "notify": "notify",
    "send_email": "notify",
    "send_message": "notify",
    "session_search": "memory",
    "memory": "memory",
    "knowledge_search": "memory",
    "wiki_search": "memory",
    "delegate_task": "delegate_task",
    "cronjob": "cronjob",
    "computer": "computer",
    # Main-chat orchestration (parity with Rust tool_catalog + coding profile)
    "crew_steward": "crew_steward",
    "clarify": "crew_steward",
    "use_tool_pack": "use_tool_pack",
    # 经营目标 / 会话 Todo（CEO 改目标、会话规划）
    "okr_goal": "okr_goal",
    "manage_goal": "manage_goal",
    "autopilot": "manage_goal",
    # 唯一映射：current_time 为独立 cap（勿重复键 F601）
    "current_time": "current_time",
    "result_load": "file_read",  # 外置结果回读，与读权限同级
    # MCP 管理与运行时（动态 mcp_* 走 tool_matches 前缀规则）
    "manage_mcp": "manage_mcp",
    "manage_skill": "manage_skill",
    "generate_ppt": "file_rw",
}


def sync_catalog_from_kernel(kernel: Any | None = None) -> bool:
    """Refresh TOOL_TO_CREW_CAP from Rust host tool_catalog RPC.

    可传入已构造的 kernel，避免 get_rust_kernel → sync → get_kernel 重入
    （init 期间 _kernel_singleton 尚未赋值会二次初始化）。
    """
    try:
        k = kernel
        if k is None:
            from backend.kernel import get_kernel

            k = get_kernel()
        if not hasattr(k, "tool_catalog"):
            return False
        cat = k.tool_catalog() or {}
        pairs = cat.get("tool_to_crew_cap") or []
        if not pairs:
            return False
        TOOL_TO_CREW_CAP.clear()
        for p in pairs:
            if isinstance(p, dict) and p.get("tool") and p.get("cap"):
                TOOL_TO_CREW_CAP[str(p["tool"])] = str(p["cap"])
        # 旧 host catalog 可能缺 goal 工具映射；补齐避免 CEO 令牌拦截
        TOOL_TO_CREW_CAP.setdefault("okr_goal", "okr_goal")
        TOOL_TO_CREW_CAP.setdefault("manage_goal", "manage_goal")
        TOOL_TO_CREW_CAP.setdefault("autopilot", "manage_goal")
        TOOL_TO_CREW_CAP.setdefault("crew_steward", "crew_steward")
        TOOL_TO_CREW_CAP.setdefault("clarify", "crew_steward")
        TOOL_TO_CREW_CAP.setdefault("manage_mcp", "manage_mcp")
        TOOL_TO_CREW_CAP.setdefault("manage_skill", "manage_skill")
        TOOL_TO_CREW_CAP.setdefault("generate_ppt", "file_rw")
        TOOL_TO_CREW_CAP.setdefault("current_time", "current_time")
        TOOL_TO_CREW_CAP.setdefault("result_load", "file_read")
        logger.info("grant_store catalog synced from rust (%s entries)", len(TOOL_TO_CREW_CAP))
        return True
    except Exception as e:
        logger.debug("sync_catalog_from_kernel: %s", e)
        return False


def tool_matches_crew_caps(tool: str, capabilities: list[str] | set[str] | frozenset[str] | None) -> bool:
    """工具名是否被编制能力集覆盖（抽象 cap 如 file_rw 可覆盖 file_read/glob/grep）。

    动态 MCP 工具（mcp_*）在无 Rust 重建时：持有 manage_mcp / mcp / integrations 即可。
    """
    if capabilities is None:
        return True
    caps = set(capabilities)
    if "*" in caps or tool in caps:
        return True
    abstract = TOOL_TO_CREW_CAP.get(tool)
    if abstract and abstract in caps:
        return True
    # 运行时 MCP 工具：prefix 规则（与 engineering.manage_mcp 对齐）
    t = str(tool or "")
    if t.startswith("mcp_") or t in {"mcp_call", "mcp"}:
        if caps & {"manage_mcp", "mcp", "integrations", "mcp_call"}:
            return True
    return False



def allow_signature(tool: str, arguments: dict[str, Any] | None = None) -> str:
    """与 PermissionGate._allow_signature 对齐：shell 按命令首词细分。"""
    args = arguments or {}
    raw = str(args.get("command") or args.get("cmd") or "").strip()
    if tool in ("command", "bash", "shell", "python", "process") and raw:
        head = raw.split()[0]
        head = head.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if head:
            return f"{tool}:{head}"
    return tool


def _rust_grant_rpc(method: str, params: dict[str, Any], kernel: Any | None = None) -> dict[str, Any] | None:
    """Best-effort live court map. Old hosts without these RPCs are ignored."""
    try:
        k = kernel
        if k is None:
            from backend.kernel_rust.client import is_rust_host_available

            if not is_rust_host_available():
                return None
            from backend.kernel import get_kernel

            k = get_kernel()
        if not hasattr(k, "_call"):
            return None
        r = k._call(method, params)
        return r if isinstance(r, dict) else None
    except Exception as e:
        logger.debug("rust grant rpc %s skip: %s", method, e)
        return None


def rehydrate_session_grants_to_kernel(kernel: Any | None = None) -> int:
    """Push persisted grants into the live Rust SessionGrantStore."""
    _ensure_loaded()
    n = 0
    with _grants_lock:
        items = [(sid, sorted(sigs)) for sid, sigs in _session_grants.items() if sigs]
    for sid, sigs in items:
        r = _rust_grant_rpc(
            "session_grant_add",
            {"session_id": sid, "sigs": sigs},
            kernel=kernel,
        )
        if r is not None:
            n += 1
    if n:
        logger.info("session grants rehydrated to rust sessions=%s", n)
    return n


def has_session_grant(session_id: str | None, tool: str, arguments: dict[str, Any] | None = None) -> bool:
    if not session_id:
        return False
    _ensure_loaded()
    sid = str(session_id)
    sig = allow_signature(tool, arguments)
    rust = _rust_grant_rpc(
        "session_grant_has",
        {"session_id": sid, "sig": sig},
    )
    if isinstance(rust, dict) and rust.get("has") is True:
        return True
    if rust is not None and tool != sig:
        whole = _rust_grant_rpc(
            "session_grant_has",
            {"session_id": sid, "sig": tool},
        )
        if isinstance(whole, dict) and whole.get("has") is True:
            return True
    with _grants_lock:
        # Lazy TTL check
        ttl = _grant_ttl_seconds()
        if ttl > 0:
            ts = _session_grant_ts.get(sid)
            if ts is not None and (time.time() - float(ts)) > ttl:
                _session_grants.pop(sid, None)
                _session_grant_ts.pop(sid, None)
                _persist()
                return False
        grants = _session_grants.get(sid)
        if not grants:
            return False
        # 细粒度签名（command:rm）或整工具名（本员工/本会话放行 command）
        if sig in grants:
            return True
        if tool in grants:
            return True
        return False


def add_session_grant(
    session_id: str | None,
    tool: str,
    arguments: dict[str, Any] | None = None,
    *,
    whole_tool: bool = False,
) -> None:
    """记录本会话放行。

    whole_tool=True：放行该工具全部调用（「本会话允许」/「本员工允许」后的会话缓存）。
    默认 False：shell 仍按命令首词细分（仅内部/兼容路径）。
    落盘：单机热重载 / 进程重启后仍可短路确认（非多机权威）。
    """
    if not session_id:
        return
    _ensure_loaded()
    sid = str(session_id)
    with _grants_lock:
        bucket = _session_grants.setdefault(sid, set())
        sig = allow_signature(tool, arguments)
        bucket.add(sig)
        if whole_tool:
            bucket.add(tool)
        _session_grant_ts[sid] = time.time()
        _persist()
    sigs = [sig]
    if whole_tool and tool != sig:
        sigs.append(tool)
    _rust_grant_rpc("session_grant_add", {"session_id": sid, "sigs": sigs})
    logger.info(
        "grant session allow session=%s sig=%s whole=%s",
        sid[:8],
        sig,
        whole_tool,
    )


async def resolve_identity_id(
    arguments: dict[str, Any] | None = None,
    *,
    identity_id: str | None = None,
    contact_name: str | None = None,
) -> str | None:
    """从工具参数 / 联系人名字解析员工 Identity id。"""
    args = arguments or {}
    iid = (identity_id or str(args.get("_identity_id") or args.get("identity_id") or "")).strip()
    if iid:
        return iid
    name = (contact_name or str(args.get("_contact_agent") or args.get("_identity_name") or "")).strip()
    if not name:
        return None
    try:
        from backend.kernel import get_kernel

        reg = getattr(get_kernel(), "identity_registry", None)
        if reg is None:
            return None
        for ident in await reg.list(status="active"):
            if str(getattr(ident, "name", "") or "") == name:
                return str(ident.id)
        # 宽松：忽略大小写
        low = name.lower()
        for ident in await reg.list(status="active"):
            if str(getattr(ident, "name", "") or "").lower() == low:
                return str(ident.id)
        # 管家别名：小白/大管家/CEO 等指向 is_ceo 编制
        try:
            from backend.agent.workforce_dispatch import is_steward_contact

            if is_steward_contact(name):
                ceo = await resolve_ceo_identity()
                if ceo and ceo.get("id"):
                    return str(ceo["id"])
        except Exception:
            pass
    except Exception as e:
        logger.debug("resolve_identity_id skip: %s", e)
    return None


async def resolve_ceo_identity() -> dict[str, str] | None:
    """解析默认 CEO/管家编制（供主会话「本员工允许」与能力短路）。

    优先 is_ceo 标记，其次常见管家名。
    """
    try:
        from backend.kernel import get_kernel

        reg = getattr(get_kernel(), "identity_registry", None)
        if reg is None:
            return None
        active = list(await reg.list(status="active") or [])
        for ident in active:
            meta = getattr(ident, "meta", None) or getattr(ident, "metadata", None) or {}
            if isinstance(meta, dict) and (
                meta.get("is_ceo") is True or str(meta.get("template_id") or "") == "ceo"
            ):
                return {"id": str(ident.id), "name": str(ident.name or "CEO")}
        # 名字启发
        try:
            from backend.agent.workforce_dispatch import is_steward_contact
        except Exception:
            is_steward_contact = None  # type: ignore[assignment]
        for ident in active:
            nm = str(getattr(ident, "name", "") or "")
            role = str(getattr(ident, "role", "") or "")
            if is_steward_contact and (
                is_steward_contact(nm) or is_steward_contact(role)
            ):
                return {"id": str(ident.id), "name": nm or "CEO"}
            low = nm.lower()
            if low in {"ceo", "小白", "管家", "大管家"} or "ceo" in low:
                return {"id": str(ident.id), "name": nm or "CEO"}
    except Exception as e:
        logger.debug("resolve_ceo_identity skip: %s", e)
    return None


async def has_identity_tool_grant(
    tool: str,
    *,
    identity_id: str | None = None,
    arguments: dict[str, Any] | None = None,
    capabilities: list[str] | None = None,
) -> bool:
    """员工编制能力是否已覆盖该工具（「本员工允许」写入后应短路弹窗）。"""
    caps = capabilities
    if caps is None and isinstance((arguments or {}).get("_identity_capabilities"), list):
        caps = list((arguments or {}).get("_identity_capabilities") or [])
    if caps is None:
        iid = identity_id or await resolve_identity_id(arguments)
        if not iid:
            return False
        try:
            from backend.agent.steward_permission import load_identity_capabilities

            caps = await load_identity_capabilities(iid)
        except Exception:
            caps = None
    if not caps:
        return False
    return tool_matches_crew_caps(tool, caps)


def clear_session_grants(session_id: str | None) -> None:
    if not session_id:
        return
    _ensure_loaded()
    with _grants_lock:
        sid = str(session_id)
        had = sid in _session_grants
        _session_grants.pop(sid, None)
        _session_grant_ts.pop(sid, None)
        if had:
            _persist()
    _rust_grant_rpc("session_grant_clear", {"session_id": str(session_id)})


def prune_expired_session_grants() -> int:
    """Drop grants past TTL. Returns number removed."""
    _ensure_loaded()
    with _grants_lock:
        dead = [
            sid
            for sid, ts in list(_session_grant_ts.items())
            if _grant_ttl_seconds() > 0
            and (time.time() - float(ts or 0)) > _grant_ttl_seconds()
        ]
        n = _prune_expired_locked()
        if n:
            _persist()
            logger.info("session grants TTL pruned count=%s", n)
    for sid in dead:
        _rust_grant_rpc("session_grant_clear", {"session_id": sid})
    return n


def prune_orphan_session_grants(live_session_ids: set[str] | list[str] | None) -> int:
    """Drop grants whose session_id is not in the live set (DB sessions)."""
    if live_session_ids is None:
        return 0
    live = {str(s) for s in live_session_ids}
    _ensure_loaded()
    with _grants_lock:
        dead = [sid for sid in list(_session_grants.keys()) if sid not in live]
        for sid in dead:
            _session_grants.pop(sid, None)
            _session_grant_ts.pop(sid, None)
        if dead:
            _persist()
            logger.info(
                "session grants orphan pruned count=%s remaining=%s",
                len(dead),
                len(_session_grants),
            )
    for sid in dead:
        _rust_grant_rpc("session_grant_clear", {"session_id": sid})
    return len(dead)


async def prune_session_grants_startup() -> dict[str, int]:
    """Startup: TTL prune + drop grants for sessions no longer in DB."""
    expired = prune_expired_session_grants()
    orphaned = 0
    try:
        from sqlalchemy import select

        from backend.database import AsyncSessionLocal
        from backend.models.session import Session

        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(Session.id))).scalars().all()
            live = {str(r) for r in rows}
        orphaned = prune_orphan_session_grants(live)
    except Exception as e:
        logger.warning("session grants orphan prune skipped: %s", e)
    return {"expired": expired, "orphaned": orphaned}


def crew_cap_for_tool(tool: str) -> str | None:
    return TOOL_TO_CREW_CAP.get(tool) or TOOL_TO_CREW_CAP.get(tool.split(":")[0])


async def grant_agent_capability(
    identity_id: str | None,
    tool: str,
    *,
    by: str = "user_confirm",
) -> tuple[bool, str]:
    """把工具对应能力并入员工编制（持久）。返回 (ok, message)。"""
    if not identity_id:
        return False, "当前操作未绑定员工 Identity，无法「允许本员工」"
    cap = crew_cap_for_tool(tool)
    if not cap:
        return False, f"工具 {tool} 没有对应的员工能力槽，已改为仅本会话放行"
    try:
        from backend.kernel import get_kernel

        kernel = get_kernel()
        reg = getattr(kernel, "identity_registry", None)
        if reg is None:
            return False, "编制层未启用"
        ident = await reg.get(identity_id)
        if ident is None:
            return False, f"员工不存在: {identity_id}"
        caps = list(ident.capabilities or [])
        if cap in caps:
            logger.info(
                "grant_agent_capability already has cap=%s identity=%s",
                cap,
                str(identity_id)[:8],
            )
            return True, f"员工「{ident.name}」已具备能力 {cap}"
        caps.append(cap)
        await reg.set_capabilities(identity_id, caps, by=by)
        logger.info(
            "grant_agent_capability wrote cap=%s identity=%s by=%s",
            cap,
            str(identity_id)[:8],
            by,
        )
        return True, f"已写入员工「{ident.name}」能力：{cap}"
    except Exception as e:
        logger.warning("grant_agent_capability failed: %s", e)
        return False, f"写入员工能力失败: {e}"


def reset_for_tests() -> None:
    global _persist_loaded
    with _grants_lock:
        _session_grants.clear()
        _session_grant_ts.clear()
        _persist_loaded = True  # 测试不读盘
        try:
            p = _grants_path()
            if p.is_file():
                p.unlink()
        except OSError:
            pass
    # Do not session_grant_clear the live host: pytest may share a desktop
    # tevarn-kernel-host. Court tests clear their own session ids explicitly.
