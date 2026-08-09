# Tevarn 0.5.0-alpha · 2026-07-31 工作摘要

**分支**：[`feature/agent-kernel`](https://github.com/wu1w/tevarn/tree/feature/agent-kernel)  
**主题**：Agent Kernel 控制平面（Python 脑 + Rust host）硬化与可交付切片

---

## 本版交付要点

### 1. 控制平面与安全

- **tool_gate**：工具路径强制经 gate；`_tool_gate_passed` 需配合内部 token，防伪造
- **Court / run_gate**：host 可用时 Rust 为准；可配置 fail-closed
- **collab / sample-rss**：交互进程强制 `session_id` 绑定（`process_access.require_session`）
- **默认沙箱**：`agent_execution_mode` 默认 sandbox；无能力 fail-closed

### 2. 资源与观测

- **RSS 采样**（含 Windows `K32GetProcessMemoryInfo`）+ 可选 Linux cgroup
- Kernel dashboard / 协作 UI：interrupt · resume · sample-rss（带 session）
- Eval 周报指针与 `/api/kernel/eval/run` 触发面

### 3. 包市场与信任根

- 远程安装：HTTPS + 重定向再校验 SSRF
- **内容 sha256 信任根**（`agent_package_trusted_content_hashes`）
- `/api/packages/market/trust` + 前端市场页信任状态
- 包签名：生产须配置密钥；insecure_default 不可当 verified

### 4. WASM / Host

- **wasmtime** 真执行（fuel / memory 限额）
- Host RPC：可靠超时、卡死后重启恢复；二进制优先 `target/*` 最新构建
- Electron / vendor host 查找路径对齐（不含 kill-process 类危险能力）
- **vendor 打包**：`scripts/build-kernel-host.ps1|-sh` 落盘 `vendor/tevarn-kernel-host/`；`npm run pack/dist` 前 `ensure-vendor-host` 失败即停

### 5. LLM 准入

- Python fallback：`release` 正确唤醒队列（`_wake_best_locked`）
- 单测可强制进程内路径，避免 host 槽位污染

---

## 测试与验证（当日）

| 范围 | 结果 |
|------|------|
| `cargo test -p tevarn-kernel`（lib + abi） | 通过 |
| kernel + security pytest（含 timeout 插件） | 全绿（少量 skip） |
| tool_gate / trust / process_access / package market 聚焦测 | 通过 |
| 全量 `backend/tests` | 未整包强跑；建议 CI 继续兜底 |

---

## 版本与文档

| 项 | 说明 |
|----|------|
| 产品版本 | **0.5.0-alpha**（`backend/VERSION` 为权威） |
| 持久文档 | [kernel-abi-v1](./kernel-abi-v1.md) · [KERNEL_RUST](./KERNEL_RUST.md) · [agent-sdk](./agent-sdk.md) · [TECHNICAL_MANUAL](./TECHNICAL_MANUAL.md) · [ROADMAP](./ROADMAP.md) |
| 过程工单 / 阶段 completion 报告 | **已从仓库移除**（避免 docs 堆积过程文） |

---

## 已知注意点

1. 生产远程装包请配置 `TEVARN_PKG_SIGNING_KEY`（或 JWT 派生）与内容哈希白名单  
2. 无 Job/bwrap 的环境若坚持 sandbox 默认，需显式改 `agent_execution_mode=local`  
3. `target/release` host 若缺新 ABI 方法，请重建并 stage：`.\scripts\build-kernel-host.ps1 -Release`  
4. 长跑后 host 偶发无响应：客户端会超时并尝试重启本进程拉起的 host  
5. 会话恢复：`GET /sessions/{id}/checkpoint` 返回 `recovery` 卡片；仅 `can_resume` / 可恢复 exit 时展示  
6. Eval 硬门：`marathon_resume_success ≥ 0.95`（`TEVARN_MARATHON_RESUME_THRESHOLD`） 

---

## 建议后续

主线已写入 **[ROADMAP.md](./ROADMAP.md)**（2026-07-31 重写；**阶段 H 已收口**）：

1. ~~0.5.x Hardening（H-01…H-14）~~ → **已完成**（见 ROADMAP §4 勾选）  
2. **0.6**：P0 验收全勾 → 最小可用 AIOS Runtime  
3. 其后 0.7 长程/成本 → 0.8 多 Agent 产品化 → 1.0 日用 GA  

本文件只作 0.5.0-alpha **交付快照**；进度与勾选以 ROADMAP 为准。  
补充文档：[PACKAGE_TRUST.md](./PACKAGE_TRUST.md) · CI：`kernel-ci.yml`
