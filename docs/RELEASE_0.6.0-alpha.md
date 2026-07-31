# P0 里程碑交付说明（产品版本仍为 0.5.0-alpha）

**产品版本**：`0.5.0-alpha`（`backend/VERSION`，不因本里程碑升号）  
**路线图里程碑**：§5 · 0.6 最小可用 AIOS Runtime（P0）  
**分支**：[`feature/agent-kernel`](https://github.com/wu1w/takton/tree/feature/agent-kernel)

> 文件名保留 `RELEASE_0.6.0-alpha` 仅对应 **路线图阶段名**；对外/安装包版本号以 `0.5.0-alpha` 为准。

---

## 本里程碑要点

| 项 | 说明 |
|----|------|
| **K-03** | `backend/agent/cap_tools.py`：能力裁剪；`use_tool_pack` 扩容后重滤 |
| **K-05** | Rust `resource_denied` 审计；memory/child_proc 硬拒 |
| **路径门控** | `POST /api/tools/{id}/execute` 无 process → 403 |
| **K-04** | run_gate / 优先级出队契约单测 |
| **验收** | ROADMAP §5.2 勾选；`test_p0_acceptance_06.py` |

---

## 测试

- `cargo test -p takton-kernel --lib` — 绿  
- Phase H + P0 acceptance 单测 — 绿  
- 全量 `backend/tests`（无 host 时 ABI 相关 skip）  

---

## 后续

产品版本继续 **0.5.0-alpha**，按 ROADMAP 推进 **0.7 长程/成本**（§6）。  
总览见 [ROADMAP.md](./ROADMAP.md)。
