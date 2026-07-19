"""
Takton 产品手册：对话配置与使用全指南。
供知识库 seed + configure_takton skill 共用。
"""

from __future__ import annotations

# 每个 topic: title + markdown body
PRODUCT_HANDBOOK: dict[str, dict[str, str]] = {
    "overview": {
        "title": "Takton 总览：用对话搞定一切",
        "body": """# Takton 总览：用对话搞定一切

Takton 是**自托管多机 Agent 工作台**。目标：尽量在**对话框**里完成配置与使用，少点菜单。

## 你可以直接说
- 「看看现在系统状态 / 用的什么模型」
- 「把温度调到 0.3」「max_tokens 改成 8000」
- 「怎么配模型 / RAG / 远程设备 / 通道」
- 「帮我配对 remote-pc」「列出现在的设备」
- 「教我用知识库 / 定时任务 / 工作流」
- 「我是小白，从头教」

## 功能地图（侧栏）
| 区域 | 页面 | 干什么 |
|------|------|--------|
| 工作区 | 对话 / 任务 / 设备 / 工作流 | 日常干活与异步任务 |
| Agent | 配置·模型 / 工具 / 技能 / 子代理 / MCP / 配置文件 | 能力与模型 |
| 记忆 | 上下文 / 定时 / 知识 / Wiki | 记忆与自动化 |
| 进化 | **自主进化** | 任务经验自动生成 skill/tool |
| 系统 | 通道 / 通知·Webhook / 设置 / Packages | 对外连接与扩展 |

## 原则
1. **先问状态，再改配置**（避免盲改 API Key）。
2. **高风险配置（API Key、服务地址）**必须你口头确认后再写。
3. **远程命令**用 `@设备名 …`，本机文件用对话 + 工具。
4. 菜单仍可用；对话是主路径。
""",
    },
    "models": {
        "title": "配置对话模型（Settings / 配置）",
        "body": """# 配置对话模型

## 对话可以说
- 「当前用的什么模型」
- 「帮我改成 xxx 模型」
- 「temperature 调低一点，写代码要稳」
- 「max_tokens 开到 12288」

## 页面路径
侧栏 **Agent → 配置**（或 **系统 → 设置** 的模型与服务）

## 步骤（小白）
1. 选服务商：OpenAI 兼容 / Ollama / Anthropic 等。
2. 填 **Base URL** 与 **API Key**（本地 Ollama 通常 Key 可随意或留空，按你的网关要求）。
3. 点「拉取模型」→ 点选一个模型保存。
4. 可选：备用模型、压缩模型（长对话用）。

## 关键配置项（settings key）
| key | 含义 | 风险 |
|-----|------|------|
| llm_provider | 服务商类型 | 中 |
| llm_model | 模型名 | 中 |
| llm_base_url | API 地址 | 高 |
| llm_api_key | 密钥 | 高 |
| temperature | 0~2，越大越活 | 低 |
| max_tokens | 单次生成上限 | 低 |
| context_window | 上下文窗口 | 中 |

## 注意
改 Key/地址属于高风险：Agent 会先问你确认，你说「确认修改」后再执行。
模型列表会缓存；离开再进设置应仍能看到已拉取列表。
""",
    },
    "embedding_rag": {
        "title": "配置 Embedding / 向量 RAG / 知识库",
        "body": """# Embedding · Qdrant · 知识库

## 对话可以说
- 「知识库怎么开」
- 「为什么搜不到文档」
- 「上传一份公司制度到知识库」
- 「用知识库回答：请假流程」

## 生效条件
向量 RAG 需要：**Embedding 配好 + Qdrant 地址非空 + rag_enabled**。  
否则是「本地模式」：仍可 Wiki / 文件 / memory，但向量检索不可用。

## 配置步骤
1. **配置 → Embedding**：provider、model、base_url、api_key。
2. **Qdrant**：`qdrant_url`（如 `http://127.0.0.1:6333`），collection 默认 `knowledge_base`。
3. 打开 `rag_enabled`。
4. **知识** 页上传文档 → 等待 indexed。
5. 对话：「根据知识库…」会触发 `search_knowledge_base`。

## 不配向量也能用
- 把说明写进 **Wiki**
- 工作区 memory.md
- 直接 `@设备` 读文件
""",
    },
    "devices": {
        "title": "远程设备与 @设备 配置",
        "body": """# 远程设备（多机）

## 对话可以说
- 「我有哪些设备」「remote-pc 在线吗」
- 「怎么配对新电脑」
- 「@remote-pc 看看磁盘」

## 页面
**工作区 → 设备**

## 配对
1. 目标机启动 takton-agent（默认端口 19876 + token）。
2. 设备页填名称、host、端口、token → 配对。
3. online + 延迟显示即成功。

## 对话
- `@remote-pc hostname`
- 设备页也可浏览目录、运行命令。
""",
    },
    "channels": {
        "title": "通道配置（QQ / 企微等）",
        "body": """# 通道 Channels

## 对话可以说
- 「QQ 通道怎么配」
- 「为什么通道收不到消息」

## 页面
**系统 → 通道**

## 注意
- 各通道凭证不同；按页面字段填。
- 双实例会导致配置改了不生效。
- 模型需能返回内容，否则通道侧几乎静音。
""",
    },
    "tools_skills": {
        "title": "工具 Tools 与技能 Skills",
        "body": """# 工具与技能

## 白话
- **Tools**：底层手（读文件、命令、浏览器…）  
- **Skills**：打包好的动作（天气、知识库、PPT…）
- **自主进化 (TEE)**：根据任务/对话失败经验**自动生成** skill / tool 草稿

## 对话可以说
- 「有哪些技能」
- 「能不能根据任务经验自动生成 skill」→ **可以**（见自主进化 TEE）
- 「关掉危险的 command」

## 页面
- **Agent → 工具** / **Agent → 技能**（含 `evo_*` 进化技能）
- **自主进化**：管理自动生成条目；删除会同步 Skills 列表
""",
    },
    "evolution": {
        "title": "自主进化 TEE（任务经验 → Skill）",
        "body": """# 自主进化 TEE v0.1.1

## 一句话
Takton **可以根据任务与对话经验自动生成 skill/tool**，写进 **自主进化** 条目，并同步到 **Skills 列表**。

## 不要再说「没有」
- 不要只查 `skills/dynamic.py` 就断言没有自动生成。
- 引擎在 `backend/evolution`。

## 触发
1. 对话失败模式（需 enabled）
2. 定时任务 cron 跑完
3. `POST /api/evolution/from_task`
4. 观察聚类

## 管理
- 列表 / 启用 / 禁用 / **删除**（删进化 → 同步删 Skills）
- Skills 页删进化 skill → 同步删进化资产

## 打开
`POST /api/evolution/enable` `{"enabled": true}`
""",
    },
    "cron": {
        "title": "定时任务 Cron",
        "body": """# 定时任务

## 对话可以说
- 「每天早上 9 点提醒我看邮件」
- 「列出定时任务」

## 页面
**记忆 → 定时**

## 注意
- 绑定 **workflow_id**（不是旧的 command 列）。
- prompt/工作流要自包含。
- 工具 `manage_cron`。
""",
    },
    "workflows": {
        "title": "工作流 Workflows",
        "body": """# 工作流

## 对话可以说
- 「做一个自动处理日报的工作流」
- 「工作流怎么建」

## 页面
**工作区 → 工作流**

## 用法
1. 新建或加载示例。  
2. 编辑节点 → 校验 → 保存 → 运行。  
3. 可被定时任务绑定触发。
""",
    },
    "mcp_profiles": {
        "title": "MCP 与 Agent 配置文件 Profiles",
        "body": """# MCP · Profiles

## MCP
- 页面：**Agent → MCP**  
- 挂第三方 MCP Server 扩展工具面。

## Profiles
- 页面：**Agent → 配置文件**  
- 切换不同系统提示/工具偏好。
""",
    },
    "context": {
        "title": "上下文压缩与长对话",
        "body": """# 上下文 / 压缩

## 对话可以说
- 「长对话老是丢上下文怎么办」
- 「压缩阈值调高一点」

## 页面
**记忆 → 上下文** 与设置中压缩模型。

## 关键 key
context_window、context_threshold_percent、context_protect_first_n / last_n 等。
""",
    },
    "wiki": {
        "title": "Wiki 图谱",
        "body": """# Wiki

## 对话可以说
- 「在 Wiki 里记一下这个概念」
- 「搜索 Wiki」

## 页面
**记忆 → Wiki**  
图谱 + 实体/关系。与知识库（文档向量）互补。
""",
    },
    "sub_agents": {
        "title": "子代理 SubAgents",
        "body": """# 子代理（SubAgents）

## 白话
主 Agent 把一块活交给「子代理」：独立人设、可选独立模型、缩小工具面，干完交回摘要。

## 对话可以说
- 「有哪些子代理」
- 「用研究型子代理总结这篇文档」

## 页面
**Agent → 子代理**

## 用法
1. 建子代理：system 提示、模型、工具。  
2. 主对话委派或工作流节点挂 sub_agent。  
3. 委派时写清 context（子代理看不到你全部历史）。

## 与 Profile
| | 子代理 | Profile |
|--|--------|---------|
| 目的 | 专职任务 | 主会话人设 |
| 生命周期 | 任务级 | 会话/全局 |

## 注意
模型列表依赖设置里供应商 **缓存 models**；先拉取再选。
""",
    },
    "tasks": {
        "title": "任务 Tasks",
        "body": """# 任务（Tasks）

## 白话
跟踪异步作业：排队、运行中、成功/失败、日志。

## 对话可以说
- 「现在有哪些任务在跑」
- 「刚才那个任务失败原因」

## 页面
**工作区 → 任务**

## 关系
对话 = 当场交互；任务 = 单次异步；定时 = 周期触发；工作流 = 多节点流水线。
""",
    },
    "sessions_chat": {
        "title": "会话与对话 Chat / Sessions",
        "body": """# 会话与对话

## 白话
每次聊天属于一个 **Session**。新会话 = 新上下文。

## 对话可以说
- 「新开一个对话」
- 「回到刚才那个会话」

## 页面
**工作区 → 对话** + 侧栏会话列表

## 技巧
1. 换主题请新会话。  
2. 长任务用任务/工作流。  
3. 过长会触发上下文压缩。
""",
    },
    "notifications_webhooks": {
        "title": "通知与 Webhooks",
        "body": """# 通知 · Webhooks

## 通知
任务完成/失败等提醒（应用内或通道，以实现为准）。

## Webhooks
HTTP 回调：外部事件进 Takton，或结果推到外部。

## 对话可以说
- 「任务完成后怎么通知我」
- 「怎么配 webhook」

## 安全
校验签名/密钥；入站 payload 当不可信输入。

## 与通道
通道 = 人聊；Webhook = 系统对系统 HTTP。
""",
    },
    "packages": {
        "title": "扩展包 Packages",
        "body": """# 扩展包（Packages）

## 白话
安装/管理额外能力包，与 Skills、MCP 并列。

## 对话可以说
- 「装了哪些扩展包」
- 「怎么装社区包」

## 注意
只装可信来源；装完无变化则重启后端或刷新会话。
""",
    },
    "security": {
        "title": "安全与确认",
        "body": """# 安全

1. API Key、Base URL 修改必须确认。  
2. 远程 agent root 不要指系统盘根目录。  
3. 危险 shell 有黑名单，仍不可盲目信任。  
4. 邮件/支付/删库类操作必须人工点头。  
5. 工具页可禁用 command / file_write。  
6. 双实例会导致配置改了不生效——只留一个。
""",
    },
    "dialog_cheatsheet": {
        "title": "对话速查表（复制即用）",
        "body": """# 对话速查表

## 状态与模型
- 系统现在什么状态？
- 当前模型和 temperature 是多少？
- 把 temperature 设为 0.2

## 知识库 / 设备
- 知识库开了吗？根据知识库回答…
- 列出所有设备 / @remote-pc hostname

## 自动化 / 进化
- 列出定时任务
- 有哪些子代理
- 能不能根据任务经验自动生成 skill
- 现在有哪些任务在跑
""",
    },
    "checklist": {
        "title": "开箱配置清单（按顺序）",
        "body": """# 开箱清单

1. 能对话（模型 + Key/本地网关）  
2. （可选）Embedding + Qdrant + rag_enabled  
3. 上传 1 篇知识库文档试搜  
4. （可选）配对一台远程设备  
5. （可选）配一个通道  
6. （可选）打开自主进化  
7. 熟悉：任务 / 工作流 / 子代理 / 定时  

每步卡住就问：「第 N 步怎么做」
""",
    },
    "charts_media": {
        "title": "图表渲染 · 文生图 · PPT/报告",
        "body": """# 图表渲染 · 文生图 · 文档输出

## 对话可以说
- 「用表格列出系统状态」
- 「画一个 mermaid 流程图」
- 「生成一张产品封面图」
- 「做一份 PPT / 写一份正式报告」
- 「capability_status 看看媒体通道」

## 工具速查
| 工具 | 作用 | 依赖 |
|------|------|------|
| render_chart action=table_md | CSV → Markdown 表（中文完整） | 无 |
| render_chart action=mermaid | 保存 .mmd，可选 PNG | `npm i -g @mermaid-js/mermaid-cli`（mmdc） |
| image_generate | 文生图 | 环境变量 **FAL_KEY** 或 **OPENAI_API_KEY** |
| generate_ppt | 幻灯片 **.pptx** | `pip install python-pptx` |
| generate_report | 正式报告 **.docx**（兼 .md） | `pip install python-docx` |
| tts | 语音 | `pip install edge-tts` |
| doc_read / doc_write | 读 PDF/DOCX/XLSX、写文档 | pymupdf / python-docx / openpyxl |

## 文生图配置（必读）
未配置 Key 时只会生成**占位图**，并提示你配置：
1. 在系统环境或 Takton `.env` 写入 `FAL_KEY=...`（或 `FAL_API_KEY`）
2. 或写入 `OPENAI_API_KEY=...` 使用 DALL·E
3. **重启后端**后再调 image_generate

## Mermaid PNG
仅有 .mmd、没有 PNG：本机缺少 mmdc。
```
npm i -g @mermaid-js/mermaid-cli
mmdc -V
```
然后重试 render_chart。

## PPT / 报告预期
- generate_ppt → 真实 **.pptx**（无 python-pptx 才降级 md，并会提示安装）
- generate_report → 优先 **.docx**，同时保留 md 副本
""",
    },
}


SETTINGS_CATALOG: list[dict[str, str]] = [
    {"key": "llm_provider", "risk": "medium", "desc": "LLM 服务商"},
    {"key": "llm_model", "risk": "medium", "desc": "对话模型名"},
    {"key": "llm_base_url", "risk": "high", "desc": "LLM Base URL"},
    {"key": "llm_api_key", "risk": "high", "desc": "LLM API Key"},
    {"key": "temperature", "risk": "low", "desc": "采样温度 0-2"},
    {"key": "max_tokens", "risk": "low", "desc": "最大生成 token"},
    {"key": "context_window", "risk": "medium", "desc": "上下文窗口"},
    {"key": "embedding_provider", "risk": "medium", "desc": "Embedding 服务商"},
    {"key": "embedding_model", "risk": "medium", "desc": "Embedding 模型"},
    {"key": "embedding_base_url", "risk": "high", "desc": "Embedding URL"},
    {"key": "embedding_api_key", "risk": "high", "desc": "Embedding Key"},
    {"key": "rag_enabled", "risk": "low", "desc": "是否允许向量 RAG"},
    {"key": "qdrant_url", "risk": "high", "desc": "Qdrant 地址"},
    {"key": "qdrant_collection", "risk": "medium", "desc": "Qdrant collection"},
    {"key": "context_threshold_percent", "risk": "low", "desc": "压缩触发比例"},
    {"key": "context_compress_model", "risk": "medium", "desc": "压缩用模型"},
    {"key": "system_name", "risk": "low", "desc": "系统显示名"},
]

TOPIC_ALIASES: dict[str, str] = {
    "llm": "models",
    "模型": "models",
    "配置模型": "models",
    "rag": "embedding_rag",
    "embedding": "embedding_rag",
    "知识库": "embedding_rag",
    "知识": "embedding_rag",
    "device": "devices",
    "设备": "devices",
    "远程": "devices",
    "agent": "devices",
    "通道": "channels",
    "qq": "channels",
    "channel": "channels",
    "tool": "tools_skills",
    "skill": "tools_skills",
    "工具": "tools_skills",
    "技能": "tools_skills",
    "evolution": "evolution",
    "自主进化": "evolution",
    "tee": "evolution",
    "haee": "evolution",
    "自动skill": "evolution",
    "subagent": "sub_agents",
    "sub_agents": "sub_agents",
    "子代理": "sub_agents",
    "委派": "sub_agents",
    "task": "tasks",
    "tasks": "tasks",
    "任务": "tasks",
    "session": "sessions_chat",
    "sessions": "sessions_chat",
    "会话": "sessions_chat",
    "对话": "sessions_chat",
    "chat": "sessions_chat",
    "notification": "notifications_webhooks",
    "notifications": "notifications_webhooks",
    "通知": "notifications_webhooks",
    "webhook": "notifications_webhooks",
    "webhooks": "notifications_webhooks",
    "package": "packages",
    "packages": "packages",
    "扩展包": "packages",
    "cron": "cron",
    "定时": "cron",
    "workflow": "workflows",
    "工作流": "workflows",
    "mcp": "mcp_profiles",
    "profile": "mcp_profiles",
    "上下文": "context",
    "压缩": "context",
    "context": "context",
    "wiki": "wiki",
    "安全": "security",
    "security": "security",
    "速查": "dialog_cheatsheet",
        "cheatsheet": "dialog_cheatsheet",
        "清单": "checklist",
        "checklist": "checklist",
        "开箱": "checklist",
        "图表": "charts_media",
        "图表渲染": "charts_media",
        "render": "charts_media",
        "render_chart": "charts_media",
        "mermaid": "charts_media",
        "文生图": "charts_media",
        "image_generate": "charts_media",
        "图片生成": "charts_media",
        "ppt": "charts_media",
        "PPT": "charts_media",
        "报告": "charts_media",
        "docx": "charts_media",
        "tts": "charts_media",
        "语音": "charts_media",
    }


def resolve_topic(topic: str | None) -> str:
    if not topic:
        return "overview"
    t = topic.strip().lower()
    if t in PRODUCT_HANDBOOK:
        return t
    return TOPIC_ALIASES.get(t) or TOPIC_ALIASES.get(topic.strip()) or "overview"


def handbook_as_kb_docs() -> list[dict[str, str]]:
    """转为知识库 seed 条目。"""
    docs = []
    for key, item in PRODUCT_HANDBOOK.items():
        docs.append(
            {
                "title": f"[手册] {item['title']}",
                "content": item["body"] + f"\n\n---\n内部 topic 键: `{key}`\n",
            }
        )
    return docs
