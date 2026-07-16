# Takton v2.0 — 完整工作计划

> 日期：2026-07-11
> 总工期：3 周（15 个工作日）
> 策略：先修安全/稳定性 → 再建 Agent 能力 → 最后做生态集成

---

## Week 1：安全与稳定性（Day 1-5）

### Day 1（周一）：Rate Limiter + ErrorBoundary

| 时间 | 任务 | 产出 | 验收 |
|------|------|------|------|
| 09:00-11:00 | **F-01: Rate Limiter 实现** | 替换 `rate_limit.py` 空壳为 slowapi 实现 | `curl -X POST /auth/login` 超过 5次/分钟 → 429 |
| 11:00-12:00 | 安装依赖 `pip install slowapi redis` | `requirements.txt` 更新 | — |
| 14:00-15:30 | **F-02: React ErrorBoundary** | 创建 `components/ErrorBoundary.tsx`，包裹 AppShell | 组件崩溃 → 友好错误页 |
| 15:30-17:00 | **F-03: Next.js error.tsx** | 为 `(auth)/`、`(dashboard)/` 添加 error.tsx + not-found.tsx | 404/500 有专属页面 |
| 17:00-18:00 | 运行 E2E 测试确认无回归 | `full_stack_e2e.py` 全绿 | 41/41 ✅ |

**关键文件：**
- `backend/core/rate_limit.py` — 重写
- `backend/main.py` — 挂载 RateLimiter middleware
- `frontend/components/ErrorBoundary.tsx` — 新建
- `frontend/components/layout/AppShell.tsx` — 包裹 ErrorBoundary
- `frontend/app/(auth)/error.tsx` — 新建
- `frontend/app/(dashboard)/error.tsx` — 新建
- `frontend/app/(dashboard)/not-found.tsx` — 新建

### Day 2（周二）：输入验证 + 错误处理

| 时间 | 任务 | 产出 | 验收 |
|------|------|------|------|
| 09:00-11:00 | **F-04: 前端 zod 校验** | 创建 `lib/validations/` 目录，定义 auth/settings/session schema | 表单提交前校验，错误信息明确 |
| 11:00-12:00 | 在注册/登录表单集成 zod | `components/auth/` 表单校验 | 无效邮箱 → 即时提示 |
| 14:00-15:00 | **F-05: audit.py 错误处理** | 补全 try/except/HTTPException | 审计接口异常 → 结构化错误 |
| 15:00-16:00 | **F-06: health.py 错误处理** | 补全异常处理 | DB 异常 → 503 |
| 16:00-18:00 | 补全其他路由的错误处理（git.py, devices.py） | 所有路由有统一错误响应格式 | — |

**关键文件：**
- `frontend/lib/validations/auth.ts` — 新建
- `frontend/lib/validations/settings.ts` — 新建
- `frontend/lib/validations/session.ts` — 新建
- `backend/api/routes/audit.py` — 修改
- `backend/api/routes/health.py` — 修改

### Day 3（周三）：加密安全 + WebSocket 安全

| 时间 | 任务 | 产出 | 验收 |
|------|------|------|------|
| 09:00-11:30 | **HIGH-4: 加密密钥管理** | 替换手动 AES 为 `cryptography.fernet.Fernet`，随机 IV | 旧数据迁移成功，新加密不可预测 |
| 11:30-12:00 | 数据迁移脚本 | `scripts/migrate_encryption.py` | 旧密文可解密再加密 |
| 14:00-16:00 | **HIGH-6: WebSocket 身份验证** | WS 连接时验证 JWT，绑定 user_id + session_id | 伪造 session_id → 连接拒绝 |
| 16:00-18:00 | **HIGH-3: 提示词注入防护** | 用户输入清理 + 结构化提示词 | 注入攻击 → 被过滤/转义 |

**关键文件：**
- `backend/core/encryption.py` — 重写
- `backend/api/websocket.py` — 修改
- `backend/skills/builtins/generate_ppt_skill.py` — 修改
- `backend/skills/builtins/generate_report_skill.py` — 修改
- `scripts/migrate_encryption.py` — 新建

### Day 4（周四）：数据库连接池 + Agent Loop 事务

| 时间 | 任务 | 产出 | 验收 |
|------|------|------|------|
| 09:00-12:00 | **HIGH-1: 数据库连接池泄漏** | 统一使用 `get_db_context`，增加 session.close() 异常保护 | 高频操作无连接泄漏 |
| 14:00-17:00 | **HIGH-2: Agent Loop 事务原子性** | 将 Loop 执行包装在单一事务中，异常时 status 正确重置 | 中途异常 → session.status=idle |
| 17:00-18:00 | 压力测试 | 100 并发 Agent Loop 无死锁 | — |

**关键文件：**
- `backend/repositories/base.py` — 修改
- `backend/agent/loop.py` — 修改
- `backend/database.py` — 可能修改

### Day 5（周五）：集成测试 + 回归验证

| 时间 | 任务 | 产出 | 验收 |
|------|------|------|------|
| 09:00-12:00 | 编写安全相关单元测试 | `tests/test_rate_limit.py`、`tests/test_encryption.py`、`tests/test_ws_auth.py` | 覆盖率 > 80% |
| 14:00-16:00 | 运行全量 E2E 测试 + 修复 | `full_stack_e2e.py` 全绿 | 41/41 ✅ |
| 16:00-18:00 | Phase 1 验收 + 文档更新 | `docs/CHANGELOG-v2.0-phase1.md` | — |

---

## Week 2：Agent 能力（Day 6-10）

### Day 6（周一）：多Agent编排 — 设计与框架

| 时间 | 任务 | 产出 | 验收 |
|------|------|------|------|
| 09:00-12:00 | 设计多Agent编排架构 | `docs/architecture/multi-agent.md` | 包含 Agent 通信协议、任务分发、结果回收 |
| 14:00-18:00 | 实现 AgentOrchestrator 基类 | `backend/agent/orchestrator.py` | 支持注册子 Agent、派发任务、回收结果 |

**架构设计：**
```
NexusAgentLoop (主 Agent)
  ├─ AgentOrchestrator
  │   ├─ CoderAgent (代码编写)
  │   ├─ ResearcherAgent (信息检索)
  │   └─ WriterAgent (文档生成)
  ├─ _call_chain 追踪 (MAX_CALL_DEPTH=3)
  └─ 结果合并 + 上下文传递
```

### Day 7（周二）：多Agent编排 — 实现

| 时间 | 任务 | 产出 | 验收 |
|------|------|------|------|
| 09:00-12:00 | 实现 CoderAgent + ResearcherAgent | `backend/agent/coder.py`、`researcher.py` | 子 Agent 可独立执行任务 |
| 14:00-17:00 | 实现 agent_call_skill 真实调度 | `backend/skills/builtins/agent_call_skill.py` | 主 Agent 可派发任务给子 Agent |
| 17:00-18:00 | 集成测试 | 主 Agent → 调用 CoderAgent → 返回代码 | — |

### Day 8（周三）：自动记忆系统

| 时间 | 任务 | 产出 | 验收 |
|------|------|------|------|
| 09:00-11:00 | 设计记忆架构 | 短期(对话) → 中期(摘要) → 长期(知识) 三层 | — |
| 11:00-12:00 | 实现对话摘要生成 | `backend/agent/memory.py` — `summarize_conversation()` | 长对话 → 压缩为关键要点 |
| 14:00-16:00 | 实现记忆检索与注入 | `memory.py` — `retrieve_relevant()` + `inject_context()` | 新对话自动加载相关记忆 |
| 16:00-18:00 | 前端记忆管理 UI | `components/settings/MemoryPanel.tsx` | 可查看/编辑/删除记忆 |

### Day 9（周四）：MCP 协议 + 上下文压缩

| 时间 | 任务 | 产出 | 验收 |
|------|------|------|------|
| 09:00-12:00 | **F-09: MCP 客户端** | `backend/mcp/client.py` — 发现 + 调用 MCP 工具 | `mcp.json` 配置 → 动态加载工具 |
| 14:00-16:00 | **F-11: 上下文自动压缩** | 集成 tiktoken，按 token 截断 | 200K token 对话 → 压缩到模型限制内 |
| 16:00-18:00 | 替换硬编码 context_window | 从 LLM 服务获取实际窗口大小 | 不同模型使用不同窗口值 |

### Day 10（周五）：沙箱执行 + 集成测试

| 时间 | 任务 | 产出 | 验收 |
|------|------|------|------|
| 09:00-12:00 | **F-10: 沙箱执行** | `backend/agent/sandbox.py` — Docker 容器隔离 | `rm -rf /` 不影响宿主机 |
| 14:00-16:00 | BashSkill/DynamicSkill 沙箱化 | 修改 Skill 执行入口 | 代码执行在容器中 |
| 16:00-18:00 | Phase 2 集成测试 | 多Agent + 记忆 + MCP + 沙箱 联调 | E2E 全绿 |

---

## Week 3：体验与生态（Day 11-15）

### Day 11（周一）：Goals 驱动模式

| 时间 | 任务 | 产出 | 验收 |
|------|------|------|------|
| 09:00-12:00 | 设计 Goals 架构 | `docs/architecture/goals.md` | 目标分解 → 子任务 → 执行 → 验证 |
| 14:00-18:00 | 实现 GoalDecomposer | `backend/agent/goals.py` | "搭建博客" → 5 个子任务 |

### Day 12（周二）：Goals UI + 技能自动创建

| 时间 | 任务 | 产出 | 验收 |
|------|------|------|------|
| 09:00-12:00 | Goals 前端 UI | `components/goals/GoalPanel.tsx` | 可视化目标分解树 |
| 14:00-16:00 | **F-13: 技能自动创建** | `backend/agent/skill_learner.py` | 重复 3 次操作 → 建议保存为 Skill |
| 16:00-18:00 | 技能建议 UI | Chat 窗口中显示"保存为技能"提示 | — |

### Day 13（周三）：LSP 集成

| 时间 | 任务 | 产出 | 验收 |
|------|------|------|------|
| 09:00-12:00 | LSP 客户端实现 | `backend/lsp/client.py` | 连接 pyls/typescript-language-server |
| 14:00-17:00 | 代码编辑器集成 | Monaco Editor + LSP 补全 | 输入时有补全建议 |
| 17:00-18:00 | 诊断与跳转 | hover 显示类型信息 | — |

### Day 14（周四）：消息平台集成 + i18n

| 时间 | 任务 | 产出 | 验收 |
|------|------|------|------|
| 09:00-12:00 | **F-15: 飞书 Bot** | `backend/integrations/feishu_bot.py` | 飞书消息 → Agent 处理 → 回复 |
| 14:00-16:00 | **F-15: 企微 Bot** | `backend/integrations/wecom_bot.py` | 企微消息 → Agent 处理 → 回复 |
| 16:00-18:00 | **F-16: i18n 框架** | `next-intl` 集成，中/英文字典 | 切换语言 → UI 全部翻译 |

### Day 15（周五）：全量验收 + 发布

| 时间 | 任务 | 产出 | 验收 |
|------|------|------|------|
| 09:00-12:00 | 全量 E2E 测试 | `full_stack_e2e.py` + 新增 Agent/Memory/MCP 测试 | 全绿 |
| 14:00-16:00 | 性能测试 | API P99 < 500ms，WS 延迟 < 200ms | — |
| 16:00-17:00 | 安全扫描 | `bandit` + `npm audit` | 无 CRITICAL |
| 17:00-18:00 | 发布 v2.0 | Git tag + CHANGELOG | — |

---

## 依赖关系图

```
Phase 1 (安全/稳定)
  F-01 Rate Limiter ─────┐
  F-02 ErrorBoundary ────┤
  F-03 error.tsx ─────────┤──→ Phase 1 验收
  F-04 Zod 校验 ──────────┤
  F-05/06 错误处理 ──────┘
       │
       ▼
Phase 2 (Agent 能力)
  F-07 多Agent编排 ──────┐
  F-08 自动记忆 ──────────┤
  F-09 MCP 协议 ──────────┤──→ Phase 2 验收
  F-10 沙箱执行 ──────────┤
  F-11 上下文压缩 ────────┘
       │
       ▼
Phase 3 (体验/生态)
  F-12 Goals 模式 ───────┐
  F-13 技能自动创建 ──────┤
  F-14 LSP 集成 ──────────┤──→ Phase 3 验收 = v2.0 发布
  F-15 消息平台 ──────────┤
  F-16 i18n ──────────────┘
```

---

## 资源估算

| Phase | 工时 | 关键风险 |
|-------|------|---------|
| Phase 1 | ~40h | 加密迁移需数据备份 |
| Phase 2 | ~55h | 多Agent编排复杂度高 |
| Phase 3 | ~60h | LSP 集成跨平台兼容性 |
| **总计** | **~155h** | — |

---

## 验收检查清单

### Phase 1
- [ ] Rate Limiter: 100 req/s × 30s 无穿透
- [ ] ErrorBoundary: 任意组件崩溃不白屏
- [ ] Zod: 所有表单有校验
- [ ] 加密: Fernet 替换完成，旧数据迁移成功
- [ ] WS Auth: 伪造 session_id 被拒绝
- [ ] DB Pool: 高频操作无连接泄漏
- [ ] Agent Loop: 中途异常 → status=idle
- [ ] E2E: 41/41 ✅

### Phase 2
- [ ] 多Agent: 主→Coder→返回代码
- [ ] 记忆: 新对话自动注入相关摘要
- [ ] MCP: 外部工具发现+调用
- [ ] 沙箱: `rm -rf /` 不影响宿主机
- [ ] 压缩: token 数不超模型限制

### Phase 3
- [ ] Goals: "搭建博客" → 自动完成
- [ ] 技能: 重复3次 → 建议保存
- [ ] LSP: 补全/跳转/诊断
- [ ] 飞书/企微: 消息→Agent→回复
- [ ] i18n: 中英文切换