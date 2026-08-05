"""
PC 端手机扫码配对服务（对标 mobile pair 协议 v2/v3）。

URI: takton://pair?v=3&pair_id=…&code=…&host=…&port=8090&exp=…&mesh=auto&scheme=http&lan=…&ts=…&hn=…&tsk=…

- start：PC 出码（LAN + Tailscale 双路径）
- claim：手机扫码后无 JWT 调用（靠 pair_id + 一次性 code）
- 配对设备持久化到 ~/.takton/paired_devices.json
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import secrets
import socket
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urlencode

logger = logging.getLogger(__name__)

PAIR_TTL_SECS = 300
PAIRED_FILE = "paired_devices.json"
MESH_AUTH_FILE = "mesh_auth_key"
MESH_CFG_FILE = "mesh_config.json"


def _takton_dir() -> Path:
    override = os.environ.get("TAKTON_DATA_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".takton"


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("read %s failed: %s", path, e)
    return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def detect_lan_ipv4() -> Optional[str]:
    """Best-effort non-loopback IPv4 for QR lan= field."""
    candidates: list[str] = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                candidates.append(ip)
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.3)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if not ip.startswith("127."):
            candidates.insert(0, ip)
    except OSError:
        pass
    # Prefer private ranges
    for ip in candidates:
        try:
            addr = ipaddress.ip_address(ip)
            if addr.is_private and not addr.is_loopback:
                return ip
        except ValueError:
            continue
    return candidates[0] if candidates else None


def detect_tailscale_ipv4() -> Optional[str]:
    """Query system Tailscale / tailscaled for IPv4."""
    commands = [
        ["tailscale", "ip", "-4"],
        ["tailscale.exe", "ip", "-4"],
    ]
    for cmd in commands:
        try:
            out = subprocess.check_output(cmd, timeout=2, stderr=subprocess.DEVNULL)
            text = out.decode("utf-8", errors="ignore").strip()
            for line in text.splitlines():
                ip = line.strip()
                if re.match(r"^100\.\d+\.\d+\.\d+$", ip):
                    return ip
        except Exception:
            continue
    # Fallback: scan interfaces for CGNAT 100.64/10
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if re.match(r"^100\.\d+\.\d+\.\d+$", ip):
                return ip
    except OSError:
        pass
    return None


def detect_hostname() -> str:
    try:
        return socket.gethostname() or "takton-pc"
    except OSError:
        return "takton-pc"


def backend_port() -> int:
    raw = os.environ.get("TAKTON_APP_PORT") or os.environ.get("PORT") or "8090"
    try:
        return int(raw)
    except ValueError:
        return 8090


@dataclass
class PendingPair:
    pair_id: str
    code: str
    host: str
    port: int
    scheme: str
    mesh: str
    name: Optional[str]
    lan: Optional[str]
    ts: Optional[str]
    hn: Optional[str]
    exp: int
    created_at: int
    require_confirm: bool
    confirmed: bool
    claimed: bool = False


@dataclass
class PairedDevice:
    id: str
    name: str
    token: str
    host: str
    port: int
    scheme: str
    mesh: str
    base_url: str
    endpoints: list[str] = field(default_factory=list)
    lan: Optional[str] = None
    ts: Optional[str] = None
    hostname: Optional[str] = None
    paired_at: int = 0
    last_seen: int = 0
    role: str = "phone"


class MobilePairService:
    def __init__(self) -> None:
        self._pending: dict[str, PendingPair] = {}
        self._dir = _takton_dir()
        self._dir.mkdir(parents=True, exist_ok=True)

    # ── mesh config / auth key ────────────────────────────────────────────

    def mesh_config(self) -> dict[str, Any]:
        cfg = _read_json(self._dir / MESH_CFG_FILE, {})
        if not isinstance(cfg, dict):
            cfg = {}
        mode = str(cfg.get("mode") or "auto").lower()
        if mode not in ("off", "lan", "ts", "auto"):
            mode = "auto"
        return {
            "mode": mode,
            "require_pair_confirm": bool(cfg.get("require_pair_confirm", False)),
            "hostname": str(cfg.get("hostname") or detect_hostname()),
            "auth_key_set": self.auth_key_set(),
            "backend_port": backend_port(),
        }

    def set_mesh_config(
        self,
        *,
        mode: Optional[str] = None,
        require_pair_confirm: Optional[bool] = None,
        hostname: Optional[str] = None,
    ) -> dict[str, Any]:
        cfg = self.mesh_config()
        if mode is not None:
            m = mode.lower().strip()
            if m in ("off", "lan", "ts", "auto", "tailscale", "dual", "both"):
                if m in ("tailscale",):
                    m = "ts"
                if m in ("dual", "both"):
                    m = "auto"
                cfg["mode"] = m
        if require_pair_confirm is not None:
            cfg["require_pair_confirm"] = bool(require_pair_confirm)
        if hostname is not None and hostname.strip():
            cfg["hostname"] = hostname.strip()
        _write_json(self._dir / MESH_CFG_FILE, cfg)
        return self.mesh_status()

    def auth_key_set(self) -> bool:
        if os.environ.get("TS_AUTHKEY") or os.environ.get("TAKTON_TS_AUTHKEY"):
            return True
        path = self._dir / MESH_AUTH_FILE
        try:
            return path.exists() and bool(path.read_text(encoding="utf-8").strip())
        except OSError:
            return False

    def set_auth_key(self, key: str) -> dict[str, Any]:
        key = (key or "").strip()
        path = self._dir / MESH_AUTH_FILE
        if not key:
            if path.exists():
                path.unlink(missing_ok=True)
            return self.mesh_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(key, encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return self.mesh_status()

    def get_auth_key(self) -> Optional[str]:
        env = (os.environ.get("TS_AUTHKEY") or os.environ.get("TAKTON_TS_AUTHKEY") or "").strip()
        if env:
            return env
        path = self._dir / MESH_AUTH_FILE
        try:
            if path.exists():
                v = path.read_text(encoding="utf-8").strip()
                return v or None
        except OSError:
            pass
        return None

    def mesh_status(self) -> dict[str, Any]:
        cfg = self.mesh_config()
        lan = detect_lan_ipv4()
        ts = detect_tailscale_ipv4()
        detail = "准备就绪"
        if cfg["mode"] == "off":
            detail = "远程已关闭 · 仅本机"
        elif ts:
            detail = "局域网与外出通道均可用"
        elif lan:
            detail = "局域网可用 · 外出需 Tailscale 或一次访问密钥"
        else:
            detail = "未检测到可用网卡 IP"
        return {
            "ok": True,
            "mode": cfg["mode"],
            "require_pair_confirm": cfg["require_pair_confirm"],
            "hostname": cfg["hostname"],
            "lan_ip": lan,
            "tailscale_ip": ts,
            "auth_key_set": self.auth_key_set(),
            "backend_port": backend_port(),
            "bind_hint": (
                "后端需监听 0.0.0.0 才能被手机访问；Electron 已默认放宽绑定。"
                if True
                else None
            ),
            "detail": detail,
        }

    # ── pair lifecycle ────────────────────────────────────────────────────

    def _gc(self) -> None:
        now = int(time.time())
        dead = [k for k, p in self._pending.items() if p.exp < now or p.claimed]
        for k in dead:
            # keep claimed briefly? drop expired unclaimed
            p = self._pending.get(k)
            if p and p.exp < now:
                self._pending.pop(k, None)

    def start(
        self,
        *,
        mesh: Optional[str] = None,
        require_confirm: Optional[bool] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        name: Optional[str] = None,
    ) -> dict[str, Any]:
        self._gc()
        status = self.mesh_status()
        mode = (mesh or status["mode"] or "auto").lower()
        if mode in ("tailscale",):
            mode = "ts"
        if mode in ("dual", "both", "smart"):
            mode = "auto"
        if mode not in ("off", "lan", "ts", "auto"):
            mode = "auto"

        lan = status.get("lan_ip")
        ts = status.get("tailscale_ip")
        hn = status.get("hostname") or detect_hostname()
        scheme = "http"
        p = int(port or backend_port())

        # Primary host: LAN preferred, then TS, then hostname
        if host and host.strip() and host.strip() not in ("127.0.0.1", "localhost"):
            primary = host.strip()
        elif lan:
            primary = lan
        elif ts:
            primary = ts
        else:
            primary = hn

        req = (
            bool(require_confirm)
            if require_confirm is not None
            else bool(status.get("require_pair_confirm"))
        )

        # Seamless tsk: reuse auth key as phone join key when available (short window in QR)
        tsk = None
        if mode in ("auto", "ts") and self.auth_key_set():
            tsk = self.get_auth_key()

        pair_id = str(uuid.uuid4())
        code = f"{secrets.randbelow(1_000_000):06d}"
        now = int(time.time())
        exp = now + PAIR_TTL_SECS

        pending = PendingPair(
            pair_id=pair_id,
            code=code,
            host=primary,
            port=p,
            scheme=scheme,
            mesh=mode,
            name=name or hn,
            lan=lan,
            ts=ts if mode in ("auto", "ts") else None,
            hn=hn,
            exp=exp,
            created_at=now,
            require_confirm=req,
            confirmed=not req,
            claimed=False,
        )
        self._pending[pair_id] = pending

        payload = self._payload_dict(pending, tsk=tsk)
        qr = self._to_uri(payload)
        endpoints = self._endpoints(pending)

        seamless = bool(tsk)
        if seamless:
            hint = "用手机扫码即可连接 · 局域网与外出自动切换"
        elif ts:
            hint = "用手机扫码即可连接"
        else:
            hint = "用手机扫码连接（当前局域网）。外出使用请粘贴一次访问密钥。"

        public_payload = dict(payload)
        if public_payload.get("tsk"):
            public_payload["tsk"] = "***"

        return {
            "ok": True,
            "pair_id": pair_id,
            "code": code,
            "exp": exp,
            "ttl_secs": PAIR_TTL_SECS,
            "qr": qr,
            "payload": public_payload,
            "require_confirm": req,
            "mesh": mode,
            "base_url": f"{scheme}://{primary}:{p}",
            "endpoints": [{"url": u, "kind": k} for u, k in endpoints],
            "lan": lan,
            "ts": ts if mode in ("auto", "ts") else None,
            "seamless": seamless,
            "mesh_status": status,
            "hint": hint,
            "link": qr,
        }

    def status(self, pair_id: str) -> Optional[dict[str, Any]]:
        self._gc()
        p = self._pending.get(pair_id)
        if not p:
            return None
        return {
            "ok": True,
            "pair_id": p.pair_id,
            "exp": p.exp,
            "remaining_secs": max(0, p.exp - int(time.time())),
            "confirmed": p.confirmed,
            "claimed": p.claimed,
            "require_confirm": p.require_confirm,
            "mesh": p.mesh,
            "host": p.host,
            "port": p.port,
            "lan": p.lan,
            "ts": p.ts,
            "hn": p.hn,
            "ttl_secs": PAIR_TTL_SECS,
            "code": p.code if not p.claimed else None,
        }

    def confirm(self, pair_id: str) -> dict[str, Any]:
        p = self._pending.get(pair_id)
        if not p:
            return {"ok": False, "error": "配对会话不存在或已过期"}
        if p.exp < int(time.time()):
            return {"ok": False, "error": "配对码已过期"}
        p.confirmed = True
        return {"ok": True, "pair_id": pair_id, "confirmed": True}

    def cancel(self, pair_id: str) -> dict[str, Any]:
        self._pending.pop(pair_id, None)
        return {"ok": True, "cancelled": pair_id}

    def claim(self, pair_id: str, code: str, device_name: str = "Phone") -> dict[str, Any]:
        self._gc()
        p = self._pending.get(pair_id)
        if not p:
            return {"ok": False, "error": "配对会话不存在或已过期"}
        if p.exp < int(time.time()):
            self._pending.pop(pair_id, None)
            return {"ok": False, "error": "配对码已过期，请在 PC 重新生成"}
        if p.code != (code or "").strip():
            return {"ok": False, "error": "配对码不正确"}
        if p.require_confirm and not p.confirmed:
            return {"ok": False, "error": "等待 PC 确认允许此手机"}
        if p.claimed:
            return {"ok": False, "error": "配对码已使用"}

        token = secrets.token_urlsafe(32)
        now = int(time.time())
        endpoints = [u for u, _ in self._endpoints(p)]
        name = (device_name or "Phone").strip() or "Phone"
        device = PairedDevice(
            id=str(uuid.uuid4()),
            name=name,
            token=token,
            host=p.host,
            port=p.port,
            scheme=p.scheme,
            mesh=p.mesh,
            base_url=f"{p.scheme}://{p.host}:{p.port}",
            endpoints=endpoints,
            lan=p.lan,
            ts=p.ts,
            hostname=p.hn or p.name,
            paired_at=now,
            last_seen=now,
            role="phone",
        )
        p.claimed = True
        self._save_device(device)

        return {
            "ok": True,
            "device": asdict(device),
            "base_url": device.base_url,
            "mesh": p.mesh,
            "token": token,
            "endpoints": endpoints,
        }

    def list_devices(self) -> list[dict[str, Any]]:
        raw = _read_json(self._dir / PAIRED_FILE, [])
        if not isinstance(raw, list):
            return []
        return [d for d in raw if isinstance(d, dict)]

    def revoke(self, device_id: str) -> dict[str, Any]:
        devices = self.list_devices()
        next_list = [d for d in devices if d.get("id") != device_id]
        _write_json(self._dir / PAIRED_FILE, next_list)
        return {"ok": True, "revoked": device_id}

    def validate_token(self, token: str) -> Optional[dict[str, Any]]:
        if not token:
            return None
        for d in self.list_devices():
            if d.get("token") == token:
                return d
        return None

    def pending_snapshot(self) -> list[dict[str, Any]]:
        self._gc()
        out = []
        for p in self._pending.values():
            if not p.claimed:
                out.append(self.status(p.pair_id))
        return [x for x in out if x]

    def _save_device(self, device: PairedDevice) -> None:
        devices = self.list_devices()
        devices = [
            d
            for d in devices
            if not (d.get("role") == "phone" and d.get("base_url") == device.base_url)
        ]
        devices.append(asdict(device))
        _write_json(self._dir / PAIRED_FILE, devices)

    @staticmethod
    def _payload_dict(p: PendingPair, tsk: Optional[str] = None) -> dict[str, Any]:
        has_tsk = bool(tsk)
        d: dict[str, Any] = {
            "v": 3 if has_tsk else 2,
            "pair_id": p.pair_id,
            "code": p.code,
            "host": p.host,
            "port": p.port,
            "exp": p.exp,
            "mesh": p.mesh,
            "scheme": p.scheme,
        }
        if p.name:
            d["name"] = p.name
        if p.lan:
            d["lan"] = p.lan
        if p.ts:
            d["ts"] = p.ts
        if p.hn:
            d["hn"] = p.hn
        if tsk:
            d["tsk"] = tsk
        return d

    @staticmethod
    def _to_uri(payload: dict[str, Any]) -> str:
        q = (
            f"takton://pair?v={payload['v']}"
            f"&pair_id={quote(str(payload['pair_id']))}"
            f"&code={quote(str(payload['code']))}"
            f"&host={quote(str(payload['host']))}"
            f"&port={payload['port']}"
            f"&exp={payload['exp']}"
            f"&mesh={quote(str(payload['mesh']))}"
            f"&scheme={quote(str(payload['scheme']))}"
        )
        for key in ("name", "lan", "ts", "hn", "tsk"):
            val = payload.get(key)
            if val:
                q += f"&{key}={quote(str(val))}"
        return q

    @staticmethod
    def _endpoints(p: PendingPair) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        seen: set[str] = set()

        def add(host: Optional[str], kind: str) -> None:
            if not host:
                return
            url = f"{p.scheme}://{host}:{p.port}"
            if url not in seen:
                seen.add(url)
                out.append((url, kind))

        add(p.lan, "lan")
        add(p.hn, "host")
        add(p.ts, "ts")
        add(p.host, "manual")
        return out


# Process-wide singleton
_pair_service: Optional[MobilePairService] = None


def get_pair_service() -> MobilePairService:
    global _pair_service
    if _pair_service is None:
        _pair_service = MobilePairService()
    return _pair_service
