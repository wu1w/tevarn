# Takton v3.0 产品需求文档（PRD）

> 版本：v3.0
> 日期：2026-07-13
> 目标形态：Windows 桌面客户端（Electron + 内嵌 Python 后端）
> 本次范围：A. MCP 客户端接入、C. 桌面原生体验增强、D. Skill/Tool 体系整合
> 排除项：多 Agent 编排与本地 SLM 推理（归入 v4.0 研究）

---

## 1. 产品定位

Takton v3.0 定位为 **Windows 桌面上的本地 Agent 终端**。

区别于网页版 Agent 和通用聊天工具，Takton v3.0 将充分利用 Windows 客户端形态：
- 本地文件系统与代码工作区直接访问
- 常驻托盘与全局快捷键
- 调用本地 MCP 工具生态
- 可扩展的桌面级交互（截图、剪贴板、文件拖拽）

核心目标：让用户在 Windows 上拥有一个“能调用外部工具、能操作本地环境、能持续进化能力”的私有 Agent。

---

## 2. 本次改造范围

| 编号 | 改造项 | 目标 | 优先级 |
|---|---|---|---|
| A | MCP 客户端接入 | 让 Takton 可以发现并调用本地/远程 MCP 服务器提供的工具 | P0 |
| C | 桌面原生体验增强 | 放大 Windows 客户端形态优势，提供截图、剪贴板、文件树、托盘菜单等能力 | P1 |
| D | Skill/Tool 体系整合 | 统一内部两套工具体系，为 MCP 和外部 Skill 生态打好基础 | P1 |

---

## 3. A. MCP 客户端接入

### 3.1 背景与目标

MCP（Model Context Protocol）正在成为 AI 工具生态的事实标准。Takton 要成为有竞争力的桌面 Agent，必须能够接入用户已有的 MCP 工具，而不是让工具开发者专门为 Takton 重写一套。

### 3.2 用户故事

| ID | 用户故事 | 验收标准 |
|---|---|---|
| MCP-01 | 作为用户，我希望在 Takton 里添加一个本地 MCP 服务器 | 通过设置页输入命令或配置文件，成功注册 |
| MCP-02 | 作为用户，我希望 MCP 工具出现在 Takton 的工具列表中 | 工具名称、描述、参数 schema 正确显示 |
| MCP-03 | 作为用户，我希望 Agent 在对话中自动调用 MCP 工具 | LLM 生成 tool call，Takton 调用 MCP 服务器并返回结果 |
| MCP-04 | 作为用户，我可以查看 MCP 服务器状态 | 在线/离线、错误日志可查看 |
| MCP-05 | 作为用户，我可以为不同 MCP 工具设置权限 | 如文件系统 MCP 限制只能访问 workspace 目录 |

### 3.3 功能需求

#### F-A01: MCP 配置管理

- 支持两种配置方式：
  1. 前端设置页表单配置（名称、命令、参数、环境变量）
  2. 读取 `~/AppData/Roaming/Takton/mcp/mcp.json`
- 配置持久化到 SQLite settings 表
- 配置项：
  - `name`: 显示名称
  - `command`: 启动命令（如 `npx`、`<路径>`、`<路径>`）
  - `args`: 启动参数数组
  - `env`: 环境变量
  - `transport`: `stdio` 或 `sse`（v3.0 优先 stdio）
  - `enabled`: 是否启用
  - `permissions`: 权限配置（如允许访问的路径）

#### F-A02: MCP 客户端生命周期

- Electron 主进程负责启动 stdio MCP 服务器子进程
- 后端 Python 通过 `mcp` SDK 与服务器通信
- 后端维护 MCP 连接池，支持多个 server 同时连接
- 应用退出时优雅关闭所有 MCP 子进程
- 单个 MCP 服务器崩溃后应自动重连（最多 3 次）

#### F-A03: MCP 工具发现与注册

- 启动后调用 `tools/list` 获取工具列表
- 将 MCP tool 转换为 Takton 内部统一工具格式（见 D 章节）
- 工具 schema 以 OpenAI function 格式呈现给 LLM
- 工具名自动加前缀避免冲突，如 `mcp_filesystem_readFile`

#### F-A04: MCP 工具执行

- LLM 调用 tool 时，路由到对应 MCP 服务器
- 调用 `tools/call` 并等待结果
- 返回结果格式化为字符串，注入对话上下文
- 工具执行超过 30 秒应提示超时

#### F-A05: MCP 权限与沙箱

- 文件系统类 MCP 默认限制只能访问 `workspace/` 目录
- 网络类 MCP 需经过 SSRF 校验（复用现有 `validate_public_url`）
- 危险命令类 MCP 调用前需用户确认（前端弹窗）
- 支持用户为每个 MCP 工具单独开启/关闭

### 3.4 首批推荐 MCP Server

| 名称 | 类型 | 用途 |
|---|---|---|
| filesystem | 文件系统 | 替代 Takton 内置 file_read/file_write |
| fetch | 网络 | 抓取网页内容 |
| brave-search / duckduckgo | 搜索 | 联网搜索 |
| playwright | 浏览器 | 浏览器自动化 |
| sqlite | 数据库 | 本地 SQLite 查询 |
| git | 代码 | 代码仓库操作 |

### 3.5 技术实现要点

- 后端新增 `backend/mcp/` 模块：
  - `client.py`: MCP 客户端封装
  - `server_manager.py`: stdio 子进程管理
  - `adapter.py`: MCP tool ↔ Takton 工具格式转换
  - `registry.py`: MCP 工具注册表
- 前端新增 `frontend/app/settings/mcp/`：
  - 列表页
  - 添加/编辑页
  - 工具详情与权限配置
- Electron 主进程新增 IPC：
  - `mcp-server-start`
  - `mcp-server-stop`
  - `mcp-server-restart`

---

## 4. C. 桌面原生体验增强

### 4.1 背景与目标

Windows 客户端形态是 Takton 相比网页 Agent 的最大差异点。v3.0 将放大这一优势，让用户在任意工作场景下都能快速唤起 Agent 并与之交互。

### 4.2 用户故事

| ID | 用户故事 | 验收标准 |
|---|---|---|
| DESK-01 | 作为用户，我希望按快捷键直接截图发给 Agent | 截图后自动打开 Takton 并附带图片 |
| DESK-02 | 作为用户，我希望复制一段代码后按快捷键让 Agent 解释 | 剪贴板内容自动进入输入框 |
| DESK-03 | 作为用户，我希望 Takton 窗口置顶 | 有“置顶”开关 |
| DESK-04 | 作为用户，我希望拖拽文件/文件夹到 Takton | 支持直接分析文件内容或目录结构 |
| DESK-05 | 作为用户，我希望托盘右键有更多快捷操作 | 显示“新建对话”“截图提问”“退出” |
| DESK-06 | 作为用户，我希望 Takton 能自动索引本地 workspace | 文件变更后知识库自动更新 |

### 4.3 功能需求

#### F-C01: 全局截图提问

- 快捷键：`Ctrl+Alt+S`（可配置）
- 触发后调用 Windows 截图 API 或模拟 `Win+Shift+S`
- 截图保存到 `~/AppData/Roaming/Takton/data/uploads/`
- 截图后自动打开 Takton 主窗口，输入框已附加图片
- 用户可直接提问“这个报错什么意思”“这个界面怎么操作”

#### F-C02: 剪贴板智能粘贴

- 快捷键：`Ctrl+Alt+V`（可配置）
- 检测剪贴板内容：
  - 文本：直接粘贴到输入框
  - 图片：作为附件上传
  - 文件路径：读取文件内容作为上下文
- 支持“复制代码片段 → 快捷键 → 解释/重构”

#### F-C03: 窗口置顶

- 前端标题栏增加“置顶”按钮
- 使用 Electron `setAlwaysOnTop(true, 'normal')`
- 状态持久化到 window-state.json

#### F-C04: 文件拖拽增强

- 已支持拖拽文件，但增强以下能力：
  - 拖拽整个文件夹：自动列出目录树，可选择“分析项目结构”
  - 拖拽代码文件：自动读取内容到上下文
  - 拖拽图片：作为附件或 OCR（预留）
- 文件读取后，用户可选择：
  - 分析文件
  - 修改文件
  - 生成相关文档

#### F-C05: 托盘菜单扩展

- 右键托盘图标显示：
  - 打开 Takton
  - 新建对话
  - 截图提问
  - 剪贴板粘贴
  - 设置
  - 退出
- 左键单击：显示/隐藏窗口

#### F-C06: 本地 workspace 自动索引

- 监听 `workspace/` 目录文件变更（使用 Node.js `fs.watch` 或 Python watchdog）
- 变更后自动更新 RAG 向量索引
- 用户可配置是否开启自动索引
- 大文件变更避免频繁触发，使用去抖动（5 分钟）

#### F-C07: 代码编辑器与 Diff 视图（轻量版）

- 在对话中展示代码块时，提供“编辑”按钮
- 点击后打开轻量编辑器浮层
- 支持修改后保存回 workspace
- 支持显示修改前后 diff（用于 Agent 改代码后确认）

### 4.4 技术实现要点

- Electron 主进程新增 IPC：
  - `capture-screenshot`: 截图并返回路径
  - `read-clipboard`: 读取剪贴板内容
  - `write-clipboard`: 写入剪贴板
  - `toggle-always-on-top`: 切换置顶
  - `open-file-with-dialog`: 文件选择对话框
- 前端新增组件：
  - `ScreenshotTrigger`
  - `ClipboardTrigger`
  - `DiffViewer`
  - `FileDropZone`（增强）
- 托盘菜单在 `electron/main.ts` 中扩展

---

## 5. D. Skill/Tool 体系整合

### 5.1 背景与目标

当前 Takton 内部存在两套并行的工具体系：
- `backend/skills/`：Python 类，通过 `BaseSkill` 和 `SkillRegistry` 注册
- `backend/services/tools/`：数据库模型，通过 `ToolRegistry` 注册执行

两套体系导致：
- LLM 看到的工具列表来自两个不同来源
- 新增工具需要同时理解两套机制
- 外部 MCP 工具难以统一接入
- 用户自定义技能与内置工具体验不一致

v3.0 目标：**统一为“Takton Tool”抽象层**，让 Skill、MCP Tool、DB Tool 对外都是一致的工具。

### 5.2 用户故事

| ID | 用户故事 | 验收标准 |
|---|---|---|
| TOOL-01 | 作为用户，我希望所有工具在同一个页面管理 | Skills/Tools/MCP 工具统一列表 |
| TOOL-02 | 作为用户，我希望 Agent 调用工具时不再区分来源 | 所有工具以统一名称/格式被调用 |
| TOOL-03 | 作为开发者，我希望新增工具只实现一个接口 | 统一 `BaseTool` 接口 |
| TOOL-04 | 作为用户，我希望自定义 Skill 和内置工具一样强大 | 自定义 Skill 也能参与工具调用 |

### 5.3 功能需求

#### F-D01: 统一工具抽象层

- 新建 `backend/tools/base.py` 中的 `BaseTool` 抽象类：
  - `name: str`
  - `description: str`
  - `parameters: dict`
  - `source: Literal["builtin", "skill", "mcp", "dynamic"]`
  - `async def execute(self, arguments: dict) -> str`
  - `to_json_schema() -> dict`
- 所有内部工具（Skill、DB Tool、MCP Tool）都实现或适配到 `BaseTool`

#### F-D02: 统一工具注册表

- 新建 `backend/tools/registry.py` 中的 `ToolRegistry`
- 替换现有 `SkillRegistry` 和 `services/tools/registry.py` 成为唯一工具发现入口
- 工具注册顺序：
  1. 加载内置工具（BuiltinTool）
  2. 加载数据库自定义工具（DynamicTool）
  3. 加载内置 Skill（SkillToolAdapter）
  4. 加载 MCP 工具（McpToolAdapter）
- 工具名冲突时：
  - 用户自定义工具优先于内置
  - MCP 工具自动加 `mcp_` 前缀
  - 记录 warning 日志

#### F-D03: 现有 Skill 适配

- 所有 `backend/skills/builtins/*.py` 通过 `SkillToolAdapter` 包装成 `BaseTool`
- 保留原有 `BaseSkill` 类，但执行入口改为统一 registry
- `DynamicSkill` 直接注册为 `DynamicTool`

#### F-D04: 现有 services/tools 适配

- 数据库中 Tool 模型继续存在，但执行通过 `BuiltinToolExecutor` 包装
- `services/tools/executors.py` 中现有执行器映射到 `BaseTool.execute`
- 前端工具列表 API 统一从 `ToolRegistry` 返回

#### F-D05: 工具权限与元数据

- 每个工具增加元数据：
  - `requires_confirmation`: 是否需要用户确认
  - `allowed_paths`: 允许访问的目录（文件类）
  - `risk_level`: `safe` / `normal` / `dangerous`
  - `enabled`: 是否启用
- 工具权限统一在前端工具设置页管理

#### F-D06: 工具市场与本地导入（预留）

- 定义 Takton Skill 包格式：
  ```
  my_skill/
  ├── SKILL.md
  ├── tool.yaml
  └── handler.py
  ```
- 支持从本地目录或 GitHub zip 导入
- 导入后自动注册到 `ToolRegistry`

### 5.4 工具统一后的结构

```
backend/tools/
├── base.py              # BaseTool 抽象
├── registry.py          # 统一工具注册表
├── builtin_tools/       # 内置工具（替代部分 Skill）
│   ├── bash_tool.py
│   ├── file_tool.py
│   ├── http_tool.py
│   ├── python_tool.py
│   ├── search_tool.py
│   └── edit_tool.py
├── adapters/
│   ├── skill_adapter.py # 把 BaseSkill 转成 BaseTool
│   ├── mcp_adapter.py   # 把 MCP tool 转成 BaseTool
│   └── db_tool_adapter.py # 把数据库 Tool 转成 BaseTool
└── permissions.py       # 工具权限校验
```

### 5.5 对外接口统一

- Agent Loop 只依赖 `ToolRegistry.get_active_tools()`
- LLM 看到的 tools schema 全部统一格式
- 执行结果全部统一为字符串
- 工具调用日志统一记录到 ContextFlow

---

## 6. 非功能需求

| ID | 需求 | 指标 | 优先级 |
|---|---|---|---|
| NFR-01 | 安全性 | MCP 工具权限隔离、危险命令确认、无高危 CVE | P0 |
| NFR-02 | 稳定性 | MCP 子进程崩溃不影响主应用；Electron 不白屏 | P0 |
| NFR-03 | 性能 | 工具发现 < 2s；单次工具调用 < 30s | P1 |
| NFR-04 | 可维护性 | 工具统一抽象后，新增工具开发工作量 < 30 分钟 | P1 |
| NFR-05 | 兼容性 | 支持 Windows 10/11；MCP stdio server 兼容主流 Node/Python server | P1 |
| NFR-06 | 离线能力 | 不依赖 MCP 时，本地工具全部可用 | P1 |
| NFR-07 | 用户体验 | 截图/剪贴板快捷键响应 < 500ms | P1 |

---

## 7. 技术架构变更

### 7.1 目标架构

```
┌────────────────────────────────────────────┐
│  Frontend (Next.js + Electron)              │
│  ├─ Chat / Settings / Tools / Skills       │
│  ├─ Screenshot / Clipboard / DragDrop IPC  │
│  ├─ Tray Menu / Global Shortcut            │
│  └─ WebSocket ↔ REST API                  │
├────────────────────────────────────────────┤
│  Electron Main Process                     │
│  ├─ Launch Python backend                  │
│  ├─ Launch MCP stdio servers               │
│  ├─ Global shortcuts / Tray menu           │
│  ├─ Screenshot / clipboard native APIs     │
│  └─ File system watcher                    │
├────────────────────────────────────────────┤
│  Backend (FastAPI + SQLite)                │
│  ├─ Unified Tool Registry                  │
│  │   ├─ Builtin Tools                     │
│  │   ├─ Skill Adapter                     │
│  │   ├─ Dynamic Tool Adapter              │
│  │   └─ MCP Tool Adapter                  │
│  ├─ MCP Client / Server Manager            │
│  ├─ Agent Loop (single)                    │
│  ├─ Context Manager / RAG / Memory         │
│  └─ API routes                             │
└────────────────────────────────────────────┘
```

### 7.2 数据目录

```
~/AppData/Roaming/Takton/
├── data/
│   ├── takton.db          # SQLite 主数据库
│   ├── uploads/           # 用户上传文件
│   ├── workspace/         # 代码/文档工作区
│   └── qdrant/            # 本地向量库（如用）
├── mcp/
│   └── mcp.json           # MCP 服务器配置
├── skills/                # 用户导入的 skill 包
├── secrets.json           # 密钥
└── window-state.json      # 窗口状态
```

---

## 8. 版本路线图

| 版本 | 周期 | 主要交付 |
|---|---|---|
| v3.0-alpha | 1 周 | Skill/Tool 统一抽象层 + 基础适配器完成 |
| v3.0-beta | 第 2 周 | MCP stdio 客户端接入 + 设置页 |
| v3.0-rc | 第 3 周 | 桌面体验增强（截图、剪贴板、托盘、置顶）+ 文件监听 |
| v3.0 | 第 4 周 | 全量 E2E + 安全审计 + 打包验证 |

---

## 9. 验收标准

### 9.1 MCP 接入

- [ ] 用户可以通过设置页添加 filesystem MCP server
- [ ] Takton 能列出 filesystem server 提供的工具
- [ ] 在对话中输入“读取 workspace/test.txt 内容”，Agent 自动调用 MCP tool 并返回结果
- [ ] MCP server 崩溃后，3 秒内自动重连
- [ ] 文件系统 MCP 默认无法访问 `workspace/` 之外的目录

### 9.2 桌面原生体验

- [ ] `Ctrl+Alt+S` 截图后自动打开 Takton 并附带图片
- [ ] 复制代码后 `Ctrl+Alt+V` 自动粘贴到输入框
- [ ] 窗口置顶功能可用，状态持久化
- [ ] 拖拽文件夹到 Takton 能显示目录树
- [ ] workspace 文件变更后 RAG 索引在 5 分钟内更新

### 9.3 Skill/Tool 整合

- [ ] 所有内置工具都通过 `BaseTool` 注册
- [ ] 前端工具列表只调用一个 API（`GET /api/tools`）
- [ ] 新增一个内置工具的开发工作量 < 30 分钟（包括注册、schema、权限）
- [ ] 单元测试覆盖 `ToolRegistry` 的注册、发现、执行流程

### 9.4 通用

- [ ] Windows 客户端打包后安装运行无白屏
- [ ] 所有新增功能在打包后的 `Takton.exe` 中验证通过
- [ ] 无 CRITICAL 级 CVE
- [ ] E2E 测试全绿

---

## 10. 风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|---|---|---|---|
| MCP stdio server 在 Windows 上进程管理复杂 | 中 | 高 | Electron 主进程负责启动，后端只通过 stdio/sse 通信；做好进程清理 |
| MCP 工具权限绕过 | 中 | 高 | 文件路径白名单、SSRF 校验、危险命令确认 |
| Skill/Tool 整合导致旧功能回归 | 高 | 中 | 保持向后兼容，原有 `SkillRegistry` 作为 adapter 保留；全量回归测试 |
| 截图/剪贴板触发 Windows 安全软件拦截 | 中 | 中 | 提供配置让用户自定义快捷键；截图时只读取屏幕，不写入敏感位置 |
| 本地文件监听导致性能问题 | 中 | 低 | 使用 debounce；只监听 workspace 目录；大文件过滤 |

---

## 11. 附录：术语表

| 术语 | 说明 |
|---|---|
| MCP | Model Context Protocol，Anthropic 主导的 AI 工具连接标准 |
| stdio | MCP 服务器通过标准输入输出与客户端通信的模式 |
| Skill | Takton 现有的 Python 类工具，继承自 `BaseSkill` |
| Tool | v3.0 统一后的工具抽象，所有来源的工具都转换为 `BaseTool` |
| workspace | 用户本地工作目录，默认 `~/AppData/Roaming/Takton/data/workspace/` |

---

*文档结束*
