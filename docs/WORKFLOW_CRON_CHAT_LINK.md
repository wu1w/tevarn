# 工作流联动设计：Cron + 对话 + 模板

> 打通 Workflow / Cron / Session 三个模块，实现：
> 1. 工作流可绑定 Cron 定时执行，结果投递到指定会话
> 2. 聊天界面可直接调用工作流参与对话
> 3. 预制场景模板，用户开箱即用

---

## 一、架构总览

```
┌──────────────────────────────────────────────────────────────────┐
│                         联动关系                                    │
│                                                                   │
│  用户配置 Cron 定时任务                                              │
│       │                                                            │
│       ▼                                                            │
│  CronScheduler 到点触发                                             │
│       │                                                            │
│       ├──→ WorkflowEngine.execute(workflow_id, inputs)              │
│       │       │                                                    │
│       │       ▼                                                    │
│       │  执行 DAG（LLM / RAG / Python / HTTP 等节点）               │
│       │       │                                                    │
│       │       ▼                                                    │
│       │  格式化结果 → 写入 Session 消息 → WebSocket 推送给用户       │
│       │                                                            │
│  用户在聊天界面选择工作流                                            │
│       │                                                            │
│       ▼                                                            │
│  NexusAgentLoop 加载工作流 DAG 作为上下文                            │
│       │                                                            │
│       ▼                                                            │
│  主 Agent 参考工作流定义处理用户消息                                  │
│                                                                   │
│  预制模板                                                          │
│       │                                                            │
│       ▼                                                            │
│  用户"使用模板" → 自动创建工作流副本 → 可微调 → 可绑定 Cron/Session  │
└──────────────────────────────────────────────────────────────────┘
```

---

## 二、Flow 1：Workflow → Cron → Session（定时投递）

### 2.1 数据模型变更

**CronJob 表新增字段**：

```python
class CronJob(Base, UUIDMixin, TimestampMixin):
    # 现有字段
    user_id, name, schedule, workflow_id, enabled
    last_run_at, next_run_at, last_status, last_error

    # === 新增字段 ===
    session_id: uuid.UUID | None   # FK → sessions.id
    # 结果投递目标：
    # - 有 session_id → 写入该 session 的消息流
    # - 无 session_id → 自动创建名为"{workflow名称} 运行结果"的 session
    deliver_as: str = "assistant"  # "assistant" | "user" — 以什么角色投递
    result_format: str = "auto"     # "auto" | "raw" | "summary"
    # ============
```

**Workflow 表新增字段（模板相关）**：

```python
class Workflow(Base, UUIDMixin, TimestampMixin):
    # 现有字段
    name, description, dag, status, trigger, variables, user_id

    # === 新增字段 ===
    icon: str = "⚡"               # 模板图标
    category: str = ""              # 模板分类（daily/weekly/monitor/...）
    is_template: bool = False       # 是否为预制模板（模板不可编辑，用于复制）
    from_template_id: uuid.UUID | None  # 从哪个模板创建的
    usage_count: int = 0            # 使用次数（热门排序用）
    # ============
```

### 2.2 执行流程

```
Cron 到点触发
  │
  ├→ 1. 加载 CronJob，获取 workflow_id + session_id
  │
  ├→ 2. 加载 Workflow DAG
  │     WorkflowEngine.execute(workflow.dag, inputs=job.variables)
  │
  ├→ 3. 获取执行结果（result 对象）
  │
  ├→ 4. 格式化结果
  │     - result_format="auto": 如果结果是纯文本直接用，否则 JSON 序列化
  │     - result_format="summary": 调用 LLM 生成简要摘要
  │     - result_format="raw": 原始 JSON 输出
  │
  ├→ 5. 投递到 Session
  │     - 有 session_id → message_repo.create(session_id, role=deliver_as, content=formatted)
  │     - 无 session_id → 创建新 Session → 写入消息
  │
  ├→ 6. WebSocket 推送
  │     - 如果 session 当前有 WebSocket 连接，实时推送新消息
  │
  └→ 7. 更新 CronJob 状态
        - last_run_at, last_status, last_error
```

### 2.3 关键代码改动

**CronScheduler._execute_job()** — 重写现有占位逻辑：

```python
async def _execute_job(self, job: Any) -> None:
    """执行 cron job：触发 workflow → 投递到 session"""
    from backend.services.workflow_engine import WorkflowEngine
    from backend.repositories import WorkflowRepository, MessageRepository, SessionRepository
    from backend.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        # 1. 加载 workflow
        workflow_repo = WorkflowRepository(db)
        workflow = await workflow_repo.get_by_id(job.workflow_id)
        if not workflow:
            raise ValueError(f"Workflow {job.workflow_id} not found")

        # 2. 执行
        engine = WorkflowEngine()
        result = await engine.execute(workflow.dag, inputs=workflow.variables)

        # 3. 格式化
        formatted = format_workflow_result(result, job.result_format)

        # 4. 获取/创建 session
        session_id = job.session_id
        if not session_id:
            session_repo = SessionRepository(db)
            session = await session_repo.create({
                "user_id": job.user_id,
                "title": f"{workflow.name} 运行结果",
                "config": {},
            })
            session_id = session.id

        # 5. 写入消息
        message_repo = MessageRepository(db)
        message = await message_repo.create({
            "session_id": session_id,
            "role": job.deliver_as,
            "content": formatted,
        })

        # 6. WebSocket 推送
        from backend.api.websocket import ws_manager
        await ws_manager.send_to_session(session_id, {
            "type": "new_message",
            "message": {"role": job.deliver_as, "content": formatted},
        })
```

### 2.4 前端 Cron 配置页变化

在创建/编辑定时任务时，增加投递配置：

```
┌── 新建定时任务 ─────────────────────────────────────┐
│                                                      │
│  名称: [每日早报                              ]      │
│                                                      │
│  执行频率: [每天 ▼] 时间: [09:00]                    │
│                                                      │
│  执行工作流: [每日早报流程 ▼]                        │
│                                                      │
│  ┌── 结果投递 ─────────────────────────────┐        │
│  │  投递到会话: [📋 我的默认会话 ▼]         │        │
│  │              (或选择已有会话)              │        │
│  │                                            │        │
│  │  格式: [自动格式化 ▼]                      │        │
│  │  [自动格式化] [纯文本原始输出] [AI 摘要]    │        │
│  └────────────────────────────────────────────┘        │
│                                                      │
│  [创建]                                               │
└──────────────────────────────────────────────────────┘
```

---

## 三、Flow 2：聊天界面直接调用工作流

### 3.1 交互流程

用户在聊天输入框选择工作流 → 发送消息 → 主 Agent 参考工作流处理 → 回复

```
用户操作：
  1. 在输入框工具栏点击 [⚡ 工作流]
  2. 弹出下拉菜单：列出用户已创建的工作流
  3. 选中一个（如"合同条款审查"）
  4. 输入："帮我看这份合同"
  5. 发送

后端流程：
  1. 用户消息附带 workflow_ref
  2. NexusAgentLoop 检测到 workflow_ref
  3. 加载对应工作流的 DAG 配置
  4. 将 DAG 节点信息转化为 system prompt 注入：
     "当前会话已启用工作流「合同条款审查」，
      该工作流包含以下步骤：
      1. 条款提取 (LLM) → 2. 风险分析 (RAG+LLM) →
      3. 合规检查 (LLM) → 4. 报告生成 (LLM)
      请参考此流程处理用户请求。"
  5. Agent 按工作流逻辑处理消息 → 回复
```

### 3.2 消息协议

**前端 → 后端**（WebSocket 消息）：

```json
{
  "type": "user_message",
  "content": "帮我看这份合同",
  "session_id": "xxx",
  "workflow_ref": {
    "workflow_id": "uuid-of-workflow",
    "mode": "guide"     // "guide" | "execute"
  }
}
```

- `mode: "guide"` — 主 Agent 参考工作流逻辑处理（默认）。适合复杂任务需要 Agent 推理的情况
- `mode: "execute"` — 直接执行工作流 DAG，结果作为回复。适合确定性流程

### 3.3 前端 UI

**MessageInput 工具栏新增按钮**：

```
┌──────────────────────────────────────────────────────┐
│  [⚡ 工作流] [深度思考] [联网搜索] [附件] [发送]      │
└──────────────────────────────────────────────────────┘
  点击 ⚡ → 弹出：
┌── 选择工作流 ──────────────────────────────────────┐
│  🔍 搜索工作流...                                    │
│                                                      │
│  ┌──────────────────────────────────────────────┐   │
│  │ 📋 合同条款审查        🤖 参考模式            │   │
│  │   自动审查合同条款风险                         │   │
│  ├──────────────────────────────────────────────┤   │
│  │ 🔍 竞品分析            🤖 参考模式            │   │
│  │   分析竞争对手产品动态                         │   │
│  ├──────────────────────────────────────────────┤   │
│  │ 📊 周报生成            ⚡ 直接执行            │   │
│  │   根据本周消息自动生成周报                      │   │
│  └──────────────────────────────────────────────┘   │
│                                                      │
│  当前: 📋 合同条款审查                      [取消]   │
└──────────────────────────────────────────────────────┘
```

选中的工作流显示在输入框上方作为一个标签：

```
┌────────────────────────────────────────────────────┐
│  ⚡ 合同条款审查（参考模式）          ✕ 移除        │
│  ┌──────────────────────────────────────────┐      │
│  │ 帮我看这份合同                           │      │
│  └──────────────────────────────────────────┘      │
└────────────────────────────────────────────────────┘
```

### 3.4 后端改动：NexusAgentLoop 注入工作流

```python
class NexusAgentLoop:
    async def _inject_workflow_context(self, messages, workflow_ref):
        """将工作流 DAG 注入 system prompt"""
        workflow = await workflow_repo.get_by_id(workflow_ref["workflow_id"])
        if not workflow:
            return

        dag_desc = self._dag_to_description(workflow.dag)
        block = (
            f"## 当前会话已启用工作流：{workflow.name}\n\n"
            f"{workflow.description}\n\n"
            f"### 工作流步骤\n"
            f"{dag_desc}\n\n"
            f"请参考以上流程结构处理用户请求。"
        )
        self._append_to_system(messages, block)
```

### 3.5 API 端点

```python
# 获取用户可用的工作流列表（供聊天界面选择）
GET /api/workflows/for-chat
→ [{ id, name, description, icon, category, mode_supported }]

# 在会话中执行工作流（直接执行模式）
POST /api/workflows/{id}/execute-in-session/{session_id}
Body: { inputs: { user_message: "..." } }
→ { result, formatted }
```

---

## 四、Flow 3：预制模板

### 4.1 模板数据结构

模板就是设置了 `is_template=True` 的 Workflow。

**种子数据**：首次部署时通过 migration 或 startup hook 写入 8-10 个模板。

```python
TEMPLATES = [
    {
        "name": "每日早报",
        "description": "每天早上定时抓取信息，生成并发送日报",
        "icon": "📅",
        "category": "daily",
        "is_template": True,
        "trigger": "cron",
        "dag": {
            "nodes": [
                {"id": "n1", "type": "input", "label": "触发输入", ...},
                {"id": "n2", "type": "http", "label": "抓取新闻",
                 "config": {"url": "https://...", "method": "GET"}},
                {"id": "n3", "type": "llm", "label": "生成摘要",
                 "config": {"prompt": "请根据以下信息生成今日早报...", "model": ""}},
                {"id": "n4", "type": "output", "label": "输出结果", ...},
            ],
            "edges": [
                {"id": "e1", "from": "n1", "to": "n2"},
                {"id": "e2", "from": "n2", "to": "n3"},
                {"id": "e3", "from": "n3", "to": "n4"},
            ],
        },
        "variables": {
            "news_url": "https://news.example.com/top",
            "language": "zh-CN",
        },
    },
    # ... 更多模板
]
```

### 4.2 API 端点

```python
# 列出所有模板（按 category 分组，按 usage_count 排序）
GET /api/workflows/templates?category=daily
→ [{ id, name, description, icon, category, usage_count, ... }]

# 从模板创建工作流
POST /api/workflows/from-template/{template_id}
Body: { name: "我的每日早报", variables: { news_url: "..." } }
→ 创建新的 Workflow（is_template=False, from_template_id=template_id）
→ 自动跳转到画布编辑页
```

### 4.3 模板使用流程（前端）

```
用户打开"自动化"页面
  → 看到模板推荐区（按使用热度排序）
  → 点击"每日早报"模板
  → 弹出"使用此模板"对话框：
     ┌── 使用模板 ─────────────────────────┐
     │  📅 每日早报                           │
     │  每天早上9点生成发送日报               │
     │                                        │
     │  工作流名称: [我的每日早报       ]      │
     │                                        │
     │  需要配置的参数：                       │
     │  🔗 新闻源 URL: [https://...   ]       │
     │  🌐 语言: [zh-CN ▼]                    │
     │                                        │
     │  [确认创建]  [进入画布编辑]              │
     └────────────────────────────────────────┘
  → 确认后创建副本
  → 可立即绑定 Cron + Session
  → 也可进入画布微调
```

---

## 五、实施计划

### Sprint 1：后端数据层 + 执行链路（3-4天）

| 任务 | 说明 |
|------|------|
| CronJob 表新增字段 | `session_id`, `deliver_as`, `result_format` migration |
| Workflow 表新增字段 | `icon`, `category`, `is_template`, `from_template_id`, `usage_count` |
| 重写 `CronScheduler._execute_job()` | 加载 workflow DAG → 执行 → 格式化 → 写消息 → WebSocket 推送 |
| `format_workflow_result()` | 自动格式化 / 摘要 / 原始三种模式 |
| API：工作流模板 | `GET /workflows/templates`, `POST /workflows/from-template/{id}` |
| API：会话执行工作流 | `POST /workflows/{id}/execute-in-session/{session_id}` |
| API：聊天工作流列表 | `GET /workflows/for-chat` |

### Sprint 2：工作流注入 Agent Loop（2天）

| 任务 | 说明 |
|------|------|
| `NexusAgentLoop._inject_workflow_context()` | 将 DAG 转描述文本注入 system prompt |
| WebSocket 消息协议 | 支持 `workflow_ref` 字段 |
| `mode: "execute"` 模式 | 直接执行 workflow 而非 Agent 推理 |

### Sprint 3：前端 + 模板（3-4天）

| 任务 | 说明 |
|------|------|
| 输入框 `⚡ 工作流` 按钮 | 选择工作流弹窗 |
| 工作流标签条 | 显示当前选中的工作流 |
| Cron 投递配置 UI | 投递到会话 + 格式选择 |
| 模板列表页 | 按分类展示 + 使用入口 |
| 模板使用弹窗 | 参数配置 + 确认创建 |
| 种子模板 | 预置 8-10 个工作流模板 |

---

## 六、种子模板清单（首批）

| 模板 | 图标 | 类型 | 说明 |
|------|------|------|------|
| 每日早报 | 📅 | daily | 定时抓取信息 → LLM 生成摘要 → 推送到会话 |
| 周报汇总 | 📊 | weekly | 读取本周消息/任务 → 生成周报 → 投递 |
| 文档翻译 | 🌐 | utility | 上传文档 → 翻译 → 输出结果 |
| 合同审查 | 📋 | business | OCR/上传 → LLM 提取条款 → 风险分析 → 报告 |
| 竞品监控 | 🔍 | monitor | 定时爬取 → 差异分析 → 发送通知 |
| 数据看板 | 📈 | monitor | 定时查 DB → 生成图表 → 推送可视化报告 |
| 会议纪要 | 🎙️ | utility | 上传录音/文字 → LLM 整理 → 结构化纪要 |
| 邮件分类 | 📧 | business | 新邮件 → AI 分类 → 摘要 → 推送到会话 |
| 代码审查 | 🐛 | dev | Git push → Review → PR Comment |
| 知识库更新 | 📚 | utility | 新文档 → 自动索引 → 确认通知 |
