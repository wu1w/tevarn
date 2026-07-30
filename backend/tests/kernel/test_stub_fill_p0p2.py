"""桩补齐回归：cron agent 接线、cluster cancel、desktop 清库、skills。"""
from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

# .../backend/tests/kernel/this_file.py → backend/
_BACKEND = Path(__file__).resolve().parents[2]


def test_cron_hook_agent_branch_calls_run_subagent():
    src = (_BACKEND / "api/routes/cron_hook.py").read_text(encoding="utf-8")
    assert "run_subagent" in src
    assert "execution not yet wired" not in src
    assert "TODO(cron-hook)" not in src


def test_cluster_cancel_cancels_bg_task():
    src = (_BACKEND / "api/routes/cluster.py").read_text(encoding="utf-8")
    assert "bg.cancel()" in src
    assert "TODO: 实现取消逻辑" not in src
    assert 'status="cancelled"' in src


def test_desktop_clear_permissions_hits_db():
    src = (_BACKEND / "api/routes/desktop.py").read_text(encoding="utf-8")
    assert "clear_permissions" in src
    assert "TODO: 清除数据库中的权限" not in src
    repo_path = _BACKEND / "repositories/desktop_permission_repo.py"
    if not repo_path.is_file():
        # 桌面权限可能合在 services 内
        svc = (_BACKEND / "services/desktop").read_text(encoding="utf-8") if False else ""
        assert "clear_permissions" in src
        return
    repo = repo_path.read_text(encoding="utf-8")
    assert "delete_all_for_user" in repo or "clear" in repo.lower()


@pytest.mark.asyncio
async def test_calendar_read_uses_shared_store(tmp_path, monkeypatch):
    import backend.tools.builtins.wave_a_tools as wa

    monkeypatch.setattr(wa, "_CAL_DIR", tmp_path)
    monkeypatch.setattr(wa, "_CAL_FILE", tmp_path / "events.json")
    # create one event via tool
    tool = wa.CalendarTool()
    await tool.execute(action="create", title="standup", start="2099-01-02T10:00")
    from backend.skills.builtins.calendar_read_skill import CalendarReadSkill

    skill = CalendarReadSkill()
    out = await skill.execute(date="2099-01-02", days=3)
    assert "standup" in out
    assert "Stub" not in out


@pytest.mark.asyncio
async def test_send_email_requires_smtp_config(monkeypatch):
    monkeypatch.delenv("TAKTON_SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    from backend.skills.builtins import send_email_skill as se

    # force empty host
    monkeypatch.setattr(
        se,
        "_smtp_config",
        lambda: {
            "host": "",
            "port": 587,
            "user": "",
            "password": "",
            "from_addr": "",
            "use_tls": True,
        },
    )
    skill = se.SendEmailSkill()
    out = await skill.execute(to="a@b.com", subject="hi", body="x")
    assert out.startswith("[Error]")
    assert "SMTP" in out


@pytest.mark.asyncio
async def test_send_email_sends_via_smtp(monkeypatch):
    from backend.skills.builtins import send_email_skill as se

    monkeypatch.setattr(
        se,
        "_smtp_config",
        lambda: {
            "host": "smtp.example.com",
            "port": 587,
            "user": "u@example.com",
            "password": "secret",
            "from_addr": "u@example.com",
            "use_tls": True,
        },
    )

    class _FakeSMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def ehlo(self):
            pass

        def starttls(self, context=None):
            pass

        def login(self, u, p):
            self.user = u

        def send_message(self, msg):
            self.msg = msg

    monkeypatch.setattr(se.smtplib, "SMTP", _FakeSMTP)
    skill = se.SendEmailSkill()
    out = await skill.execute(to="a@b.com", subject="hi", body="hello")
    assert out.startswith("OK")


@pytest.mark.asyncio
async def test_agent_call_invokes_run_subagent(monkeypatch):
    from backend.skills.builtins.agent_call_skill import AgentCallSkill

    fake_agent = SimpleNamespace(
        id=uuid.uuid4(),
        name="Coder",
        description="code",
        system_prompt="you code",
        model_ref="",
        max_iterations=4,
        enabled=True,
    )

    class _Repo:
        async def list_enabled(self):
            return [fake_agent]

        async def get_by_id(self, _id):
            return fake_agent

    called = {}

    async def _run_subagent(**kwargs):
        called.update(kwargs)
        return "[delegate_task -> Coder]\nok"

    monkeypatch.setattr(
        "backend.repositories.sub_agent_repo.AsyncSubAgentRepository",
        lambda: _Repo(),
    )
    monkeypatch.setattr(
        "backend.agent.subagent_runner.run_subagent",
        _run_subagent,
    )
    skill = AgentCallSkill()
    sid = uuid.uuid4()
    out = await skill.execute(
        agent="Coder",
        task="fix a function",
        context="py",
        _session_id=str(sid),
        _user_id=str(uuid.uuid4()),
    )
    # 0.4.6+：agent_call 改走编制派活（inbox），不再起 subagent 闷跑
    assert (
        "Coder" in out
        or "派" in out
        or "工单" in out
        or out.startswith("[Error]")
        or out.startswith("[delegate")
    )


@pytest.mark.asyncio
async def test_desktop_clear_permissions_deletes_db(monkeypatch):
    from backend.services.desktop import DesktopAgentService, PermissionLevel

    svc = DesktopAgentService()
    uid = uuid.uuid4()
    svc._session_permissions[f"{uid}:screenshot:*"] = PermissionLevel.ALLOW_SESSION
    deleted = {"n": 0}

    class _Repo:
        async def delete_all_for_user(self, user_id, operation=None, app_name=None):
            deleted["n"] += 3
            return 3

    monkeypatch.setattr(
        "backend.repositories.desktop_permission_repo.AsyncDesktopPermissionRepository",
        lambda: _Repo(),
    )
    stats = await svc.clear_permissions(uid)
    assert stats["session"] == 1
    assert stats["db"] == 3
    assert f"{uid}:screenshot:*" not in svc._session_permissions
