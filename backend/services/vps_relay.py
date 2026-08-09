"""
PC-side VPS relay configuration + status.

Storage: ~/.tevarn/vps_relay.json (0600)
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

VPS_CFG_FILE = "vps_relay.json"


def _tevarn_dir() -> Path:
    override = os.environ.get("TEVARN_DATA_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".tevarn"


def _cfg_path() -> Path:
    return _tevarn_dir() / VPS_CFG_FILE


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


def default_config() -> dict[str, Any]:
    return {
        "enabled": False,
        "host": "",
        "port": 80,
        "scheme": "http",
        "master_token": "",
        "tunnel_id": "",
        "updated_at": 0,
    }


def load_config() -> dict[str, Any]:
    raw = _read_json(_cfg_path(), {})
    if not isinstance(raw, dict):
        raw = {}
    cfg = default_config()
    cfg.update({k: raw[k] for k in cfg if k in raw})
    # normalize
    cfg["enabled"] = bool(cfg.get("enabled"))
    try:
        cfg["port"] = int(cfg.get("port") or 80)
    except (TypeError, ValueError):
        cfg["port"] = 80
    scheme = str(cfg.get("scheme") or "http").lower().strip()
    if scheme not in ("http", "https"):
        scheme = "https" if cfg["port"] == 443 else "http"
    cfg["scheme"] = scheme
    cfg["host"] = str(cfg.get("host") or "").strip()
    cfg["master_token"] = str(cfg.get("master_token") or "").strip()
    cfg["tunnel_id"] = str(cfg.get("tunnel_id") or "").strip()
    return cfg


def save_config(cfg: dict[str, Any]) -> dict[str, Any]:
    base = default_config()
    base.update(cfg)
    base["updated_at"] = int(time.time())
    if not base.get("tunnel_id"):
        base["tunnel_id"] = f"pc-{secrets.token_hex(8)}"
    _write_json(_cfg_path(), base)
    return load_config()


def mint_vpt(
    master_token: str,
    tunnel_id: str,
    *,
    ttl_secs: int = 300,
) -> str:
    """Mint short-lived edge ticket for /t/{id} (HMAC over tunnel_id:exp).

    Format: ``{exp}.{sig16hex}`` — VPS verifies with same RELAY_TOKEN without state.
    """
    import hashlib
    import hmac as hmac_mod

    exp = int(time.time()) + max(60, int(ttl_secs))
    msg = f"{tunnel_id}:{exp}".encode()
    sig = hmac_mod.new(
        (master_token or "").encode(),
        msg,
        hashlib.sha256,
    ).hexdigest()[:32]
    return f"{exp}.{sig}"


def verify_vpt(master_token: str, tunnel_id: str, vpt: str | None) -> bool:
    """Verify mint_vpt ticket (used by relay server — duplicated logic for tests)."""
    import hashlib
    import hmac as hmac_mod

    if not vpt or not master_token or not tunnel_id:
        return False
    try:
        exp_s, sig = vpt.strip().split(".", 1)
        exp = int(exp_s)
    except Exception:
        return False
    # 30s clock skew
    if time.time() > exp + 30:
        return False
    msg = f"{tunnel_id}:{exp}".encode()
    expect = hmac_mod.new(
        master_token.encode(),
        msg,
        hashlib.sha256,
    ).hexdigest()[:32]
    return secrets.compare_digest(sig, expect)


def public_base_url(cfg: Optional[dict[str, Any]] = None) -> Optional[str]:
    """Base URL phones use (with tunnel path)."""
    cfg = cfg or load_config()
    host = cfg.get("host") or ""
    if not host:
        return None
    scheme = cfg.get("scheme") or "http"
    port = int(cfg.get("port") or 80)
    tunnel_id = cfg.get("tunnel_id") or ""
    # omit default ports
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        authority = host
    else:
        authority = f"{host}:{port}"
    if tunnel_id:
        return f"{scheme}://{authority}/t/{tunnel_id}"
    return f"{scheme}://{authority}"


def relay_origin(cfg: Optional[dict[str, Any]] = None) -> Optional[str]:
    """Origin for control-plane calls (no tunnel path)."""
    cfg = cfg or load_config()
    host = cfg.get("host") or ""
    if not host:
        return None
    scheme = cfg.get("scheme") or "http"
    port = int(cfg.get("port") or 80)
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def ws_tunnel_url(cfg: Optional[dict[str, Any]] = None) -> Optional[str]:
    cfg = cfg or load_config()
    origin = relay_origin(cfg)
    if not origin:
        return None
    tunnel_id = cfg.get("tunnel_id") or ""
    token = cfg.get("master_token") or ""
    if origin.startswith("https://"):
        ws = "wss://" + origin[len("https://") :]
    else:
        ws = "ws://" + origin[len("http://") :]
    from urllib.parse import quote

    return (
        f"{ws}/relay/v1/tunnel"
        f"?tunnel_id={quote(tunnel_id)}"
        f"&token={quote(token)}"
        f"&pc_name={quote(os.environ.get('COMPUTERNAME') or os.environ.get('HOSTNAME') or 'tevarn-pc')}"
    )


async def test_relay(cfg: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Probe /relay/v1/health and optional register dry-run."""
    cfg = cfg or load_config()
    origin = relay_origin(cfg)
    if not origin:
        return {"ok": False, "error": "请填写 VPS 地址", "latency_ms": None}
    if not cfg.get("master_token"):
        return {"ok": False, "error": "请填写访问令牌", "latency_ms": None}

    health_url = f"{origin.rstrip('/')}/relay/v1/health"
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=8.0, verify=False) as client:
            r = await client.get(health_url)
            latency = round((time.perf_counter() - t0) * 1000)
            if r.status_code != 200:
                return {
                    "ok": False,
                    "error": f"中继不可达 HTTP {r.status_code}",
                    "latency_ms": latency,
                    "health_url": health_url,
                }
            try:
                body = r.json()
            except Exception:
                body = {}
            # register dry-run
            reg = await client.post(
                f"{origin.rstrip('/')}/relay/v1/register",
                headers={"Authorization": f"Bearer {cfg['master_token']}"},
                json={
                    "tunnel_id": cfg.get("tunnel_id") or f"pc-{secrets.token_hex(6)}",
                    "pc_name": "probe",
                },
            )
            if reg.status_code == 401:
                return {
                    "ok": False,
                    "error": "令牌无效（401）",
                    "latency_ms": latency,
                    "health": body,
                }
            if reg.status_code >= 400:
                return {
                    "ok": False,
                    "error": f"登记失败 HTTP {reg.status_code}",
                    "latency_ms": latency,
                    "health": body,
                }
            reg_body = {}
            try:
                reg_body = reg.json()
            except Exception:
                pass
            return {
                "ok": True,
                "latency_ms": latency,
                "health": body,
                "register": reg_body,
                "public_base": public_base_url(cfg),
                "detail": f"中继可达 · {latency}ms",
            }
    except httpx.ConnectError:
        return {
            "ok": False,
            "error": "中继不可达，请检查域名/防火墙/安全组是否放行端口",
            "latency_ms": None,
            "health_url": health_url,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "latency_ms": None}


def set_vps_config(
    *,
    host: Optional[str] = None,
    port: Optional[int] = None,
    token: Optional[str] = None,
    enabled: Optional[bool] = None,
    scheme: Optional[str] = None,
    tunnel_id: Optional[str] = None,
) -> dict[str, Any]:
    cfg = load_config()
    if host is not None:
        h = host.strip()
        # allow user paste full URL
        if "://" in h:
            from urllib.parse import urlparse

            u = urlparse(h)
            cfg["scheme"] = u.scheme or cfg["scheme"]
            cfg["host"] = u.hostname or ""
            if u.port:
                cfg["port"] = u.port
        else:
            # host:port
            if h.count(":") == 1 and not h.startswith("["):
                hh, pp = h.rsplit(":", 1)
                if pp.isdigit():
                    cfg["host"] = hh.strip()
                    cfg["port"] = int(pp)
                else:
                    cfg["host"] = h
            else:
                cfg["host"] = h
    if port is not None:
        cfg["port"] = int(port)
    if token is not None:
        cfg["master_token"] = token.strip()
    if scheme is not None and scheme.strip():
        cfg["scheme"] = scheme.strip().lower()
    if tunnel_id is not None and tunnel_id.strip():
        cfg["tunnel_id"] = tunnel_id.strip()
    if enabled is not None:
        cfg["enabled"] = bool(enabled)
    # auto scheme by port if still default-ish
    if cfg["port"] == 443 and cfg.get("scheme") == "http" and scheme is None:
        cfg["scheme"] = "https"
    return save_config(cfg)


def public_status(online: bool = False, detail: str = "") -> dict[str, Any]:
    cfg = load_config()
    configured = bool(cfg.get("host") and cfg.get("master_token"))
    return {
        "configured": configured,
        "enabled": bool(cfg.get("enabled")),
        "online": bool(online) if configured and cfg.get("enabled") else False,
        "host": cfg.get("host") or None,
        "port": cfg.get("port"),
        "scheme": cfg.get("scheme"),
        "tunnel_id": cfg.get("tunnel_id") or None,
        "public_base": public_base_url(cfg) if configured else None,
        "has_token": bool(cfg.get("master_token")),
        "detail": detail
        or (
            "隧道在线"
            if online and cfg.get("enabled")
            else (
                "已配置 · 未启用"
                if configured and not cfg.get("enabled")
                else ("未配置" if not configured else "连接中…")
            )
        ),
    }


# Install command shown in UI (user copies to VPS)
INSTALL_HINT_ZH = """# 在 Ubuntu 22.04+ VPS 上执行（需 root）
# 1) 把 tevarn 仓库里的 deploy/vps-relay 拷到 VPS，或 git clone 后：
cd deploy/vps-relay
sudo bash install.sh

# 安装结束会打印 Host + Token，粘贴回本页即可
# 云安全组请放行 TCP 80
"""

INSTALL_HINT_EN = """# On Ubuntu 22.04+ VPS (root):
cd deploy/vps-relay
sudo bash install.sh

# Paste the printed Host + Token back here.
# Open TCP 80 in your cloud security group.
"""
