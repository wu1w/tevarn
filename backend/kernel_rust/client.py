"""JSON-RPC client for takton-kernel-host — AgentKernel-compatible API."""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

DEFAULT_HOST = os.environ.get("TAKTON_KERNEL_HOST", "127.0.0.1:17890")
_RPC_TIMEOUT = float(os.environ.get("TAKTON_KERNEL_RPC_TIMEOUT", "10"))


class KernelPermissionError(PermissionError):
    def __init__(self, message: str, decision: Any = None) -> None:
        super().__init__(message)
        self.decision = decision


class BudgetExceededError(RuntimeError):
    pass


class CapabilityEscalationError(PermissionError):
    pass


@dataclass
class MediationDecision:
    allowed: bool
    reason: str = ""
    capability_checked: bool = False


@dataclass
class KernelEvent:
    kind: str
    process_id: str
    detail: dict[str, Any] = field(default_factory=dict)
    id: str = ""
    ts: float = 0.0
    prev_hash: str = ""
    hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "process_id": self.process_id,
            "detail": self.detail,
            "ts": self.ts,
            "prev_hash": self.prev_hash,
            "hash": self.hash,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "KernelEvent":
        return cls(
            kind=str(d.get("kind") or ""),
            process_id=str(d.get("process_id") or ""),
            detail=dict(d.get("detail") or {}),
            id=str(d.get("id") or ""),
            ts=float(d.get("ts") or 0),
            prev_hash=str(d.get("prev_hash") or ""),
            hash=str(d.get("hash") or ""),
        )


@dataclass
class EscalationRequest:
    id: str
    process_id: str
    capabilities: tuple[str, ...]
    reason: str = ""
    status: str = "pending"
    created_at: float = 0.0
    resolved_at: float | None = None
    resolved_by: str | None = None
    target: str | None = None
    identity_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "process_id": self.process_id,
            "capabilities": list(self.capabilities),
            "reason": self.reason,
            "status": self.status,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "resolved_by": self.resolved_by,
        }
        if self.target is not None:
            d["target"] = self.target
        if self.identity_id is not None:
            d["identity_id"] = self.identity_id
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EscalationRequest":
        caps = d.get("capabilities") or []
        return cls(
            id=str(d.get("id") or ""),
            process_id=str(d.get("process_id") or ""),
            capabilities=tuple(caps),
            reason=str(d.get("reason") or ""),
            status=str(d.get("status") or "pending"),
            created_at=float(d.get("created_at") or 0),
            resolved_at=d.get("resolved_at"),
            resolved_by=d.get("resolved_by"),
            target=d.get("target"),
            identity_id=str(d["identity_id"]) if d.get("identity_id") else None,
        )


class RustKernelProcess:
    """Process view hydrated from Rust kernel to_dict()."""

    def __init__(self, data: dict[str, Any], client: "RustAgentKernel | None" = None) -> None:
        self._data = dict(data)
        self._client = client
        self.id: str = str(data.get("id") or "")
        self.identity: str = str(data.get("identity") or "main")
        self.session_id = data.get("session_id")
        self.parent_id = data.get("parent_id")
        self.capabilities = data.get("capabilities")
        self.token_budget = data.get("token_budget")
        self.tokens_used = int(data.get("tokens_used") or 0)
        self.state: str = str(data.get("state") or "created")
        self.created_at = float(data.get("created_at") or 0)
        self.started_at = data.get("started_at")
        self.ended_at = data.get("ended_at")
        self.exit_reason = data.get("exit_reason")
        self.meta: dict[str, Any] = dict(data.get("meta") or {})
        self.token = data.get("token")
        self._resume_event: Any = None

    @property
    def is_terminal(self) -> bool:
        return self.state in ("completed", "failed", "killed")

    @property
    def budget_remaining(self) -> int | None:
        if self.token_budget is None:
            return None
        return max(0, int(self.token_budget) - int(self.tokens_used))

    def has_capability(self, cap: str) -> bool:
        if self.capabilities is None:
            return True
        if cap in self.capabilities or "*" in self.capabilities:
            return True
        try:
            from backend.agent.grant_store import tool_matches_crew_caps

            return tool_matches_crew_caps(cap, self.capabilities)
        except Exception:
            return False

    def charge_tokens(self, amount: int) -> int | None:
        if self._client is not None:
            return self._client.charge_tokens(self.id, amount)
        raise RuntimeError("no kernel client")

    def suspend(self) -> None:
        if self.is_terminal:
            raise ValueError(f"进程 {self.id} 已终止（{self.state}），不可挂起")
        if self._client is not None:
            p = self._client.suspend_process_sync(self.id)
            self._refresh(p)
        else:
            self.state = "suspended"

    def resume(self) -> None:
        if self._client is not None:
            p = self._client.resume_process_sync(self.id)
            self._refresh(p)
        elif self.state == "suspended":
            self.state = "running"

    async def wait_if_suspended(
        self, *, poll: float = 0.5, should_stop: Any = None, refresh_state: Any = None
    ) -> bool:
        import asyncio

        while self.state == "suspended":
            if should_stop is not None and should_stop():
                return False
            if refresh_state is not None:
                try:
                    refresh_state(self)
                except Exception:
                    pass
            if self._client is not None:
                fresh = self._client.get_process(self.id)
                if fresh is not None:
                    self._refresh(fresh)
            if self.state != "suspended":
                break
            await asyncio.sleep(poll)
        return not self.is_terminal

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "identity": self.identity,
            "session_id": self.session_id,
            "parent_id": self.parent_id,
            "capabilities": self.capabilities,
            "token_budget": self.token_budget,
            "tokens_used": self.tokens_used,
            "budget_remaining": self.budget_remaining,
            "state": self.state,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "exit_reason": self.exit_reason,
            "meta": self.meta,
            "token": self.token,
        }

    def _refresh(self, other: "RustKernelProcess") -> None:
        self._data = other._data
        self.identity = other.identity
        self.session_id = other.session_id
        self.parent_id = other.parent_id
        self.capabilities = other.capabilities
        self.token_budget = other.token_budget
        self.tokens_used = other.tokens_used
        self.state = other.state
        self.created_at = other.created_at
        self.started_at = other.started_at
        self.ended_at = other.ended_at
        self.exit_reason = other.exit_reason
        self.meta = other.meta
        self.token = other.token


class _JsonRpcClient:
    def __init__(self, host: str = DEFAULT_HOST) -> None:
        self.host = host
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._recv_buf = bytearray()
        self._id = 0

    def _parse_addr(self) -> tuple[str, int]:
        host, _, port = self.host.rpartition(":")
        return host or "127.0.0.1", int(port or 17890)

    def connect(self) -> None:
        with self._lock:
            if self._sock is not None:
                return
            h, p = self._parse_addr()
            last_err: Exception | None = None
            for attempt in range(15):
                try:
                    s = socket.create_connection((h, p), timeout=_RPC_TIMEOUT)
                    s.settimeout(_RPC_TIMEOUT)
                    # 禁用 Nagle，降低小 JSON 行延迟
                    try:
                        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    except OSError:
                        pass
                    self._sock = s
                    self._recv_buf = bytearray()
                    return
                except OSError as e:
                    last_err = e
                    time.sleep(0.1 * (attempt + 1))
            raise ConnectionError(
                f"cannot connect to kernel host {h}:{p}: {last_err}"
            ) from last_err

    def close(self) -> None:
        with self._lock:
            try:
                if self._sock:
                    self._sock.close()
            except Exception:
                pass
            self._sock = None
            self._recv_buf = bytearray()

    def _recv_line(self) -> bytes:
        """按行读响应；依赖 socket timeout，避免 makefile 在 Windows 上吞超时。"""
        assert self._sock is not None
        while True:
            nl = self._recv_buf.find(b"\n")
            if nl >= 0:
                line = bytes(self._recv_buf[:nl])
                del self._recv_buf[: nl + 1]
                return line
            try:
                chunk = self._sock.recv(65536)
            except socket.timeout as e:
                raise TimeoutError(
                    f"kernel host RPC read timeout ({_RPC_TIMEOUT}s)"
                ) from e
            if not chunk:
                raise ConnectionError("kernel host closed connection")
            self._recv_buf.extend(chunk)

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        with self._lock:
            if self._sock is None:
                raise ConnectionError("not connected")
            self._id += 1
            req_id = self._id
            req = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params or {},
            }
            line = (json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8")
            try:
                self._sock.sendall(line)
            except socket.timeout as e:
                raise TimeoutError(
                    f"kernel host RPC write timeout ({_RPC_TIMEOUT}s) method={method}"
                ) from e
            raw = self._recv_line()
            if not raw:
                raise ConnectionError("kernel host closed connection")
            resp = json.loads(raw.decode("utf-8"))
            if "error" in resp and resp["error"] is not None:
                err = resp["error"]
                msg = str(err.get("message") or "kernel error")
                data = err.get("data") or {}
                kind = (data.get("error") if isinstance(data, dict) else None) or ""
                code = int(err.get("code") or 0)
                if kind == "permission" or code == -32001:
                    raise KernelPermissionError(msg, MediationDecision(False, msg, True))
                if kind == "budget_exceeded" or code == -32002:
                    raise BudgetExceededError(msg)
                if kind == "capability_escalation" or code == -32003:
                    raise CapabilityEscalationError(msg)
                if kind == "invalid" or code == -32005:
                    raise ValueError(msg)
                if kind == "not_found" or code == -32004:
                    raise ValueError(msg)
                raise RuntimeError(msg)
            return resp.get("result")


def _find_host_bin() -> Path | None:
    """Locate takton-kernel-host binary.

    优先 ``target/release`` / ``target/debug``（当前 ABI），再回落 vendor。
    在候选中取 **最新 mtime**，避免拉起缺方法的旧 vendor 副本。
    """
    env = os.environ.get("TAKTON_KERNEL_HOST_BIN")
    if env and Path(env).is_file():
        return Path(env)
    here = Path(__file__).resolve()
    root = here.parents[2]  # repo root (backend/kernel_rust -> backend -> root)
    names = ("takton-kernel-host.exe", "takton-kernel-host")
    # release/debug 优先于 vendor（旧 vendor 可能缺 pkg_set_signing_key 等）
    dirs = [
        root / "target" / "release",
        root / "target" / "debug",
        root / "vendor" / "takton-kernel-host",
        root / "vendor",
    ]
    extra_roots = [
        Path(os.environ.get("TAKTON_ROOT", "") or ""),
        Path.cwd(),
        here.parents[1],  # backend/
    ]
    for er in extra_roots:
        if er and er.is_dir():
            dirs.extend(
                [
                    er / "target" / "release",
                    er / "target" / "debug",
                    er / "vendor" / "takton-kernel-host",
                    er / "vendor",
                ]
            )
    candidates: list[Path] = []
    seen: set[str] = set()
    for d in dirs:
        try:
            key = str(d.resolve())
        except Exception:
            key = str(d)
        if key in seen:
            continue
        seen.add(key)
        for name in names:
            c = d / name
            if c.is_file():
                candidates.append(c)
    if not candidates:
        return None

    def _rank(p: Path) -> tuple[int, float]:
        """target/{release,debug} 优先于 vendor；同档取 mtime 最新（避免旧 release 缺 ABI 方法）。"""
        s = str(p).replace("\\", "/").lower()
        if "/target/release/" in s or "/target/debug/" in s:
            tier = 0
        else:
            tier = 1
        try:
            mtime = -float(p.stat().st_mtime)
        except OSError:
            mtime = 0.0
        return (tier, mtime)

    try:
        return min(candidates, key=_rank)
    except OSError:
        return candidates[0]


_host_proc: subprocess.Popen | None = None
_host_lock = threading.Lock()


def is_rust_host_available(host: str = DEFAULT_HOST) -> bool:
    h, _, p = host.rpartition(":")
    try:
        with socket.create_connection((h or "127.0.0.1", int(p or 17890)), timeout=0.5):
            return True
    except OSError:
        return False


def stop_kernel_host() -> None:
    """终止本进程拉起的 host（外部已有 host 不处理）。"""
    global _host_proc
    with _host_lock:
        proc = _host_proc
        _host_proc = None
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()
    except Exception as e:
        logger.debug("stop_kernel_host: %s", e)


def _kill_stale_host_processes() -> None:
    """仅终止名为 takton-kernel-host 的残留进程（超时恢复用）。"""
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/IM", "takton-kernel-host.exe"],
                capture_output=True,
                timeout=5,
                check=False,
            )
        else:
            subprocess.run(
                ["pkill", "-f", "takton-kernel-host"],
                capture_output=True,
                timeout=5,
                check=False,
            )
    except Exception as e:
        logger.debug("kill stale host: %s", e)


def restart_kernel_host(
    host: str = DEFAULT_HOST, *, extra_args: list[str] | None = None
) -> bool:
    """停掉 host 并重新 start（host 卡死时的恢复路径）。"""
    stop_kernel_host()
    # 外部残留 / 无响应 host：按进程名强杀后重启
    if is_rust_host_available(host):
        _kill_stale_host_processes()
    for _ in range(40):
        if not is_rust_host_available(host):
            break
        time.sleep(0.1)
    return start_kernel_host(host, extra_args=extra_args)


def start_kernel_host(host: str = DEFAULT_HOST, *, extra_args: list[str] | None = None) -> bool:
    """Spawn takton-kernel-host if binary exists. Returns True if host is up.

    Product default enables soft budget renew. Tests may pass
    ``extra_args=["--no-soft-renew"]``.
    """
    global _host_proc
    if is_rust_host_available(host):
        return True
    bin_path = _find_host_bin()
    if bin_path is None:
        logger.error(
            "takton-kernel-host binary not found. Build with:\n"
            "  cargo build -p takton-kernel-host --release\n"
            "  .\\scripts\\build-kernel-host.ps1 -Release\n"
            "Or set TAKTON_KERNEL_HOST_BIN to the executable path."
        )
        return False
    with _host_lock:
        if is_rust_host_available(host):
            return True
        cmd = [str(bin_path), "--listen", host]
        if extra_args:
            cmd.extend(extra_args)
        try:
            popen_kwargs: dict[str, Any] = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.PIPE,
            }
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            _host_proc = subprocess.Popen(cmd, **popen_kwargs)
        except Exception as e:
            logger.error("failed to start kernel host (%s): %s", bin_path, e)
            return False
        for _ in range(80):
            if is_rust_host_available(host):
                logger.info(
                    "rust kernel host ready at %s (pid=%s bin=%s)",
                    host,
                    _host_proc.pid,
                    bin_path,
                )
                return True
            if _host_proc.poll() is not None:
                err = ""
                try:
                    if _host_proc.stderr:
                        err = _host_proc.stderr.read().decode("utf-8", errors="replace")[:500]
                except Exception:
                    pass
                logger.error(
                    "kernel host exited early code=%s stderr=%s",
                    _host_proc.returncode,
                    err,
                )
                return False
            time.sleep(0.1)
        logger.error("kernel host did not become ready at %s within timeout", host)
        return False


class RustAgentKernel:
    """Drop-in stand-in for backend.kernel.kernel.AgentKernel backed by Rust host."""

    def __init__(self, host: str = DEFAULT_HOST, *, auto_start: bool = True) -> None:
        self._host = host
        self._rpc = _JsonRpcClient(host)
        self.identity_registry: Any | None = None
        self._scheduler_proxy = _SchedulerProxy(self)
        if auto_start and not is_rust_host_available(host):
            if os.environ.get("TAKTON_KERNEL_AUTO_START", "1") not in ("0", "false", "False"):
                start_kernel_host(host)
        self._rpc.connect()
        self._configure_pkg_signing()

    def _configure_pkg_signing(self) -> None:
        """Push package HMAC key so we never rely on public insecure_default in app mode."""
        key = (os.environ.get("TAKTON_PKG_SIGNING_KEY") or "").strip()
        if not key:
            try:
                from backend.core.config import settings

                key = str(getattr(settings, "agent_package_signing_key", "") or "").strip()
                if not key:
                    # use jwt secret as signing material (≥16) — host stores as "set"
                    key = str(getattr(settings, "jwt_secret", "") or "").strip()
            except Exception:
                key = ""
        if len(key) < 16:
            return
        try:
            self._call("pkg_set_signing_key", {"key": key})
        except Exception as e:
            logger.debug("pkg_set_signing_key skip: %s", e)

    @property
    def scheduler(self) -> Any:
        return self._scheduler_proxy

    def _call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        try:
            return self._rpc.call(method, params)
        except (ConnectionError, OSError, BrokenPipeError, TimeoutError, socket.timeout) as e:
            logger.warning("kernel RPC %s failed (%s); reconnect/retry", method, e)
            self._rpc.close()
            # 读超时通常 = host 卡死（端口仍监听但不回包）：必须强杀重启
            try:
                if isinstance(e, (TimeoutError, socket.timeout)):
                    restart_kernel_host(self._host)
                elif not is_rust_host_available(self._host):
                    start_kernel_host(self._host)
            except Exception as re:
                logger.debug("host recover after RPC fail: %s", re)
            self._rpc.connect()
            return self._rpc.call(method, params)

    def _proc(self, data: dict[str, Any] | None) -> RustKernelProcess | None:
        if not data or not isinstance(data, dict) or not data.get("id"):
            return None
        return RustKernelProcess(data, self)

    async def create_process(
        self,
        identity: str,
        *,
        session_id: str | None = None,
        parent_id: str | None = None,
        capabilities: list[str] | None = None,
        token_budget: int | None = None,
        meta: dict[str, Any] | None = None,
        intent: dict[str, Any] | None = None,
    ) -> RustKernelProcess:
        params: dict[str, Any] = {
            "identity": identity,
            "session_id": session_id,
            "parent_id": parent_id,
            "capabilities": capabilities,
            "token_budget": token_budget,
            "meta": meta or {},
        }
        if intent is not None:
            params["intent"] = intent
        result = self._call("create_process", params)
        p = self._proc(result)
        assert p is not None
        return p

    def apply_intent(
        self,
        process_id: str,
        intent: dict[str, Any],
        *,
        parent_token: Any | None = None,
    ) -> tuple[Any, list[str]]:
        """P0-B: synthesize caps+token on process. Returns (CapabilityToken, dropped)."""
        params: dict[str, Any] = {"process_id": process_id, "intent": intent}
        if parent_token is not None:
            if hasattr(parent_token, "to_dict"):
                try:
                    params["parent_token"] = parent_token.to_dict(sign=False)
                except TypeError:
                    params["parent_token"] = parent_token.to_dict()
            elif isinstance(parent_token, dict):
                params["parent_token"] = parent_token
        r = self._call("apply_intent", params) or {}
        from backend.kernel.capability import CapabilityToken

        tok = CapabilityToken.from_dict(r.get("token") or {}, verify=False)
        # refresh local view if process returned
        if r.get("process"):
            pass
        return tok, list(r.get("dropped") or [])

    def filter_tools(self, process_id: str, tool_names: list[str]) -> list[str]:
        r = self._call(
            "filter_tools",
            {"process_id": process_id, "tools": list(tool_names)},
        ) or {}
        return list(r.get("tools") or [])

    def tools_for_process(self, process_id: str) -> list[str] | None:
        r = self._call("tools_for_process", {"process_id": process_id}) or {}
        if r.get("unrestricted"):
            return None
        return list(r.get("tools") or [])

    def tool_catalog(self) -> dict[str, Any]:
        return self._call("tool_catalog") or {}

    def schedule_run(
        self,
        process_id: str,
        *,
        priority_class: str = "workforce",
        priority: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "process_id": process_id,
            "priority_class": priority_class,
            "payload": payload or {},
        }
        if priority is not None:
            params["priority"] = priority
        return self._call("schedule_run", params) or {}

    def run_acquire(self, process_id: str) -> int | None:
        r = self._call("run_acquire", {"process_id": process_id}) or {}
        return r.get("remaining")

    def run_release(self, process_id: str) -> bool:
        r = self._call("run_release", {"process_id": process_id}) or {}
        return bool(r.get("ok"))

    def run_gate_try(
        self,
        process_id: str,
        *,
        priority_class: str = "workforce",
        priority: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "process_id": process_id,
            "priority_class": priority_class,
        }
        if priority is not None:
            params["priority"] = priority
        return self._call("run_gate_try", params) or {}

    def run_gate_poll(self, request_id: str) -> dict[str, Any]:
        return self._call("run_gate_poll", {"request_id": request_id}) or {}

    def run_gate_release(self, process_id: str) -> bool:
        r = self._call("run_gate_release", {"process_id": process_id}) or {}
        return bool(r.get("ok"))

    def run_gate_status(self) -> dict[str, Any]:
        return self._call("run_gate_status") or {}

    def run_gate_set_max(self, max_concurrent: int) -> dict[str, Any]:
        return (
            self._call("run_gate_set_max", {"max_concurrent": int(max_concurrent)})
            or {}
        )

    def llm_status(self) -> dict[str, Any]:
        return self._call("llm_status") or {}

    def decide_tool(
        self,
        name: str,
        args: dict[str, Any] | None = None,
        *,
        process_id: str | None = None,
        skill_tools: list[str] | None = None,
        skill_deny: list[str] | None = None,
        emit: bool = True,
    ) -> dict[str, Any]:
        return (
            self._call(
                "decide_tool",
                {
                    "name": name,
                    "args": args or {},
                    "process_id": process_id,
                    "skill_tools": skill_tools,
                    "skill_deny": skill_deny,
                    "emit": emit,
                },
            )
            or {}
        )

    def set_court_policy(self, policy: dict[str, Any]) -> bool:
        r = self._call("set_court_policy", policy) or {}
        return bool(r.get("ok", True))

    def export_decision_trail(
        self, process_id: str, *, limit: int = 500
    ) -> dict[str, Any]:
        return (
            self._call(
                "export_decision_trail",
                {"process_id": process_id, "limit": limit},
            )
            or {}
        )

    def isolation_resolve(
        self,
        process_id: str,
        *,
        profile: str | None = None,
        is_workforce: bool = False,
    ) -> dict[str, Any]:
        return (
            self._call(
                "isolation_resolve",
                {
                    "process_id": process_id,
                    "profile": profile,
                    "is_workforce": is_workforce,
                },
            )
            or {}
        )

    # ── P0.5: long-run reliability ─────────────────────────

    def process_snapshot(
        self, process_id: str, *, meta: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"process_id": process_id}
        if meta is not None:
            params["meta"] = meta
        return self._call("process_snapshot", params) or {}

    def process_snapshot_latest(self, process_id: str) -> dict[str, Any] | None:
        r = self._call("process_snapshot_latest", {"process_id": process_id})
        return r if isinstance(r, dict) and r.get("id") else None

    def process_recovery_plan(self, process_id: str) -> dict[str, Any]:
        return self._call("process_recovery_plan", {"process_id": process_id}) or {}

    def result_spill(
        self, process_id: str, tool: str, content: str
    ) -> dict[str, Any]:
        return (
            self._call(
                "result_spill",
                {"process_id": process_id, "tool": tool, "content": content},
            )
            or {}
        )

    def result_load(self, handle_id: str) -> dict[str, Any]:
        return self._call("result_load", {"handle_id": handle_id}) or {}

    def iteration_set_budget(self, process_id: str, max_total: int) -> bool:
        r = (
            self._call(
                "iteration_set_budget",
                {"process_id": process_id, "max_total": int(max_total)},
            )
            or {}
        )
        return bool(r.get("ok", True))

    def iteration_consume(self, process_id: str) -> dict[str, Any]:
        return self._call("iteration_consume", {"process_id": process_id}) or {}

    def iteration_refund(self, process_id: str) -> bool:
        r = self._call("iteration_refund", {"process_id": process_id}) or {}
        return bool(r.get("ok"))

    def iteration_status(self, process_id: str) -> dict[str, Any]:
        return self._call("iteration_status", {"process_id": process_id}) or {}

    def doom_record(
        self,
        process_id: str,
        tool: str,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return (
            self._call(
                "doom_record",
                {"process_id": process_id, "tool": tool, "args": args or {}},
            )
            or {}
        )

    def doom_reset(self, process_id: str) -> bool:
        r = self._call("doom_reset", {"process_id": process_id}) or {}
        return bool(r.get("ok", True))

    def cache_record(
        self, family: str, *, hit: bool, bytes_saved: int = 0
    ) -> dict[str, Any]:
        return (
            self._call(
                "cache_record",
                {
                    "family": family,
                    "hit": bool(hit),
                    "bytes_saved": int(bytes_saved),
                },
            )
            or {}
        )

    def cache_metrics(self) -> dict[str, Any]:
        return self._call("cache_metrics") or {}

    def reclaim_process_tree(
        self, process_id: str, *, reason: str | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"process_id": process_id}
        if reason:
            params["reason"] = reason
        return self._call("reclaim_process_tree", params) or {}

    def cost_charge(
        self,
        process_id: str,
        family: str,
        tokens: int,
        billable: int | None = None,
    ) -> dict[str, Any]:
        return (
            self._call(
                "cost_charge",
                {
                    "process_id": process_id,
                    "family": family,
                    "tokens": int(tokens),
                    "billable": int(billable if billable is not None else tokens),
                },
            )
            or {}
        )

    def cost_panel(self) -> dict[str, Any]:
        return self._call("cost_panel") or {}

    def cost_process(self, process_id: str) -> dict[str, Any]:
        return self._call("cost_process", {"process_id": process_id}) or {}

    def marathon_record(
        self, kind: str, *, reason: str | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"kind": kind}
        if reason:
            params["reason"] = reason
        return self._call("marathon_record", params) or {}

    def marathon_metrics(self) -> dict[str, Any]:
        return self._call("marathon_metrics") or {}

    # ── P1 ─────────────────────────────────────────────────

    def ipc_send(
        self,
        from_id: str,
        to_id: str,
        kind: str = "message",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return (
            self._call(
                "ipc_send",
                {
                    "from": from_id,
                    "to": to_id,
                    "kind": kind,
                    "payload": payload or {},
                },
            )
            or {}
        )

    def ipc_recv(self, process_id: str, max: int = 8) -> dict[str, Any]:
        return (
            self._call("ipc_recv", {"process_id": process_id, "max": int(max)})
            or {}
        )

    def service_list(self) -> dict[str, Any]:
        return self._call("service_list") or {}

    def service_status(self) -> dict[str, Any]:
        return self._call("service_status") or {}

    def sys_memory_put(
        self, identity: str, key: str, value: Any
    ) -> dict[str, Any]:
        return (
            self._call(
                "sys_memory_put",
                {"identity": identity, "key": key, "value": value},
            )
            or {}
        )

    def sys_memory_get(self, identity: str, key: str) -> dict[str, Any]:
        return (
            self._call("sys_memory_get", {"identity": identity, "key": key}) or {}
        )

    def sys_notify_push(
        self,
        process_id: str,
        title: str,
        body: str = "",
        *,
        level: str = "info",
    ) -> dict[str, Any]:
        return (
            self._call(
                "sys_notify_push",
                {
                    "process_id": process_id,
                    "level": level,
                    "title": title,
                    "body": body,
                },
            )
            or {}
        )

    def identity_cache_put(self, identity: dict[str, Any]) -> dict[str, Any]:
        return self._call("identity_cache_put", {"identity": identity}) or {}

    def identity_cache_get(self, id_or_name: str) -> dict[str, Any] | None:
        r = self._call("identity_cache_get", {"id": id_or_name})
        return r if isinstance(r, dict) and r.get("id") else None

    def inbox_submit(
        self,
        identity: str,
        instruction: str,
        *,
        priority: int = 50,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "identity": identity,
            "instruction": instruction,
            "priority": priority,
        }
        if meta is not None:
            params["meta"] = meta
        return self._call("inbox_submit", params) or {}

    def inbox_claim(
        self, worker_id: str, identity: str | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"worker_id": worker_id}
        if identity:
            params["identity"] = identity
        return self._call("inbox_claim", params) or {}

    def inbox_complete(
        self,
        item_id: str,
        claim_token: str,
        result: str,
        process_id: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "item_id": item_id,
            "claim_token": claim_token,
            "result": result,
        }
        if process_id:
            params["process_id"] = process_id
        return self._call("inbox_complete", params) or {}

    def inbox_status(self) -> dict[str, Any]:
        return self._call("inbox_status") or {}

    def skill_register(
        self,
        name: str,
        content: str,
        *,
        version: str = "0.1.0",
        permissions: list[str] | None = None,
        tests: list[str] | None = None,
    ) -> dict[str, Any]:
        return (
            self._call(
                "skill_register",
                {
                    "name": name,
                    "version": version,
                    "content": content,
                    "permissions": permissions or [],
                    "tests": tests or [],
                },
            )
            or {}
        )

    def skill_verify(self, package_id: str) -> dict[str, Any]:
        return self._call("skill_verify", {"package_id": package_id}) or {}

    def skill_activate(self, package_id: str) -> dict[str, Any]:
        return self._call("skill_activate", {"package_id": package_id}) or {}

    def skill_rollback(self, name: str) -> dict[str, Any]:
        return self._call("skill_rollback", {"name": name}) or {}

    def skill_is_loadable(self, name: str) -> bool:
        r = self._call("skill_is_loadable", {"name": name}) or {}
        return bool(r.get("loadable"))

    def evolution_policy(self) -> dict[str, Any]:
        return self._call("evolution_policy") or {}

    def context_set_quota(self, process_id: str, tokens: int) -> dict[str, Any]:
        return (
            self._call(
                "context_set_quota",
                {"process_id": process_id, "tokens": int(tokens)},
            )
            or {}
        )

    def context_put_page(
        self, process_id: str, label: str, content: str
    ) -> dict[str, Any]:
        return (
            self._call(
                "context_put_page",
                {
                    "process_id": process_id,
                    "label": label,
                    "content": content,
                },
            )
            or {}
        )

    def memory_layer_put(
        self,
        identity: str,
        layer: str,
        content: str,
        score: float = 0.5,
    ) -> dict[str, Any]:
        return (
            self._call(
                "memory_layer_put",
                {
                    "identity": identity,
                    "layer": layer,
                    "content": content,
                    "score": float(score),
                },
            )
            or {}
        )

    def memory_layer_consolidate(self, identity: str) -> dict[str, Any]:
        return (
            self._call("memory_layer_consolidate", {"identity": identity}) or {}
        )

    async def end_process(
        self,
        process_id: str,
        *,
        state: Literal["completed", "failed", "killed"] = "completed",
        reason: str | None = None,
    ) -> RustKernelProcess | None:
        result = self._call(
            "end_process",
            {"process_id": process_id, "state": state, "reason": reason},
        )
        return self._proc(result) if result else None

    async def mark_running(self, process_id: str) -> None:
        self._call("mark_running", {"process_id": process_id})

    async def suspend_process(self, process_id: str, *, reason: str = "") -> RustKernelProcess:
        result = self._call("suspend_process", {"process_id": process_id, "reason": reason})
        p = self._proc(result)
        assert p is not None
        return p

    def suspend_process_sync(self, process_id: str, reason: str = "") -> RustKernelProcess:
        result = self._call("suspend_process", {"process_id": process_id, "reason": reason})
        p = self._proc(result)
        assert p is not None
        return p

    async def resume_process(self, process_id: str) -> RustKernelProcess:
        result = self._call("resume_process", {"process_id": process_id})
        p = self._proc(result)
        assert p is not None
        return p

    def resume_process_sync(self, process_id: str) -> RustKernelProcess:
        result = self._call("resume_process", {"process_id": process_id})
        p = self._proc(result)
        assert p is not None
        return p

    def get_process(self, process_id: str) -> RustKernelProcess | None:
        result = self._call("get_process", {"process_id": process_id})
        return self._proc(result)

    def list_processes(self, *, include_terminal: bool = False) -> list[RustKernelProcess]:
        result = self._call("list_processes", {"include_terminal": include_terminal}) or {}
        out = []
        for d in result.get("processes") or []:
            p = self._proc(d)
            if p:
                out.append(p)
        return out

    def live_processes_for_identity(self, identity: str) -> list[RustKernelProcess]:
        key = str(identity or "").strip()
        if not key:
            return []
        return [p for p in self.list_processes(include_terminal=False) if p.identity == key]

    async def retire_live_identity_processes(
        self,
        identity: str,
        *,
        reason: str = "superseded by new job",
        except_process_id: str | None = None,
    ) -> list[str]:
        result = self._call(
            "retire_live_identity_processes",
            {
                "identity": identity,
                "reason": reason,
                "except_process_id": except_process_id,
            },
        ) or {}
        return list(result.get("killed") or [])

    def top_up_budget(
        self,
        process_id: str,
        amount: int,
        *,
        by: str = "ceo",
        reason: str = "",
    ) -> dict[str, Any]:
        return self._call(
            "top_up_budget",
            {"process_id": process_id, "amount": amount, "by": by, "reason": reason},
        )

    def try_soft_renew_budget(
        self,
        process_id: str,
        *,
        need: int = 0,
        reason: str = "soft_renew",
    ) -> dict[str, Any] | None:
        r = self._call(
            "try_soft_renew_budget",
            {"process_id": process_id, "need": need, "reason": reason},
        )
        return r if r else None

    def charge_tokens(self, process_id: str, amount: int) -> int | None:
        r = self._call("charge_tokens", {"process_id": process_id, "amount": amount}) or {}
        # host 写入 last_charge_at；本地若有缓存视图由调用方 get_process 刷新
        return r.get("remaining")

    def _emit_policy_decision(
        self,
        process_id: str,
        *,
        action: Any,
        target: str,
        outcome: str,
        reason: str,
        source: str = "kernel",
        identity: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        detail: dict[str, Any] = {
            "who": identity or process_id,
            "what": f"{action}:{target}",
            "action": str(action),
            "target": target,
            "outcome": outcome,
            "allowed": outcome == "allow",
            "reason": reason,
            "source": source,
        }
        if extra:
            detail.update(extra)
        self._call(
            "emit",
            {"kind": "policy.decision", "process_id": process_id, "detail": detail},
        )

    async def mediate(
        self,
        process_id: str,
        action: str,
        target: str,
        args: dict[str, Any] | None = None,
    ) -> MediationDecision:
        r = self._call(
            "mediate",
            {
                "process_id": process_id,
                "action": action,
                "target": target,
                "args": args or {},
            },
        )
        return MediationDecision(
            allowed=bool(r.get("allowed")),
            reason=str(r.get("reason") or ""),
            capability_checked=bool(r.get("capability_checked")),
        )

    def issue_token(
        self,
        process_id: str,
        capabilities: list[str] | set[str] | frozenset[str] | None = None,
        *,
        expires_at: float | None = None,
    ) -> Any:
        caps = list(capabilities) if capabilities is not None else None
        data = self._call(
            "issue_token",
            {
                "process_id": process_id,
                "capabilities": caps,
                "expires_at": expires_at,
            },
        )
        from backend.kernel.capability import CapabilityToken

        return CapabilityToken.from_dict(data, verify=False)

    async def request_escalation(
        self,
        process_id: str,
        capabilities: list[str] | set[str],
        *,
        reason: str = "",
    ) -> EscalationRequest:
        r = self._call(
            "request_escalation",
            {
                "process_id": process_id,
                "capabilities": list(capabilities),
                "reason": reason,
            },
        )
        return EscalationRequest.from_dict(r)

    async def approve_escalation(self, request_id: str, *, by: str = "user") -> EscalationRequest:
        r = self._call("approve_escalation", {"request_id": request_id, "by": by})
        return EscalationRequest.from_dict(r)

    async def deny_escalation(self, request_id: str, *, by: str = "user") -> EscalationRequest:
        r = self._call("deny_escalation", {"request_id": request_id, "by": by})
        return EscalationRequest.from_dict(r)

    async def ensure_escalation_loaded(self, request_id: str) -> EscalationRequest | None:
        for e in self.list_escalations():
            if e.id == request_id:
                return e
        return None

    def list_escalations(self, *, status: str | None = None) -> list[EscalationRequest]:
        r = self._call("list_escalations", {"status": status}) or {}
        return [EscalationRequest.from_dict(d) for d in r.get("escalations") or []]

    def hydrate_escalation(self, data: dict[str, Any]) -> None:
        # no local store — host is authority
        _ = data

    def verify_event_chain(self) -> tuple[bool, int]:
        r = self._call("verify_event_chain") or {}
        return bool(r.get("ok")), int(r.get("break_index") if r.get("break_index") is not None else -1)

    def events(
        self,
        *,
        process_id: str | None = None,
        kind: str | None = None,
        limit: int = 200,
    ) -> list[KernelEvent]:
        r = self._call(
            "events",
            {"process_id": process_id, "kind": kind, "limit": limit},
        ) or {}
        return [KernelEvent.from_dict(d) for d in r.get("events") or []]

    def gc_terminal(self, *, older_than_seconds: float = 3600.0) -> int:
        r = self._call("gc_terminal", {"older_than_seconds": older_than_seconds}) or {}
        return int(r.get("removed") or 0)

    def health(self) -> dict[str, Any]:
        return self._call("health") or {}

    def abi_version(self) -> dict[str, Any]:
        return self._call("abi_version") or {}

    def list_methods(self) -> list[str]:
        r = self._call("list_methods") or {}
        return list(r.get("methods") or [])

    def get_escalation(self, request_id: str) -> EscalationRequest | None:
        r = self._call("get_escalation", {"request_id": request_id})
        if not r:
            return None
        return EscalationRequest.from_dict(r)

    def _emit(self, kind: str, process_id: str, detail: dict[str, Any]) -> KernelEvent:
        """Used by IdentityRegistry / Python adapters that call kernel._emit."""
        r = self._call(
            "emit",
            {"kind": kind, "process_id": process_id, "detail": detail or {}},
        ) or {}
        return KernelEvent.from_dict(r)

    def resource_charge(self, process_id: str, kind: str, amount: int = 1) -> int:
        r = self._call(
            "resource_charge",
            {"process_id": process_id, "kind": kind, "amount": amount},
        ) or {}
        return int(r.get("remaining") if r.get("remaining") is not None else 0)

    def resource_usage(self, process_id: str) -> dict[str, Any]:
        return self._call("resource_usage", {"process_id": process_id}) or {}


class _SchedulerProxy:
    def __init__(self, kernel: RustAgentKernel) -> None:
        self._k = kernel

    def submit(self, process_id: str, payload: dict | None = None, *, priority: int = 10) -> Any:
        return self._k._call(
            "scheduler_submit",
            {"process_id": process_id, "payload": payload or {}, "priority": priority},
        )

    def next(self) -> Any:
        return self._k._call("scheduler_next")

    def stats(self) -> dict[str, int]:
        return self._k._call("scheduler_stats") or {}

    def complete(self, task_id: str, *, cancelled: bool = False) -> None:
        self._k._call(
            "scheduler_complete",
            {"task_id": task_id, "cancelled": cancelled},
        )

    def cancel_process(self, process_id: str) -> int:
        r = self._k._call(
            "scheduler_cancel_process",
            {"process_id": process_id},
        ) or {}
        return int(r.get("cancelled") or 0)

    def queued(self) -> list:
        return []


_rust_singleton: RustAgentKernel | None = None


def get_rust_kernel() -> RustAgentKernel:
    global _rust_singleton
    if _rust_singleton is None:
        # 确保 host 存活（测试超时/杀进程后可能掉线）
        if not is_rust_host_available():
            start_kernel_host()
        _rust_singleton = RustAgentKernel()
        # attach identity registry like Python kernel
        try:
            from backend.database import AsyncSessionLocal
            from backend.kernel.identity import IdentityRegistry

            _rust_singleton.identity_registry = IdentityRegistry(
                _rust_singleton, AsyncSessionLocal
            )
        except Exception as e:
            logger.warning("rust kernel identity registry attach failed: %s", e)
        # P0-B: keep grant_store catalog aligned with Rust authority
        # 传入已构造实例，禁止 sync → get_kernel 重入二次 init
        try:
            from backend.agent.grant_store import sync_catalog_from_kernel

            sync_catalog_from_kernel(kernel=_rust_singleton)
        except Exception as e:
            logger.debug("catalog sync skip: %s", e)
    return _rust_singleton


def reset_rust_kernel_for_tests() -> None:
    """测试隔离：清空 host 残留 live 进程。

    成功时**保留** RPC 连接与 ``_rust_singleton``（反复 close/重建在 Windows 上
    易半开挂死）。仅在清理 RPC 失败时丢弃单例，下次 ``get_rust_kernel`` 重建。
    """
    global _rust_singleton
    k = _rust_singleton
    if k is None:
        return
    try:
        live = list(k.list_processes(include_terminal=False) or [])
        for p in live[:64]:
            try:
                k._call(
                    "end_process",
                    {
                        "process_id": p.id,
                        "state": "killed",
                        "reason": "test_reset",
                    },
                )
            except Exception:
                pass
        try:
            k._call("gc_terminal", {"older_than_seconds": 0})
        except Exception:
            pass
        # 探测连接仍可用
        k._call("ping")
        return
    except Exception as e:
        logger.debug("reset_rust_kernel_for_tests cleanup: %s", e)
    try:
        k._rpc.close()
    except Exception:
        pass
    _rust_singleton = None
