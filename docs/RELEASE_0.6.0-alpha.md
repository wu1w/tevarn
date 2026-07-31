# Takton 0.6.0-alpha · 最小可用 AIOS Runtime（P0）

**分支**：[`feature/agent-kernel`](https://github.com/wu1w/takton/tree/feature/agent-kernel)  
**基准**：0.5.0-alpha + Phase H 打磨

---

## 本版要点

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
- `pytest backend/tests` — 绿（无 host 时 ABI/host 用例 skip）  
- Phase H + P0 acceptance 单测 — 绿  

---

## 后续（0.7）

长程 checkpoint / 成本面板 / marathon 指标 — 见 [ROADMAP.md](./ROADMAP.md) §6。
