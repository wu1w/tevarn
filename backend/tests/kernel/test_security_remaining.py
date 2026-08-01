"""剩余安全项：process_id 覆盖、zip slip、process 访问、签名密钥语义。"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


@pytest.mark.asyncio
async def test_tool_gate_process_id_kwarg_overrides_args():
    """process_id 形参覆盖 args 内伪造的 process id。"""
    from backend.kernel import tool_gate

    monkeypatch_charges = []

    class FakeK:
        async def mediate(self, pid, action, target, args=None):
            assert pid == "REAL_PROC"
            return {"allowed": True}

        def resource_charge(self, pid, kind, amount=1):
            monkeypatch_charges.append(pid)
            return 1

    import backend.kernel as kmod

    # force via enforce API
    async def run(monkeypatch):
        monkeypatch.setattr(tool_gate, "_kernel_enabled", lambda: True)
        monkeypatch.setattr("backend.kernel.get_kernel", lambda: FakeK())
        args, err = await tool_gate.enforce_tool_gate(
            "file_read",
            {"_kernel_process_id": "FAKE_FROM_MODEL", "path": "x"},
            process_id="REAL_PROC",
        )
        assert err is None
        assert args["_kernel_process_id"] == "REAL_PROC"

    # use pytest monkeypatch via fixture style manual
    from _pytest.monkeypatch import MonkeyPatch

    mp = MonkeyPatch()
    try:
        await run(mp)
    finally:
        mp.undo()


def test_validate_tool_args_strips_gate_keys():
    from backend.agent.loop_cluster import LoopClusterMixin

    class X(LoopClusterMixin):
        pass

    x = X()
    out = x._validate_tool_args(
        None,
        {
            "path": "a",
            "_kernel_process_id": "evil",
            "_tool_gate_passed": True,
            "_tool_gate_internal": True,
        },
    )
    assert "path" in out
    assert "_kernel_process_id" not in out
    assert "_tool_gate_passed" not in out


def test_process_access_not_found():
    from backend.kernel.process_access import assert_process_accessible

    k = MagicMock()
    k.get_process.return_value = None
    with pytest.raises(ValueError, match="not found"):
        assert_process_accessible(k, "missing")


def test_process_access_session_mismatch():
    from backend.kernel.process_access import assert_process_accessible

    k = MagicMock()
    p = MagicMock()
    p.to_dict.return_value = {"id": "p1", "session_id": "sess-A", "state": "running"}
    k.get_process.return_value = p
    with pytest.raises(ValueError, match="session mismatch"):
        assert_process_accessible(k, "p1", session_id="sess-B")
    # match ok
    d = assert_process_accessible(k, "p1", session_id="sess-A")
    assert d["id"] == "p1"


def test_process_access_require_session_forced():
    """collab/sample-rss：交互进程必须带匹配 session_id。"""
    from backend.kernel.process_access import assert_process_accessible

    k = MagicMock()
    p = MagicMock()
    p.to_dict.return_value = {
        "id": "p1",
        "session_id": "sess-A",
        "identity": "user:1",
        "state": "running",
    }
    k.get_process.return_value = p
    with pytest.raises(ValueError, match="session_id required"):
        assert_process_accessible(k, "p1", require_session=True)
    with pytest.raises(ValueError, match="session mismatch"):
        assert_process_accessible(
            k, "p1", session_id="sess-B", require_session=True
        )
    ok = assert_process_accessible(
        k, "p1", session_id="sess-A", require_session=True
    )
    assert ok["id"] == "p1"


def test_process_access_require_session_wf_no_bind():
    """编制进程（wf:）无 session 绑定时允许无 session_id。"""
    from backend.kernel.process_access import assert_process_accessible

    k = MagicMock()
    p = MagicMock()
    p.to_dict.return_value = {
        "id": "wf1",
        "session_id": None,
        "identity": "wf:crew-1",
        "state": "running",
    }
    k.get_process.return_value = p
    d = assert_process_accessible(k, "wf1", require_session=True)
    assert d["id"] == "wf1"


def test_process_access_require_session_unbound_interactive_denied():
    """无 session 的非编制进程在 require_session 下拒绝。"""
    from backend.kernel.process_access import assert_process_accessible

    k = MagicMock()
    p = MagicMock()
    p.to_dict.return_value = {
        "id": "bg1",
        "session_id": None,
        "identity": "agent:x",
        "state": "running",
    }
    k.get_process.return_value = p
    with pytest.raises(ValueError, match="session_id required"):
        assert_process_accessible(k, "bg1", require_session=True)
    with pytest.raises(ValueError, match="no session binding"):
        assert_process_accessible(
            k, "bg1", session_id="any", require_session=True
        )


@pytest.mark.asyncio
async def test_identity_owner_multi_user_match_and_deny(monkeypatch):
    """编制 identity top-up 同源：多用户按 user_id 归属，无主 fail-closed。"""
    from unittest.mock import patch

    from backend.kernel import process_access as pa

    class Row:
        user_id = "owner-1"
        owner_user_id = None
        created_by = None

    row = Row()

    class FakeResult:
        def scalar_one_or_none(self):
            return row

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, *a, **k):
            return FakeResult()

    monkeypatch.setattr(pa, "_single_user_mode", lambda: False)
    iid = "11111111-1111-1111-1111-111111111111"
    with patch("backend.database.AsyncSessionLocal", return_value=FakeSession()):
        got = await pa.assert_user_owns_identity(iid, "owner-1")
        assert got is row
        with pytest.raises(PermissionError, match="not owned"):
            await pa.assert_user_owns_identity(iid, "other-user")
        row.user_id = None
        with pytest.raises(PermissionError, match="no owner binding"):
            await pa.assert_user_owns_identity(f"wf:{iid}", "owner-1")


@pytest.mark.asyncio
async def test_identity_owner_single_user_allows(monkeypatch):
    from unittest.mock import patch

    from backend.kernel import process_access as pa

    class Row:
        user_id = "someone-else"

    class FakeResult:
        def scalar_one_or_none(self):
            return Row()

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, *a, **k):
            return FakeResult()

    monkeypatch.setattr(pa, "_single_user_mode", lambda: True)
    with patch("backend.database.AsyncSessionLocal", return_value=FakeSession()):
        got = await pa.assert_user_owns_identity(
            "11111111-1111-1111-1111-111111111111", "any"
        )
        assert got is not None


def test_verify_content_trust_root(monkeypatch):
    from backend.packages import market as m

    data = b"hello-pkg-bytes"
    digest = m.content_sha256(data)

    monkeypatch.setattr(m, "_parse_trusted_hashes", lambda: set())
    monkeypatch.setattr(m, "_require_content_hash", lambda: False)
    r = m.verify_content_trust(data)
    assert r["ok"] is True
    assert r.get("matched_trust_root") is False

    monkeypatch.setattr(m, "_parse_trusted_hashes", lambda: {digest})
    r2 = m.verify_content_trust(data)
    assert r2["ok"] is True and r2.get("matched_trust_root") is True

    monkeypatch.setattr(m, "_parse_trusted_hashes", lambda: {"a" * 64})
    r3 = m.verify_content_trust(data)
    assert r3["ok"] is False
    assert "trust root" in (r3.get("error") or "")

    r4 = m.verify_content_trust(data, expected_sha256="b" * 64)
    assert r4["ok"] is False
    assert "mismatch" in (r4.get("error") or "")


def test_verify_content_trust_require_hash(monkeypatch):
    from backend.packages import market as m

    data = b"x"
    monkeypatch.setattr(m, "_parse_trusted_hashes", lambda: set())
    monkeypatch.setattr(m, "_require_content_hash", lambda: True)
    r = m.verify_content_trust(data)
    assert r["ok"] is False
    r2 = m.verify_content_trust(data, expected_sha256=m.content_sha256(data))
    assert r2["ok"] is True


def test_signing_trust_status_shape(monkeypatch):
    from backend.packages import market as m

    monkeypatch.setattr(
        m,
        "_call",
        lambda k, method, *a, **kw: {
            "key_source": "insecure_default",
            "insecure_default_key": True,
            "warning": "dev",
        },
    )
    monkeypatch.setattr(m, "_kernel", lambda: object())
    monkeypatch.setattr(m, "_parse_trusted_hashes", lambda: set())
    monkeypatch.setattr(m, "_require_content_hash", lambda: False)
    st = m.signing_trust_status()
    assert "signing" in st and "content_trust" in st
    assert st["content_trust"]["trusted_count"] == 0


def test_zip_slip_rejected(tmp_path):
    from backend.packages.publisher import install_package_zip

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("evil/../../../tmp/x", "pwn")
        zf.writestr("evil/SYSTEM.md", "# x")
    # may fail on tops or path
    r = install_package_zip(buf.getvalue())
    assert r.ok is False


def test_zip_symlink_rejected():
    from backend.packages.publisher import install_package_zip

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("pkg/link")
        info.external_attr = (0o120777 << 16)  # symlink
        zf.writestr(info, "target")
        zf.writestr("pkg/SYSTEM.md", "# x")
    r = install_package_zip(buf.getvalue())
    assert r.ok is False


def test_remote_install_http_blocked():
    from backend.packages.market import install_from_remote_url

    r = install_from_remote_url("http://example.com/a.zip")
    assert r["ok"] is False


def test_safe_download_requires_https():
    from backend.core.net_safety import UnsafeURLError
    from backend.packages.market import _safe_https_download

    with pytest.raises(UnsafeURLError):
        _safe_https_download("http://example.com/x")
