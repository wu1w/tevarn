"""跨源准入 —— 本地优先场景下最关键的一道网络边界。

威胁模型不是「远程攻击者扫端口」，而是：**用户自己打开了一个恶意网页**。
那个网页发往 127.0.0.1 的请求，在后端看来对端就是 loopback，于是
single_user_mode 的免登录放行会把它当 admin。唯一能拦住它的就是 Origin 检查。

因此这里既要证明「恶意源被拒」，也要证明「本地正常用法一个都没被误伤」。
"""

import pytest

from backend.core.simple_cors import is_origin_allowed

# ── 必须放行：本地优先的全部正常入口 ─────────────────────────


@pytest.mark.parametrize(
    "origin",
    [
        "",  # curl / CLI / tevarn-code / Electron 主进程反代：不发 Origin
        "http://127.0.0.1:3000",  # Electron 内置静态服务器
        "http://127.0.0.1:8000",  # 单体模式（后端直接托管前端）
        "http://localhost:3000",  # next dev
        "http://localhost:3001",  # next dev 端口被占时的备选
        "http://127.0.0.1:54321",  # Electron 随机端口
        "http://[::1]:3000",  # IPv6 loopback
        "https://localhost:3000",  # 本地 https 开发
    ],
)
def test_local_usage_is_never_broken(origin):
    assert is_origin_allowed(origin) is True, f"{origin} 是正常本地用法，不能被拦"


# ── 必须拒绝：浏览器可达的外部源 ─────────────────────────────


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.com",
        "http://evil.com",
        "https://tevarn.dev",  # 官网也不行 —— 没有例外
        "https://127.0.0.1.evil.com",  # 子域名前缀混淆
        "https://evil.com/127.0.0.1",  # 路径混淆
        "http://localhost.evil.com",  # 后缀混淆
        "http://192.168.1.50:3000",  # 局域网：默认不放行，需显式配置
        "null",  # file:// / 沙箱 iframe
        "file://",
    ],
)
def test_external_origins_are_rejected(origin):
    assert is_origin_allowed(origin) is False, f"{origin} 不该被放行"


# ── 显式配置的逃生口 ────────────────────────────────────────


def test_configured_origin_is_allowed(monkeypatch):
    from backend.core.config import settings

    monkeypatch.setattr(
        settings,
        "cors_allowed_origins",
        "https://tevarn.mylan.home http://192.168.1.9:3000",
        raising=False,
    )
    assert is_origin_allowed("https://tevarn.mylan.home") is True
    assert is_origin_allowed("http://192.168.1.9:3000") is True
    # 配了别人不等于放开所有
    assert is_origin_allowed("https://evil.com") is False


def test_trailing_slash_and_case_are_normalized(monkeypatch):
    from backend.core.config import settings

    monkeypatch.setattr(
        settings, "cors_allowed_origins", "https://Tevarn.MyLan.home/", raising=False
    )
    assert is_origin_allowed("https://tevarn.mylan.home") is True


def test_wildcard_is_opt_in_only(monkeypatch):
    from backend.core.config import settings

    assert is_origin_allowed("https://evil.com") is False
    monkeypatch.setattr(settings, "cors_allowed_origins", "*", raising=False)
    assert is_origin_allowed("https://evil.com") is True


# ── 中间件行为：拒绝要真拒绝，不是只省略响应头 ───────────────


@pytest.mark.asyncio
async def test_disallowed_origin_gets_403_not_just_missing_header():
    """简单请求（GET / 表单 POST）即使被浏览器挡住读响应，请求本身照样执行。

    只省略 Access-Control-Allow-Origin 挡不住副作用，必须在中间件层拒绝。
    """
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    hit = {"n": 0}

    async def _endpoint(request):
        hit["n"] += 1
        return PlainTextResponse("sensitive-data")

    from backend.core.simple_cors import SimpleCORSMiddleware

    app = Starlette(routes=[Route("/api/secret", _endpoint, methods=["GET", "POST"])])
    app.add_middleware(SimpleCORSMiddleware)
    client = TestClient(app)

    r = client.get("/api/secret", headers={"Origin": "https://evil.com"})
    assert r.status_code == 403
    assert "sensitive-data" not in r.text
    assert hit["n"] == 0, "请求不该到达路由处理器 —— 否则副作用已经发生"

    # 预检也要拒
    r = client.options("/api/secret", headers={"Origin": "https://evil.com"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_allowed_origin_round_trip():
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from backend.core.simple_cors import SimpleCORSMiddleware

    app = Starlette(
        routes=[Route("/api/ok", lambda r: PlainTextResponse("ok"), methods=["GET"])]
    )
    app.add_middleware(SimpleCORSMiddleware)
    client = TestClient(app)

    r = client.get("/api/ok", headers={"Origin": "http://localhost:3000"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert r.headers["access-control-allow-credentials"] == "true"
    assert r.headers["vary"] == "Origin"


@pytest.mark.asyncio
async def test_no_origin_client_gets_no_wildcard_acao():
    """ACAO: * 与 Allow-Credentials: true 组合是非法的，浏览器会直接拒绝。

    无 Origin 的客户端（curl / CLI）本来也不需要 CORS 头。
    """
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from backend.core.simple_cors import SimpleCORSMiddleware

    app = Starlette(
        routes=[Route("/api/ok", lambda r: PlainTextResponse("ok"), methods=["GET"])]
    )
    app.add_middleware(SimpleCORSMiddleware)
    client = TestClient(app)

    r = client.get("/api/ok")
    assert r.status_code == 200
    assert "access-control-allow-origin" not in r.headers
