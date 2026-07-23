"""
Project Nexus - FastAPI 应用入口
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.api.routes import register_routes
from backend.api.websocket import router as ws_router
from backend.core.config import settings
from backend.core.exceptions import register_exception_handlers
from backend.core.logging_config import setup_logging
from backend.database import init_db
from backend.repositories.setting_repo import AsyncSettingRepository
from backend.repositories.skill_repo import AsyncSkillRepository
from backend.repositories.tool_repo import AsyncToolRepository
from backend.repositories.user_repo import AsyncUserRepository
from backend.services.tools import ToolRegistry
from backend.core.rate_limit import RateLimitMiddleware
from backend.core.security_headers import SecurityHeadersMiddleware
from backend.core.simple_cors import SimpleCORSMiddleware
from backend.skills import SkillRegistry
from backend.skills.builtins import *  # noqa: F401 自动注册内置 Skill

# 使用结构化日志系统替代 basicConfig
setup_logging()
logger = logging.getLogger(__name__)

# lifespan 后台任务句柄（shutdown 时取消，避免测试/热重载环关闭时报错）
_bg_tasks: set[asyncio.Task] = set()


def _spawn_bg(coro, name: str) -> None:
    """Fire-and-forget with logging; track for shutdown cancel."""
    async def _runner():
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("Background task %s failed: %s", name, e)

    try:
        task = asyncio.create_task(_runner(), name=name)
    except TypeError:
        # py3.10 may not accept name=
        task = asyncio.create_task(_runner())
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)



def _validate_secrets() -> None:
    """启动前强制检查密钥强度，防止默认弱密钥上线"""
    insecure_defaults = {
        "jwt_secret": "change-me",
        "api_key": "nexus-api-key-change-me",
    }
    for field, default in insecure_defaults.items():
        value = getattr(settings, field, "")
        if value == default:
            logger.error(
                f"SECURITY ERROR: {field} is using the default insecure value '{value}'. "
                f"Please set a strong random value via environment variable or .env file."
            )
            raise RuntimeError(
                f"{field} must be changed from the default insecure value. "
                f"Set it via environment variable before starting the application."
            )


async def _seed_default_user() -> None:
    """单用户模式下幂等地创建默认用户，避免首个请求并发创建冲突。"""
    if not settings.single_user_mode:
        return
    repo = AsyncUserRepository()
    default = await repo.get_by_email("admin@takton.dev")
    if default:
        logger.info(f"Default user already exists: {default.id}")
        return
    # 竞态防护：同时检查 username 唯一性（email 和 username 都有 UNIQUE 约束）
    existing_uname = await repo.get_by_username("admin")
    if existing_uname:
        logger.info(f"Default user 'admin' already taken by {existing_uname.id}, skipping seed")
        return
    from backend.core.security import get_password_hash
    import os
    default_pw = (
        (settings.default_admin_password or "").strip()
        or os.environ.get("TAKTON_DEFAULT_ADMIN_PASSWORD", "").strip()
        or "admin"
    )
    try:
        user = await repo.create({
            "email": "admin@takton.dev",
            "username": "admin",
            "hashed_password": get_password_hash(default_pw),
            "is_superuser": True,
            "is_active": True,
        })
        logger.info(f"Default user created: {user.id}")
    except Exception as e:
        # 并发创建冲突时忽略，由请求侧重试读取
        from sqlalchemy.exc import IntegrityError
        if isinstance(e, IntegrityError) and "UNIQUE" in str(e):
            logger.info(f"Default user seed race resolved: {e.orig}")
        else:
            logger.warning(f"Default user seeding skipped (will be resolved on first request): {e}")


async def _seed_settings() -> None:
    """插入默认系统设置项（如果不存在）"""
    repo = AsyncSettingRepository()
    defaults = [
        # LLM — 默认空，引导用户选择服务商
        ("llm_provider", "openai-compatible", "llm", "LLM 服务提供商 (ollama / openai / anthropic / openai-compatible)"),
        ("llm_model", "", "llm", "默认 LLM 模型"),
        ("llm_base_url", "", "llm", "LLM 服务基础地址"),
        ("llm_api_key", "", "llm", "LLM API Key（云服务商必填）"),
        ("max_tokens", 12288, "llm", "最大生成 Token 数（默认 12K）"),
        ("context_window", 128000, "llm", "当前模型上下文窗口（选模型时自动更新）"),
        ("temperature", 0.7, "llm", "采样温度 (0.0-2.0)"),
        # Embedding — 默认空，未配置时不启用
        ("embedding_provider", "", "embedding", "Embedding 服务提供商 (ollama / openai / openai-compatible)"),
        ("embedding_model", "", "embedding", "Embedding 模型"),
        ("embedding_base_url", "", "embedding", "Embedding 服务基础地址"),
        ("embedding_api_key", "", "embedding", "Embedding API Key（云服务商必填）"),
        # Reranker — 默认空，可选
        ("reranker_provider", "", "reranker", "Reranker 服务提供商 (local / cohere / openai-compatible)"),
        ("reranker_model", "", "reranker", "Reranker 模型"),
        ("reranker_base_url", "", "reranker", "Reranker 服务基础地址"),
        ("reranker_api_key", "", "reranker", "Reranker API Key（云服务商必填）"),
        # Image Generation
        ("image_provider", "openai-compatible", "image", "图片生成服务提供商 (openai / openai-compatible)"),
        ("image_model", "", "image", "图片生成模型"),
        ("image_base_url", "", "image", "图片生成服务基础地址"),
        ("image_api_key", "", "image", "图片生成 API Key（云服务商必填）"),
        # RAG / Qdrant — 默认关闭，配好 Embedding 后再开启
        ("rag_enabled", True, "rag", "允许向量 RAG（仍需配置 Embedding+Qdrant 才生效）"),
        ("qdrant_url", "", "rag", "Qdrant 向量数据库地址（空=本地模式，不启用向量 RAG）"),
        ("qdrant_collection", "knowledge_base", "rag", "Qdrant collection 名称"),
        # General
        ("system_name", "Takton", "general", "系统名称"),
        # Context engine
        ("context_threshold_percent", 0.72, "context", "上下文压缩触发阈值（相对 context_window）"),
        ("context_protect_first_n", 3, "context", "压缩时保护的头部非 system 消息数"),
        ("context_protect_last_n", 12, "context", "压缩时保护的尾部消息数"),
        ("context_max_tool_output_chars", 12000, "context", "单条 tool 输出最大字符（L1）"),
        ("context_enable_l1", True, "context", "启用 L1 tool 输出截断"),
        ("context_enable_l3", True, "context", "启用 L3 microcompact"),
        ("context_enable_l5", True, "context", "启用 L5 LLM 语义压缩"),
        ("context_compress_model", "", "context", "L5 压缩用模型（空=主模型）"),
    ]
    for key, value, category, description in defaults:
        existing = await repo.get_by_key(key)
        if existing is None:
            await repo.upsert(key, value, category, description)
            logger.info(f"Setting seeded: {key}")
        # 将过小的历史 max_tokens 默认值抬到 12K（仅当仍是旧默认 4096）
        elif key == "max_tokens" and existing.value in (4096, "4096", 2048, "2048"):
            await repo.upsert(key, 12288, category, description)
            logger.info("Setting upgraded max_tokens 4096 → 12288")


async def _seed_tools() -> None:
    """插入默认内置工具（如果不存在）"""
    repo = AsyncToolRepository()
    builtin_tools = ToolRegistry.get_builtin_tools()
    for data in builtin_tools:
        existing = await repo.get_tool_by_name(data["name"])
        if existing is None:
            await repo.create(data)
            logger.info(f"Tool seeded: {data['name']}")


async def _seed_skills() -> None:
    """将内存中注册的内置 Skill 同步到数据库"""
    repo = AsyncSkillRepository()
    for skill in SkillRegistry.get_all_skills():
        existing = await repo.get_skill_by_name(skill.name)
        if existing is None:
            await repo.create({
                "name": skill.name,
                "description": skill.description,
                "schema": skill.parameters,
                "enabled": True,
                "is_builtin": True,
                "handler": "http",
                "handler_config": {},
            })
            logger.info(f"Skill seeded: {skill.name}")
        elif getattr(existing, "is_builtin", None) is None:
            await repo.update(existing.id, {"is_builtin": True})



async def _seed_beginner_knowledge() -> None:
    """为默认用户写入小白知识库 + 产品配置手册（幂等：按 title 创建；已存在则刷新 meta.content）。

    结束后在后台为 seed 文档建向量索引（skip_wiki，避免卡住）。
    """
    from backend.repositories.user_repo import AsyncUserRepository
    from backend.repositories.knowledge_repo import AsyncDocumentRepository
    from backend.services.knowledge.beginner_seed import BEGINNER_KB_DOCS
    from backend.content.product_handbook import handbook_as_kb_docs

    users = AsyncUserRepository()
    docs = AsyncDocumentRepository()
    user = await users.get_by_email("admin@takton.dev")
    if user is None:
        logger.warning("Beginner KB seed skipped: no user")
        return
    existing = await docs.list_by_user(user.id) or []
    by_title = {getattr(d, "title", None): d for d in existing}
    items = list(BEGINNER_KB_DOCS) + handbook_as_kb_docs()
    created = 0
    updated = 0
    to_index: list[tuple[Any, str, str]] = []  # (doc, title, content)
    for item in items:
        title = item["title"]
        content = item["content"]
        meta = {
            "content": content,
            "seed": True,
            "audience": "beginner" if not title.startswith("[手册]") else "product",
            "seed_version": "0.1.1",
        }
        if title in by_title and by_title[title] is not None:
            doc = by_title[title]
            old_meta = getattr(doc, "meta", None) or {}
            if old_meta.get("seed") or getattr(doc, "source", "") == "builtin-seed":
                new_meta = dict(old_meta)
                new_meta.update(meta)
                try:
                    await docs.update(
                        doc.id,
                        {
                            "meta": new_meta,
                            "source": "builtin-seed",
                            "status": getattr(doc, "status", None) or "ready",
                        },
                    )
                    updated += 1
                    logger.info("Knowledge seed refreshed: %s", title)
                    # re-index if not indexed or content refreshed
                    st = getattr(doc, "status", "") or ""
                    if st != "indexed" or (old_meta.get("content") or "") != content:
                        to_index.append((doc, title, content))
                except Exception as e:
                    logger.warning("Knowledge seed refresh failed %s: %s", title, e)
            continue
        doc = await docs.create(
            {
                "user_id": user.id,
                "title": title,
                "status": "ready",
                "source": "builtin-seed",
                "meta": meta,
            }
        )
        created += 1
        logger.info("Knowledge seeded: %s", title)
        to_index.append((doc, title, content))
    logger.info(
        "Knowledge seed done: created=%s updated=%s total_items=%s to_index=%s",
        created,
        updated,
        len(items),
        len(to_index),
    )

    if to_index:
        # 刷新 list 拿最新 id（create 返回对象）
        try:
            _spawn_bg(_index_seed_documents(user.id, to_index), "seed_kb_index")
        except Exception as e:
            logger.warning("Schedule seed KB index failed: %s", e)


async def _index_seed_documents(user_id: Any, items: list) -> None:
    """Background: index builtin-seed docs with skip_wiki for speed/reliability."""
    from backend.services.knowledge.indexer import index_document_text

    ok_n = 0
    fail_n = 0
    for doc, title, content in items:
        try:
            doc_id = getattr(doc, "id", None)
            if doc_id is None:
                fail_n += 1
                continue
            result = await index_document_text(
                document_id=doc_id,
                title=title,
                text=content or "",
                user_id=user_id,
                source="builtin-seed",
                skip_wiki=True,
                replace_chunks=True,
            )
            if result.get("ok"):
                ok_n += 1
                logger.info("Seed indexed: %s chunks=%s", title, result.get("chunks"))
            else:
                fail_n += 1
                logger.warning(
                    "Seed index failed: %s → %s",
                    title,
                    result.get("message") or result.get("error"),
                )
        except Exception as e:
            fail_n += 1
            logger.warning("Seed index exception %s: %s", title, e)
    logger.info("Seed KB index finished: ok=%s fail=%s", ok_n, fail_n)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理（替代已弃用的 on_event）"""
    # ---- Startup ----
    _validate_secrets()

    logger.info("=" * 50)
    logger.info("Takton Backend Starting...")
    logger.info(f"LLM Provider: {settings.llm_provider}")
    logger.info(f"LLM Model: {settings.llm_model}")
    logger.info(f"RAG Service: {settings.rag_service_class}")
    logger.info("=" * 50)

    # Initialize database tables
    try:
        await init_db()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise RuntimeError(f"Cannot start without database: {e}") from e

    # Seed default user (single-user mode)
    try:
        await _seed_default_user()
    except Exception as e:
        logger.warning(f"Default user seeding skipped: {e}")

    # Seed default settings
    try:
        await _seed_settings()
    except Exception as e:
        logger.warning(f"Settings seeding skipped: {e}")

    # 将 DB 中的运行时设置加载进内存
    try:
        from backend.core.runtime_settings import load_settings_from_db

        applied = await load_settings_from_db()
        if applied:
            logger.info(
                "Runtime settings applied: LLM=%s/%s",
                settings.llm_provider,
                settings.llm_model,
            )
    except Exception as e:
        logger.warning(f"Runtime settings load skipped: {e}")

    # 模型目录
    try:
        from backend.core import model_catalog as model_catalog_mod
        from backend.repositories.setting_repo import AsyncSettingRepository

        cat = await model_catalog_mod.load_catalog(AsyncSettingRepository())
        if cat.get("active_provider_id") and cat.get("providers"):
            model_catalog_mod.apply_active_to_runtime(cat)
            logger.info(
                "Model catalog active: %s / %s",
                cat.get("active_provider_id"),
                cat.get("active_model"),
            )
    except Exception as e:
        logger.warning(f"Model catalog load skipped: {e}")

    # Seed default tools
    try:
        await _seed_tools()
    except Exception as e:
        logger.warning(f"Tools seeding skipped: {e}")

    # Seed built-in skills
    try:
        await _seed_skills()
        try:
            await _seed_beginner_knowledge()
        except Exception as e:
            logger.warning(f"Beginner knowledge seeding skipped: {e}")
    except Exception as e:
        logger.warning(f"Skills seeding skipped: {e}")

    # v3.0: 加载统一工具注册表（后台，不挡就绪）
    try:
        from backend.tools.loader import load_all_tools

        _spawn_bg(load_all_tools(), "load_all_tools")
    except Exception as e:
        logger.warning(f"Unified tool registry loading skipped: {e}")

    # v3.0: 连接 MCP Servers 并注册 MCP 工具（后台）
    try:
        from backend.mcp_hub.service import load_mcp_tools

        _spawn_bg(load_mcp_tools(), "load_mcp_tools")
    except Exception as e:
        logger.warning(f"MCP tools loading skipped: {e}")

    # Start cron scheduler
    try:
        from backend.services.cron_scheduler import scheduler as cron_scheduler
        _spawn_bg(cron_scheduler.start(), "cron_scheduler")
        logger.info("Cron scheduler started")
    except Exception as e:
        logger.warning(f"Cron scheduler start skipped: {e}")

    # Seed Wiki Graph 基础通识数据（仅空库时，后台）
    try:
        from backend.api.routes.wiki import ensure_wiki_seed
        _spawn_bg(ensure_wiki_seed(), "wiki_seed")
    except Exception as e:
        logger.warning(f"Wiki seeding skipped: {e}")

    # Start channel gateway (消息通道长连接，后台)
    try:
        from backend.services.channel_gateway import start_channel_gateway
        _spawn_bg(start_channel_gateway(), "channel_gateway")
    except Exception as e:
        logger.warning(f"Channel gateway start skipped: {e}")

    yield  # 应用运行中

    # ---- Shutdown ----
    logger.info("Takton Backend Shutting down...")

    # Cancel fire-and-forget startup tasks
    for t in list(_bg_tasks):
        t.cancel()
    if _bg_tasks:
        await asyncio.gather(*list(_bg_tasks), return_exceptions=True)
    _bg_tasks.clear()


    # Stop channel gateway
    try:
        from backend.services.channel_gateway import stop_channel_gateway
        await stop_channel_gateway()
    except Exception as e:
        logger.warning(f"Channel gateway stop warning: {e}")

    # 关闭 MCP 连接
    try:
        from backend.mcp_hub.client import get_mcp_manager

        await get_mcp_manager().close_all()
    except Exception as e:
        logger.warning(f"MCP manager close warning: {e}")

    # Stop cron scheduler
    try:
        from backend.services.cron_scheduler import scheduler as cron_scheduler
        await cron_scheduler.stop()
    except Exception as e:
        logger.warning(f"Cron scheduler stop warning: {e}")



# 创建 FastAPI 应用
app = FastAPI(
    title="Takton",
    description="个人专属异步 Agent 终端后端",
    version="0.2.6",
    lifespan=lifespan,
)

# CORS 配置
_ALLOWED_ORIGINS = os.environ.get(
    "CORS_ALLOWED_ORIGINS",
    "http://localhost:3000,http://localhost:3001,http://localhost:3002,http://localhost:3020,http://127.0.0.1:3000,http://127.0.0.1:3001,http://127.0.0.1:3002,http://127.0.0.1:3020,http://127.0.0.1:3000,http://127.0.0.1:3000"
).split(",")
_ALLOWED_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]
_ALLOWED_HEADERS = ["Content-Type", "Authorization", "X-Requested-With", "X-API-Key"]

# 安全响应头中间件
app.add_middleware(SecurityHeadersMiddleware)

# 速率限制中间件（基于用户ID+IP双维度滑动窗口）
app.add_middleware(
    RateLimitMiddleware,
    max_requests=300,
    window_seconds=60,
    exempt_paths={"/health", "/api/health", "/docs", "/openapi.json", "/api/docs", "/api/openapi.json"},
)

# 注册全局异常处理器
register_exception_handlers(app)

# 注册 REST 路由（统一入口）
register_routes(app, prefix="/api")

# 注册 WebSocket 路由
app.include_router(ws_router, prefix="/api")

# CORS 配置——放在所有路由之后，确保覆盖所有响应
app.add_middleware(
    SimpleCORSMiddleware,
)

# 静态文件服务：上传的附件、生成的PPT和报告
# 优先环境变量 / settings（桌面模式写入 userData），否则回退项目 uploads/
def _resolve_uploads_dir() -> str:
    env_dir = os.environ.get("TAKTON_UPLOADS_DIR", "").strip()
    if env_dir:
        return os.path.abspath(env_dir)
    cfg = (getattr(settings, "uploads_dir", None) or "").strip()
    if cfg:
        return os.path.abspath(cfg)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "uploads"))

UPLOADS_DIR = _resolve_uploads_dir()
os.makedirs(UPLOADS_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")

# 单体模式：在 API / uploads 之后挂载 Next 静态导出（若存在）
try:
    from backend.static_frontend import mount_frontend_static

    mount_frontend_static(app)
except Exception as _fe:
    logger.warning("Frontend static mount skipped: %s", _fe)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=True,
    )
