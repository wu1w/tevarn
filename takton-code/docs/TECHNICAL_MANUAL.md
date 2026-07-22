# Takton Code 技术手册

**版本:** 0.1.0  
**文档日期:** 2026-07-21  
**代码规模:** ~15.6k LOC / 50 Python 模块（`src/takton_code`）  
**测试:** `pytest` 全绿（编写时 93+ passed）  
**定位:** 仓库原生（repo-native）编码 Agent — 独立进程；可选接入 Takton Desktop Bridge；开源中立 system prompt。

---

## 1. 产品定位与边界

### 1.1 是什么
Takton Code（CLI: `takton-code` / `tkc`）是在**本地项目目录**上运行的 coding agent：

- 读写文件、跑测试、git、权限门控、会话持久化、检查点/回滚、上下文压缩
- TUI（Textual 全屏 + minimal）与 headless `-p` 双模式
- LLM 走 **OpenAI-compatible** HTTP（本机 llama.cpp / 网关均可）

### 1.2 刻意不做
| 不做 | 原因 |
|------|------|
| 自建 MCP / Skills 应用商店 | 生态只走 Desktop Bridge |
| 默认 Anthropic / xAI 身份 system | 开源中立，避免污染网关模型 |
| Grok 式整仓回传 | 隐私边界 |
| 公网 share / 云会话 | 本地优先 |

### 1.3 与竞品关系（摘要）
| 能力 | Claude Code | OpenCode | Grok CLI | Takton |
|------|-------------|----------|----------|--------|
| 日用 terminal agent | ✓ | ✓ | ✓ | ✓ |
| 检查点/rewind | ✓ | 弱 | 弱 | **更深**（partial/hunk/unrewind） |
| Tool 剪枝 microcompact | ✓ | 截断落盘 | 摘要为主 | ✓ + Anthropic-strict pair |
| 模型绑定 | Anthropic | 多厂商 | xAI | **任意 OAI 兼容** |
| 生态 | Skills/MCP/IDE | MCP/plugin | 封闭 | Desktop bridge only |

---

## 2. 仓库与布局

```
takton-code/
├── src/takton_code/          # 主包
│   ├── cli.py                # Typer CLI 入口
│   ├── config.py             # 配置模型与加载
│   ├── agent/                # 核心 agent 运行时
│   ├── context/              # 压缩 / 策略 / 仪表
│   ├── session/              # SQLite 会话与导出
│   ├── llm/                  # Provider 抽象
│   ├── bridge/               # Desktop HTTP 协议与客户端
│   ├── tui/                  # Textual 全屏 + vim UX
│   ├── plan/                 # Plan gate
│   ├── diff/                 # 回合 diff / revert
│   ├── project/              # 项目绑定 / worktree / pr
│   ├── leader/               # 本地 multi-session leader
│   └── settings/             # 模型引导 CLI 文案
├── tests/                    # pytest（pythonpath=src）
├── smoke/                    # LOCAL 真模型 smoke/stress
├── fixtures/sample_repo/     # 测试用小仓库
├── docs/                     # 文档（本手册等）
├── config.example.toml
└── pyproject.toml            # hatchling, requires-python>=3.11
```

### 2.1 依赖（运行时）
`httpx`, `rich`, `typer`, `pydantic`/`pydantic-settings`, `aiosqlite`, `gitpython`, `prompt-toolkit`, `textual`  
可选 dev: `pytest`, `pytest-asyncio`

### 2.2 安装与启动
```bash
cd E:/项目/takton-code
uv pip install -e ".[dev]"
takton-code --path <repo>           # TUI
takton-code -p "fix typo" --path <repo>
takton-code -p "..." --yes-build --autoloop
```

环境变量前缀：`TAKTON_CODE_*`（如 `TAKTON_CODE_HOME`, `TAKTON_CODE_BASE_URL`, `TAKTON_CODE_MODEL`）。

数据根目录默认：`~/.takton-code`（Windows: `%USERPROFILE%\.takton-code`），可用 `TAKTON_CODE_HOME` 覆盖。

---

## 3. 总体架构

```
                    ┌─────────────────────────────────────┐
                    │  CLI (typer) / TUI (textual) / -p   │
                    └─────────────────┬───────────────────┘
                                      │
                    ┌─────────────────▼───────────────────┐
                    │         AgentRuntime (loop)         │
                    │  plan gate · permissions · parts    │
                    │  compress · file_history · tools    │
                    └───┬─────────────┬─────────────┬─────┘
                        │             │             │
              ┌─────────▼──┐  ┌───────▼──────┐  ┌──▼──────────┐
              │ LLMProvider│  │ SessionStore │  │ ToolRuntime │
              │ OAI /Bridge│  │ aiosqlite    │  │ + DiffEngine│
              └────────────┘  └──────────────┘  └─────────────┘
                        │
              ┌─────────▼──────────┐
              │ ContextCompressor  │
              │ + policy(thrash/   │
              │   archive/RAG)     │
              └────────────────────┘
                        │ optional
              ┌─────────▼──────────┐
              │ Takton Desktop     │
              │ /bridge/v1/*       │
              └────────────────────┘
```

### 3.1 单轮（turn）主循环
`AgentRuntime._run_turn_unlocked` 大致：

1. 处理 `/slash`（若是命令则短路返回）
2. 可选 auto-plan → 切 `plan` 模式
3. 展开 `@file`、多模态图片路径
4. `append` user message + file-history leaf
5. **while** iterations < max：
   - drain steer
   - **`_maybe_compress`**（soft micro / hard middle / thrash 熔断）
   - **`_llm_chat`**（发送前 `ensure_anthropic_strict`；overflow 则强压+重试 1 次）
   - 若有 `tool_calls`：逐个 execute → tool message（大结果截断+落盘）→ continue
   - 否则写 final assistant → break
6. 更新 session stats（tokens_input/output、compress_count、diff 摘要）
7. checkpoint / clear；`replace_messages` 持久化

---

## 4. 模块说明书

### 4.1 `agent/loop.py` — AgentRuntime
核心状态机与 slash 处理。

| 职责 | 说明 |
|------|------|
| 模式 | `plan` / `build` / `ask` / `explore` / `always`（always≈build+自动放行写） |
| 权限 | `PermissionGate` + `PermissionBroker`（headless 下 ask→deny 除非 always） |
| 压缩 | 委托 `ContextCompressor` + `ThrashingGuard` |
| 历史 | `FileHistory` 用户叶快照、rewind/unrewind |
| 事件 | `on_event` 回调：text_delta、tool_start/end、compress、thrashing、usage… |

重要方法：`setup`, `run_turn`, `continue_after_interrupt`, `_maybe_compress`, `_llm_chat`, `context_meter`, `handle` slash 族。

### 4.2 `agent/tools.py` — ToolRuntime
内置工具（OpenAI tools schema）：

| 工具 | 只读 | 说明 |
|------|------|------|
| `file_read` | ✓ | 读文件 |
| `file_write` | | 写文件 |
| `edit_file` | | 精确替换 |
| `grep` | ✓ | 内容搜索 |
| `glob` | ✓ | 文件名匹配 |
| `run_tests` | | 项目测试命令 |
| `bash` / shell 类 | 视权限 | 受 profile 约束 |
| `git_status` / `git_diff` / commit… | 部分 | push 默认关 |
| `todo_write` / `todo_list` | | 会话 todo |
| `web_fetch` | ✓ | 只读拉取（可关） |
| `spawn_subagent` | | 子代理（可关） |
| Desktop invoke | | bridge 开启时 |

`plan`/`ask`/`explore`：**禁止写工具**。

### 4.3 `agent/permissions.py`
- Profiles: `cautious`, `free`, `acceptEdits`, `always`, `bypass`, `dontAsk`, `plan`, `auto`
- `auto`：本地启发式 + `auto_rules.toml` 热加载（`auto_classify.py`）
- 危险命令（如破坏性 rm）在 cautious 下 ask/deny

### 4.4 `agent/file_history.py` + `hunks.py` + `redo.py`
Claude 语义检查点 **增强版**：

| 能力 | 说明 |
|------|------|
| Disk backups | `~/.takton-code/file-history/<session>/` |
| Rewind scopes | `code` / `conversation` / `both` |
| Partial | `only_paths` / TUI 多选文件 |
| Side unified | 当前盘 vs checkpoint diff |
| Hunk apply | 解析 unified hunk，选择性应用 |
| Unrewind | rewind 前压 redo 栈，可反悔 |

Slash：`/rewind`, `/unrewind`, `/hunk list|apply`, `/patch`  
TUI：EscEsc / Ctrl+R、Hunks 工作台、Ctrl+Shift+Z。

### 4.5 `agent/autoloop.py`
有界会话驱动：plan → lint/test/verify → fix，含 doom-loop 指纹，**非**仓库 daemon。  
CLI：`--autoloop --yes-build`。

### 4.6 `context/compressor.py` + `context/policy.py`

**压缩分层（Anthropic 严格 pair）：**

1. **Microcompact**：旧 tool **只清 content**，保留 `tool_call_id` 结构；可落盘全文  
2. **Middle summary**：丢 middle 前 **完整归档** `archives/<session>/transcript.jsonl`，再写文本摘要  
3. **ensure_anthropic_strict**：修复/剔除不完整 tool block，发送前再校验  
4. **Overflow 反应**：API context 过长 → aggressive 压 + **同请求重试 1 次**

**策略（policy）：**

| 配置 | 默认 | 含义 |
|------|------|------|
| `compact_mode` | `static` | 尽量多留完整轮次（类 openclaw 笨保留） |
| | `balanced` | 常规 keep_recent |
| | `aggressive` | 更狠裁剪 |
| `retain_turns` | 24 | static 下扩大 live keep |
| thrashing_* | 3 / 180s / 300s | 频繁 hard compact 熔断 → 只 micro |
| `rag_compact` | false | true 且 bridge 开：compact 时注入 Desktop RAG |

命令：`/context`（双轨仪表）、`/compact`、`/usage`。

### 4.7 `session/store.py`
aiosqlite：sessions、messages、parts、queue、todos、checkpoints 元数据、tokens_input/output、compress_count。  
`export_session` + `export_fmt` → json/md/jsonl。  
`fork_session`、`stats_summary`、`import`。

### 4.8 `llm/provider.py`
- `OpenAICompatibleProvider`：chat + SSE stream（`stream_options.include_usage`）  
- `BridgeLLMProvider`：走 Desktop  
- `_sanitize_messages`：空 content+tool_calls→null + **strict pair**  
- 兼容 `reasoning_content` / thinking 标签剥离  

### 4.9 `bridge/`
- `protocol.py`：路由与 schema（稳定契约）  
- `client.py`：`TaktonBridge` HTTP 真实现；`NullBridge` 为 **enabled=false 的显式空实现（非业务桩）**  
- 文档：`docs/DESKTOP_BRIDGE.md`

### 4.10 `tui/`
- `app.py`：全屏 Textual — 工具栏、权限条、Rewind/Hunk/Palette、vim NORMAL/INSERT、`/` 搜索、数字计数 `10j`  
- `minimal.py` / `renderer.py` / `stream_buffer.py` / `vim_ux.py`  
- 底栏：双轨 ctx 条、THRASH、Σ tokens、compact mode  

### 4.11 `plan/gate.py`
Plan 文档解析、批准状态机；与 mode=plan 写保护配合。

### 4.12 `diff/engine.py`
回合级文件 before/after、unified diff、单文件 revert。

### 4.13 `project/`
- `binder.py`：发现 CODE.md / AGENTS.md / CLAUDE.md / .cursorrules / copilot 等  
- `worktree.py`：git worktree 隔离  
- `pr_checkout.py`：`gh pr checkout`  

### 4.14 `leader/`
本机 TCP leader（127.0.0.1），多会话 attach；无公网 token 默认。

### 4.15 其他 agent 辅件
- `prompt.py`：中立 system 拼装  
- `parts.py`：turn 结构化 parts  
- `subagent.py` / `best_of_n.py`  
- `multimodal.py`：本地图片 → data URL  
- `memory_local.py`：opt-in MEMORY.md  
- `agents_fs.py`：`.takton/agents/*.md`  
- `refs.py`：slash 表 + `@path` 展开  

---

## 5. CLI 命令面

| 命令 | 作用 |
|------|------|
| （默认） | 打开 TUI / 带 flags 的会话 |
| `-p/--prompt` | headless 单轮或多轮 |
| `--autoloop --yes-build` | 闭环编码 |
| `init` | 初始化项目文件 |
| `inspect` | 检查项目绑定 |
| `sessions` | 列会话 |
| `config` | 读/改配置 |
| `models` / 设置向导 | 模型引导 |
| `bridge-check` | 探测 Desktop |
| `worktree` 子命令 | list/add/show/rm/gc |
| `leader` / `attach` | 多会话 |
| `auto-rules` | 权限规则 |
| `export` / `import-session` | 会话导入导出 |
| `stats` | token/compress 统计 |
| `pr` | gh PR checkout |

常用 flags：`--path/-C`, `--mode`, `--permission-mode`, `--continue`, `--json`, `--stream` 等。

---

## 6. Slash 命令（会话内）

节选（完整见 `agent/refs.py`）：

`/help` `/status` `/usage` `/context` `/compact` `/todo` `/agent` `/fork` `/export`  
`/rewind` `/unrewind` `/hunk` `/patch` `/autoloop` `/rules` `/memory` `/pr`  
`/plan` `/build` `/ask` `/explore` `/always` `/stop` `/enqueue` …

---

## 7. 配置参考

路径优先级：环境变量 > `~/.takton-code/settings.json` 补丁 > `config.toml` > 默认。

```toml
[llm]
base_url = "http://192.168.5.32:8088/v1"
api_key = "no-key"
model = "local-model.gguf"
context_window = 65536
max_tokens = 4096
compress_threshold = 0.55
compress_keep_recent = 8
compress_keep_tool_blocks = 4
max_tool_result_chars = 4000
compact_mode = "static"          # static | balanced | aggressive
retain_turns = 24
thrashing_max_events = 3
thrashing_window_sec = 180
thrashing_cooldown_sec = 300
rag_compact = false              # true + bridge → Desktop RAG 注入摘要

[agent]
max_iterations = 40
permission_profile = "cautious"  # 或 always / auto / …
file_checkpointing = true
autoloop = false
allow_images = true
local_memory = false
allow_web_fetch = true

[bridge]
enabled = false
base_url = "http://127.0.0.1:8090/api"
# use_desktop_* flags

[ui]
screen_mode = "fullscreen"       # 或 minimal
vim_keys = true
command_palette = true
```

---

## 8. 数据目录（`TAKTON_CODE_HOME`）

```
~/.takton-code/
├── config.toml / settings.json
├── state.db                 # 会话主库
├── auto_rules.toml
├── file-history/<session>/  # 文件备份 + redo.jsonl
├── tool-outputs/<session>/  # 大 tool 全文
├── archives/<session>/      # compact 前完整 transcript.jsonl
├── memory/MEMORY.md         # opt-in
└── logs/
```

---

## 9. 上下文压缩与 thrashing（设计要点）

### 9.1 为什么严格 pair
OpenAI `role=tool` 与 Anthropic `tool_use`/`tool_result` 都要求：**assistant 工具调用块后必须跟齐结果**。  
错误拆 pair → 400。Takton 在 compress / provider sanitize / 每次 LLM 调用前统一 `ensure_anthropic_strict`。

### 9.2 Static 模式（默认）
类似“笨但全”的保留：live 窗口尽量大；不得不丢时 **磁盘全文归档**，摘要只作 LLM 提示，**本地可追**。

### 9.3 Thrashing
180s 内 ≥3 次 hard compact → 熔断：只 microcompact tool，禁止再砍对话，直到冷却。  
TUI 显示 `[THRASH]`；`/context` 可见状态。

### 9.4 RAG 进阶
`rag_compact=true` 且 Desktop bridge 开启时，hard compact 前用最近用户句做 `rag_search`，结果写入摘要块 `[DESKTOP_RAG_CONTEXT]`。

---

## 10. 权限与安全

- 默认 **cautious**：高风险工具需确认  
- `always` / headless `permission_profile=always`：压测/CI  
- `allow_git_push=false` 默认  
- Shell 安全写法偏好；灾难命令需明确  
- Bridge token 可选 Bearer  
- Leader 默认绑 127.0.0.1  

隐私 hardening 记录见 `docs/PRIVACY_HARDENING_LOCAL.md`。

---

## 11. 测试与压测

```bash
# 单元 + 集成（无网）
PYTHONPATH=src python -m pytest tests/ -q

# LOCAL 真模型 smoke / 高负载
PYTHONPATH=src python smoke/smoke_aiga.py
PYTHONPATH=src python smoke/stress_aiga_load.py
```

| 套件 | 覆盖 |
|------|------|
| `test_core*` | plan/diff/store/tools |
| `test_anthropic_strict_compress` | pair 不 400 |
| `test_context_policy` | thrash/archive/meter/rag |
| `test_*history*|*rewind*|*hunk*|*redo*` | 检查点链 |
| `test_core_robustness` | FakeLLM 真 loop + overflow |
| `stress_aiga_load` | 真模型：工具链+多次 threshold 压缩+interrupt+tokens 落库 |

**压测结论（2026-07-21，LOCAL Qwen3.5-122B）：**  
ALL STRESS PASSED；compress×数十次 integrity_ok；tokens_in/out 落库非 0。

---

## 12. 非桩说明（审计）

| 项 | 结论 |
|----|------|
| `NullBridge` | bridge 关闭时的**显式**空实现，返回 disabled |
| `BridgeClientProtocol` 的 `...` | typing Protocol，非实现 |
| TUI `except: pass` | UI 刷新容错，非业务桩 |
| `NotImplementedError` | 源码中无 |
| 核心 tools/loop/compress/store/history | 均为真实实现 |

---

## 13. 开发约定（本仓库）

1. 路径优先 `E:/项目/takton-code` 风格；Windows 上 git-bash 用 `python`  
2. 改完 skill/tool 需同步相关副本并重启进程（若对接 Desktop）  
3. 开源仓只放正式文档；过程稿不默认提交  
4. 验收要真命令输出 / pytest / smoke，不编造  
5. System prompt 保持模型中立  

---

## 14. 关键文件索引

| 路径 | 内容 |
|------|------|
| `README.md` | 用户向快速说明 |
| `docs/TECHNICAL_MANUAL.md` | **本手册** |
| `docs/DESKTOP_BRIDGE.md` | Bridge 契约 |
| `docs/COMPETITOR_*` | 竞品审计与坑 |
| `docs/PRIVACY_HARDENING_LOCAL.md` | 隐私 |
| `config.example.toml` | 配置样例 |
| `smoke/stress_aiga_load.py` | 高负载脚本 |

---

## 15. 版本与路线（简）

**已交付：** A/B 检查点链、autoloop、auto 权限、TUI 鼠标+vim+search、严格压缩、usage 落库、static 归档、thrashing、可选 RAG compact、export/stats、worktree/leader/bon 等。

**可选下一刀：** compact undo、大文件强制分页策略、上下文曲线 UI、Desktop ingest API（若桌面侧补写入）。

---

## 16. 快速故障排查

| 现象 | 检查 |
|------|------|
| LLM 空回复 | Qwen thinking：看 `reasoning_content`；provider 已兜底 |
| context 400 | 应自动 overflow 重试；看 compress 事件与 `/context` |
| tokens 统计为 0 | 旧版本 bug 已修；确认跑的是当前 loop 落库路径 |
| 工具被拒 | mode=plan/explore 或 permission_profile |
| bridge 全空 | `enabled=false` 时 NullBridge 属正常 |
| thrashing | `/context`；等冷却或 `/compact` 手动 |

---

*本手册依据 2026-07-21 代码树与全量 pytest 生成。代码变更后请以仓库与测试为准更新本文。*

---

## 附录 A — P0 源码验证审计（2026-07-21）

验收纪律：**有源码/测试输出背书**；不符则改代码或改文档。

| # | 验证项 | 结论 | 证据 |
|---|--------|------|------|
| 1 | `_llm_chat` 每次 LLM 前 `ensure_anthropic_strict` | **属实** | `loop.py` `_llm_chat`：strict → `collect_stream`/`chat`；overflow 后再 strict+retry。`provider` 的 `chat`/`chat_stream` 再经 `_sanitize_messages`→strict。测试：`test_p0_llm_chat_source_calls_ensure_strict`、`test_p1_provider_chat_sanitizes_async` |
| 2 | 子代理成功路径 final + idle/end | **曾漏 → 已修** | 原成功路径只 `return TurnResult`，TUI 听的 `turn_end`/`idle`/`assistant_final` 未推。现正常结束与 LLM error 均 emit。测试：`test_p1_subagent_success_emits_idle_and_final`、`test_p1_spawn_subagent_success_path` |
| 3 | microcompact 保 id + middle 前归档 | **属实** | `microcompact_tools` 只改 content；`_archive_middle`→`archives/<session>/transcript.jsonl`。测试：`test_p0_microcompact_keeps_tool_call_ids`、`test_p0_middle_summary_archives_transcript` |
| 4 | sanitize 空 content→null；截断 JSON 合法 | **曾有问题 → 已修** | 旧逻辑 `args[:30000]+"/*truncated*/"` 可破坏 JSON。现 `_truncate_tool_arguments` 产出合法 JSON。测试：`test_p0_sanitize_empty_content_null_and_json_args` |
| 5 | pytest + LOCAL stress | **属实** | `pytest tests/ -q` → **107 passed**（本轮）。`smoke/stress_aiga_load.py` → **ALL STRESS PASSED 334.6s**；tokens_in=232665、compress=54、integrity_ok；事件含 `turn_end`/`idle`/`assistant_final`×10 |

### A.1 本轮代码修复（非“只改嘴”）

1. 成功/失败路径 emit `assistant_final` / `turn_end` / `idle`  
2. sanitize 参数截断保持 JSON  
3. static 过阈 1.5× 紧急收 keep（小窗压测可压下去）  
4. thrashing 按 `context_window` 推荐标定（≤16k 更宽）  
5. doom_loop 运行时检测  
6. leader client 重连  
7. sessions.worktree_name/path 绑定  

### A.2 Thrashing 推荐（LOCAL）

| context_window | max_events | window_sec | cooldown_sec |
|----------------|------------|------------|--------------|
| ≤16k（压测 12k） | 8 | 60 | 90 |
| ≤48k | 5 | 120 | 180 |
| >48k（默认 64k+） | 3 | 180 | 300 |

函数：`context.policy.recommended_thrashing`；小窗且配置仍为 3 时自动改用推荐。

### A.3 权限矩阵（8×5）

完整 40 格 JSON：`docs/PERMISSION_MATRIX.json`（由 `test_p1_permission_profile_mode_matrix` 生成）。

**硬规则（已测）：** `mode ∈ {plan, ask, explore}` 时，`file_write` / `run_shell`（及同类）一律 **deny**，即使 `profile=always`（Grok 坑：always 不解 plan 写锁）。

| mode\profile 示意 | cautious | always | free |
|-------------------|----------|--------|------|
| plan | write deny | write deny | write deny |
| build | write allow / bash ask | write allow | write allow |
| ask/explore | write deny | write deny | write deny |

### A.4 P2 落地状态

| # | 项 | 状态 |
|---|-----|------|
| 10 | Ctrl+' queue | **已有** `ctrl+apostrophe`→`show_queue`（兼 `ctrl+semicolon`） |
| 11 | doom_loop | **已做** `agent/doom_loop.py` + loop 接线 |
| 12 | leader 重连 | **已做** `LeaderClient.reconnect` / `request(retries=)` |
| 13 | worktree↔session | **已做** `sessions.worktree_*` + `bind_worktree` + CLI 接线 |

### A.5 Bridge 联调门闩

P0 五项均有背书 → **可以进入 Bridge 联调**。联调前建议再跑：

```bash
PYTHONPATH=src python -m pytest tests/ -q
PYTHONPATH=src python smoke/stress_aiga_load.py
takton-code bridge-check
```
