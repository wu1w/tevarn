# L4 Capability Packs（Sidecar）

功能超车走 **pack**，不进默认 `coding` 主脑。

## Primary packs（本阶段 DoD）

| Pack | 工具（摘要） | 如何启用 |
|------|--------------|----------|
| **devices** | list_devices_tool, remote_exec, device_onboard, shell_session | `use_tool_pack(enable, packs=["devices"])` 或 profile=`ops` |
| **desktop** | desktop_* , uia_snapshot, vision_analyze | `use_tool_pack(..., ["desktop"])` 或场景关键词（dynamic） |
| **evolution** | manage_evolution, query_evolution, manage_skill | `use_tool_pack(..., ["evolution"])` |

## Coding 默认（回归门禁）

默认 **不含**：`desktop_click`、`manage_evolution`、`list_devices_tool`、`remote_exec`、`manage_cron`。

仍含：file/edit/grep/command/web + `use_tool_pack`。

## 验收

```bash
.venv311/bin/python -m pytest backend/tests/test_l4_packs.py -q
set -a; source /opt/hermes-workspace/.secrets/bench_llm.env; set +a
.venv311/bin/python scripts/bench_agent/run_bench.py --models local-llm,kimi
```

单测：`test_l4_packs.py`  
Bench：cases 中 `pack_*` + 原有 coding 题。

## 与 Hermes

| 方向 | Hermes | Takton pack |
|------|--------|-------------|
| 多机 agent | 弱/不同形态 | **devices** |
| 桌面 UIA | 有 computer_use，形态不同 | **desktop** |
| 进化资产运营 | skill 自进化 | **evolution** 可运营 API |
