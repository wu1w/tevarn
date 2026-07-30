# Takton 开发总计划：Phase 1-5（0.4.6-alpha → 0.7 公开线）

> 制定日期：2026-07-30 · 周期约 15 周 · 单人 + AI 协作模式
> 定位：带治理内核的可自进化数字员工运行时（下一代 Agent 形态）
> 验收基准：**开发者本人的三大真实工作流** —— ① 日常开发任务 ② 公司 PPT 制作 ③ AI 辅助安全审计

---

## 0. 总原则（全程有效）

1. **广度冻结**：Phase 2 完成前，不新增第七种执行心智、不新增工具大类、不新增渠道适配器。
   例外：dogfood 中阻断自己真实工作的缺陷修复不受此限。
2. **每个 Phase 以"验收标准"关账**，不以"代码写完"关账。验收不过 = Phase 未完成。
3. **Dogfood 优先**：每周至少用 Takton 完成一次真实开发任务 + 一次真实 PPT 任务，
   把体验痛点记入 `docs/internal/DOGFOOD_LOG.md`（按周追加，格式：日期/任务/卡点/严重度）。
   该日志是各 Phase 排期微调的唯一输入源。
4. **AI 分工**：AI 承担安全审计、回归测试编写、大文件拆分等机械性工作；
   架构决策（Run 状态机、记忆权威规则）由本人拍板后再让 AI 实现。
5. 每完成一个 Phase，更新本文档的 checkbox 与"实际完成日期"。

---

## Phase 1：止血与地基（第 1-2 周）

目标：把已知的安全/并发风险清零，建立静态检查防线。此后所有开发都在更硬的地面上进行。

### 1.1 安全 Critical 回归测试化（第 1 周，AI 主力）

第一轮审计 11 个 Critical 逐条转为 pytest 回归测试，放入 `backend/tests/security/`：

- [ ] `test_shell_injection.py`：shell 工具注入面（命令拼接、管道、反引号、`$()`、编码绕过）
- [ ] `test_sandbox_path_escape.py`：沙箱路径绕过（`..`、符号链接、UNC 路径、Windows 短文件名）
- [ ] `test_tool_auth.py`：工具执行端点无认证访问全部 401/403
- [ ] `test_sql_injection.py`：所有接受用户输入拼接查询的仓库方法
- [ ] `test_channel_gateway_input.py`：channel_gateway.py（40KB）入站消息注入面
  —— IM 消息是外部不可信输入直达 agent loop，是公开后的第一攻击面
- [ ] 验收：以上测试全绿进 CI；任何一条红 = 对应 Critical 视为未修复，立即修

### 1.2 已知并发缺陷修复（第 1 周）

第二轮审计遗留 High 项，全部在主循环热路径上：

- [ ] L2-H1 `tool_execution` 超时重试竞态：重试前 `task.cancel()` + `await task` 显式清理
- [ ] L2-H2 `context_pipeline.py` 压缩递归：max_retries=3，超限硬截断 + 告警日志
- [ ] L2-H3 orchestrator `_state_lock` 内含 await：改为锁内只做状态读写决策，锁外执行异步动作
- [ ] `checkpoint.py` 原子写：temp file + `os.replace()`，崩溃不产生半截 checkpoint
- [ ] L2-M1/M2/M3（deepcopy 边界、goal 状态同步）顺带修复
- [ ] 验收：每项修复附带一个能复现原缺陷的回归测试（先红后绿）

### 1.3 CI 加固（第 2 周）

扩展 `.github/workflows/backend-ci.yml` 并新增前端 CI：

- [ ] `ruff check backend/`：初次全量修复由 AI 完成；规则集从 `E,F,W,I,B` 起步
- [ ] `mypy backend/agent backend/kernel`（渐进式，先覆盖两个核心目录，
      `--ignore-missing-imports`），每个 Phase 扩一个目录
- [ ] 新增 `frontend-ci.yml`：`tsc --noEmit` + `eslint` + `next build`
- [ ] pyproject.toml 增加 `[tool.ruff]` / `[tool.mypy]` 配置节，本地与 CI 同一套规则
- [ ] 验收：main 分支 CI 全绿；此后红 CI 不合并

### 1.4 工程卫生（第 2 周，AI 主力）

- [ ] 版本号单一来源：`backend/VERSION` 为权威，pyproject / README / frontend package.json
      构建时读取或脚本同步（`scripts/sync_version.py`），三处不一致 CI 报错
- [ ] 根目录 15+ 个报告类 MD 归档至 `docs/archive/2026-07/`，根目录只留
      README / CHANGELOG / AGENTS / PLAN
- [ ] `llms.txt` 与实际能力对齐一次（去掉未实现声明）

**Phase 1 关账验收**：CI（含 lint/type/前端）全绿；安全回归测试目录存在且全绿；
用 Takton 跑一次真实开发任务，全程无因 1.2 所列缺陷导致的中断。

---

## Phase 2：统一 Run + Durable Runtime（第 3-6 周）→ 0.5.0-alpha

目标：消灭 G1（双轨心智）/G2（重启丢单）/G3（Run 不统一）。
这是全项目最重要的一次重构，**本 Phase 期间新功能完全冻结**。

### 2.1 统一 Run 实体（第 3 周，本人设计 + AI 实现）

- [ ] 设计文档先行：`docs/design/RUN_UNIFICATION.md`，一页纸定义：
  ```
  Run {
    id, origin: chat|inbox|cron|cluster|subagent,
    status: pending|running|suspended|waiting_approval|done|failed|cancelled,
    session_id, identity_id?, parent_run_id?,
    checkpoint (JSON), budget {token_limit, token_used},
    created_at, updated_at, finished_at
  }
  ```
- [ ] `backend/models/run.py` + alembic 迁移 + `AsyncRunRepository`
- [ ] 现有 run_state.py / run_recorder.py 收敛为该实体的读写层（权威在 DB，内存是缓存）
- [ ] 状态机转换集中在一个模块（`backend/agent/run_lifecycle.py`），
      禁止四处直接改 status 字段

### 2.2 四条触发路径接入统一 Run（第 4 周）

- [ ] chat（websocket → NexusAgentLoop）：进入 loop 前创建/恢复 Run
- [ ] inbox（dispatcher.py）：工单派遣 = 创建 origin=inbox 的 Run；
      kernel process 与 Run 一一关联（process.meta 记 run_id）
- [ ] cron（cron_scheduler.py）：触发 = 创建 origin=cron 的 Run
- [ ] cluster（cluster_executor.py）：父 Run + 子 Run（parent_run_id），
      聚合状态由子 Run 状态推导
- [ ] 验收：`GET /runs` 一个接口能看到全部四类执行的统一状态

### 2.3 Durable 化（第 5 周）

- [ ] 每次迭代结束将 checkpoint 写入 Run（iteration、messages 摘要指针、goal 状态、
      mode），复用现有 checkpoint.py 机制但权威落到 runs 表
- [ ] 启动时 recovery：扫描 status=running 的 Run → 标记 interrupted →
      按策略处理（inbox/cron 自动续跑，chat 提示用户可恢复）；
      续跑走现有 `resume.py` 的续跑提示词机制
- [ ] 配置开关：`agent_run_auto_recover`（默认 true）
- [ ] **验收（本 Phase 核心）**：启动一个 ≥20 轮的长任务，中途 kill 后端进程，
      重启后任务自动续跑且不重复已完成步骤。写成集成测试
      `tests/test_durable_run_recovery.py`（已有 test_durable_run.py 作基础扩展）

### 2.4 收敛双轨心智（第 6 周）

- [ ] 心智统一为一句话：**"一切执行都是 Run；Identity 是执行者；
      Cluster/SubAgent/Hire 是 Identity 的三种编排形态；Workflow 是 Run 模板"**
- [ ] 落地动作（不做大爆炸重写，做归位）：
  - subagent_runner / cluster_executor 的执行入口统一收到 Run 创建路径
  - workflow_engine 的节点执行改为派生子 Run（保留其编排逻辑）
  - Goal 挂在 Run 链上（goal_state 关联 run_id）而非独立心智
- [ ] `docs/design/EXECUTION_MODEL.md`：一页纸讲清执行模型，讲不清 = 没收敛完
- [ ] 顺带拆分：loop.py（2842 行）继续 phases 化拆到 <1500 行；
      manage_tools.py（97KB）按域拆成 file_tools / shell_tools / search_tools 等

**Phase 2 关账验收**：kill -9 恢复测试绿；四来源统一进 /runs；
用 Takton 布置一个隔夜长任务（如"审计 X 模块并出报告"），睡前启动、早上收结果。

---

## Phase 3：记忆总线 + 权限一张网 + 前端补缺（第 7-10 周）→ 0.5.x

目标：消灭 G4（记忆多套）/G5（权限散落），并让已有后端能力"被看见"。

### 3.1 记忆总线（第 7-8 周）

- [ ] 写入权威规则（定死，写入 `docs/design/MEMORY_BUS.md`）：
  | 记忆类型 | 权威存储 | 谁能写 | 冲突裁决 |
  |---|---|---|---|
  | 人格/经验（identity memory） | identity_memory 表 | 本 Identity + 审批 | supersede 版本链 |
  | 事实/实体 | entities + memory graph | 任何 Run（经总线） | supersede 版本链 |
  | 文档知识 | wiki | 用户 + 审批后的 agent 写入 | 人工 |
  | RAG/向量 | Qdrant | **仅索引层**，随权威变更同步，不独立写 | 不适用 |
- [ ] `backend/services/memory_bus.py`：唯一写入口
      `remember(kind, content, source_run_id, confidence)`，内部路由到权威存储 +
      触发向量索引；现有各写入点（crew_memory、memory_tools、wiki 写入）改走总线
- [ ] supersede 机制从 identity memory 推广到 entities（版本链 + 旧向量清理）
- [ ] 读侧统一：`recall(query, kinds, top_k)` 跨源检索并标注来源与新鲜度
- [ ] 验收：grep 全库，除 memory_bus 外无任何模块直接写记忆表；
      supersede 后旧版本不再出现在检索结果（回归测试）

### 3.2 权限一张网（第 9 周）

- [ ] 四层规则（permissions_rules profile 基线 / permission_rules_dsl 用户 DSL /
      tools/permissions.py 路径白名单 / skill contract 声明）合并为单一决策器
      `backend/kernel/permission_court.py`，kernel.mediate() 只调它
- [ ] 每次决策输出可解释记录：`{tool, args_digest, verdict, matched_rule, layer}`
      进哈希链审计事件
- [ ] 决策优先级定死：secret floor deny > 用户 deny > skill 声明 > 用户 allow >
      profile 默认 > ask
- [ ] 验收：任意一次工具调用能在审计里查到"哪条规则、哪一层放行/拦截"；
      现有权限相关测试全数迁移后仍绿

### 3.3 前端补缺（第 10 周，只补三块，其余 60 个缺口接口明确不补）

- [ ] **Evolution 审批面板**（`/evolution`）：draft 列表 → diff 预览 → approve/reject
      /apply/rollback —— 没有它自进化闭环等于不存在
- [ ] **Kernel 监控页**（`/kernel`）：/proc 风格进程表（Run/预算/状态）+
      审计链滚动视图 + suspend/resume 操作 —— demo 时的核心"哇点"
- [ ] **Run 时间线**（`/tasks` 改造）：统一 Run 的列表/详情/checkpoint/续跑按钮
- [ ] 顺带：localeStore.ts（181KB）拆为 JSON 资源文件按需加载；
      修复 GAP 报告中 FE 调用不存在接口的 2 处
- [ ] 验收：三个页面在 Electron 桌面端可用；GAP_FE_BE_REPORT.md 重跑更新

**Phase 3 关账验收**：用真实 PPT 工作流验证记忆——本周告诉它公司 PPT 的风格偏好，
下周新会话它能自动应用；权限面板上能解释它为什么被允许读某目录。

---

## Phase 4：可信自进化 + 主循环手感（第 11-14 周）→ 0.6.0

目标：把 G7（进化浅）做成护城河；主循环体验对齐 Claude Code。

### 4.1 技能沙箱回放验证（第 11-12 周，差异化核心）

- [ ] `backend/evolution/replay_validator.py`：distiller 蒸馏出的 SKILL.md
      在进入待审批前，用产生它的历史轨迹在 computer/ 沙箱中回放验证：
      挂载新技能 → 重跑同类任务 → 比较完成度/轮数/工具错误率
- [ ] 验证结果（pass/fail + 指标对比）附在 draft 上，进审批面板展示
- [ ] scoreboard 自动回滚阈值参数化复查（沿用 `agent_evolution_*` settings 模式）
- [ ] 验收：一条"回放不通过的技能无法进入 approved"的端到端测试

### 4.2 身份成长档案（第 12 周）

- [ ] `/agents/{id}` 档案页：记忆时间线（版本链可视化）+ 已习得技能及其评分曲线 +
      历史 Run 统计（成功率/平均轮数/预算消耗）
- [ ] 数据全部来自现有表（identity_memory、evolution store、runs），只做聚合 API + UI

### 4.3 主循环手感冲刺（第 13-14 周，以 dogfood 日志为需求清单）

- [ ] **压缩基准**：`scripts/bench_agent/` 增加固定任务集（含 3 个你真实做过的
      开发任务 + 1 个 PPT 任务脚本化版本），度量压缩触发前后的任务完成率与轮数；
      每次改 context_pipeline 必跑
- [ ] **中断/恢复手感**：前端停止按钮 → loop 干净落 checkpoint → 一键续跑不丢上下文
- [ ] **错误可读性**：工具失败信息面向用户重写（保留 `_sanitize_tool_error` 脱敏，
      但给出"下一步建议"而非只报错误类型）
- [ ] **PPT 工作流专项**（你的高频场景）：generate_ppt 技能打磨——模板记忆化
      （走记忆总线存公司风格）、中间产物落 workspace 可预览、失败可从中间步骤续跑
- [ ] 清空 DOGFOOD_LOG.md 中所有严重度=高 的条目

**Phase 4 关账验收**：bench_agent 固定任务集完成率 ≥ 上一版本；
一次完整的"公司 PPT 从要点到成稿"任务在 30 轮内无人工救场完成；
进化面板里至少有一个经回放验证、审批上岗、真实用过的自产技能。

---

## Phase 5：轻量化发行与公开（第 15 周起）→ 0.7

目标：以差异化定位公开，吸取 OpenClaw"太重"的教训。

### 5.1 轻量化发行版（第 15-16 周）

- [ ] 单命令安装路径复核：install.ps1（Windows 主力）/ install.sh，
      全新机器 10 分钟内从零到可对话
- [ ] 默认零外部依赖：SQLite + 无 Qdrant（RAG 降级本地检索）+ 无 Redis（单进程内存）
      —— 三者全部已是可选，验证降级路径全绿即可
- [ ] 资源基线：8GB 内存 / 无 GPU 机器空载 <500MB、单会话峰值 <1.5GB，实测记录
- [ ] `start.py` 一键启动 + Electron 打包三平台冒烟（Win NSIS 优先）

### 5.2 公开准备（第 16-17 周）

- [ ] 安全终审：Phase 1 的安全回归全绿 + 一轮针对公开暴露面的新审计
      （认证、CORS、channel webhook 签名校验、默认凭据）；`TAKTON_JWT_SECRET`
      无默认值强制生成
- [ ] 版本对齐：README 重写为公开定位——**不是又一个 coding CLI，而是
      "带治理内核的可自进化数字员工运行时"**
- [ ] 首发 demo 三连（录屏 + 文档）：
  1. kill 掉进程，隔夜任务自己爬起来继续（Phase 2 成果）
  2. 审批面板看着 agent 学会新技能并回放验证（Phase 4 成果）
  3. 审计链逐条回放 agent 做过的每件事及权限依据（Phase 3 成果）
- [ ] CHANGELOG 从 0.4.6 → 0.7 补齐；docs/TECHNICAL_MANUAL.md 对齐新执行模型

### 5.3 公开后节奏（第 18 周起，滚动）

- [ ] 生态最小面：packages 发布/安装 URL 流程文档化（已有 publisher.py），
      不自建市场，先兼容 agentskills.io 格式（distiller 已兼容）
- [ ] 渠道解冻：按外部用户需求逐个开渠道适配器（此前保持冻结）
- [ ] 收集外部 issue 重建 backlog，回到按月迭代

---

## 附A：每周节奏（全程执行）

| 时间 | 事项 |
|---|---|
| 周一 | 看 DOGFOOD_LOG，定本周 3 个必完成项 |
| 周中 | 开发；每日 CI 保绿 |
| 周五 | 用 Takton 做一次真实任务（开发/PPT 交替）；记录 dogfood 日志 |
| 双周 | 让 AI 做一次增量安全审计（只审两周内的 diff，避免预算耗尽式全量审计盲区） |

## 附B：风险与止损

| 风险 | 信号 | 止损动作 |
|---|---|---|
| Phase 2 重构失控 | 第 5 周结束 kill-恢复测试仍不绿 | 缩小范围：先只 durable 化 inbox 一条轨，chat/cron/cluster 延后 |
| 记忆总线过度设计 | 3.1 超过两周未收口 | 砍到只做"唯一写入口 + supersede"，读侧统一延后 |
| 手感冲刺无底洞 | bench 分数停滞两周 | 冻结 context_pipeline，转攻错误可读性等确定性收益项 |
| 单人倦怠 | dogfood 日志连续两周空白 | 主动降速一周，只修 bug 不做新项 |

## 附C：明确不做清单（防止范围蔓延）

- 不自建技能市场 / 不做多租户 / 不做云托管版（0.7 前）
- 不补 GAP 报告中除 Phase 3.3 三块外的 57 个前端缺口
- 不新增第 8 种及以后的渠道适配器（0.7 前）
- 不做移动端
