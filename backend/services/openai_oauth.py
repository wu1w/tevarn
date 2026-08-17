"""
OpenAI ChatGPT OAuth（Codex / 订阅额度）

对齐 Codex CLI / Cline / OpenClaw 使用的公开 OAuth 客户端：
- 浏览器登录 ChatGPT Plus/Pro
- PKCE 授权码换 access_token + refresh_token
- 后续以 Bearer 访问 ChatGPT Codex 后端（订阅计费，非 platform API 按量）

回调：Codex 固定 redirect = http://localhost:1455/auth/callback
本模块会在 start 时临时监听 1455，浏览器跳转后自动换 token（前端再 poll 激活供应商）。
若 1455 被占用，仍可粘贴地址栏完整 URL 手动 complete。

说明：
- 额度受 ChatGPT 订阅公平使用限制（Codex 档位），不是无限 API。
- 不要与 OPENAI_API_KEY（platform 按量）混用；激活本供应商后会用 OAuth 令牌。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import aiohttp
from aiohttp import web

logger = logging.getLogger(__name__)

# Codex CLI 公开 client（与业界工具一致）
OPENAI_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OPENAI_OAUTH_AUTH_URL = "https://auth.openai.com/oauth/authorize"
OPENAI_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
# Codex 注册的回调；浏览器完成登录后会跳到此 URL
OPENAI_OAUTH_REDIRECT_URI = "http://localhost:1455/auth/callback"
OPENAI_OAUTH_CALLBACK_HOST = "127.0.0.1"
OPENAI_OAUTH_CALLBACK_PORT = 1455
OPENAI_OAUTH_SCOPE = "openid email profile offline_access"

# 订阅后端（Codex 路径）；经本地代理转成 /v1/chat/completions
OPENAI_CODEX_UPSTREAM = "https://chatgpt.com/backend-api/codex"
# 本地代理 base（写入供应商 llm_base_url）
OPENAI_CODEX_LOCAL_BASE = "http://127.0.0.1:8090/api/llm-proxy/openai-codex/v1"

_pending: dict[str, dict[str, Any]] = {}
# 回调/换 token 结果（供前端 poll 激活目录）
_login_results: dict[str, dict[str, Any]] = {}
_callback_runner: web.AppRunner | None = None
_callback_site: web.TCPSite | None = None
_callback_lock = asyncio.Lock()
_callback_started_at: float = 0.0


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _pkce_pair() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _cleanup_pending() -> None:
    now = time.time()
    for k, v in list(_pending.items()):
        if now - float(v.get("created_at") or 0) > 900:
            _pending.pop(k, None)


def start_pkce_login() -> dict[str, Any]:
    """生成 PKCE 授权链接（用户浏览器打开）。不启监听；请用 async start_pkce_login_async。"""
    _cleanup_pending()
    _cleanup_results()
    state = secrets.token_urlsafe(24)
    verifier, challenge = _pkce_pair()
    _pending[state] = {
        "created_at": time.time(),
        "code_verifier": verifier,
        "redirect_uri": OPENAI_OAUTH_REDIRECT_URI,
    }
    params = {
        "response_type": "code",
        "client_id": OPENAI_OAUTH_CLIENT_ID,
        "redirect_uri": OPENAI_OAUTH_REDIRECT_URI,
        "scope": OPENAI_OAUTH_SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
    }
    auth_url = f"{OPENAI_OAUTH_AUTH_URL}?{urlencode(params)}"
    return {
        "ok": True,
        "state": state,
        "authorization_url": auth_url,
        "redirect_uri": OPENAI_OAUTH_REDIRECT_URI,
        "expires_in": 600,
        "callback_listening": False,
        "message": (
            "请在浏览器登录 ChatGPT 并授权。授权后会跳到 localhost:1455；"
            "若本机已监听则自动完成，否则请复制地址栏完整 URL 粘贴回来。"
        ),
    }


def _cleanup_results() -> None:
    now = time.time()
    for k, v in list(_login_results.items()):
        created = float(v.get("_stored_at") or 0)
        if created and now - created > 900:
            _login_results.pop(k, None)
        # 已消费的授权结果尽快清理
        if v.get("_consumed") and created and now - created > 120:
            _login_results.pop(k, None)


def _store_result(state: str, result: dict[str, Any]) -> None:
    payload = dict(result)
    payload["_stored_at"] = time.time()
    payload["_consumed"] = False
    if state:
        _login_results[state] = payload
    _login_results["__last__"] = payload


def poll_login_result(state: str | None = None) -> dict[str, Any]:
    """前端轮询：是否已通过 1455 回调换好 token。"""
    _cleanup_results()
    key = (state or "").strip() or "__last__"
    result = _login_results.get(key) or (
        _login_results.get("__last__") if key != "__last__" else None
    )
    if not result:
        return {
            "ok": True,
            "status": "pending",
            "message": "等待浏览器授权回调…",
        }
    if result.get("_consumed"):
        return {
            "ok": True,
            "status": "pending",
            "message": "结果已消费，请重新登录",
        }
    if result.get("ok") and result.get("access_token"):
        return {
            "ok": True,
            "status": "authorized",
            "message": result.get("message") or "授权成功",
            "access_token": result["access_token"],
            "refresh_token": result.get("refresh_token") or "",
            "expires_at": result.get("expires_at") or "",
            "expires_in": result.get("expires_in"),
            "account_id": result.get("account_id") or "",
            "base_url": result.get("base_url") or OPENAI_CODEX_LOCAL_BASE,
            "state": key if key != "__last__" else state,
        }
    return {
        "ok": False,
        "status": "error",
        "message": result.get("message") or "授权失败",
        "detail": result.get("detail"),
    }


def mark_result_consumed(state: str | None = None) -> None:
    key = (state or "").strip()
    for k in (key, "__last__"):
        if k and k in _login_results:
            _login_results[k]["_consumed"] = True


def _html_page(title: str, body: str, *, ok: bool) -> str:
    color = "#16a34a" if ok else "#dc2626"
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; background: #0b1220; color: #e5e7eb;
           display:flex; align-items:center; justify-content:center; min-height:100vh; margin:0; }}
    .card {{ max-width: 28rem; padding: 1.75rem 1.5rem; border-radius: 1rem;
             background: #111827; border: 1px solid #1f2937; box-shadow: 0 10px 40px rgba(0,0,0,.4); }}
    h1 {{ font-size: 1.15rem; margin: 0 0 .75rem; color: {color}; }}
    p {{ margin: .4rem 0; line-height: 1.55; font-size: .92rem; color: #9ca3af; }}
    code {{ font-size: .8rem; color: #93c5fd; word-break: break-all; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>{html.escape(title)}</h1>
    {body}
  </div>
</body>
</html>"""


async def _handle_oauth_callback(request: web.Request) -> web.Response:
    """浏览器跳到 localhost:1455/auth/callback 时处理。"""
    qs = request.rel_url.query
    code = (qs.get("code") or "").strip()
    state = (qs.get("state") or "").strip()
    err = (qs.get("error") or "").strip()
    err_desc = (qs.get("error_description") or "").strip()

    if err:
        msg = err_desc or err
        _store_result(state, {"ok": False, "message": f"授权被拒绝: {msg}"})
        page = _html_page(
            "授权失败",
            f"<p>{html.escape(msg)}</p><p>请回到 Tevarn 设置页重试。</p>",
            ok=False,
        )
        return web.Response(text=page, content_type="text/html", charset="utf-8")

    if not code:
        page = _html_page(
            "缺少授权码",
            "<p>回调 URL 里没有 <code>code</code>。请回到 Tevarn 重新点「ChatGPT 登录」。</p>",
            ok=False,
        )
        return web.Response(text=page, content_type="text/html", charset="utf-8")

    # 拼完整回调 URL，复用 complete 逻辑
    callback_url = str(request.url)
    # 若 host 是 127.0.0.1，规范成 redirect_uri 同款 localhost（token 端只校验 redirect_uri 参数，不校验此处）
    try:
        result = await complete_pkce_login(callback_url, state=state or None)
    except Exception as e:
        logger.exception("oauth callback complete failed")
        result = {"ok": False, "message": str(e)}

    _store_result(state or "", result)

    if result.get("ok"):
        page = _html_page(
            "ChatGPT 登录成功",
            "<p>订阅额度 OAuth 已完成。请回到 <strong>Tevarn</strong> 设置页"
            "（会自动激活供应商），本页可关闭。</p>"
            "<p style='margin-top:1rem;font-size:.8rem'>若设置页未自动更新，点一次刷新即可。</p>",
            ok=True,
        )
        # 成功后稍后停监听，避免长期占端口
        asyncio.create_task(_stop_callback_server_later(8.0))
        return web.Response(text=page, content_type="text/html", charset="utf-8")

    msg = str(result.get("message") or "换取令牌失败")
    page = _html_page(
        "登录未完成",
        f"<p>{html.escape(msg)}</p>"
        "<p>也可复制地址栏完整 URL，粘贴回 Tevarn「完成登录」。</p>"
        f"<p><code>{html.escape(callback_url[:220])}</code></p>",
        ok=False,
    )
    return web.Response(text=page, content_type="text/html", charset="utf-8")


async def _stop_callback_server_later(delay: float) -> None:
    try:
        await asyncio.sleep(delay)
        await stop_callback_server()
    except Exception:
        pass


async def stop_callback_server() -> None:
    global _callback_runner, _callback_site, _callback_started_at
    async with _callback_lock:
        site, runner = _callback_site, _callback_runner
        _callback_site = None
        _callback_runner = None
        _callback_started_at = 0.0
    if site is not None:
        try:
            await site.stop()
        except Exception:
            pass
    if runner is not None:
        try:
            await runner.cleanup()
        except Exception:
            pass
    logger.info("openai oauth callback server stopped")


async def ensure_callback_server() -> dict[str, Any]:
    """在 127.0.0.1:1455 监听 /auth/callback（Codex redirect）。"""
    global _callback_runner, _callback_site, _callback_started_at
    async with _callback_lock:
        if _callback_site is not None and _callback_runner is not None:
            return {"ok": True, "listening": True, "port": OPENAI_OAUTH_CALLBACK_PORT}

        app = web.Application()
        app.router.add_get("/auth/callback", _handle_oauth_callback)
        app.router.add_get("/auth/callback/", _handle_oauth_callback)

        async def _root(_request: web.Request) -> web.Response:
            page = _html_page(
                "Tevarn ChatGPT OAuth",
                "<p>回调服务已就绪。请在授权页完成登录，浏览器会自动跳转到本服务。</p>",
                ok=True,
            )
            return web.Response(text=page, content_type="text/html", charset="utf-8")

        app.router.add_get("/", _root)

        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(
            runner,
            OPENAI_OAUTH_CALLBACK_HOST,
            OPENAI_OAUTH_CALLBACK_PORT,
            reuse_address=True,
        )
        try:
            await site.start()
        except OSError as e:
            await runner.cleanup()
            logger.warning("cannot bind :%s for oauth callback: %s", OPENAI_OAUTH_CALLBACK_PORT, e)
            return {
                "ok": False,
                "listening": False,
                "port": OPENAI_OAUTH_CALLBACK_PORT,
                "message": (
                    f"无法监听 {OPENAI_OAUTH_CALLBACK_PORT} 端口（{e}）。"
                    "授权后请复制地址栏完整 URL 粘贴回 Tevarn。"
                ),
            }

        _callback_runner = runner
        _callback_site = site
        _callback_started_at = time.time()
        logger.info(
            "openai oauth callback listening on http://%s:%s/auth/callback",
            OPENAI_OAUTH_CALLBACK_HOST,
            OPENAI_OAUTH_CALLBACK_PORT,
        )
        # 10 分钟无人回调则自动停
        asyncio.create_task(_stop_callback_server_later(600.0))
        return {"ok": True, "listening": True, "port": OPENAI_OAUTH_CALLBACK_PORT}


async def start_pkce_login_async() -> dict[str, Any]:
    """生成授权链接并尽量启动 1455 回调监听。"""
    info = start_pkce_login()
    listen = await ensure_callback_server()
    info["callback_listening"] = bool(listen.get("listening"))
    if listen.get("listening"):
        info["message"] = (
            "已打开登录页。授权后浏览器会跳到 localhost:1455 并自动完成；"
            "请回到 Tevarn 等待「登录成功」（也可关闭回调页）。"
        )
    elif listen.get("message"):
        info["message"] = str(listen["message"])
        info["callback_error"] = listen.get("message")
    return info


def _parse_callback(callback_url: str) -> dict[str, str]:
    from urllib.parse import parse_qs, urlparse

    raw = (callback_url or "").strip()
    if not raw:
        return {}
    # 允许只贴 query 或完整 URL
    if raw.startswith("code=") or ("code=" in raw and "://" not in raw):
        if not raw.startswith("?"):
            raw = "?" + raw
        raw = "http://localhost/cb" + raw
    q = parse_qs(urlparse(raw).query)
    out: dict[str, str] = {}
    for k, vals in q.items():
        if vals:
            out[k] = vals[0]
    return out


async def complete_pkce_login(callback_url: str, *, state: str | None = None) -> dict[str, Any]:
    """用回调 URL 中的 code 换 token。"""
    parsed = _parse_callback(callback_url)
    code = (parsed.get("code") or "").strip()
    st = (parsed.get("state") or state or "").strip()
    if not code:
        return {"ok": False, "message": "回调里没有 code，请粘贴完整 URL"}
    if not st or st not in _pending:
        return {
            "ok": False,
            "message": "state 无效或已过期，请重新点击「ChatGPT 登录」",
        }
    meta = _pending.pop(st)
    verifier = str(meta.get("code_verifier") or "")
    redirect_uri = str(meta.get("redirect_uri") or OPENAI_OAUTH_REDIRECT_URI)

    from backend.core.outbound_http import format_openai_geo_error, outbound_session

    try:
        async with outbound_session(timeout=aiohttp.ClientTimeout(total=45)) as (
            session,
            proxy,
        ):
            async with session.post(
                OPENAI_OAUTH_TOKEN_URL,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                data={
                    "grant_type": "authorization_code",
                    "client_id": OPENAI_OAUTH_CLIENT_ID,
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": verifier,
                },
                proxy=proxy,
            ) as resp:
                text = await resp.text()
                try:
                    payload = await resp.json(content_type=None) if text else {}
                except Exception:
                    payload = {}
                if resp.status != 200 or not payload.get("access_token"):
                    return {
                        "ok": False,
                        "message": format_openai_geo_error(resp.status, payload, text),
                        "detail": text[:400],
                    }
    except RuntimeError as e:
        return {"ok": False, "message": str(e)}
    except Exception as e:
        from backend.core.outbound_http import format_proxy_hint

        return {
            "ok": False,
            "message": (
                f"连接 auth.openai.com 失败: {e}。"
                f"{format_proxy_hint()}"
            ),
        }

    access = str(payload["access_token"])
    refresh = str(payload.get("refresh_token") or "")
    expires_in = int(payload.get("expires_in") or 3600)
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=max(60, expires_in - 60))
    ).isoformat()
    account_id = _extract_account_id(payload, access)

    return {
        "ok": True,
        "status": "authorized",
        "access_token": access,
        "refresh_token": refresh,
        "expires_at": expires_at,
        "expires_in": expires_in,
        "account_id": account_id or "",
        "base_url": OPENAI_CODEX_LOCAL_BASE,
        "upstream": OPENAI_CODEX_UPSTREAM,
        "message": "ChatGPT OAuth 登录成功（订阅额度 · Codex 路径）",
    }


def _extract_account_id(payload: dict[str, Any], access_token: str) -> str | None:
    for key in ("chatgpt_account_id", "account_id", "https://api.openai.com/auth.chatgpt_account_id"):
        v = payload.get(key)
        if v:
            return str(v)
    # JWT payload 启发式
    try:
        parts = access_token.split(".")
        if len(parts) >= 2:
            pad = "=" * (-len(parts[1]) % 4)
            data = base64.urlsafe_b64decode(parts[1] + pad)
            import json

            claims = json.loads(data.decode("utf-8", errors="replace"))
            for k in (
                "https://api.openai.com/auth",
                "chatgpt_account_id",
                "account_id",
                "org_id",
            ):
                c = claims.get(k)
                if isinstance(c, dict):
                    aid = c.get("chatgpt_account_id") or c.get("account_id")
                    if aid:
                        return str(aid)
                elif c and k != "https://api.openai.com/auth":
                    return str(c)
    except Exception:
        pass
    return None


async def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    if not refresh_token or not str(refresh_token).strip():
        return {"ok": False, "message": "缺少 refresh_token，请重新 ChatGPT 登录"}
    from backend.core.outbound_http import format_openai_geo_error, outbound_session

    try:
        async with outbound_session(timeout=aiohttp.ClientTimeout(total=45)) as (
            session,
            proxy,
        ):
            async with session.post(
                OPENAI_OAUTH_TOKEN_URL,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                data={
                    "grant_type": "refresh_token",
                    "client_id": OPENAI_OAUTH_CLIENT_ID,
                    "refresh_token": refresh_token.strip(),
                },
                proxy=proxy,
            ) as resp:
                text = await resp.text()
                try:
                    payload = await resp.json(content_type=None) if text else {}
                except Exception:
                    payload = {}
                if resp.status != 200 or not payload.get("access_token"):
                    return {
                        "ok": False,
                        "message": format_openai_geo_error(resp.status, payload, text),
                        "detail": text[:300],
                    }
                expires_in = int(payload.get("expires_in") or 3600)
                expires_at = (
                    datetime.now(timezone.utc)
                    + timedelta(seconds=max(60, expires_in - 60))
                ).isoformat()
                return {
                    "ok": True,
                    "access_token": str(payload["access_token"]),
                    "refresh_token": str(payload.get("refresh_token") or refresh_token),
                    "expires_at": expires_at,
                    "expires_in": expires_in,
                    "account_id": _extract_account_id(
                        payload, str(payload["access_token"])
                    )
                    or "",
                }
    except RuntimeError as e:
        return {"ok": False, "message": str(e)}
    except Exception as e:
        return {"ok": False, "message": f"刷新令牌失败: {e}"}


def token_needs_refresh(expires_at: str | None, skew_seconds: int = 120) -> bool:
    if not expires_at:
        return False
    try:
        exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= exp - timedelta(seconds=skew_seconds)
    except Exception:
        return False
