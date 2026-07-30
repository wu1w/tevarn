"""Agent 联网工具（http / browser）的分层准入。

本地优先在这里**真的改变了正确答案**：服务端 SSRF 防护拦一切私网+回环，
但让 Agent 看一眼跑在 localhost:3000 的项目、读 NAS、调路由器接口，
是这类产品的核心价值。照搬会把产品拦死。

所以分两层：
  硬拦  —— 云元数据端点（个人设备上永无正当用途，泄露的是云凭证）
  放行  —— 私网/回环（记审计，可用 agent_block_private_network 收紧）
"""

import pytest

from backend.core.net_safety import check_agent_url

# ── 必须放行：本地优先的核心用法 ─────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:3000",  # 自己在开发的前端
        "http://127.0.0.1:8080/api/health",  # 自己跑的服务
        "http://192.168.1.100:5000",  # NAS / 局域网服务
        "http://10.0.0.5",  # 内网
        "http://[::1]:3000",  # IPv6 回环
        "https://example.com",  # 普通外网
        "https://api.github.com/repos/x/y",
    ],
)
def test_normal_local_first_usage_allowed(url):
    allowed, note = check_agent_url(url)
    assert allowed is True, f"{url} 被拦了，会毁掉本地优先的核心用法：{note}"


def test_private_access_is_audited_not_silent():
    """私网放行，但要留痕 —— 事后能查『Agent 那天摸了内网的什么』。"""
    allowed, note = check_agent_url("http://192.168.1.100:5000")
    assert allowed is True
    assert note, "私网访问应返回审计说明"
    assert "internal-network" in note

    allowed, note = check_agent_url("https://example.com")
    assert allowed is True
    assert note == "", "普通外网访问不需要额外留痕"


# ── 必须硬拦：云元数据端点 ───────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "http://169.254.169.254",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://100.100.100.200/latest/meta-data/",  # 阿里云
        "http://[fd00:ec2::254]/latest/meta-data/",
        "http://instance-data/latest/meta-data/",
    ],
)
def test_cloud_metadata_always_blocked(url):
    allowed, note = check_agent_url(url)
    assert allowed is False, f"{url} 必须硬拦"
    assert "metadata" in note.lower()


def test_metadata_block_survives_private_network_being_allowed(monkeypatch):
    """169.254.169.254 本身就是链路本地地址；不能因为『私网放行』而漏掉它。"""
    from backend.core.config import settings

    monkeypatch.setattr(settings, "agent_block_private_network", False, raising=False)
    allowed, _ = check_agent_url("http://169.254.169.254/latest/meta-data/")
    assert allowed is False


# ── scheme / 格式 ───────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "ftp://x.com", "gopher://x", "", "http://"],
)
def test_bad_schemes_rejected(url):
    allowed, _ = check_agent_url(url)
    assert allowed is False


# ── 严格模式：给跑在服务器上的人 ─────────────────────────────


def test_strict_mode_blocks_private(monkeypatch):
    from backend.core.config import settings

    monkeypatch.setattr(settings, "agent_block_private_network", True, raising=False)
    allowed, note = check_agent_url("http://192.168.1.100:5000")
    assert allowed is False
    assert "agent_block_private_network" in note

    # 外网仍然通
    allowed, _ = check_agent_url("https://example.com")
    assert allowed is True


# ── 工具层真的接上了 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_http_tool_blocks_metadata():
    from backend.services.tools.executors import execute_http

    out = await execute_http({}, {"url": "http://169.254.169.254/latest/meta-data/"})
    assert "Security Blocked" in out


@pytest.mark.asyncio
async def test_browser_fetch_blocks_metadata():
    from backend.services.tools.executors import execute_browser

    out = await execute_browser(
        {}, {"action": "fetch", "url": "http://metadata.google.internal/"}
    )
    assert "Security Blocked" in out
