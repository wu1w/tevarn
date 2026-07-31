# Takton Kernel / Runtime — Rust 化说明

版本：0.1.0（与 `crates/takton-kernel` 同步）  
**ABI**：`1.0.0` — 见 [`kernel-abi-v1.md`](./kernel-abi-v1.md)（P0-A）

## 架构

```
Python (FastAPI / Agent Loop / Tools / Workforce DB)
        │  JSON-RPC (line-delimited) over TCP 127.0.0.1:17890
        ▼
takton-kernel-host  ──►  takton-runtime  ──►  takton-kernel
                                              (process · capability · mediate
                                               · budget · audit · resources
                                               · scheduler)
```

| Crate | 职责 |
|-------|------|
| `takton-kernel` | 控制平面纯库：进程表、能力令牌、中介、预算账本、哈希链审计、调度、资源账户 |
| `takton-runtime` | Runtime 门面：单例装配、service registry、health |
| `takton-kernel-host` | 独立进程：JSON-RPC 宿主 |

Python 侧：

- `backend/kernel_rust/` — RPC 客户端，API 兼容原 `AgentKernel`
- `backend/kernel/kernel.py::get_kernel()` — 默认走 Rust，失败回退 Python
- `backend/kernel/*.py` 中 **Identity / Inbox / Dispatcher / Evolution** 仍为 Python（依赖 SQLAlchemy / Agent Loop），作为 Runtime 适配器挂到 kernel

## 构建

```bash
# 需安装 Rust toolchain (rustup)
cd takton-feature-agent-kernel
cargo build -p takton-kernel-host --release
cargo test -p takton-kernel
```

产物：`target/release/takton-kernel-host`（Windows 为 `.exe`）。

## 运行

```bash
# 单独启动 kernel host
./target/release/takton-kernel-host --listen 127.0.0.1:17890

# 或由 Python 自动拉起（找到 debug/release 二进制后）
set TAKTON_KERNEL_BACKEND=rust
set TAKTON_KERNEL_AUTO_START=1
python start.py
```

### 环境变量

| 变量 | 说明 |
|------|------|
| `TAKTON_KERNEL_BACKEND` | `rust`（默认倾向）/ `python` |
| `TAKTON_KERNEL_HOST` | 默认 `127.0.0.1:17890` |
| `TAKTON_KERNEL_HOST_BIN` | host 可执行文件路径 |
| `TAKTON_KERNEL_AUTO_START` | `1` 时自动 spawn host |
| `TAKTON_KERNEL_AUDIT_PATH` | 审计 JSONL 路径 |

强制 Python（测试 / 未编译）：

```bash
set TAKTON_KERNEL_BACKEND=python
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
cargo test -p takton-kernel

# Python 基础测试仍直接实例化 Python AgentKernel()（不依赖 host）
set TAKTON_KERNEL_BACKEND=python
pytest backend/tests/kernel/test_kernel_basic.py -q

# 集成：host 启动后
cargo build -p takton-kernel-host
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
