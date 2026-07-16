# 竞品预置能力 vs Takton 补全（小白向）

调研日期：2026-07-16

## 竞品一般预置什么

| 产品 | 核心预置 Tools | 预置「Skill/能力」叙事 | 知识/记忆 |
|------|----------------|------------------------|----------|
| **ChatGPT Agent** | Visual browser、文本浏览器、Terminal、Code interpreter、Apps 连接（邮箱/日历等） | Agent Mode：研究+预订+幻灯片；工具箱下拉启用 | 连接云端服务与上传文件 |
| **Claude Code** | Read/Edit/Write、Glob/Grep、Bash、WebFetch/WebSearch、Task 子代理 | 以 coding 工具为主，几乎不讲「生活 skill」 | 仓库上下文 |
| **Cursor Agent** | 读改代码、终端、Web、Browser 控制 | 工程向；Browser 测 UI | 代码库索引 |
| **Hermes / 个人 agent** | 终端、文件、web_search、browser、多通道 | 通道+技能包可扩展 | memory / skills 目录 |
| **社区（Composio 等）** | Gmail、Slack、Notion、Calendar、GitHub… | 集成型 skill 市场 | 外连 SaaS |

共性：
1. **文件 + 终端 + 网页** 是标配（工程向）。
2. **个人助理向** 会加：搜索、日历、邮件、浏览器操作、代码解释器。
3. **小白产品** 更强调：时间、天气、总结链接、人话帮助、安全说明，而不是暴露 grep/glob。

## Takton 已有（补全前）

**Tools（工程底座，已齐）**  
browser / command / file_read / file_write / edit / glob / grep / http / python / search / sqlite_query + 系统配置/工作流工具

**Skills**  
web_search、http_get、bash、rag、wiki、calendar_read、send_email、ppt、report、goal、agent_call

**缺口（相对小白 + 竞品助理向）**  
- 时间 / 天气 / 看链接（零配置）  
- 「我不会用」自助帮助  
- 设备列表（多机差异点）  
- 开箱知识库新手文

## 本次已补全

### Skills（已 seed 到运行实例，共 16 个）
- `current_time` — 现在几点  
- `weather` — Open-Meteo 免 Key  
- `fetch_webpage` — 抓公开网页文本  
- `list_devices` — 已配对设备  
- `beginner_help` — 上手/安全/设备/示例  

### 知识库种子（admin 用户，source=builtin-seed）
1. Takton 新手 5 分钟上手  
2. 安全须知（必读）  
3. 远程设备与 @ 用法  
4. 工具与 Skill 对照（白话）  
5. 常用说法模板  

## 建议下一期（社区高星 / 集成）

| 优先级 | 能力 | 来源灵感 | 备注 |
|--------|------|----------|------|
| P1 | 日历写事件 + 提醒 | ChatGPT Apps / calendar_read 扩展 | 需账号连接 |
| P1 | 邮件读摘要（只读） | Gmail skill / send_email 对称 | OAuth |
| P2 | Notion/飞书文档读 | Composio 类 | MCP 更合适 |
| P2 | 图片理解 / OCR | Cursor/ChatGPT | 已有图生成，缺读图 skill |
| P2 | 社区 Skill 导入优化 | skills 页 community tab | 已有入口，可预置几个 URL |
| P3 | Browser 点击填表 | ChatGPT Operator / Cursor Browser | 重量级 |

**不建议** 为小白堆更多 coding-only 工具（grep/glob 已够）；应 **默认折叠高级工具**，首页示例引导天气/帮助/@设备。
