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

> **实际完成日期：2026-07-30**（关账条件见下；dogfood 日志骨架已建，真实任务条目由本人持续追加）

### 1.1 安全 Critical 回归测试化（第 1 周，AI 主力）

第一轮审计 11 个 Critical 逐条转为 pytest 回归测试，放入 `backend/tests/security/`：

- [x] `test_shell_injection.py`：shell 工具注入面（命令拼接、管道、反引号、`$()`、编码绕过）
- [x] `test_sandbox_path_escape.py`：沙箱路径绕过（`..`、符号链接、UNC 路径、Windows 短文件名）
- [x] `test_tool_auth.py`：loopback 信任边界 + JWT 伪造/无 exp 拒绝（工具执行统一走认证依赖）
- [x] `test_sql_injection.py`：`_assert_sql_ident` 白名单守卫（仓库层 ORM；raw SQL 标识符冻结）
- [x] `test_channel_gateway_input.py`：channel_gateway 去重/平台探测/@ 清理不变量
  —— 入站长度/注入过滤仍为已知缺口，记入后续 Phase，不阻塞 1.1 关账
- [x] 验收：以上测试全绿进 CI（`backend-ci` 单独 step `Security regression suite`）

### 1.2 已知并发缺陷修复（第 1 周）

第二轮审计遗留 High 项，**按本仓库真实路径重映射**（审计原文中的
`tool_execution.py` / `orchestrator.py` / `session_manager.py` 不存在）：

- [x] L2-H1 超时竞态：`tool_round._await_with_timeout_cleanup` — 超时后 `cancel()` + `await` 清理
- [x] L2-H2 压缩风暴：`context_pipeline` `max_l5_retries=3` + 超阈值 `_hard_truncate` + 告警
- [x] L2-H3 orchestrator `_state_lock`：**N/A**（本仓库无该模块；dispatcher/kernel 另有并发模型）
- [x] checkpoint 原子语义：DB 键级 `merge_config_keys`（非文件 temp+replace；崩溃不半截写）
- [x] L2-M1/M2/M3：checkpoint/goal 并发不互盖（`test_session_config_merge`）
- [x] 验收：`backend/tests/test_phase1_concurrency.py` + `test_session_config_merge.py`

### 1.3 CI 加固（第 2 周）

扩展 `.github/workflows/backend-ci.yml` 并新增前端 CI：

- [x] `ruff check backend/`：规则集 `F,I,B,C4`（聚焦真 bug；E/W 全家风格噪音后置）
- [x] `mypy` 渐进式 typed core（含 agent/kernel 子集：checkpoint/turn_retry/capability/signing…）
- [x] `frontend-ci.yml`：`tsc --noEmit` + `eslint` + `next build`
- [x] pyproject.toml `[tool.ruff]` / `[tool.mypy]` 配置节，本地与 CI 同一套规则
- [x] 验收：feature/agent-kernel CI 门禁（ruff/mypy/pytest/security/version/frontend）

### 1.4 工程卫生（第 2 周，AI 主力）

- [x] 版本号单一来源：`backend/VERSION` + `scripts/sync_version.py --check`（CI 门禁）
- [x] 根目录报告类 MD 归档至 `docs/archive/2026-07/`，根目录保留
      README / CHANGELOG / AGENTS / PLAN（`ALPHA_AUTHORITY` → `docs/`）
- [x] `llms.txt` 与实际能力对齐一次；`docs/internal/DOGFOOD_LOG.md` 骨架建立

**Phase 1 关账验收**：CI（含 lint/type/前端）全绿；安全回归测试目录存在且全绿；
用 Takton 跑一次真实开发任务，全程无因 1.2 所列缺陷导致的中断（dogfood 持续记入日志）。

---

## Phase 2：统一 Run + Durable Runtime（第 3-6 周）→ 0.5.0-alpha

目标：消灭 G1（双轨心智）/G2（重启丢单）/G3（Run 不统一）。
这是全项目最重要的一次重构，**本 Phase 期间新功能完全冻结**。

> **2.1 切片开工：2026-07-30**（演进 `agent_runs`，不平行新建 `runs` 表）
> **实际完成日期：2026-07-30**（2.1–2.4 工程关账：kill-恢复测试 + 四 origin 统一 /runs + 前后端全量绿）

### 2.1 统一 Run 实体（第 3 周，本人设计 + AI 实现）

- [x] 设计文档先行：`docs/design/RUN_UNIFICATION.md`，一页纸定义：
  ```
  Run {
    id, origin: chat|inbox|cron|cluster|subagent|headless,
    status: 细粒度 phase SM（对外映射 pending|running|waiting_approval|…）,
    session_id, identity_id?, parent_run_id?,
    checkpoint (JSON), budget {token_limit, token_used},
    created_at, updated_at, finished_at(=ended_at)
  }
  ```
- [x] 演进 `agent_runs`（非新建 run.py 表）+ alembic `0002_run_unification` +
      `AsyncAgentRunRepository`（别名 `AsyncRunRepository`）
- [x] run_recorder 经 `build_create_payload` 写 origin/identity/parent/budget；
      权威仍在 DB，recorder 内存为缓存
- [x] 状态机门面 `backend/agent/run_lifecycle.py`（origin 推断 / public_status /
      transition 校验入口）；细粒度合法表仍在 `run_state.py`
- [x] process.meta.run_id 回写；`test_run_unification` 绿（2.1 关账）

### 2.2 四条触发路径接入统一 Run（第 4 周）

- [x] chat（websocket `_run_origin=chat` → loop 创建 Run）
- [x] inbox（dispatcher `_run_origin=inbox|cron`；process.meta.run_id；`inbox.attach_run_id`）
- [x] cron（enqueue source=cron + payload；经 dispatcher 落 origin=cron Run）
- [x] cluster（父 AgentRun origin=cluster + 子 Run parent_run_id；与 ClusterRun 互链）
- [x] 验收：`GET /runs` / `?origin=` 可列四类执行

### 2.3 Durable 化（第 5 周）

- [x] 每次迭代结束将 checkpoint 写入 Run（iteration、messages 摘要指针、goal 状态、
      mode），复用现有 checkpoint.py 机制但权威落到 `agent_runs.checkpoint`（session 双写兼容）
- [x] 启动时 recovery：扫描非终态 Run → 标记 interrupted →
      按策略处理（inbox/cron/headless 自动续跑，chat/subagent/cluster 仅标记）；
      续跑走现有 `resume.py` 的续跑提示词机制（`run_recovery.py` + main lifespan）
- [x] 配置开关：`agent_run_auto_recover`（默认 true；`TAKTON_TEST_MODE` 下不 auto-resume）
- [x] **验收（本 Phase 核心）**：集成测试
      `backend/tests/test_durable_run_recovery.py`（checkpoint 权威落列 + kill-9 标记 interrupted +
      inbox auto-resume + chat 不自动 resume + SM 合法迁移）

### 2.4 收敛双轨心智（第 6 周）

- [x] 心智统一为一句话：**"一切执行都是 Run；Identity 是执行者；
      Cluster/SubAgent/Hire 是 Identity 的三种编排形态；Workflow 是 Run 模板"**
- [x] 落地动作（不做大爆炸重写，做归位）：
  - subagent_runner / cluster 执行入口统一收 origin + parent_run_id 的 Run 创建路径
  - workflow_engine 的节点执行派生子 Run（父 Run origin=cluster/workflow 链）
  - Goal 挂在 Run 链上（`goal_state.run_id` + `bind_goal_run_id`）而非独立心智
- [x] `docs/design/EXECUTION_MODEL.md`：一页纸讲清执行模型
- [x] 顺带拆分：loop.py phases 化 + `loop_io`/`loop_cluster`/`loop_tools` mixin，本体 <1500 行；
      manage_tools 按域拆为 `manage_crew_tools` / `manage_integration_tools` /
      `manage_ops_tools` + `manage_common`（`manage_tools.py` 兼容 re-export）

**Phase 2 关账验收**：kill -9 恢复测试绿；四来源统一进 /runs；
用 Takton 布置一个隔夜长任务（如"审计 X 模块并出报告"），睡前启动、早上收结果。
（工程验收以 `test_durable_run_recovery` + `test_run_unification` + 前后端全量 CI 为准；
隔夜手测为 alpha 体验项，不阻塞 2.x 关账。）

---

## Phase 3：记忆总线 + 权限一张网 + 前端补缺（第 7-10 周）→ 0.5.x

目标：消灭 G4（记忆多套）/G5（权限散落），并让已有后端能力"被看见"。

> **开工：2026-07-30** · 详规 `docs/design/PHASE3_EXECUTION_PLAN.md`  
> **实际完成日期：2026-07-30**（工程关账；PPT dogfood 体验项由本人持续验证）

### 3.1 记忆总线（第 7-8 周）

- [x] 写入权威规则（定死，写入 `docs/design/MEMORY_BUS.md`）：
  | 记忆类型 | 权威存储 | 谁能写 | 冲突裁决 |
  |---|---|---|---|
  | 人格/经验（identity memory） | identity_memory 表 | 本 Identity + 审批 | supersede 版本链 |
  | 事实/实体 | entities + memory graph | 任何 Run（经总线） | supersede 版本链 |
  | 文档知识 | wiki | 用户 + 审批后的 agent 写入 | 人工 |
  | RAG/向量 | Qdrant | **仅索引层**，随权威变更同步，不独立写 | 不适用 |
- [x] `backend/services/memory_bus.py`：唯一业务写入口
      `remember/recall/supersede`；`memory_tools` + `CrewMemoryWriter` 归口总线；
      identity 底层 API 保留供总线调用；RAG 仍随 identity 写入同步
- [x] supersede 推广到 graph 节点（soft）与 entities（archive + 新版本）
- [x] 读侧统一：`recall(query, kinds, top_k)` 跨源检索并标注来源与新鲜度
- [x] 验收：`test_memory_bus`（remember/recall/supersede 隐藏旧版）；
      既有 crew/graph/authority 回归保持绿。Wiki 人类导入可直写（文档约定）。

### 3.2 权限一张网（第 9 周）

- [x] 四层规则合并为 `backend/kernel/permission_court.py`；
      `kernel.mediate()` 走 `decide_capability`；工具路径 `tool_hooks` 走 `decide_tool`
- [x] 每次决策输出可解释记录：`{tool, args_digest, verdict, matched_rule, layer}`
      进 `policy.decision` 哈希链
- [x] 决策优先级：secret floor deny > 用户 deny > skill > path > steward >
      user allow > profile > session_grant > ask
- [x] 验收：`test_memory_bus` 中 court 字段用例 + 既有权限测试回归

### 3.3 前端补缺（第 10 周，只补三块，其余 60 个缺口接口明确不补）

- [x] **Evolution**（`/evolution`）：draft 预览 + apply/reject + enable；
      受控 approve/rollback 仍在审批中心（双系统不混）
- [x] **Kernel**（`/kernel`）：进程表 + 审计/policy（展示 layer/rule）+
      suspend/resume HTTP 与按钮
- [x] **Run 时间线**（`/tasks`）：全局 Run 列表/详情/checkpoint/续跑；会话 Task 板次 tab
- [x] localeStore 拆为 `frontend/locales/zh.json` + `en.json`；
      GAP 报告 2 处路径经复核为扫描假阳性（enable/disable 与 rebuild-index 均存在）
- [x] 验收：三页代码就绪；全量 FE lint/tsc/build + 本地联调冒烟

**Phase 3 关账验收**：用真实 PPT 工作流验证记忆——本周告诉它公司 PPT 的风格偏好，
下周新会话它能自动应用；权限面板上能解释它为什么被允许读某目录。
（工程侧：`memory_bus.remember(preference)` + court 可解释审计已具备；dogfood 由本人补条目。）

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
