"""Project Nexus 全局配置管理
使用 pydantic-settings 从环境变量加载配置
"""

import json
import logging
import os
import secrets as _secrets
from pathlib import Path
from typing import Literal, Optional

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_logger = logging.getLogger(__name__)

# 公开仓库中出现过的已知弱密钥（显式设置这些值一律拒绝）
_KNOWN_WEAK_SECRETS = frozenset({
    "change-me",
    "change-me-in-production",
    "nexus-api-key-change-me",
    "takton-dev-secret-key-2026",
    "takton-dev-api-key-2026",
})


def _secrets_file_path() -> Path:
    override = os.environ.get("TAKTON_SECRETS_FILE", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".takton" / "secrets.json"


def _load_or_generate_secret(kind: str) -> str:
    """首次启动生成随机密钥并持久化到本地文件，之后复用（重启后已签发 token 不失效）。

    环境变量（TAKTON_JWT_SECRET 等）由 pydantic 优先于 default_factory 处理，
    本函数只兜底"未配置环境变量"的场景，保证默认值不再是源码里的已知字符串。
    """
    path = _secrets_file_path()
    try:
        data: dict = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        val = str(data.get(kind, "")).strip()
        if len(val) >= 16:
            return val
        data[kind] = _secrets.token_urlsafe(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.chmod(path, 0o600)
        _logger.info("Generated new %s and persisted to %s", kind, path)
        return data[kind]
    except Exception as e:
        # 只读文件系统等场景：退回纯随机值（重启后 token 失效，但绝不落已知默认值）
        _logger.warning("Cannot persist %s to %s (%s); using ephemeral random secret", kind, path, e)
        return _secrets.token_urlsafe(32)


def get_or_create_initial_admin_password() -> str:
    """非 Electron 部署首次创建默认用户时：随机生成管理员密码并持久化（0600）。

    只打印文件路径、不打印密码本身；用户首次登录后应立即修改。
    """
    path = _secrets_file_path().parent / "initial_admin_password"
    try:
        if path.exists():
            val = path.read_text(encoding="utf-8").strip()
            if val:
                return val
        pw = _secrets.token_urlsafe(12)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(pw, encoding="utf-8")
        os.chmod(path, 0o600)
        _logger.warning(
            "Initial admin password generated and written to %s — "
            "log in with admin@takton.dev and change it immediately",
            path,
        )
        return pw
    except Exception as e:
        # 无法持久化：用临时随机密码并明确告知（密码仅本次运行有效）
        pw = _secrets.token_urlsafe(12)
        _logger.warning(
            "Cannot persist initial admin password (%s); ephemeral password for THIS RUN ONLY: %s",
            e,
            pw,
        )
        return pw


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
        populate_by_name=True,  # 允许代码级 Settings(jwt_secret=...) 传参（alias 不屏蔽字段名）
    )

    # Database
    db_url: str = "sqlite+aiosqlite:///./takton.db"

    # Security
    # 默认随机生成并持久化到 ~/.takton/secrets.json（可用 TAKTON_SECRETS_FILE 覆盖），
    # 源码不再含已知默认密钥；alias 兼容旧变量名 TAKTON_SECRET_KEY（deprecated）。
    jwt_secret: str = Field(
        default_factory=lambda: _load_or_generate_secret("jwt_secret"),
        validation_alias=AliasChoices("TAKTON_JWT_SECRET", "TAKTON_SECRET_KEY"),
    )
    api_key: str = Field(
        default_factory=lambda: _load_or_generate_secret("api_key"),
        validation_alias=AliasChoices("TAKTON_API_KEY"),
    )
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
    # T1：同一轮内的只读工具并发执行（system_prompt 的 PARALLEL_TOOL_CALLS 段
    # 已向模型承诺并发；此前实现是串行 for 循环）。整批含写类工具时自动退回串行。
    agent_tool_parallel: bool = True
    # ── Agent Kernel（阶段 1/W1）──
    # Kernel 控制平面：loop 运行纳入 AgentProcess 生命周期管理 + 中介审计。
    # 关闭后退回纯旧路径（不影响功能，仅失去 kernel 可观测性）。
    agent_kernel_enabled: bool = True
    # ── 动态 skill 隔离（阶段 2）──
    # python handler 的沙箱策略：off=仅 AST+子进程 / auto=bwrap 有则用（默认）/
    # required=bwrap 不可用即拒绝执行
    agent_skill_sandbox: str = "auto"
    # ── Kernel 审计落盘（阶段 3）──
    agent_kernel_audit_persist: bool = True
    # 空 = 默认 ~/.takton/kernel_events.jsonl
    agent_kernel_audit_path: str = ""
    # 主进程能力显式化：开启后挂注册表全集快照（等效放行，
    # 但使 subagent 继承/narrow 生效）；Intent 最小权限落地的前置
    agent_kernel_explicit_capabilities: bool = False
    agent_tool_parallel_max: int = 5
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
    agent_tool_profile: Literal["coding", "assistant", "ops", "dynamic", "core", "full"] = "coding"
    # default 模式是否按复杂度自动集群（默认关，避免主脑被拆散）
    agent_auto_cluster: bool = False
    # 空正文重试 / 工具重复熔断（loop 读取，缺省有 fallback）
    agent_empty_reply_retries: int = 2
    agent_tool_repeat_max: int = 3
    # 集群复核（Phase 2 Agent Contract + Review Loop）：子代理交付物契约化 +
    # reviewer 独立复核；revise 触发有限次返工，reject 从综合输入剔除
    cluster_review_enabled: bool = True
    cluster_review_max_revise: int = 1
    # reviewer 专用模型（provider_id/model 格式；空 = 与综合共用默认服务）
    # 独立模型可避免「自己审自己」的同源偏差
    cluster_review_model_ref: str = ""
    # LLM HTTP 超时（压测病灶 B1：此前无显式超时，provider 故障挂 300s）
    llm_request_timeout_seconds: float = 120.0
    llm_connect_timeout_seconds: float = 10.0
    llm_stream_read_timeout_seconds: float = 180.0
    # cluster 准入配额（压测病灶 B2：此前全局 semaphore=5 共享、排队无上限无 429）
    # 同时运行的 cluster 上限；超限立即 429（诚实拒绝，不无限排队）
    cluster_max_concurrent: int = 3
    # doom-loop：同工具+近似参数连续 thrash（默认 on，替代/增强 ToolRepeatGuard）
    agent_doom_loop_enabled: bool = True
    # best-of-n：默认关；完整 fanout 依赖 worktree（Batch2）
    agent_best_of_n_enabled: bool = False
    # 用户消息中本地图片路径 → 多模态 parts
    agent_multimodal_images: bool = True
    agent_multimodal_max_images: int = 4
    # ── 工作方式 / 执行环境（T5：权限体系的两个正交开关，见 agent/working_mode.py）──
    # 用户只需选这两个；下面的 profile / ask_mode / 沙箱后端都由它们派生。
    # readonly | cautious | auto_edit | autonomous
    agent_working_mode: str = "cautious"
    # sandbox（强制隔离，不可用则报错）| auto（有则用，无则本机并明示）| local
    agent_execution_mode: str = "auto"

    # Batch2: permissions last-match profile
    # auto（跟随工作方式）|cautious|acceptEdits|dontAsk|free|plan
    # 显式设成非 auto 即为高级覆盖，会在权限控制台标记为 custom。
    agent_permission_profile: str = "auto"
    # ask 决策：auto（有确认通道则弹窗，否则走 headless 兜底）|interactive|local_allow|deny
    # 此前默认 local_allow —— 把所有 ask 静默降级为放行，使整套权限规则形同虚设。
    agent_permission_ask_mode: str = "auto"
    # 无确认通道（cron / 渠道机器人 / headless）时的兜底：allow | deny
    # 保持 allow 以免定时任务集体卡死；要更严就设 deny。
    agent_permission_headless: str = "allow"
    agent_permission_enabled: bool = True
    # T4 prompt caching：Volatile 层（秒级时间戳/记忆）不并入 messages[0] 的 system 块，
    # 改挂 messages 尾部，保住可缓存的稳定前缀。设 False 回到旧的三层合并行为。
    agent_prompt_cache_friendly: bool = True
    # 向 Anthropic 请求写入 cache_control 断点（system / tools / 历史前缀）
    agent_prompt_cache_anthropic: bool = True
    # file history after writes
    agent_file_history: bool = True
    # plan mode auto-detect complex tasks (soft; does not force mode alone)
    agent_auto_plan_complex: bool = False
    agent_auto_plan_simple_max_chars: int = 120
    # git worktree helpers available (opt-in tools later)
    agent_worktree_enabled: bool = True
    # Agent Computer（Phase 0.5.3）：command/python 走隔离执行后端
    # 兼容键：真正的开关是 agent_execution_mode。设为 True 等价于 execution_mode=sandbox，
    # 保留是为了旧配置/旧前端不炸；新代码一律走 working_mode.decide_sandbox()。
    agent_computer_enabled: bool = False
    # bwrap=Linux 沙箱（推荐，需 bubblewrap）；local=现状直跑
    # 沙箱后端：auto = 按平台自动选最强（linux→bwrap / darwin→seatbelt / win32→wsl|job）；
    # 也可显式指定 bwrap | seatbelt | job | wsl | local
    agent_computer_backend: str = "auto"
    # 沙箱内是否放开网络（默认断网 --unshare-net）
    agent_computer_network: bool = False
    # 真 Sub-Agent（Phase 1）：嵌套深度 / 总超时防失控
    agent_subagent_max_depth: int = 1
    agent_subagent_timeout_seconds: int = 300
    # 写文件工具前自动快照到 .takton/checkpoints/
    agent_file_checkpoint: bool = True
    # 搜索：有 Key 时 web_search/search 优先 Tavily
    tavily_api_key: str = ""

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

    # Memory Graph 二期
    memory_graph_auto_recall: bool = True  # 按用户输入自动召回注入 context（无命中回退静态提示）
    memory_graph_recall_limit: int = 3  # 单轮最多注入几条记忆
    memory_graph_auto_link: bool = True  # remember 时自动与相似节点建 related_to 边

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
    # 文件浏览器 / Agent 工具工作区根目录
    # 默认 "." = 项目根（编码助手可读 backend/ 等）；桌面模式由 Electron 注入 userData/workspace
    # 相对路径相对项目根解析；也可用环境变量 TAKTON_FILE_BROWSER_ROOT 覆盖
    file_browser_root: str = "."
    # 上传目录（桌面模式由 Electron 注入 userData/uploads）
    uploads_dir: str = ""
    # 单用户模式默认管理员密码（仅首次创建用户时使用；桌面由 Electron 注入随机值）。
    # 留空（默认）→ 首次创建用户时随机生成并写入 ~/.takton/initial_admin_password（0600）。
    default_admin_password: str = ""
    # 单用户模式（个人部署时无需登录）
    single_user_mode: bool = True

    @model_validator(mode="before")
    @classmethod
    def _warn_legacy_env_names(cls, data):
        """旧变量名 TAKTON_SECRET_KEY 仍生效但告警，引导迁移到 TAKTON_JWT_SECRET。"""
        if isinstance(data, dict) and "TAKTON_SECRET_KEY" in data:
            _logger.warning(
                "TAKTON_SECRET_KEY is deprecated; please rename to TAKTON_JWT_SECRET"
            )
        return data

    @field_validator("jwt_secret", "api_key", mode="after")
    @classmethod
    def _reject_default_secrets(cls, v: str, info) -> str:
        """拒绝公开仓库中出现过的已知弱密钥（无论通过何种途径注入）"""
        if v.strip() in _KNOWN_WEAK_SECRETS:
            raise ValueError(
                f"{info.field_name} is using a known insecure default value. "
                f"Please set a strong random value via environment variable "
                f"(e.g. TAKTON_{info.field_name.upper()})."
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
