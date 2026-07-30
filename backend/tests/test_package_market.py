"""Phase 4 Skill 生态市场：发布 / 安装 / 卸载 测试

零 mock 真端到端：真 zip 字节、真文件系统（仓库 workspace/packages 安装根，
gitignored），用 uuid 包名隔离，测完 uninstall 清理。
"""
import io
import json
import uuid
import zipfile

import pytest

from backend.packages import publisher


def _mk_zip(top: str, files: dict[str, str]) -> bytes:
    """内存造包 zip：top 为顶层目录，files 为 {相对路径: 内容}"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, content in files.items():
            zf.writestr(f"{top}/{rel}" if top else rel, content)
    return buf.getvalue()


@pytest.fixture()
def installed_name():
    """uuid 包名；用例结束后强制清理安装根"""
    name = f"pkg-test-{uuid.uuid4().hex[:12]}"
    yield name
    publisher.uninstall_package(name)


def test_export_example_package():
    content, filename = publisher.export_package_zip("code-review-lite")
    assert filename == "code-review-lite.takton-pkg.zip"
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        names = set(zf.namelist())
    assert "code-review-lite/takton.package.json" in names
    assert "code-review-lite/skill.yaml" in names


def test_export_not_found():
    with pytest.raises(ValueError, match="not found"):
        publisher.export_package_zip(f"no-such-pkg-{uuid.uuid4().hex[:8]}")


def test_export_invalid_name():
    with pytest.raises(ValueError, match="invalid package name"):
        publisher.export_package_zip("../escape")


def test_install_roundtrip_and_loader_discovers(installed_name):
    """安装 → loader 发现 → 详情含契约 → 卸载后消失"""
    data = _mk_zip(installed_name, {
        "takton.package.json": json.dumps({
            "name": installed_name,
            "version": "1.2.3",
            "description": "roundtrip test pkg",
            "system_snippet": "hello",
        }),
        "skill.yaml": "name: %s\nversion: 1.2.3\nworkflow:\n  - step one\n" % installed_name,
    })
    result = publisher.install_package_zip(data)
    assert result.ok, result.error
    assert result.name == installed_name
    assert result.contract is not None
    assert result.contract["workflow"] == ["step one"]

    from backend.packages.loader import get_package_by_name, load_workspace_packages

    pkgs = load_workspace_packages()
    p = get_package_by_name(pkgs, installed_name)
    assert p is not None
    assert p.version == "1.2.3"

    assert publisher.uninstall_package(installed_name) is True
    pkgs = load_workspace_packages()
    assert get_package_by_name(pkgs, installed_name) is None


def test_install_path_traversal_rejected():
    data = _mk_zip("evil-pkg", {"../escape.txt": "boom"})
    result = publisher.install_package_zip(data)
    assert not result.ok
    assert "unsafe path" in result.error


def test_install_absolute_path_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("/abs/evil.txt", "boom")
    result = publisher.install_package_zip(buf.getvalue())
    assert not result.ok


def test_install_no_manifest_rejected():
    data = _mk_zip("no-manifest-pkg", {"readme.txt": "nothing"})
    result = publisher.install_package_zip(data)
    assert not result.ok
    assert "manifest" in result.error


def test_install_multi_top_dir_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a/package.json", "{}")
        zf.writestr("b/package.json", "{}")
    result = publisher.install_package_zip(buf.getvalue())
    assert not result.ok
    assert "top-level" in result.error


def test_install_duplicate_then_overwrite(installed_name):
    data = _mk_zip(installed_name, {"SYSTEM.md": "v1"})
    assert publisher.install_package_zip(data).ok
    again = publisher.install_package_zip(data)
    assert not again.ok and "already installed" in again.error
    ok = publisher.install_package_zip(data, overwrite=True)
    assert ok.ok


def test_install_requires_missing_transparent(installed_name):
    """契约 requires 缺失透出但不阻断安装"""
    data = _mk_zip(installed_name, {
        "SYSTEM.md": "x",
        "skill.yaml": (
            "name: %s\nrequires:\n  bins: [definitely-not-a-real-bin-xyz123]\n"
            % installed_name
        ),
    })
    result = publisher.install_package_zip(data)
    assert result.ok, result.error
    assert "bin: definitely-not-a-real-bin-xyz123" in result.missing_requires


def test_uninstall_rejects_examples_package():
    """examples 根（非安装根）的包不可卸载"""
    assert publisher.uninstall_package("code-review-lite") is False


# ─────────── 路由级（TestClient，single_user_mode 免 token）───────────


@pytest.fixture()
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import packages as packages_mod

    app = FastAPI()
    app.include_router(packages_mod.router)
    with TestClient(app) as c:
        yield c


def test_route_export_install_uninstall_roundtrip(client, installed_name):
    # 导出 examples 包
    resp = client.get("/packages/export/code-review-lite")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"

    # 改包名后上传安装（沿用导出内容，仅重写顶层目录名）
    src = io.BytesIO(resp.content)
    buf = io.BytesIO()
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(buf, "w") as zout:
        for info in zin.infolist():
            rel = info.filename.split("/", 1)[1]
            zout.writestr(f"{installed_name}/{rel}", zin.read(info))
    resp = client.post(
        "/packages/install",
        files={"file": ("pkg.zip", buf.getvalue(), "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] and body["name"] == installed_name

    # 重复安装 → 409
    resp = client.post(
        "/packages/install",
        files={"file": ("pkg.zip", buf.getvalue(), "application/zip")},
    )
    assert resp.status_code == 409

    # 卸载 → 200；再卸 → 404
    assert client.delete(f"/packages/installed/{installed_name}").status_code == 200
    assert client.delete(f"/packages/installed/{installed_name}").status_code == 404


def test_route_install_bad_zip_400(client):
    resp = client.post(
        "/packages/install",
        files={"file": ("pkg.zip", b"not a zip at all", "application/zip")},
    )
    assert resp.status_code == 400
