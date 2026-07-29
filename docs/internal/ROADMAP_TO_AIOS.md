# Takton → AIOS 终点路线图

> **产品定义**  
> Claude Code = AI 程序员；Takton = **用户的 AI 公司操作系统**。  
> Linux 管机器，Takton 管智能：运行在传统 OS 之上的 **AI-native Operating Environment**。

> **终局一句话**  
> 以 **Agent Kernel** 为核心，把 Agent 从一次性工具调用升级为拥有身份、记忆、目标、权限与组织关系的**长期数字员工**；前端不是聊天窗，而是**管理 AI 公司的工作空间**。

> **四核护城河**（缺一不可）  
> 1. Agent Kernel（生命管理）  
> 2. Event + Memory（持续存在）  
> 3. Organization（协作体系）  
> 4. AI Workspace（用户感知）

**关联**：`ROADMAP_0.4.5_to_0.6.md`（近程）· `ROADMAP_AIOS_OS_FULL.md`（Host/事件工程）· 本文（**产品+架构到终点**）

---

## 0. 终局六层架构（目标态）

```
Human
  │
Experience Layer ── AI Company Workspace（晨报/组织/审批/员工/目标）
  │
Organization Layer ── 编制 · 汇报 · KPI · 派活
  │
Takton Kernel ── Identity · Process · Cap · Mediate · Audit · Domain Events
  │
AI Runtime ── Loop · Inbox/Dispatcher · Goal Engine · Skill Lifecycle · Memory
  │
Integration ── MCP · Browser · Git · Sandbox · APIs
  │
Host OS ── Linux / macOS / Windows
```

**不做什么**：魔改 Linux、换栈为「更像 OS」、公有多租户抢跑、静默 auto_apply 编制。

---

## 1. 现状锚点（2026-07-29）

| 四核 | 约完成度 | 已有 |
|------|----------|------|
| Kernel | 55–65% | process/cap/mediate/budget/hash · Host-first 入口 |
| Event+Memory | 30–40% | 领域事件+WS · Identity memory 权威 · 非全量 event-sourcing |
| Organization | 40–50% | 员工/工单/审批 · dispatcher · 组织边 |
| AI Workspace | 35–45% | 主轨收束 · 高级页降级 · **晨报叙事待成型** |

综合相对 GPT 终局 AIOS：**~35–45%**。  
自家 0.6 可离家班子：**代码 ~80%**（人工验收另计）。

---

## 2. 版本火车（到终点）

```
NOW 0.4.6-alpha + 0.5/0.6 预览
 │
 ├─ P0  0.6.0-alpha     可离家班子（产品底线）
 │
 ├─ P1  0.7.0-alpha     AI 组织可感（Workspace 晨报）     ◄── 本迭代开工
 │
 ├─ P2  0.8.0-alpha     持续存在加深（事件谱系 + 经验记忆）
 │
 ├─ P3  0.9.0-alpha     Goal-first 经营 + Skill 生命周期
 │
 ├─ P4  1.0.0-alpha     AIOS 内测：四核齐 · 多客户端
 │
 └─ P5  1.x             远程节点 / 协议增强 / 可选边缘部署
```

| 版本 | 用户一句话 | 工程重点 | DoD（可感知） |
|------|------------|----------|----------------|
| **0.6** | 离家派活还能续 | Durable · 死信 · 停止 · 主路径 | kill 恢复手测 · 无码走通 |
| **0.7** | 早上看公司在干啥 | **晨报 Workspace** · 待批/在跑/完成叙事 | 首页不再像 Chat 启动页 |
| **0.8** | 员工有履历有经验 | 事件全谱 · Memory 经验面 · 回放入口 | 「这员工为什么变强」可答 |
| **0.9** | 有目标在经营 | Goal Engine 一等 · Skill 包生命周期 | Goal→工单→汇报闭环 |
| **1.0** | 这是我的 AI 公司 OS | 四核文档+测试门 · CLI/桌面/Headless 同等 | 对外可讲 AIOS 内测 |
| **1.x** | 多端连同一 Kernel | 远程/协议 0.2 · 仍可不做多租户 | 换壳不换核 |

---

## 3. 分阶段 WBS

### P0 · 0.6.0 — 可离家班子（底线，可与 P1 并行测）

| ID | 项 | 类型 |
|----|-----|------|
| P0.1 | 主路径 E2E 无码 | 验收 |
| P0.2 | 杀后端/kill-9 对照 CRASH_RECOVERY | 验收 |
| P0.3 | 死信/停止/并发已落地 | 代码✓ |

### P1 · 0.7.0 — AI 组织可感（**当前开发波次**）

| ID | 项 | 产出 |
|----|-----|------|
| **P1.1** | 驾驶舱 → **组织晨报** | ✅ `OrgMorningBrief` + `workspace/brief` |
| **P1.2** | 晨报 API `workspace/brief` | ✅ + CLI `takton brief` |
| **P1.3** | 员工入口叙事：「管理员工」 | ✅ 编制页文案 + 联系 TA + 晨报链 |
| **P1.4** | 审批=老板桌 | ✅ Boss desk 横幅 + 工作台链 |
| **P1.5** | Chat 降权为联系员工 | ✅ 导轨顺序 + 空态条 + nav 文案 |

**P1 DoD**：打开首页 5 秒内理解「组织昨夜干了啥、谁卡在审批」→ **代码交付完成**（主人验收另计）。

### P2 · 0.8.0 — Event + Memory 加深

| ID | 项 |
|----|-----|
| P2.1 | 领域事件扩全谱：memory.* · goal.* · skill.* · employee.* |
| P2.2 | 组织活动时间线（产品语言回放，非仅 hash dump） |
| P2.3 | Identity 经验记忆列表/检索 UI（主入口已有 CrewMemoryHub） |
| P2.4 | 逐步 event-sourcing 叙事（表仍权威；事件可重建「故事」） |

### P3 · 0.9.0 — Goal + Skill 生命

| ID | 项 |
|----|-----|
| P3.1 | Goal 升主轨（或晨报一等卡）：Goal→拆单→工单→汇报 |
| P3.2 | Skill 生命周期：Generated→Sandbox→Eval→Approved→Active→Deprecated |
| P3.3 | 进化提案与 Skill 生命周期合并叙事 |

### P4 · 1.0.0 — AIOS 内测门

| ID | 项 |
|----|-----|
| P4.1 | 四核验收清单全绿（产品+测试） |
| P4.2 | 多客户端同等：Desktop · CLI · Headless · protocol |
| P4.3 | 依赖方向与 adapters 绞杀者完成度 ≥ 约定阈值 |

### P5 · 1.x — 扩展（非阻断）

远程节点 · 协议 0.2 · 边缘设备 · 仍可不做公有多租户。

---

## 4. 产品心智演进

| 阶段 | 用户记住的词 |
|------|----------------|
| 0.6 | 员工 · 工单 · 审批 |
| 0.7 | + **公司晨报**（仍三词内核） |
| 0.9 | + **目标**（经营） |
| 1.0 | 管理 AI 公司（OS 隐喻对外可选） |

工程内部可用 Kernel/Event；**主人界面不弹 OS 黑话**。

---

## 5. 成功判据（终点）

用户能自然做到：

1. 早上打开 Workspace，看到组织完成与待批，而不是空白输入框  
2. 点进员工，看到职责、记忆、在办工单、历史结果  
3. 批权限/进化后系统行为可预期  
4. 关控制台窗口，班子仍在跑  
5. 用 CLI/脚本派活与看状态，与桌面同一 Kernel  

技术判据：

- Kernel 门禁无旁路危险能力  
- 关键业务状态 durable  
- 领域事件可订阅、可续订  
- 多客户端不撕裂状态  

---

## 6. P1 交付清单（整包完成）

- [x] 本路线图文档  
- [x] `GET /kernel/workspace/brief` 晨报聚合  
- [x] 驾驶舱 UI：`OrgMorningBrief` 组织晨报布局  
- [x] CLI `takton brief`  
- [x] 事件失效 `workspace-brief`  
- [x] 员工页「管理员工」叙事 + 联系 TA  
- [x] 审批页「老板桌」叙事 + 待决横幅  
- [x] Chat 降权：导轨 Contact、空态引导回员工/工作台  
- [x] nav：工作台 / 联系员工  
- [x] CHANGELOG / 手册索引更新  

---

## 7. 一句话

> **先让用户感到 AI 公司，再让系统配得上这个感觉。**  
> Kernel 已有底座；终点靠 **Workspace 叙事 → 事件与记忆深度 → Goal/Skill 生命 → 多客户端一等** 四段火车抵达，不靠换栈。
