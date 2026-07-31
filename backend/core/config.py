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

    # Database —— alpha 权威存储默认 SQLite（可离家 / 备份 / 工单状态机）
    # Redis 不是主库；见 docs/internal/STORAGE.md
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
    # Kernel 实现：rust = takton-kernel-host（默认倾向）；python = 进程内旧实现
    # 也可由环境变量 TAKTON_KERNEL_BACKEND 覆盖
    agent_kernel_backend: str = "rust"
    # P0-B：无显式 capabilities 时禁止静默全开——默认只读 Intent（grantable）
    # 关闭则恢复兼容模式（capabilities=None 全放行）
    agent_kernel_require_intent: bool = True
    # T2：run_gate 不可用/超时是否 fail-closed（禁止静默跳过全局并发门）
    agent_kernel_run_gate_required: bool = True
    # T1：host 可用时 Court 必须以 Rust 为准；失败则 deny（不静默 Python 放宽）
    agent_court_rust_required: bool = True
    # H-03：create_process 失败时 fail-closed（禁止退回无 kernel 工具路径）
    agent_kernel_fail_closed_on_create: bool = True
    # T7：远程包市场 catalog JSON URL（空=仅本地）
    agent_package_market_url: str = ""
    # 包签名密钥（≥16 字符）；空则 host 从 JWT 派生或 insecure_default
    agent_package_signing_key: str = ""
    # 远程包信任根：允许的内容 sha256 列表（逗号/空白分隔）；非空时远程安装必须命中
    agent_package_trusted_content_hashes: str = ""
    # 远程安装是否强制提供 content_sha256 查询参数或 catalog 字段
    agent_package_require_content_hash: bool = False
    # 资源加深：Linux cgroup v2 可选硬限（失败不阻断）
    agent_resource_cgroup_enabled: bool = False
    # 每轮工具后采样 RSS 并上报 memory_bytes（需 process_id）
    agent_resource_rss_sample: bool = True
    # 主会话默认 intent goal（require_intent 且未传 _intent_declaration 时）
    agent_kernel_default_intent_goal: str = "interactive chat (minimum privilege)"
    # Phase 2.3：启动时将非终态 Run 标 interrupted，并对 inbox/cron/headless 自动续跑
    agent_run_auto_recover: bool = True
    # ── 动态 skill 隔离（阶段 2）──
    # python handler 的沙箱策略：off=仅 AST+子进程 / auto=bwrap 有则用（默认）/
    # required=bwrap 不可用即拒绝执行
    agent_skill_sandbox: str = "auto"
    # ── Kernel 审计落盘（阶段 3）──
    agent_kernel_audit_persist: bool = True
    # 空 = 默认 ~/.takton/kernel_events.jsonl
    agent_kernel_audit_path: str = ""
    # 主进程能力显式化：开启后挂注册表全集快照（等效放行，
    # 但使 subagent 继承/narrow 生效）；Intent 最小权限落地的前置。
    # 0.4.1 起默认开启——新装 dynamic skill 首次使用将触发提权申请
    # （权限控制台批准后并入进程能力集）；设为 False 回退全放行兼容模式
    agent_kernel_explicit_capabilities: bool = True
    # 工具被能力集拦截时自动发起提权申请（用户在 /security 批准）
    agent_kernel_auto_escalate: bool = True
    # 0.5 编制与档案：进程档案/身份/checkpoint 持久化（sink 模式，失败不阻断）
    agent_kernel_persistence: bool = True
    # checkpoint 快照间隔（事件数）：恢复=快照+增量，禁止全量 replay
    agent_kernel_checkpoint_interval: int = 500
    # 多 worker 前提：观测 API 合并 DB 进程/提权
    agent_kernel_shared_state: bool = True
    # 多 worker 热共享（可选）：Redis 共享 mediate / charge_tokens / 能力集 / 提权
    # 默认 False —— 个人单进程 AIOS 只靠 SQLite 即可 durable。
    # 开启时需同时设置 redis_url；未装 redis 包或 ping 失败则静默回退内存
    #（业务表仍在 SQLite，不丢工单权威）。接口：shared_store.create_shared_store_from_settings
    agent_kernel_redis_shared: bool = False
    redis_url: str = ""  # 例 redis://127.0.0.1:6379/0
    # 开发剖面：aios-dev 时建议 dispatcher/kernel 全开（见 apply_aios_dev_profile）
    aios_profile: str = ""  # "" | "aios-dev"
    # 0.6 自主运转：收件箱/派遣器
    agent_dispatcher_enabled: bool = True
    agent_dispatcher_poll_seconds: float = 10.0
    agent_inbox_max_pending: int = 200  # 有界红线：超限丢弃最旧 pending
    agent_inbox_item_timeout: float = 600.0  # 单工单执行超时（秒）
    # F2 并发上限：全局同时在跑工单数；单身份默认串行（1）
    agent_dispatcher_max_global_concurrent: int = 8
    agent_dispatcher_max_identity_concurrent: int = 1
    # 异步兜底预算：身份未设默认预算时按此硬顶（0 = 显式不限，不推荐）
    agent_workforce_fallback_budget: int = 100_000
    # 编制预算硬顶（CEO 显式 / 自动抬升上限）；可用环境 TAKTON_WORKFORCE_BUDGET_HARD_CAP 覆盖
    agent_workforce_budget_hard_cap: int = 2_000_000
    # 弹性预算：用尽前自动 top_up，避免长任务硬撞死（CEO 仍可 budgets/top_up 治理）
    agent_budget_soft_renew_enabled: bool = True
    # H2-B4：硬顶模式 — 关闭 soft renew，预算用尽即停（与「硬顶」叙事一致）
    agent_budget_hard_cap_only: bool = False
    # 剩余不足预估 或 已用占比 ≥ 此阈值时尝试续航
    agent_budget_soft_renew_threshold: float = 0.85
    # 每次续航追加 = max(原预算 * factor, min_add, 缺口*2)
    agent_budget_soft_renew_factor: float = 1.0
    agent_budget_soft_renew_min_add: int = 200_000
    # 单进程最多自动续航次数（防无限烧）
    agent_budget_soft_renew_max: int = 12
    # H2-C1：CapabilityToken HMAC 专用密钥（≥16）；空则从 jwt_secret 派生
    agent_token_hmac_secret: str = ""
    # 演化分析阈值（Alpha Review #3：参数化——研发型/运营型身份工作模式
    # 不同，阈值应可调而非统一硬编码；默认值与 alpha 常量一致）
    agent_evolution_min_samples: int = 5
    agent_evolution_deprecate_denial_rate: float = 0.5
    agent_evolution_caps_adjust_approvals: int = 2
    agent_evolution_distill_min_done: int = 5
    agent_evolution_distill_min_success: float = 0.8
    agent_evolution_planner_tune_fail_rate: float = 0.3
    # P1（2026-07-29）：轨迹蒸馏 + 技能计分/回滚（evolution/distiller.py, scoreboard.py）
    agent_evolution_distill_enabled: bool = True
    agent_evolution_score_window: int = 50
    agent_evolution_score_min_samples: int = 8
    agent_evolution_regression_delta: float = 0.15
    # Phase 4.1：技能回放验证门禁（apply 前）
    agent_evolution_require_replay: bool = True
    agent_evolution_replay_max_tool_error_rate: float = 0.4
    agent_evolution_replay_min_body_chars: int = 120
    agent_evolution_replay_require_sections: bool = True
    # P2：进程无 charge 心跳超过该秒数 → list_processes 标 stalled
    agent_process_stall_seconds: float = 300.0
    agent_tool_parallel_max: int = 5
    # 研究任务收敛刹车：同 run 内同查询重复搜索，第 2 次提醒、第 3 次拦截
    agent_search_repeat_guard: bool = True
    # 单次 agent.run 内搜索类工具总调用上限；触顶后强制总结
    agent_search_max_per_run: int = 8
    # 词集合 Jaccard ≥ 此值视为近似同查询
    agent_search_similar_jaccard: float = 0.72
    # Kernel 事前预算检查：LLM 调用前预估消耗，剩余不足即事前中断
    #（llm_round 的事后 charge 是兜底，事前刹车防最后一次调用烧穿预算）
    agent_kernel_budget_precheck: bool = True
    # 事前预估的输出预留 token（输入按近期上下文 /3.4 粗估）
    agent_kernel_precheck_reserve: int = 2000
    # 身份记忆全量注入上限：条目数超此值改按工单相关性检索 top-k
    #（Alpha Review #4：防 prompt 膨胀；检索不可用回落全量截断）
    agent_identity_memory_full_inject_max: int = 8
    # ── LLM 公平调度（LlmAdmissionController）────────────────
    # 全局同时在飞的 LLM HTTP 请求数（≠ 工单并发）
    llm_max_in_flight: int = 4
    llm_max_in_flight_per_identity: int = 1
    # 从全局槽位中预留给主人对话（后台工单不得占满）
    llm_owner_reserve_slots: int = 1
    llm_queue_max: int = 64
    # 等待时间加权，防低优先级饿死
    llm_fairness_wait_weight: float = 1.0
    # 日 token 硬顶；0 = 不限制
    llm_daily_token_budget_global: int = 0
    llm_daily_token_budget_per_identity: int = 0
    # ── 编制记忆（CrewMemoryAssembler / Writer）──────────────
    crew_memory_experience_max_inject: int = 2
    crew_memory_experience_max_inject_chat: int = 1
    crew_memory_experience_max_chars: int = 800
    # 完工自动沉淀默认关（可信）；手动 distill-from-item 仍可用
    crew_memory_auto_distill: bool = False
    crew_memory_auto_distill_min_chars: int = 200
    # 自动沉淀若开：无 approved_by 则跳过（不静默写 distilled）
    crew_memory_require_approve_distill: bool = True
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
    # 无确认通道（cron / 渠道机器人 / webhook）时的兜底：allow | safe | deny
    #
    # 这条路径上没人可问，而它恰恰是**外部内容进入本机**的入口 ——
    # 邮件、群消息、网页里的提示词注入走的就是这里。旧默认 "allow" 等于
    # 整套权限规则在无人值守场景下完全不存在。
    #
    #   allow —— 全放行（0.3.x 旧行为；仅在你完全信任所有触发源时选）
    #   safe  —— 默认：读文件 / 写文件 / 搜索照常，
    #            但 shell·python·remote_exec·http·browser·desktop 一律拒绝。
    #            绝大多数定时任务（整理笔记、汇总报告）不受影响。
    #   deny  —— 全拒绝（最严；无人值守只做只读也不行）
    agent_permission_headless: str = "safe"
    agent_permission_enabled: bool = True
    # T4 prompt caching：Volatile 层（秒级时间戳/记忆）不并入 messages[0] 的 system 块，
    # 改挂 messages 尾部，保住可缓存的稳定前缀。设 False 回到旧的三层合并行为。
    agent_prompt_cache_friendly: bool = True
    # 向 Anthropic 请求写入 cache_control 断点（system / tools / 历史前缀）
    agent_prompt_cache_anthropic: bool = True
    # Qwen / MiniMax 显式 cache_control（默认关，避免兼容网关 400；命中后可开）
    agent_prompt_cache_qwen_explicit: bool = False
    agent_prompt_cache_minimax_explicit: bool = False
    # OpenAI 官方 prompt_cache_key（会话稳定 hash）
    agent_prompt_cache_openai_key: bool = True
    # Kernel 扣费优先 billable（cache miss + output），否则 raw prompt+completion
    agent_budget_prefer_billable: bool = True
    # file history after writes
    agent_file_history: bool = True
    # plan mode auto-detect complex tasks (soft; does not force mode alone)
    agent_auto_plan_complex: bool = False
    agent_auto_plan_simple_max_chars: int = 120
    # git worktree helpers available (opt-in tools later)
    agent_worktree_enabled: bool = True
    # Agent Computer（Phase 0.5.3）：command/python 走隔离执行后端
    # 兼容键：真正的开关是 agent_execution_mode。设为 True 时 auto 模式会优先尝试沙箱。
    # 默认 True：有能力就隔离；无能力时 auto 降级本机并在 UI/日志标明 degraded。
    agent_computer_enabled: bool = True
    # bwrap=Linux 沙箱（推荐，需 bubblewrap）；local=现状直跑
    # 沙箱后端：auto = 按平台自动选最强（linux→bwrap / darwin→seatbelt / win32→wsl|job）；
    # 也可显式指定 bwrap | seatbelt | job | wsl | docker | ssh | local
    agent_computer_backend: str = "auto"
    # 沙箱内是否放开网络（默认断网 --unshare-net）
    agent_computer_network: bool = False
    # P2a（2026-07-29）：docker/ssh 执行后端配置
    agent_computer_docker_image: str = "python:3.12-slim"
    agent_computer_ssh_host: str = ""      # user@host；空 = 未配置
    agent_computer_ssh_port: int = 22
    agent_computer_ssh_workdir: str = "~/takton-ws"
    # 沙箱档位：off | workspace | read_only | strict（见 computer/profiles.py）
    agent_sandbox_profile: str = "workspace"
    # Grok-style allow/ask/deny 规则（字符串列表或 JSON）
    agent_permission_allow: list | str = ""
    agent_permission_ask: list | str = ""
    agent_permission_deny: list | str = ""
    agent_permission_rules_json: str = ""
    # 显式关闭密钥路径硬拒绝（不推荐）
    agent_permission_relax_secrets: bool = False
    # 工作流默认 agent 调用预算
    agent_workflow_budget: int = 8
    # 真 Sub-Agent（Phase 1）：嵌套深度 / 总超时防失控
    agent_subagent_max_depth: int = 1
    agent_subagent_timeout_seconds: int = 300

    # SMTP（send_email skill）
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_use_tls: bool = True
    # 写文件工具前自动快照到 .takton/checkpoints/
    agent_file_checkpoint: bool = True
    # 搜索：有 Key 时 web_search/search 优先 Tavily
    tavily_api_key: str = ""
    # Agent 的 http / browser 工具是否禁止访问私网与回环。
    # 默认 False —— 本地优先产品里「让 Agent 看一眼我跑在 localhost:3000 的项目」
    # 「读一下 NAS 上的文件」是核心用法，照搬服务端 SSRF 防护会把它拦死。
    # 云厂商元数据端点（169.254.169.254 等）无论此开关如何都硬拦，
    # 那些地址在个人设备上没有任何正当用途。
    # 把 Takton 跑在服务器/共享主机上时建议设为 True。
    agent_block_private_network: bool = False

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
    # Qdrant collection 向量维；0=创建时按首次 embed 探测（推荐 Qwen3-Embedding-4B=2560）
    embedding_dimensions: int = 0

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
    # 允许的跨源 Origin（空格/逗号分隔）。默认空 = 只放行 loopback，
    # 覆盖 Electron 与 next dev，零配置即可用。
    # 把 Takton 开给局域网或自建域名时在这里加，例如：
    #   TAKTON_CORS_ALLOWED_ORIGINS="https://takton.mylan.home http://192.168.1.9:3000"
    # 设为 "*" 等于关闭跨源保护 —— 配合 single_user_mode 会让任意网页拿到 admin，
    # 只在你清楚后果时使用。
    cors_allowed_origins: str = ""
    # Phase 5 / D1：channel 入站文本上限（字符）；0 = 不限制（不推荐公开）
    channel_ingress_max_chars: int = 32_000
    # 入站剥离 NUL；拒绝全是控制字符的 payload
    channel_ingress_strip_nul: bool = True

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

    @model_validator(mode="after")
    def _apply_aios_dev_profile(self):
        """TAKTON_AIOS_PROFILE=aios-dev：打开编制/派活相关开关（Redis 仍默认关）。"""
        profile = (self.aios_profile or "").strip().lower()
        if profile != "aios-dev":
            return self
        object.__setattr__(self, "agent_kernel_enabled", True)
        object.__setattr__(self, "agent_kernel_persistence", True)
        object.__setattr__(self, "agent_kernel_shared_state", True)
        object.__setattr__(self, "agent_dispatcher_enabled", True)
        object.__setattr__(self, "agent_kernel_auto_escalate", True)
        # 明确不强制 Redis：aios-dev 默认仍走 SQLite 权威
        return self

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
