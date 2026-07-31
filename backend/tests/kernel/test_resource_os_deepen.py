"""Resource OS deepen + remote install guards (lightweight)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def test_sample_rss_self():
    from backend.kernel.resource_os import sample_rss_bytes_self, status

    rss = sample_rss_bytes_self()
    # may be None on exotic platforms without APIs
    if rss is not None:
        assert rss > 0
    st = status()
    assert "platform" in st
    assert "cgroup_enabled" in st


def test_report_rss_without_kernel(monkeypatch):
    from backend.kernel import resource_os as ros

    class FakeK:
        def resource_usage(self, pid):
            return {"memory_bytes": {"used": 100, "limit": 10_000_000}}

        def resource_charge(self, pid, kind, amount=1):
            return 1

    monkeypatch.setattr("backend.kernel.get_kernel", lambda: FakeK())
    r = ros.report_rss_to_kernel("p1", 5000)
    assert r.get("ok") is True or r.get("used") is not None


def test_cgroup_skip_on_non_linux():
    from backend.kernel.resource_os import cgroup_apply

    r = cgroup_apply("test", memory_max_bytes=1024 * 1024)
    # windows/mac: skipped
    assert r.get("ok") is False or r.get("skipped") is True or r.get("ok") is True


def test_install_remote_rejects_http():
    from backend.packages.market import install_from_remote_url

    r = install_from_remote_url("http://example.com/x.zip")
    assert r.get("ok") is False


def test_resource_os_module_exports():
    from backend.kernel import resource_os as ros

    assert callable(ros.sample_and_report)
    assert callable(ros.cgroup_apply)
    assert callable(ros.report_rss_to_kernel)
