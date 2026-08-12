"""
PC 端手机扫码配对服务（对标 mobile pair 协议 v2/v3/v4）。

URI v4:
tevarn://pair?v=4&pair_id=…&code=…&host=…&port=…&exp=…&mesh=auto
  &scheme=http|https&lan=…&ts=…&hn=…&tsk=…
  &vps=…&vp=443&vps_path=/t/{id}&vpt=…

- start：PC 出码（LAN + VPS + Tailscale）
- claim：手机扫码后无 JWT 调用（靠 pair_id + 一次性 code）
- 配对设备持久化到 ~/.tevarn/paired_devices.json
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
from urllib.parse import quote

logger = logging.getLogger(__name__)

PAIR_TTL_SECS = 300
PAIRED_FILE = "paired_devices.json"
MESH_AUTH_FILE = "mesh_auth_key"
MESH_CFG_FILE = "mesh_config.json"


def _tevarn_dir() -> Path:
    override = os.environ.get("TEVARN_DATA_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".tevarn"


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


def _is_usable_lan_ipv4(ip: str) -> bool:
    """Skip loopback / link-local / Docker-ish bridges that phones cannot reach."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if not isinstance(addr, ipaddress.IPv4Address):
        return False
    if addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified:
        return False
    # Docker default bridges: 172.17.0.0/16, compose often 172.18–172.31
    # Prefer real home/office LAN (10/8, 192.168/16, or non-docker 172.16/12).
    o = int(addr)
    # 172.16.0.0 – 172.31.255.255 is "private" but commonly Docker on dev PCs
    if 0xAC100000 <= o <= 0xAC1FFFFF:  # 172.16/12
        # Allow only if it looks like classic site LAN (rare); skip docker-ish .0.1 gateways
        parts = str(addr).split(".")
        if parts[1] in ("17", "18", "19", "20") or parts[3] == "1" and parts[2] == "0":
            return False
    # Tailscale CGNAT should not be advertised as LAN
    if 0x64400000 <= o <= 0x647FFFFF:  # 100.64/10
        return False
    return addr.is_private


def detect_lan_ipv4() -> Optional[str]:
    """Best-effort phone-reachable LAN IPv4 for QR lan= field."""
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
    # Prefer 192.168/16 then 10/8 then other usable private
    scored: list[tuple[int, str]] = []
    for ip in candidates:
        if not _is_usable_lan_ipv4(ip):
            continue
        if ip.startswith("192.168."):
            scored.append((0, ip))
        elif ip.startswith("10."):
            scored.append((1, ip))
        else:
            scored.append((2, ip))
    if scored:
        scored.sort(key=lambda x: (x[0], x[1]))
        return scored[0][1]
    return None


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
        return socket.gethostname() or "tevarn-pc"
    except OSError:
        return "tevarn-pc"


def backend_port() -> int:
    raw = os.environ.get("TEVARN_APP_PORT") or os.environ.get("PORT") or "8090"
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
    vps: Optional[str] = None
    vp: Optional[int] = None
    vps_path: Optional[str] = None
    vpt: Optional[str] = None
    vps_scheme: Optional[str] = None


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
    vps: Optional[str] = None
    hostname: Optional[str] = None
    paired_at: int = 0
    last_seen: int = 0
    role: str = "phone"


class MobilePairService:
    def __init__(self) -> None:
        self._pending: dict[str, PendingPair] = {}
        self._dir = _tevarn_dir()
        self._dir.mkdir(parents=True, exist_ok=True)

    # ── mesh config / auth key ────────────────────────────────────────────

    def mesh_config(self) -> dict[str, Any]:
        cfg = _read_json(self._dir / MESH_CFG_FILE, {})
        if not isinstance(cfg, dict):
            cfg = {}
        mode = str(cfg.get("mode") or "auto").lower()
        if mode not in ("off", "lan", "ts", "vps", "auto"):
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
            if m in ("off", "lan", "ts", "vps", "auto", "tailscale", "dual", "both", "relay"):
                if m in ("tailscale",):
                    m = "ts"
                if m in ("relay",):
                    m = "vps"
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
        if os.environ.get("TS_AUTHKEY") or os.environ.get("TEVARN_TS_AUTHKEY"):
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
        env = (os.environ.get("TS_AUTHKEY") or os.environ.get("TEVARN_TS_AUTHKEY") or "").strip()
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
        from backend.services import vps_relay as vps_mod
        from backend.services.vps_tunnel import get_vps_tunnel

        cfg = self.mesh_config()
        lan = detect_lan_ipv4()
        ts = detect_tailscale_ipv4()
        tun = get_vps_tunnel().status()
        vps = vps_mod.public_status(
            online=bool(tun.get("online")),
            detail=tun.get("last_error") or "",
        )
        if tun.get("online") and vps.get("enabled"):
            vps["detail"] = "隧道在线"
            if tun.get("latency_ms") is not None:
                vps["latency_ms"] = tun["latency_ms"]
                vps["detail"] = f"隧道在线 · {tun['latency_ms']}ms"
        elif vps.get("enabled") and vps.get("configured"):
            vps["detail"] = tun.get("last_error") or "连接中…"
        detail = "准备就绪"
        if cfg["mode"] == "off":
            detail = "远程已关闭 · 仅本机"
        elif vps.get("online"):
            detail = "局域网与 VPS 中继可用" + (" · 含 Tailscale" if ts else "")
        elif ts:
            detail = "局域网与外出通道均可用"
        elif lan:
            detail = "局域网可用 · 外出可配 VPS 中继或 Tailscale"
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
            "vps": vps,
            "install_hint": vps_mod.INSTALL_HINT_ZH,
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
        if mode in ("relay",):
            mode = "vps"
        if mode in ("dual", "both", "smart"):
            mode = "auto"
        if mode not in ("off", "lan", "ts", "vps", "auto"):
            mode = "auto"

        lan = status.get("lan_ip")
        ts = status.get("tailscale_ip")
        hn = status.get("hostname") or detect_hostname()
        scheme = "http"
        p = int(port or backend_port())

        # VPS fields
        from backend.services import vps_relay as vps_mod

        vps_info = status.get("vps") or {}
        vps_host = None
        vps_port = None
        vps_path = None
        vps_scheme = None
        vpt = None
        include_vps = mode in ("auto", "vps") and bool(
            vps_info.get("enabled") and vps_info.get("configured") and vps_info.get("host")
        )
        if include_vps:
            vcfg = vps_mod.load_config()
            vps_host = vcfg.get("host")
            vps_port = int(vcfg.get("port") or 80)
            vps_scheme = vcfg.get("scheme") or "http"
            tid = vcfg.get("tunnel_id") or ""
            vps_path = f"/t/{tid}" if tid else ""
            # HMAC short ticket (relay verifies with RELAY_TOKEN / master_token)
            master = str(vcfg.get("master_token") or "").strip()
            if master and tid:
                try:
                    from backend.services.vps_relay import mint_vpt

                    vpt = mint_vpt(master, tid, ttl_secs=PAIR_TTL_SECS)
                except Exception:
                    vpt = secrets.token_urlsafe(18)
            else:
                vpt = secrets.token_urlsafe(18)

        # Primary host: LAN preferred, then VPS, then TS, then hostname
        if host and host.strip() and host.strip() not in ("127.0.0.1", "localhost"):
            primary = host.strip()
            primary_port = p
            primary_scheme = scheme
        elif lan and mode != "vps":
            primary = lan
            primary_port = p
            primary_scheme = scheme
        elif include_vps and vps_host:
            primary = vps_host
            primary_port = vps_port or 80
            primary_scheme = vps_scheme or "http"
        elif ts and mode in ("auto", "ts"):
            primary = ts
            primary_port = p
            primary_scheme = scheme
        else:
            primary = hn
            primary_port = p
            primary_scheme = scheme

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
            port=primary_port,
            scheme=primary_scheme,
            mesh=mode,
            name=name or hn,
            lan=lan if mode in ("auto", "lan", "vps", "ts") else None,
            ts=ts if mode in ("auto", "ts") else None,
            hn=hn,
            exp=exp,
            created_at=now,
            require_confirm=req,
            confirmed=not req,
            claimed=False,
            vps=vps_host if include_vps else None,
            vp=vps_port if include_vps else None,
            vps_path=vps_path if include_vps else None,
            vpt=vpt if include_vps else None,
            vps_scheme=vps_scheme if include_vps else None,
        )
        self._pending[pair_id] = pending

        payload = self._payload_dict(pending, tsk=tsk)
        qr = self._to_uri(payload)
        endpoints = self._endpoints(pending)

        seamless = bool(tsk) or bool(include_vps and vps_info.get("online"))
        if include_vps and vps_info.get("online"):
            hint = "用手机扫码即可连接 · 局域网与 VPS 外出自动切换"
        elif seamless:
            hint = "用手机扫码即可连接 · 局域网与外出自动切换"
        elif include_vps:
            hint = "二维码已含 VPS 端点，但隧道未在线 — 请先在「自有 VPS」启用中继"
        elif ts:
            hint = "用手机扫码即可连接"
        else:
            hint = "用手机扫码连接（当前局域网）。外出请配置 VPS 中继或 Tailscale。"

        public_payload = dict(payload)
        if public_payload.get("tsk"):
            public_payload["tsk"] = "***"
        if public_payload.get("vpt"):
            public_payload["vpt"] = "***"

        base = f"{primary_scheme}://{primary}:{primary_port}"
        if include_vps and vps_path and primary == vps_host:
            base = f"{primary_scheme}://{primary}:{primary_port}{vps_path}"

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
            "base_url": base,
            "endpoints": [{"url": u, "kind": k} for u, k in endpoints],
            "lan": lan,
            "ts": ts if mode in ("auto", "ts") else None,
            "vps": vps_host if include_vps else None,
            "vp": vps_port if include_vps else None,
            "vps_path": vps_path if include_vps else None,
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
            "vps": p.vps,
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
        # Prefer VPS base when present — backend often binds 127.0.0.1 so LAN
        # in the QR is unreachable from the phone. Returning LAN-only base_url
        # caused phones to "register then fail login" on cellular / loopback-bind.
        best_base = f"{p.scheme}://{p.host}:{p.port}"
        for u in endpoints:
            if "/t/" in u:
                best_base = u
                break
        device = PairedDevice(
            id=str(uuid.uuid4()),
            name=name,
            token=token,
            host=p.host,
            port=p.port,
            scheme=p.scheme,
            mesh=p.mesh,
            base_url=best_base,
            endpoints=endpoints,
            lan=p.lan,
            ts=p.ts,
            vps=p.vps,
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
            "base_url": best_base,
            "mesh": p.mesh,
            "token": token,
            "endpoints": endpoints,
        }

    def touch_device_token(self, token: str) -> None:
        """Update last_seen for a paired device token (best-effort)."""
        if not token:
            return
        devices = self.list_devices()
        now = int(time.time())
        changed = False
        for d in devices:
            if d.get("token") == token:
                d["last_seen"] = now
                changed = True
                break
        if changed:
            _write_json(self._dir / PAIRED_FILE, devices)

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
        has_vps = bool(p.vps)
        version = 4 if has_vps else (3 if has_tsk else 2)
        # Primary scheme/port for host field; VPS uses vp/vps_scheme separately
        d: dict[str, Any] = {
            "v": version,
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
        if p.vps:
            d["vps"] = p.vps
            if p.vp:
                d["vp"] = p.vp
            if p.vps_path:
                d["vps_path"] = p.vps_path
            if p.vpt:
                d["vpt"] = p.vpt
            if p.vps_scheme:
                d["vps_scheme"] = p.vps_scheme
        return d

    @staticmethod
    def _to_uri(payload: dict[str, Any]) -> str:
        q = (
            f"tevarn://pair?v={payload['v']}"
            f"&pair_id={quote(str(payload['pair_id']))}"
            f"&code={quote(str(payload['code']))}"
            f"&host={quote(str(payload['host']))}"
            f"&port={payload['port']}"
            f"&exp={payload['exp']}"
            f"&mesh={quote(str(payload['mesh']))}"
            f"&scheme={quote(str(payload['scheme']))}"
        )
        for key in ("name", "lan", "ts", "hn", "tsk", "vps", "vps_path", "vpt", "vps_scheme"):
            val = payload.get(key)
            if val:
                q += f"&{key}={quote(str(val))}"
        if payload.get("vp"):
            q += f"&vp={int(payload['vp'])}"
        return q

    @staticmethod
    def _endpoints(p: PendingPair) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        seen: set[str] = set()

        def add(url: Optional[str], kind: str) -> None:
            if not url:
                return
            if url not in seen:
                seen.add(url)
                out.append((url, kind))

        def add_host(host: Optional[str], kind: str, port: Optional[int] = None, scheme: Optional[str] = None) -> None:
            if not host:
                return
            sch = scheme or p.scheme or "http"
            prt = int(port or p.port or 8090)
            add(f"{sch}://{host}:{prt}", kind)

        add_host(p.lan, "lan", backend_port(), "http")
        add_host(p.hn, "host", backend_port(), "http")
        if p.vps:
            sch = p.vps_scheme or "http"
            prt = int(p.vp or 80)
            path = (p.vps_path or "").rstrip("/")
            if path:
                add(f"{sch}://{p.vps}:{prt}{path}", "vps")
            else:
                add(f"{sch}://{p.vps}:{prt}", "vps")
        add_host(p.ts, "ts", backend_port(), "http")
        add_host(p.host, "manual", p.port, p.scheme)
        return out


# Process-wide singleton
_pair_service: Optional[MobilePairService] = None


def get_pair_service() -> MobilePairService:
    global _pair_service
    if _pair_service is None:
        _pair_service = MobilePairService()
    return _pair_service
