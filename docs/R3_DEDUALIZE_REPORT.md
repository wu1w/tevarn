# R3 去双轨完成报告（P0–P4 Rust 重构）

**日期**：2026-07-31  
**状态**：✅ 已落地并联调  
**范围**：上一轮约定的 P0–P4（控制平面去双轨 + claim + 身份热路径 + 记忆写路径 + 领域事件/审批）

---

## 1. 范围对照

| 档 | 目标 | 状态 |
|----|------|------|
| **P0** | 去双轨：生产 LLM/领域事件/审批优先 Rust；废弃模块标注 | ✅ |
| **P1** | dispatcher claim 接 Rust inbox（enqueue 镜像 + claim 先 Rust 再 DB） | ✅ |
| **P2** | identity 热路径 `identity_cache` 推送/查询 | ✅ |
| **P3** | crew_memory 写镜像 `sys_memory` + `memory_layers` | ✅ |
| **P4** | `domain_events` + `approval_rules` Rust 权威 + Py shim | ✅ |

---

## 2. 关键改动

### Rust
- `domain_events.rs` — seq / recent / kind map；`emit_locked` 自动扇出
- `approval_rules.rs` — classify / should_auto_approve / evolution 硬审
- Host RPC：`domain_*` · `approval_*`

### Python
- `inbox.py`：enqueue → `inbox_submit`；claim_next → `inbox_claim` 再原子 UPDATE DB
- `identity.py`：create/get → `identity_cache_put`；`get_cached()` 同步热查
- `crew_memory.py`：distill/manual 成功后镜像 Memory 服务
- `domain_events.py`：publish/recent 优先 host
- `approval_rules.py`：classify/auto 优先 host
- `llm_admission.py`：host 不可用且未设 `TAKTON_LLM_ALLOW_PY_FALLBACK` 时拒绝 Py 双控制器
- process/scheduler 等废弃说明强化

---

## 3. 联调结果（本机）

| 项 | 结果 |
|----|------|
| `cargo test -p takton-kernel` | **61** passed（57 lib + 4 abi） |
| `smoke_r3_dedualize.py` | **PASSED**（ABI=181） |
| `test_r3_dedualize.py` | **4** passed |
| `smoke_p1_integration.py` | **PASSED** |
| `smoke_p2_integration.py` | **PASSED** |

```powershell
cargo test -p takton-kernel
cargo build -p takton-kernel-host
$env:TAKTON_KERNEL_HOST_BIN=(Resolve-Path target\debug\takton-kernel-host.exe)
$env:TAKTON_KERNEL_BACKEND="rust"; $env:PYTHONPATH="."
python scripts/smoke_r3_dedualize.py
python -m pytest backend/tests/kernel/test_r3_dedualize.py -q
python scripts/smoke_p1_integration.py
python scripts/smoke_p2_integration.py
```

---

## 4. 诚实边界

| 项 | 说明 |
|----|------|
| Inbox 持久化 | **DB 仍是工单档案**；Rust 做 claim 协调。全量删 Py 队列需迁移历史数据，未做 |
| Identity ORM | 档案 CRUD 仍 SQLAlchemy；热路径可走 cache |
| crew_memory 读 | 组装/检索仍 Py；**写**已镜像 Rust |
| dispatcher 整体 | 只强化 claim 边界；唤醒 loop 仍 Py |
| kernel.py fallback | 仍可显式 `TAKTON_KERNEL_BACKEND=python` 供离线单测 |

---

## 5. 结论

**P0–P4 去双轨重构已完成**：生产控制路径默认只认 Rust host；Python 收敛为适配/投影/脑唤醒。  
全量回归见联调命令。
