# -*- coding: utf-8 -*-
"""Packaged Electron layout: host bin discovery + no dotenv leak."""
from __future__ import annotations

from pathlib import Path

import pytest


def test_find_host_bin_packaged_resources_layout(tmp_path, monkeypatch):
    """resources/takton-kernel-host/takton-kernel-host.exe must be found.

    Electron extraResources ships the binary at resources/takton-kernel-host/,
    while Python client lives at resources/backend/kernel_rust/client.py.
    """
    from backend.kernel_rust import client as kc

    resources = tmp_path / "resources"
    backend_kr = resources / "backend" / "kernel_rust"
    backend_kr.mkdir(parents=True)
    host_dir = resources / "takton-kernel-host"
    host_dir.mkdir(parents=True)
    host_bin = host_dir / "takton-kernel-host.exe"
    host_bin.write_bytes(b"MZ-fake")

    # Pretend client.py lives under resources/backend/kernel_rust/
    fake_client = backend_kr / "client.py"
    fake_client.write_text("# fake")

    monkeypatch.delenv("TAKTON_KERNEL_HOST_BIN", raising=False)
    monkeypatch.delenv("TAKTON_ROOT", raising=False)
    monkeypatch.setenv("TAKTON_RESOURCES_PATH", str(resources))

    # Point __file__ resolution: patch Path(__file__) via rewriting _find_host_bin roots
    # Easiest: set TAKTON_RESOURCES_PATH (covered by extra_roots) — already set.
    found = kc._find_host_bin()
    assert found is not None
    assert found.resolve() == host_bin.resolve()


def test_find_host_bin_env_override(tmp_path, monkeypatch):
    from backend.kernel_rust import client as kc

    bin_path = tmp_path / "custom-host.exe"
    bin_path.write_bytes(b"MZ")
    monkeypatch.setenv("TAKTON_KERNEL_HOST_BIN", str(bin_path))
    found = kc._find_host_bin()
    assert found is not None
    assert found.resolve() == bin_path.resolve()


def test_settings_env_file_packaged_skips_dotenv(monkeypatch):
    monkeypatch.setenv("TAKTON_PACKAGED", "1")
    monkeypatch.delenv("TAKTON_ENV_FILE", raising=False)
    # Re-import helper
    from backend.core import config as cfg

    assert cfg._settings_env_file() is None


def test_settings_env_file_explicit(monkeypatch, tmp_path):
    envp = tmp_path / "custom.env"
    envp.write_text("TAKTON_LLM_MODEL=x\n")
    monkeypatch.setenv("TAKTON_ENV_FILE", str(envp))
    monkeypatch.delenv("TAKTON_PACKAGED", raising=False)
    from backend.core import config as cfg

    assert cfg._settings_env_file() == str(envp)
