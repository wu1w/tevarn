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
    jwt_secret: str = "takton-dev-secret-key-2026"
    api_key: str = "takton-dev-api-key-2026"
    # Takton Code ↔ Desktop bridge 可选独立 Bearer token。
    # 留空 → 回落 get_current_user（single_user_mode 下 loopback 免 token）。
    # 设置后 → /bridge/v1/* 强制校验该 token（共享机/非 loopback 加固）。
    bridge_token: Optional[str] = None

    # LLM — 默认空，引导用户在设置页选择服务商
    llm_provider: Literal["ollama", "vllm", "openai", "anthropic", "openai-compatible"] = "openai-compatible"
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: Optional[str] = None
    # 新会话默认模型（学 hermes model.default）：独立选项，创建会话时快照用，
    # 与 provider 连接配置解耦；留空则用当前 provider 配置的 llm_model
    default_llm_model: str = ""
    default_max_tokens: int = 12288  # 生成上限默认 12K
    llm_temperature: float = 0.7
    # 当前模型上下文窗口（选模型时写入；用于截断/摘要/auto-optimize）
    context_window: int = 128000
    # Agent 多步工具循环上限（长链/编码任务需要更高，默认 40）
    agent_max_iterations: int = 40
    # Goal 模式额外轮次上限
    agent_goal_max_iterations: int = 100
    max_tool_result_length: int = 12_000
    # 单次工具执行超时（秒）；0 = 不限制
    agent_tool_timeout_seconds: float = 180.0
    # 用户单条输入硬上限（字符），超出截断并提示
    agent_max_user_input_chars: int = 100_000
    # 大输入 soft 策略：超过则保留头尾，中间省略（仍受硬上限约束）
    agent_large_input_soft_chars: int = 32_000
    # 每 N 个工具轮强制 checkpoint 一次
    agent_checkpoint_every: int = 5
    # 触顶 max_iterations 后是否自动开下一段
    agent_auto_continue: bool = True
    agent_auto_continue_max_segments: int = 5
    # 每 N 个工具轮即使未超阈值也做一次 L1（防慢膨胀）
    agent_midloop_l1_every: int = 3
    # 单次 agent.run 墙钟上限（秒）；0 = 不限制
    agent_max_duration_seconds: float = 0.0
    # LLM 调用失败重试次数（含首次）
    agent_llm_retry_attempts: int = 3
    # 对话默认工具面：core=白名单(~18) | full=全部注册工具
    agent_tool_profile: Literal["core", "dynamic", "full"] = "dynamic"
    # default 模式是否按复杂度自动集群（默认关，避免主脑被拆散）
    agent_auto_cluster: bool = False
    # 空正文重试 / 工具重复熔断（loop 读取，缺省有 fallback）
    agent_empty_reply_retries: int = 2
    agent_tool_repeat_max: int = 3

    # Context engine (Claude Code–style pipeline + Hermes meter)
    context_threshold_percent: float = 0.72
    context_protect_first_n: int = 3
    context_protect_last_n: int = 12
    context_max_tool_output_chars: int = 12_000
    context_enable_l1: bool = True
    context_enable_l3: bool = True
    context_enable_l5: bool = True
    # 空 = 使用主 LLM；可单独指定便宜模型做 L5 摘要
    context_compress_model: str = ""

    # Prompt-Skill 注入策略（商店安装的 SKILL.md）
    # summary=仅目录摘要 | auto=摘要+相关全文 | full=尽量全文（仍受限额）
    prompt_skill_mode: Literal["summary", "auto", "full"] = "auto"
    prompt_skill_max_full: int = 2  # 单轮最多注入几个全文 skill
    prompt_skill_full_max_chars: int = 6000  # 单个 skill 正文上限
    prompt_skill_match_threshold: float = 0.85  # auto 模式相关度阈值

    # Embedding — 默认空，未配置时不启用
    embedding_provider: Literal["ollama", "openai", "openai-compatible", ""] = ""
    embedding_base_url: str = ""
    embedding_model: str = ""
    embedding_api_key: Optional[str] = None

    # Reranker — 默认空，可选
    reranker_provider: Literal["local", "cohere", "openai-compatible", ""] = ""
    reranker_base_url: str = ""
    reranker_model: str = ""
    reranker_api_key: Optional[str] = None

    # Image Generation — 默认空
    image_provider: Literal["openai", "openai-compatible"] = "openai-compatible"
    image_base_url: str = ""
    image_model: str = ""
    image_api_key: Optional[str] = None

    # WebSocket
    ws_heartbeat_interval: int = 30

    # Session
    session_default_ttl_hours: int = 168  # 7 days

    # RAG Service class path (for factory injection)
    rag_service_class: str = "backend.services.rag.qdrant_impl.QdrantRAGService"

    # RAG / Qdrant
    # True=允许在 Embedding+Qdrant 已配置时启用向量 RAG；未配置时仍为 local 模式
    rag_enabled: bool = True
    qdrant_url: str = ""
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
