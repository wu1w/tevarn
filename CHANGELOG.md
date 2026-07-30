# Changelog

本项目版本记录遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与语义化版本。

## [0.4.10-alpha] - 2026-07-30

**feature/agent-kernel 分支统一版本号**；Phase 5 开工（轻量化发行准备）。

### Changed
- 产品版本全系对齐 **`0.4.10-alpha`**（`backend/VERSION` + `scripts/sync_version.py`）
- Phase 5 详规：`docs/design/PHASE5_EXECUTION_PLAN.md`；安装 `docs/INSTALL.md`；零依赖 `docs/ZERO_DEPS.md`

### Added / Hardened
- Phase 5.1a：默认零外部依赖冒烟 `test_phase5_zero_deps`
- Phase 5 D1：channel 入站 `sanitize_channel_ingress`（长度 / NUL / 非打印）
- 测试：xdist 每 worker 独立 SQLite，消除并行建表竞态

### Notes
- 正式公开 tag（0.7 / 1.0 等）留到 Phase 5 关账再定；本分支一律 `0.4.10-alpha`

## [1.0.0-alpha] - 2026-07-29

**Agent Runtime 叙事成立（alpha 内测）**：对齐 `ROADMAP_AIOS_OS_FULL` 0.7→1.0 DoD 切片。  

产品文件版本升至 `1.0.0-alpha`；协议 **0.2.0**。

### Added / Hardened（0.7 Kernel-first）

- `python -m backend.runtime` Kernel Host；`--headless` 无 UI 跑 dispatcher
- Electron：关窗默认不杀 Runtime；托盘「Stop AI Runtime & Quit」
- 领域事件：`domain_events` + WS / REST 续订；`DomainEventBridge` 全站失效查询
- **kernel 去 FastAPI 依赖**：`kernel/ports.set_ws_manager`；dispatcher 不再 import `backend.api`

### Added / Hardened（0.8 Console）

- 托盘角标轮询 jobs/running + 审批
- 主路径事件刷新为主、REST 兜底

### Added / Hardened（0.9 多客户端）

- CLI：`status` / `jobs` / `job-stop` / `approve` / `events` / `login`
- 协议 manifest `client_guide` + `domain_events` 互操作索引

### Added（1.0 叙事）

- 版本号全系 `1.0.0-alpha`
- 协议 `PROTOCOL_VERSION=0.2.0`
- FE E2E：`frontend/e2e/aios_os_spine.spec.ts`
- 测试：`test_kernel_no_fastapi.py`

### Added（LLM 公平调度 + 编制记忆 · 2026-07-29）

- **`LlmAdmissionController`**（`kernel/llm_scheduler.py`）：全局 in-flight 槽位、主人预留、加权公平排队、日配额；挂接 `llm_round`
- **`CrewMemoryAssembler` / `CrewMemoryWriter`**（`kernel/crew_memory.py`）：注入收口、失败不沉淀、默认不自动 distill
- API：`GET /kernel/scheduler/status`；`POST .../memory/preview` · `retire` · `distill-from-item`
- UI：内核页「调度」Tab；资料卡记忆废止 / 预览注入 / 工单沉淀
- 配置：`llm_max_in_flight*`、`crew_memory_*`；单测 `test_llm_scheduler` / `test_crew_memory`
- 方案：`docs/internal/PLAN_LLM_SCHED_AND_CREW_MEMORY.md`
- **编制记忆 × 向量 RAG**：`CrewMemoryAssembler` experience 超 cap 时 `search_identity_memory` top-k，对齐 SQLite current / 废止过滤；联调 `python -m backend.scripts.smoke_vector_crew_memory`

## [0.4.6-alpha] - 2026-07-28

Product Spine：对话式管家 + 多级危险授权 + 员工权限看板。  
**0.5 Durable / 0.6 预览**（同版本增量；完整 0.6.0 见 `docs/internal/ROADMAP_0.4.5_to_0.6.md`）。

### Added（AIOS 终点路线图 + **P1 整包** · 2026-07-29）

- **路线图** `docs/internal/ROADMAP_TO_AIOS.md`：四核护城河 · P0→P5 到 AIOS 终点
- **晨报 API** `GET /kernel/workspace/brief` + CLI `takton brief`
- **工作台** `OrgMorningBrief`：组织晨报主叙事
- **P1.3 员工页**：编制/管理员工文案 · 联系 TA · 链回工作台
- **P1.4 审批页**：老板桌文案 · 待决/清空横幅 · 链回晨报
- **P1.5 Chat 降权**：导轨「联系员工」排在审批后 · 空态引导 · 标题联系人语义
- **nav**：驾驶舱→工作台（中/英 Workspace）

### Fixed（工程收口 · 七项卫生 · 2026-07-29）

- **默认端口统一 8090**（Electron / package scripts / deploy；候选仍含 8000）
- **Electron 唯一真源** `electron/`；`frontend/electron` 改为 stub + README
- **`backend/adapters` + `runtime.facade`** 拓扑占位与门面
- **`useDomainEvents` 只读 store**；Owner 仅 AppShell
- **`scripts/_patch_*.py` → `scripts/archive/patches/`**
- **agent resume/subagent、evolution evaluator** 去掉 `api.dependencies`
- **高级页** 统一 `AdvancedShell`/`LegacyQuiet`（activity/market/tasks/cluster/…）
- **TECHNICAL_MANUAL** 顶部过期声明

### Added（代码 DoD 收口 · 事件全站 / CLI / Run 关联 · 2026-07-29）

- **全局 DomainEventBridge**：AppShell 订 WS，按 topic 失效 jobs/identities/approvals 查询
- **事件续订**：`seq` / `after_seq` / `since_ts` / `head_seq` / cursor
- **CLI**：`login` / `logout` / `follow`；token `~/.takton/cli_token`
- **Run 关联**：`jobs/running` 含 `run_ref`（job_id/process_id/session_id/identity_id）
- **记忆主入口**：`/memory` 顶部 CrewMemoryHub + LegacyQuiet
- **Electron**：复用 detached Kernel Host（`/runtime/status` 探测）

### Added（OS 化落地 · Kernel-first / 事件 / CLI · 2026-07-29）

- **Kernel Host 入口**：`python -m backend.runtime`（`--headless` 可选）；`scripts/start-kernel-host.ps1`
- **领域事件**：`kernel/domain_events.py`；`_emit` 挂钩；
  `GET /kernel/events/domain` · `WS /ws/domain`；驾驶舱实时条
- **Runtime 心跳**：`GET /runtime/status`（loopback badge）
- **Electron 退出语义**：关窗隐藏；「退出控制台」不杀 AI；「停止 AI 并退出」才杀进程；托盘 tooltip
- **CLI**：`takton status|jobs|job-stop|approve|events|runtime`
- **dispatcher** 不再 import `api.dependencies`（Kernel 层去 FastAPI 依赖）
- **测试** `test_domain_events.py`

### Added（开发手册 / 架构 / 拓扑 · 2026-07-29）

- **内部文档集** `docs/internal/`：
  - `DEV_HANDBOOK.md` — 环境、分层改码、API 速查、测试、反模式
  - `ARCHITECTURE.md` — 逻辑分层、目录映射、通信/数据/治理
  - `TOPOLOGY.md` — 部署/进程/主路径/工单序列/事件/存储拓扑
  - `README.md` — 文档索引
  - `ROADMAP_AIOS_OS_FULL.md` — Kernel-first OS 化路线（既有）

### Added（协议 / 治理 / 心智 · 2026-07-29）

- **互操作协议 0.1** `takton-aios-protocol`：
  `GET /kernel/protocol/manifest|concepts|governance|surface|agent-cards`；
  `POST /kernel/protocol/a2a/tasks`（A2A-lite → Inbox 工单）
- **Agent Card**：员工可移植描述（skills=capabilities + takton 扩展）
- **治理骨架**：红线清单、relaxed_visible/locked 预设、可研究 kernel surface
- **产品心智**：`ProductConceptsBar`（员工/工单/审批）挂驾驶舱/员工/审批；
  Goals/Knowledge 套 `LegacyQuiet` 降级；内核页「协议」Tab
- **文档** `docs/internal/PROTOCOL.md`；测试 `test_protocol_governance.py`

### Added（0.5/0.6 缺口续开发 · 2026-07-29）

- **E4 统一停止**：`POST /kernel/jobs/stop`（inbox_item_id / process_id）→
  agent `loop.stop` + task cancel + `end_process(killed)` + 工单 `cancelled`；
  内核页 Live jobs「停止」按钮
- **F2 并发上限**：`agent_dispatcher_max_global_concurrent`（默认 8）/
  `agent_dispatcher_max_identity_concurrent`（默认 1 串行）
- **日报一键已读**：`POST /kernel/workforce/report/read`；report 含 `marked_read_at` /
  `has_unread`；员工页「标记已读」
- **审计只读页** `/audit`：系统 audit_logs + 内核事件 + policy.decision
- **空编制 seed**：`POST /kernel/workforce/seed-template-crew` + 员工页空态 CTA
- **测试** `backend/tests/kernel/test_stop_concurrency_report.py`
- **缺口报告** `reports/AIOS_0.5_0.6_GAP_CONTINUATION_2026-07-29.md`

### Added（0.5.x / 0.6 预览 · 夜冲刺）

- **工单完成/失败通知**：dispatcher 终态写 `notifications`（`task_complete` / `task_failed`）
- **待批扩权通知**：`request_escalation` 仍 pending 时系统通知主人
- **policy.decision 权限网**：mediate / escalate 统一 who/what/allow|deny|escalate；
  `GET /kernel/policy/decisions`；内核页「权限网」Tab
- **一键备份**：`POST /kernel/backup/export` 导出编制/记忆/工单/会话摘要/审计尾；内核页按钮
- **操作手册** `docs/internal/AIOS_OPERATOR.md`；**进化叙事** `EVOLUTION_NARRATIVE.md`
- **模板员工 seed** `backend/scripts/seed_template_crew.py`（小白/研究员/工程师）
- **测试** `test_policy_and_notify.py` / `test_memory_authority.py`

### Added（0.5 Durable 预览）

- **死信台**：工单达最大重试 → `dead`；`GET /kernel/inbox/dead`、重放/丢弃；员工页 DeadLetterPanel
- **现在在跑**：`GET /kernel/jobs/running`；内核页摘要
- **崩溃预期** `docs/internal/CRASH_RECOVERY.md`；**记忆权威** `docs/internal/MEMORY_AUTHORITY.md`
- **运行记录收起**：SessionRunsPanel 默认折叠，展开看列表/步骤
- **测试** `test_inbox_dead_letter.py`：dead → requeue / discard

### Added（管家与权限）

- **危险确认四级授权**：拒绝 / 允许一次 / 本会话允许 / 本员工允许（写入 Identity.capabilities）
- **grant_store**：会话级授权短路；本员工允许持久化编制能力
- **员工权限看板**（`/security` 顶部）：实时人数 + 分员工能力开关
- **crew_steward 工具**：CEO 对话 hire/list/assign/status（编制真源）

### Added

- **存储决策文档** `docs/internal/STORAGE.md`：默认 SQLite 权威；Redis 接口保留、默认关
- **概念表** `docs/internal/concepts.md`：员工 / 工单 / 审批
- **Hire→Identity 写死**：`POST /kernel/identities` 支持 `create_skill_pack` + persona/duty/memory；
  自动创建 SubAgent 技能包并 1:1 挂 `sub_agent_id`
- **配置** `TAKTON_AIOS_PROFILE=aios-dev`：打开 kernel/dispatcher（不强制 Redis）
- **派活人话错误**：inbox 503/404/400 返回可操作说明；FE 空选/空指令前置校验
- **员工页日报条**：近 24h 完成/失败/待处理/提权待批（与驾驶舱同源）
- **C1 主路径测试**：`backend/tests/kernel/test_product_spine_046.py`
  （hire + skill pack + mock dispatcher 闭环 + HTTP 人话错误）
- **C1 Playwright 骨架**：`e2e/product-spine-hire-dispatch.spec.ts`
  （API 招人派活 + UI 员工页 CTA；后端不可达则 skip）

### Changed

- **版本全系 0.4.6-alpha**：package.json / frontend / appVersion / VERSION / pyproject / FastAPI
- **IconRail 主路径**：驾驶舱 / 对话 / 员工 / 审批 / 内核；Goals/Knowledge/Activity/Market 降级
- 招聘向导文案改为「新建员工 / 入编」；空态 CTA 串到招聘
- 审批通过 toast 提示「可重试工具步 / 重派工单」
- **CEO 编排脊柱**：联系 CEO/管家会话注入 `steward_orchestration_prompt`；强制 `crew` 工具包
  （`crew_steward` / `delegate_task` / `agent_call`）；coding profile 默认带 `crew_steward`
- **派活改走编制**：`delegate_task` / `agent_call` 有员工时写 Inbox，禁止临时子代理闷跑
- **联系 TA 人设**：CEO/小白等用大管家 identity 文案（分析→assign），普通员工仍用执行者人设

### Fixed

- **Dispatcher 建会话**：`AsyncSessionRepository.create` 改为传 `data` dict；
  员工无 `user_id` 时回落默认 admin；修复工单 claim 后立刻 failed
  （`unexpected keyword argument 'user_id'`）
- **工单抢跑空工具表**：`load_all_tools` 改为 await 后再启动 workforce dispatcher；
  避免首轮 `Loaded 0 tools` 只吐假 tool_call XML
- **工单 process_id 回写**：`loop.run` 结束后保留 `_last_kernel_process_id` 给 dispatcher
- **编制能力映射**：`file_rw` 覆盖 glob/grep/file_read 等；mediate 不再因工具名≠抽象 cap 误拦
- **员工工具不弹主人**：workforce 权限走 `steward_permission`（编制能力裁决）；
  禁止危险确认窗 / 提权洪水；主人只批策略与节点（clarify/goal）

### Changed（企业 IM 心智）

- **一人一会话**：`POST /sessions/contact` find-or-create；侧栏点同事进聊天，不堆 session
- **侧栏通讯录**：点名字聊天、点头像进资料；项目组列表；去掉工单会话占「最近对话」
- **今日任务**：员工资料卡绑 inbox 工单（可展开结果），不再只看 process.identity 名
- **项目组群**：`project_groups` 表 + API；`crew_steward open_project` / assign 挂 `project_title`；
  `/chat?group=` 进度看板

## [0.4.5-alpha] - 2026-07-28

### Fixed（合入 0.3.5 聊天稳定性补丁）

- **长会话一拨一动**：L5 注入改为 Claude Code 式续跑（`Pick up the last task…`），
  去掉 `REFERENCE ONLY / Do not resume`；9 段工程摘要；L3 只清 tool 正文保配对；
  工具轮 mid-loop **禁止 L5**（`allow_l5=False, micro_only=True`）
- **乱切页面/会话状态稳定**：`SessionRunSnapshot` 断线仍累积 partial + live tools；
  `sync_response` 恢复 in-flight；前端 `streamSessionStore`；软断线不假 idle；
  Stop 清 tools；叠跑等待旧 task

### Kernel（既有 0.4.5 条目）

多 worker Kernel 执行面正确性加固：计数权威、提权去重、挂起恢复、事件观测。

### Fixed

- **tokens_used 回滚**：`put_process` 更新时不再 HSET 覆盖计数；权威仅在
  `charge_tokens` / Lua `HINCRBY`；`set_process_fields` 显式拒绝写 `tokens_used`
- **跨 worker 双 pending 提权**：`try_claim_escalation` SETNX 占坑
  （`esc:claim:{process}:{caps_fp}`），后到者复用 owner id；
  `find_covering_pending` 二次去重
- **claim TTL 与 pending 同寿**：`_CLAIM_TTL = _ESC_TTL`（7d），`put_escalation`
  pending 时对指向本单的 claim 续期，避免 120s 过期后同 caps 再开第二单
- **产品版本号对齐**：`package.json` / `frontend` / `appVersion` / `VERSION` /
  `pyproject` 统一为 **0.4.5-alpha**（与 CHANGELOG 顶栏一致）
- **跨 worker 挂起恢复**：`resume_process` 写 Redis + `publish_resume`；
  loop `wait_if_suspended` 经 `refresh_state` 轮询 Redis state
- **事件多 worker 不可见**：`_emit` → Redis LPUSH 热缓冲；
  `events()` 合并本机缓冲与 Redis 列表
- **批准目标不透明**：`EscalationRequest.target` = `process|identity`，
  API/前端 toast 区分「并入当前进程」与「写入编制档案」
- **身份重名歧义**：`AgentIdentity.name` unique + create 前置查重（409）

### Added

- **auto_tighten_2x 真正生效**：日均 charge/run 统计 + charge 路径同步收紧
  `token_budget`（settings 规则缓存 `rule_enabled_sync`）
- 共享态测试：claim SETNX / 事件缓冲 / daily_avg / put 不回滚 /
  提权去重 / approve target / auto_tighten / events 合并

## [0.4.4-alpha] - 2026-07-27

PLAN 阶段 0.7「成长与组织」落地：六大支柱全部立起——
**员工可以写述职报告，升职决定权在老板手里**。

### Added

- **受控进化引擎**（`agent_evolution_proposals` 表 + `EvolutionEngine`）：
  规则化分析器（无 LLM，机器可验证）从 Episodic 工作记录生成
  **述职报告式建议**——四类规则：
  SOP 沉淀（≥5 单且成功率 ≥80% → methodology 记忆）/
  工具淘汰（mediation 拒绝率 ≥50% 且样本 ≥5 → 移出编制）/
  能力入编（同能力 escalation 获批 ≥2 次 → 并入权限档案）/
  planner 检讨（失败率 ≥30% → planner_prefs 调整）
- **审批状态机**：pending → approved → applied / rejected / rolled_back；
  **auto_apply=False 硬约束**——不存在配置项/环境变量/内部 API 形式的
  自动应用后门（状态机守卫测试断言）；`payload.before` 回滚点，
  回滚恢复应用前状态；全生命周期事件进哈希链
- **汇报线观察**：`GET /api/kernel/workforce/org`——从 kernel_processes
  parent 链聚合 reports_to 关系（谁派生谁=汇报线）+
  组织预算层级汇总（只读视图，不改预算机制）
- **进化 API**：`GET/POST /api/kernel/evolution/proposals`、
  `POST .../analyze|approve|reject|rollback`
- **测试**：+6（SOP 全生命周期含回滚/能力入编回滚/工具淘汰回滚/
  无后门状态机守卫/planner 检讨/汇报线聚合），全量 712/0

### Changed

- 过程/战略文档移出公开库（本地留存），docs/ 只保留
  TECHNICAL_MANUAL；gitignore 防回潮

## [0.4.3-alpha] - 2026-07-27

PLAN 阶段 0.6「自主运转」落地：workforce 开始异步运转——
**当你回来时，工作已经推进了**。

### Added

- **Agent 收件箱**：`agent_inbox_items` 表 + `InboxService`——
  cron/webhook/api/manual 统一转工单；**有界红线**（`agent_inbox_max_pending`
  默认 200，超限丢弃最旧 pending + 审计）；身份停职拒收/归档拒投，
  全程哈希链审计（enqueued/claimed/done/retry/failed/dropped）
- **Workforce Dispatcher（唤醒执行器）**：扫描 inbox → 唤醒身份——
  kernel 进程挂**编制内权限档案与默认预算**（异步入口不绕过权限与预算，
  越权工具被 mediate 拦截、token 真实扣减）；**编制内串行**
  （同身份同时在手一单）；失败自动重试（attempts≤3）后 failed；
  单工单超时熔断（`agent_inbox_item_timeout`）
- **休眠-唤醒-续作**：无常驻进程（零成本休眠）→ 工单唤醒 →
  身份专属 workforce session 复用（历史对话即 Episodic 上下文）+
  **Identity Memory 注入 prompt**（人格/职责/方法论常驻）
- **cron 派活**：`cron_jobs.identity_id + instruction`——
  定时任务从「跑 workflow」升级为「定时给员工派活」（优先于 workflow_id）
- **「你不在的这段时间」日报**：`GET /api/kernel/workforce/report?hours=`——
  工单统计/各身份产出/中介拦截/待批准提权聚合；
  首页空状态新增 **workforce 工作汇报卡片**（有活动才显示）
- **工单 API**：`POST/GET /api/kernel/inbox`（手动派活 + 查询）
- **测试**：+7（身份门控/有界溢出/优先级+串行/全流程 mediate+预算/
  重试熔断/停职不派发复职恢复/日报聚合），全量 706/0

### Changed

- lifespan 启动装配 inbox + dispatcher（`agent_dispatcher_enabled=false` 可关）

## [0.4.2-alpha] - 2026-07-27

PLAN 阶段 0.5「编制与档案」落地（PLAN_AI_WORKFORCE 第一阶段）：Agent 从运行时对象
变为持久实体——**进程可以死，Agent 不死**。

### Added

- **Agent Identity 系统**：`agent_identities` 表 + `IdentityRegistry`——
  持久身份（名称/职责/权限档案/信用分），状态机
  active → suspended ⇄ active → archived（**archived 终态不可逆，身份不可销毁**）；
  权限变更全程审计事件（禁止静默改权），所有身份事件进哈希链
  （`identity:<uuid>` 前缀，与进程事件同链）
- **Identity Memory（四层记忆第四层）**：`identity_memory` 表——
  persona/duty/experience/preference/methodology 五类；
  **修改不覆盖**（版本链 supersede），distilled 来源必须带审批人
  （进化审批不可绕过的红线在数据结构层强制）
- **进程档案持久化**：`kernel_processes` 表——kernel 进程 create/running/
  end/权限变更经 **sink 模式**落盘（同步 put_nowait 零 await，符合单线程红线，
  后台 worker 异步消费）；重启恢复时 created/running → **interrupted**
  （诚实中断标记，不伪造存活）
- **Checkpoint 快照**：`kernel_checkpoints` 表，每 N 事件自动快照
  （`agent_kernel_checkpoint_interval` 默认 500）；**恢复 = 快照 + tail_hash
  后增量事件，禁止全量 replay**（快照锚点丢失时宁可空增量也不默默从头读）
- **身份 API**：`GET/POST /api/kernel/identities`、
  `POST .../transition|capabilities`、`GET/POST .../memory[/{id}/supersede]`
- **测试**：+7（身份生命周期/改权审计/记忆版本链/蒸馏审批/进程落盘重启/
  checkpoint 增量恢复断言非全量/锚点篡改安全），全量 699/0

### Changed

- `get_kernel()` 默认装配持久化 + 身份注册表
  （`agent_kernel_persistence=false` 回退纯内存态）
- lifespan 启动时执行 kernel 恢复 + 拉起持久化 worker

## [0.4.1-alpha] - 2026-07-26

审计缺口修复 + 提权交互地基：用户授权成为唯一合法的能力扩大通道。

### Added

- **提权交互（Escalation）**：agent 被能力集拦截时自动发起权限申请，
  `/security` 权限控制台实时展示待批准列表（5s 轮询），批准/拒绝一键处理；
  批准后能力并入进程能力集（持令牌进程自动重签令牌），
  申请去重防模型重试刷屏；三段事件（requested/approved/denied）全部进哈希链审计
- **观测 API**：`GET /api/kernel/escalations`、`POST .../approve|deny`
- **Token HMAC-SHA256 签名**：`to_dict` 默认签名、`from_dict` 默认验签，
  伪造/无签名一律 `TokenSignatureError`（密钥 HKDF 派生自 jwt_secret）；
  `verify=False` 保留历史数据兼容窗口
- **subagent 父进程链**：`delegate_task → run_subagent → child loop`
  按父能力集 narrow——修复 parent_id 误传 run 记录 id 导致父链落空的潜藏 bug
- **测试**：kernel 测试 58 → 70（escalation 全生命周期/令牌重签/去重/边界/父子隔离）

### Changed

- **`agent_kernel_explicit_capabilities` 默认 True**：主进程挂注册表全集快照，
  等效放行但使 subagent 继承/narrow 真实生效；新装 dynamic skill 首次使用
  将触发提权申请（批准后并入）；设 False 回退全放行兼容模式
- **并发假设文档化**：kernel 方法内部零 await（asyncio 单线程无竞态），
  模块 docstring 立维护红线——引入 await 前必先加锁
- **README**：Agent Kernel 章节 + 架构图叠加控制平面层

## [0.4.0-alpha] - 2026-07-26

Agent Kernel 控制平面正式引入：进程抽象、能力令牌、全路径执行中介、
预算强制、意图声明雏形、哈希链审计、系统服务化。

### Added

- **Agent Kernel（`backend/kernel/`）**：`AgentProcess` 六态生命周期 +
  `CapabilityToken`（narrowing 单调递减/过期单调递减/可序列化）+
  `AgentKernel`（进程管理/mediate/预算治理/审计）
- **全路径执行中介**：所有工具调用（含并行预取）、dynamic skill、MCP 工具
  统一经 `kernel.mediate()`；显式能力集/令牌未授权即拦截，
  拦截作为工具级错误反馈模型（不中断 run）
- **能力单调递减**：子进程能力只能是父集子集，预算不超父余额，
  提权在数据结构层面不可能
- **预算强制**：进程级 token 预算按 provider usage 扣减，
  耗尽自动中断 run（`[Budget Exceeded]`）
- **Intent Declaration 雏形**：意图声明 → 白名单策略合成最小能力令牌，
  高危能力需显式 `allow_risky`，父令牌自动 narrow
- **哈希链审计**：KernelEvent 链式 SHA-256，`verify_event_chain()` 篡改检测
- **观测 API + Console**：`GET /api/kernel/processes|events`，
  权限控制台新增 Agent Kernel 区块（进程能力/预算 + 中介事件流，5s 轮询）
- **系统服务化**：`deploy/takton-backend.service`（systemd 加固单元）+
  Windows NSSM 部署指南

### Changed

- `agent_kernel_enabled` 配置开关（默认开）：loop 运行纳入 Kernel 进程管理，
  装配失败显式降级告警（兼容模式行为与旧路径完全一致）

### Tests

- 新增 `backend/tests/kernel/` 33 用例：进程生命周期 / 令牌 narrowing /
  过期与范围强制 / 中介拦截 / 预算上限 / Intent 合成 / 哈希链篡改检测 /
  观测 API。全量回归 661/0。

## [0.3.2] - 2026-07-26

安全加固与跨平台沙箱大版本：访问控制、密钥管理、命令执行隔离、
依赖供应链四个维度的系统性加固。

### Added

- **独立权限控制台 `/security`**：侧边栏盾牌入口
  - 命令执行模式二选一：沙箱模式 / 本地模式（实时生效）
  - 高危命令策略表：8 个分类（删除文件/提权执行/关机重启/磁盘操作/系统服务/
    远程脚本管道/数据外泄/写入系统目录）逐类三态控制——
    **放行**（直接执行）/ **每次确认**（弹窗，默认）/ **禁止**（硬拦截 `[Policy Blocked]`）
  - 访问与凭证：单用户模式开关（关闭需确认）+ 桥接令牌一键生成
  - 安全自检面板：6 项检查绿/黄/红分级 + 修复建议
- **跨平台真沙箱**（`agent_computer_backend` 默认 `auto`，按平台自动选最强隔离）：
  - macOS：`sandbox-exec`（seatbelt）——deny default / 读全系统保工具链 /
    写仅 workspace / 最小环境 / 默认断网；系统自带零依赖
  - Windows + WSL2：经 `wsl.exe` 转发 bwrap，完整 Linux 隔离语义
  - Windows 无 WSL2：Job Object 受限模式（进程树清理 / 防 fork 炸弹 / 内存限额）
  - 统一能力探测（完整/受限/无三级），启动自检与权限控制台按真实能力展示
- **实时终端面板**：chat 页 agent 工具调用以终端命令流形式实时展示
  （$ 命令 + ✓/✗ 结果行），取代原截图面板；header「终端」开关 + 未读提示
- **统一启动安全自检**：host 绑定 × 单用户模式组合 fail-hard 拒绝启动，
  其余项（bridge_token/沙箱/加密盐/弱密码）醒目告警
- **管理员初始密码随机化**：非 Electron 部署首启自动生成并写入
  `~/.takton/initial_admin_password`（0600），不再硬编码 `admin`

### Security

- **P0-1**：`single_user_mode` 增加 loopback 硬闸门——非本机直连来源一律 403
  （只信 socket 对端，不信任可伪造的 X-Forwarded-For）
- **P0-2**：`jwt_secret`/`api_key` 默认值改为首次启动随机生成并持久化
  `~/.takton/secrets.json`（0600）；validator 修正为比对真实已知弱值集合
  （原比对 `change-me` 与实际默认值不符，防线从未触发）
- **P0-3**：docker-compose 环境变量名全面修正（`TAKTON_JWT_SECRET` 等；
  另修复 `DATABASE_URL`/`QDRANT_URL`/`TAKTON_ENCRYPTION_SALT` 失效变量，
  移除后端未使用的 redis 服务）；`TAKTON_SECRET_KEY` 旧名兼容 + deprecation 告警
- **P0-4**：补齐 `backend/Dockerfile`（非 root）与 `frontend/Dockerfile`（多阶段）
- **P0-5**：危险命令黑名单补数据外泄类规则（文件上传/反弹连接/凭证读取/
  编码外发/远程传输）；evolution G2 内容检查与执行层共用高严重度子集
- **P0-6**：bridge 端点非 loopback 且未设 token 时启动告警
- **P2**：审计日志 details 落盘前递归打码（api_key/password/secret/token 等
  键名 → `[REDACTED]`）
- **P2**：MCP 商店安装详情展示来源链接与 `registry:package_id` 包标识
- **P2**：Electron 渲染进程开启 `sandbox: true`（OS 级沙箱）
- **依赖漏洞清零**：`python-jose`（弃维护）迁移至 PyJWT 2.13.0；
  python-multipart 0.0.31 / python-dotenv 1.2.2 / jinja2 3.1.6 升级；
  xlsx 换 SheetJS 官方 0.20.3（修 Prototype Pollution + ReDoS）。
  `pip-audit` 14 → 0，`npm audit` 1 高危 → 0

### Fixed

- 运行中切换页面导致会话被误删（`Session not found`）——三层防线：
  活跃会话查询 API + 侧边栏清理前置跳过 + 删除接口 409 保护
- 输入框偶发无法输入（消息菜单透明遮罩吞点击 + composer 焦点兜底）
- Electron `file://` 协议下相对 `/api` 图片地址 404 导致的「实时画面加载失败」
- Docker 部署路径不可用（变量名错误 + Dockerfile 缺失）现已端到端可用

### Changed

- 截图实时面板退役（agent 的 `desktop_screenshot` 工具保留用于视觉感知，
  不再向前端推送截图流）
- 设置页安全区块迁移至独立 `/security` 权限控制台

## [0.3.1] - 2026-07-25

### Added

- Windows 一键安装包发布（Setup.exe + portable zip + install.ps1 自动解析最新 Release）
- 对话底栏模型选择框视觉瘦身

### Fixed

- 根 package.json 版本号对齐

## [0.3.0] - 2026-07-24

### Added

- Agent Computer（Linux bwrap 沙箱执行后端）
- 本地 RAG 全家桶：Qdrant 向量库 + 本地 Embedding/Reranker（OpenAI 兼容服务）
- 会话误删防护初版与桌面端体验打磨
