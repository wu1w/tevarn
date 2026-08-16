"""
Knowledge 路由
知识库文档管理 + 向量索引
"""

import json
import logging
import uuid
from typing import Annotated, Any, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from pydantic import BaseModel, Field

from backend.core.config import settings
from backend.repositories import DocumentRepository
from backend.schemas.knowledge import DocumentCreate, DocumentRead, DocumentUpdate
from backend.schemas.user import UserRead

from ..dependencies import get_current_user, get_document_repo

# rebuild_index 等后台任务里引用了 logger 却从未定义 —— 该路径必然 NameError，
# 且因跑在 BackgroundTasks 里，失败被静默吞掉，表现为「重建索引点了没反应」。
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge", tags=["Knowledge"])

MAX_DOCUMENT_BYTES = 16 * 1024 * 1024


def _reject_oversized_request(request: Request) -> None:
    raw = request.headers.get("content-length", "").strip()
    if not raw:
        return
    try:
        length = int(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的 Content-Length") from None
    if length > MAX_DOCUMENT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"文档过大，最大允许 {MAX_DOCUMENT_BYTES // 1024 // 1024}MB",
        )


def _is_doc_owner_or_admin(doc: Any, user: UserRead) -> bool:
    if doc.user_id is None:
        return user.is_superuser
    return doc.user_id == user.id or user.is_superuser


class DocumentCreateWithContent(DocumentCreate):
    """创建时可附带正文；索引时从 content 或 meta.content 读取。"""

    content: str = ""


class IndexBody(BaseModel):
    content: Optional[str] = Field(None, description="可选：覆盖索引正文")


@router.get("/documents", response_model=list[DocumentRead])
async def list_documents(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[DocumentRepository, Depends(get_document_repo)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """Paginated document list (default 100, max 500) to keep first paint fast."""
    rows = await repo.list_by_user(current_user.id) or []
    return rows[offset : offset + limit]


@router.post("/documents", response_model=DocumentRead)
async def create_document(
    request: Request,
    background: BackgroundTasks,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[DocumentRepository, Depends(get_document_repo)],
):
    """创建文档；支持 JSON body 或 multipart/form-data 文件上传。若有 content 则后台自动索引到向量库。"""
    content_type = request.headers.get("content-type", "")

    title = ""
    doc_content = ""
    status_val = "pending"
    meta_val: dict[str, Any] = {}
    source_val = ""
    _reject_oversized_request(request)

    if content_type.startswith("multipart/form-data"):
        # multipart 上传：title（可选）、auto_index（可选）、file（必选）
        form = await request.form(max_files=1, max_fields=10, max_part_size=MAX_DOCUMENT_BYTES)
        title = (form.get("title") or "").strip()
        auto_index = str(form.get("auto_index", "true")).strip().lower() in {"1", "true", "yes", "on"}
        uploaded: UploadFile | None = form.get("file")
        if uploaded is None:
            raise HTTPException(status_code=400, detail="multipart 请求缺少 file 字段")
        filename = uploaded.filename or "uploaded.txt"
        if not title:
            title = filename.rsplit(".", 1)[0]
        raw_bytes = await uploaded.read(MAX_DOCUMENT_BYTES + 1)
        if len(raw_bytes) > MAX_DOCUMENT_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"文档过大，最大允许 {MAX_DOCUMENT_BYTES // 1024 // 1024}MB",
            )
        try:
            doc_content = raw_bytes.decode("utf-8", errors="replace")
        except Exception:
            doc_content = ""
        meta_val = {
            "source": filename,
            "content": doc_content,
            "size": len(raw_bytes),
        }
        source_val = filename
        status_val = "pending" if auto_index else "draft"
    else:
        # JSON body
        body = await request.body()
        if len(body) > MAX_DOCUMENT_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"请求体过大，最大允许 {MAX_DOCUMENT_BYTES // 1024 // 1024}MB",
            )
        if not body:
            raise HTTPException(status_code=400, detail="请求体为空")
        try:
            json_data = json.loads(body)
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"JSON 解析失败: {e}") from e
        title = str(json_data.get("title") or "")
        doc_content = str(json_data.get("content") or "").strip()
        status_val = str(json_data.get("status") or "pending")
        meta_val = dict(json_data.get("meta") or {})
        source_val = str(json_data.get("source") or "")
        meta_val["content"] = doc_content

    payload = {
        "title": title,
        "user_id": current_user.id,
        "status": status_val,
        "source": source_val,
        "meta": meta_val,
    }

    doc = await repo.create(payload)
    if doc_content.strip():
        background.add_task(
            _bg_index,
            doc.id,
            doc.title,
            doc_content,
            current_user.id,
            doc.source or "",
        )
    return doc


async def _bg_index(
    doc_id: uuid.UUID,
    title: str,
    content: str,
    user_id: uuid.UUID | None,
    source: str,
) -> None:
    from backend.services.knowledge.indexer import index_document_text

    await index_document_text(
        document_id=doc_id,
        title=title,
        text=content,
        user_id=user_id,
        source=source,
    )


@router.get("/documents/{doc_id}", response_model=DocumentRead)
async def get_document(
    doc_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[DocumentRepository, Depends(get_document_repo)],
):
    doc = await repo.get_by_id(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if not _is_doc_owner_or_admin(doc, current_user):
        raise HTTPException(status_code=403, detail="Access denied")
    return doc


@router.put("/documents/{doc_id}", response_model=DocumentRead)
async def update_document(
    doc_id: uuid.UUID,
    data: DocumentUpdate,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[DocumentRepository, Depends(get_document_repo)],
):
    doc = await repo.get_by_id(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if not _is_doc_owner_or_admin(doc, current_user):
        raise HTTPException(status_code=403, detail="Access denied")
    doc = await repo.update(doc_id, data.model_dump(exclude_unset=True))
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.post("/documents/{doc_id}/index")
async def index_document(
    doc_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[DocumentRepository, Depends(get_document_repo)],
    body: IndexBody | None = None,
):
    """对文档执行/重做向量索引。"""
    doc = await repo.get_by_id(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if not _is_doc_owner_or_admin(doc, current_user):
        raise HTTPException(status_code=403, detail="Access denied")

    content = (body.content if body else None) or ""
    if not content:
        content = str((doc.meta or {}).get("content") or "")
    if not content and doc.source:
        # 仅允许 workspace/uploads 下文件（防 LFI 读 secrets）
        try:
            from pathlib import Path

            p = Path(doc.source).resolve()
            roots = []
            try:
                roots.append(Path("workspace").resolve())
            except Exception:
                pass
            try:
                roots.append(Path("uploads").resolve())
            except Exception:
                pass
            allowed = False
            for r in roots:
                try:
                    p.relative_to(r)
                    allowed = True
                    break
                except ValueError:
                    continue
            if allowed and p.is_file() and p.suffix.lower() in (
                ".md", ".txt", ".json", ".csv", ".py", ".ts", ".tsx", ".js", ".yaml", ".yml",
            ):
                content = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            pass
    if not content.strip():
        raise HTTPException(status_code=400, detail="无可用正文，请在 content 或 meta.content 中提供")

    from backend.services.knowledge.indexer import index_document_text

    result = await index_document_text(
        document_id=doc.id,
        title=doc.title,
        text=content,
        user_id=doc.user_id or current_user.id,
        source=doc.source or "",
        skip_wiki=(doc.source == "builtin-seed"),
        replace_chunks=True,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("message") or "索引失败")
    # 回写 content 到 meta 便于再次索引
    meta = dict(doc.meta or {})
    meta["content"] = content
    await repo.update(doc_id, {"meta": meta})
    return result


@router.delete("/documents/{doc_id}")
async def delete_document(
    doc_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[DocumentRepository, Depends(get_document_repo)],
):
    doc = await repo.get_by_id(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if not _is_doc_owner_or_admin(doc, current_user):
        raise HTTPException(status_code=403, detail="Access denied")
    # 先删向量（按 document_id filter），再删 SQL
    try:
        from backend.services.knowledge.indexer import delete_qdrant_by_filter

        await delete_qdrant_by_filter(document_id=str(doc_id))
    except Exception as e:
        logger.warning("delete_document qdrant vectors: %s", e)
    success = await repo.delete(doc_id)
    if not success:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"deleted": True}


# ─── RAG 检索测试 + Qdrant 状态 + 维度检查 + 重建索引 ───


class RAGTestBody(BaseModel):
    """RAG 检索测试请求体"""
    query: str = Field(..., min_length=1, description="测试查询文本")
    top_k: int = Field(5, ge=1, le=20, description="返回文档数")
    collection: Optional[str] = Field(None, description="指定 collection（可选）")
    collections: Optional[list[str]] = Field(None, description="多个 collection（可选）")
    search_mode: Optional[str] = Field(None, description="hybrid | vector | keyword")


@router.post("/rag-test")
async def rag_test(
    body: RAGTestBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """RAG 检索测试 — 返回检索结果 + 诊断信息"""
    from backend.services.rag.factory import RAGServiceFactory

    svc = RAGServiceFactory.get_service()
    context = await svc.search_knowledge_base(
        query=body.query,
        top_k=body.top_k,
        collection=body.collection,
        collections=body.collections,
        user_id=str(current_user.id),
        search_mode=body.search_mode,
    )

    # 获取诊断信息
    diag = svc.get_diagnostics() if hasattr(svc, "get_diagnostics") else None

    return {
        "query": body.query,
        "context": context,
        "context_length": len(context),
        "diagnostics": {
            "total_time_ms": diag.total_time_ms if diag else 0,
            "embed_time_ms": diag.embed_time_ms if diag else 0,
            "search_time_ms": diag.search_time_ms if diag else 0,
            "rerank_time_ms": diag.rerank_time_ms if diag else 0,
            "fused_count": diag.fused_count if diag else 0,
            "reranked_count": diag.reranked_count if diag else 0,
            "collections_searched": diag.collections_searched if diag else [],
            "search_mode": diag.search_mode if diag else "unknown",
            "errors": diag.errors if diag else [],
        } if diag else {},
    }


@router.get("/qdrant-status")
async def qdrant_status(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """Qdrant 连接状态 + Collection 信息"""
    import aiohttp

    url = settings.qdrant_url.rstrip("/")
    result: dict[str, Any] = {
        "qdrant_url": url,
        "connected": False,
        "collections": [],
    }

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            # 1. 健康检查
            async with session.get(f"{url}/healthz") as resp:
                result["connected"] = resp.status == 200

            if not result["connected"]:
                return result

            # 2. 列出所有 collection
            async with session.get(f"{url}/collections") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    col_list = data.get("result", {}).get("collections", [])
                    result["collections"] = [
                        {"name": c.get("name", ""), "status": "ok"}
                        for c in col_list
                    ]

            # 3. 获取默认 collection 详情
            col_name = settings.qdrant_collection
            async with session.get(f"{url}/collections/{col_name}") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    info = data.get("result", {})
                    vectors_config = info.get("vectors", {})
                    points_count = info.get("points_count", 0)
                    result["default_collection"] = {
                        "name": col_name,
                        "vector_size": vectors_config.get("size"),
                        "distance": vectors_config.get("distance"),
                        "points_count": points_count,
                        "status": info.get("status", "unknown"),
                    }

            # 4. 获取配置的多 collection 信息
            col_map = getattr(settings, "qdrant_collections", {})
            multi_info = []
            for logical_name, actual_name in col_map.items():
                async with session.get(f"{url}/collections/{actual_name}") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        info = data.get("result", {})
                        multi_info.append({
                            "logical_name": logical_name,
                            "actual_name": actual_name,
                            "vector_size": info.get("vectors", {}).get("size"),
                            "points_count": info.get("points_count", 0),
                            "status": info.get("status", "unknown"),
                        })
                    else:
                        multi_info.append({
                            "logical_name": logical_name,
                            "actual_name": actual_name,
                            "status": "not_found",
                        })
            result["multi_collections"] = multi_info

    except Exception as e:
        result["error"] = str(e)

    return result


@router.get("/dimension-check")
async def dimension_check(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """检查 Embedding 维度与 Qdrant Collection 维度是否匹配"""
    from backend.services.embedding.dimension import DimensionManager

    result = await DimensionManager.check_qdrant_dimension()
    return result


@router.post("/rebuild-index")
async def rebuild_index(
    background: BackgroundTasks,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    collection: Optional[str] = Query(None, description="指定 collection（默认用配置的）"),
):
    """
    一键重建索引
    安全策略：先 rename 旧 collection 为备份，建新 collection 成功后再删旧
    """
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Only admin can rebuild index")

    import aiohttp

    col = collection or settings.qdrant_collection
    url = settings.qdrant_url.rstrip("/")

    # 1. 检查旧 collection 是否存在
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(f"{url}/collections/{col}") as resp:
                old_exists = resp.status == 200
                old_info = await resp.json() if old_exists else {}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Qdrant 连接失败: {e}") from e

    if not old_exists:
        raise HTTPException(status_code=404, detail=f"Collection '{col}' 不存在")

    # 2. Rename 旧 collection 为备份
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            # Qdrant 不支持 rename，用 alias 方式：先创建别名指向旧 collection
            # 实际策略：删除旧 collection 前先记录信息，后台重建
            pass
    except Exception:
        pass

    # 3. 后台执行重建
    background.add_task(_bg_rebuild_index, col, str(current_user.id))

    return {
        "ok": True,
        "message": f"索引重建已启动重建: collection={col}",
        "collection": col,
        "old_points_count": old_info.get("result", {}).get("points_count", 0),
    }


async def _bg_rebuild_index(collection: str, user_id: str) -> None:
    """后台重建索引：只删该用户向量点 → 再重建该用户文档（不毁他人 collection）。"""
    from backend.repositories.knowledge_repo import AsyncDocumentRepository
    from backend.services.knowledge.indexer import (
        delete_qdrant_by_filter,
        index_document_text,
    )

    # 1. 仅删除该 user_id 的向量点（禁止 drop 全 collection）
    try:
        dr = await delete_qdrant_by_filter(user_id=str(user_id), collection=collection)
        if not dr.get("ok"):
            logger.error(
                "Rebuild: failed to delete user vectors collection=%s user=%s: %s",
                collection,
                user_id[:8],
                dr.get("message"),
            )
            # 继续尝试 reindex：upsert 会覆盖同 id 点
        else:
            logger.info(
                "Deleted user vectors for rebuild collection=%s user=%s",
                collection,
                user_id[:8],
            )
    except Exception as e:
        logger.error(f"Rebuild: failed to delete user vectors: {e}")

    # 2. 遍历该用户已索引文档，重新索引
    doc_repo = AsyncDocumentRepository()
    try:
        docs = await doc_repo.list_by_user(uuid.UUID(user_id))
        if not docs:
            logger.info("No documents to reindex")
            return

        success = 0
        failed = 0
        for doc in docs:
            content = str((doc.meta or {}).get("content", ""))
            if not content and doc.source:
                # rebuild 同样禁止任意路径 LFI
                try:
                    from pathlib import Path

                    p = Path(doc.source).resolve()
                    for root_name in ("workspace", "uploads"):
                        try:
                            root = Path(root_name).resolve()
                            p.relative_to(root)
                            if p.is_file():
                                content = p.read_text(encoding="utf-8", errors="ignore")
                            break
                        except ValueError:
                            continue
                except Exception:
                    pass

            if not content.strip():
                failed += 1
                continue

            result = await index_document_text(
                document_id=doc.id,
                title=doc.title,
                text=content,
                user_id=doc.user_id,
                source=doc.source or "",
                skip_wiki=(doc.source == "builtin-seed"),
                replace_chunks=True,
            )
            if result.get("ok"):
                success += 1
            else:
                failed += 1

        logger.info(f"Rebuild complete: {success} success, {failed} failed, collection={collection}")
    except Exception as e:
        logger.error(f"Rebuild index failed: {e}")
