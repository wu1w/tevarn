# TOOLS.md — Tevarn 运行时操作说明（Agent 必读）

> 产品：**Tevarn**（本地 AI 工作站）。代码仓可能仍叫 Takton，行为以本说明为准。  
> 本文件由 `workspace_contract` 自动注入 system context。用户本轮明确指令优先。

## 0. 三秒规则（防空转）

1. **有工具结果就答**：`http`/`mcp_*` 已返回正文 → 立刻写结论，禁止换 URL 再拉。  
2. **过程话不是交付**：「先打开」「再解码」「分段拉」不能当最终回复。  
3. **轮次硬顶**（Grok 式 harness）：chat≈8 · ops≈12 · coding≈20。  
4. **同类工具空转会强制收束**：http 族、command 族、探测类会熔断。

## 1. 本机路径事实（Windows）

| 用途 | 典型路径 |
|------|----------|
| 主程序 | `%LOCALAPPDATA%\Programs\Tevarn\Tevarn.exe` |
| 内置资源/后端 | `...\Tevarn\resources\backend\` |
| 内核 host | `...\Tevarn\resources\tevarn-kernel-host\` |
| 用户数据 | `%APPDATA%\Tevarn\data\`（db、workspace、logs） |
| 会话 workspace | 当前绑定项目；未绑定时可能是 data/workspace |

- `file_read` / `grep` / `command` 默认只能在 **workspace_root + 宿主数据根** 内。  
- 宿主数据根含：`%APPDATA%\Tevarn`、`%APPDATA%\takton`、安装目录 `Programs\Tevarn`、`~/.takton` / `~/.tevarn`。  
- **不要**对 `C:\Program Files\...` 乱 `file_read`（会被拒，浪费轮次）。  
- Shell：**cmd**。串联用 `&`，列目录 `dir`，不要默认 PowerShell。

## 2. 本轮工具面（怎么挂）

- 工具列表 = **本轮 schema 里出现的名字**才存在。没有的就别编。  
- Profile：`core`（薄）/ `coding` / `ops` / `assistant`；由 harness 意图决定。  
- **扩包**：`use_tool_pack(action="list"|"enable", packs=[...])`  
  - 常见 pack：`coding` `web` `manage` `devices` `office` `crew` `mcp`  
- **禁止**在 chat 薄面上指望全量 MCP 70+ schema；默认 **matching-only**。

## 3. MCP（配置 vs 使用）

| 意图 | 做法 |
|------|------|
| **用** 任意已注册 MCP | 用户话里**点名**服务名/产品名（预制或自定义）→ matching 只挂对应 `mcp_*`；直接调 |
| **一句话装预制 + Key** | 「装下豆包搜索MCP：KEY」→ 配置快路径（豆包/tavily/firecrawl）；勿 list 空转 |
| **自定义 MCP（新装）** | `manage_mcp` `add`：`name` + `transport=stdio\|sse` + `command/args` 或 `url` + `env`；成功会热同步 |
| **自定义补 Key** | 已有 server → `manage_mcp` `update` 的 `env`；或一句话带服务名+KEY（快路径会尝试写已有库） |
| 未列出的 MCP 名 | `manage_mcp list/get` 看状态；**勿伪造**工具名；没有就引导用户 add |

- matching-only：**不是**只认 tavily/豆包 白名单——任何已注册 server，用户点名即可挂载。  
- **禁止**一上来 dump 全部 MCP schema；配置轮只挂 `manage_mcp`。  
- 本机常见预制（以 **用户库** `%APPDATA%\Tevarn\data\tevarn.db` 为准）：`tavily` `firecrawl` `github` `fetch` `doubao-search` + 用户自建名。  
- 热同步：`sync_mcp_runtime` / 设置页；密钥只进 DB `env`，勿写 workspace 明文。

## 4. 常用工具怎么选

| 任务 | 优先工具 | 不要 |
|------|----------|------|
| 读/改代码 | `file_read` → `edit`/`apply_patch`/`file_write` → `command`/`python` 验证 | 未读就改 |
| 网页/README | **一次** `http`（raw 优先）或 `web_search` | 连环换 CDN/API 空转 |
| 联网搜索 | `web_search`；指定 MCP 时用 `mcp_tavily_*` 等 | 重复同 query |
| 本机时间 | `current_time` | shell `date` |
| 大结果回读 | `result_load`（spill 句柄） | 瞎猜 id |
| 经营目标 O-KR | `okr_goal` | grep 源码找目标 |
| 会话 todo | `manage_goal` | 与目标页混淆 |
| 编制派工 | `crew_steward`（仅工具面有时） | 简单问答也派工 |
| 配置产品 | `configure_takton` / 设置 + `manage_mcp` | 改全局代理（除非用户明确要求） |

## 5. Skills（两套，别混）

| 类型 | 装在哪 | 怎么挂到本轮 |
|------|--------|--------------|
| **Prompt-skill**（SKILL.md 流程说明） | `~/.takton/skills/<source>/<name>/`；也扫描 workspace `skills/` | Context 注入：summary / 相关全文；点名或描述匹配 |
| **可执行 skill**（function 工具） | DB `skills` + `manage_skill create` | **热挂载** ToolRegistry；用户**点名**才 matching 进 schema |

- **匹配任务的 skill 必须先 follow**，禁止凭记忆编造流程  
- light 轮：只给 skill **目录摘要**（不塞全文）；需要全文时用户点名或升到 standard/coding  
- 新装可执行 skill：`manage_skill create` 后应热同步；下一轮点名即可，勿要求重启  
- 工具 pack 是**内置白名单**；自定义 skill/MCP **不靠 pack 枚举**，靠 matching 点名  

内置工具/skill 名示例：`web_search` `weather` `http_get` `configure_tevarn` `okr_goal` `manage_goal` …

## 6. System / 契约注入链路

每轮自动注入（大致）：

1. **Stable**：身份 + tool-use + 完成标准 + 并行工具 + 搜索收敛  
2. **Context**：workspace 契约 `AGENTS.md`/`SOUL.md`/`USER.md`/`TOOLS.md` + DATA MAP  
3. **Volatile**：记忆 + 时间 + 会话  
4. **短 brief**：`compact_capability_brief`（工具数 / profile / 纪律）  
5. **Harness 注记**：simple 会话禁派工；force_final 禁再工具  

若契约文件 missing，会标 `[missing]`——不要假装读过。

## 7. 公司内网 / 代理

- 默认 **不要改系统全局代理**（会搞挂公司网）。  
- 需要时只给目标进程设 `HTTP_PROXY`/`HTTPS_PROXY`，或用户明确要求。  
- 用户说「别用 PowerShell / 别扫环境」→ 禁止 `where`/`tasklist`/环境枚举，用已知路径或最少工具。

## 8. 失败与收束

- 权限 deny / Hook Blocked：换**允许根**内路径或改用已注入 DATA MAP，**禁止连撞 5 次**。  
- 工具熔断后：**禁止再 tool_calls**，用已有结果写完整中文结论。  
- 预算/轮次耗尽：说明卡点 + 建议用户「请继续」。
