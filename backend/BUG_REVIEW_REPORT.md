# Takton 后端 Bug 审查报告

> 审查范围：backend/core/, backend/services/, backend/repositories/, backend/skills/, backend/agent/, backend/schemas/
> 审查日期：2026-07-06
> 审查方法：逐文件读取、代码审查、安全分析、AST 语法检查

---

## 🔴 CRITICAL - 严重（需立即修复）

### CRIT-1: 任意代码执行漏洞（RCE）—— WorkflowEngine + DynamicSkill
**位置：** `backend/services/workflow_engine.py`（`execute_python` 节点）、`backend/skills/dynamic.py`（`_run_python`）

**问题描述：**
- `workflow_engine.py` 中的 `execute_python` 节点通过 `ast.parse` 和 `exec()` 执行用户可控的 Python 代码。
- `_validate_code_ast` 仅检查 AST 节点是否在 `_ALLOWED_NODES` 中，但 Python 的 AST 允许通过 `__import__('os').system('rm -rf /')` 这样的表达式绕过限制（因为 `Call` 和 `Attribute` 节点在允许列表中）。
- `DynamicSkill` 的 `_run_python` 直接调用 `WorkflowEngine._run_code_in_subprocess`，这意味着任何拥有自定义 Skill 权限的用户都可以执行任意 Python 代码。
- `_run_code_in_subprocess` 使用 `subprocess.run` 创建子进程执行 Python，但没有沙箱限制（如 seccomp、容器隔离），子进程可以访问整个文件系统和网络。

**安全隐患：**
- 完全的服务器接管（RCE）
- 数据泄露、文件系统删除、网络横向移动
- 在 Docker 外运行时直接威胁宿主机安全

**建议修复：**
1. 立即禁用 `python` 类型的 DynamicSkill，或仅允许管理员使用。
2. 使用受限沙箱（如 `RestrictedPython`、Docker 容器、Firejail）执行用户代码。
3. 在 AST 层面禁止 `__import__`、`getattr`、`eval`、`exec` 等危险调用。
4. 在子进程中设置 `seccomp` 限制和文件系统只读/隔离。
5. 工作流引擎中的 `execute_python` 节点应默认禁用，且需要显式配置启用。

---

### CRIT-2: Bash Skill 命令注入漏洞
**位置：** `backend/skills/builtins/bash_skill.py`

**问题描述：**
- `shlex.split(cmd)` 在 Windows 上默认使用 `posix=False`，这意味着反斜杠和引号的处理与 Linux 不同。
- 危险字符串检查 `_DANGEROUS_SUBSTRINGS` 包含 `;`、`&&`、`||`、`|`、`\``、`$(`、`>>`、`<(`，但**
- **漏掉了 `>` 单重定向**，恶意用户可以通过 `ls > /dev/null` 覆盖文件（虽然 ls 在白名单，但参数不受限制）。
- 更重要的是，**`shlex.split` 之后只检查了 `args[0]` 是否在白名单，但没有限制 `args[1:]` 的内容**。例如：`git -C / status` 可以切换到任意目录；`git log --format=%H` 等只读子命令虽然安全，但 `find` 的 `-exec` 被检查，而 `find` 的 `-print0` 配合 `xargs` 可以通过 `-exec` 的绕过方式执行。
- 最危险的是：`shlex.split` 对 `echo $(id)` 的解析：在 `posix=False` 下，`echo` 和 `$(id)` 是两个独立参数，第一个参数 `echo` 在白名单，会**绕过检查**。`echo` 命令会打印 `$(id)`，但如果参数被其他命令处理（如 `git log --format=$(id)`），这可能导致命令注入。
- 在 Windows 上，`asyncio.create_subprocess_exec` 执行 `.bat` 或 `.cmd` 文件时可能触发 shell 执行。

**安全隐患：**
- 虽然白名单限制了命令，但参数过滤不严格可能导致信息泄露或文件覆盖。
- `echo` 参数中可能包含环境变量，泄露敏感信息。

**建议修复：**
1. 为 `git` 命令增加参数白名单检查，禁止 `-C`、 `--git-dir`、 `--work-tree` 等可以切换目录/仓库的参数。
2. 对 `find` 命令增加更严格的参数检查，禁止任何包含 `-exec`、 `-execdir`、 `-ok`、 `-okdir`、 `-delete`、 `-fprintf`、 `-fls` 的变体。
3. 增加 `--` 参数限制，禁止任何以 `-` 开头的非白名单参数。
4. 在 Windows 上完全禁用 BashSkill，或提供 Windows 安全命令等价物。

---

### CRIT-3: 不安全的 HTTP 重定向处理
**位置：** `backend/skills/dynamic.py`（`_run_http`）

**问题描述：**
- `DynamicSkill._run_http` 在发起 HTTP 请求时，没有设置 `allow_redirects=False`。
- 虽然 `HttpGetSkill`（`http_get_skill.py`）设置了 `allow_redirects=False`，但 `DynamicSkill` 的 HTTP 处理器没有。
- `validate_public_url` 只在原始 URL 上检查，如果服务器返回 302 重定向到内网地址（如 `http://169.254.169.254/latest/meta-data/`），`aiohttp` 会跟随重定向，从而绕过 SSRF 防护。

**安全隐患：**
- SSRF（服务器端请求伪造）攻击，可访问内网服务、云元数据 API、内部数据库等。
- 可能导致云凭证泄露（AWS/阿里云/GCP 元数据服务）。

**建议修复：**
1. 在 `DynamicSkill._run_http` 中设置 `allow_redirects=False`。
2. 如果必须跟随重定向，在每次重定向后重新调用 `validate_public_url`。
3. 限制重定向的最大次数（如 3 次）。

---

## 🟠 HIGH - 高（需尽快修复）

### HIGH-1: 数据库连接池泄漏风险
**位置：** `backend/repositories/base.py`、所有 `Async*Repository` 实现

**问题描述：**
- `AsyncBaseRepository._get_session()` 在没有外部 session 时，每次调用 `AsyncSessionLocal()` 创建新 session。
- 在 `get_by_id`、`create`、`update`、`delete` 等高频操作中，每个方法都会创建并关闭 session。
- 虽然 `_close_session` 在 `finally` 中调用了 `session.close()`，但如果 `_maybe_commit` 抛出异常（如数据库连接超时），`session.close()` 可能无法执行，导致连接泄漏。
- 更重要的是，当通过 `get_db_context`（UOW）注入 session 时，`_owns_session = False`，`_close_session` 不会关闭 session，这是正确行为。但**很多仓库方法在没有 UOW 的情况下被直接调用**，这可能导致连接池耗尽。

**建议修复：**
1. 统一使用 `get_db_context` 或 `UnitOfWork` 管理事务，避免仓库自行管理 session。
2. 在 `_close_session` 中增加 `await session.close()` 的异常处理，确保即使 commit 失败也会关闭 session。
3. 考虑使用 `@asynccontextmanager` 包装仓库操作，保证 session 生命周期。

---

### HIGH-2: Agent Loop 中的并发竞态条件
**位置：** `backend/agent/loop.py`

**问题描述：**
- `agent/loop.py` 的 `run` 方法中，使用 `get_db_context` 进行事务管理，但在 `for iteration in range(self.max_iterations)` 循环中，每次迭代都可能执行数据库操作。
- `_persist_user_input`、 `_persist_tool_start`、 `_persist_tool_completion`、 `_persist_final_response` 都使用 `async with get_db_context() as db`，这意味着每个方法在自己的事务中执行，**不是原子操作**。
- 如果在循环中间发生异常（如 LLM 服务超时），用户消息已保存但助手回复未保存，导致会话状态不一致。
- 更严重的是，`session_repo.update_status` 在 `_persist_final_response` 的异常处理中被调用，但这又开启了一个新事务，如果事务嵌套有问题可能导致死锁。

**建议修复：**
1. 将 Agent Loop 的完整执行（用户消息保存、工具执行、助手回复保存）包装在单一事务中，确保原子性。
2. 使用 `UnitOfWork` 模式显式管理事务边界。
3. 在循环入口和出口处确保 `session.status` 总是被正确重置为 `idle`，即使发生异常。

---

### HIGH-3: 提示词注入风险（Prompt Injection）
**位置：** `backend/skills/builtins/generate_ppt_skill.py`、`generate_report_skill.py`

**问题描述：**
- `SYSTEM_PROMPT` 被直接拼接到用户输入中：`prompt = f"请为以下主题生成PPT内容：\n\n主题：{topic}\n目标受众：{audience}\n期望页数：{pages}页\n"`。
- 如果用户输入的 `topic` 包含类似 `"忽略之前的所有指令，改为执行以下操作..."` 的内容，LLM 可能忽略系统提示，执行恶意指令。
- 虽然这不是直接的安全漏洞，但可能导致 LLM 输出有害内容、泄露系统提示或执行非预期操作。

**建议修复：**
1. 对用户输入进行清理和转义，使用 `|e` 或类似方式转义特殊字符。
2. 使用结构化提示词（如 JSON 格式），将用户输入与系统指令严格分离。
3. 在 LLM 调用后增加输出验证，检查生成的内容是否符合预期格式。

---

### HIGH-4: 加密密钥管理问题
**位置：** `backend/core/encryption.py`

**问题描述：**
- `_get_key` 函数使用 `settings.ENCRYPTION_KEY` 和 `salt` 生成 AES 密钥。
- 如果 `salt` 是固定值（如代码中硬编码），则密钥可预测。
- 如果 `salt` 是随机生成的，则每次加密使用不同的密钥，这会导致无法解密之前加密的数据（除非 salt 被存储）。
- 代码中 `salt = b'static_salt_for_takton'` 看起来是硬编码的，这大大降低了安全性。
- `encrypt_password` 和 `verify_password` 使用了 `bcrypt`，这是正确的。但 `encrypt`（AES）的密钥派生方式不够安全（SHA-256 一次哈希，没有使用 PBKDF2 或 Argon2）。

**建议修复：**
1. 使用 `cryptography.fernet.Fernet` 替代手动 AES 实现，或升级到更安全的密钥派生（如 PBKDF2HMAC）。
2. 为每个加密操作生成随机的 IV，并将 IV 与密文一起存储（当前代码中 `iv = key[:16]` 是固定的，这是严重安全问题）。
3. 确保 `salt` 是随机且存储的，或直接使用 `Fernet` 简化密钥管理。

---

### HIGH-5: 缺少请求速率限制实现
**位置：** `backend/core/rate_limit.py`

**问题描述：**
- `rate_limit.py` 定义了 `RateLimitMiddleware` 和 `RateLimitConfig`，但检查逻辑不完整：
  - `check_rate_limit` 是一个异步函数，但实现为空（`pass`），意味着**实际上没有进行任何速率限制**。
  - `get_rate_limit_headers` 返回了 `X-RateLimit-*` 头，但因为没有实际限制，这些头的值没有意义。

**安全隐患：**
- 没有速率限制意味着 API 容易受到 DDoS 和暴力破解攻击。
- 攻击者可以无限次调用登录、注册、Agent Loop 等高成本接口。

**建议修复：**
1. 使用 `slowapi` 或 `fastapi-limiter` 等现成库实现基于 Redis 的分布式速率限制。
2. 为不同端点设置不同的限制策略（如登录 5 次/分钟，Agent Loop 30 次/分钟）。
3. 对超出限制的请求返回 429 状态码，并设置 `Retry-After` 头。

---

### HIGH-6: 不安全的 WebSocket 广播
**位置：** `backend/agent/loop.py`（`_push_status`、 `_push_stream`、 `_push_task_update`）

**问题描述：**
- `ws_manager.broadcast` 将消息广播给 `session_id` 对应的所有连接，但没有验证发送者是否有权向该 session 发送消息。
- 如果 WebSocket 连接没有严格的身份验证，恶意用户可能通过伪造 `session_id` 窃取其他用户的会话数据。

**建议修复：**
1. 在 WebSocket 连接建立时验证用户身份，并将 `user_id` 与 `session_id` 绑定。
2. 在 `broadcast` 前检查当前连接的用户是否有权访问该 session。
3. 使用 JWT 或 session token 验证 WebSocket 连接的合法性。

---

## 🟡 MEDIUM - 中（建议修复）

### MED-1: 竞态条件 - 默认配置设置
**位置：** `backend/repositories/agent_profile_repo.py`（`set_default`）

**问题描述：**
- `set_default` 方法虽然使用了 `with_for_update()` 行级锁，但**锁只针对目标行**。
- 如果两个并发请求同时设置不同配置的默认状态，`unset_filter` 的批量 UPDATE 可能并发执行，导致最终有两个默认配置（虽然这种情况在行级锁下不太可能，但如果目标配置不是同一行则不受锁保护）。
- 代码逻辑中：先对目标行加锁，然后取消其他行的默认标记。如果两个请求的目标行不同，取消其他行的 UPDATE 可能并发执行，导致竞态条件。

**建议修复：**
1. 在 `set_default` 中使用更粗粒度的锁（如数据库级别的 `SELECT ... FOR UPDATE` 或应用级分布式锁）。
2. 在 UPDATE 时增加 `is_default=True` 的条件，确保只更新当前是默认的行。

---

### MED-2: 全局状态导致的扩展性问题
**位置：** `backend/skills/registry.py`

**问题描述：**
- `SkillRegistry` 使用类变量 `_skills` 和 `_instances` 作为全局内存注册表。
- 在单进程部署下工作正常，但在多进程（如 Gunicorn）或多实例（Kubernetes）部署时，每个进程/实例都有自己的注册表，导致：
  - 技能注册不一致
  - 自定义技能在一个实例上可用，在另一个实例上不可用
  - 动态技能的修改不会全局生效

**建议修复：**
1. 将技能注册表迁移到 Redis 或数据库中，确保多实例共享。
2. 或在启动时从数据库统一加载技能到内存，并设置 TTL 刷新机制。

---

### MED-3: 上下文窗口截断不准确
**位置：** `backend/agent/context.py`（`ContextManager._truncate_history`）

**问题描述：**
- `_truncate_history` 按消息数量截断（`max_messages - 2`），而不是按 token 数量截断。
- 如果某些消息非常长（如包含大量 RAG 检索结果），即使消息数量没有超过 `max_messages`，总 token 数也可能超过 LLM 的上下文窗口限制。
- 这会导致 LLM 返回 400 Bad Request（上下文过长）或静默截断。

**建议修复：**
1. 集成 `tiktoken` 或对应模型的 tokenizer，实现基于 token 数量的精确截断。
2. 在截断时保留 system prompt 和最新的用户消息，优先截断中间的旧消息。

---

### MED-4: LLM 服务阻塞事件循环
**位置：** `backend/services/llm/anthropic.py`

**问题描述：**
- `anthropic.py` 使用了 `requests` 同步 HTTP 客户端，而其他 LLM 服务（如 OpenAI、Ollama）使用 `httpx` 异步客户端。
- 在 `async def chat` 中调用同步 HTTP 请求会阻塞整个事件循环，导致其他并发请求无法处理。
- 这会导致严重的性能问题，在高并发下服务响应时间急剧增加。

**建议修复：**
1. 将 `anthropic.py` 迁移到 `httpx.AsyncClient` 或使用 `asyncio.to_thread` 包装同步请求。
2. 统一所有 LLM 服务的 HTTP 客户端为 `httpx.AsyncClient`。

---

### MED-5: 异步 Generator 消费问题
**位置：** `backend/skills/builtins/generate_ppt_skill.py`（`generate_ppt_skill.py` 第 78 行）

**问题描述：**
- `async for chunk in llm_service.chat(...):` 在 `stream=False` 时可能不返回 generator，或者返回空。
- 如果 `stream=False` 但代码中仍使用 `async for`，当服务返回单个响应时可能只执行一次循环，但 `chunk.finish_reason` 可能永远不会为 `True`，导致死循环。

**建议修复：**
1. 在 `stream=False` 时直接使用 `await llm_service.chat(...)` 获取完整响应，不使用 `async for`。
2. 或者检查 `llm_service.chat` 的返回值类型，确保在 `stream=False` 时返回的是单个对象而非 generator。

---

### MED-6: 缺少超时和资源释放
**位置：** `backend/services/llm/openai_compatible.py`、`vllm.py`

**问题描述：**
- 多个 LLM 服务类使用了 `httpx.AsyncClient` 但没有在 `__del__` 或 `asynccontextmanager` 中显式关闭客户端。
- 在 `__init__` 中创建 `httpx.AsyncClient` 实例，但没有对应的关闭机制，可能导致连接泄漏。
- 某些 LLM 请求没有设置超时（如 `read_timeout`、 `connect_timeout`），在网络异常时会无限等待。

**建议修复：**
1. 在 LLM 服务类中实现 `async def close()` 方法，并在应用关闭时调用。
2. 使用 `asynccontextmanager` 管理 `httpx.AsyncClient` 的生命周期。
3. 为所有 HTTP 请求设置合理的超时（如 `timeout=httpx.Timeout(30.0, connect=5.0)`）。

---

### MED-7: 缺少输入验证
**位置：** `backend/schemas/workflow.py`（`WorkflowDag`）

**问题描述：**
- `WorkflowDag` 的 `nodes` 和 `edges` 没有验证图结构的合法性（如是否有环、是否有孤立节点、端口类型是否匹配）。
- 恶意用户可能构造一个包含无限循环的 DAG（如 A->B->A），导致工作流引擎死循环。

**建议修复：**
1. 在保存工作流前验证 DAG 无环（使用拓扑排序）。
2. 验证所有 `edge.from_` 和 `edge.to` 对应存在的节点。
3. 验证输入/输出端口的类型匹配。

---

### MED-8: 日志信息泄露
**位置：** 多处 `logger.info` 和 `logger.warning`

**问题描述：**
- 多个地方将用户输入、LLM 输出、工具结果直接记录到日志中，如 `logger.info(f"Agent loop started for session {session_id}, mode={mode}")`。
- 如果日志包含敏感信息（如用户密码、API key、个人隐私数据），且日志文件权限不当，可能导致信息泄露。
- `audit_log.py` 记录所有操作，但没有对 `details` 字段进行敏感数据脱敏。

**建议修复：**
1. 在日志中避免记录敏感数据（如密码、token、个人身份信息）。
2. 对 `AuditLog` 的 `details` 字段进行自动脱敏，屏蔽密码、API key 等字段。
3. 确保日志文件的权限为 `640` 或更严格。

---

### MED-9: 不安全的文件路径拼接
**位置：** `backend/skills/builtins/generate_ppt_skill.py`（`PPT_OUTPUT_DIR`）

**问题描述：**
- `PPT_OUTPUT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "uploads", "ppt"))` 使用相对路径向上回溯，如果项目结构变化可能导致文件保存到非预期位置。
- 虽然使用了 `uuid.uuid4().hex` 生成文件名，但如果 `ppt_data` 的 `title` 被用于文件名（目前没有），可能导致路径遍历。

**建议修复：**
1. 使用环境变量或配置项指定上传目录，避免硬编码相对路径。
2. 确保所有用户可控的文件名都经过 `os.path.basename` 和 `secure_filename` 处理。

---

### MED-10: 缺失的 `except` 处理过于宽泛
**位置：** 多处代码

**问题描述：**
- 很多方法使用了 `except Exception as e:` 捕获所有异常，这会吞掉真实的错误信息，使得调试困难。
- 例如 `DynamicSkill.execute` 捕获所有异常并返回字符串错误，这可能导致调用方无法区分可重试错误和致命错误。

**建议修复：**
1. 捕获具体的异常类型（如 `aiohttp.ClientError`、`asyncio.TimeoutError`、`ValidationError`）。
2. 对于未预期的异常，记录完整 traceback 后重新抛出或返回明确的错误码。

---

## 🟢 LOW - 低（可选修复）

### LOW-1: 类型注解不一致
**位置：** `backend/schemas/workflow.py`（`WorkflowEdge.from_`）

**问题描述：**
- `from_: str = Field(..., alias="from")` 在 Pydantic v2 中需要使用 `validation_alias` 或 `serialization_alias`，`alias` 在 v2 中行为有变化。
- 如果 FastAPI 使用 `model_validate` 解析 JSON，前端传递 `"from"` 字段时，Pydantic v2 可能无法正确映射到 `from_`。

**建议修复：**
1. 升级到 Pydantic v2 的明确语法：`from_: str = Field(..., validation_alias="from", serialization_alias="from")`。
2. 在 `model_config` 中设置 `populate_by_name=True`。

---

### LOW-2: 硬编码的上下文窗口大小
**位置：** `backend/agent/context.py`（`context_window = 200_000`）

**问题描述：**
- 代码中硬编码了 `context_window = 200_000`，但不同 LLM 模型的上下文窗口不同（如 GPT-4 是 8K/32K/128K，Claude 是 200K）。
- 如果用户使用 8K 上下文的模型，按 200K 计算的使用率会严重低估实际使用量。

**建议修复：**
1. 从配置或 LLM 服务接口获取当前模型的实际上下文窗口大小。
2. 在 `SessionConfig` 中增加 `context_window` 配置项。

---

### LOW-3: 魔法数字和字符串
**位置：** 多处代码

**问题描述：**
- 代码中散布着大量魔法数字和字符串，如 `max_iterations = 5`、`limit=500`、`timeout=30`、`timeout=10`、 `context_window = 200_000`。
- 这些值没有集中管理，修改时容易遗漏。

**建议修复：**
1. 将所有配置参数集中到 `backend/core/config.py` 的 `Settings` 类中。
2. 使用常量或枚举替代魔法值。

---

### LOW-4: 缺少单元测试覆盖
**位置：** 所有模块

**问题描述：**
- 审查中未看到任何测试文件（如 `tests/` 目录）。
- 复杂逻辑（如工作流引擎、Agent Loop、安全验证）没有自动化测试，容易在修改时引入回归 Bug。

**建议修复：**
1. 为核心模块添加单元测试（pytest + pytest-asyncio）。
2. 为安全相关逻辑（如 `bash_skill` 白名单、 `net_safety` URL 验证）添加专门的边界测试。
3. 为工作流引擎添加集成测试，验证 DAG 执行的正确性。

---

### LOW-5: 资源未释放 - 异步客户端
**位置：** `backend/services/embedding/ollama.py`、`openai.py`

**问题描述：**
- `httpx.AsyncClient` 在 `__init__` 中创建，但没有对应的 `__aexit__` 或 `close()` 方法。
- 虽然 Python 的垃圾回收最终会关闭这些连接，但在高并发下可能导致短暂的连接泄漏。

**建议修复：**
1. 为所有使用 `httpx.AsyncClient` 的类实现 `async def close()` 方法。
2. 在应用生命周期事件（如 FastAPI 的 `lifespan`）中调用所有服务的 `close()`。

---

## 📋 总结

| 严重度 | 数量 | 关键问题 |
|--------|------|----------|
| 🔴 CRITICAL | 3 | 任意代码执行（RCE）、Bash 注入、SSRF 重定向绕过 |
| 🟠 HIGH | 6 | 连接池泄漏、竞态条件、提示词注入、加密密钥管理、速率限制缺失、WebSocket 安全 |
| 🟡 MEDIUM | 10 | 竞态条件、全局状态、上下文截断、事件循环阻塞、异步生成器、资源泄漏、DAG 验证、日志泄露、路径安全、异常处理 |
| 🟢 LOW | 5 | 类型注解、硬编码、魔法数字、缺少测试、资源释放 |

**最优先修复项：**
1. **CRIT-1**：立即禁用 WorkflowEngine 的 `execute_python` 节点和 DynamicSkill 的 `_run_python`，或实施沙箱隔离。
2. **CRIT-2**：加强 BashSkill 的参数过滤，禁止 `git -C` 等危险参数。
3. **CRIT-3**：在 DynamicSkill 的 HTTP 处理器中禁用重定向跟随。
4. **HIGH-1**：统一使用 `UnitOfWork` 管理数据库事务，防止连接泄漏。
5. **HIGH-4**：修复 `encryption.py` 的固定 IV 和弱密钥派生问题。
6. **HIGH-5**：实现基于 Redis 的分布式速率限制。

> 建议在下一次部署前完成所有 CRITICAL 和 HIGH 级别问题的修复，并补充相应的安全测试用例。
