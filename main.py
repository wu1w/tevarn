"""
Project Nexus - FastAPI 应用入口
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

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
from backend.services.tools import ToolRegistry
from backend.core.rate_limit import RateLimitMiddleware
from backend.core.security_headers import SecurityHeadersMiddleware
from backend.core.simple_cors import SimpleCORSMiddleware
from backend.skills import SkillRegistry
from backend.skills.builtins import *  # noqa: F401 自动注册内置 Skill

# 使用结构化日志系统替代 basicConfig
setup_logging()
logger = logging.getLogger(__name__)


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


async def _seed_settings() -> None:
    """插入默认系统设置项（如果不存在）"""
    repo = AsyncSettingRepository()
    defaults = [
        # LLM
        ("llm_provider", "ollama", "llm", "LLM 服务提供商 (ollama / vllm / openai / anthropic / openai-compatible)"),
        ("llm_model", "llama3.2", "llm", "默认 LLM 模型"),
        ("llm_base_url", "http://localhost:11434", "llm", "LLM 服务基础地址"),
        ("llm_api_key", "", "llm", "LLM API Key（云服务商必填）"),
        ("max_tokens", 12288, "llm", "最大生成 Token 数（默认 12K）"),
        ("context_window", 128000, "llm", "当前模型上下文窗口（选模型时自动更新）"),
        ("temperature", 0.7, "llm", "采样温度 (0.0-2.0)"),
        # Embedding
        ("embedding_provider", "ollama", "embedding", "Embedding 服务提供商 (ollama / openai / openai-compatible)"),
        ("embedding_model", "nomic-embed-text", "embedding", "Embedding 模型"),
        ("embedding_base_url", "http://localhost:11434", "embedding", "Embedding 服务基础地址"),
        ("embedding_api_key", "", "embedding", "Embedding API Key（云服务商必填）"),
        # Reranker
        ("reranker_provider", "local", "reranker", "Reranker 服务提供商 (local / cohere)"),
        ("reranker_model", "bge-reranker-base", "reranker", "Reranker 模型"),
        ("reranker_base_url", "http://localhost:8001", "reranker", "Reranker 服务基础地址"),
        ("reranker_api_key", "", "reranker", "Reranker API Key（云服务商必填）"),
        # Image Generation
        ("image_provider", "openai-compatible", "image", "图片生成服务提供商 (openai / openai-compatible)"),
        ("image_model", "sd-xl", "image", "图片生成模型"),
        ("image_base_url", "http://localhost:7860", "image", "图片生成服务基础地址"),
        ("image_api_key", "", "image", "图片生成 API Key（云服务商必填）"),
        # RAG / Qdrant
        ("rag_enabled", True, "rag", "是否启用 RAG"),
        ("qdrant_url", "http://localhost:6333", "rag", "Qdrant 向量数据库地址"),
        ("qdrant_collection", "knowledge_base", "rag", "Qdrant collection 名称"),
        # General
        ("system_name", "Takton", "general", "系统名称"),
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
    except Exception as e:
        logger.warning(f"Skills seeding skipped: {e}")

    # v3.0: 加载统一工具注册表
    try:
        from backend.tools.loader import load_all_tools

        await load_all_tools()
    except Exception as e:
        logger.warning(f"Unified tool registry loading skipped: {e}")

    # v3.0: 连接 MCP Servers 并注册 MCP 工具
    try:
        from backend.mcp_hub.service import load_mcp_tools

        await load_mcp_tools()
    except Exception as e:
        logger.warning(f"MCP tools loading skipped: {e}")

    # Start cron scheduler
    try:
        from backend.services.cron_scheduler import scheduler as cron_scheduler
        asyncio.create_task(cron_scheduler.start())
        logger.info("Cron scheduler started")
    except Exception as e:
        logger.warning(f"Cron scheduler start skipped: {e}")

    yield  # 应用运行中

    # ---- Shutdown ----
    logger.info("Takton Backend Shutting down...")
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
    version="0.1.0",
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

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=True,
    )
