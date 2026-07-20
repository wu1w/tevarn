"""Agent 自配置工具集 — get_system_status, update_config, list_available_models, manage_knowledge, manage_cron"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from backend.core.config import settings
from backend.tools.base import BaseTool, ToolSource, ToolRiskLevel

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    success: bool
    data: dict[str, Any]
    message: str


# ── 字段敏感度分级 ──
FIELD_SENSITIVITY: dict[str, Literal["low", "medium", "high"]] = {
    # 低风险 — 直接执行
    "temperature": "low",
    "max_tokens": "low",
    "rag_enabled": "low",
    "context_window": "low",
    "system_name": "low",
    # 中风险 — 提示但不阻断
    "llm_model": "medium",
    "embedding_model": "medium",
    "reranker_model": "medium",
    # 高风险 — 必须确认
    "llm_api_key": "high",
    "embedding_api_key": "high",
    "reranker_api_key": "high",
    "llm_base_url": "high",
    "embedding_base_url": "high",
    "reranker_base_url": "high",
    "llm_provider": "medium",
    "embedding_provider": "medium",
    "reranker_provider": "medium",
}

# 配置键 → 分类（与 settings 路由保持一致）
_SETTING_CATEGORY: dict[str, str] = {
    "llm_provider": "llm",
    "llm_model": "llm",
    "llm_base_url": "llm",
    "llm_api_key": "llm",
    "max_tokens": "llm",
    "context_window": "llm",
    "temperature": "llm",
    "embedding_provider": "embedding",
    "embedding_model": "embedding",
    "embedding_base_url": "embedding",
    "embedding_api_key": "embedding",
    "reranker_provider": "reranker",
    "reranker_model": "reranker",
    "reranker_base_url": "reranker",
    "reranker_api_key": "reranker",
    "rag_enabled": "rag",
    "qdrant_url": "rag",
    "qdrant_collection": "rag",
    "system_name": "general",
}


class GetSystemStatus(BaseTool):
    """获取系统当前运行状态和配置摘要"""

    def __init__(self):
        super().__init__(
            name="get_system_status",
            description="获取 AI 系统当前运行状态，包括对话模型、向量模型、知识库、定时任务等配置摘要",
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.SAFE,
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        from backend.database import get_db_context
        from backend.services.rag.qdrant_impl import QdrantService

        status: dict[str, Any] = {
            "llm_provider": getattr(settings, "llm_provider", "—"),
            "llm_model": getattr(settings, "llm_model", "—"),
            "llm_base_url": getattr(settings, "llm_base_url", "—"),
            "has_llm_key": bool(getattr(settings, "llm_api_key", "")),
            "embedding_provider": getattr(settings, "embedding_provider", "—"),
            "embedding_model": getattr(settings, "embedding_model", "—"),
            "has_embedding_key": bool(getattr(settings, "embedding_api_key", "")),
            "rag_enabled": getattr(settings, "rag_enabled", False),
            "qdrant_url": getattr(settings, "qdrant_url", "—"),
            "qdrant_collection": getattr(settings, "qdrant_collection", "—"),
            "temperature": getattr(settings, "temperature", 0.7),
            "max_tokens": getattr(settings, "max_tokens", 4096),
        }

        # 检查 Qdrant 连接
        try:
            svc = QdrantService()
            info = await svc.get_collections()
            status["qdrant_connected"] = True
            status["qdrant_collections"] = info.get("collections", [])
        except Exception as e:
            status["qdrant_connected"] = False
            status["qdrant_error"] = str(e)

        # 检查定时任务数量
        try:
            from backend.database import get_db_context
            async with get_db_context() as db:
                from sqlalchemy import text
                result = await db.execute(text("SELECT COUNT(*) FROM cron_jobs WHERE enabled = 1"))
                row = result.scalar()
                status["active_cron_jobs"] = row or 0
        except Exception as e:
            logger.warning("get_system_status: cron_jobs count failed: %s", e)
            status["active_cron_jobs"] = "—"

        return ToolResult(
            success=True,
            data=status,
            message=f"系统状态: {status['llm_provider']} · {status['llm_model']} | RAG: {'✓' if status['rag_enabled'] else '✗'} | Qdrant: {'✓' if status.get('qdrant_connected') else '✗'}"
        )


class UpdateConfig(BaseTool):
    """更新系统配置（带字段敏感度分级确认）"""

    def __init__(self):
        super().__init__(
            name="update_config",
            description="更新 AI 系统配置。支持 temperature/max_tokens/rag_enabled 等低风险字段直接修改，API Key/服务地址等高风险字段需要用户确认",
            parameters={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "配置项 key，如 llm_model, temperature, rag_enabled 等"},
                    "value": {"type": "string", "description": "配置项值"},
                    "confirm": {"type": "boolean", "description": "高风险操作确认标记，设为 true 表示用户已确认"},
                },
                "required": ["key", "value"],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.MEDIUM,
        )

    async def execute(self, key: str, value: str, confirm: bool = False, **kwargs: Any) -> ToolResult:
        sensitivity = FIELD_SENSITIVITY.get(key, "medium")

        if sensitivity == "high" and not confirm:
            return ToolResult(
                success=False,
                data={"needs_confirmation": True, "sensitivity": "high", "key": key},
                message=f"⚠️ 修改 `{key}` 是高风险操作（涉及 API 凭证或服务地址）。请确认是否继续？"
            )

        # 执行更新：走 SettingRepository（含加密）+ 立即应用到运行时单例
        # 注意：不要 import 不存在的 update_setting（会 ImportError 导致写入永远失败）
        try:
            from backend.repositories.setting_repo import AsyncSettingRepository
            from backend.core.runtime_settings import apply_setting_value, reset_factories_for_keys

            # 避免把脱敏占位写回
            if key.endswith("_api_key") and isinstance(value, str):
                if not value or "..." in value or value == "***":
                    return ToolResult(
                        success=False,
                        data={"key": key},
                        message="❌ API Key 不能为空或为脱敏占位（sk-xx...yy）",
                    )

            cat = _SETTING_CATEGORY.get(key, "general")
            repo = AsyncSettingRepository()
            setting = await repo.upsert(key=key, value=value, category=cat)
            plain = setting.value  # repo.upsert 已返回明文
            applied = apply_setting_value(key, plain)
            if applied:
                reset_factories_for_keys([key])
            display = value if not key.endswith("_api_key") else ("*" * min(8, len(value)) + "…")
            return ToolResult(
                success=True,
                data={
                    "key": key,
                    "value": display,
                    "sensitivity": sensitivity,
                    "runtime_applied": applied,
                },
                message=(
                    f"✅ `{key}` 已更新为 `{display[:50]}{'...' if len(display) > 50 else ''}`"
                    + ("" if applied else "（已落库；该键无对应运行时字段，重启后仍生效于 DB）")
                ),
            )
        except Exception as e:
            logger.exception("UpdateConfig failed for key=%s", key)
            return ToolResult(success=False, data={}, message=f"❌ 更新失败: {e}")


class ListAvailableModels(BaseTool):
    """列出当前配置下可用的 AI 模型"""

    def __init__(self):
        super().__init__(
            name="list_available_models",
            description="获取当前 AI 服务商提供的可用模型列表，需要配置正确的 API Key",
            parameters={
                "type": "object",
                "properties": {
                    "provider": {"type": "string", "description": "可选，指定服务商（如 openai, ollama, deepseek）"},
                },
                "required": [],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.SAFE,
        )

    async def execute(self, provider: str = "", **kwargs: Any) -> ToolResult:
        from backend.services.llm import LLMServiceFactory
        from backend.core.config import settings

        target_provider = provider or getattr(settings, "llm_provider", "")
        base_url = getattr(settings, "llm_base_url", "")
        api_key = getattr(settings, "llm_api_key", "")

        if not target_provider:
            return ToolResult(success=False, data={}, message="未配置 LLM 服务商，请先设置 llm_provider")

        try:
            from backend.lib.api import list_remote_models
            res = await list_remote_models(
                llm_provider=target_provider,
                llm_base_url=base_url,
                llm_api_key=api_key or None,
            )
            if res.get("ok") and res.get("models"):
                models = res["models"]
                return ToolResult(
                    success=True,
                    data={"provider": target_provider, "models": models, "count": len(models)},
                    message=f"{target_provider} 可用模型 ({len(models)} 个): {', '.join(models[:10])}{'...' if len(models) > 10 else ''}"
                )
            else:
                return ToolResult(
                    success=False,
                    data={"provider": target_provider, "raw_response": res},
                    message=f"未能获取模型列表: {res.get('message', '未知错误')}"
                )
        except Exception as e:
            return ToolResult(success=False, data={}, message=f"获取模型列表失败: {e}")


class ManageKnowledge(BaseTool):
    """知识库管理工具（合并 upload/list/search/delete）"""

    def __init__(self):
        super().__init__(
            name="manage_knowledge",
            description="管理知识库文档。action: upload(上传文本), upload_file(从工作区文件上传), list(列出), search(搜索), delete(删除)",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["upload", "upload_file", "list", "search", "delete"], "description": "操作类型"},
                    "content": {"type": "string", "description": "upload 时: 文档内容"},
                    "title": {"type": "string", "description": "upload/upload_file 时: 文档标题"},
                    "file_path": {"type": "string", "description": "upload_file 时: 工作区文件路径"},
                    "doc_id": {"type": "string", "description": "delete 时: 文档 ID"},
                    "query": {"type": "string", "description": "search 时: 搜索关键词"},
                    "top_k": {"type": "integer", "description": "search 时: 返回结果数量，默认 5"},
                    "collection": {"type": "string", "description": "search/upload_file 时: 指定 Collection，默认使用配置值"},
                },
                "required": ["action"],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.MEDIUM,
        )

    async def execute(self, action: str, **kwargs: Any) -> ToolResult:
        from backend.services.rag.qdrant_impl import QdrantService
        from backend.services.rag.factory import RAGServiceFactory

        if action == "upload":
            content = kwargs.get("content", "")
            title = kwargs.get("title", "")
            if not content or not title:
                return ToolResult(success=False, data={}, message="upload 需要提供 content 和 title")
            try:
                svc = QdrantService()
                # Document 真实形状是检索结果；upload 走 kwargs 兼容
                doc = await svc.add_document(title=title, content=content)
                return ToolResult(success=True, data={"doc_id": getattr(doc, "id", None)}, message=f"✅ 文档 `{title}` 已上传并索引")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 上传失败: {e}")

        elif action == "upload_file":
            file_path = (kwargs.get("file_path") or "").strip()
            if not file_path:
                return ToolResult(success=False, data={}, message="upload_file 需要提供 file_path")
            try:
                from pathlib import Path

                from backend.repositories.knowledge_repo import AsyncDocumentRepository
                from backend.services.knowledge.indexer import index_document_text

                p = Path(file_path)
                if not p.is_file():
                    return ToolResult(success=False, data={}, message=f"文件不存在: {file_path}")
                raw_bytes = p.read_bytes()
                if not raw_bytes:
                    return ToolResult(success=False, data={}, message=f"文件为空: {file_path}")
                doc_content = raw_bytes.decode("utf-8", errors="replace")
                title = (kwargs.get("title") or "").strip() or p.stem
                collection = (kwargs.get("collection") or "").strip()

                # 与上传路由同一管线：建 Document 记录 → index_document_text 同步索引
                doc_repo = AsyncDocumentRepository()
                doc = await doc_repo.create({
                    "title": title,
                    "user_id": None,
                    "status": "pending",
                    "source": str(p),
                    "meta": {
                        "source": p.name,
                        "content": doc_content,
                        "size": len(raw_bytes),
                        "collection": collection,
                    },
                })
                result = await index_document_text(
                    document_id=doc.id,
                    title=title,
                    text=doc_content,
                    user_id=None,
                    source=str(p),
                )
                if result.get("ok"):
                    return ToolResult(
                        success=True,
                        data={"doc_id": str(doc.id), "title": title, "size": len(raw_bytes)},
                        message=f"✅ 文件 `{p.name}` 已作为文档 `{title}` 上传并索引",
                    )
                return ToolResult(
                    success=True,
                    data={"doc_id": str(doc.id), "title": title, "indexed": False, "detail": result.get("message")},
                    message=f"⚠️ 文档 `{title}` 已创建，但向量索引未完成: {result.get('message', '未知原因')}（向量栈就绪后可重新索引）",
                )
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 文件上传失败: {e}")

        elif action == "list":
            try:
                svc = QdrantService()
                docs = await svc.list_documents()
                return ToolResult(
                    success=True,
                    data={"documents": docs, "count": len(docs)},
                    message=f"知识库共 {len(docs)} 篇文档"
                )
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 列出文档失败: {e}")

        elif action == "search":
            query = kwargs.get("query", "")
            top_k = kwargs.get("top_k", 5)
            collection = kwargs.get("collection", "")
            if not query:
                return ToolResult(success=False, data={}, message="search 需要提供 query")
            try:
                svc = await RAGServiceFactory.get_service()
                results = await svc.search(query, top_k=top_k, collection=collection or None)
                return ToolResult(
                    success=True,
                    data={"results": results, "count": len(results)},
                    message=f"找到 {len(results)} 条相关结果"
                )
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 搜索失败: {e}")

        elif action == "delete":
            doc_id = kwargs.get("doc_id", "")
            if not doc_id:
                return ToolResult(success=False, data={}, message="delete 需要提供 doc_id")
            try:
                svc = QdrantService()
                await svc.delete_document(doc_id)
                return ToolResult(success=True, data={"doc_id": doc_id}, message=f"✅ 文档 `{doc_id}` 已删除")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 删除失败: {e}")

        return ToolResult(success=False, data={}, message=f"未知 action: {action}")


class ManageCron(BaseTool):
    """定时任务管理工具 — 对齐 cron_jobs(workflow_id) 与调度器"""

    def __init__(self):
        super().__init__(
            name="manage_cron",
            description=(
                "管理定时任务（绑定工作流按 schedule 执行）。"
                "action: create/list/update/delete/toggle。"
                "create 需要 name + schedule，建议提供 workflow_id（工作流 UUID）；"
                "也可用 workflow_name 按名称匹配。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "list", "update", "delete", "toggle"],
                        "description": "操作类型",
                    },
                    "job_id": {"type": "string", "description": "update/delete/toggle 时: 任务 ID"},
                    "name": {"type": "string", "description": "create/update: 任务名称"},
                    "schedule": {
                        "type": "string",
                        "description": "create/update: Cron 表达式，如 '0 9 * * *' 或 'every 1h'",
                    },
                    "workflow_id": {
                        "type": "string",
                        "description": "create/update: 绑定的工作流 UUID",
                    },
                    "workflow_name": {
                        "type": "string",
                        "description": "create/update: 按名称查找工作流（workflow_id 优先）",
                    },
                    "enabled": {"type": "boolean", "description": "create/update: 是否启用"},
                },
                "required": ["action"],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.MEDIUM,
        )

    async def _resolve_workflow_id(self, kwargs: dict[str, Any]) -> Any | None:
        import uuid as uuid_mod

        from backend.repositories.workflow_repo import AsyncWorkflowRepository

        raw = (kwargs.get("workflow_id") or "").strip()
        if raw:
            try:
                return uuid_mod.UUID(raw)
            except ValueError:
                raise ValueError(f"workflow_id 不是合法 UUID: {raw}")

        name = (kwargs.get("workflow_name") or kwargs.get("command") or "").strip()
        # command 兼容旧参数：若看起来像 UUID 当 id，否则当 workflow 名
        if not name:
            return None
        try:
            return uuid_mod.UUID(name)
        except ValueError:
            pass

        repo = AsyncWorkflowRepository()
        # list_by_status("") → 全部
        try:
            wfs = await repo.list_by_status("")
        except Exception:
            wfs = []
        name_l = name.lower()
        for w in wfs or []:
            if str(getattr(w, "name", "") or "").lower() == name_l:
                return w.id
        for w in wfs or []:
            if name_l in str(getattr(w, "name", "") or "").lower():
                return w.id
        raise ValueError(f"找不到工作流: {name}")

    def _job_to_dict(self, job: Any) -> dict[str, Any]:
        return {
            "id": str(job.id),
            "name": job.name,
            "schedule": job.schedule,
            "workflow_id": str(job.workflow_id) if job.workflow_id else None,
            "enabled": bool(job.enabled),
            "last_status": getattr(job, "last_status", None),
            "last_error": getattr(job, "last_error", None),
            "last_run_at": job.last_run_at.isoformat() if getattr(job, "last_run_at", None) else None,
            "next_run_at": job.next_run_at.isoformat() if getattr(job, "next_run_at", None) else None,
        }

    def _notify_scheduler(self, job: Any | None, *, deleted_id: str | None = None) -> None:
        try:
            from backend.services.cron_scheduler import scheduler

            if deleted_id:
                scheduler._unschedule_job(str(deleted_id))
            elif job is not None:
                scheduler.reschedule(job)
        except Exception as e:
            logger.warning("cron scheduler notify failed: %s", e)

    async def execute(self, action: str, **kwargs: Any) -> ToolResult:
        import uuid as uuid_mod

        from backend.repositories.cron_repo import AsyncCronJobRepository
        from backend.services.cron_scheduler import compute_next_run

        repo = AsyncCronJobRepository()

        if action == "create":
            name = (kwargs.get("name") or "").strip()
            schedule = (kwargs.get("schedule") or "").strip()
            if not name or not schedule:
                return ToolResult(
                    success=False,
                    data={},
                    message="create 需要提供 name, schedule；建议再提供 workflow_id 或 workflow_name",
                )
            try:
                workflow_id = await self._resolve_workflow_id(kwargs)
            except ValueError as e:
                return ToolResult(success=False, data={}, message=f"❌ {e}")

            enabled = bool(kwargs.get("enabled", True))
            next_run = compute_next_run(schedule) if enabled else None
            try:
                job = await repo.create(
                    {
                        "name": name,
                        "schedule": schedule,
                        "workflow_id": workflow_id,
                        "enabled": enabled,
                        "last_status": "pending",
                        "next_run_at": next_run,
                    }
                )
                self._notify_scheduler(job)
                return ToolResult(
                    success=True,
                    data=self._job_to_dict(job),
                    message=f"✅ 定时任务 `{name}` 已创建"
                    + ("" if workflow_id else "（未绑定工作流，启用后也不会执行业务）"),
                )
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 创建失败: {e}")

        elif action == "list":
            try:
                jobs = await repo.list_all()
                data = [self._job_to_dict(j) for j in jobs]
                return ToolResult(
                    success=True,
                    data={"jobs": data, "count": len(data)},
                    message=f"共 {len(data)} 个定时任务",
                )
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 列出失败: {e}")

        elif action == "update":
            job_id = (kwargs.get("job_id") or "").strip()
            if not job_id:
                return ToolResult(success=False, data={}, message="update 需要提供 job_id")
            try:
                jid = uuid_mod.UUID(job_id)
            except ValueError:
                return ToolResult(success=False, data={}, message="job_id 不是合法 UUID")

            patch: dict[str, Any] = {}
            if "name" in kwargs and kwargs["name"] is not None:
                patch["name"] = str(kwargs["name"]).strip()
            if "schedule" in kwargs and kwargs["schedule"] is not None:
                patch["schedule"] = str(kwargs["schedule"]).strip()
            if "enabled" in kwargs and kwargs["enabled"] is not None:
                patch["enabled"] = bool(kwargs["enabled"])
            if any(k in kwargs for k in ("workflow_id", "workflow_name", "command")):
                try:
                    patch["workflow_id"] = await self._resolve_workflow_id(kwargs)
                except ValueError as e:
                    return ToolResult(success=False, data={}, message=f"❌ {e}")
            if not patch:
                return ToolResult(success=False, data={}, message="update 至少需要提供一项更新")

            if "schedule" in patch or patch.get("enabled") is True:
                sched = patch.get("schedule")
                if sched is None:
                    existing = await repo.get_by_id(jid)
                    sched = existing.schedule if existing else None
                if sched and patch.get("enabled", True):
                    patch["next_run_at"] = compute_next_run(sched)

            try:
                job = await repo.update(jid, patch)
                if job is None:
                    return ToolResult(success=False, data={}, message="任务不存在")
                self._notify_scheduler(job)
                return ToolResult(
                    success=True,
                    data=self._job_to_dict(job),
                    message=f"✅ 任务 `{job_id}` 已更新",
                )
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 更新失败: {e}")

        elif action == "delete":
            job_id = (kwargs.get("job_id") or "").strip()
            if not job_id:
                return ToolResult(success=False, data={}, message="delete 需要提供 job_id")
            try:
                jid = uuid_mod.UUID(job_id)
                ok = await repo.delete(jid)
                if not ok:
                    return ToolResult(success=False, data={}, message="任务不存在")
                self._notify_scheduler(None, deleted_id=job_id)
                return ToolResult(success=True, data={"job_id": job_id}, message=f"✅ 任务 `{job_id}` 已删除")
            except ValueError:
                return ToolResult(success=False, data={}, message="job_id 不是合法 UUID")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 删除失败: {e}")

        elif action == "toggle":
            job_id = (kwargs.get("job_id") or "").strip()
            if not job_id:
                return ToolResult(success=False, data={}, message="toggle 需要提供 job_id")
            try:
                jid = uuid_mod.UUID(job_id)
                job = await repo.get_by_id(jid)
                if job is None:
                    return ToolResult(success=False, data={}, message="任务不存在")
                new_enabled = not bool(job.enabled)
                patch: dict[str, Any] = {"enabled": new_enabled}
                if new_enabled:
                    patch["next_run_at"] = compute_next_run(job.schedule)
                job = await repo.update(jid, patch)
                self._notify_scheduler(job)
                return ToolResult(
                    success=True,
                    data=self._job_to_dict(job) if job else {"job_id": job_id, "enabled": new_enabled},
                    message=f"✅ 任务 `{job_id}` 已{'启用' if new_enabled else '禁用'}",
                )
            except ValueError:
                return ToolResult(success=False, data={}, message="job_id 不是合法 UUID")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 切换失败: {e}")

        return ToolResult(success=False, data={}, message=f"未知 action: {action}")
