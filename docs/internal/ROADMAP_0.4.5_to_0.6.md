# Takton Alpha 线：0.4.5 → 0.6 内部开发方案

> **定位（主人决策）**  
> - **main（0.3.x）**：继续公开演进，打磨「传统 Agent 终端」终极形态；发布 GitHub。  
> - **alpha（0.4.x→0.6）**：**不发布 GitHub**，自家私有线，押 **AIOS 内核**（编制 / 调度 / 权限 / 记忆 / 受控进化）。  
>
> **基线**：`takton-v0.4.0-alpha/takton-feature-agent-kernel` @ **0.4.5-alpha**（2026-07 内核正确性 + FE 收口批次）。  
> **原则**：收束主路径 > 堆功能；可靠性默认路径 > 演示页；概念只保留用户能记住的 3 个词。

---

## 0. 战略边界

| 项 | 约定 |
|----|------|
| 仓库 | alpha **本地/私有**，不 push 公开 GitHub（与 main 分离） |
| 用户心智（alpha） | **员工 Identity → 工单 Inbox/Task → 审批 Approvals** |
| 不做（0.6 前） | 公有多租户 SaaS、无审批全自动进化、第五套记忆系统、为发版而发版 |
| 与 main 关系 | **代码可单向吸收**（main bugfix/工具收敛可 cherry-pick 进 alpha）；alpha **不反向污染** main 公开叙事 |
| 成功判据 | 「离家也能派活、回来能续上、权限可解释、记忆不精神分裂」——不是页面数量 |

---

## 1. 现状盘点（0.4.5-alpha 已有 / 缺口）

### 1.1 已具备（AIOS 骨架）

| 域 | 模块/能力 | 成熟度 |
|----|-----------|--------|
| 进程/编制 | `kernel/{identity,process,capability,kernel}`，budget charge，parent 委托 | 中高 |
| 多 worker | `shared_store` Redis、claim/escalation TTL、events 合并 | 中（刚修） |
| 调度 | `dispatcher` + `inbox` + workforce org/report | 中 |
| 权限 | escalation、approval_rules、desktop permission、mediation | 中 |
| 进化 | EvolutionEngine 述职提案 + TEE 草稿/策展（人批） | 中低→中 |
| 执行 | loop/tools/skills/MCP、cluster、cron-hook→subagent、runs | 中 |
| 产品壳 | agents 派活、approvals、evolution ops、chat runs、kernel 页 | 中（刚接线） |
| 质量债处理 | 租户过滤、搜索预算/Jaccard、会话 sync resume（若已合入 alpha） | 视分支同步情况验收 |

### 1.2 明确缺口（相对 0.6「可离家 AIOS」）

| ID | 缺口 | 影响 |
|----|------|------|
| G1 | **双轨心智**：Identity / SubAgent / Hire / Cluster / Workflow / Goal 并存 | 无法当「系统」用 |
| G2 | **调度非 durable**：进程内队列为主，重启丢单/重试/死信不完整 | 不能宣称 OS |
| G3 | **Run 不统一**：chat / inbox / cron / cluster 状态机各说各话 | 可观测碎 |
| G4 | **记忆多套**：identity memory / entities / graph / wiki 写入权威不清 | 员工「不记得」 |
| G5 | **权限未一张网**：kernel caps + desktop + security 策略 + audit 未统一事件 | 不可解释 |
| G6 | **主路径 E2E 脆**：招人→派活→执行→提权→日报 未产品级验收 | 演示≠能用 |
| G7 | **自演进闭环浅**：提案/草稿/apply/rollback 可演示但不「一周不炸」 | 进化像玩具 |
| G8 | **工程卫生**：与 main 分叉、版本/包仅自用、测试网未绑主路径 | 长期债 |

### 1.3 0.4.5 收尾项（进入 0.4.6 前建议清零）

- [ ] 会话切页 resume + search 预算：**与 main 0.3.4 对齐验收**（alpha 若未合齐则先合）
- [ ] Inbox / Evolution / Desktop permission / Runs：**冒烟清单书面化**（见附录 A）
- [ ] 版本文件已统一 0.4.5-alpha；**CHANGELOG 补「内部」条目模板**
- [ ] 明确 **默认 single_user_mode=True**、Redis shared 默认关/开的运维说明（本机笔记即可）

---

## 2. 版本地图总览

```
0.4.5-alpha  ──基线（正确性+收口）
     │
0.4.6-alpha  ──「主路径能走通」Product Spine
     │
0.5.0-alpha  ──「可离家」Durable Runtime
     │
0.5.x-alpha  ──加固（记忆总线 / 权限一张网 / 观测）
     │
0.6.0-alpha  ──「有记忆的班子」AIOS 内测可用
```

| 版本 | 主题一句话 | 建议周期* |
|------|------------|-----------|
| **0.4.6** | 只保留一条主路径，E2E 打穿 | 1–2 周 |
| **0.5.0** | 工单/进程跨重启仍在 | 2–4 周 |
| **0.5.1–0.5.3** | 记忆总线 + 权限事件 + 观测 | 3–5 周 |
| **0.6.0** | 编制日常可用 + 进化可信 | 2–3 周 |

\*一人全职约日历时间；可按主人节奏伸缩，**顺序不要倒**。

---

## 3. 分版本工作包（WBS）

### 3.1 0.4.6-alpha — Product Spine（主路径）

**目标**：新用户（或主人自己）10 分钟内完成：招人 → 派一条活 → 看到结果/失败 → 批一次权 → 看日报摘要。

#### A. 信息架构（必做）

| 工作项 | 说明 | 产出 |
|--------|------|------|
| A1 导航收敛 | 侧栏突出：对话 / 员工 / 审批 / 内核(高级) / 设置；Cluster/Workflow/Market **降级高级或隐藏** | 导航配置 + 文案 |
| A2 概念表 | 对外文案只保留 Identity=员工、Inbox=工单、Approval=审批；SubAgent 映射为「员工模板/技能包」 | `docs/internal/concepts.md` |
| A3 空态与引导 | agents/approvals/inbox 空态 CTA 串成 wizard | FE |

#### B. 主路径 E2E（必做）

| 工作项 | 说明 | 产出 |
|--------|------|------|
| B1 Hire→Identity | 招聘向导结果 **必落 Identity**，与 SubAgent 档案关系写死（1:1 或模板引用） | BE+FE |
| B2 派活闭环 | Inbox enqueue → dispatcher claim → loop/process → status/result 回写 | 已有则补洞 |
| B3 提权闭环 | 工具被拒 → escalation → 审批中心 → 重试成功 | E2E 脚本 |
| B4 日报 | workforce report 在 agents 或 kernel 一键可见、可读 | FE |
| B5 稳定性门禁 | 切页不丢跑；搜索不连环；派活 503/空 inbox 有人话错误 | 回归表 |

#### C. 工程（必做）

| 工作项 | 说明 |
|--------|------|
| C1 主路径 pytest + 1 条 playwright「招人派活」 | 可 mock LLM |
| C2 内部 CHANGELOG 0.4.6 | 不发 GitHub |
| C3 配置剖面 `profile=aios-dev` | 打开 kernel/inbox/evolution 默认 |

**0.4.6 完成定义（DoD）**  
- [ ] 主路径脚本/手测 3 次无阻断  
- [ ] 侧栏不再并列 10+ 同级「产品」  
- [ ] 无新增大功能面  

---

### 3.2 0.5.0-alpha — Durable Runtime（可离家）

**目标**：杀后端 / 重启桌面后，**未完成工单可恢复**；cron/派活不靠「进程碰巧活着」。

#### D. 持久化调度

| 工作项 | 说明 | 优先级 |
|--------|------|--------|
| D1 Inbox 状态机落库/Redis 权威 | pending/claimed/running/done/failed/dead + 租约续期 | P0 |
| D2 Dispatcher 重启回收 | 启动 `reclaim_stale`、超时、最大 attempts → dead letter | P0 |
| D3 死信台 | UI 列表 + 重放/丢弃 | P1 |
| D4 Cron-hook / 派活统一「Job」视图 | 最少：来源、关联 process/run、状态 | P1 |
| D5 单机 SQLite 路径 | **已拍板（2026-07-28）**：默认 SQLite 权威；Redis 仅可选多 worker 热共享（见 `docs/internal/STORAGE.md`） | P0 已决 |

#### E. 统一 Run/Process

| 工作项 | 说明 | 优先级 |
|--------|------|--------|
| E1 映射表 | chat run / kernel process / cluster task / cron 执行 → 统一 ID 或关联 | P0 |
| E2 UI「运行中」 | 全局或员工维度：正在跑什么、能否停 | P0 |
| E3 Resume | checkpoint + session resume + process resume **同一套用户语言** | P1 |
| E4 停止语义 | stop = 取消 process + 取消 agent task + 工单 failed/cancelled | P0 |

#### F. 可靠性

| 工作项 | 说明 |
|--------|------|
| F1 断线 buffer | 可选：断线期间 status/tool 事件落热存储，重连回放（增强 0.3.4 sync） |
| F2 预算/并发上限 | 每 Identity 并发 process、全局并发，防炸机 |
| F3 崩溃演练 | 文档：kill -9 后端后预期行为清单 |

**0.5.0 DoD**  
- [ ] 派活后杀后端，重启 5 分钟内工单进入终态或可续跑  
- [ ] 用户能回答：「现在系统里有几个活、卡在哪」  
- [ ] 死信不出现静默丢失  

---

### 3.3 0.5.1–0.5.3 — Memory · Policy · Observability

可并行三条泳道，但 **记忆优先于炫技 UI**。

#### G. 0.5.1 记忆总线

| 工作项 | 说明 |
|--------|------|
| G1 写入权威 | 规定：工作记忆→Identity memory；事实实体→entities；wiki 仅文档；**graph 只读投影或暂隐藏** |
| G2 读取优先级 | loop 拼上下文顺序写死并测试 |
| G3 员工记忆页 | 列表 / 作废 supersede / 来源 |
| G4 禁止新记忆后端 | 0.6 前冻结 |

#### H. 0.5.2 权限一张网

| 工作项 | 说明 |
|--------|------|
| H1 事件模型 | `policy.decision`：who/what/allow|deny|escalate、来源（kernel/desktop/security） |
| H2 审批中心 | 提权 + 进化 +（可选）高危桌面 同一 inbox |
| H3 审计只读页 | 基于 audit_store + 桌面/security，不新造库优先 |
| H4 默认策略包 | 单用户「宽松但提权可见」vs「锁死」两套预设 |

#### I. 0.5.3 可观测

| 工作项 | 说明 |
|--------|------|
| I1 Runs 全局入口 | 不依赖先开 chat |
| I2 Trace 抽样 | tool 时间线只读 |
| I3 内核仪表盘 | process 计数、escalation pending、inbox depth、token 日耗 |
| I4 结构化日志字段 | session_id / identity_id / process_id / job_id 强制关联 |

**0.5.x DoD**  
- [ ] 「这员工为什么能跑 shell」有一条审计链  
- [ ] 「它记得什么」只有一个主 UI  
- [ ] 内核仪表盘 5 秒看懂负载  

---

### 3.4 0.6.0-alpha — Crew OS（有记忆的班子）

**目标**：主人自己愿意用 alpha **连续一周**处理真实杂务（研究、定时、桌面、多员工），而不是演示完即弃。

#### J. 组织与委托

| 工作项 | 说明 |
|--------|------|
| J1 委托预算 | parent→child 预留 token、深度上限（防递归 agent_call） |
| J2 org 视图 | 已有 workforce org，绑定真实 Identity 名与状态 |
| J3 模板员工 | 研究/编码/管家 三套预置 Identity+caps+budget |

#### K. 进化可信

| 工作项 | 说明 |
|--------|------|
| K1 述职→批→apply→rollback 全链路测试 | 含失败回滚 |
| K2 TEE 草稿与编制进化 **叙事合并** | 避免两个「进化」 |
| K3 进化从不静默改 caps | 保持 auto_apply=False 硬约束 |

#### L. 日常体验

| 工作项 | 说明 |
|--------|------|
| L1 通知 | 工单完成/失败/待审批 系统通知或托盘 |
| L2 日报/周报 | 自动生成 + 一键已读 |
| L3 性能 | 本地默认模型路径下主路径延迟基线（记录，不攀比云端） |
| L4 备份 | 一键导出 kernel+会话+记忆（私有线也要防盘挂） |

**0.6.0 DoD（内测可用）**  
- [ ] 连续 7 天真实使用无不可恢复数据丢失  
- [ ] 主人无需看代码能处理：派活、停跑、批权、看记忆、看日报  
- [ ] 文档：`docs/internal/AIOS_OPERATOR.md`（仅内部）  

---

## 4. 统筹：泳道、依赖、节奏

### 4.1 依赖图

```
0.4.6 主路径 ─────────────┬──► 0.5.0 Durable ──► 0.6 组织/进化加厚
                          │
                          ├──► 0.5.1 记忆（依赖主路径 Identity 稳定）
                          ├──► 0.5.2 权限网（依赖 escalation 主路径）
                          └──► 0.5.3 观测（依赖统一 Run id）
```

**硬约束**：未完成 0.4.6 DoD，不开始 0.5.0 大持久化（否则持久化的是混乱模型）。

### 4.2 建议人力模型（单人 / 铃+主人）

| 角色 | 焦点 |
|------|------|
| 主人 | 拍板概念收敛、主路径手测、默认策略、是否 Redis 必选 |
| 铃 | WBS 实现、测试、内部文档、从 main cherry-pick 可靠性补丁 |

### 4.3 迭代节奏（建议双周）

| 双周 | 主题 |
|------|------|
| W1–2 | 0.4.6 A+B+C |
| W3–5 | 0.5.0 D+E+F |
| W6–7 | 0.5.1 记忆 |
| W8–9 | 0.5.2 权限 |
| W10 | 0.5.3 观测 |
| W11–13 | 0.6.0 J+K+L + 稳定 |

可压缩，但 **0.4.6→0.5.0 顺序不反**。

### 4.4 每版本固定仪式（内部）

1. 更新 `CHANGELOG.md` 顶栏版本  
2. 跑：kernel 单测 + 主路径手测清单（附录 A）  
3. 打 **本地标签** `alpha-0.x.y`（不必公开）  
4. 可选：打 zip 自用备份（不上传 GitHub）  

---

## 5. 与 main 线协作协议

| 规则 | 说明 |
|------|------|
| main | 传统 Agent 打磨；公开 Release；**不强制**上 Kernel/Inbox |
| alpha | AIOS；**不发布** GitHub |
| 同步方向 | main→alpha：会话 resume、搜索预算、安全沙箱、工具 bugfix **应定期 cherry-pick** |
| 禁止 | alpha 实验 API 合入 main 公开版除非主人显式批准 |
| 文档 | 本方案仅 alpha 树 `docs/internal/`；main README 不提 AIOS 路线细节 |

---

## 6. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 范围膨胀又变功能仓库 | 每版本 DoD 清单；新想法进 icebox，不进当前 milestone |
| Redis/SQLite 决策摇摆 | 0.5.0 第 0 天书面二选一 |
| LLM 不稳定导致 E2E 红 | 主路径测允许 mock；另备「真模型周测」 |
| 与 main 分叉过大 | 每月 1 次 cherry-pick 日 |
| 一人精力不足 | 砍 0.5.2/0.5.3 并行，串行记忆→权限→观测 |

---

## 7. Icebox（0.6 后才考虑）

- 多用户/多机 worker 集群生产化  
- 记忆图谱主 UI  
- 全自动进化  
- 公开 GitHub alpha  
- 移动端遥控班子  
- 与 main 强制合并为单一发行版  

---

## 附录 A — 主路径手测清单（每版本）

1. 新建员工（Identity）成功，列表可见  
2. Inbox 派活 → 状态 pending→…→done/failed，结果可读  
3. 触发提权 → 审批中心可见 → 批准后工具可续  
4. 聊天长任务：切设置再回，仍 running 或结束后消息完整  
5. 研究类提示：搜索次数收敛，有总结  
6. 杀后端重启：未完成工单可观察（0.5.0+ 必须可恢复）  
7. 进化提案/草稿：拒绝与 apply 各一次，无静默改权  

## 附录 B — 关键里程碑验收句

| 版本 | 一句话验收 |
|------|------------|
| 0.4.6 | 「这就是一个带员工和审批的 Agent 班组，不是设置博物馆。」 |
| 0.5.0 | 「我关电脑再开，工单还在，系统还知道卡在哪。」 |
| 0.5.1 | 「每个员工的记忆只有一个地方管。」 |
| 0.5.2 | 「谁允许它动我电脑，审批和审计说得清。」 |
| 0.6.0 | 「我愿意让它管一周杂事，出了事找得回。」 |

---

## 附录 C — 建议立即执行的下一动作（启动 0.4.6）

1. 主人确认：**Redis 必选 vs SQLite durable** 倾向（可先表态，0.5.0 落地）  
2. 铃开 0.4.6：导航收敛 + Hire→Identity 关系写死 + 主路径 E2E 测试骨架  
3. 把 main 0.3.4 可靠性补丁 **核对 alpha 已合齐**  
4. 建 `docs/internal/concepts.md` + 本文件作为 alpha 纲领  

---

*文档版本：2026-07-28 · 适用树：alpha 本地 · 不发布 GitHub*
