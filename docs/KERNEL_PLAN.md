# Agent Kernel 规划与路线

> feature/agent-kernel 分支 · v0.4.0-alpha
> 本文档随 kernel 演进持续更新。

## 现状（Alpha）

Kernel 作为观测层 + 拦截层接入 Loop：

- **进程模型**：每个 run/工单一个 kernel process（capabilities、token_budget、meta）
- **拦截**：工具调用经 `kernel.mediate()`（权限/HMAC 签名/能力检查）
- **预算**：loop 在 LLM 调用后主动 `charge_tokens`，超限 raise → run 硬中断
- **Workforce**：identity registry + inbox + dispatcher 异步唤醒 + evolution engine
- **异步兜底预算**：身份未设预算时挂 `agent_workforce_fallback_budget`（默认 50000）

## Alpha Review 处置（2026-07-27 外部评审四条）

### #1 Kernel↔Loop「搭桥非融合」—— Phase 2 待办

评审准确，附澄清：预算超限**已硬中断**（`KernelBudgetExceeded` → run 中止，
非仅记录）。但两处属实：

- charge 是**事后记账**（LLM 先烧后扣），不能事前预防单次大调用
- Scheduler / suspended 是死代码——loop 仍用 session lock 串行，
  未实现挂起/恢复

**Phase 2 待办**：
1. loop IterationBudget 接 kernel scheduler 节奏（而非 session lock）
2. LLM 调用前预估预算检查（事前刹车，而非只事后中断）
3. suspended 状态的挂起/恢复语义落地

### #2 Dispatcher 每工单新建 Loop —— 中期待办

属实，短期可接受（功能正确，仅初始化开销）。

**中期待办**：WorkforceWorker 池——worker 持长生命周期 loop 实例，
工单到来只切 session/prompt，复用 repo / tool registry / LLM service。

### #3 Evolution 阈值硬编码 —— ✅ 已修复（2026-07-27）

6 阈值全部参数化进 settings（`agent_evolution_*`，默认值与 alpha 常量一致），
`evolution_engine._threshold()` 读配置、模块常量仅兜底。
研发型/运营型身份可按需调整。

### #4 Identity Memory 与 RAG/Wiki/memory_graph 断开 —— 中期待办

属实：identity memory（persona/duty/experience/preference/methodology）
只在 dispatcher 构造 prompt 时硬注入开头，不参与检索；agent 执行中
无法自动召回相关身份记忆。现有 RAG（向量）、Wiki（图谱）、memory_graph
三套记忆互不相通，identity memory 是第四套孤岛。

**中期待办**：identity memory 条目 embedding 纳入 RAG 检索范围，
与三套记忆系统打通（工具执行中可检索召回，而非仅 prompt 硬注入）。

## 打磨方向（不阻塞 alpha 试用）

1. **三设备实机验证**：Mi310p Arch、Mac、Win Xeon——
   上传工单 → 主 agent 审批 → dispatcher 唤醒 → 报告落库可见
2. **前端 Workforce 页面**：身份 CRUD、工单列表/上传、报告时间线、
   演化建议审批面板
3. **阶段 4（治理 UI）**：预算面板、kernel 监控页（/proc 风格）
4. **身份能力 UI 声明**：workforce 页创建身份时勾选能力
5. **优化源码移植**：外部优化代码逐个评估移植
   （规则：先跑测试确认行为变化，看不懂的不合）
