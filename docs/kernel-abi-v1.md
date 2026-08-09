# Tevarn Kernel ABI v1

**ABI 版本**：`1.0.0`  
**传输**：JSON-RPC 2.0 · 每行一条 JSON（TCP 或 stdio）  
**默认端点**：`127.0.0.1:17890`（环境变量 `TEVARN_KERNEL_HOST`）  
**权威实现**：`tevarn-kernel` + `tevarn-kernel-host`（Rust）  
**Python**：`backend/kernel_rust` 客户端；`backend/kernel` 仅为兼容 shim / fallback  

---

## 1. 请求 / 响应

### Request

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "create_process",
  "params": { }
}
```

### Success

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": { }
}
```

### Error

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32001,
    "message": "human readable",
    "data": { "error": "permission", "message": "..." }
  }
}
```

### 错误码

| code | kind (`data.error`) | 含义 |
|------|---------------------|------|
| -32700 | — | Parse error |
| -32600 | — | Invalid Request |
| -32601 | — | Method not found |
| -32000 | `internal` | 内部错误 |
| -32001 | `permission` | mediate / 权限拒绝 |
| -32002 | `budget_exceeded` | 预算不足 |
| -32003 | `capability_escalation` | 能力扩大非法 |
| -32004 | `not_found` | 未知进程/申请 |
| -32005 | `invalid` | 参数/状态非法 |

---

## 2. 方法表（与 host 1:1）

### 2.1 元数据

| method | params | result |
|--------|--------|--------|
| `abi_version` | `{}` | `{ "abi": "1.0.0", "kernel": "<semver>", "runtime": "<semver>" }` |
| `list_methods` | `{}` | `{ "methods": ["ping", ...] }` |
| `ping` | `{}` | `{ "pong": true, "runtime": <health> }` |
| `health` | `{}` | runtime health 对象 |
| `register_service` | `{ name, meta? }` | `{ "ok": true }` |

### 2.2 进程

| method | params | result |
|--------|--------|--------|
| `create_process` | `identity`, `session_id?`, `parent_id?`, `capabilities?`, `token_budget?`, `meta?` | process dict |
| `end_process` | `process_id`, `state?` (`completed`/`failed`/`killed`), `reason?` | process dict \| null |
| `mark_running` | `process_id` | `{ "ok": true }` |
| `suspend_process` | `process_id`, `reason?` | process dict |
| `resume_process` | `process_id` | process dict |
| `get_process` | `process_id` | process dict \| null |
| `list_processes` | `include_terminal?` | `{ processes, total }` |
| `live_processes_for_identity` | `identity` | `{ processes, total }` |
| `retire_live_identity_processes` | `identity`, `reason?`, `except_process_id?` | `{ killed: [id...] }` |
| `gc_terminal` | `older_than_seconds?` | `{ removed: n }` |

#### Process dict

```json
{
  "id": "16 hex",
  "identity": "main",
  "session_id": null,
  "parent_id": null,
  "capabilities": null,
  "token_budget": null,
  "tokens_used": 0,
  "budget_remaining": null,
  "state": "created|running|suspended|completed|failed|killed",
  "created_at": 0.0,
  "started_at": null,
  "ended_at": null,
  "exit_reason": null,
  "meta": {},
  "token": null
}
```

### 2.3 中介与预算

| method | params | result |
|--------|--------|--------|
| `mediate` | `process_id`, `action`, `target`, `args?` | `{ allowed, reason, capability_checked }` |
| `charge_tokens` | `process_id`, `amount` | `{ remaining }` |
| `top_up_budget` | `process_id`, `amount`, `by?`, `reason?` | budget dict |
| `try_soft_renew_budget` | `process_id`, `need?`, `reason?` | dict \| null |
| `issue_token` | `process_id`, `capabilities?`, `expires_at?` | token dict |

`action` 常用：`tool_call` · `skill_exec` · `mcp_call` · `command_exec` · `subagent_spawn`

### 2.4 提权

| method | params | result |
|--------|--------|--------|
| `request_escalation` | `process_id`, `capabilities`, `reason?` | escalation dict |
| `approve_escalation` | `request_id`, `by?` | escalation dict |
| `deny_escalation` | `request_id`, `by?` | escalation dict |
| `list_escalations` | `status?` | `{ escalations }` |
| `get_escalation` | `request_id` | escalation dict \| null |

### 2.5 审计

| method | params | result |
|--------|--------|--------|
| `events` | `process_id?`, `kind?`, `limit?` | `{ events }` |
| `verify_event_chain` | `{}` | `{ ok, break_index }` |
| `emit` | `kind`, `process_id`, `detail?` | event dict |

#### Event dict

```json
{
  "id": "...",
  "kind": "process_created|mediation|policy.decision|...",
  "process_id": "...",
  "detail": {},
  "ts": 0.0,
  "prev_hash": "...",
  "hash": "..."
}
```

### 2.6 资源与调度

| method | params | result |
|--------|--------|--------|
| `resource_charge` | `process_id`, `kind`, `amount` | `{ remaining }` |
| `resource_usage` | `process_id` | `{ kind: { limit, used, remaining } }` |
| `scheduler_submit` | `process_id`, `payload?`, `priority?` | task dict |
| `scheduler_next` | `{}` | task dict \| null |
| `scheduler_stats` | `{}` | `{ queued, running, done, cancelled }` |
| `scheduler_complete` | `task_id`, `cancelled?` | `{ ok: true }` |
| `scheduler_cancel_process` | `process_id` | `{ cancelled: n }` |

`resource.kind`：`token_budget` · `memory_bytes` · `concurrency_slots` · `child_proc` · `tool_calls` · `io_write_bytes` · `io_read_bytes`

### 2.7 能力 / Intent 辅助

| method | params | result |
|--------|--------|--------|
| `capability_narrow` | `token`, `subset`, `process_id?`, `expires_at?` | token dict |
| `synthesize_intent` | intent dict (`goal`, `capabilities`, `constraints`) | `{ granted, dropped, intent }` |
| `apply_intent` | `process_id`, `intent`, `parent_token?` | `{ token, granted, dropped, process }` |
| `synthesize_and_issue` | `intent` + either `process_id` **or** create fields | process + granted |
| `filter_tools` | `process_id`, `tools: [name…]` | `{ tools, total }` |
| `tools_for_process` | `process_id` | `{ tools, unrestricted }` |
| `tool_catalog` | `{}` | `{ tool_to_crew_cap, version }` |

### P0-B 语义

- `create_process` 可带 `intent`；host 默认 `require_intent=true`（env `TEVARN_KERNEL_REQUIRE_INTENT`）。
- 无 `capabilities` 且 require_intent → 自动只读 grantable intent（禁止静默全开）。
- `filter_tools` 用于 LLM tool schema 裁剪；抽象 cap（如 `file_rw`）覆盖具体工具。

### P0-C 方法

| method | params | result |
|--------|--------|--------|
| `schedule_run` | `process_id`, `priority_class?`, `priority?`, `payload?` | task dict |
| `llm_try_acquire` | lease request fields | `{status: granted\|queued\|rejected, ...}` |
| `llm_poll` | `request_id` | same as try_acquire |
| `llm_release` | `request_id` | `{ok}` |
| `llm_cancel_wait` | `request_id` | `{ok}` |
| `llm_charge_quota` | `identity_id?`, `amount` | `{ok}` |
| `llm_status` | `{}` | in_flight / queued / config / quota |
| `llm_set_config` | max_in_flight, owner_reserve, … | `{ok}` |
| `run_acquire` | `process_id` | `{remaining}` concurrency slot |
| `run_release` | `process_id` | `{ok}` |
| `run_gate_try` | `process_id`, `priority_class?`, `priority?` | `{status: granted\|queued\|rejected, ...}` |
| `run_gate_poll` | `request_id` | same shape as try |
| `run_gate_release` | `process_id` | `{ok}` |
| `run_gate_status` | `{}` | in_flight / queued / counts |
| `run_gate_set_max` | `max_concurrent` | `{ok, max_concurrent}` |

`priority_class`: `system` · `foreground` · `interactive` · `workforce_high` · `workforce` · `background`

**RunGate 语义**：跨会话全局并发上限；同 process 幂等 grant；满则排队，release 按优先级唤醒。loop 在 `schedule_run` 后 `_await_run_gate`，session 锁只保同会话串行。

### P0-D 方法

| method | params | result |
|--------|--------|--------|
| `decide_tool` | `name`, `args`, `process_id?`, `skill_tools?`, `skill_deny?`, `emit?` | court audit dict |
| `set_court_policy` | workspace_root, user_deny, profile, … | `{ok}` |
| `isolation_resolve` | `process_id`, `profile?`, `is_workforce?` | profile policy |
| `isolation_set_profile` | `process_id`, `profile` | `{ok}` |
| `isolation_spawn` | `process_id`, `command`, `backend` | handle / error |
| `isolation_complete` | `handle_id`, `exit_code` | handle |
| `checkpoint_begin` | `process_id`, `path` | checkpoint dict |
| `checkpoint_restore` | `checkpoint_id` | checkpoint dict |
| `checkpoint_list` | `process_id` | `{checkpoints}` |
| `export_decision_trail` | `process_id`, `limit?` | `{events, total}` |

Isolation profiles: `off` · `interactive` · `workforce` · `untrusted` · `read_only`

### P0.5 方法（长程可靠）

| method | params | result |
|--------|--------|--------|
| `process_snapshot` | `process_id`, `meta?` | snapshot dict（含 `tail_hash`） |
| `process_snapshot_latest` | `process_id` | snapshot \| null |
| `process_snapshot_list` | `process_id` | `{snapshots}` |
| `process_recovery_plan` | `process_id` | `{mode, full_replay:false, tail_hash, …}` |
| `result_spill` | `process_id`, `tool`, `content` | `{spilled, handle?, context}` |
| `result_load` | `handle_id` | `{content, bytes}` |
| `result_store_status` | `{}` | handles / threshold |
| `iteration_set_budget` | `process_id`, `max_total` | `{ok}` |
| `iteration_consume` | `process_id` | `{status: allow\|exhausted, …}` |
| `iteration_refund` | `process_id` | `{ok}` |
| `iteration_status` | `process_id` | used/max/remaining |
| `doom_record` | `process_id`, `tool`, `args?` | `{status: allow\|doom_loop, …}` |
| `doom_reset` | `process_id` | `{ok}` |
| `doom_status` | `process_id` | streak/tripped |
| `policy_status` | `{}` | budgets / doom counts |
| `cache_record` | `family`, `hit`, `bytes_saved?` | metrics dict |
| `cache_metrics` | `{}` | per-family hit_rate |
| `cost_charge` | `process_id`, `family`, `tokens`, `billable?` | cost panel |
| `cost_panel` | `{}` | totals + by_process + by_family |
| `cost_process` | `process_id` | process cost |
| `marathon_record` | `kind` (`attempt`/`resume_ok`/`resume_fail`/`snapshot_ok`), `reason?` | metrics |
| `marathon_metrics` | `{}` | `marathon_resume_success` 等 |
| `reclaim_process_tree` | `process_id`, `reason?` | `{reclaimed, live_before, live_after}` |

**恢复红线**：`process_recovery_plan.full_replay` 必须为 `false`；路径 = 最新进程快照 + `tail_hash` 之后增量事件。

### P1 方法（多 Agent / 服务 / 技能门）

| method | 说明 |
|--------|------|
| `ipc_send` / `ipc_recv` / `ipc_status` | 点对点 IPC + 背压 + 能力鉴权 |
| `service_register` / `service_list` / `service_health` / `service_status` | 系统服务框架 |
| `sys_memory_put/get/list` | Memory 系统服务 |
| `sys_notify_push/list/ack` | Notify 系统服务 |
| `identity_cache_put/get/list` | 身份热缓存 |
| `inbox_submit/claim/complete/fail/release/list/status` | 内核 inbox claim |
| `skill_register/verify/activate/rollback/*` | 技能验证门 |
| `evolution_policy` | `auto_apply: false` 硬策略 |
| `context_*` | 上下文配额与 swap |
| `memory_layer_*` | 分层记忆与 consolidate |

**P1 红线**：`evolution_policy.auto_apply` / `auto_apply_live_caps` 必须为 false；未 `skill_verify` 不可 `skill_activate`。

### P2 方法（平台化 / §8 加深）

| method | 说明 |
|--------|------|
| `coding_profile_list/get/apply/spawn` | 工程/审阅/结对模板；spawn 一键建进程 |
| `collab_*` / `collab_status` | plan / interrupt / resume / approval；**mediate 写/命令门控** |
| `edit_propose/confirm/reject/rollback` | diff 一等公民 |
| `repo_index_build/get/list` | 仓库索引 + 配额 |
| `hal_platform` / `hal_resolve_*` / `hal_enforce_*` | 解析 + **能力 mediate 强制路径** |
| `wasm_load/activate/invoke` / `wasm_explain` | fuel/memory 沙箱 + 限额可解释 |
| `pkg_install/activate/sign/...` / `pkg_set_require_secure` | 签名扫描；生产密钥可强制 |
| `instance_export/import` | 多设备迁移；import hydrate memory/skills(draft) |
| `abi_compat` / `abi_negotiate` / `abi_record_break` | 兼容窗口 + break 计数（目标 0） |

---

## 3. 语义红线（实现不得违反）

1. **能力单调递减**：子进程 / narrow 不得扩大 cap 集（`*` 父除外）。  
2. **兼容模式**：`capabilities=null` 时 capability 层放行但仍写审计。  
3. **预算硬顶**：超预算 charge 拒绝写入 used。  
4. **哈希链**：每条事件 `hash` 含 `prev_hash`；`verify_event_chain` 必须可验证缓冲。  
5. **提权唯一扩权通道**：不得静默合并 caps（approve 除外）。  

---

## 4. 客户端约定

| 环境变量 | 含义 |
|----------|------|
| `TEVARN_KERNEL_BACKEND` | `rust`（默认）\| `python` |
| `TEVARN_KERNEL_HOST` | `host:port` |
| `TEVARN_KERNEL_HOST_BIN` | host 可执行文件绝对路径 |
| `TEVARN_KERNEL_AUTO_START` | `1` 时自动 spawn host |
| `TEVARN_KERNEL_AUDIT_PATH` | 审计 JSONL 路径 |

二进制查找顺序：

1. `TEVARN_KERNEL_HOST_BIN`  
2. `vendor/tevarn-kernel-host/tevarn-kernel-host[.exe]`  
3. `target/release/tevarn-kernel-host[.exe]`  
4. `target/debug/tevarn-kernel-host[.exe]`  

---

## 5. 版本演进

| ABI | 变更 |
|-----|------|
| 1.0.0 | P0-A 初版：上表方法冻结 |

新增方法应递增次版本（1.1.0）；破坏字段语义递增主版本。
