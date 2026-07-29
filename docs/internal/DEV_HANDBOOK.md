# Takton Alpha 开发手册

> 面向在本仓库改代码的人。  
> 架构：`ARCHITECTURE.md` · 拓扑：`TOPOLOGY.md` · OS 路线：`ROADMAP_AIOS_OS_FULL.md`

---

## 1. 5 分钟心智模型

1. 你在做的是 **Personal Agent OS（数字班子）**，不是又一个 Chat 皮肤。  
2. 用户只应感知三个词：**员工 · 工单 · 审批**（`concepts.md`）。  
3. **Kernel 是主角**；Next/Electron 是控制台；FastAPI 是适配层。  
4. 业务状态 **服务端权威**；前端 store 只放主题/草稿/折叠。  
5. 危险能力 **必须** 走 `kernel.mediate`；能力只能收窄，扩大走审批。  
6. 进化 **禁止** 静默改编制 caps。  
7. 近程优先 **0.6 产品与 Durable**；OS 大拆目录让路（绞杀者迁移）。

---

## 2. 环境与启动

### 2.1 建议环境

| 项 | 建议 |
|----|------|
| OS | Windows / macOS / Linux |
| Python | 3.11–3.12 优先；3.14 需 SQLAlchemy ≥ 2.0.40 |
| Node | 与 `frontend/package.json` 一致 |
| 数据 | 默认 SQLite（见 `STORAGE.md`） |

### 2.2 常用启动（开发）

```bash
# 仓库根
# 后端（Kernel Host + Adapter，现状同进程）
set PYTHONPATH=.
set TAKTON_AIOS_PROFILE=aios-dev
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8090 --reload

# 前端 Console
cd frontend
npm install
npm run dev
# → http://localhost:3000  代理 /api → 8090
```

脚本参考：`scripts/dev-backend.ps1`、`backend/scripts/start_dev.py`、`run-dev-e2e.ps1`。

### 2.3 Headless / Kernel-first（无 UI）

目标语义：只起 Runtime，不打开窗口。

```bash
set PYTHONPATH=.
set TAKTON_AIOS_PROFILE=aios-dev
# 推荐入口（与手册 OS 化一致）
python -m backend.runtime --host 127.0.0.1 --port 8090
# 或
python -m backend.cli start --host 127.0.0.1 --port 8090
# 纯 dispatcher 调试（无 HTTP）
python -m backend.runtime --headless
# Windows 脚本
.\scripts\start-kernel-host.ps1
```

CLI 客户端（对已运行的 Host）：

```bash
python -m backend.cli status
python -m backend.cli jobs --token <JWT>
python -m backend.cli job-stop <inbox_item_id> --token <JWT>
python -m backend.cli approve <escalation_id> --token <JWT>
python -m backend.cli events --token <JWT>
```

模板员工：

```bash
PYTHONPATH=. python -m backend.scripts.seed_template_crew
# 或登录后 POST /api/kernel/workforce/seed-template-crew
```

### 2.4 端口与事件

| 端口/路径 | 用途 |
|-----------|------|
| 8090 | API / WS / Kernel Adapter |
| 3000 | Next dev |
| `GET /api/runtime/status` | 托盘/CLI 心跳（loopback 含 badge） |
| `GET /api/kernel/events/domain` | 领域事件快照 |
| `WS /api/ws/domain?token=` | 领域事件实时流 |

### 2.5 Desktop 退出语义（Electron）

| 操作 | 效果 |
|------|------|
| 关主窗口 | **隐藏**，不退出，不杀 Kernel |
| 托盘「退出控制台（不停止 AI）」 | 退 Electron，**Kernel 常驻** |
| 托盘「停止 AI 运行时并退出」 | 杀 Kernel 子进程并退 |

### 2.6 可代码完成的 DoD 清单（手册目标）

| # | 目标 | 状态 |
|---|------|------|
| 1 | Kernel-first 入口 `python -m backend.runtime` | ✅ |
| 2 | 关窗 ≠ 停 AI；退出控制台 vs 停运行时 | ✅ |
| 3 | 领域事件 + WS + **全局 invalidate**（员工/审批查询） | ✅ |
| 4 | kernel 不依赖 fastapi（dispatcher 已去 dependencies） | ✅ |
| 5 | 托盘 tooltip / badge 计数 | ✅ |
| 6 | CLI status/jobs/stop/approve/**login/follow**/events | ✅ |
| 7 | 协议 0.1 / 治理导出 / 三词心智 | ✅ |
| 8 | 事件 **seq/after_seq/since_ts** 续订 | ✅ |
| 9 | Run 关联 `run_ref`（job/process/session/identity） | ✅ |
| 10 | 记忆主入口 CrewMemoryHub（/memory） | ✅ |
| 11 | 主人 7 天真实使用 / kill-9 手测 | ⏸ **需主人在场** |
| 12 | `adapters/` + `runtime.facade` 占位 | ✅ 门面已建；routes 绞杀者迁入 |
| 13 | Electron 唯一真源 + 端口 8090 | ✅ |
| 14 | 高级页 AdvancedShell 全覆盖 | ✅ |
| 15 | 主人 7 天 / kill-9 | ⏸ 人工 |

---

### 2.7 端口（速查）

| 端口 | 用途 |
|------|------|
| 8090 | API / WS / Kernel Adapter |
| 3000 | Next dev |

---

## 3. 改代码：落到哪一层

### 3.1 决策表

| 你想改… | 优先目录 | 不要 |
|---------|----------|------|
| 进程/能力/预算/审计 | `backend/kernel/` | 在 route 里复制策略 |
| 员工/工单/调度/日报 | `kernel/identity·inbox·dispatcher·workforce` | 前端自造状态机 |
| 对话推理/压缩/工具轮 | `backend/agent/` | 绕过 mediate 调 shell |
| HTTP 形状/鉴权/DTO | `backend/api/routes/` | 在此实现调度核心 |
| 工具实现 | `backend/tools/` | 不校验 caps |
| UI 主路径 | `frontend/app/{page,agents,approvals,chat,kernel}` | 新增大导航概念 |
| 托盘/关窗/拉起后端 | `electron/` | 让关窗默认杀 Kernel（0.7 起禁止） |
| 协议/Card/A2A | `kernel/protocol_spec` · `api/routes/protocol` | 暴露给主人新名词 |
| 治理红线文案/导出 | `kernel/governance.py` | 软文案代替硬 enforce |

### 3.2 依赖铁律

```text
frontend / electron / cli
        →  only HTTP/WS API
api/routes
        →  kernel · agent · services · repos
agent
        →  kernel.mediate · tools · services
kernel
        →  标准库 · models/repos 抽象 · 禁止 fastapi
```

新增 import 时自问：是否破坏单向依赖？

### 3.3 产品文案

| 对用户说 | 不要对用户说 |
|----------|----------------|
| 员工 | SubAgent / Identity UUID |
| 工单 | InboxItem / claim |
| 审批 | Escalation token |
| 新建员工 | Hire wizard（内部可以） |
| 日报 | workforce report（内部可以） |

主路径页可挂 `ProductConceptsBar`；高级页用 `LegacyQuiet`。

---

## 4. 关键 API 速查（开发用）

### 4.1 编制与工单

| 方法 | 路径 | 说明 |
|------|------|------|
| GET/POST | `/api/kernel/identities` | 员工列表/入编 |
| GET/POST | `/api/kernel/inbox` | 工单列表/投递 |
| GET | `/api/kernel/inbox/dead` | 死信 |
| POST | `/api/kernel/inbox/{id}/requeue` | 重放 |
| POST | `/api/kernel/jobs/stop` | **统一停止** |
| GET | `/api/kernel/jobs/running` | 现在在跑 |
| GET | `/api/kernel/workforce/report` | 日报 |
| POST | `/api/kernel/workforce/report/read` | 日报已读 |
| POST | `/api/kernel/workforce/seed-template-crew` | 模板员工 |

### 4.2 审批与内核

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/kernel/escalations` | 提权列表 |
| POST | `/api/kernel/escalations/{id}/approve\|deny` | 批/拒 |
| GET | `/api/kernel/processes` | 进程 |
| GET | `/api/kernel/policy/decisions` | 权限网 |
| POST | `/api/kernel/backup/export` | 备份 |

### 4.3 协议 0.1

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/kernel/protocol/manifest` | 清单 |
| GET | `/api/kernel/protocol/concepts` | 三词机读 |
| GET | `/api/kernel/protocol/governance` | 红线/预设 |
| GET | `/api/kernel/protocol/surface` | 研究面 |
| GET | `/api/kernel/protocol/agent-cards` | Agent Card |
| POST | `/api/kernel/protocol/a2a/tasks` | A2A→工单 |

详情：`PROTOCOL.md`。

---

## 5. 开发约定

### 5.1 工单与停止

- 新异步工作优先 **Inbox + Dispatcher**，禁止临时 SubAgent 闷跑（有编制时）。  
- 停止必须走统一语义：process killed + loop stop + job `cancelled`（见 `dispatcher.cancel_job`）。  
- 失败重试有上限 → `dead`；可 requeue/discard。

### 5.2 权限

- 员工工单：`steward_permission` + Identity caps，**不**向主人刷确认。  
- 主人对话：可危险确认（一次/会话/本员工）。  
- 扩权：escalation → 审批中心。

### 5.3 记忆

- 员工长期人设/职责 → Identity memory。  
- 权威说明：`MEMORY_AUTHORITY.md`。  
- 禁止再开第五套记忆后端（0.6 前）。

### 5.4 事件（方向）

- 新状态变更优先 `kernel._emit` / 领域事件，便于 0.7 WS 广播。  
- kind 命名稳定：`job.*` / `approval.*` / `process.*` / `policy.decision`。

### 5.5 前端

- 数据：`@tanstack/react-query` + `lib/api.ts`。  
- 业务状态不要长期只存在 zustand。  
- 主轨：`/` · `/chat` · `/agents` · `/approvals` · `/kernel`。  
- 高级：Goals/Workflows/Market… 保持降级。

### 5.6 配置

- 业务开关进 `backend/core/config.py`（settings）。  
- 编制相关：`agent_dispatcher_*` · `agent_inbox_*` · `agent_dispatcher_max_global_concurrent`。  
- 开发剖面：`TAKTON_AIOS_PROFILE=aios-dev`。

### 5.7 版本与双轨

- alpha **不**默认 push 公有 GitHub。  
- 版本号：`0.4.6-alpha` 风格，直至产品决定抬升。  
- 可从 main 吸收 bugfix；不反向污染 main 叙事。

---

## 6. 测试

### 6.1 内核与协议（优先）

```bash
cd <repo>
set PYTHONPATH=.
python -m pytest backend/tests/kernel -q --tb=line
# 若 conftest 因环境失败，协议纯测可：
python -m pytest backend/tests/kernel/test_protocol_governance.py -q --noconftest
python -m pytest backend/tests/kernel/test_stop_concurrency_report.py -q --noconftest
```

### 6.2 建议覆盖

| 改动类型 | 至少测 |
|----------|--------|
| inbox/dispatcher | claim 串行、dead/requeue、cancel、并发 cap |
| mediate/caps | 越权拒绝、narrow |
| protocol | envelope 解析、card 字段、governance invariants |
| 前端主路径 | 手测或 e2e `e2e/product-spine-*.ts` |

### 6.3 Python 3.14

SQLAlchemy 过旧会在 model 导入失败 → `pip install "sqlalchemy>=2.0.40"`。

---

## 7. 提交与文档

### 7.1 改完自检清单

- [ ] 依赖方向未破坏  
- [ ] 用户可见文案是否引入第四概念  
- [ ] 是否绕过 mediate  
- [ ] 工单/进程失败路径是否有终态  
- [ ] 相关 `docs/internal` 是否需一行更新  
- [ ] CHANGELOG 是否记一笔（有用户/架构影响时）

### 7.2 文档该改哪

| 变化 | 更新 |
|------|------|
| 新层/新目录职责 | ARCHITECTURE.md |
| 端口/进程/部署 | TOPOLOGY.md |
| 开发步骤/约定 | DEV_HANDBOOK.md（本文） |
| 版本阶段 | ROADMAP_* |
| 用户概念 | concepts.md |
| 外部集成 | PROTOCOL.md |

---

## 8. 反模式（禁止 / 慎用）

| 反模式 | 为何 |
|--------|------|
| 前端判断「能不能跑 shell」 | 双源真相；应展示服务端拒绝原因 |
| 关窗默认 `kill` 后端（0.7+） | 破坏 Persistent Agent |
| route 内实现 claim/调度 | Adapter 变胖，无法 headless 单测 |
| 为 OS 感换 Tauri/Go | 无用户收益；违背路线图 |
| 新增大导航「第六业务」 | 击碎三词心智 |
| auto_apply 进化改 caps | 治理红线 |
| 无限 pending 队列 | 有界 inbox 红线 |
| 工单失败不写状态 | 静默丢失，非 OS |

---

## 9. 常见任务配方

### 9.1 新增一种可派工能力

1. 能力名加入编制与 `protocol_spec.STANDARD_CAPABILITIES`（若公开）。  
2. `grant_store` / cap 映射覆盖工具名。  
3. mediate 路径验证。  
4. 测试：有 cap 放行、无 cap 拒绝。  
5. 不对用户发明新词。

### 9.2 新增 UI 只读观测

1. 优先已有 API（jobs/running、events、protocol）。  
2. 放内核页或审计页，不进 IconRail 主轨。  
3. 数据用 react-query；考虑未来改事件订阅。

### 9.3 外部脚本派活

```http
POST /api/kernel/protocol/a2a/tasks
Authorization: Bearer …
{ "instruction": "…", "identity_name": "研究员" }
```

见 `PROTOCOL.md`。

### 9.4 调试「工单不跑」

1. Dispatcher 是否 enable（aios-dev / settings）。  
2. 员工是否 `active`。  
3. `GET /kernel/jobs/running` · inbox pending/claimed。  
4. 日志：claim 后 fail 原因（会话 user_id、工具表未加载等）。  
5. 死信台是否已 full attempts。

---

## 10. 路线图阶段与你当前任务

| 阶段 | 你该优先 |
|------|----------|
| **0.6** | 主路径稳、Durable、崩溃手测、少堆架构 |
| **0.7** | Kernel-first 启动、关窗语义、事件 MVP、kernel 去框架依赖 |
| **0.8** | 托盘心跳、Dashboard 订事件 |
| **0.9+** | CLI、包边界、多客户端 |

冲突时：**产品主路径与数据不丢** > 目录美感。

---

## 11. 文档与代码入口索引

| 主题 | 入口 |
|------|------|
| Kernel API | `backend/api/routes/kernel.py` · `protocol.py` |
| Dispatcher | `backend/kernel/dispatcher.py` |
| Loop | `backend/agent/loop.py` |
| 生命周期宿主 | `backend/main.py` lifespan |
| 前端 API | `frontend/lib/api.ts` |
| 三词条 | `frontend/components/layout/ProductConceptsBar.tsx` |
| 桌面壳 | `electron/main.ts` |

---

## 12. 获取帮助（文档）

```text
docs/internal/README.md          ← 索引
docs/internal/ARCHITECTURE.md
docs/internal/TOPOLOGY.md
docs/internal/DEV_HANDBOOK.md    ← 你在这里
docs/internal/ROADMAP_AIOS_OS_FULL.md
docs/KERNEL_PLAN.md
docs/TECHNICAL_MANUAL.md         ← 偏全量 API/历史手册
```
