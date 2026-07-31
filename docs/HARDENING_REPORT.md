# Hardening 完成报告（四类债 · 第 1 条）

**日期**：2026-07-31  
**状态**：✅ 已落地  
**范围**：工具路径全量 mediate（绕过=0）· computer spawn 强制 isolation + 资源账户 · Windows Job 硬绑 · workforce 无沙箱 fail-closed

---

## 1. 目标对照

| ID | 工作项 | 状态 |
|----|--------|------|
| h1 | 工具路径全量 mediate 审计（绕过=0） | ✅ |
| h2 | computer spawn 强制 isolation + 资源账户 | ✅ |
| h3 | child_proc / 资源与 OS Job 硬绑（Windows） | ✅ |
| h4 | workforce 无沙箱 fail-closed 文案 | ✅ |
| h5 | 测试 / smoke / 本报告 | ✅ |

---

## 2. 关键改动

### 2.1 中央门控 `backend/kernel/tool_gate.py`

- `enforce_tool_gate(name, args)`：统一 mediate + `tool_calls` / `child_proc` charge
- **幂等**：`_tool_gate_passed` 防 double charge（loop → registry 双层）
- **fail-closed（Agent 上下文）**：带 `_session_id` / `_workforce` / `_require_kernel_process` 但无 process → 拒绝
- **兼容（单测/脚本）**：无 Agent 上下文且无 process → 放行
- `workforce_sandbox_fail_message()`：编制无沙箱统一文案

### 2.2 接线点（绕过清零）

| 路径 | 门控 |
|------|------|
| `loop_tools._execute_registered_tool` | `enforce_tool_gate` + 编制扩权 / 主人 escalate |
| `ToolRegistry.execute` | 入口强制 `enforce_tool_gate` |
| `tool_round` 遗留 SkillRegistry | 执行前 `enforce_tool_gate` |
| `tool_round` DB skill / Tool | validated_args + Registry（含 gate）或 dynamic + gate |
| MCP adapter | 既有 `mcp_call` mediate（保留） |

### 2.3 Computer / Job

- `ComputerManager._make_backend`：workforce / `sandbox_required` 禁止落到 `local`；无沙箱 → 统一 fail-closed 文案
- `ComputerManager._job_limits_from_resources`：从 `resource_usage` 映射
  - `memory_bytes.limit` → Job `ProcessMemoryLimit` + `JobMemoryLimit`
  - `child_proc.limit/remaining` → `ActiveProcessLimit`
- `JobBackend`：增加 `JOB_MEMORY` 限额标志
- `execute()`：spawn 前核对 child_proc 超限；isolation deny / 非 sandboxed workforce → fail-closed
- `executors.execute_command`：编制/强制沙箱失败时用统一文案，**不降级本机裸跑**

---

## 3. 验收

```text
# 单元 + 门控策略
pytest backend/tests/kernel/test_hardening_tool_gate.py -q

# 既有隔离 / 资源（回归）
pytest backend/tests/kernel/test_p0c_scheduler_resources.py backend/tests/kernel/test_p0d_court_isolation.py -q
pytest backend/tests/test_sandbox_backends.py -q
```

期望：

- Agent 上下文无 process → 工具结果含拒绝文案，工具体未执行
- 有 process + 未授权工具 → mediate deny，无 resource charge
- 授权工具 → charge `tool_calls`；`command` 等再 charge `child_proc`
- workforce + detect.mode=none → `RuntimeError` / 工具错误含 fail-closed，不出现 LocalBackend
- Windows Job 限额随资源账户收紧（映射单测）

---

## 4. 诚实边界

| 项 | 说明 |
|----|------|
| Job ≠ 完整 FS 沙箱 | 进程树 + 内存/进程数管控；完整隔离仍需 WSL+bwrap / bwrap / seatbelt |
| 逻辑 memory_bytes | 上下文/结果账户，与 OS 物理 RSS 非同一计量；Job 限额是其 OS 侧上界 |
| 后台 process 工具 | `start_background` 仍走 process_registry；后续可再并入 Job |
| Electron 打包 | 不在本债范围（按既定排期） |

---

## 5. 后续三条债（未开）

1. ~~硬化~~ ← 本报告  
2. 观测 / Eval 周报打穿  
3. 包市场 / 签名扫描产品化  
4. WASM skill 运行时加深（fuel 以外）

---

## 6. 文件清单

- 新增：`backend/kernel/tool_gate.py`
- 新增：`backend/tests/kernel/test_hardening_tool_gate.py`
- 新增：`docs/HARDENING_REPORT.md`
- 修改：`backend/tools/registry.py`
- 修改：`backend/agent/loop_tools.py`
- 修改：`backend/agent/phases/tool_round.py`
- 修改：`backend/computer/manager.py`
- 修改：`backend/computer/job_backend.py`
- 修改：`backend/services/tools/executors.py`
- 修改：`backend/kernel/__init__.py`
