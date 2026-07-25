"""Skill 生态市场（Phase 4）：本地包的发布 / 安装 / 卸载。

闭环口径：
- 发布 export：本地包目录 → `.takton-pkg.zip`（整个目录原样打包，含 manifest/skill.yaml/SYSTEM.md）
- 安装 install：zip 字节流 → 安全校验（防路径穿越/单顶层目录/必须有可识别清单）
  → 解压到可写安装根 → 契约解析 + requires 检测透出 → 立即可被 loader 发现
- 卸载 uninstall：删除可写安装根内的同名包目录（builtin/examples/virtual 包拒绝动）

安全红线：
- zip 条目拒绝绝对路径与 `..` 穿越；拒绝 symlink
- 包名只许 [A-Za-z0-9_.-]，防目录注入
- 安装根固定在 package_search_roots 的可写候选内，不越界写文件系统
"""
from __future__ import annotations

import io
import logging
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from backend.packages.loader import (
    get_package_by_name,
    load_workspace_packages,
    _project_root,
)

logger = logging.getLogger(__name__)

_PKG_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
# 可识别清单：任一存在即视为合法包
_MANIFEST_CANDIDATES = (
    "takton.package.json",
    "package.json",
    "PACKAGE.yaml",
    "package.yaml",
    "SYSTEM.md",
)


class InstallResult(BaseModel):
    """安装结果（契约与 requires 缺失透出，不硬阻断）"""

    ok: bool
    name: str = ""
    path: str = ""
    version: str = ""
    contract: dict[str, Any] | None = None
    contract_errors: list[str] = Field(default_factory=list)
    missing_requires: list[str] = Field(default_factory=list)
    error: str = ""


def _validate_pkg_name(name: str) -> str:
    if not _PKG_NAME_RE.match(name or ""):
        raise ValueError(f"invalid package name: {name!r}")
    return name


def install_root() -> Path:
    """可写安装根：优先仓库 workspace/packages，退回用户数据目录。"""
    root = _project_root() / "workspace" / "packages"
    try:
        root.mkdir(parents=True, exist_ok=True)
        return root
    except OSError:
        pass
    import os

    appdata = os.environ.get("APPDATA") or os.environ.get("HOME") or ""
    if not appdata:
        raise RuntimeError("no writable package install root")
    root = Path(appdata) / "takton" / "packages"
    root.mkdir(parents=True, exist_ok=True)
    return root


def export_package_zip(name: str) -> tuple[bytes, str]:
    """本地包（非 virtual）→ zip 字节；返回 (content, filename)。

    raise ValueError: 包不存在 / 是 virtual 投影 / 目录不可读。
    """
    _validate_pkg_name(name)
    pkgs = load_packages_sync()
    p = get_package_by_name(pkgs, name)
    if p is None:
        raise ValueError(f"package `{name}` not found")
    if p.virtual:
        raise ValueError(f"package `{name}` is a virtual projection, export the real directory instead")
    pkg_dir = Path(p.path)
    if not pkg_dir.is_dir():
        raise ValueError(f"package `{name}` has no readable directory")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(pkg_dir.rglob("*")):
            if f.is_dir() or f.is_symlink():
                continue
            # 以包名为顶层目录，安装端按单顶层目录校验
            zf.write(f, arcname=f"{name}/{f.relative_to(pkg_dir).as_posix()}")
    return buf.getvalue(), f"{name}.takton-pkg.zip"


def load_packages_sync():
    """导出只需真实目录包（workspace/examples 各根），同步扫描即可。"""
    return load_workspace_packages()


def _safe_zip_entries(data: bytes) -> tuple[zipfile.ZipFile, str, list[str]]:
    """校验 zip：单顶层目录 + 无穿越/绝对路径/symlink。返回 (zf, top_dir, files)。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ValueError(f"not a valid zip: {e}") from e

    tops: set[str] = set()
    files: list[str] = []
    for info in zf.infolist():
        name = info.filename
        # symlink 拒绝（Unix mode 高位 0120000）
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError(f"symlink entry not allowed: {name}")
        norm = name.replace("\\", "/")
        if norm.startswith("/") or norm.startswith("..") or "/../" in norm or norm.endswith("/.."):
            raise ValueError(f"unsafe path entry: {name}")
        parts = [p for p in norm.split("/") if p]
        if not parts:
            continue
        tops.add(parts[0])
        if not info.is_dir():
            files.append(norm)
    if len(tops) != 1:
        raise ValueError("zip must contain exactly one top-level package directory")
    top = tops.pop()
    _validate_pkg_name(top)
    if not files:
        raise ValueError("zip contains no files")
    # 必须带可识别清单
    if not any(f.startswith(f"{top}/") and f.split("/", 1)[1] in _MANIFEST_CANDIDATES for f in files):
        raise ValueError(
            f"no recognizable manifest ({'/'.join(_MANIFEST_CANDIDATES)}) in package"
        )
    return zf, top, files


def install_package_zip(data: bytes, *, overwrite: bool = False) -> InstallResult:
    """zip 字节 → 校验 → 解压到安装根 → 契约/requires 透出。"""
    try:
        zf, top, files = _safe_zip_entries(data)
    except ValueError as e:
        return InstallResult(ok=False, error=str(e))

    root = install_root()
    dest = root / top
    if dest.exists():
        if not overwrite:
            return InstallResult(ok=False, name=top, error=f"package `{top}` already installed")
        shutil.rmtree(dest)

    try:
        for info in zf.infolist():
            if info.is_dir():
                continue
            norm = info.filename.replace("\\", "/")
            rel = norm.split("/", 1)[1] if "/" in norm else ""
            if not rel:
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
    except OSError as e:
        shutil.rmtree(dest, ignore_errors=True)
        return InstallResult(ok=False, name=top, error=f"extract failed: {e}")

    result = InstallResult(ok=True, name=top, path=str(dest))
    # 契约解析 + requires 检测（透出，不阻断）
    try:
        from backend.skills.contract import SkillContract, check_requires, load_contract_for_dir

        contract, errs = load_contract_for_dir(dest)
        if contract is not None:
            result.contract = contract.model_dump()
            result.version = contract.version
            result.missing_requires = check_requires(SkillContract.model_validate(contract.model_dump()))
        if errs:
            result.contract_errors = errs
    except Exception as e:
        logger.debug("contract check skipped for %s: %s", dest, e)
    logger.info("package installed: %s -> %s", top, dest)
    return result


def uninstall_package(name: str) -> bool:
    """删除可写安装根内的同名包；examples/其他只读根的包拒绝卸载。"""
    _validate_pkg_name(name)
    root = install_root()
    dest = root / name
    # 防符号链接逃逸：仅允许真实子目录（is_dir 对 symlink 同样为 True，须先查）
    if dest.is_symlink() or not dest.is_dir():
        return False
    shutil.rmtree(dest)
    logger.info("package uninstalled: %s", name)
    return True
