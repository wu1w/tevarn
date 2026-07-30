# tests/（已归档）

**自 2026-07-30 起，pytest 权威目录只有 `backend/tests/`。**

历史根目录 `tests/` 与 `backend/tests/` 双轨并存，导致重复用例、双 conftest、
CI 假绿与漏改。清债结果：

| 动作 | 说明 |
|------|------|
| 独有用例 | 迁入 `backend/tests/`（durable_run / loop_freeze / subagent_* 等） |
| 内容相同重复 | 删除（以 `backend/tests` 为准） |
| `test_unified_tools` | 保留 backend 版（含 local 执行路径 monkeypatch） |
| conftest | 仅 `backend/tests/conftest.py` |

本地 / CI：

```bash
python -m pytest backend/tests -q
python -m pytest backend/tests/security -q   # Phase 1.1 安全回归
```

请勿再向本目录添加测试文件。
