# 治理能力回落评估：alpha → main 0.3.x

**日期**：2026-07-29  
**Alpha 权威**：`E:\项目\takton-alpha`（会话包 20260729-1646）  
**Main 线**：`E:\项目\takton`（`package.json` **0.3.5**）

---

## 1. 结论摘要

| 治理能力 | 能否直接搬到 0.3.5 main | 推荐 |
|----------|-------------------------|------|
| **grounding_policy + task_grounding 扩展** | **可移植**（中等工作量） | **P0 建议移植** |
| **completion_gate soft 策略** | **可移植**（main 已有雏形） | **P0** |
| **dispatch_grounding（派单门）** | **不可直接用**（无 crew/inbox） | 改造成 **chat 入口校验** 后可移植 |
| **workforce_budget 自动抬预算** | **不可直接用**（无编制/进程预算） | 改造成 **session/run token 预算** 后可选 |
| **Budget→failed / experience 不污染** | **部分可移植** | 需 main 先有 run 终态与 experience 写入点 |
| **domain_events / protocol / A2A** | **强依赖 kernel** | **不移植**；main 保持 chat 产品 |
| **jobs/stop、死信、Identity** | **无 kernel 等价物** | **不移植** |
| **审计页 / 工作台 brief / 编制 seed** | 强依赖 alpha API | **不移植** |

**一句话**：main 0.3.x **能吃**「幻觉防护 + 完成门 soft 化」；**不能整包吃**「编制 OS / 派单 / 权限网 / 领域事件」。  
治理里**与模型行为相关的一层**可回落；**与 AIOS 内核相关的一层**应留在 alpha。

---

## 2. Main 0.3.5 现状（对照）

| 能力 | Main | Alpha |
|------|------|-------|
| `backend/kernel/` | **无** | 有（dispatcher/inbox/identity/…） |
| `crew_steward` / Identity / Inbox | **无** | 有 |
| `completion_gate.py` | **有**（fix/build/find 硬规则） | 扩展 + policy |
| `task_grounding` / `dispatch_grounding` | **无** | 有 |
| `token_budget` / `charge_tokens` / Budget Exceeded | **agent 层未见** | kernel + workforce |
| 领域事件 / protocol | **无** | 有 |
| 默认端口 | 8000（Electron 脚本） | 8090 |

Main 仍是 **个人 Agent 终端（对话 + 工具 + 上下文压缩）**，不是编制 OS。

---

## 3. 可移植模块（按推荐顺序）

### P0 — 直接有价值、依赖面小

#### 3.1 `grounding_policy.py`（几乎纯函数）

- **依赖**：仅 `os` / `re` / 可选 model name  
- **改动**：拷贝到 `takton/backend/agent/`  
- **接线**：`completion_gate` / `system_prompt` 读 `get_grounding_policy(model_name)`  
- **风险**：低  

#### 3.2 扩展 `completion_gate.py`（main 已有 + 测试）

- Main 已在 `phases/no_tool_round.py` 调用 `evaluate_completion`  
- Alpha 增量：soft 下少硬拦、fix/build 无写仍拦、`max_hard_followups`  
- **做法**：把 alpha 的 policy 分支 **merge 进 main 的 gate**，保留 main 测试并加 soft 用例  
- **风险**：中（行为变化，需回归 chat 一拨一答）

#### 3.3 `task_grounding.py` 的「报告脚注 / 路径存在性」子集

- **可移植**：`extract_cited_paths`、`project_roots`、`maybe_annotate_report`  
- **接线**：`epilogue` 终答后附加脚注（alpha 已有）  
- **跳过**：依赖 Identity memory / workforce 的部分  
- **风险**：中（路径根目录在桌面安装包 vs 源码 monorepo 不同）

### P1 — 需改造成 main 语义

#### 3.4 `dispatch_grounding.py` → **用户消息预检**（非 assign）

Main 没有 `crew_steward.assign`。可改为：

- 入口：`loop.run` 收到 user_input 时 `scan_user_instruction`（复用同一套正则）  
- block：仅对「明确不存在路径 + 要求修改该文件」类（可选，默认 warn）  
- 不引入 force 参数；设置页开关 `TAKTON_GROUNDING_MODE`

#### 3.5 会话级 token 预算「抬升」（非 workforce）

Main 无 `default_token_budget` / 进程预算。可选：

- 用 `iteration_budget` 或 settings 里已有的 run 限制  
- 或对「审计/长文」任务提高 `max_iterations` / context 窗口策略  
- **不要**硬拷 `workforce_budget.py` 整文件进 main 假装有编制

### P2 — 不建议移植

| 模块 | 原因 |
|------|------|
| `WorkforceDispatcher` / inbox / dead letter | main 无编制状态机 |
| `domain_events` / WS domain | 无 kernel `_emit` 总线 |
| `protocol` / A2A / Agent Card | 产品面不同；公有 GitHub 0.3.x 不宜半吊子协议 |
| `policy.decision` + 审批中心 | main 无 escalation 编制流 |
| `POST /jobs/stop` 统一停 | 无 inbox+process 双轴 |
| 模板员工 seed / OrgMorningBrief | 无 Identity |

若未来 main 要 OS 化，应 **升版本线或继续在 alpha 孵化**，而不是往 0.3.5 塞半套 kernel。

---

## 4. 建议移植 PR 切片（main）

```
PR-M1  grounding_policy.py + 接线 completion_gate（soft 默认）
PR-M2  task_grounding 脚注子集 + epilogue
PR-M3  （可选）user_input 预检 warn（从 dispatch_grounding 抽公共正则）
PR-M4  文档：MAIN_GROUNDING.md + 环境变量 TAKTON_GROUNDING_MODE
```

**不要**做：把整个 `backend/kernel` 拷进 0.3.5。

---

## 5. 兼容与风险

| 风险 | 缓解 |
|------|------|
| soft 过松导致假完成 | fix/build 无写仍 hard；测试保留 |
| 路径校验误杀用户路径 | soft 默认 warn；`project_roots` 含 workspace + cwd |
| 强模型被 prompt 绑死 | 默认 soft + 短 `grounding_prompt_block`（alpha 已缩短） |
| 公有仓库安全预期 | 不把 force 派毒单、A2A 放开默认带到 main |

---

## 6. 验证清单（main 移植后）

1. `pytest backend/tests/test_completion_gate.py -q`  
2. 加 soft 策略用例  
3. 手测：修 bug 只 glob → 仍 followup；闲聊 → 不打断  
4. 手测：终答含不存在路径 → 脚注提示  
5. **不**要求 main 出现员工/工单/审批导航

---

## 7. 决策建议

| 决策 | 建议 |
|------|------|
| Alpha 权威 | **已定为** `E:\项目\takton-alpha` = 会话包 |
| Main 是否合并治理 | **只合并 P0 模型治理**；OS 编制留 alpha |
| 时间点 | 0.3.6 小版本可带 grounding soft；0.4 再议是否吸收 kernel |

---

*评估基于 2026-07-29 目录快照：`takton` 0.3.5 vs `takton-alpha` session 权威。*
