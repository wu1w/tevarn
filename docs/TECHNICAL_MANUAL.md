# Takton 技术手册

版本：**0.4.10-alpha**  
更新：2026-07-30（Phase 5 对齐）

> **执行模型（现行）**  
> **一切执行都是 Run**；Identity 是执行者；Cluster/SubAgent/Hire 是编排形态；Workflow 是 Run 模板。  
> 记忆总线：`backend/services/memory_bus.py` · 权限法院：`backend/kernel/permission_court.py`  
> Intent：`backend/kernel/intent.py` · 回放门禁：`backend/evolution/replay_validator.py`  
> 默认端口 **8090**（`start.py` / CLI / Electron）。文中若写 `8000` 仅历史参考。  
> 默认零外部依赖：SQLite；Redis / Qdrant 可选。  
>
> **编制预算（弹性）**  
> - 硬顶默认 **200 万** token/进程（`agent_workforce_budget_hard_cap` / `TAKTON_WORKFORCE_BUDGET_HARD_CAP`）  
> - **软续航**：用尽前自动 top_up（`agent_budget_soft_renew_*`，默认开，最多 12 次）  
> - CEO：`crew_steward top_up` / `set_budget` / `budgets` 仍可人工治理  
> - 长 instruction / 马拉松类任务自动抬高开局预算

---

## 目录

1. [系统架构](#1-系统架构)
2. [前端设计](#2-前端设计)
3. [后端设计](#3-后端设计)
4. [数据库设计](#4-数据库设计)
5. [API 参考](#5-api-参考)
6. [部署指南](#6-部署指南)
7. [开发指南](#7-开发指南)

---

## 1. 系统架构

### 1.0 执行模型（Phase 2+ 现行）

```
Trigger (chat|inbox|cron|cluster|subagent)
        │
        ▼
   AgentRun  ◄── checkpoint / budget / origin / parent_run_id
        │
        ├── Kernel Process (mediate · charge · suspend)
        ├── permission_court → policy.decision 审计链
        └── memory_bus (remember/recall/supersede)
```

列表：`GET /api/runs` · 恢复：`run_recovery` · 进化：`replay_validator`。

### 1.1 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│  Web / Electron / CLI / Channel  ── HTTP+WS ──► Next.js BFF │
├─────────────────────────────────────────────────────────────┤
│  FastAPI：Auth · Chat · Runs · Kernel · Evolution · Crew    │
│  Agent Loop + Kernel (court/budget) + Dispatcher/Inbox      │
│  memory_bus · RAG(可选) · MCP · Tools · Workflow            │
├─────────────────────────────────────────────────────────────┤
│  SQLite(默认) · Redis(可选) · Qdrant(可选) · 文件存储         │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 技术选型

| 层级 | 技术 | 版本 | 说明 |
|------|------|------|------|
| 前端框架 | Next.js | 16.2.10 | App Router, Static Export |
| UI 库 | React | 19.2.4 | Concurrent Features |
| 样式 | Tailwind CSS | 4.x | Utility-first |
| 状态管理 | Zustand | 5.0.14 | 轻量级状态 |
| 数据获取 | TanStack Query | 5.101.2 | 服务端状态 |
| 桌面框架 | Electron | 43.1.0 | 跨平台桌面 |
| 后端框架 | FastAPI | 0.139.0 | 异步高性能 |
| ORM | SQLAlchemy | 2.0.36 | 异步支持 |
| 数据库 | SQLite | 3.x | 默认，可切换 PostgreSQL |
| 向量数据库 | Qdrant | 1.12.1 | RAG 检索 |
| 任务调度 | croniter | 2.0+ | Cron 表达式解析 |
| LLM 协议 | MCP | 1.12.0 | Model Context Protocol |

---

## 2. 前端设计

### 2.1 目录结构

```
frontend/
├── app/                    # Next.js App Router 页面
│   ├── page.tsx           # 首页（对话主界面）
│   ├── layout.tsx         # 根布局（主题初始化）
│   ├── login/             # 登录页
│   ├── tasks/             # 任务管理
│   ├── cron/              # 定时任务
│   ├── knowledge/         # 知识库
│   ├── workflows/         # 工作流
│   ├── tools/             # 工具管理
│   ├── skills/            # 技能管理
│   ├── agents/            # Agent 配置
│   ├── settings/          # 系统设置
│   └── ...
├── components/            # React 组件
│   ├── chat/              # 对话组件
│   ├── tasks/             # 任务组件
│   ├── knowledge/         # 知识库组件
│   ├── workflow/          # 工作流组件
│   ├── ui/                # 通用 UI 组件
│   └── ...
├── stores/                # Zustand 状态管理
│   ├── themeStore.ts      # 主题状态
│   ├── sessionStore.ts    # 会话状态
│   └── ...
├── hooks/                 # 自定义 Hooks
├── lib/                   # 工具函数
├── electron/              # Electron 主进程
│   ├── main.ts            # 入口
│   └── preload.ts         # 预加载脚本
└── public/                # 静态资源
```

### 2.2 核心页面

| 页面 | 路径 | 功能 |
|------|------|------|
| 首页 | `/` | 对话主界面，支持多会话切换 |
| 任务 | `/tasks` | 任务列表、创建、执行历史 |
| 定时任务 | `/cron` | Cron 任务管理、执行日志 |
| 知识库 | `/knowledge` | 文档管理、向量检索 |
| 工作流 | `/workflows` | 工作流编排、执行监控 |
| 工具 | `/tools` | MCP 工具管理 |
| 技能 | `/skills` | Agent 技能配置 |
| 设置 | `/settings` | 系统配置、模型设置 |

### 2.3 状态管理

使用 Zustand 管理客户端状态：

```typescript
// stores/themeStore.ts
interface ThemeState {
  theme: 'light' | 'dark' | 'system';
  setTheme: (theme: Theme) => void;
}

// stores/sessionStore.ts
interface SessionState {
  currentSession: Session | null;
  sessions: Session[];
  setCurrentSession: (session: Session) => void;
}
```

### 2.4 主题系统

支持 Light / Dark / System 三种模式：

```typescript
// 初始化时从 localStorage 恢复
const stored = localStorage.getItem('takton-theme');
if (stored === 'dark' || stored === 'light') {
  document.documentElement.classList.add(stored);
  document.documentElement.setAttribute('data-theme', stored);
}
```

---

## 3. 后端设计

### 3.0 Agent Kernel（控制平面，v0.4 引入）

`backend/kernel/` 是系统的控制平面，把「一次 agent 运行」抽象为 **AgentProcess**，
所有执行动作统一经 **Kernel.mediate()** 中介并留下不可变审计事件。

```
backend/kernel/
├── process.py      # AgentProcess：identity / capabilities / token_budget / 生命周期状态机
├── capability.py   # CapabilityToken：narrowing 单调递减（子令牌只能是父令牌子集）
└── kernel.py       # AgentKernel：进程管理 + mediate() + 预算治理 + 审计事件
```

核心语义：

- **能力单调递减**：`create_process(parent_id=...)` 时子进程能力只能是父进程子集，
  试图扩大抛 `CapabilityEscalationError`；子进程预算不得超过父进程剩余预算。
- **兼容模式**：`capabilities=None` 的进程不启用能力强制（旧路径行为不变），
  显式能力集进程每次 mediate 强制检查，未授权抛 `KernelPermissionError`。
- **令牌强制（W2）**：`issue_token()` 将 CapabilityToken 挂载到进程后，
  mediate 以令牌为准——过期（`expires_at`）与范围外一律拒绝；
  令牌可 `narrow()` 出更严格子令牌（过期时间同样单调递减）。
- **全路径中介（W3）**：所有工具调用（含并行预取）经
  `loop._execute_registered_tool → kernel.mediate("tool_call")`；
  dynamic skill 经 `mediate("skill_exec")`（`_kernel_process_id` 由 loop 注入）；
  MCP 工具走统一注册表，同样覆盖。拦截返回工具级错误反馈模型，不炸 run。
- **预算强制**：`llm_round` 每轮按 provider usage 扣减进程预算，
  耗尽置 `_should_stop` 中断 run 并返回 `[Budget Exceeded]`。
- **Intent Declaration（阶段 2 雏形）**：`kernel/intent.py`
  声明（goal/capabilities/constraints）→ 白名单策略合成最小能力令牌；
  高危能力需 `allow_risky=True`；有父令牌自动 narrow。
- **哈希链审计（阶段 3）**：每条事件 `prev_hash` 链接前一条（SHA-256），
  `kernel.verify_event_chain()` 检测篡改；环形缓冲截断不算断链。
- **观测 API**：`GET /api/kernel/processes`（进程树：能力/预算/状态）、
  `GET /api/kernel/events`（中介审计流，含哈希），Security Console
  `/security` 页 Kernel 区块 5s 轮询展示。
- **系统服务化（阶段 3）**：`deploy/takton-backend.service`（systemd 加固单元）
  + `deploy/README.md`（Linux systemd / Windows NSSM）。
- **审计落盘（阶段 3）**：`kernel/audit_store.py` 事件追加 JSONL
  （默认 `~/.takton/kernel_events.jsonl`，`agent_kernel_audit_persist` 可关），
  重启后新事件从磁盘链尾续 `prev_hash`（跨进程链连续），
  `verify_file_chain()` 全量验证；写失败只告警不阻断。
- **多 Agent 调度器（阶段 2 雏形）**：`kernel/scheduler.py`
  优先级队列（数值小者优先）+ 同优先级 FIFO + aging 防饿死
  （等待超阈值自动提档），`kernel.scheduler` 挂载。
- **动态 skill 强隔离（阶段 2）**：`computer/sandbox_exec.py`
  bwrap 包装（断网/clearenv/只读系统目录/隔离 HOME），
  `_run_code_in_subprocess(sandbox=...)` 三态：off/auto（默认，有则用）/
  required（无则拒绝）；工作流节点默认 off 不受影响。
- **Evolution 生成 Kernel 配置（阶段 3 雏形）**：
  `evolution/improver.propose_kernel_config(events)` 从审计事件分析
  高频被拒能力与预算耗尽，产出 `kernel_config` 建议资产（auto_apply=False，
  必须人工确认）——Kernel 观测 → Evolution 分析 → 策略建议的闭环。
- **接入点**：`agent/loop.py::_run_inner` 在 run 生命周期内创建/结束进程；
  配置开关 `agent_kernel_enabled`（默认开），装配失败显式降级并告警。
- **Intent Declaration（已接线）**：`apply_intent_to_process`；loop 在
  create_process 后读取 `_intent_declaration` / process options.intent；
  subagent_runner 默认注入只读意图（可 `allow_risky` / 显式 capabilities）。
- **演进路线**：调度器与 loop 执行耦合（替换 session 锁）；
  审计建议的人工确认 UI、多设备同步加固。

### 3.1 目录结构

```
backend/
├── main.py                # FastAPI 应用入口
├── cli.py                 # 命令行接口
├── database.py            # 数据库连接
├── api/                   # API 路由
│   ├── routes/            # 各模块路由
│   │   ├── auth.py        # 认证
│   │   ├── chat.py        # 对话
│   │   ├── tasks.py       # 任务
│   │   ├── cron.py        # 定时任务
│   │   ├── knowledge.py   # 知识库
│   │   ├── workflows.py   # 工作流
│   │   ├── tools.py       # 工具
│   │   ├── skills.py      # 技能
│   │   └── ...
│   ├── dependencies.py    # 依赖注入
│   └── websocket.py       # WebSocket 处理
├── agent/                 # Agent 核心
│   ├── loop.py            # Agent 循环
│   ├── context.py         # 上下文管理
│   ├── context_engine.py  # 上下文引擎
│   ├── system_prompt.py   # 系统提示词
│   └── ...
├── models/                # SQLAlchemy 模型
├── schemas/               # Pydantic 模式
├── services/              # 业务服务
├── repositories/          # 数据访问层
├── core/                  # 核心配置
├── evolution/             # 进化系统
├── mcp_hub/               # MCP 协议集成
├── tools/                 # 内置工具
└── tests/                 # 测试
```

### 3.2 核心模块

#### 3.2.1 Agent 循环 (`agent/loop.py`)

Agent 的主执行循环：

```python
async def agent_loop(
    session_id: str,
    user_message: str,
    context: AgentContext
) -> AsyncGenerator[AgentEvent, None]:
    """
    Agent 执行循环
    1. 构建上下文
    2. 调用 LLM
    3. 执行工具调用
    4. 返回流式响应
    """
```

#### 3.2.2 上下文引擎 (`agent/context_engine.py`)

管理对话上下文，支持：
- 上下文压缩（token 限制）
- 目标追踪
- 断点续传

#### 3.2.3 工作流引擎 (`services/workflow_engine.py`)

可视化工作流执行：

```python
class WorkflowEngine:
    async def execute(self, workflow_id: str, input_data: dict):
        """执行工作流"""
        # 1. 加载工作流定义
        # 2. 拓扑排序节点
        # 3. 按序执行
        # 4. 处理条件分支
```

#### 3.2.4 定时调度 (`services/cron_scheduler.py`)

基于 croniter 的定时任务调度：

```python
class CronScheduler:
    async def start(self):
        """启动调度器"""
        while True:
            await self.check_and_execute()
            await asyncio.sleep(60)
```

### 3.3 认证系统

JWT Token 认证：

```python
# 登录获取 Token
POST /api/auth/login
{
  "email": "user@example.com",
  "password": "password"
}

# 响应
{
  "access_token": "eyJ...",
  "token_type": "bearer"
}

# 后续请求携带
Authorization: Bearer eyJ...
```

---

## 4. 数据库设计

### 4.1 核心表结构

#### 用户表 (`users`)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| email | String | 邮箱（唯一） |
| username | String | 用户名 |
| hashed_password | String | 密码哈希 |
| is_active | Boolean | 是否激活 |
| created_at | DateTime | 创建时间 |

#### 会话表 (`sessions`)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| user_id | UUID | 用户 ID |
| title | String | 会话标题 |
| agent_profile_id | UUID | Agent 配置 |
| created_at | DateTime | 创建时间 |
| updated_at | DateTime | 更新时间 |

#### 消息表 (`messages`)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| session_id | UUID | 会话 ID |
| role | Enum | user / assistant / system / tool |
| content | Text | 消息内容 |
| tool_calls | JSON | 工具调用 |
| created_at | DateTime | 创建时间 |

#### 任务表 (`tasks`)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| session_id | UUID | 会话 ID |
| title | String | 任务标题 |
| status | Enum | pending / running / completed / failed |
| result | JSON | 执行结果 |
| created_at | DateTime | 创建时间 |

#### 定时任务表 (`cron_jobs`)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| name | String | 任务名称 |
| schedule | String | Cron 表达式 |
| command | JSON | 执行命令 |
| is_active | Boolean | 是否激活 |
| last_run_at | DateTime | 最后执行时间 |

#### 知识库表 (`knowledge_documents`)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| title | String | 文档标题 |
| content | Text | 文档内容 |
| embedding | Vector | 向量嵌入 |
| metadata | JSON | 元数据 |
| created_at | DateTime | 创建时间 |

#### 工作流表 (`workflows`)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| name | String | 工作流名称 |
| description | Text | 描述 |
| nodes | JSON | 节点定义 |
| edges | JSON | 边定义 |
| is_active | Boolean | 是否激活 |

### 4.2 数据库迁移

使用 Alembic 管理数据库版本：

```bash
# 生成迁移
alembic revision --autogenerate -m "description"

# 执行迁移
alembic upgrade head
```

---

## 5. API 参考

### 5.1 认证

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/login` | 用户登录 |
| POST | `/api/auth/register` | 用户注册 |
| GET | `/api/auth/me` | 获取当前用户 |

### 5.2 对话

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/sessions` | 获取会话列表 |
| POST | `/api/sessions` | 创建会话 |
| GET | `/api/sessions/{id}` | 获取会话详情 |
| DELETE | `/api/sessions/{id}` | 删除会话 |
| GET | `/api/sessions/{id}/messages` | 获取消息历史 |
| WS | `/api/chat/ws` | WebSocket 对话 |

### 5.3 任务

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/tasks` | 获取任务列表 |
| POST | `/api/tasks` | 创建任务 |
| GET | `/api/tasks/{id}` | 获取任务详情 |
| POST | `/api/tasks/{id}/cancel` | 取消任务 |

### 5.4 定时任务

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/cron` | 获取定时任务列表 |
| POST | `/api/cron` | 创建定时任务 |
| PUT | `/api/cron/{id}` | 更新定时任务 |
| DELETE | `/api/cron/{id}` | 删除定时任务 |
| GET | `/api/cron/{id}/logs` | 获取执行日志 |

### 5.5 知识库

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/knowledge/documents` | 获取文档列表 |
| POST | `/api/knowledge/documents` | 上传文档 |
| DELETE | `/api/knowledge/documents/{id}` | 删除文档 |
| POST | `/api/knowledge/search` | 向量检索 |

### 5.6 工作流

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/workflows` | 获取工作流列表 |
| POST | `/api/workflows` | 创建工作流 |
| GET | `/api/workflows/{id}` | 获取工作流详情 |
| PUT | `/api/workflows/{id}` | 更新工作流 |
| DELETE | `/api/workflows/{id}` | 删除工作流 |
| POST | `/api/workflows/{id}/execute` | 执行工作流 |

### 5.7 WebSocket 协议

```javascript
// 连接
const ws = new WebSocket('ws://localhost:8000/api/chat/ws');

// 发送消息
ws.send(JSON.stringify({
  type: 'message',
  session_id: 'xxx',
  content: 'Hello'
}));

// 接收流式响应
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  switch (data.type) {
    case 'token':      // 流式 token
    case 'tool_call':  // 工具调用
    case 'done':       // 完成
    case 'error':      // 错误
  }
};
```

---

## 6. 部署指南

### 6.1 桌面客户端部署

#### Windows

```bash
cd frontend
npm run dist:win
# 输出: release/Takton Setup 0.1.2.exe
```

#### Linux

```bash
cd frontend
npm run dist:linux
# 输出: 
#   release/Takton-0.1.2.AppImage
#   release/takton_0.1.2_amd64.deb
```

### 6.2 服务器部署

#### Docker（推荐）

```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app

# 安装后端依赖
COPY backend/requirements.txt .
RUN pip install -r requirements.txt

# 复制代码
COPY backend/ ./backend/
COPY frontend/dist ./frontend/dist

# 启动
CMD ["python", "backend/main.py"]
```

```bash
# 构建
docker build -t takton .

# 运行
docker run -p 8000:8000 -v takton-data:/data takton
```

#### 手动部署

```bash
# 1. 安装依赖
pip install -r backend/requirements-prod.txt

# 2. 构建前端
cd frontend
NEXT_EXPORT=1 npm run build

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env 设置数据库、密钥等

# 4. 启动
python backend/main.py
```

### 6.3 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `DATABASE_URL` | 数据库连接 | `sqlite:///./takton.db` |
| `SECRET_KEY` | JWT 密钥 | 随机生成 |
| `QDRANT_URL` | Qdrant 地址 | `http://localhost:6333` |
| `EMBEDDING_BASE_URL` | Embedding 服务（OpenAI 兼容） | `http://localhost:8086/v1` |
| `EMBEDDING_MODEL` | Embedding 模型 | 任意 OpenAI 兼容 Embedding 模型（如 BGE 系列） |
| `RERANKER_BASE_URL` | Reranker 服务（OpenAI 兼容） | `http://localhost:8087/v1` |
| `RERANKER_MODEL` | Reranker 模型 | 任意 OpenAI 兼容 Reranker 模型 |
| `OPENAI_API_KEY` | OpenAI API Key | - |
| `OPENAI_BASE_URL` | OpenAI 兼容 API | `https://api.openai.com/v1` |

---

## 7. 开发指南

### 7.1 环境要求

- Python 3.10+
- Node.js 20+
- npm / pnpm / yarn

### 7.2 初始化开发环境

```bash
# 克隆仓库
git clone https://github.com/wu1w/takton.git
cd takton

# 后端
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r backend/requirements-dev.txt

# 前端
cd frontend
npm install
```

### 7.3 启动开发服务器

```bash
# 方式 1：一键启动（推荐）
python start.py

# 方式 2：分别启动
# 终端 1 - 后端
cd backend
python main.py

# 终端 2 - 前端
cd frontend
npm run dev
```

### 7.4 代码规范

- Python: 遵循 PEP 8，使用 `ruff` 检查
- TypeScript: 遵循 ESLint 配置
- Git Commit: 遵循 Conventional Commits

### 7.5 测试

```bash
# 后端测试
cd backend
pytest

# 前端 E2E 测试
cd frontend
npx playwright test
```

---

## 附录

### A. 端口说明

| 端口 | 服务 | 说明 |
|------|------|------|
| 3000 | Next.js Dev | 前端开发服务器 |
| 8000 | FastAPI | 后端 API 服务 |
| 6333 | Qdrant | 向量数据库 |
| 8086 | Embedding | 本地 Embedding 服务示例（llama-server，模型自选） |
| 8087 | Reranker | 本地 Reranker 服务示例（llama-server，走 chat/logprobs） |

### B. 文件路径

| 路径 | 说明 |
|------|------|
| `~/.takton/` | 用户数据目录（Linux/Mac） |
| `%APPDATA%/takton/` | 用户数据目录（Windows） |
| `backend/uploads/` | 上传文件存储 |
| `frontend/dist/` | 前端构建输出 |

### C. 更新日志

- **v0.2.6** (2026-07-23)
  - 主脑 L0–L4：coding profile、工具预算/重试、workspace 契约、hooks、sidecar packs
  - 桌面 Linux 截图链路修复；真显示/Xvfb E2E；高强度 bench 双模型
  - takton-code TUI lazy import；版本号对齐 0.2.6
- **v0.2.5** (2026-07-22)
  - 自主进化开关跨重启持久化：`EvolutionConfig` 运行时开关字段（enabled/mode/auto_apply_*/from_*/curator 等 9 项）写入 `evolution_config.json`（与 evolution.db 同目录），启动时 env 默认值 + 持久化覆盖，重启不丢（`backend/evolution/config.py`）
  - 自主进化页资产列表项直接加「查看/删除」小按钮：原先删除藏在右侧详情面板（需先选中），现每项行内可操作，复用 onDelete（确认弹窗 + seed 保护 + toast + 自动刷新），整行 button 改 div 修掉嵌套 button 无效 HTML（`frontend/app/evolution/page.tsx`）
  - bridge 打通 evolution：`/bridge/v1/evolution/{status,assets,from_task}` 三端点（GET 读状态/资产 + POST 回流），复用 bridge_auth，转发 get_evolution_manager，契约收敛在 /bridge/v1/* 下（`backend/api/routes/bridge.py`）
  - takton-code 源码并入主仓 `takton-code/`：bridge client 新增 evolution_status/evolution_assets/report_outcome 三方法（NullBridge no-op + TaktonBridge HTTP 实现），protocol 注册 EvolutionAssetInfo/EvolutionOutcomeRequest schema + 3 条 BRIDGE_ROUTES。Code 学到的 task outcome 可经 `report_outcome` 回流 Desktop evolution P1 管线生成 skill 资产（端到端实测 FULL CHAIN E2E PASS）
  - 版本号统一：package.json / frontend/package.json / backend/main.py / bridge.py / README 全量对齐 0.2.5

- **v0.2.4** (2026-07-21)
  - RAG 全家桶接入：Qdrant 向量库 + 本地 Embedding/Reranker 服务（OpenAI 兼容），配置经 settings 持久化至 DB，重启保留
  - 修复 RAG 检索全链路卡死的真 bug：`QdrantRAGService.__init__` 未初始化 `self._ensured_collections`，`_ensure_collection` 首次检索即 AttributeError（`backend/services/rag/qdrant_impl.py`）
  - Chat 式 Reranker 精排：部分模型的 llama.cpp 原生 `/v1/rerank` 因 prompt 模板不兼容返回坏分数（relevance_score 1e-24、排序反转）。新增 `_qwen3_chat_rerank`：走 `/v1/chat/completions` + `enable_thinking=False` + `max_tokens=1` + `top_logprobs=50`，取 yes/no token logprob 做 softmax 归一化得相关性分数（`backend/services/reranker/local.py`）。单 session 复用 + 信号量限 2 路并发，规避 llama-server 突发多连接断连
  - 顶栏「打开 Takton Code」按钮：TitleBar 新增终端图标按钮，经 `open-takton-code` IPC 在系统终端拉起 takton-code TUI，注入 `TAKTON_CODE_BRIDGE_URL` 桥接当前 backend（复用 LLM/skills/tools/MCP/RAG）
  - takton-code 内嵌打包：PyInstaller `--onefile` 产物放到 vendor/takton-code/（不进 git，见该目录 README）；打包时可拷入 extraResources。顶栏按钮三级探测：PATH → 开发 bundle venv → 打包 resources
  - 版本号统一：package.json / frontend/package.json / backend/main.py / bridge.py / README 全量对齐 0.2.4

- **v0.1.2** (2026-07-17)
  - P2: 输入草稿自动保存
  - P3: 主题保持修复
  - P3: Loading 组件统一

- **v0.2.0** (2026-07-17)
  - 中英双语界面（i18n）：登录页 + 设置页语言切换，Zustand persist 持久化
  - 自动集群模式：任务复杂度 ≥ 0.8 时自动拆分子代理并行执行（默认关闭，`agent_auto_cluster` 开启才生效）
  - Desktop Agent：7 个桌面操作工具（截图/点击/输入/滚动/文件读写），三级权限模型
  - 透明化面板：实时展示 Agent 思考过程和工具调用链
  - 长期记忆系统：Entity 自动提取 + Wiki 知识图谱
  - Prompt-Skill 集成：SKILL.md 自动注入 Agent system prompt
  - MCP 商店：多源（精选 + Official Registry）一键安装/转换
  - workspace 路径修复：解决 file_write 双层嵌套问题（`_resolve_workspace_path`）
  - 依赖修复：tzdata（时区）、croniter（cron 表达式解析）
  - shadcn/ui 组件库：11 个 UI 组件（button/card/badge/input/textarea/label/checkbox/separator/progress/scroll-area/tooltip/select）
  - GitHub README 中英双语重写：品牌 badge + 截图展示 + 架构图

- **v0.1.2** (2026-07-17)
  - 工作区人设持久化（IDENTITY.md / SOUL.md / CLAUDE.md / AGENTS.md）
  - Kimi Code 模型修复（kimi-for-coding / kimi-for-coding-highspeed）
  - 目标面板与对话尾部恢复

- **v0.1.1** (2026-07-16)
  - N1-N8 系列错误修复

- **v0.1.0** (2026-07-14)
  - 首个正式发布版本

---

*本文档随版本更新，最新版本请查看 [GitHub](https://github.com/wu1w/takton)*
