# -*- coding: utf-8 -*-
import asyncio
import sys

sys.path.insert(0, ".")

from backend.core.safe_subprocess import (  # noqa: E402
    needs_shell,
    run_capture,
    validate_app_name,
)
from backend.core.command_policy import _default_policy  # noqa: E402
from backend.agent.context_compress import compress_history_if_needed  # noqa: E402


async def main() -> None:
    r = await run_capture('python -c "print(123)"', timeout=30)
    print("python", r.get("ok"), repr(r.get("stdout")), r.get("mode"))
    assert r.get("ok") and "123" in (r.get("stdout") or "")

    r2 = await run_capture("cmd_with\x00null")
    print("nul", r2)
    assert not r2.get("ok")
    assert "Security" in (r2.get("stderr") or "") or "NUL" in (r2.get("stderr") or "")

    assert validate_app_name("notepad") is None
    assert validate_app_name("notepad & calc") is not None

    pol = _default_policy()
    assert pol.get("delete") == "deny"
    assert pol.get("system") == "confirm"
    print("policy ok", pol.get("delete"), pol.get("exfiltration"))

    # compress does not mutate global threshold permanently
    from backend.agent.context_engine import get_context_engine

    eng = get_context_engine()
    before = eng.threshold_percent
    msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
    await compress_history_if_needed(msgs, threshold=0.45)
    assert eng.threshold_percent == before
    print("compress threshold stable", before)
    print("ALL OK")


if __name__ == "__main__":
    asyncio.run(main())
