"""Local python/command spawn strips product secrets, keeps PATH/HOME/user env."""

from __future__ import annotations

from backend.core.host_commands import is_control_plane_env_key, tool_spawn_env


def test_control_plane_keys_are_recognized():
    assert is_control_plane_env_key("TEVARN_JWT_SECRET")
    assert is_control_plane_env_key("TEVARN_API_KEY")
    assert is_control_plane_env_key("TEVARN_DEFAULT_ADMIN_PASSWORD")
    assert is_control_plane_env_key("TEVARN_DESKTOP_PERMISSION_SECRET")
    assert is_control_plane_env_key("TEVARN_SETTINGS_ENCRYPTION_SALT")
    assert is_control_plane_env_key("TEVARN_KERNEL_RPC_SECRET")
    assert not is_control_plane_env_key("PATH")
    assert not is_control_plane_env_key("HOME")
    assert not is_control_plane_env_key("OPENAI_API_KEY")
    assert not is_control_plane_env_key("TEVARN_HOME")


def test_tool_spawn_env_strips_secrets_keeps_user_env(monkeypatch):
    monkeypatch.setenv("TEVARN_JWT_SECRET", "jwt-must-not-leak")
    monkeypatch.setenv("TEVARN_API_KEY", "api-must-not-leak")
    monkeypatch.setenv("TEVARN_DEFAULT_ADMIN_PASSWORD", "admin-must-not-leak")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("HOME", "/home/owner")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-user-intended")
    monkeypatch.setenv("MY_CUSTOM_FLAG", "keep-me")

    env = tool_spawn_env()
    assert "TEVARN_JWT_SECRET" not in env
    assert "TEVARN_API_KEY" not in env
    assert "TEVARN_DEFAULT_ADMIN_PASSWORD" not in env
    assert env.get("PATH")
    assert env.get("HOME") == "/home/owner"
    assert env.get("OPENAI_API_KEY") == "sk-user-intended"
    assert env.get("MY_CUSTOM_FLAG") == "keep-me"


def test_tool_spawn_env_rejects_secret_in_extra(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    env = tool_spawn_env({"TEVARN_JWT_SECRET": "injected", "FOO": "bar"})
    assert "TEVARN_JWT_SECRET" not in env
    assert env["FOO"] == "bar"


def test_executors_local_spawn_pass_curated_env():
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "services" / "tools" / "executors.py"
    text = src.read_text(encoding="utf-8")
    assert "tool_spawn_env" in text
    assert "create_process_exec" in text
    assert "env=tool_spawn_env()" in text or "env=_child_env" in text
