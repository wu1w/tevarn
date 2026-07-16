"""Agent 自配置工具集 — get_system_status, update_config, list_available_models, manage_knowledge, manage_cron"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from backend.core.config import settings
from backend.tools.base import BaseTool, ToolSource, ToolRiskLevel


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
        except Exception:
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

        # 执行更新
        try:
            from backend.api.routes.settings import update_setting
            from backend.database import get_db_context
            from sqlalchemy import text

            async with get_db_context() as db:
                # 查找或创建 setting
                result = await db.execute(
                    text("SELECT * FROM settings WHERE key = :key"),
                    {"key": key}
                )
                row = result.mappings().first()
                if row:
                    await db.execute(
                        text("UPDATE settings SET value = :value WHERE key = :key"),
                        {"key": key, "value": value}
                    )
                else:
                    await db.execute(
                        text("INSERT INTO settings (key, value, category, description) VALUES (:key, :value, 'general', '')"),
                        {"key": key, "value": value}
                    )
                await db.commit()

            return ToolResult(
                success=True,
                data={"key": key, "value": value, "sensitivity": sensitivity},
                message=f"✅ `{key}` 已更新为 `{value[:50]}{'...' if len(value) > 50 else ''}`"
            )
        except Exception as e:
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
            description="管理知识库文档。action: upload(上传), list(列出), search(搜索), delete(删除)",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["upload", "list", "search", "delete"], "description": "操作类型"},
                    "content": {"type": "string", "description": "upload 时: 文档内容"},
                    "title": {"type": "string", "description": "upload 时: 文档标题"},
                    "doc_id": {"type": "string", "description": "delete 时: 文档 ID"},
                    "query": {"type": "string", "description": "search 时: 搜索关键词"},
                    "top_k": {"type": "integer", "description": "search 时: 返回结果数量，默认 5"},
                    "collection": {"type": "string", "description": "search 时: 指定 Collection，默认使用配置值"},
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
                from backend.services.rag.qdrant_impl import Document
                doc = await svc.add_document(Document(title=title, content=content))
                return ToolResult(success=True, data={"doc_id": doc.id}, message=f"✅ 文档 `{title}` 已上传并索引")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 上传失败: {e}")

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
    """定时任务管理工具（合并 create/list/update/delete）"""

    def __init__(self):
        super().__init__(
            name="manage_cron",
            description="管理定时任务。action: create(创建), list(列出), update(更新), delete(删除), toggle(启用/禁用)",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["create", "list", "update", "delete", "toggle"], "description": "操作类型"},
                    "job_id": {"type": "string", "description": "update/delete/toggle 时: 任务 ID"},
                    "name": {"type": "string", "description": "create 时: 任务名称"},
                    "schedule": {"type": "string", "description": "create/update 时: Cron 表达式或描述，如 '0 9 * * *' 或 '每天9点'"},
                    "command": {"type": "string", "description": "create/update 时: 执行命令或描述"},
                    "enabled": {"type": "boolean", "description": "create/update 时: 是否启用"},
                },
                "required": ["action"],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.MEDIUM,
        )

    async def execute(self, action: str, **kwargs: Any) -> ToolResult:
        from backend.database import get_db_context
        from sqlalchemy import text

        if action == "create":
            name = kwargs.get("name", "")
            schedule = kwargs.get("schedule", "")
            command = kwargs.get("command", "")
            if not all([name, schedule, command]):
                return ToolResult(success=False, data={}, message="create 需要提供 name, schedule, command")
            try:
                async with get_db_context() as db:
                    result = await db.execute(
                        text("INSERT INTO cron_jobs (name, schedule, command, enabled) VALUES (:name, :schedule, :command, :enabled)"),
                        {"name": name, "schedule": schedule, "command": command, "enabled": kwargs.get("enabled", True)}
                    )
                    await db.commit()
                    job_id = result.lastrowid
                return ToolResult(success=True, data={"job_id": job_id}, message=f"✅ 定时任务 `{name}` 已创建")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 创建失败: {e}")

        elif action == "list":
            try:
                async with get_db_context() as db:
                    result = await db.execute(text("SELECT * FROM cron_jobs ORDER BY created_at DESC"))
                    rows = result.mappings().all()
                    jobs = [dict(r) for r in rows]
                return ToolResult(
                    success=True,
                    data={"jobs": jobs, "count": len(jobs)},
                    message=f"共 {len(jobs)} 个定时任务"
                )
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 列出失败: {e}")

        elif action == "update":
            job_id = kwargs.get("job_id", "")
            if not job_id:
                return ToolResult(success=False, data={}, message="update 需要提供 job_id")
            try:
                updates = []
                params: dict[str, Any] = {"job_id": job_id}
                if "schedule" in kwargs:
                    updates.append("schedule = :schedule")
                    params["schedule"] = kwargs["schedule"]
                if "command" in kwargs:
                    updates.append("command = :command")
                    params["command"] = kwargs["command"]
                if "enabled" in kwargs:
                    updates.append("enabled = :enabled")
                    params["enabled"] = kwargs["enabled"]
                if "name" in kwargs:
                    updates.append("name = :name")
                    params["name"] = kwargs["name"]
                if not updates:
                    return ToolResult(success=False, data={}, message="update 至少需要提供一项更新")
                async with get_db_context() as db:
                    await db.execute(
                        text(f"UPDATE cron_jobs SET {', '.join(updates)} WHERE id = :job_id"),
                        params
                    )
                    await db.commit()
                return ToolResult(success=True, data={"job_id": job_id}, message=f"✅ 任务 `{job_id}` 已更新")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 更新失败: {e}")

        elif action == "delete":
            job_id = kwargs.get("job_id", "")
            if not job_id:
                return ToolResult(success=False, data={}, message="delete 需要提供 job_id")
            try:
                async with get_db_context() as db:
                    await db.execute(text("DELETE FROM cron_jobs WHERE id = :job_id"), {"job_id": job_id})
                    await db.commit()
                return ToolResult(success=True, data={"job_id": job_id}, message=f"✅ 任务 `{job_id}` 已删除")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 删除失败: {e}")

        elif action == "toggle":
            job_id = kwargs.get("job_id", "")
            if not job_id:
                return ToolResult(success=False, data={}, message="toggle 需要提供 job_id")
            try:
                async with get_db_context() as db:
                    result = await db.execute(
                        text("UPDATE cron_jobs SET enabled = NOT enabled WHERE id = :job_id RETURNING enabled"),
                        {"job_id": job_id}
                    )
                    row = result.mappings().first()
                    await db.commit()
                    enabled = row["enabled"] if row else None
                return ToolResult(success=True, data={"job_id": job_id, "enabled": enabled}, message=f"✅ 任务 `{job_id}` 已{'启用' if enabled else '禁用'}"
                )
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 切换失败: {e}")

        return ToolResult(success=False, data={}, message=f"未知 action: {action}")
