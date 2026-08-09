"""本地包市场产品层（债 #3）：zip 安装 + Kernel 签名扫描 + catalog / promote。

与 ``publisher.py``（文件系统 zip）和 Rust ``package_mgr``（签名/隔离）打通：
- 安装 zip 后把清单/正文镜像进 kernel ``pkg_install``（扫描 + 签名）
- 市场目录 = 工作区包列表 ∪ kernel catalog
- 隔离包不可 activate，须 ``pkg_promote`` 出 quarantine
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from backend.packages.publisher import (
    InstallResult,
    install_package_zip,
    install_root,
    uninstall_package,
)

logger = logging.getLogger(__name__)


def _kernel():
    try:
        from backend.kernel import get_kernel

        return get_kernel()
    except Exception:
        return None


def _call(k: Any, method: str, params: dict | None = None) -> Any:
    if k is None:
        return None
    try:
        if hasattr(k, "_call"):
            return k._call(method, params or {})
        fn = getattr(k, method, None)
        if callable(fn):
            return fn(**(params or {})) if params else fn()
    except Exception as e:
        logger.debug("market kernel %s: %s", method, e)
        return {"error": str(e)}
    return None


def security_scan_content(
    content: str, permissions: list[str] | None = None
) -> dict[str, Any]:
    """预检：优先 Rust pkg_scan，否则本地启发式。"""
    k = _kernel()
    r = _call(k, "pkg_scan", {"content": content, "permissions": permissions or []})
    if isinstance(r, dict) and "scan" in r:
        return r
    # fallback local
    findings = []
    lower = content.lower()
    for pat in ("api_key", "sk-", "secret", "password=", "private_key"):
        if pat in lower:
            findings.append({"severity": "high", "rule": pat})
    if "auto_apply" in lower and "true" in lower:
        findings.append({"severity": "high", "rule": "auto_apply_true"})
    ok = not any(f.get("severity") == "high" for f in findings)
    return {
        "scan": {"ok": ok, "findings": findings},
        "content_hash": None,
        "size": len(content),
        "backend": "python_fallback",
    }


def _read_package_body(pkg_dir: Path) -> tuple[str, str, list[str]]:
    """读版本/正文/权限（尽力）。"""
    version = "0.0.0"
    perms: list[str] = []
    body_parts: list[str] = []
    for name in ("SYSTEM.md", "SKILL.md", "README.md", "main.py", "skill.py"):
        p = pkg_dir / name
        if p.is_file():
            try:
                body_parts.append(p.read_text(encoding="utf-8", errors="replace")[:200_000])
            except OSError:
                pass
    for mf in ("tevarn.package.json", "package.json"):
        p = pkg_dir / mf
        if p.is_file():
            try:
                import json

                data = json.loads(p.read_text(encoding="utf-8"))
                version = str(data.get("version") or version)
                caps = data.get("permissions") or data.get("capabilities") or []
                if isinstance(caps, list):
                    perms = [str(c) for c in caps]
            except Exception:
                pass
    content = "\n\n".join(body_parts) if body_parts else f"# package {pkg_dir.name}\n"
    return version, content, perms


def mirror_to_kernel(
    name: str,
    version: str,
    content: str,
    permissions: list[str] | None = None,
) -> dict[str, Any]:
    """把已落盘包镜像进 kernel package_mgr（签名 + 扫描）。"""
    k = _kernel()
    if k is None:
        return {"ok": False, "error": "kernel unavailable"}
    perms = permissions or []
    sig_r = _call(k, "pkg_sign", {"content": content}) or {}
    sig = (sig_r or {}).get("signature") if isinstance(sig_r, dict) else None
    installed = _call(
        k,
        "pkg_install",
        {
            "name": name,
            "version": version,
            "content": content,
            "permissions": perms,
            "dependencies": [],
            "signature": sig,
        },
    )
    if not isinstance(installed, dict):
        return {"ok": False, "error": "pkg_install failed", "raw": installed}
    if installed.get("error"):
        return {"ok": False, **installed}
    return {
        "ok": True,
        "kernel_status": installed.get("status"),
        "security": installed.get("security"),
        "id": installed.get("id"),
        "package": installed,
    }


def install_zip_market(
    data: bytes, *, overwrite: bool = False, mirror: bool = True
) -> dict[str, Any]:
    """产品化安装：文件系统 zip + Kernel 扫描镜像。

    高危内容（本地启发式 / kernel scan high）默认 **阻断安装并回滚落盘**；
    仅当设置 ``agent_package_allow_quarantine_install=true`` 时允许 quarantine 留存。
    """
    result: InstallResult = install_package_zip(data, overwrite=overwrite)
    out: dict[str, Any] = result.model_dump()
    out["kernel"] = None
    if not result.ok:
        return out

    dest = Path(result.path)
    version, content, perms = _read_package_body(dest)
    if result.version:
        version = result.version
    # 契约声明的 tools 也可作 permissions 线索
    if result.contract and isinstance(result.contract, dict):
        tools = result.contract.get("tools") or []
        if isinstance(tools, list) and tools and not perms:
            perms = [str(t) for t in tools]

    # 安装前本地预检：高危直接阻断（不依赖 kernel 是否在线）
    pre = security_scan_content(content, permissions=perms)
    out["pre_scan"] = pre
    scan = (pre or {}).get("scan") if isinstance(pre, dict) else None
    if isinstance(scan, dict) and scan.get("ok") is False:
        high = [
            f
            for f in (scan.get("findings") or [])
            if isinstance(f, dict) and str(f.get("severity") or "").lower() == "high"
        ]
        if high:
            try:
                uninstall_package(result.name)
            except Exception:
                pass
            return {
                "ok": False,
                "name": result.name,
                "error": "package blocked by security scan (high severity findings)",
                "findings": high,
                "pre_scan": pre,
            }

    if not mirror:
        return out
    kern = mirror_to_kernel(result.name, version, content, perms)
    out["kernel"] = kern
    # 高危扫描失败：默认回滚；可选 quarantine 模式
    allow_q = False
    try:
        from backend.core.config import settings

        allow_q = bool(getattr(settings, "agent_package_allow_quarantine_install", False))
    except Exception:
        allow_q = False
    if kern.get("kernel_status") == "quarantined" or (
        isinstance(kern.get("security"), dict)
        and kern["security"].get("ok") is False
    ):
        if not allow_q:
            try:
                uninstall_package(result.name)
            except Exception:
                pass
            # kernel 侧也尽量卸掉
            try:
                _call(_kernel(), "pkg_uninstall", {"name": result.name})
            except Exception:
                pass
            return {
                "ok": False,
                "name": result.name,
                "error": "package blocked: kernel security scan failed / quarantined",
                "kernel": kern,
            }
        out["warning"] = (
            "package extracted but kernel security scan quarantined it; "
            "call market promote after review, or fix content and reinstall"
        )
    return out


def _fetch_remote_catalog(url: str) -> list[dict[str, Any]]:
    """T7：拉取远程 catalog JSON（仅公网 URL；失败返回空）。"""
    if not url or not str(url).strip().startswith("https://"):
        return []
    try:
        from backend.core.net_safety import UnsafeURLError, validate_public_url

        validate_public_url(url)
    except Exception as e:
        logger.warning("remote market url rejected: %s", e)
        return []
    try:
        import json

        raw = _safe_https_download(url, max_bytes=2_000_000)
        data = json.loads(raw.decode("utf-8", errors="replace"))
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            items = data.get("items") or data.get("packages") or []
            return [x for x in items if isinstance(x, dict)]
    except Exception as e:
        logger.warning("remote market fetch failed: %s", e)
    return []


async def market_catalog() -> dict[str, Any]:
    """合并文件系统包与 kernel catalog（+ 可选远程源）。"""
    from backend.core.config import settings
    from backend.packages.loader import list_all_packages, package_to_list_item

    items: list[dict[str, Any]] = []
    try:
        pkgs = await list_all_packages()
        for p in pkgs:
            item = package_to_list_item(p, attached=False).model_dump()
            item["source"] = getattr(p, "source", "workspace")
            item["market"] = "filesystem"
            items.append(item)
    except Exception as e:
        logger.debug("list_all_packages: %s", e)

    k = _kernel()
    kcat = _call(k, "pkg_catalog") or {}
    k_items = []
    if isinstance(kcat, dict):
        k_items = list(kcat.get("items") or [])
    by_name = {i.get("name"): i for i in items if i.get("name")}
    for ki in k_items:
        name = ki.get("name")
        if not name:
            continue
        if name in by_name:
            by_name[name]["kernel"] = ki
            by_name[name]["kernel_status"] = ki.get("status")
            by_name[name]["security_ok"] = ki.get("security_ok")
        else:
            by_name[name] = {
                "name": name,
                "version": ki.get("version"),
                "market": "kernel",
                "kernel": ki,
                "kernel_status": ki.get("status"),
                "security_ok": ki.get("security_ok"),
            }

    remote_url = str(getattr(settings, "agent_package_market_url", "") or "").strip()
    remote_items = _fetch_remote_catalog(remote_url) if remote_url else []
    for ri in remote_items:
        name = ri.get("name")
        if not name:
            continue
        if name in by_name:
            by_name[name]["remote"] = ri
        else:
            by_name[name] = {
                "name": name,
                "version": ri.get("version"),
                "market": "remote",
                "remote": ri,
                "download_url": ri.get("url") or ri.get("download_url"),
                "description": ri.get("description"),
            }

    merged = list(by_name.values())
    status = _call(k, "pkg_status") or {}
    return {
        "market": "local+remote" if remote_url else "local",
        "remote_url": remote_url or None,
        "remote_count": len(remote_items),
        "items": merged,
        "count": len(merged),
        "kernel_status": status,
        "install_root": str(install_root()),
    }


def promote_package(name: str, *, force: bool = False) -> dict[str, Any]:
    k = _kernel()
    r = _call(k, "pkg_promote", {"name": name, "force": force})
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, **r}
    if r is None:
        return {"ok": False, "error": "kernel unavailable"}
    # RPC errors may raise; dict package means ok
    if isinstance(r, dict) and r.get("status"):
        return {"ok": True, "package": r}
    return {"ok": True, "package": r}


def activate_package(name: str) -> dict[str, Any]:
    k = _kernel()
    r = _call(k, "pkg_activate", {"name": name})
    if r is None:
        return {"ok": False, "error": "kernel unavailable"}
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, **r}
    return {"ok": True, "package": r}


def uninstall_market(name: str) -> dict[str, Any]:
    fs_ok = uninstall_package(name)
    k = _kernel()
    k_r = _call(k, "pkg_uninstall", {"name": name})
    return {
        "ok": bool(fs_ok or (isinstance(k_r, dict) and k_r.get("ok"))),
        "filesystem": fs_ok,
        "kernel": k_r,
    }


def _safe_https_download(url: str, *, max_bytes: int = 64 * 1024 * 1024) -> bytes:
    """下载公网 https 内容；**每一跳重定向都重新 validate_public_url**（防 SSRF）。

    最多跟随 5 跳；禁止 http 降级与私网目标。
    """
    from backend.core.net_safety import UnsafeURLError, validate_public_url
    import urllib.error
    import urllib.request

    if not str(url).lower().startswith("https://"):
        raise UnsafeURLError("only https allowed")
    validate_public_url(url)

    _redirect_hops = {"n": 0}
    _MAX_REDIRECTS = 5

    class _NoOpenRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
            _redirect_hops["n"] += 1
            if _redirect_hops["n"] > _MAX_REDIRECTS:
                raise UnsafeURLError(
                    f"too many redirects (>{_MAX_REDIRECTS}); possible SSRF chain"
                )
            # 强制 https + 公网校验，禁止跳到 metadata/内网
            if not str(newurl).lower().startswith("https://"):
                raise UnsafeURLError(f"redirect to non-https blocked: {newurl}")
            validate_public_url(newurl)
            return urllib.request.HTTPRedirectHandler.redirect_request(
                self, req, fp, code, msg, headers, newurl
            )

    opener = urllib.request.build_opener(_NoOpenRedirect())
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "tevarn-package-market/1.0"},
        method="GET",
    )
    with opener.open(req, timeout=60) as resp:  # noqa: S310
        # 最终 URL 再校验一次（部分实现不经 redirect_request）
        final = getattr(resp, "geturl", lambda: url)()
        if final and str(final) != str(url):
            if not str(final).lower().startswith("https://"):
                raise UnsafeURLError(f"final url non-https blocked: {final}")
            validate_public_url(final)
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"download too large (>{max_bytes} bytes)")
    return data


def _parse_trusted_hashes() -> set[str]:
    try:
        from backend.core.config import settings

        raw = str(getattr(settings, "agent_package_trusted_content_hashes", "") or "")
    except Exception:
        raw = ""
    out: set[str] = set()
    for part in raw.replace(",", " ").split():
        h = part.strip().lower()
        if len(h) == 64 and all(c in "0123456789abcdef" for c in h):
            out.add(h)
    return out


def _require_content_hash() -> bool:
    try:
        from backend.core.config import settings

        return bool(getattr(settings, "agent_package_require_content_hash", False))
    except Exception:
        return False


def content_sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def verify_content_trust(
    data: bytes, *, expected_sha256: str | None = None
) -> dict[str, Any]:
    """信任根校验：内容 sha256 必须在白名单或与 expected 一致。"""
    digest = content_sha256(data)
    trusted = _parse_trusted_hashes()
    require = _require_content_hash()
    exp = (expected_sha256 or "").strip().lower()
    out: dict[str, Any] = {
        "content_sha256": digest,
        "trusted_list_size": len(trusted),
        "require_hash": require,
    }
    if exp and exp != digest:
        out["ok"] = False
        out["error"] = f"content sha256 mismatch (got {digest[:12]}… want {exp[:12]}…)"
        return out
    if trusted:
        if digest not in trusted and (not exp or exp not in trusted):
            out["ok"] = False
            out["error"] = (
                "content sha256 not in agent_package_trusted_content_hashes trust root"
            )
            return out
        out["ok"] = True
        out["matched_trust_root"] = True
        return out
    if require and not exp:
        out["ok"] = False
        out["error"] = (
            "agent_package_require_content_hash=true but no content_sha256 provided "
            "and trust list empty"
        )
        return out
    out["ok"] = True
    out["matched_trust_root"] = False
    out["warning"] = (
        "no trust root configured; set agent_package_trusted_content_hashes for production"
    )
    return out


def signing_trust_status() -> dict[str, Any]:
    """包签名 / 信任根状态（供 UI）。"""
    from backend.core.config import settings

    k = _kernel()
    pkg = _call(k, "pkg_status") or {}
    trusted = sorted(_parse_trusted_hashes())
    return {
        "signing": {
            "key_source": pkg.get("key_source"),
            "insecure_default_key": pkg.get("insecure_default_key"),
            "warning": pkg.get("warning") or "",
            "algo": "hmac-sha256",
        },
        "content_trust": {
            "trusted_hashes": trusted,
            "trusted_count": len(trusted),
            "require_content_hash": _require_content_hash(),
            "configured": bool(trusted) or _require_content_hash(),
        },
        "signing_key_setting_set": bool(
            str(getattr(settings, "agent_package_signing_key", "") or "").strip()
        ),
        "advice": (
            "Set TEVARN_PKG_SIGNING_KEY (or JWT) and agent_package_trusted_content_hashes "
            "for remote installs in production."
        ),
    }


def install_from_remote_url(
    url: str,
    *,
    overwrite: bool = False,
    expected_name: str | None = None,
    content_sha256_hex: str | None = None,
) -> dict[str, Any]:
    """远程 zip 一键安装：公网 URL → 信任根校验 → install_zip_market。"""
    from backend.core.net_safety import UnsafeURLError

    try:
        data = _safe_https_download(url)
    except UnsafeURLError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"download failed: {e}"}

    trust = verify_content_trust(data, expected_sha256=content_sha256_hex)
    if not trust.get("ok"):
        return {
            "ok": False,
            "error": trust.get("error") or "trust check failed",
            "trust": trust,
        }

    result = install_zip_market(data, overwrite=overwrite, mirror=True)
    if expected_name and result.get("ok") and result.get("name") != expected_name:
        result["warning"] = (
            f"installed name={result.get('name')} differs from expected={expected_name}"
        )
    result["source_url"] = url
    result["trust"] = trust
    # insecure signing key → already quarantined by kernel; surface status
    if result.get("kernel") and isinstance(result["kernel"], dict):
        result["kernel_status"] = result["kernel"].get("kernel_status") or (
            result["kernel"].get("package") or {}
        ).get("status")
    return result


async def install_remote_by_name(
    name: str,
    *,
    overwrite: bool = False,
    content_sha256_hex: str | None = None,
) -> dict[str, Any]:
    """从 market catalog 的 remote 项按名安装。

    content_sha256_hex：调用方显式期望哈希优先于 catalog 字段。
    """
    cat = await market_catalog()
    for item in cat.get("items") or []:
        if item.get("name") != name:
            continue
        remote = item.get("remote") if isinstance(item.get("remote"), dict) else {}
        url = (
            item.get("download_url")
            or remote.get("url")
            or remote.get("download_url")
        )
        if not url:
            return {"ok": False, "error": f"package `{name}` has no download_url"}
        sha = (
            content_sha256_hex
            or item.get("content_sha256")
            or remote.get("content_sha256")
            or remote.get("sha256")
        )
        return install_from_remote_url(
            str(url),
            overwrite=overwrite,
            expected_name=name,
            content_sha256_hex=str(sha) if sha else None,
        )
    return {"ok": False, "error": f"remote package `{name}` not found in catalog"}
