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
| 工作区 | 对话 / 任务 / 设备 / 工作流 | 日常干活 |
| Agent | 配置·模型 / 工具 / 技能 / MCP / 配置文件 | 能力与模型 |
| 记忆 | 上下文 / 定时 / 知识 / Wiki | 记忆与自动化 |
| 系统 | 通道 / 设置 | 对外连接与偏好 |

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

## 对话改低风险示例
- 「把 temperature 设为 0.2」→ 工具 `update_config`
- 「把 max_tokens 设为 8192」

## 注意
改 Key/地址属于高风险：Agent 会先问你确认，你说「确认修改」后再执行。
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

## settings key
| key | 说明 |
|-----|------|
| embedding_provider / embedding_model / embedding_base_url / embedding_api_key | 向量模型 |
| reranker_* | 可选重排 |
| rag_enabled | 总开关 |
| qdrant_url / qdrant_collection | 向量库 |

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

## 架构
- **控制面**：本机/中枢 Takton 后端  
- **边缘**：每台机器跑 `takton-agent`（默认端口 **19876**）

## 目标机启动 agent
```bash
# Linux 示例
cd ~/takton-agent
PYTHONPATH=$PWD .venv/bin/python -m takton_agent \\
  --host 0.0.0.0 --port 19876 \\
  --token 你的密钥 --root ~/projects --name remote-pc
```

## 本机配对
1. 打开 **设备** 页。  
2. 填 name / host / port / token → **配对**。  
3. 看到 online + 延迟即成功。  
4. 主 CTA：**用此设备对话**（预填 `@name `）。

## 对话执行
- `@remote-pc hostname`
- `@win-local dir`
- `list:.` / `read:路径`（agent 协议支持）

## API（高级）
- `POST /api/devices/pair`
- `POST /api/devices/{id}/remote/exec`
- `GET /api/devices/discover`（mDNS，Windows 可能不稳）

## Skill
- `list_devices`：列设备；可 ping。
""",
    },
    "channels": {
        "title": "通道配置（QQ / 企微等）",
        "body": """# 通道（社交入口）

## 对话可以说
- 「QQ 通道怎么接」
- 「为什么通道很久不回」
- 「通道会推送思考过程吗」

## 页面
侧栏 **系统 → 通道**

## 原则
- 通道消息走同一套 Agent Loop。  
- 进度策略：**只推真实思考**，不推工具明细，不硬编码「收到正在处理」。  
- 工具轮若无 reasoning/content，中途可能安静（设计如此）。

## 配置要点
1. 在通道页启用对应平台、填 Bot 凭证（以页面字段为准）。  
2. 确认后端进程唯一（避免 8000/8001 双实例）。  
3. 模型需能返回内容或 reasoning，否则通道侧几乎静音。

## 排查
- 本机 API health 是否 200。  
- 是否有两个 Takton/uvicorn。  
- 看后端日志是否收到通道事件。
""",
    },
    "tools_skills": {
        "title": "工具 Tools 与技能 Skills",
        "body": """# 工具与技能

## 白话
- **Tools**：底层手（读文件、命令、浏览器…）  
- **Skills**：打包好的动作（天气、知识库、PPT…）

## 对话可以说
- 「有哪些技能」
- 「关掉危险的 command」
- 「天气 skill 怎么用」

## 页面
- **Agent → 工具**：分类筛选（文件/执行/网络/数据）  
- **Agent → 技能**：内置/自定义/社区 + 分类

## 内置 Skills（节选）
| 名称 | 用途 |
|------|------|
| beginner_help | 新手说明 |
| configure_takton | **对话配置本产品（本手册）** |
| current_time | 时间 |
| weather | 天气 |
| fetch_webpage | 抓网页 |
| web_search | 联网搜 |
| search_knowledge_base | 知识库 |
| list_devices | 设备列表 |
| calendar_read / send_email | 日历/邮件 |
| generate_ppt / generate_report | 文档生成 |

## 自定义
- 工具：HTTP 类型自定义 API。  
- 技能：自定义 handler http/python，或社区导入。
""",
    },
    "cron": {
        "title": "定时任务 Cron",
        "body": """# 定时任务

## 对话可以说
- 「每天早上 9 点提醒我看邮件」
- 「列出定时任务」
- 「关掉某个定时任务」

## 页面
**记忆 → 定时**

## 工具
`manage_cron`：create / list / update / delete / toggle  
schedule 可用 cron 表达式或「每天9点」类描述（以实现为准）。

## 注意
- 定时任务在独立上下文跑，prompt 要自包含。  
- 通道投递目标以产品配置为准。
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
2. 用自然语言/画布编辑节点。  
3. 校验 → 保存 → 运行。  

Agent 侧有 `generate_workflow` / `validate_dag` 等工具时可对话生成草稿。
""",
    },
    "mcp_profiles": {
        "title": "MCP 与 Agent 配置文件 Profiles",
        "body": """# MCP · Profiles

## MCP
- 页面：**Agent → MCP**  
- 用于挂第三方 MCP Server（扩展工具面）。  
- 对话：「有哪些 MCP」「怎么加某个 MCP」→ 引导到该页填 command/url。

## Profiles（人格/配置文件）
- 页面：**Agent → 配置文件**  
- 切换不同系统提示/工具偏好（若已启用）。  
- 对话：「切换到运维配置文件」——若后端支持 profile API，由 Agent 调用；否则指导你点页面。
""",
    },
    "context": {
        "title": "上下文压缩与长对话",
        "body": """# 上下文 / 压缩

## 对话可以说
- 「长对话老是丢上下文怎么办」
- 「压缩阈值调高一点」

## settings key
| key | 含义 |
|-----|------|
| context_window | 模型窗口 |
| context_threshold_percent | 触发压缩比例（默认约 0.72） |
| context_protect_first_n / last_n | 保护头尾消息 |
| context_max_tool_output_chars | 工具输出截断 |
| context_enable_l1 / l3 / l5 | 分层压缩开关 |
| context_compress_model | L5 专用模型，空=主模型 |

## 页面
**记忆 → 上下文** 与 **设置** 中压缩模型下拉。
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
图谱 + 实体/关系。Skill：`wiki_search`。

适合结构化长期记忆；与知识库（文档向量）互补。
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
6. 双实例（两个 Takton）会导致配置改了不生效——只留一个。
""",
    },
    "dialog_cheatsheet": {
        "title": "对话速查表（复制即用）",
        "body": """# 对话速查表

## 状态与模型
- 系统现在什么状态？
- 当前模型和 temperature 是多少？
- 把 temperature 设为 0.2
- 把 max_tokens 设为 8192

## 知识库
- 知识库开了吗？为什么搜不到？
- 教我配置 Embedding 和 Qdrant
- 根据知识库说明请假流程

## 设备
- 列出所有设备
- 怎么配对 remote-pc？
- @remote-pc hostname

## 日常
- 现在几点 / 北京天气
- 我不会用 Takton，从头教
- 这个链接讲什么：https://...

## 自动化
- 列出定时任务
- 每天 9 点提醒我看待办

## 产品手册
- 用 configure_takton 讲设备怎么配
- 打开 Takton 配置清单
""",
    },
    "checklist": {
        "title": "开箱配置清单（按顺序）",
        "body": """# 开箱清单

1. **对话模型**可用（能正常回一句话）  
2. （可选）**Embedding + Qdrant** → 开 RAG  
3. 上传 1 份测试文档到**知识**并检索  
4. （可选）本机或另一台电脑跑 **takton-agent** 并配对  
5. 试 `@设备 echo ok`  
6. （可选）接**通道**，确认只推思考不刷工具  
7. （可选）建一个**定时任务**  
8. 浏览**工具/技能**开关，关掉不需要的  

全部可用对话推进：先说「按开箱清单一步步带我配」。
""",
    },
}

# 配置项速查（skill list_keys）
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
    "all": "overview",
    "home": "overview",
    "总览": "overview",
    "model": "models",
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
