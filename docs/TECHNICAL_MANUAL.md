# Takton 技术手册

版本：0.1.2  
更新：2026-07-17

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

### 1.1 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                        用户层                                │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐        │
│  │  Web    │  │ Desktop │  │   API   │  │ Webhook │        │
│  │ Browser │  │ Electron│  │  Client │  │         │        │
│  └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘        │
│       └─────────────┴─────────────┴─────────────┘            │
│                         │                                    │
│                    HTTP / WebSocket                          │
│                         │                                    │
├─────────────────────────┼────────────────────────────────────┤
│                      前端层 (Next.js 16)                     │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  React 19 + Tailwind CSS 4 + Zustand + React Query  │    │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐   │    │
│  │  │  Chat   │ │  Tasks  │ │Knowledge│ │Workflows│   │    │
│  │  │  UI     │ │  UI     │ │  UI     │ │  UI     │   │    │
│  │  └─────────┘ └─────────┘ └─────────┘ └─────────┘   │    │
│  └─────────────────────────────────────────────────────┘    │
│                         │                                    │
│              Next.js API Routes (BFF)                        │
│                         │                                    │
├─────────────────────────┼────────────────────────────────────┤
│                      后端层 (FastAPI)                        │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐   │    │
│  │  │  Auth   │ │  Chat   │ │  Cron   │ │Workflow │   │    │
│  │  │ Service │ │ Service │ │Scheduler│ │ Engine  │   │    │
│  │  └─────────┘ └─────────┘ └─────────┘ └─────────┘   │    │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐   │    │
│  │  │  Agent  │ │Knowledge│ │   MCP   │ │  Tools  │   │    │
│  │  │  Loop   │ │  RAG    │ │  Hub    │ │ Registry│   │    │
│  │  └─────────┘ └─────────┘ └─────────┘ └─────────┘   │    │
│  └─────────────────────────────────────────────────────┘    │
│                         │                                    │
│              SQLAlchemy 2.0 (Async)                          │
│                         │                                    │
├─────────────────────────┼────────────────────────────────────┤
│                      数据层                                  │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐        │
│  │ SQLite  │  │ Qdrant  │  │  File   │  │  Cache  │        │
│  │(默认)   │  │(向量)   │  │ Storage │  │         │        │
│  └─────────┘  └─────────┘  └─────────┘  └─────────┘        │
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

### B. 文件路径

| 路径 | 说明 |
|------|------|
| `~/.takton/` | 用户数据目录（Linux/Mac） |
| `%APPDATA%/takton/` | 用户数据目录（Windows） |
| `backend/uploads/` | 上传文件存储 |
| `frontend/dist/` | 前端构建输出 |

### C. 更新日志

- **v0.1.2** (2026-07-17)
  - P2: 输入草稿自动保存
  - P3: 主题保持修复
  - P3: Loading 组件统一

- **v0.1.1** (2026-07-16)
  - N1-N8 系列错误修复

- **v0.1.0** (2026-07-14)
  - 首个正式发布版本

---

*本文档随版本更新，最新版本请查看 [GitHub](https://github.com/wu1w/takton)*
