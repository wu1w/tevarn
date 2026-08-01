"""JSON-RPC client for takton-kernel-host — AgentKernel-compatible API."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

DEFAULT_HOST = os.environ.get("TAKTON_KERNEL_HOST", "127.0.0.1:17890")
# Agent mediate can be slower; UI side-channel uses _UI_RPC_TIMEOUT.
_RPC_TIMEOUT = float(os.environ.get("TAKTON_KERNEL_RPC_TIMEOUT", "8"))
_UI_RPC_TIMEOUT = float(os.environ.get("TAKTON_KERNEL_UI_RPC_TIMEOUT", "2.0"))
# Full host restart wipes the in-memory process table. Prefer soft reconnect;
# hard restart only when host is proven stuck (port up, ping dead) after soft fails.
# Default cooldown raised (was 8s) to stop thrash during marathon / assign storms.
_RESTART_COOLDOWN_S = float(os.environ.get("TAKTON_KERNEL_RESTART_COOLDOWN", "45.0"))
# Cap hard restarts per rolling window (env TAKTON_KERNEL_HARD_RESTART_MAX, 0=unlimited)
_HARD_RESTART_MAX = int(os.environ.get("TAKTON_KERNEL_HARD_RESTART_MAX", "3"))
_HARD_RESTART_WINDOW_S = float(os.environ.get("TAKTON_KERNEL_HARD_RESTART_WINDOW", "3600"))
# 0/false: never hard-restart (soft only); operator uses REST restart
_HARD_RESTART_ENABLED = os.environ.get("TAKTON_KERNEL_HARD_RESTART", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
_recovery_lock = threading.Lock()
_last_hard_restart_at = 0.0
_hard_restart_times: list[float] = []
# Agent RPC executor (serial). UI reads use a separate ephemeral TCP client so a
# stuck mediate cannot queue-block panel APIs (Next proxy 500 / socket hang up).
_RPC_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kernel-rpc")
_UI_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="kernel-ui")
_RPC_RESULT_TIMEOUT = float(os.environ.get("TAKTON_KERNEL_RPC_RESULT_TIMEOUT", "40"))
# Background / best-effort RPCs: soft reconnect only (never taskkill).
_SOFT_ONLY_METHODS = frozenset(
    {
        "inbox_reclaim",
        "inbox_touch_by_db_id",
        "inbox_set_claim_timeout",
        "inbox_list",
        "inbox_status",
        "inbox_submit",
        "isolation_reap",
        "doom_record",
        "emit",
        "audit_append",
        "ping",
        "health",
        "list_methods",
        "list_processes",
        "list_escalations",
        "get_process",
        "iteration_consume",
        "iteration_status",
        "top_up_budget",
        "try_soft_renew_budget",
        "charge_tokens",
        "resource_release",
        "resource_usage",
    }
)
# Dashboard / panel methods: short-timeout side-channel (own socket).
# NOTE: list_methods / ping / health stay on the agent path — ABI gate and
# liveness must not use the UI degrade-to-empty path (false host_abi_mismatch).
_UI_SIDECHANNEL_METHODS = frozenset(
    {
        "list_processes",
        "list_escalations",
        "get_process",
        "abi_version",
        "tool_catalog",
        "run_gate_status",
        "llm_status",
        "cost_panel",
        "service_list",
        "service_status",
        "cache_metrics",
        "marathon_metrics",
        "eval_status",
    }
)

# 必须与 backend.kernel 共用同一异常类：
# 1) tool_gate `except KernelPermissionError` 才能命中
# 2) PermissionError ⊂ OSError —— 若自建子类会被 _call 当成断连 reconnect
from backend.kernel.capability import CapabilityEscalationError
from backend.kernel.kernel import BudgetExceededError, KernelPermissionError


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
    def __init__(
        self, host: str = DEFAULT_HOST, *, rpc_timeout: float | None = None
    ) -> None:
        self.host = host
        self.rpc_timeout = float(
            rpc_timeout if rpc_timeout is not None else _RPC_TIMEOUT
        )
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._recv_buf = bytearray()
        self._id = 0

    def _parse_addr(self) -> tuple[str, int]:
        host, _, port = self.host.rpartition(":")
        return host or "127.0.0.1", int(port or 17890)

    def connect(self, *, attempts: int | None = None, connect_timeout: float | None = None) -> None:
        """Connect to host. H-14: keep initial connect short so missing host fails fast."""
        with self._lock:
            if self._sock is not None:
                return
            h, p = self._parse_addr()
            last_err: Exception | None = None
            n = int(attempts if attempts is not None else 12)
            # 首次建连用短超时；RPC 读写用本 client 的 rpc_timeout
            cto = float(
                connect_timeout
                if connect_timeout is not None
                else min(1.5, self.rpc_timeout)
            )
            for attempt in range(max(1, n)):
                try:
                    s = socket.create_connection((h, p), timeout=cto)
                    s.settimeout(self.rpc_timeout)
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
                    time.sleep(min(0.25, 0.05 * (attempt + 1)))
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
                    f"kernel host RPC read timeout ({self.rpc_timeout}s)"
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
# chat_elastic / workforce auto top-up 次数（Rust meta 写回不可靠时的权威侧车）
_CHAT_TOP_UP_COUNTS: dict[str, int] = {}
_WF_TOP_UP_COUNTS: dict[str, int] = {}
_host_lock = threading.Lock()


def is_rust_host_available(host: str = DEFAULT_HOST) -> bool:
    """TCP connect probe. Short timeout — hung accept looks like unavailable."""
    h, _, p = host.rpartition(":")
    try:
        with socket.create_connection((h or "127.0.0.1", int(p or 17890)), timeout=0.35):
            return True
    except OSError:
        return False


def is_rust_host_responsive(host: str = DEFAULT_HOST, *, timeout: float = 1.0) -> bool:
    """Port open AND answers JSON-RPC ping (detects accept-dead hosts)."""
    if not is_rust_host_available(host):
        return False
    client: _JsonRpcClient | None = None
    try:
        client = _JsonRpcClient(host, rpc_timeout=timeout)
        client.connect(attempts=1, connect_timeout=min(0.5, timeout))
        r = client.call("ping", {})
        return bool(r is not None)
    except Exception:
        return False
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


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
    host: str = DEFAULT_HOST,
    *,
    extra_args: list[str] | None = None,
    force: bool = False,
) -> bool:
    """停掉 host 并重新 start（host 卡死时的恢复路径）。

    Serialized + cooldown: concurrent RPC failures must not multi-taskkill the
    host (that wiped the process table mid-rehydrate and broke crew_steward assign).

    force=True: operator/UI 一键重启 — 跳过 cooldown，且始终 taskkill 残留进程
    （假死时 port 仍 open，is_rust_host_available 会误报 True 导致跳过）。
    """
    global _last_hard_restart_at, _hard_restart_times
    with _recovery_lock:
        if not _HARD_RESTART_ENABLED:
            logger.warning(
                "kernel host hard-restart disabled (TAKTON_KERNEL_HARD_RESTART=0); soft-only"
            )
            return is_rust_host_available(host)
        now = time.time()
        # Rate limit: max N hard restarts per window (force still counts)
        _hard_restart_times = [
            t for t in _hard_restart_times if now - t < _HARD_RESTART_WINDOW_S
        ]
        if _HARD_RESTART_MAX > 0 and len(_hard_restart_times) >= _HARD_RESTART_MAX:
            logger.error(
                "kernel host hard-restart rate-limited (%s/%ss); soft-only",
                _HARD_RESTART_MAX,
                int(_HARD_RESTART_WINDOW_S),
            )
            return is_rust_host_available(host)
        if (
            not force
            and now - float(_last_hard_restart_at or 0.0) < _RESTART_COOLDOWN_S
            and is_rust_host_available(host)
        ):
            logger.warning(
                "kernel host hard-restart skipped (cooldown %.1fs, host still up)",
                _RESTART_COOLDOWN_S,
            )
            return True
        stop_kernel_host()
        # Always force-kill by image name on operator restart, or if port still open
        # after stop (external / orphan host, or hung accept loop).
        if force or is_rust_host_available(host):
            _kill_stale_host_processes()
        for _ in range(50):
            if not is_rust_host_available(host):
                break
            time.sleep(0.1)
        else:
            # Still up after ~5s — one more kill pass
            _kill_stale_host_processes()
            time.sleep(0.3)
        ok = start_kernel_host(host, extra_args=extra_args)
        if ok:
            _last_hard_restart_at = time.time()
            _hard_restart_times.append(_last_hard_restart_at)
        return ok


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
            # P0：stderr=PIPE 无人排空 → Windows 管道写满后 host 阻塞 → RPC 全超时
            # 启动期若需诊断，设 TAKTON_KERNEL_HOST_STDERR=pipe 并起 drain 线程
            _stderr_mode = (os.environ.get("TAKTON_KERNEL_HOST_STDERR") or "devnull").strip().lower()
            popen_kwargs: dict[str, Any] = {
                "stdout": subprocess.DEVNULL,
                "stderr": (
                    subprocess.PIPE
                    if _stderr_mode in ("pipe", "1", "true")
                    else subprocess.DEVNULL
                ),
            }
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            _host_proc = subprocess.Popen(cmd, **popen_kwargs)
            # pipe 模式：后台 drain，防管道塞死
            if popen_kwargs.get("stderr") is subprocess.PIPE and _host_proc.stderr:
                def _drain_stderr(proc: subprocess.Popen = _host_proc) -> None:
                    try:
                        for line in iter(proc.stderr.readline, b""):
                            if not line:
                                break
                            try:
                                logger.debug(
                                    "kernel-host: %s",
                                    line.decode("utf-8", errors="replace").rstrip()[:300],
                                )
                            except Exception:
                                pass
                    except Exception:
                        pass

                threading.Thread(
                    target=_drain_stderr, name="kernel-host-stderr", daemon=True
                ).start()
        except Exception as e:
            logger.error("failed to start kernel host (%s): %s", bin_path, e)
            # Another process may already own the port
            return bool(is_rust_host_available(host))
        for _ in range(80):
            if is_rust_host_available(host):
                logger.info(
                    "rust kernel host ready at %s (pid=%s bin=%s)",
                    host,
                    getattr(_host_proc, "pid", None),
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
                # Bind race: second instance exits with port-in-use, but first is fine
                if is_rust_host_available(host):
                    logger.info(
                        "kernel host already up after spawn race (stderr=%s)",
                        (err or "")[:120],
                    )
                    return True
                logger.error(
                    "kernel host exited early code=%s stderr=%s",
                    _host_proc.returncode,
                    err,
                )
                return False
            time.sleep(0.1)
        if is_rust_host_available(host):
            return True
        logger.error("kernel host did not become ready at %s within timeout", host)
        return False


class RustAgentKernel:
    """Drop-in stand-in for backend.kernel.kernel.AgentKernel backed by Rust host."""

    def __init__(self, host: str = DEFAULT_HOST, *, auto_start: bool = True) -> None:
        self._host = host
        self._rpc = _JsonRpcClient(host)
        # Serialize ALL RPC + recovery: concurrent close/reconnect was tearing down
        # the shared socket mid-rehydrate (CEO assign storm in logs).
        self._call_lock = threading.RLock()
        self.identity_registry: Any | None = None
        self._scheduler_proxy = _SchedulerProxy(self)
        if auto_start and not is_rust_host_available(host):
            if os.environ.get("TAKTON_KERNEL_AUTO_START", "1") not in ("0", "false", "False"):
                started = start_kernel_host(host)
                if not started and not is_rust_host_available(host):
                    raise ConnectionError(
                        f"takton-kernel-host unavailable at {host} "
                        "(binary missing or failed to start). "
                        "Build: cargo build -p takton-kernel-host --release"
                    )
        # H-14：host 已在线时少重试；刚拉起时稍多
        if is_rust_host_available(host):
            self._rpc.connect(attempts=5, connect_timeout=1.0)
        else:
            self._rpc.connect(attempts=8, connect_timeout=0.8)
        self._abi_checked = False
        self._restart_count = 0
        # Bumped on every host process restart — loops use this to rehydrate
        # process handles after in-memory process table wipe.
        self._host_epoch = 0
        self._last_health_ok_at = 0.0
        self._assert_abi_or_fail()
        self._configure_pkg_signing()

    def _assert_abi_or_fail(self) -> None:
        """Fail-closed if host lacks required ABI methods (half-run forbidden).

        Uses the agent socket path (not UI side-channel) so a transient UI
        timeout cannot report 0 methods and false-fail ABI.
        """
        from backend.kernel_rust.abi_gate import AbiMismatchError, assert_required_abi

        try:
            # Direct RPC — avoid list_methods() wrappers that may degrade to []
            raw = self._rpc.call("list_methods") if self._rpc._sock else None
            if raw is None:
                self._rpc.connect(attempts=3, connect_timeout=0.8)
                raw = self._rpc.call("list_methods")
            methods = list((raw or {}).get("methods") or []) if isinstance(raw, dict) else list(raw or [])
            assert_required_abi(methods)
            self._abi_checked = True
            logger.info(
                "kernel host ABI ok methods=%s required_gate=pass",
                len(methods),
            )
        except AbiMismatchError:
            self._rpc.close()
            raise
        except Exception as e:
            # list_methods itself failed — treat as host unusable
            self._rpc.close()
            raise ConnectionError(
                f"kernel host ABI check failed: {e}. "
                "Rebuild host binary and ensure it is the staged/current build."
            ) from e

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

    def _mark_host_wiped(self) -> None:
        """Host process table is gone — bump epoch so loops rehydrate process ids."""
        self._restart_count = int(getattr(self, "_restart_count", 0)) + 1
        self._host_epoch = int(getattr(self, "_host_epoch", 0)) + 1
        self._abi_checked = False

    def _soft_reconnect(self) -> None:
        """Re-open TCP only. Does not kill host / wipe process table."""
        self._rpc.close()
        time.sleep(0.05)
        self._rpc.connect()

    def _hard_recover_host(self, *, reason: str) -> bool:
        """Kill+restart host (wipes process table). Serialized + cooldown."""
        logger.warning(
            "kernel host hard-restart reason=%s epoch_before=%s",
            reason,
            getattr(self, "_host_epoch", 0),
        )
        ok = bool(restart_kernel_host(self._host))
        if ok:
            self._mark_host_wiped()
            try:
                self._rpc.close()
                self._rpc.connect()
                self._assert_abi_or_fail()
            except Exception as abi_e:
                logger.error("post hard-restart ABI gate failed: %s", abi_e)
                return False
        return ok

    def _host_responsive(self, timeout: float = 1.5) -> bool:
        """Liveness via short-lived UI socket (does not touch agent socket)."""
        if not is_rust_host_available(self._host):
            return False
        probe: _JsonRpcClient | None = None
        try:
            probe = _JsonRpcClient(self._host, rpc_timeout=timeout)
            probe.connect(attempts=2, connect_timeout=0.4)
            pong = probe.call("ping")
            return pong is not None
        except Exception as e:
            logger.debug("host responsive probe failed: %s", e)
            return False
        finally:
            if probe is not None:
                try:
                    probe.close()
                except Exception:
                    pass

    def _call_ui(
        self, method: str, params: dict[str, Any] | None = None
    ) -> Any:
        """Dashboard side-channel: own TCP socket + short timeout.

        Never shares the agent socket / single-worker queue, so a stuck mediate
        cannot freeze panel APIs (browser 500 via Next proxy hang-up).

        On failure: fail fast (caller degrades to empty). Do NOT hard-restart from
        UI path — concurrent panel polls would thrash-kill the host.
        """
        client = _JsonRpcClient(self._host, rpc_timeout=_UI_RPC_TIMEOUT)
        try:
            client.connect(attempts=2, connect_timeout=0.4)
            return client.call(method, params)
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _inject_rpc_auth(self, params: dict[str, Any] | None) -> dict[str, Any]:
        """若配置 TAKTON_KERNEL_RPC_SECRET，注入特权 RPC 鉴权。"""
        p = dict(params or {})
        secret = (os.environ.get("TAKTON_KERNEL_RPC_SECRET") or "").strip()
        if secret:
            p["_rpc_auth"] = secret
        return p

    def _call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """JSON-RPC with reconnect (sync). Prefer ``_acall`` from async code.

        UI/read methods use a side-channel socket. Agent methods run on the
        dedicated kernel-rpc thread (serial host socket).
        """
        # auth 在 _invoke_call / _call_ui 注入，避免 _acall 漏注
        if method in _UI_SIDECHANNEL_METHODS:
            params = self._inject_rpc_auth(params)
            try:
                if threading.current_thread().name.startswith("kernel-ui"):
                    return self._call_ui(method, params)
                fut = _UI_EXECUTOR.submit(self._call_ui, method, params)
                return fut.result(timeout=max(6.0, _UI_RPC_TIMEOUT * 3))
            except Exception as e:
                # Degraded empty for list-like UI methods so panels stay up.
                # Mark degraded so ABI health does not mislabel timeout as missing methods.
                logger.warning("kernel UI RPC %s failed: %s", method, e)
                if method == "list_methods":
                    return {
                        "methods": [],
                        "_degraded": True,
                        "error": str(e)[:200],
                    }
                if method == "list_processes":
                    return {"processes": [], "_degraded": True, "error": str(e)[:200]}
                if method == "list_escalations":
                    return {"escalations": [], "_degraded": True, "error": str(e)[:200]}
                if method == "health":
                    return {"ok": False, "error": str(e)[:200], "_degraded": True}
                if method == "ping":
                    return None
                raise

        if threading.current_thread().name.startswith("kernel-rpc"):
            with self._call_lock:
                return self._call_locked(method, self._inject_rpc_auth(params))
        # 超时只影响本 future；不再全局 _rpc_gen 作废队列里无关的 charge/end_process
        fut = _RPC_EXECUTOR.submit(self._invoke_call, method, params)
        try:
            return fut.result(timeout=_RPC_RESULT_TIMEOUT)
        except TimeoutError:
            logger.error(
                "kernel RPC timeout method=%s after %ss",
                method,
                _RPC_RESULT_TIMEOUT,
            )
            raise TimeoutError(
                f"kernel RPC timeout method={method} after {_RPC_RESULT_TIMEOUT}s"
            )

    def _invoke_call(
        self, method: str, params: dict[str, Any] | None = None
    ) -> Any:
        # 统一注入 auth（_call / _acall 都经此）
        params = self._inject_rpc_auth(params)
        with self._call_lock:
            return self._call_locked(method, params)

    async def _acall(
        self, method: str, params: dict[str, Any] | None = None
    ) -> Any:
        """Async RPC: does not block the uvicorn event loop (UI polls stay alive)."""
        # auth 由 _invoke_call / _call_ui 路径注入
        loop = asyncio.get_running_loop()
        if method in _UI_SIDECHANNEL_METHODS:
            params = self._inject_rpc_auth(params)
            return await loop.run_in_executor(
                _UI_EXECUTOR, self._call_ui, method, params
            )
        return await loop.run_in_executor(
            _RPC_EXECUTOR,
            self._invoke_call,
            method,
            params,
        )

    def _call_locked(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Single RPC under lock. Soft reconnect first; hard-restart only if host
        is proven stuck (port up but ping dead) after soft retries — never thrash.
        """
        soft_only = method in _SOFT_ONLY_METHODS
        try:
            return self._rpc.call(method, params)
        except (
            KernelPermissionError,
            BudgetExceededError,
            CapabilityEscalationError,
            ValueError,
        ):
            # 业务拒绝 / 参数错误：禁止当网络故障 reconnect（PermissionError⊂OSError）
            raise
        except (ConnectionError, OSError, BrokenPipeError, TimeoutError, socket.timeout) as e:
            last: BaseException = e
            max_attempts = 2 if soft_only else 3
            for attempt in range(1, max_attempts + 1):
                logger.warning(
                    "kernel RPC %s failed (%s); soft-reconnect %s/%s",
                    method,
                    last,
                    attempt,
                    max_attempts,
                )
                self._rpc.close()
                wiped = False
                try:
                    host_up = bool(is_rust_host_available(self._host))
                    if not host_up:
                        started = bool(start_kernel_host(self._host))
                        if started:
                            self._mark_host_wiped()
                            wiped = True
                            time.sleep(0.2)
                    elif (
                        not soft_only
                        and attempt >= max_attempts
                        and isinstance(last, (TimeoutError, socket.timeout))
                        and _HARD_RESTART_ENABLED
                    ):
                        # Host port open but agent RPC stuck (e.g. mediate hang):
                        # hard restart only after dead ping + cooldown/rate-limit.
                        # Exponential soft backoff first.
                        time.sleep(min(2.0, 0.35 * attempt))
                        # Prefer full ping (not just TCP) so accept-dead hosts recover
                        if not is_rust_host_responsive(
                            self._host, timeout=1.2
                        ) and not self._host_responsive(timeout=1.0):
                            wiped = self._hard_recover_host(
                                reason=f"stuck-timeout method={method}"
                            )
                except Exception as re:
                    logger.debug("host soft recover after RPC fail: %s", re)
                try:
                    if not wiped or self._rpc._sock is None:
                        self._soft_reconnect()
                    if wiped or not getattr(self, "_abi_checked", False):
                        try:
                            self._assert_abi_or_fail()
                        except Exception as abi_e:
                            logger.error("post-start ABI gate failed: %s", abi_e)
                            last = abi_e
                            continue
                    return self._rpc.call(method, params)
                except (
                    KernelPermissionError,
                    BudgetExceededError,
                    CapabilityEscalationError,
                    ValueError,
                ):
                    raise
                except (
                    ConnectionError,
                    OSError,
                    BrokenPipeError,
                    TimeoutError,
                    socket.timeout,
                ) as e2:
                    last = e2
                    time.sleep(0.2 * attempt)
                    continue
            raise last

    def ensure_and_mediate_sync(
        self,
        process_id: str | None,
        *,
        identity: str,
        capabilities: list[str] | None,
        token_budget: int | None,
        meta: dict[str, Any] | None,
        session_id: str | None,
        action: str,
        target: str,
        args: dict[str, Any] | None,
        intent: dict[str, Any] | None = None,
    ) -> tuple[RustKernelProcess, MediationDecision]:
        """Atomic get-or-create process + mediate under one lock.

        Closes the race: rehydrate create_process succeeds, then another RPC
        (or auto-restart) wipes host before mediate — CEO saw rotating process ids.
        """

        def body() -> tuple[RustKernelProcess, MediationDecision]:
            with self._call_lock:
                pid = (process_id or "").strip() or None
                proc_data: dict[str, Any] | None = None
                if pid:
                    try:
                        proc_data = self._call_locked(
                            "get_process", {"process_id": pid}
                        )
                        if not (isinstance(proc_data, dict) and proc_data.get("id")):
                            proc_data = None
                    except Exception:
                        proc_data = None
                if proc_data is None:
                    create_params: dict[str, Any] = {
                        "identity": identity,
                        "session_id": session_id,
                        "capabilities": capabilities,
                        "token_budget": token_budget,
                        "meta": meta or {},
                    }
                    if intent is not None:
                        create_params["intent"] = intent
                    proc_data = self._call_locked("create_process", create_params)
                    if not (isinstance(proc_data, dict) and proc_data.get("id")):
                        raise RuntimeError("create_process returned no process")
                    pid = str(proc_data["id"])
                    profile = str((meta or {}).get("coding_profile") or "")
                    if profile:
                        try:
                            self._call_locked(
                                "coding_profile_apply",
                                {"process_id": pid, "profile": profile},
                            )
                            refreshed = self._call_locked(
                                "get_process", {"process_id": pid}
                            )
                            if isinstance(refreshed, dict) and refreshed.get("id"):
                                proc_data = refreshed
                        except Exception as pe:
                            logger.debug("coding_profile_apply in ensure batch: %s", pe)
                assert pid is not None
                med = self._call_locked(
                    "mediate",
                    {
                        "process_id": pid,
                        "action": action,
                        "target": target,
                        "args": args or {},
                    },
                )
                decision = MediationDecision(
                    allowed=bool((med or {}).get("allowed")),
                    reason=str((med or {}).get("reason") or ""),
                    capability_checked=bool(
                        (med or {}).get("capability_checked")
                    ),
                )
                if not decision.allowed:
                    raise KernelPermissionError(
                        decision.reason or "mediate denied", decision
                    )
                p = self._proc(proc_data)
                assert p is not None
                return p, decision

        if threading.current_thread().name.startswith("kernel-rpc"):
            return body()
        fut = _RPC_EXECUTOR.submit(body)
        return fut.result(timeout=_RPC_RESULT_TIMEOUT)

    async def ensure_and_mediate(
        self,
        process_id: str | None,
        *,
        identity: str,
        capabilities: list[str] | None = None,
        token_budget: int | None = None,
        meta: dict[str, Any] | None = None,
        session_id: str | None = None,
        action: str = "tool_call",
        target: str,
        args: dict[str, Any] | None = None,
        intent: dict[str, Any] | None = None,
    ) -> tuple[RustKernelProcess, MediationDecision]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _RPC_EXECUTOR,
            lambda: self.ensure_and_mediate_sync(
                process_id,
                identity=identity,
                capabilities=capabilities,
                token_budget=token_budget,
                meta=meta,
                session_id=session_id,
                action=action,
                target=target,
                args=args,
                intent=intent,
            ),
        )

    def host_watchdog_ping(self) -> dict[str, Any]:
        """Lightweight liveness for long-run; restarts on timeout once."""
        t0 = time.time()
        try:
            pong = self._call("ping") or {}
            self._last_health_ok_at = time.time()
            return {
                "ok": True,
                "latency_ms": int((time.time() - t0) * 1000),
                "restart_count": int(getattr(self, "_restart_count", 0)),
                "pong": pong,
                "abi_checked": bool(getattr(self, "_abi_checked", False)),
            }
        except Exception as e:
            return {
                "ok": False,
                "error": str(e),
                "restart_count": int(getattr(self, "_restart_count", 0)),
                "latency_ms": int((time.time() - t0) * 1000),
            }

    def host_runtime_status(self) -> dict[str, Any]:
        """Aggregated host health for recovery UX / marathon gate."""
        from backend.kernel_rust.abi_gate import check_required_abi

        up = is_rust_host_available(self._host)
        methods: list[str] = []
        health: dict[str, Any] = {}
        abi: dict[str, Any] = {"ok": False, "missing": ["unreachable"]}
        degraded = False
        if up:
            try:
                # Prefer agent RPC path for ABI truth; UI channel may return [] on timeout.
                raw = self._invoke_call("list_methods") or {}
                if isinstance(raw, dict) and raw.get("_degraded"):
                    degraded = True
                methods = list(
                    (raw.get("methods") if isinstance(raw, dict) else None) or []
                )
                if not methods:
                    # Fallback UI once
                    try:
                        m2 = self.list_methods()
                        methods = list(m2 or [])
                    except Exception:
                        pass
                if not methods:
                    degraded = True
                    abi = {
                        "ok": False,
                        "degraded": True,
                        "error": "host unresponsive (list_methods empty/timeout)",
                        "missing": [],
                        "have": 0,
                        "required": 0,
                    }
                else:
                    abi = check_required_abi(methods)
                try:
                    health = self.health() or {}
                    if isinstance(health, dict) and health.get("_degraded"):
                        degraded = True
                except Exception as he:
                    health = {"error": str(he), "_degraded": True}
                    degraded = True
            except Exception as e:
                health = {"error": str(e), "_degraded": True}
                abi = {"ok": False, "degraded": True, "error": str(e), "missing": []}
                degraded = True
        return {
            "host": self._host,
            "up": up,
            "abi": abi,
            "health": health,
            "methods_count": len(methods),
            "degraded": degraded,
            "restart_count": int(getattr(self, "_restart_count", 0)),
            "host_epoch": int(getattr(self, "_host_epoch", 0) or 0),
            "last_health_ok_at": float(getattr(self, "_last_health_ok_at", 0) or 0),
            "acceptance": {
                "marathon_min_hours": 2,
                "abi_fail_closed": True,
                "auto_restart_on_timeout": True,
            },
        }

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
        result = await self._acall("create_process", params)
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

        tok = CapabilityToken.from_dict_safe(r.get("token") or {})
        if tok is None:
            # Host just issued — accept without sig field if present as caps list
            raw = r.get("token") or {}
            if isinstance(raw, dict) and raw.get("capabilities") is not None:
                tok = CapabilityToken.from_dict(raw, verify=False)
            else:
                from backend.kernel.capability import CapabilityToken as CT

                tok = CT(capabilities=frozenset(), process_id=process_id)
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
        # Defense: strip ConnectionManager / non-JSON before RPC (same as mediate).
        safe_args: dict[str, Any] = {}
        if isinstance(args, dict):
            try:
                from backend.kernel.tool_gate import sanitize_args_for_kernel

                safe_args = sanitize_args_for_kernel(args)
                if isinstance(args.get("_identity_capabilities"), (list, tuple)):
                    safe_args["_identity_capabilities"] = [
                        str(x) for x in args["_identity_capabilities"]
                    ]
                if args.get("_identity_id"):
                    safe_args["_identity_id"] = str(args["_identity_id"])
                if args.get("_workforce") is True:
                    safe_args["_workforce"] = True
            except Exception:
                for k, v in args.items():
                    try:
                        json.dumps(v, default=str)
                        safe_args[str(k)] = v
                    except Exception:
                        continue
        return (
            self._call(
                "decide_tool",
                {
                    "name": name,
                    "args": safe_args,
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

    def ipc_channel_subscribe(self, process_id: str, channel: str) -> dict[str, Any]:
        return (
            self._call(
                "ipc_channel_subscribe",
                {"process_id": process_id, "channel": channel},
            )
            or {}
        )

    def ipc_channel_publish(
        self,
        from_id: str,
        channel: str,
        kind: str = "message",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return (
            self._call(
                "ipc_channel_publish",
                {
                    "from": from_id,
                    "channel": channel,
                    "kind": kind,
                    "payload": payload or {},
                },
            )
            or {}
        )

    def ipc_broadcast(
        self,
        from_id: str,
        kind: str = "message",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return (
            self._call(
                "ipc_broadcast",
                {"from": from_id, "kind": kind, "payload": payload or {}},
            )
            or {}
        )

    def ipc_reply(
        self,
        from_id: str,
        to_id: str,
        reply_to: str,
        kind: str = "reply",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return (
            self._call(
                "ipc_reply",
                {
                    "from": from_id,
                    "to": to_id,
                    "reply_to": reply_to,
                    "kind": kind,
                    "payload": payload or {},
                },
            )
            or {}
        )

    def multi_agent_demo(self) -> dict[str, Any]:
        """M-01 productization: Rust-orchestrated two-agent ping-pong demo."""
        return self._call("multi_agent_demo") or {}

    def eval_record(
        self,
        suite: str = "default",
        overall: float = 0.0,
        parts: dict[str, float] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return (
            self._call(
                "eval_record",
                {
                    "suite": suite,
                    "overall": float(overall),
                    "parts": parts or {},
                    "meta": meta or {},
                },
            )
            or {}
        )

    def eval_trend(self, suite: str = "default", last_n: int = 8) -> dict[str, Any]:
        return self._call("eval_trend", {"suite": suite, "last_n": int(last_n)}) or {}

    def eval_gate_check(self, suite: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if suite:
            params["suite"] = suite
        return self._call("eval_gate_check", params) or {}

    def eval_status(self) -> dict[str, Any]:
        return self._call("eval_status") or {}

    def agent_manifest_validate(
        self, manifest: dict[str, Any] | None = None, *, json_str: str | None = None
    ) -> dict[str, Any]:
        if json_str is not None:
            return self._call("agent_manifest_validate", {"json": json_str}) or {}
        return self._call("agent_manifest_validate", {"manifest": manifest or {}}) or {}

    def agent_sdk_checklist(self) -> dict[str, Any]:
        return self._call("agent_sdk_checklist") or {}

    def skill_require_loadable(self, name: str) -> dict[str, Any]:
        return self._call("skill_require_loadable", {"name": name}) or {}

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

    def _is_workforce_process(self, process_id: str) -> bool:
        try:
            p = self.get_process(process_id)
            if p is None:
                return False
            ident = str(getattr(p, "identity", "") or "")
            meta = getattr(p, "meta", None) or {}
            if ident.startswith("wf:") or ident.startswith("workforce"):
                return True
            if isinstance(meta, dict) and meta.get("workforce"):
                return True
        except Exception:
            pass
        return False

    def _chat_elastic_top_up(
        self,
        process_id: str,
        *,
        need: int = 0,
        reason: str = "chat_elastic",
    ) -> dict[str, Any] | None:
        """CEO / 主会话弹性续航（不依赖 host --soft-renew）。

        编制工单 (wf:) 不走此路径——仍受 hard_cap_only / soft_renew 策略约束。
        hard_cap 默认 200 万；达顶后返回 None。
        """
        if self._is_workforce_process(process_id):
            return None
        try:
            from backend.core.config import settings as _st

            max_n = int(getattr(_st, "agent_chat_auto_top_up_max", 16) or 16)
            min_add = int(getattr(_st, "agent_chat_auto_top_up_min_add", 250_000) or 250_000)
            hard = int(
                getattr(_st, "agent_workforce_budget_hard_cap", 2_000_000) or 2_000_000
            )
            # 主会话可略高于编制 hard_cap（长 CEO 对话）
            chat_hard = int(
                getattr(_st, "agent_chat_budget_hard_cap", 0) or 0
            ) or max(hard, 5_000_000)
        except Exception:
            max_n, min_add, chat_hard = 16, 250_000, 5_000_000

        p = self.get_process(process_id)
        if p is None:
            return None
        if getattr(p, "token_budget", None) is None:
            return None
        meta = dict(getattr(p, "meta", None) or {})
        # P1：Rust ABI 无 process_set_meta → meta 写回静默失败；用进程侧计数兜底
        n_meta = int(meta.get("chat_auto_top_up_count") or meta.get("soft_renew_count") or 0)
        n_local = int(_CHAT_TOP_UP_COUNTS.get(str(process_id), 0) or 0)
        n = max(n_meta, n_local)
        if n >= max_n:
            logger.info(
                "chat elastic top_up cap reached proc=%s n=%s", process_id[:12], n
            )
            return None
        budget = int(p.token_budget or 0)
        used = int(getattr(p, "tokens_used", 0) or 0)
        remaining = max(0, budget - used)
        gap = max(0, int(need) - remaining)
        add = max(gap + 80_000, min_add, budget // 2 if budget > 0 else min_add)
        if chat_hard > 0 and budget + add > chat_hard:
            add = max(0, chat_hard - budget)
        if add <= 0:
            return None
        try:
            r = self.top_up_budget(
                process_id,
                int(add),
                by="system:chat_elastic",
                reason=f"{reason}#{n + 1}",
            )
        except Exception as e:
            logger.warning("chat elastic top_up failed proc=%s: %s", process_id[:12], e)
            return None
        # 权威计数：本地进程字典（跨 top_up 存活）+ 尽力写 host meta
        _CHAT_TOP_UP_COUNTS[str(process_id)] = n + 1
        try:
            fresh = self.get_process(process_id)
            if fresh is not None:
                m = dict(getattr(fresh, "meta", None) or {})
                m["chat_auto_top_up_count"] = n + 1
                m["soft_renew_count"] = max(int(m.get("soft_renew_count") or 0), n + 1)
                m["last_chat_elastic_at"] = time.time()
                try:
                    fresh.meta = m  # type: ignore[misc]
                except Exception:
                    pass
                if hasattr(self, "_call"):
                    for method in (
                        "process_set_meta",
                        "update_process_meta",
                        "process_patch_meta",
                        "set_process_meta",
                    ):
                        try:
                            self._call(
                                method,
                                {"process_id": process_id, "meta": m},
                            )
                            break
                        except Exception:
                            continue
        except Exception:
            pass
        logger.info(
            "chat elastic top_up proc=%s +%s need=%s n=%s budget→%s",
            process_id[:12],
            add,
            need,
            n + 1,
            (r or {}).get("token_budget"),
        )
        return {
            "ok": True,
            "amount": int(add),
            "renew_count": n + 1,
            "token_budget": (r or {}).get("token_budget"),
            "tokens_used": (r or {}).get("tokens_used"),
            "budget_remaining": (r or {}).get("budget_remaining"),
            "source": "chat_elastic",
        }

    def try_soft_renew_budget(
        self,
        process_id: str,
        *,
        need: int = 0,
        reason: str = "soft_renew",
    ) -> dict[str, Any] | None:
        # Host soft_renew 默认关（hard-budget first）；主会话走 chat_elastic 兜底
        r = self._call(
            "try_soft_renew_budget",
            {"process_id": process_id, "need": need, "reason": reason},
        )
        if r:
            return r
        return self._chat_elastic_top_up(
            process_id, need=need, reason=reason or "soft_renew"
        )

    def charge_tokens(self, process_id: str, amount: int) -> int | None:
        try:
            r = self._call(
                "charge_tokens", {"process_id": process_id, "amount": amount}
            ) or {}
            return r.get("remaining")
        except BudgetExceededError:
            # 撞墙前再弹性一次（CEO 长对话）
            renewed = self._chat_elastic_top_up(
                process_id, need=int(amount), reason="charge_overflow"
            )
            if not renewed:
                raise
            r = self._call(
                "charge_tokens", {"process_id": process_id, "amount": amount}
            ) or {}
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
        # Defense-in-depth: never ship live objects (e.g. ConnectionManager) over RPC.
        safe_args: dict[str, Any] = {}
        if isinstance(args, dict):
            try:
                from backend.kernel.tool_gate import sanitize_args_for_kernel

                safe_args = sanitize_args_for_kernel(args)
            except Exception:
                for k, v in args.items():
                    if str(k).startswith("_") and str(k) not in (
                        "_kernel_process_id",
                        "_session_id",
                    ):
                        continue
                    try:
                        json.dumps(v, default=str)
                        safe_args[str(k)] = v
                    except Exception:
                        safe_args[str(k)] = str(v)[:500]
        r = await self._acall(
            "mediate",
            {
                "process_id": process_id,
                "action": action,
                "target": target,
                "args": safe_args,
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

        tok = CapabilityToken.from_dict_safe(data if isinstance(data, dict) else None)
        if tok is not None:
            return tok
        # Fresh host issue may omit signature field in some builds — dev/host trust
        return CapabilityToken.from_dict(
            data if isinstance(data, dict) else {"capabilities": []},
            verify=False,
        )

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

    def resource_release(self, process_id: str, kind: str, amount: int = 1) -> dict[str, Any]:
        """Release concurrency lease (child_proc after command exits)."""
        return (
            self._call(
                "resource_release",
                {"process_id": process_id, "kind": kind, "amount": amount},
            )
            or {}
        )

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
