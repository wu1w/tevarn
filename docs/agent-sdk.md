# Takton Agent SDK 雏形（P1-B G6）

**版本**：0.8.0-alpha  
**目标**：声明式权限 + 入口约定 + 打包清单；控制平面仍经 kernel ABI。

---

## 1. 包布局

```text
my-agent/
  agent.json          # 清单（必填）
  entry.py            # 或 entry.md 指令入口
  skills/             # 可选技能源
  tests/              # skill-gate tests 名称列表对应
  README.md
```

## 2. `agent.json` schema

```json
{
  "name": "research-assistant",
  "version": "0.1.0",
  "entry": "entry.py",
  "permissions": ["file_read", "grep", "knowledge_search", "ipc_recv"],
  "resources": {
    "token_budget": 50000,
    "max_iterations": 20,
    "isolation": "interactive"
  },
  "events": ["on_start", "on_message", "on_stop"],
  "skills": [
    {"name": "summarize", "path": "skills/summarize.md", "tests": ["unit_ok"]}
  ]
}
```

## 3. 安装约定

1. 校验 `agent.json`  
2. 对每个 skill：`skill_register` → `skill_verify` →（人工）`skill_activate`  
3. `identity_cache_put` 写入权限档案  
4. 运行时 `create_process` + `apply_intent` 使用声明的 `permissions`  

**红线**：`auto_apply` 永不为 true；未过 skill-gate 的 skill 不可 `is_loadable`。

## 4. 最小 Python 辅助

```python
from backend.kernel import get_kernel

def install_agent_manifest(manifest: dict) -> dict:
    k = get_kernel()
    k._call("identity_cache_put", {
        "identity": {
            "id": manifest["name"],
            "name": manifest["name"],
            "capabilities": manifest.get("permissions"),
            "status": "active",
        }
    })
    results = []
    for sk in manifest.get("skills") or []:
        content = open(sk["path"], encoding="utf-8").read()
        pkg = k._call("skill_register", {
            "name": sk["name"],
            "version": manifest.get("version", "0.1.0"),
            "content": content,
            "permissions": manifest.get("permissions") or [],
            "tests": sk.get("tests") or [],
        })
        k._call("skill_verify", {"package_id": pkg["id"]})
        # activate is human-gated in product UI
        results.append(pkg)
    return {"identity": manifest["name"], "skills": results}
```

## 5. CLI

```text
python scripts/takton_sdk_pack.py ./my-agent              # 校验清单
python scripts/takton_sdk_pack.py ./my-agent --out dist/  # 校验并打 zip
python scripts/takton_eval.py                             # 固定评测
python scripts/ci_eval_gate.py                            # CI 门禁（可 --run）
```

打包产物：`{name}-{version}.takton-agent.zip`（单顶层目录，可被包市场安装流程消费）。

## 6. 红线清单（pack 会提示 risky_permissions）

- `terminal` / `command` / `*` 等需人工审  
- `resources.isolation` 建议 `interactive` 或更严  
- **禁止** 在清单中声明 `auto_apply: true`（内核硬关）
