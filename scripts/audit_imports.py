#!/usr/bin/env python3
"""标准导入审计：只 importlib，禁止猜类名。

用法（务必用项目 venv）:
  .venv/Scripts/python.exe scripts/audit_imports.py
  .venv/bin/python scripts/audit_imports.py

退出码: 0=全绿；1=有失败
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

# 仓库根
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 只测「模块能否 import」；符号可选，不存在不算模块失败
MODULES: list[tuple[str, list[str] | None]] = [
    ("backend.core.config", None),
    ("backend.database", None),
    ("backend.kernel.kernel", ["AgentKernel"]),
    ("backend.kernel.dispatcher", ["WorkforceDispatcher"]),
    ("backend.kernel.inbox", ["InboxService"]),
    ("backend.kernel.identity", ["IdentityRegistry"]),
    ("backend.kernel.workforce", None),
    ("backend.kernel.governance", None),
    ("backend.kernel.intent", ["IntentDeclaration"]),
    ("backend.kernel.protocol_spec", None),
    ("backend.kernel.signing", None),
    ("backend.agent.loop", ["NexusAgentLoop"]),
    ("backend.agent.workforce_dispatch", None),
    ("backend.tools.registry", None),
    ("backend.skills.base", None),
    ("backend.services.workflow_engine", ["WorkflowEngine"]),
    ("backend.services.cron_scheduler", ["CronScheduler"]),
    ("backend.services.memory_bus", None),  # 函数 API，无 MemoryBus 类
    ("backend.services.channel_gateway", ["ChannelGateway"]),
    ("backend.services.confirm_manager", None),
    ("backend.services.entity_service", ["EntityService"]),
    ("backend.services.slash_commands", None),
    ("backend.tools.builtins.core_tools", None),
    ("backend.tools.builtins.memory_tools", None),
    ("backend.tools.builtins.capability_tools", None),
    ("backend.tools.builtins.agent_ops_tools", None),
]


def main() -> int:
    print(f"python={sys.executable}")
    print(f"version={sys.version.split()[0]}")
    print(f"cwd={Path.cwd()}")
    print("---")
    ok_n = fail_n = 0
    rows: list[dict] = []
    for mod, attrs in MODULES:
        try:
            m = importlib.import_module(mod)
            missing = []
            if attrs:
                for a in attrs:
                    if not hasattr(m, a):
                        missing.append(a)
            if missing:
                # 模块 OK，符号缺失 → 警告不算 fail
                print(f"OK   {mod}  (warn missing symbols: {missing})")
                ok_n += 1
                rows.append({"module": mod, "ok": True, "missing": missing})
            else:
                print(f"OK   {mod}")
                ok_n += 1
                rows.append({"module": mod, "ok": True})
        except Exception as e:
            print(f"FAIL {mod}  {type(e).__name__}: {e}")
            fail_n += 1
            rows.append({"module": mod, "ok": False, "error": f"{type(e).__name__}: {e}"})
    print("---")
    print(f"ok={ok_n} fail={fail_n} total={ok_n + fail_n}")
    out = ROOT / "reports" / "import-audit-latest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {"python": sys.executable, "ok": ok_n, "fail": fail_n, "rows": rows},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {out}")
    return 0 if fail_n == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
