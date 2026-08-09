"""
免 API Key 网络搜索瀑布（开源/公开端点）。

优先级：
1. ddgs 库（backend=auto → bing → duckduckgo → …）— 无配置
2. DuckDuckGo Lite HTML
3. Bing HTML / DuckDuckGo HTML（aiohttp 解析）
4. Wikipedia OpenSearch（en/zh）+ 摘要

Tavily/Brave 等需 Key 的通道不在此模块；由调用方可选叠加。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.parse
from html import unescape
from typing import Any

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_BOT_UA = "TevarnSearch/1.0 (+https://github.com/wu1w/takton; research agent)"


def _fmt(results: list[dict[str, str]], query: str, engine: str) -> str:
    if not results:
        return ""
    lines = [f"# Search: {query} ({engine})"]
    for i, r in enumerate(results, 1):
        title = (r.get("title") or "").strip() or "No title"
        url = (r.get("url") or r.get("href") or "").strip()
        body = (r.get("body") or r.get("snippet") or r.get("content") or "").strip()
        if len(body) > 280:
            body = body[:280] + "…"
        lines.append(f"{i}. {title}\n   {url}\n   {body}")
    return "\n".join(lines)


def _has_cjk(s: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", s or ""))


def _native_dep_broken() -> str | None:
    """Return reason if a present native dep is unusable (wrong ABI / half install).

    One-click packs often keep AppData python-packages across upgrades; a
    leftover cp311 lxml on Python 3.12 imports as a package but crashes on
    etree — ddgs then fails every backend. Detect early so HTML fallbacks run.

    Also detect half-installed primp (only .pyd, missing __init__.py) which
    imports as an empty namespace without Client.
    """
    for mod in ("lxml.etree", "primp"):
        try:
            m = __import__(mod)
        except ImportError:
            continue
        except Exception as e:
            return f"{mod}:{type(e).__name__}:{e}"
        if mod == "primp" and not hasattr(m, "Client"):
            return "primp:AttributeError:module 'primp' has no attribute 'Client'"
    return None


def _import_ddgs():
    """Import DDGS from ddgs (new) or duckduckgo_search (legacy rename)."""
    broken = _native_dep_broken()
    if broken:
        raise ImportError(f"native dep broken ({broken}); use HTML fallbacks") from None
    try:
        from ddgs import DDGS  # type: ignore

        return DDGS, "ddgs"
    except Exception:
        pass
    try:
        from duckduckgo_search import DDGS  # type: ignore

        return DDGS, "duckduckgo_search"
    except Exception as e:
        raise ImportError(
            "Neither 'ddgs' nor 'duckduckgo_search' is available "
            "(desktop will auto-repair on restart; HTML fallbacks still work)."
        ) from e


async def search_ddgs(query: str, max_results: int = 5) -> tuple[list[dict[str, str]], str]:
    """ddgs / duckduckgo_search：无 Key。按 backend 依次试。"""
    try:
        DDGS, pkg = _import_ddgs()
    except ImportError as e:
        logger.info("ddgs unavailable: %s", e)
        return [], "ddgs-skip"

    def _run(backend: str) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        # DDGS is both context manager and callable depending on version
        client = DDGS()
        try:
            if hasattr(client, "__enter__"):
                ddg = client.__enter__()
            else:
                ddg = client
            try:
                kwargs: dict[str, Any] = {"max_results": max_results}
                # older duckduckgo_search used backend=; newer ddgs same
                if backend and backend != "default":
                    kwargs["backend"] = backend
                rows = list(ddg.text(query, **kwargs))
            finally:
                if hasattr(client, "__exit__"):
                    try:
                        client.__exit__(None, None, None)
                    except Exception:
                        pass
        except TypeError:
            # Some versions: DDGS().text(query, max_results=n) without backend
            with DDGS() as ddg:
                rows = list(ddg.text(query, max_results=max_results))
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            out.append(
                {
                    "title": str(row.get("title") or ""),
                    "url": str(row.get("href") or row.get("link") or row.get("url") or ""),
                    "body": str(
                        row.get("body") or row.get("description") or row.get("snippet") or ""
                    ),
                }
            )
        return [r for r in out if r.get("url") or r.get("title")]

    # ddgs≥9 removed "bing"; use auto/brave first. Cap list to fail fast to HTML.
    backends = ["auto", "brave", "duckduckgo"]
    errors: list[str] = []
    for b in backends:
        try:
            rows = await asyncio.wait_for(asyncio.to_thread(_run, b), timeout=10)
            if rows:
                return rows[:max_results], f"{pkg}/{b}"
        except Exception as e:
            msg = f"{b}:{type(e).__name__}:{e}"
            errors.append(msg)
            # ABI / native crash → stop trying other ddgs backends
            low = str(e).lower()
            if "etree" in low or "dll" in low or "module" in low and "lxml" in low:
                logger.info("ddgs native failure, skip remaining backends: %s", e)
                break
            logger.debug("ddgs %s failed: %s", b, e)
            continue
    if errors:
        logger.info("ddgs all failed: %s", errors[:6])
    return [], pkg


async def search_ddg_lite(query: str, max_results: int = 5) -> tuple[list[dict[str, str]], str]:
    import aiohttp

    url = "https://lite.duckduckgo.com/lite/?" + urllib.parse.urlencode({"q": query})
    headers = {"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            html = await resp.text()
            if resp.status >= 400:
                return [], "ddg-lite"

    results: list[dict[str, str]] = []
    link_re = re.compile(
        r"<a[^>]+rel=['\"]nofollow['\"][^>]+href=['\"](https?://[^'\"]+)['\"][^>]*>(.*?)</a>",
        re.I | re.S,
    )
    if not link_re.search(html):
        link_re = re.compile(
            r"<a[^>]+class=['\"]result-link['\"][^>]+href=['\"](https?://[^'\"]+)['\"][^>]*>(.*?)</a>",
            re.I | re.S,
        )
    seen: set[str] = set()
    for m in link_re.finditer(html):
        href = unescape(m.group(1))
        if "duckduckgo.com" in href:
            continue
        if href in seen:
            continue
        seen.add(href)
        title = re.sub(r"<[^>]+>", "", unescape(m.group(2))).strip()
        results.append({"title": title, "url": href, "body": ""})
        if len(results) >= max_results:
            break

    snips = re.findall(
        r"class=['\"]result-snippet['\"][^>]*>(.*?)</(?:td|span|div)",
        html,
        flags=re.I | re.S,
    )
    for i, sn in enumerate(snips):
        if i < len(results):
            results[i]["body"] = re.sub(r"<[^>]+>", "", unescape(sn)).strip()[:280]

    return results, "ddg-lite"


async def search_ddg_html(query: str, max_results: int = 5) -> tuple[list[dict[str, str]], str]:
    import aiohttp

    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    headers = {"User-Agent": _UA}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            html = await resp.text()

    results: list[dict[str, str]] = []
    blocks = re.split(r'<div class="result[^"]*"[^>]*>', html)[1:]
    for block in blocks[: max_results * 2]:
        title_match = re.search(
            r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            block,
            re.DOTALL,
        )
        if not title_match:
            title_match = re.search(
                r'<a[^>]*class="result__a"[^>]*>(.*?)</a>',
                block,
                re.DOTALL,
            )
            href = ""
            title = ""
            if title_match:
                title = re.sub(r"<[^>]+>", "", unescape(title_match.group(1))).strip()
            url_match = re.search(r'href="(https?://[^"]+)"', block)
            href = unescape(url_match.group(1)) if url_match else ""
        else:
            href = unescape(title_match.group(1))
            title = re.sub(r"<[^>]+>", "", unescape(title_match.group(2))).strip()
        if "uddg=" in href:
            try:
                href = urllib.parse.unquote(
                    re.search(r"uddg=([^&]+)", href).group(1)  # type: ignore[union-attr]
                )
            except Exception:
                pass
        snip_m = re.search(
            r'class="result__snippet"[^>]*>(.*?)</(?:a|td|div)',
            block,
            re.DOTALL | re.I,
        )
        snip = (
            re.sub(r"<[^>]+>", "", unescape(snip_m.group(1))).strip() if snip_m else ""
        )
        if title or href:
            results.append({"title": title, "url": href, "body": snip})
        if len(results) >= max_results:
            break
    return results, "ddg-html"


async def search_bing_html(query: str, max_results: int = 5) -> tuple[list[dict[str, str]], str]:
    import aiohttp

    url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": query})
    headers = {"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            html = await resp.text()

    results: list[dict[str, str]] = []
    blocks = re.split(r'<li class="b_algo"[^>]*>', html)[1:]
    for block in blocks[:max_results]:
        title_match = re.search(
            r'<h2[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            block,
            re.DOTALL,
        )
        if not title_match:
            continue
        href = unescape(title_match.group(1))
        title = re.sub(r"<[^>]+>", "", unescape(title_match.group(2))).strip()
        snip_m = re.search(r"<p[^>]*>(.*?)</p>", block, re.DOTALL)
        snip = (
            re.sub(r"<[^>]+>", "", unescape(snip_m.group(1))).strip() if snip_m else ""
        )
        results.append({"title": title, "url": href, "body": snip})
    return results, "bing-html"


async def search_wikipedia(query: str, max_results: int = 5) -> tuple[list[dict[str, str]], str]:
    import aiohttp

    lang = "zh" if _has_cjk(query) else "en"
    bases = (
        [f"https://{lang}.wikipedia.org", "https://en.wikipedia.org"]
        if lang == "zh"
        else ["https://en.wikipedia.org", "https://zh.wikipedia.org"]
    )
    headers = {"User-Agent": _BOT_UA, "Accept": "application/json"}

    async with aiohttp.ClientSession() as session:
        for base in bases:
            try:
                api = base + "/w/api.php?" + urllib.parse.urlencode(
                    {
                        "action": "opensearch",
                        "search": query,
                        "limit": max_results,
                        "namespace": 0,
                        "format": "json",
                    }
                )
                async with session.get(
                    api, headers=headers, timeout=aiohttp.ClientTimeout(total=12)
                ) as resp:
                    text = await resp.text()
                    if resp.status >= 400:
                        continue
                    data = json.loads(text)
                titles = data[1] if len(data) > 1 else []
                descs = data[2] if len(data) > 2 else []
                urls = data[3] if len(data) > 3 else []
                results = []
                for i, title in enumerate(titles):
                    results.append(
                        {
                            "title": title,
                            "url": urls[i] if i < len(urls) else "",
                            "body": descs[i] if i < len(descs) else "",
                        }
                    )
                if results:
                    try:
                        t0 = urllib.parse.quote(results[0]["title"].replace(" ", "_"))
                        sum_url = f"{base}/api/rest_v1/page/summary/{t0}"
                        async with session.get(
                            sum_url,
                            headers=headers,
                            timeout=aiohttp.ClientTimeout(total=10),
                        ) as r2:
                            if r2.status < 400:
                                js = json.loads(await r2.text())
                                extract = (js.get("extract") or "")[:400]
                                if extract:
                                    results[0]["body"] = extract
                    except Exception:
                        pass
                if results:
                    return results[:max_results], f"wikipedia/{base.split('//')[1].split('.')[0]}"
            except Exception as e:
                logger.debug("wiki %s failed: %s", base, e)
                continue
    return [], "wikipedia"


async def free_web_search(query: str, max_results: int = 5) -> str:
    """免 Key 瀑布（短超时）。适配一键包多网络环境。

    顺序原则：
    1. ddgs（若原生依赖 ABI 完好）
    2. 纯 regex HTML 通道（无 lxml，CN 友好：Bing → DDG Lite）
    3. Wikipedia OpenSearch（JSON，防火墙友好、无 Key）
    """
    q = (query or "").strip()
    if not q:
        return "[Error] query is required"
    n = max(1, min(int(max_results or 5), 15))
    errors: list[str] = []

    # CJK queries: Bing HTML + Wikipedia-zh usually beat DDG from mainland CN.
    # Non-CJK: keep ddgs first when healthy.
    cjk = _has_cjk(q)
    if cjk:
        pipeline = [
            ("ddgs", search_ddgs),
            ("bing-html", search_bing_html),
            ("wikipedia", search_wikipedia),
            ("ddg-lite", search_ddg_lite),
            ("ddg-html", search_ddg_html),
        ]
    else:
        pipeline = [
            ("ddgs", search_ddgs),
            ("ddg-lite", search_ddg_lite),
            ("bing-html", search_bing_html),
            ("ddg-html", search_ddg_html),
            ("wikipedia", search_wikipedia),
        ]
    for name, fn in pipeline:
        try:
            rows, engine = await fn(q, n)
            if rows:
                text = _fmt(rows, q, engine)
                if text:
                    return text
            errors.append(f"{name}: empty")
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}: {e}")
            logger.info("free_search %s error: %s", name, e)

    return (
        f"[Error] free web_search failed for «{q}».\n"
        f"tried: {'; '.join(errors)}\n"
        "Hint: check network / proxy; restart Tevarn once to auto-repair "
        "search packages (desktop one-click). HTML/Wikipedia fallbacks need no API key."
    )


async def free_web_search_structured(
    query: str, max_results: int = 5
) -> dict[str, Any]:
    """结构化结果（可选）。"""
    text = await free_web_search(query, max_results)
    return {"text": text, "ok": not text.startswith("[Error]")}
