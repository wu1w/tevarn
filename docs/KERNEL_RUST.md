# Tevarn Kernel / Runtime — Rust 化说明

版本：0.1.0（与 `crates/tevarn-kernel` 同步）  
**ABI**：`1.0.0` — 见 [`kernel-abi-v1.md`](./kernel-abi-v1.md)（P0-A）

## 架构

```
Python (FastAPI / Agent Loop / Tools / Workforce DB)
        │  JSON-RPC (line-delimited) over TCP 127.0.0.1:17890
        ▼
tevarn-kernel-host  ──►  tevarn-runtime  ──►  tevarn-kernel
                                              (process · capability · mediate
                                               · budget · audit · resources
                                               · scheduler)
```

| Crate | 职责 |
|-------|------|
| `tevarn-kernel` | 控制平面纯库：进程表、能力令牌、中介、预算账本、哈希链审计、调度、资源账户 |
| `tevarn-runtime` | Runtime 门面：单例装配、service registry、health |
| `tevarn-kernel-host` | 独立进程：JSON-RPC 宿主 |

Python 侧：

- `backend/kernel_rust/` — RPC 客户端，API 兼容原 `AgentKernel`
- `backend/kernel/kernel.py::get_kernel()` — **生产必须 Rust host**；失败时：
  - 默认 **raise**（H2）
  - 仅 `TEVARN_DEV_UNSAFE=1` 或 `TEVARN_KERNEL_BACKEND=python` 才回退废弃 Python 实现
- `backend/kernel/*.py` 中 **Identity / Inbox / Dispatcher / Evolution** 仍为 Python（依赖 SQLAlchemy / Agent Loop），作为 Runtime 适配器挂到 kernel
- **禁止**在 `backend/kernel/kernel.py` 增加新权威业务逻辑（改 `crates/tevarn-kernel`）

### 权威边界（H2）

| 职责 | 权威 | 备注 |
|------|------|------|
| process / mediate / budget / audit / court / run_gate | **Rust** | host JSON-RPC |
| tool schema 裁剪 | Rust `filter_tools` + Python `cap_tools` | 生产 None caps → 空表 |
| **Identity 热路径** | **Rust** `identity_hire/admit/set_*` | SQL 镜像可选 |
| **Inbox claim 队列** | **Rust** 并发/溢出/超时 reclaim | dispatcher 调 RPC |
| **Evolution 分析+门禁** | **Rust** `evolution_analyze` / `evolution_gate` | Python 只组装 snapshot + SQL pending mirror；pytest/DEV 才 offline mirror |
| **Isolation OS** | **Rust** `spawn_os`/`Child`/`poll`/`kill`/`reap` | local/os/auto 真进程；sandbox 后端仍 ledger |
| **Dispatcher claim** | **Rust** `inbox_claim` + `complete_by_db_id` | host 在线禁纯 SQL claim；tick 调 reclaim + identity_admit |
| **Scheduler** | **Rust** 全局 cap + session fair share | 非 session 互斥锁 |
| **Isolation** | **Rust** spawn/reap/os_pid | 平台 backend 仍适配 |
| 单测直接 `AgentKernel()` | Python fixture | 非生产路径 |

## 构建

```bash
# 需安装 Rust toolchain (rustup)
cd tevarn-feature-agent-kernel
cargo build -p tevarn-kernel-host --release
cargo test -p tevarn-kernel
```

产物：`target/release/tevarn-kernel-host`（Windows 为 `.exe`）。

## 运行

```bash
# 单独启动 kernel host
./target/release/tevarn-kernel-host --listen 127.0.0.1:17890

# 或由 Python 自动拉起（找到 debug/release 二进制后）
set TEVARN_KERNEL_BACKEND=rust
set TEVARN_KERNEL_AUTO_START=1
python start.py
```

### 环境变量

| 变量 | 说明 |
|------|------|
| `TEVARN_KERNEL_BACKEND` | `rust`（默认）/ `python`（仅 fixture） |
| `TEVARN_KERNEL_HOST` | 默认 `127.0.0.1:17890` |
| `TEVARN_KERNEL_HOST_BIN` | host 可执行文件路径 |
| `TEVARN_KERNEL_AUTO_START` | `1` 时自动 spawn host |
| `TEVARN_DEV_UNSAFE` | `1` 允许 Python fallback / 兼容全开（开发 only） |
| `TEVARN_FORCE_PRODUCTION_GUARD` | `1` 在 test 中也强制生产守卫 |
| `TEVARN_TOKEN_HMAC_SECRET` | CapabilityToken HMAC（与 JWT 解耦） |
| `TEVARN_KERNEL_AUDIT_PATH` | 审计 JSONL 路径 |
| `TEVARN_PKG_SIGNING_KEY` | 包市场签名密钥（生产必设，见 [PACKAGE_TRUST.md](./PACKAGE_TRUST.md)） |
| `TEVARN_KERNEL_HOST_BIN` | host 可执行文件绝对路径 |

### Court fail-closed（H-04）

| host | `agent_court_rust_required` | 行为 |
|------|----------------------------|------|
| 在线 + Rust 裁决成功 | * | 使用 Rust |
| 在线 + Rust 失败/无结果 | **true**（默认） | **deny** |
| 离线 | * | 回退 Python court |

包生产信任：见 [PACKAGE_TRUST.md](./PACKAGE_TRUST.md)。

强制 Python（测试 / 未编译）：

```bash
set TEVARN_KERNEL_BACKEND=python
```

## RPC 方法（节选）

`ping` `health` `create_process` `end_process` `mark_running` `suspend_process` `resume_process` `get_process` `list_processes` `mediate` `charge_tokens` `top_up_budget` `issue_token` `request_escalation` `approve_escalation` `deny_escalation` `events` `verify_event_chain` `resource_charge` `resource_usage` `scheduler_submit` `scheduler_next` `emit` `register_service`

## 资源账户（OS 化扩展）

进程创建时自动挂载：

- `token_budget` — 与 charge_tokens 同步
- `memory_bytes` — 默认 256 MiB 逻辑配额
- `concurrency_slots` — 默认 4
- `child_proc` — 默认 16
- `tool_calls` / `io_*` — 默认不限

Python：`kernel.resource_charge(pid, "tool_calls", 1)` / `kernel.resource_usage(pid)`。

## 测试

```bash
# Rust 单元测试
cargo test -p tevarn-kernel

# Python 基础测试仍直接实例化 Python AgentKernel()（不依赖 host）
set TEVARN_KERNEL_BACKEND=python
pytest backend/tests/kernel/test_kernel_basic.py -q

# 集成：host 启动后
cargo build -p tevarn-kernel-host
# 再对 get_kernel() 路径做 smoke
```

## 迁移状态

| 模块 | 状态 |
|------|------|
| process / capability / mediate / budget / audit | ✅ Rust |
| scheduler / resource manager | ✅ Rust |
| runtime façade + host | ✅ Rust |
| intent synthesize | ✅ Rust |
| Python get_kernel 适配 | ✅ |
| identity / inbox / dispatcher | ⏳ Python 适配（DB） |
| permission_court 完整 path/steward 层 | ⏳ 仍 Python tool_hooks |
| LLM admission | ⏳ Python |
| computer supervisor | ⏳ 后续 |
