# Changelog

本项目版本记录遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与语义化版本。

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
