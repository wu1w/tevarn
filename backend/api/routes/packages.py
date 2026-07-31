"""Takton Package API — 统一 skill / 子代理 / 工作流挂载。"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from backend.packages.loader import (
    get_package_by_name,
    list_all_packages,
    package_to_detail,
    package_to_list_item,
    resolve_attached_snippets,
)
from backend.packages.session_packages import (
    attach_package,
    detach_package,
    get_session_attached_packages,
    set_session_attached_packages,
)
from backend.schemas.user import UserRead

from ..dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/packages", tags=["Packages"])


class AttachBody(BaseModel):
    session_id: str
    name: str


class SetAttachedBody(BaseModel):
    session_id: str
    packages: list[str] = Field(default_factory=list)


@router.get("")
async def list_packages(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    session_id: str | None = Query(default=None),
    source: str | None = Query(default=None, description="workspace|skill|sub_agent|workflow"),
):
    pkgs = await list_all_packages()
    attached: list[str] = []
    if session_id:
        try:
            attached = await get_session_attached_packages(session_id)
        except Exception:
            attached = []
    att_set = set(attached)
    items = []
    for p in pkgs:
        if source and p.source != source:
            continue
        items.append(package_to_list_item(p, attached=p.name in att_set).model_dump())
    return {
        "packages": items,
        "attached": attached,
        "count": len(items),
    }


@router.get("/session/{session_id}")
async def get_session_packages(
    session_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    attached = await get_session_attached_packages(session_id)
    snippets = await resolve_attached_snippets(attached)
    return {"session_id": session_id, "attached": attached, "snippets": snippets}


@router.post("/attach")
async def attach_pkg(
    body: AttachBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    pkgs = await list_all_packages()
    if not get_package_by_name(pkgs, body.name):
        raise HTTPException(status_code=404, detail=f"package `{body.name}` not found")
    try:
        attached = await attach_package(body.session_id, body.name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    snippets = await resolve_attached_snippets(attached)
    return {
        "ok": True,
        "attached": attached,
        "snippets": snippets,
        "message": f"已挂载包 `{body.name}`",
    }


@router.post("/detach")
async def detach_pkg(
    body: AttachBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    try:
        attached = await detach_package(body.session_id, body.name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {
        "ok": True,
        "attached": attached,
        "message": f"已卸载包 `{body.name}`",
    }


@router.put("/session")
async def set_session_packages(
    body: SetAttachedBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    pkgs = await list_all_packages()
    known = {p.name for p in pkgs}
    unknown = [n for n in body.packages if n not in known]
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown packages: {unknown}")
    try:
        attached = await set_session_attached_packages(body.session_id, body.packages)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    snippets = await resolve_attached_snippets(attached)
    return {"ok": True, "attached": attached, "snippets": snippets}


@router.get("/detail/{name:path}")
async def get_package(
    name: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    session_id: str | None = Query(default=None),
):
    pkgs = await list_all_packages()
    p = get_package_by_name(pkgs, name)
    if not p:
        raise HTTPException(status_code=404, detail=f"package `{name}` not found")
    attached = False
    if session_id:
        att = await get_session_attached_packages(session_id)
        attached = name in att
    return package_to_detail(p, attached=attached).model_dump()


# ─────────── Phase 4：发布 / 安装 / 卸载 ───────────


class InstallUrlBody(BaseModel):
    url: str
    overwrite: bool = False


@router.get("/export/{name}")
async def export_pkg(
    name: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """发布：本地包导出为 .takton-pkg.zip 下载"""
    from fastapi.responses import Response

    from backend.packages.publisher import export_package_zip

    try:
        content, filename = export_package_zip(name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/install")
async def install_pkg_upload(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    file: UploadFile = File(...),
    overwrite: bool = Query(default=False),
    mirror_kernel: bool = Query(
        default=True,
        description="安装后镜像进 Kernel 包管理（签名扫描）",
    ),
):
    """安装：上传 .takton-pkg.zip → 校验 → 解压 →（默认）Kernel 签名扫描镜像"""
    from backend.packages.market import install_zip_market

    data = await file.read()
    if len(data) > 64 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="package zip too large (64MB max)")
    result = install_zip_market(data, overwrite=overwrite, mirror=mirror_kernel)
    if not result.get("ok"):
        status = 409 if "already installed" in str(result.get("error") or "") else 400
        raise HTTPException(status_code=status, detail=result.get("error") or "install failed")
    return result


@router.post("/install-url")
async def install_pkg_url(
    body: InstallUrlBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """安装：从 URL 拉取 zip（公网校验防 SSRF）→ 同上传安装流程"""
    import aiohttp

    from backend.core.net_safety import UnsafeURLError, validate_public_url
    from backend.packages.publisher import install_package_zip

    try:
        validate_public_url(body.url)
    except UnsafeURLError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(body.url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    raise HTTPException(status_code=502, detail=f"download failed: HTTP {resp.status}")
                data = await resp.read()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"download failed: {e}") from e
    if len(data) > 64 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="package zip too large (64MB max)")
    result = install_package_zip(data, overwrite=body.overwrite)
    if not result.ok:
        status = 409 if "already installed" in result.error else 400
        raise HTTPException(status_code=status, detail=result.error)
    return result.model_dump()


@router.delete("/installed/{name}")
async def uninstall_pkg(
    name: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """卸载：删除可写安装根内的同名包（examples/只读根的包拒绝）"""
    from backend.packages.publisher import uninstall_package

    try:
        removed = uninstall_package(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not removed:
        raise HTTPException(status_code=404, detail=f"installed package `{name}` not found")
    return {"ok": True, "name": name}


# ─────────── 债 #3：本地包市场（签名扫描 / catalog / promote）───────────


class ScanBody(BaseModel):
    content: str
    permissions: list[str] = Field(default_factory=list)


class PromoteBody(BaseModel):
    name: str
    force: bool = False


@router.get("/market")
async def market_list(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """本地市场目录：文件系统包 ∪ Kernel catalog（含 quarantine 状态）。"""
    from backend.packages.market import market_catalog

    return await market_catalog()


@router.post("/market/scan")
async def market_scan(
    body: ScanBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """安装前安全扫描（Rust pkg_scan）。"""
    from backend.packages.market import security_scan_content

    return security_scan_content(body.content, body.permissions)


@router.post("/market/install")
async def market_install_upload(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    file: UploadFile = File(...),
    overwrite: bool = Query(default=False),
):
    """市场安装：zip 解压 + Kernel 签名扫描镜像。"""
    from backend.packages.market import install_zip_market

    data = await file.read()
    if len(data) > 64 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="package zip too large (64MB max)")
    result = install_zip_market(data, overwrite=overwrite, mirror=True)
    if not result.get("ok"):
        status = 409 if "already installed" in str(result.get("error") or "") else 400
        raise HTTPException(status_code=status, detail=result.get("error") or "install failed")
    return result


@router.post("/market/promote")
async def market_promote(
    body: PromoteBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """出隔离：重扫通过后 status→verified（仍须 activate）。"""
    from backend.packages.market import promote_package

    r = promote_package(body.name, force=body.force)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error") or "promote failed")
    return r


class RemoteInstallBody(BaseModel):
    """远程一键安装：name（catalog）或 url（https zip）。"""

    name: str | None = None
    url: str | None = None
    overwrite: bool = False
    content_sha256: str | None = Field(
        default=None,
        description="期望内容 sha256；信任根非空时必须命中",
    )


@router.get("/market/trust")
async def market_trust_status(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """包签名密钥来源 + 内容信任根配置状态。"""
    from backend.packages.market import signing_trust_status

    return signing_trust_status()


@router.post("/market/install-remote")
async def market_install_remote(
    body: RemoteInstallBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """远程包一键下载安装（https + 重定向防护 + 内容信任根 + 签名扫描）。"""
    from backend.packages.market import install_from_remote_url, install_remote_by_name

    if body.url:
        result = install_from_remote_url(
            body.url,
            overwrite=body.overwrite,
            content_sha256_hex=body.content_sha256,
        )
    elif body.name:
        result = await install_remote_by_name(
            body.name,
            overwrite=body.overwrite,
            content_sha256_hex=body.content_sha256,
        )
    else:
        raise HTTPException(status_code=400, detail="name or url required")
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "install failed")
    return result


@router.post("/market/activate")
async def market_activate(
    body: PromoteBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """激活已 verified 包（quarantined 拒绝）。"""
    from backend.packages.market import activate_package

    r = activate_package(body.name)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error") or "activate failed")
    return r
