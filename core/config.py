"""
Project Nexus 全局配置管理
使用 pydantic-settings 从环境变量加载配置
"""

from typing import Literal, Optional
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMConfig(BaseSettings):
    """LLM 通用配置基类"""

    base_url: str = "http://localhost:11434"
    model: str = "llama3.2"
    max_tokens: int = 4096
    temperature: float = 0.7
    api_key: Optional[str] = None


class OllamaConfig(LLMConfig):
    """Ollama 特定配置"""

    api_path: str = "/api/chat"


class VLLMConfig(LLMConfig):
    """vLLM (OpenAI 兼容) 特定配置"""

    api_path: str = "/v1/chat/completions"


class OpenAICompatibleConfig(LLMConfig):
    """通用 OpenAI 兼容服务配置"""

    api_path: str = "/v1/chat/completions"


class OpenAIConfig(LLMConfig):
    """OpenAI 官方配置"""

    base_url: str = "https://api.openai.com"
    api_path: str = "/v1/chat/completions"


class AnthropicConfig(LLMConfig):
    """Anthropic Claude 配置"""

    base_url: str = "https://api.anthropic.com"
    api_path: str = "/v1/messages"


class Settings(BaseSettings):
    """Nexus 全局配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_prefix="TAKTON_",  # 桌面模式通过 TAKTON_* 环境变量注入
        protected_namespaces=(),  # 允许 settings_encryption_salt 等含 "settings_" 前缀的字段名
    )

    # Database
    db_url: str = "sqlite+aiosqlite:///./takton.db"

    # Security
    jwt_secret: str = "change-me"
    api_key: str = "nexus-api-key-change-me"

    # LLM
    llm_provider: Literal["ollama", "vllm", "openai", "anthropic", "openai-compatible"] = "ollama"
    llm_base_url: str = "http://localhost:11434"
    llm_model: str = "llama3.2"
    llm_api_key: Optional[str] = None
    default_max_tokens: int = 12288  # 生成上限默认 12K
    llm_temperature: float = 0.7
    # 当前模型上下文窗口（选模型时写入；用于截断/摘要/auto-optimize）
    context_window: int = 128000
    # Agent 多步工具循环上限（长链/编码任务需要更高，默认 25）
    agent_max_iterations: int = 25
    # Goal 模式额外轮次上限
    agent_goal_max_iterations: int = 50
    max_tool_result_length: int = 12_000

    # Embedding
    embedding_provider: Literal["ollama", "openai", "openai-compatible"] = "ollama"
    embedding_base_url: str = "http://localhost:11434"
    embedding_model: str = "nomic-embed-text"
    embedding_api_key: Optional[str] = None

    # Reranker
    reranker_provider: Literal["local", "cohere"] = "local"
    reranker_base_url: str = "http://localhost:8001"
    reranker_model: str = "bge-reranker-base"
    reranker_api_key: Optional[str] = None

    # Image Generation
    image_provider: Literal["openai", "openai-compatible"] = "openai-compatible"
    image_base_url: str = "http://localhost:7860"
    image_model: str = "sd-xl"
    image_api_key: Optional[str] = None

    # WebSocket
    ws_heartbeat_interval: int = 30

    # Session
    session_default_ttl_hours: int = 168  # 7 days

    # RAG Service class path (for factory injection)
    rag_service_class: str = "backend.services.rag.qdrant_impl.QdrantRAGService"

    # RAG / Qdrant
    rag_enabled: bool = True
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "knowledge_base"
    # 多 Collection 路由（key=逻辑名, value=Qdrant collection 名）
    qdrant_collections: dict[str, str] = {
        "knowledge": "knowledge_base",
        "wiki": "wiki_pages",
        "session": "session_history",
        "feishu": "feishu_messages",
    }
    # 默认检索范围（逻辑名列表）
    rag_default_collections: list[str] = ["knowledge", "wiki"]
    # 检索模式: hybrid (BM25+Vector+RRF) | vector | keyword
    rag_search_mode: str = "hybrid"
    # 查询变换
    rag_query_transform: bool = True
    rag_hyde_enabled: bool = False
    rag_query_expansion: bool = True
    rag_decompose_enabled: bool = False
    # 上下文注入策略
    rag_min_score: float = 0.5
    rag_max_context_tokens: int = 4000
    rag_source_weights: dict[str, float] = {
        "knowledge": 1.0,
        "wiki": 0.8,
        "session": 0.6,
        "feishu": 0.5,
    }
    rag_deduplicate: bool = True

    # Workflow
    enable_python_execution: bool = False  # 安全：默认禁用 Python 代码执行节点，防止 RCE

    # Application
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    log_level: str = "info"
    # 设置加密 salt（用于 settings 字段加密的确定性密钥派生）
    settings_encryption_salt: str = ""
    # 文件浏览器根目录（桌面模式由 Electron 注入 userData/workspace）
    # 默认使用相对路径 workspace，跨平台可写
    file_browser_root: str = "workspace"
    # 上传目录（桌面模式由 Electron 注入 userData/uploads）
    uploads_dir: str = ""
    # 单用户模式默认管理员密码（仅首次创建用户时使用；桌面由 Electron 注入）
    default_admin_password: str = "admin"
    # 单用户模式（个人部署时无需登录）
    single_user_mode: bool = True

    @field_validator("jwt_secret", "api_key", mode="after")
    @classmethod
    def _reject_default_secrets(cls, v: str, info) -> str:
        """禁止在生产环境使用默认弱密钥"""
        field_name = info.field_name
        defaults = {
            "jwt_secret": "change-me",
            "api_key": "nexus-api-key-change-me",
        }
        default_value = defaults.get(field_name, "")
        if v == default_value:
            raise ValueError(
                f"{field_name} is using the default insecure value '{v}'. "
                f"Please set a strong random value via environment variable."
            )
        return v

    def get_llm_config(self) -> LLMConfig:
        """根据 llm_provider 返回对应的 LLM 配置实例"""
        common = {
            "base_url": self.llm_base_url,
            "model": self.llm_model,
            "max_tokens": self.default_max_tokens,
            "temperature": self.llm_temperature,
            "api_key": self.llm_api_key,
        }
        if self.llm_provider == "ollama":
            return OllamaConfig(**common)
        if self.llm_provider == "openai":
            return OpenAIConfig(**common)
        if self.llm_provider == "anthropic":
            return AnthropicConfig(**common)
        return OpenAICompatibleConfig(**common)


# 全局单例
settings = Settings()
