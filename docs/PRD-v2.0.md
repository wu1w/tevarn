# Takton v2.0 — 产品需求文档 (PRD)

> 版本：v2.0
> 日期：2026-07-11
> 对标：OpenCode v1.17.4 / Codex CLI v0.140.0 / Claude Code (2026.7) / Hermes Agent v0.15.2

---

## 1. 产品定位

**Takton** 是一个面向个人开发者的桌面 Agent 终端，提供：
- 本地优先的 AI 对话与任务执行
- 知识图谱 + RAG 记忆系统
- 多 Agent 编排与工具集成
- 桌面原生体验（Electron）

**对标差距：** 当前功能完备性约 60%，缺失多Agent编排、自动记忆、MCP协议、沙箱执行等核心能力。

---

## 2. 用户故事

| ID | 用户故事 | 优先级 | 对标 |
|----|---------|--------|------|
| US-01 | 作为开发者，我希望 Agent 能自动记住我的工作上下文，不用每次重复说明 | P0 | Hermes |
| US-02 | 作为开发者，我希望同时运行多个子 Agent 处理不同任务 | P0 | Claude Code |
| US-03 | 作为运维，我希望 API 有速率限制，防止暴力破解和 DDoS | P0 | — |
| US-04 | 作为用户，我希望前端有错误边界，崩溃时不白屏 | P0 | — |
| US-05 | 作为开发者，我希望 Agent 能通过 MCP 协议调用外部工具 | P1 | Claude Code |
| US-06 | 作为安全管理员，我希望代码执行在沙箱中隔离运行 | P1 | Codex |
| US-07 | 作为高级用户，我希望设定目标让 Agent 自主分解执行 | P1 | Codex |
| US-08 | 作为用户，我希望 Agent 能自动压缩长对话上下文 | P1 | Claude Code |
| US-09 | 作为开发者，我希望有 LSP 集成进行代码智能补全 | P2 | OpenCode |
| US-10 | 作为用户，我希望通过飞书/企微/Telegram 与 Agent 交互 | P2 | Hermes |

---

## 3. 功能需求

### Phase 1：安全与稳定性（Week 1）

| # | 功能 | 描述 | 验收标准 | 工作量 |
|---|------|------|---------|--------|
| F-01 | **Rate Limiter 实现** | 将 `rate_limit.py` 的空壳 `check_rate_limit` 实现为基于滑动窗口的限流 | 登录 5次/分钟、Agent 30次/分钟、其他 60次/分钟，超限返回 429 + Retry-After | 2h |
| F-02 | **React ErrorBoundary** | 在 AppShell 外层添加 ErrorBoundary，捕获渲染异常并显示友好错误页 | 任意组件崩溃 → 显示"出错了"页面 + 重试按钮，不白屏 | 1h |
| F-03 | **Next.js error.tsx** | 为每个路由组添加 error.tsx 和 not-found.tsx | 404/500 有专属错误页面，显示错误详情和返回按钮 | 1h |
| F-04 | **前端输入验证** | 引入 zod，所有表单提交前做 schema 校验 | 注册/登录/设置表单有即时校验，错误信息明确 | 2h |
| F-05 | **audit.py 错误处理** | 补全 audit.py 的 try/except/HTTPException | 审计接口异常时返回结构化错误而非 500 | 0.5h |
| F-06 | **health.py 错误处理** | 补全 health.py 的异常处理 | 健康检查异常时返回 503 而非崩溃 | 0.5h |

### Phase 2：Agent 能力（Week 2）

| # | 功能 | 描述 | 验收标准 | 工作量 |
|---|------|------|---------|--------|
| F-07 | **多Agent编排** | 实现 `agent_call_skill.py` 的桩代码为真实子 Agent 调度 | 主 Agent 可派发任务给 Coder/Researcher/Writer 子 Agent，回收结果 | 8h |
| F-08 | **自动记忆系统** | 对话摘要 → 持久化 → 自动注入上下文的闭环 | 新对话自动加载相关历史摘要，无需手动搜索 | 6h |
| F-09 | **MCP 协议支持** | 实现 MCP 客户端，发现并调用 MCP 服务器提供的工具 | 支持 `mcp.json` 配置，动态加载外部工具 | 4h |
| F-10 | **沙箱执行** | 用 Docker/Firejail 隔离代码执行 | BashSkill/DynamicSkill 在沙箱中运行，无法访问宿主机文件系统 | 6h |
| F-11 | **上下文自动压缩** | 基于 token 数量的精确截断，集成 tiktoken | 对话超限时自动压缩历史，保留 system prompt + 最新消息 | 3h |

### Phase 3：体验与生态（Week 3）

| # | 功能 | 描述 | 验收标准 | 工作量 |
|---|------|------|---------|--------|
| F-12 | **Goals 驱动模式** | 用户设定目标 → Agent 自主分解 → 多步执行 → 浏览器验证 | 输入"帮我搭建一个博客" → Agent 自动创建项目/写代码/启动服务 | 8h |
| F-13 | **技能自动创建** | 成功的复杂操作自动封装为可复用 Skill | 执行 3 次以上的工作流模式自动建议保存为 Skill | 4h |
| F-14 | **LSP 集成** | 集成 Language Server Protocol | 代码编辑时有补全/跳转/诊断 | 6h |
| F-15 | **消息平台集成** | 飞书/企微/Telegram bot | 通过消息平台与 Agent 交互，接收通知 | 8h |
| F-16 | **i18n 多语言** | 国际化框架 | 支持中/英文切换，后续可扩展 | 3h |

---

## 4. 非功能需求

| ID | 需求 | 指标 | 优先级 |
|----|------|------|--------|
| NFR-01 | **安全性** | 无 CRITICAL 级 CVE，沙箱逃逸评分 < 3.0 | P0 |
| NFR-02 | **可用性** | 99.9% uptime，无白屏崩溃 | P0 |
| NFR-03 | **性能** | API P99 < 500ms（不含 LLM 调用），WebSocket 延迟 < 200ms | P1 |
| NFR-04 | **可扩展性** | 支持 3+ 子 Agent 并发执行 | P1 |
| NFR-05 | **可维护性** | 核心模块单元测试覆盖率 > 70% | P1 |
| NFR-06 | **兼容性** | 支持 Linux/macOS/Windows 三端 | P1 |
| NFR-07 | **离线能力** | 断网时本地缓存可用，重连后自动同步 | P2 |

---

## 5. 技术架构变更

```
当前架构：
┌─────────────────────────────────────────┐
│  Frontend (Next.js + Electron)          │
│  ├─ Sidebar / Chat / Settings / ...     │
│  └─ WebSocket ↔ REST API               │
├─────────────────────────────────────────┤
│  Backend (FastAPI + SQLite)             │
│  ├─ 22 Route Modules                    │
│  ├─ Agent Loop (单Agent)                │
│  ├─ Skills (自建CRUD)                   │
│  └─ Context (手动管理)                  │
└─────────────────────────────────────────┘

目标架构：
┌─────────────────────────────────────────┐
│  Frontend (Next.js + Electron)          │
│  ├─ ErrorBoundary + Zod Validation      │
│  ├─ Goals UI / 子Agent面板              │
│  └─ WebSocket ↔ REST API               │
├─────────────────────────────────────────┤
│  Backend (FastAPI + SQLite/PostgreSQL)  │
│  ├─ Rate Limiter (slowapi)              │
│  ├─ Agent Loop → 多Agent Orchestrator   │
│  ├─ MCP Client → 外部工具               │
│  ├─ Auto Memory (摘要+注入)             │
│  ├─ Sandbox (Docker/Firejail)           │
│  └─ Context Auto-Compress (tiktoken)    │
├─────────────────────────────────────────┤
│  Platform Integration Layer              │
│  ├─ Feishu / WeCom / Telegram Bot       │
│  └─ LSP Server                          │
└─────────────────────────────────────────┘
```

---

## 6. 风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| 多Agent编排导致无限递归 | 中 | 高 | 保留现有 `_call_chain` + `MAX_CALL_DEPTH=3` 安全骨架 |
| 沙箱逃逸 | 低 | 极高 | 使用 Docker 容器 + seccomp + 只读文件系统 + 网络隔离 |
| MCP 工具引入恶意代码 | 低 | 高 | MCP 工具注册需审批，运行时沙箱隔离 |
| 自动记忆泄露敏感信息 | 中 | 中 | 记忆存储加密，支持手动删除/编辑 |
| 前端 ErrorBoundary 遗漏 | 低 | 中 | 全局 + 路由级双重 ErrorBoundary |

---

## 7. 发布标准

### Phase 1 完成标准
- [ ] Rate Limiter 通过压力测试（100 req/s 持续 30s，无穿透）
- [ ] ErrorBoundary 覆盖所有路由
- [ ] 所有表单有 zod 校验
- [ ] audit.py/health.py 错误处理补全
- [ ] E2E 测试 41/41 全绿

### Phase 2 完成标准
- [ ] 子 Agent 调度成功（Coder/Researcher/Writer 三个子 Agent）
- [ ] 自动记忆在新对话中正确注入
- [ ] MCP 工具发现 + 调用成功
- [ ] 沙箱内 `rm -rf /` 不影响宿主机
- [ ] 上下文压缩后 token 数不超过模型限制

### Phase 3 完成标准
- [ ] Goals 模式：输入"搭建博客" → 自动完成
- [ ] 技能自动创建：重复 3 次的操作生成 Skill
- [ ] LSP 补全/跳转/诊断可用
- [ ] 至少一个消息平台集成可用
- [ ] 中英文切换正常