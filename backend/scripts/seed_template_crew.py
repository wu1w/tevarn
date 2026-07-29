"""预置三套模板员工：管家 / 研究 / 编码（幂等）。

用法（仓库根）:
  PYTHONPATH=. python -m backend.scripts.seed_template_crew

API: POST /kernel/workforce/seed-template-crew
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any


TEMPLATES = [
    {
        "name": "小白",
        "role": "CEO",
        "capabilities": [
            "file_rw",
            "command",
            "web_search",
            "git",
            "browser",
            "notify",
        ],
        "token_budget": 500_000,
        "persona": "掌控全局，严谨克制",
        "duty": "向主人汇报、拆单派活、建项目组",
    },
    {
        "name": "研究员",
        "role": "research",
        "capabilities": ["file_rw", "web_search", "browser"],
        "token_budget": 100_000,
        "persona": "好奇、有据可查",
        "duty": "检索与综合，输出可验证结论",
    },
    {
        "name": "工程师",
        "role": "engineering",
        "capabilities": ["file_rw", "command", "web_search", "git"],
        "token_budget": 120_000,
        "persona": "务实、少空话",
        "duty": "读代码、改代码、跑测试",
    },
]


async def seed_template_crew(registry: Any) -> dict[str, Any]:
    """幂等预置模板员工。返回 {created, skipped, total}。"""
    created: list[dict[str, str]] = []
    skipped: list[str] = []
    existing_names = {i.name for i in await registry.list(status=None)}
    for t in TEMPLATES:
        if t["name"] in existing_names:
            skipped.append(t["name"])
            continue
        ident = await registry.create(
            t["name"],
            role=t["role"],
            capabilities=t["capabilities"],
            default_token_budget=t["token_budget"],
        )
        try:
            await registry.append_memory(
                ident.id, "persona", t["persona"], source="system", approved_by="seed"
            )
            await registry.append_memory(
                ident.id, "duty", t["duty"], source="system", approved_by="seed"
            )
        except Exception:
            pass
        created.append({"name": t["name"], "id": str(ident.id), "role": t["role"]})
        existing_names.add(t["name"])
    return {
        "ok": True,
        "created": created,
        "skipped": skipped,
        "total_after": len(existing_names),
        "message": (
            f"hired {len(created)}, skipped {len(skipped)}"
            if created or skipped
            else "nothing to do"
        ),
    }


async def main() -> None:
    # 确保能 import backend
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if root not in sys.path:
        sys.path.insert(0, root)

    from backend.database import AsyncSessionLocal, init_db
    from backend.kernel.kernel import get_kernel
    from backend.kernel.identity import IdentityRegistry

    await init_db()
    kernel = get_kernel()
    # 尽量挂上 registry
    if getattr(kernel, "identity_registry", None) is None:
        kernel.identity_registry = IdentityRegistry(kernel, AsyncSessionLocal)
    reg = kernel.identity_registry
    assert reg is not None

    result = await seed_template_crew(reg)
    for c in result.get("created") or []:
        print(f"hired: {c['name']} id={c['id']}")
    for name in result.get("skipped") or []:
        print(f"skip exists: {name}")
    print(result.get("message", "done"))


if __name__ == "__main__":
    asyncio.run(main())
