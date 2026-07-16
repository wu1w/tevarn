# Takton UX 优化与 Agent 自配置工具设计 v2.0

> 覆盖：Settings 分层 / 侧边栏提示 / 自然语言工作流（嵌入画布） / Agent 自配置工具集
> 状态：小白已完成 Embedding 健壮性，从面向用户的 UI 改进开始

---

## 一、总体架构

```
┌──────────────────────────────────────────────────────────┐
│                    用户对话框                             │
│  （主 Agent，全能力）                                     │
│  ┌──────────────────────────────────────────────────┐   │
│  │  💬 "帮我改成深度思考模式" → Agent 调 set_config  │   │
│  │  💬 "创建一个每天早上 9 点的日报"                │   │
│  │     → Agent 调 create_workflow + create_cron     │   │
│  │  💬 "把这篇文章加入知识库" → Agent 调 upload_and_index │   │
│  └──────────────────────────────────────────────────┘   │
│         │                                               │
│         ▼                                               │
│  ┌──────────────────────────────────────────────────┐   │
│  │             Agent 自配置工具集                     │   │
│  │                                                   │   │
│  │  ┌──────────────┐ ┌──────────┐ ┌──────────────┐  │   │
│  │  │ 配置管理工具   │ │ 工作流工具 │ │ 定时任务工具 │  │   │
│  │  │ get_config   │ │ generate  │ │ create_cron  │  │   │
│  │  │ update_config│ │ _workflow │ │ list_cron    │  │   │
│  │  │ list_models  │ │ update_dag│ │ delete_cron  │  │   │
│  │  │ test_connect │ │ validate  │ │ run_now      │  │   │
│  │  │              │ │ execute   │ │              │  │   │
│  │  └──────────────┘ └──────────┘ └──────────────┘  │   │
│  │                                                   │   │
│  │  ┌──────────────┐ ┌──────────┐ ┌──────────────┐  │   │
│  │  │ 知识库工具    │ │ 内部查询  │ │ 系统管理     │  │   │
│  │  │ upload_doc   │ │ rag_search│ │ get_status   │  │   │
│  │  │ list_docs    │ │ wiki_qry  │ │ list_sessions│  │   │
│  │  │ index_doc    │ │ skill_run │ │ get_logs     │  │   │
│  │  │ search_kb    │ │           │ │              │  │   │
│  │  └──────────────┘ └──────────┘ └──────────────┘  │   │
│  └──────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
```

---

## 二、Settings 分层设计（第一优先级，小白可开始）

### 2.1 改造方案

现有 Settings 页改成**双层结构**，普通模式为默认。

```
┌── 设置 ───────────────────────────────────────────────┐
│  右上角: [普通模式 ▼] ← 默认  /  可切换为 [高级模式]   │
│                                                         │
│  ┌── AI 能力 ─────────────────────────────────────┐    │
│  │                                                  │    │
│  │  方式一：一键登录（推荐）                         │    │
│  │  ┌────────────────────────────────────────┐     │    │
│  │  │ 🤖 OpenAI  🤖 Anthropic  🤖 阿里云百炼  │     │    │
│  │  │ 🤖 Google  🤖 DeepSeek  🤖 MiniMax    │     │    │
│  │  │  点击后跳转授权，自动完成配置              │     │    │
│  │  └────────────────────────────────────────┘     │    │
│  │                                                  │    │
│  │  方式二：手动配置                                │    │
│  │  服务商: [OpenAI ▼]                              │    │
│  │  密钥:   [sk-... •••••••• ]  [👁]                │    │
│  │  模型:   [gpt-4o ▼]  [🔄 拉取列表]               │    │
│  │                                                  │    │
│  │  ▸ 高级选项  ← 点击展开                         │    │
│  │    服务地址  [https://api.openai.com/v1     ]    │    │
│  │    创意度    [0.7 ───●───────────]              │    │
│  │    最大回复  [4096]                             │    │
│  └──────────────────────────────────────────────────┘    │
│                                                         │
│  ┌── 偏好 ─────────────────────────────────────────┐    │
│  │  AI 回复风格: [均衡 ▼]                           │    │
│  │  界面语言: [中文 ▼]                              │    │
│  │  头像: [修改]                                    │    │
│  └──────────────────────────────────────────────────┘    │
│                                                         │
│  ▸ 高级设置  ← 默认折叠（完全技术参数）                   │
│    知识库、向量模型、Embedding、Qdrant 等                │
│                                                         │
└──────────────────────────────────────────────────────────┘
```

### 2.2 前端改动清单

| 文件 | 改动 |
|------|------|
| `frontend/app/settings/page.tsx` | 新增 mode 切换（普通/高级） |
| 新增普通模式视图 | 仅展示 AI 能力 + 偏好两大区块 |
| 现有简单/全部参数模式 | 保留，重命名为"高级模式" |

### 2.3 后端改动

- OAuth 回调处理路由（各供应商 OAuth 流程）
- 普通模式下保存时自动填充默认值（如 base_url 根据供应商自动补全）

---

## 三、侧边栏名词提示（第二优先级，可并行）

### 3.1 方案

每个技术术语旁加一个 `?` 圆圈图标，hover 显示解释气泡。

```
├ Agent
│  ├ 心智配置  [?]  ← hover: "调整 AI 的性格和思维方式..."
│  ├ 工具      [?]  ← hover: "给 AI 配置它能调用的能力..."
│  ├ MCP       [?]  ← hover: "通过标准协议连接外部服务..."
│  ├ 画像      [?]  ← hover: "管理 AI 在不同任务中的角色设定..."
│  ├ 上下文    [?]  ← hover: "查看和管理 AI 当前记忆的信息..."
│  └ 定时任务  [?]  ← hover: "让 AI 在指定时间自动执行任务..."
├ 记忆
│  ├ 知识库    [?]  ← hover: "上传文档让 AI 阅读并记住..."
│  └ Wiki图谱  [?]  ← hover: "以图谱形式管理知识关联..."
```

### 3.2 实现

```tsx
// components/ui/Tooltip.tsx — 已有类似组件，复用即可

// Sidebar.tsx 中修改
<NavItem>
  {label}
  <Tooltip text={tooltipText[label]}>
    <span className="...">?</span>
  </Tooltip>
</NavItem>
```

一个文件加一个字典，0.5 天搞定。

---

## 四、自然语言工作流（嵌入画布）

### 4.1 交互形态

不是独立的 tab，而是在现有画布编辑器**上方**加一个对话栏：

```
┌── 自动化 ─────────────────────────────────────────────────┐
│                                                           │
│  ┌── 模板推荐 ──────────────────────────────────────┐    │
│  │  📅 每日早报  📊 周报汇总  📋 合同审查  🔍 竞品监控  │    │
│  └────────────────────────────────────────────────────┘    │
│                                                           │
│  左边: 工作流列表         右边: [📐 画布]                  │
│  ┌──────────────┐       ┌───────────────────────────┐    │
│  │ 📅 每日早报   │       │  ┌── 对话创建 ──────────┐ │    │
│  │  每天 9:00    │       │  │ 💬 描述工作流...   [发送]│ │    │
│  │  运行中 ✅    │       │  └──────────────────────┘ │    │
│  │              │       │                           │    │
│  │ 📊 周报汇总   │       │    ┌────┐    ┌────┐      │    │
│  │  草稿        │       │    │输入│───▶│ LLM│      │    │
│  │              │       │    └────┘    └─┬──┘      │    │
│  │ [+ 新建]     │       │               ▼          │    │
│  └──────────────┘       │            ┌────┐       │    │
│                          │            │输出│       │    │
│                          │            └────┘       │    │
│                          └───────────────────────────┘    │
└──────────────────────────────────────────────────────────┘
```

### 4.2 交互流程

```
1. 用户切换到画布 tab，看到画布上方有一个小的对话栏
   ┌────────────────────────────────────────────────┐
   │ 💬 用自然语言创建工作流...  [发送]  [🎤 语音] │
   └────────────────────────────────────────────────┘

2. 用户输入："每天早上 9 点抓取行业新闻生成中文日报发飞书"

3. 主 Agent 收到请求（通过现有的 WebSocket 连接），
   调用自配置工具集中的 generate_workflow 工具

4. generate_workflow 返回 DAG 结构：
   { nodes: [...], edges: [...] }

5. 画布实时更新，显示生成的流程图
   同时对话栏中展示构建日志：
   "✅ 已创建步骤：定时触发 → 新闻抓取 → LLM 摘要 → 推送到飞书"

6. 用户可以直接在画布上拖拽微调
   也可以在对话栏继续输入："再加一个竞品分析步骤"

7. Agent 调用 update_dag 工具增量修改
   画布同步刷新
```

### 4.3 复用主 Agent 的方式

不创建新的 Agent Loop。对话栏的请求通过现有的 WebSocket 通道发送，但标记 `context: "workflow_builder"`。

```python
# WebSocket 消息格式
{
    "type": "user_message",
    "content": "每天早上9点抓新闻生成日报",
    "session_id": "current-session-id",
    "context": "workflow_builder",  # ← 标记为工作流构建模式
    "workflow_builder_session": "wb_xxx"  # 关联的工作流构建对话
}
```

主 Agent 检测到 `context: "workflow_builder"` 时：

1. **不加载 RAG/知识库上下文**（工作流构建不需要检索知识库）
2. **不加载用户自定义 skill**（避免干扰）
3. **注入工作流节点类型定义**到 system prompt
4. **限制工具集**：只暴露自配置工具中的工作流相关工具
5. **其余逻辑完全不变**（同一 LLM、同一会话管理、同一流式输出）

```python
class NexusAgentLoop:
    async def _build_messages(self, ...):
        messages = await super()._build_messages(...)
        
        if self._context == "workflow_builder":
            # 1. 不加载 RAG
            # 2. 注入节点类型定义
            self._append_to_system(messages, NODE_TYPE_DEFINITIONS)
            # 3. 限制工具集
            self._tool_filter = ["generate_workflow", "update_dag", 
                                 "add_node", "remove_node", 
                                 "add_edge", "validate_dag",
                                 "save_workflow", "list_templates"]
        
        return messages
```

### 4.4 对话历史持久化

工作流构建的对话独立于主聊天 Session，存在单独的存储中：

```sql
-- workflow_builder_sessions 表
id VARCHAR(64) PRIMARY KEY,
user_id UUID NOT NULL,
workflow_id UUID NULL,          -- 关联的工作流（保存后）
messages JSON NOT NULL,          -- 对话历史
current_dag JSON NOT NULL,       -- 当前 DAG 状态
created_at TIMESTAMP DEFAULT NOW(),
updated_at TIMESTAMP DEFAULT NOW()
```

这样用户关闭页面后回来，可以继续之前的对话接着完善。

### 4.5 画布同步机制

Agent 每次调用 DAG 修改工具后，后端通过 WebSocket 向画布推送 DAG 更新：

```json
{
    "type": "dag_update",
    "workflow_builder_session": "wb_xxx",
    "dag": { "nodes": [...], "edges": [...] },
    "build_log": "✅ 已添加 LLM 节点：生成摘要"
}
```

画布前端监听 `dag_update` 事件，实时刷新 DAG 展示。

---

## 五、Agent 自配置工具集

### 5.1 设计原则

让用户可以通过**自然语言对话**完成所有配置操作，不需要进入设置页面。

### 5.2 工具清单

#### 配置管理（4 个）

```python
{
    "name": "get_config",
    "description": "获取当前 AI 配置（模型、供应商、参数等）",
    "parameters": {
        "keys": ["llm_model", "llm_provider", "temperature"]  # 空=返回全部
    }
}
```

```python
{
    "name": "update_config",
    "description": "修改 AI 配置参数，如切换模型、调整创意度等",
    "parameters": {
        "updates": {
            "llm_model": "gpt-4o",
            "temperature": 0.7
        }
    }
}
```

```python
{
    "name": "list_available_models",
    "description": "列出当前供应商可用的模型列表",
    "parameters": {}
}
```

```python
{
    "name": "test_connection",
    "description": "测试当前 AI 配置的连接是否正常",
    "parameters": {}
}
```

#### 工作流工具（7 个）

```python
{
    "name": "generate_workflow",
    "description": "根据自然语言描述生成完整工作流 DAG",
    "parameters": {
        "description": "用户的需求描述",
        "name": "工作流名称（可选）"
    }
}
```

```python
{
    "name": "update_dag",
    "description": "更新工作流的 DAG（替换全部）",
    "parameters": { "dag": { "nodes": [...], "edges": [...] } }
}

{
    "name": "add_node",
    "description": "在工作流中添加一个节点",
    "parameters": { "type": "llm", "label": "生成摘要",
                     "config": {...}, "after_node": "n1" }
}

{
    "name": "remove_node",
    "description": "删除工作流中的节点",
    "parameters": { "node_id": "n2" }
}

{
    "name": "validate_dag",
    "description": "验证工作流 DAG 的合法性",
    "parameters": {}
}

{
    "name": "save_workflow",
    "description": "保存当前工作流到数据库",
    "parameters": { "name": "...", "description": "...", "icon": "📅" }
}

{
    "name": "list_workflow_templates",
    "description": "列出可用的工作流模板",
    "parameters": { "category": "daily" }
}
```

#### 定时任务工具（4 个）

```python
{
    "name": "create_cron_job",
    "description": "创建定时任务，可绑定工作流或直接执行 AI 任务",
    "parameters": {
        "name": "每日早报",
        "schedule": "0 9 * * *",        # cron 表达式
        "workflow_id": "uuid",           # 绑定工作流（可选）
        "prompt": "执行任务描述"         # 不绑定工作流时直接执行 AI
    }
}

{
    "name": "list_cron_jobs",
    "description": "列出所有定时任务",
    "parameters": {}
}

{
    "name": "update_cron_job",
    "description": "修改定时任务",
    "parameters": { "cron_id": "uuid", "updates": {...} }
}

{
    "name": "delete_cron_job",
    "description": "删除定时任务",
    "parameters": { "cron_id": "uuid" }
}
```

#### 知识库工具（4 个）

```python
{
    "name": "upload_and_index",
    "description": "上传文件到知识库并自动索引",
    "parameters": { "file_content": "base64", "filename": "...", "title": "..." }
}

{
    "name": "search_knowledge_base",
    "description": "搜索知识库",
    "parameters": { "query": "...", "top_k": 5 }
}

{
    "name": "list_documents",
    "description": "列出知识库中的文档",
    "parameters": {}
}

{
    "name": "delete_document",
    "description": "删除知识库中的文档",
    "parameters": { "document_id": "uuid" }
}
```

#### 状态查询（2 个）

```python
{
    "name": "get_system_status",
    "description": "获取系统运行状态（连接状态、模型信息、存储用量等）",
    "parameters": {}
}

{
    "name": "list_active_sessions",
    "description": "列出当前活跃的会话",
    "parameters": {}
}
```

### 5.3 注册方式

这些工具作为 Takton 内置工具，注册到现有 `ToolRegistry` 中。

```python
# backend/tools/builtins/self_config.py

from backend.tools.registry import registry

def register_self_config_tools():
    registry.register(
        name="get_config",
        toolset="self_config",
        schema=GET_CONFIG_SCHEMA,
        handler=handle_get_config,
    )
    registry.register(
        name="update_config",
        toolset="self_config",
        schema=UPDATE_CONFIG_SCHEMA,
        handler=handle_update_config,
    )
    # ... 注册所有 21 个工具
```

这些工具在**所有对话中默认可用**（不只在工作流构建模式），所以用户在普通聊天中也可以说：
- "帮我改成深度思考模式" → `update_config({"reasoning_effort": "high"})`
- "看看我有哪些定时任务" → `list_cron_jobs()`
- "把温度调到 0.8" → `update_config({"temperature": 0.8})`

### 5.4 安全约束

对 `update_config` 和 `delete_cron_job` 等破坏性操作：

```python
DESTRUCTIVE_TOOLS = {"update_config", "delete_cron_job", "delete_document", "remove_node"}

# 在执行前需要用户确认（同现有 terminal 命令的审批机制）
async def handle_update_config(args, user_id, requires_confirmation=True):
    if any(k in args.get("updates", {}) for k in SENSITIVE_KEYS):
        # API Key 等敏感字段需要额外确认
        return {"status": "confirmation_required", "changes": list(args["updates"].keys())}
    # 执行更新
```

---

## 六、用户场景示例

### 场景 1：用户想换模型

```
用户："帮我换到深度思考模式"

Agent 调用：get_config()
→ 当前模型: gpt-4o, temperature: 0.7

Agent 调用：update_config({"llm_model": "gpt-4o-thinking", "temperature": 0.3})
→ AI 配置已更新

Agent 回复："已切换到深度思考模式（GPT-4o Thinking，创意度降至 0.3）"
```

### 场景 2：用户想创建定时工作流

```
用户："每天早上 9 点抓热点新闻生成 200 字摘要发给我"

Agent 调用：generate_workflow("每天早上9点抓热点新闻生成200字摘要")
→ 返回 DAG

Agent 调用：save_workflow({name: "每日早报", dag: ...})
→ 已保存

Agent 调用：create_cron_job({name: "每日早报", schedule: "0 9 * * *", workflow_id: "xxx"})
→ 已创建

Agent 回复（同时画布显示 DAG）：
"已创建「每日早报」工作流：
 ① 每天早上 9 点触发定时器
 ② 抓取热点新闻（配置中）
 ③ LLM 生成 200 字摘要
 ④ 推送到你的会话

 你可以在下方画布中微调细节，或直接说'再加一个翻译成英文的步骤'"
```

### 场景 3：用户想了解系统状态

```
用户："我的 AI 现在什么状态？"

Agent 调用：get_system_status()
→ { model: "gpt-4o", provider: "OpenAI", connected: true,
    knowledge_base: { documents: 12, chunks: 2341 },
    cron_jobs: [{name: "每日早报", status: "running"}] }

Agent 回复：
"🤖 AI 运行正常
• 当前模型: GPT-4o（OpenAI）
• 知识库: 12 篇文档，2341 个分块
• 定时任务: 1 个正在运行（每日早报）
• 所有服务连接正常"
```

---

## 七、实施计划

### Phase 1：UI 优化（小白可立即开始，3-4 天）

| 任务 | 工作量 | 前置 |
|------|--------|------|
| Settings 分层（折叠+普通模式） | 2 天 | 无 |
| OAuth 一键登录按钮 | 1 天 | 后端 OAuth 路由 |
| 侧边栏 `?` 提示 | 0.5 天 | 无 |
| **总计** | **~3.5 天** | |

### Phase 2：Agent 自配置工具（4-5 天）

| 任务 | 工作量 | 前置 |
|------|--------|------|
| 配置管理工具（4 个） | 1 天 | 无 |
| 工作流工具（7 个） | 2 天 | Phase 1 |
| 定时任务工具（4 个） | 1 天 | 无 |
| 知识库工具（4 个） | 0.5 天 | 无 |
| 状态查询工具（2 个） | 0.5 天 | 无 |
| 安全约束（确认机制） | 0.5 天 | 无 |
| **总计** | **~5.5 天** | |

### Phase 3：自然语言工作流嵌入画布（3-4 天）

| 任务 | 工作量 | 前置 |
|------|--------|------|
| 画布对话栏 UI | 1 天 | Phase 1 |
| 工作流构建模式（context 分发） | 1 天 | Phase 2 |
| 画布 DAG 同步（dag_update 推送） | 1 天 | 无 |
| 对话持久化 | 0.5 天 | 无 |
| **总计** | **~3.5 天** | |

---

## 八、关键设计决策

1. **不建独立 Agent Loop**——工作流对话直接复用主 Agent，只通过 `context` 标记限制工具集和上下文加载。用户看到的是同一套对话体验
2. **自配置工具默认全对话可用**——不只是工作流构建模式，普通聊天也能用。用户说"帮我改模型"就改，不需要进设置页
3. **画布和对话双向同步**——Agent 改 DAG → 画布刷新；用户拖画布 → 对话栏感知。两种操作方式不互斥
4. **对话持久化独立于主会话**——工作流构建的对话有独立的存储，关页面回来可以继续
5. **破坏性操作需确认**——改配置/删定时任务等操作需要用户二次确认
