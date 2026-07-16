"""
HTTP 端点自适应：Embedding / Reranker / Qdrant 多主流路径探测。

不把单一路径写死；首次成功后缓存 path，后续请求直接命中。
"""

from __future__ import annotations

import logging
from typing import Any, Callable
from urllib.parse import urlparse, urlunparse

import aiohttp

logger = logging.getLogger(__name__)


def normalize_base_url(url: str) -> str:
    """去掉末尾斜杠；若用户填了 .../v1 则去掉，便于再拼子路径。"""
    u = (url or "").strip().rstrip("/")
    if not u:
        return u
    # 去掉末尾 /v1（保留自定义完整 path 的情况由 caller 处理）
    if u.endswith("/v1"):
        u = u[:-3]
    return u.rstrip("/")


def base_candidates(url: str) -> list[str]:
    """生成候选 base（原样 + 去 /v1）。"""
    raw = (url or "").strip().rstrip("/")
    if not raw:
        return []
    out: list[str] = []
    for b in (raw, normalize_base_url(raw)):
        if b and b not in out:
            out.append(b)
    return out


def _parse_openai_embeddings(data: Any) -> list[list[float]] | None:
    if not isinstance(data, dict):
        return None
    items = data.get("data")
    if not isinstance(items, list) or not items:
        return None
    try:
        items = sorted(items, key=lambda x: x.get("index", 0) if isinstance(x, dict) else 0)
        vecs = []
        for it in items:
            if not isinstance(it, dict):
                return None
            emb = it.get("embedding")
            if not isinstance(emb, list) or not emb:
                return None
            vecs.append(emb)
        return vecs
    except Exception:
        return None


def _parse_ollama_embed(data: Any, n_inputs: int) -> list[list[float]] | None:
    if not isinstance(data, dict):
        return None
    embs = data.get("embeddings")
    if isinstance(embs, list) and embs and isinstance(embs[0], list):
        return embs
    single = data.get("embedding")
    if isinstance(single, list) and single and n_inputs == 1:
        return [single]
    return None


def _parse_tei_embed(data: Any) -> list[list[float]] | None:
    # TEI: [[...], [...]] 或 {"embeddings": [[...]]}
    if isinstance(data, list) and data and isinstance(data[0], list):
        return data
    if isinstance(data, list) and data and isinstance(data[0], (int, float)):
        return [data]
    if isinstance(data, dict):
        embs = data.get("embeddings")
        if isinstance(embs, list) and embs:
            if isinstance(embs[0], list):
                return embs
            if isinstance(embs[0], (int, float)):
                return [embs]
    return None


def parse_embedding_response(data: Any, n_inputs: int) -> list[list[float]] | None:
    for parser in (
        lambda d: _parse_openai_embeddings(d),
        lambda d: _parse_ollama_embed(d, n_inputs),
        lambda d: _parse_tei_embed(d),
    ):
        try:
            out = parser(data)
            if out and len(out) == n_inputs:
                return out
            # 允许 batch 长度不一致时仍有部分（宽松）
            if out and len(out) > 0 and n_inputs == 1:
                return [out[0]]
            if out and len(out) == n_inputs:
                return out
        except Exception:
            continue
    return None


# (path_suffix, payload_builder_name)
# path_suffix 拼在 base 后；空字符串表示 base 本身已是完整 endpoint
EMBED_ATTEMPTS: list[tuple[str, str]] = [
    ("/v1/embeddings", "openai"),
    ("/embeddings", "openai"),
    ("/v1/embed", "openai"),
    ("/api/embeddings", "ollama_legacy"),
    ("/api/embed", "ollama"),
    ("/embed", "tei"),
    ("/embedding", "tei"),
]


def build_embed_payload(kind: str, model: str, texts: list[str]) -> dict[str, Any]:
    if kind == "openai":
        return {"model": model, "input": texts if len(texts) > 1 else (texts[0] if len(texts) == 1 else texts)}
    if kind == "ollama":
        return {"model": model, "input": texts if len(texts) > 1 else texts[0]}
    if kind == "ollama_legacy":
        # 旧 Ollama /api/embeddings 单条
        return {"model": model, "prompt": texts[0] if texts else ""}
    if kind == "tei":
        return {"inputs": texts if len(texts) > 1 else texts[0]}
    return {"model": model, "input": texts}


def parse_ollama_legacy_embeddings(data: Any) -> list[list[float]] | None:
    if isinstance(data, dict) and isinstance(data.get("embedding"), list):
        return [data["embedding"]]
    return None


async def embed_with_fallback(
    session: aiohttp.ClientSession,
    base_url: str,
    model: str,
    texts: list[str],
    headers: dict[str, str],
    cached_url: str | None = None,
    cached_kind: str | None = None,
) -> tuple[list[list[float]], str, str]:
    """
    多路径探测 embedding。
    返回 (vectors, used_url, used_kind)
    """
    errors: list[str] = []

    async def _try(url: str, kind: str) -> list[list[float]] | None:
        payload = build_embed_payload(kind, model, texts)
        # openai 单条时部分服务要 list
        if kind == "openai" and isinstance(payload.get("input"), str):
            payload_list = {**payload, "input": texts}
        else:
            payload_list = payload
        for body in (payload_list, payload):
            try:
                async with session.post(url, json=body, headers=headers) as resp:
                    text = await resp.text()
                    if resp.status >= 400:
                        errors.append(f"{url} HTTP {resp.status}: {text[:120]}")
                        continue
                    try:
                        import json

                        data = json.loads(text)
                    except Exception:
                        errors.append(f"{url} non-json")
                        continue
                    vecs = parse_embedding_response(data, len(texts))
                    if vecs is None and kind == "ollama_legacy":
                        vecs = parse_ollama_legacy_embeddings(data)
                        if vecs and len(texts) > 1:
                            # legacy 只支持单条，逐条请求由上层处理
                            pass
                    if vecs:
                        return vecs
                    errors.append(f"{url} unparsed keys={list(data)[:8] if isinstance(data, dict) else type(data)}")
            except Exception as e:
                errors.append(f"{url} {e}")
        return None

    # 优先缓存
    if cached_url and cached_kind:
        vecs = await _try(cached_url, cached_kind)
        if vecs:
            return vecs, cached_url, cached_kind

    attempts: list[tuple[str, str]] = []
    for base in base_candidates(base_url):
        # 用户若直接填了完整 endpoint
        lower = base.lower()
        if any(lower.endswith(s) for s in ("/embeddings", "/embed", "/api/embed", "/api/embeddings")):
            attempts.append((base, "openai" if "embed" in lower else "openai"))
            attempts.append((base, "ollama"))
            attempts.append((base, "tei"))
        for suffix, kind in EMBED_ATTEMPTS:
            attempts.append((f"{base}{suffix}", kind))

    # 去重保持顺序
    seen: set[str] = set()
    for url, kind in attempts:
        key = f"{url}|{kind}"
        if key in seen:
            continue
        seen.add(key)
        vecs = await _try(url, kind)
        if vecs:
            logger.info("Embedding endpoint locked: %s (%s)", url, kind)
            return vecs, url, kind

    raise RuntimeError(
        "Embedding 所有候选端点均失败。已尝试 OpenAI /v1/embeddings、/embeddings、"
        "Ollama /api/embed、TEI /embed 等。详情: " + " | ".join(errors[:6])
    )


def parse_rerank_response(data: Any, documents: list[str]) -> list[tuple[int, float]] | None:
    """返回 (original_index, score) 列表。"""
    # TEI: [{"index":0,"score":0.9}, ...]
    if isinstance(data, list) and data and isinstance(data[0], dict):
        out = []
        for item in data:
            idx = int(item.get("index", 0))
            score = float(item.get("score", item.get("relevance_score", 0.0)))
            if 0 <= idx < len(documents):
                out.append((idx, score))
        return out or None

    if not isinstance(data, dict):
        return None

    # Cohere / Jina / SiliconFlow: {results:[{index, relevance_score}]}
    results = data.get("results") or data.get("data")
    if isinstance(results, list) and results:
        out = []
        for item in results:
            if not isinstance(item, dict):
                continue
            idx = int(item.get("index", 0))
            score = float(
                item.get("relevance_score", item.get("score", item.get("relevanceScore", 0.0)))
            )
            if 0 <= idx < len(documents):
                out.append((idx, score))
        if out:
            return out

    # 某些服务返回 scores 数组
    scores = data.get("scores")
    if isinstance(scores, list) and len(scores) == len(documents):
        return [(i, float(s)) for i, s in enumerate(scores)]

    return None


RERANK_ATTEMPTS: list[tuple[str, str]] = [
    ("/v1/rerank", "cohere"),
    ("/rerank", "tei"),
    ("/v1/reranking", "cohere"),
    ("/api/rerank", "cohere"),
    ("/v1/score", "score"),
    ("/score", "score"),
]


def build_rerank_payload(kind: str, model: str, query: str, documents: list[str], top_n: int) -> dict[str, Any]:
    if kind == "tei":
        return {"query": query, "texts": documents, "truncate": True}
    if kind == "score":
        return {"model": model, "query": query, "documents": documents}
    # cohere / jina / siliconflow
    return {
        "model": model,
        "query": query,
        "documents": documents,
        "top_n": top_n,
    }


async def rerank_with_fallback(
    session: aiohttp.ClientSession,
    base_url: str,
    model: str,
    query: str,
    documents: list[str],
    top_n: int,
    headers: dict[str, str],
    cached_url: str | None = None,
    cached_kind: str | None = None,
) -> tuple[list[tuple[int, float]], str, str]:
    errors: list[str] = []

    async def _try(url: str, kind: str) -> list[tuple[int, float]] | None:
        # TEI 还可能用 documents 字段
        payloads = [build_rerank_payload(kind, model, query, documents, top_n)]
        if kind == "tei":
            payloads.append({"query": query, "documents": documents})
        if kind == "cohere":
            payloads.append({"model": model, "query": query, "texts": documents, "top_n": top_n})
        for body in payloads:
            try:
                async with session.post(url, json=body, headers=headers) as resp:
                    text = await resp.text()
                    if resp.status >= 400:
                        errors.append(f"{url} HTTP {resp.status}: {text[:120]}")
                        continue
                    try:
                        import json

                        data = json.loads(text)
                    except Exception:
                        errors.append(f"{url} non-json")
                        continue
                    parsed = parse_rerank_response(data, documents)
                    if parsed:
                        return parsed
                    errors.append(f"{url} unparsed")
            except Exception as e:
                errors.append(f"{url} {e}")
        return None

    if cached_url and cached_kind:
        got = await _try(cached_url, cached_kind)
        if got:
            return got, cached_url, cached_kind

    attempts: list[tuple[str, str]] = []
    for base in base_candidates(base_url):
        lower = base.lower()
        if lower.endswith("/rerank") or lower.endswith("/reranking"):
            attempts.append((base, "cohere"))
            attempts.append((base, "tei"))
        for suffix, kind in RERANK_ATTEMPTS:
            attempts.append((f"{base}{suffix}", kind))

    seen: set[str] = set()
    for url, kind in attempts:
        key = f"{url}|{kind}"
        if key in seen:
            continue
        seen.add(key)
        got = await _try(url, kind)
        if got:
            logger.info("Reranker endpoint locked: %s (%s)", url, kind)
            return got, url, kind

    raise RuntimeError(
        "Reranker 所有候选端点均失败。已尝试 /v1/rerank、/rerank、TEI、score 等。详情: "
        + " | ".join(errors[:6])
    )


QDRANT_PATHS = [
    "/collections",
    "/qdrant/collections",
    "/api/collections",
    "/",
]


async def probe_qdrant(
    session: aiohttp.ClientSession,
    base_url: str,
    headers: dict[str, str] | None = None,
) -> tuple[bool, str, str]:
    """探测 Qdrant 可达性。返回 (ok, message, used_url)."""
    headers = headers or {}
    bases = base_candidates(base_url)
    # 也尝试原样（用户可能填了带 path 的）
    raw = (base_url or "").strip().rstrip("/")
    if raw and raw not in bases:
        bases.insert(0, raw)

    errors: list[str] = []
    for base in bases:
        for path in QDRANT_PATHS:
            url = f"{base}{path}" if path != "/" else f"{base}/"
            try:
                async with session.get(url, headers=headers) as resp:
                    text = await resp.text()
                    if resp.status == 200:
                        # collections 接口
                        if "collections" in path or "result" in text or "status" in text:
                            return True, f"Qdrant 可达 · {url}", url
                        if path == "/":
                            return True, f"Qdrant 根路径可达 · {url}", url
                    errors.append(f"{url} HTTP {resp.status}")
            except Exception as e:
                errors.append(f"{url} {e}")
    return False, "无法连接 Qdrant。详情: " + " | ".join(errors[:5]), ""
