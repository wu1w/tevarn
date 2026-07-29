# 工程收口记录（七项）

日期：2026-07-29

| # | 问题 | 处理 |
|---|------|------|
| 1 | 端口 8000 vs 8090 | Electron/package/deploy 默认 **8090**；候选保留 8000 |
| 2 | 双 Electron main | 真源 `electron/`；`frontend/electron` stub + README |
| 3 | adapters/runtime 文档超前 | 建 `backend/adapters`、`runtime.facade`；ARCHITECTURE 改「现状」 |
| 4 | useDomainEvents 半死 | 只读 store；Owner 仅 Bridge |
| 5 | `_patch_*.py` + 手册过期 | 移入 `scripts/archive/patches`；TECHNICAL_MANUAL 警告条 |
| 6 | agent→api.dependencies | resume/subagent/evaluator 改 repo |
| 7 | 高级页无降级条 | AdvancedShell 覆盖 activity/market/tasks/cluster/channels/… |

人工项（kill-9 / 7 天）仍不在本 pass。
