"""T2/T3: edit 唯一性契约 + file_read 行号分页契约。

回归目标：
- edit 多处匹配时必须报错，不得静默改第一处（T2）
- file_read 输出带行号、按行边界截断、给出可续读 offset（T3）
- 分页上限低于全局 max_tool_result_length，避免二次 head+tail 拼接
"""

import pytest

from backend.services.tools.executors import (
    FILE_READ_DEFAULT_LIMIT,
    _file_read_cache_clear,
    _file_read_char_budget,
    execute_edit,
    execute_file_read,
)


@pytest.fixture
def ws(tmp_path):
    return {"base_path": str(tmp_path)}


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# ── T2: edit 唯一性 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_edit_rejects_ambiguous_match_and_leaves_file_intact(ws, tmp_path):
    original = "x = 1\nprint(x)\ny = 1\nprint(y)\n"
    p = _write(tmp_path, "a.py", original)

    out = await execute_edit(
        ws, {"filepath": "a.py", "old_text": "= 1", "new_text": "= 2"}
    )

    assert out.startswith("[Error]")
    assert "appears 2 times" in out
    # 必须给出重复位置的行号，模型才知道要扩多少上下文
    assert "line 1" in out and "line 3" in out
    # 关键：报错时文件一个字节都不能变
    assert p.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_edit_replace_all_opt_in(ws, tmp_path):
    p = _write(tmp_path, "b.py", "a = 1\nb = 1\n")

    out = await execute_edit(
        ws,
        {"filepath": "b.py", "old_text": "= 1", "new_text": "= 2", "replace_all": True},
    )

    assert out.startswith("[Success]")
    assert "all 2 occurrences" in out
    assert p.read_text(encoding="utf-8") == "a = 2\nb = 2\n"


@pytest.mark.asyncio
async def test_edit_unique_match_reports_line_number(ws, tmp_path):
    p = _write(tmp_path, "c.py", "one\ntwo\nthree\n")

    out = await execute_edit(
        ws, {"filepath": "c.py", "old_text": "two", "new_text": "TWO"}
    )

    assert out.startswith("[Success]")
    assert "c.py:2" in out
    assert p.read_text(encoding="utf-8") == "one\nTWO\nthree\n"


@pytest.mark.asyncio
async def test_edit_missing_text_is_actionable(ws, tmp_path):
    _write(tmp_path, "d.py", "hello\n")

    out = await execute_edit(
        ws, {"filepath": "d.py", "old_text": "nope", "new_text": "x"}
    )

    assert out.startswith("[Error]")
    assert "not found" in out
    assert "indentation" in out  # 提示模型去 file_read 抄原文


@pytest.mark.asyncio
async def test_edit_noop_rejected(ws, tmp_path):
    _write(tmp_path, "e.py", "same\n")

    out = await execute_edit(
        ws, {"filepath": "e.py", "old_text": "same", "new_text": "same"}
    )

    assert out.startswith("[Error]")
    assert "identical" in out


# ── T3: file_read 行号 + 分页 ────────────────────────────────


@pytest.mark.asyncio
async def test_file_read_emits_line_numbers(ws, tmp_path):
    _write(tmp_path, "f.py", "alpha\nbeta\ngamma\n")

    out = await execute_file_read(ws, {"filepath": "f.py"})

    assert "     1\talpha" in out
    assert "     2\tbeta" in out
    assert "     3\tgamma" in out
    assert "end of file" in out


@pytest.mark.asyncio
async def test_file_read_paginates_and_offers_continuation(ws, tmp_path):
    _write(tmp_path, "big.py", "".join(f"line{i}\n" for i in range(1, 3001)))

    out = await execute_file_read(ws, {"filepath": "big.py"})

    assert "     1\tline1" in out
    # 必须告知总量与续读方式，否则模型不知道自己只读了一部分
    assert "of 3000" in out
    assert "offset=" in out

    # 续读 offset 必须精确接上最后一行，不重不漏
    shown = [
        int(ln.split("\t", 1)[0])
        for ln in out.split("\n\n[")[0].split("\n")
        if "\t" in ln
    ]
    assert shown == list(range(1, len(shown) + 1))
    assert f"offset={shown[-1] + 1}" in out


@pytest.mark.asyncio
async def test_file_read_line_limit_is_honoured(ws, tmp_path):
    _write(tmp_path, "big.py", "".join(f"line{i}\n" for i in range(1, 3001)))

    out = await execute_file_read(ws, {"filepath": "big.py", "limit": 5})

    assert "     1\tline1" in out
    assert "     5\tline5" in out
    assert "line6" not in out
    assert "line limit" in out
    assert "offset=6" in out


@pytest.mark.asyncio
async def test_file_read_offset_reaches_tail(ws, tmp_path):
    _write(tmp_path, "big.py", "".join(f"line{i}\n" for i in range(1, 3001)))

    out = await execute_file_read(ws, {"filepath": "big.py", "offset": 2500})

    assert "  2500\tline2500" in out
    assert "  3000\tline3000" in out
    assert "end of file" in out


@pytest.mark.asyncio
async def test_file_read_never_splits_a_line(ws, tmp_path):
    # 每行 500 字符，必然撞上字符预算
    _write(tmp_path, "wide.py", "".join(("x" * 500) + "\n" for _ in range(200)))

    out = await execute_file_read(ws, {"filepath": "wide.py"})

    body = out.split("\n\n[")[0]
    for line in body.split("\n"):
        # 每行都必须是完整的「行号 TAB 正文」，不能出现半截行
        assert "\t" in line
        assert len(line.split("\t", 1)[1]) == 500
    assert "char budget" in out
    assert "offset=" in out


@pytest.mark.asyncio
async def test_file_read_stays_under_effective_result_cap(ws, tmp_path):
    """分页上限必须低于 tool_round 的有效上限，否则那里会二次 head+tail 拼接，
    把按行分页的成果重新打断。两处的上限算法必须一致。"""
    from backend.agent.tool_result_contract import TOOL_RESULT_BUDGET
    from backend.core.config import settings

    _write(tmp_path, "big.py", "".join(f"line{i}\n" for i in range(1, 5001)))
    out = await execute_file_read(ws, {"filepath": "big.py"})

    effective = max(
        int(getattr(settings, "max_tool_result_length", 12_000) or 12_000),
        int(TOOL_RESULT_BUDGET.get("file_read", 0) or 0),
    )
    assert len(out) <= effective
    assert _file_read_char_budget() < effective


@pytest.mark.asyncio
async def test_file_read_rejects_binary(ws, tmp_path):
    (tmp_path / "blob.bin").write_bytes(b"\x89PNG\x00\x01\x02binary")

    out = await execute_file_read(ws, {"filepath": "blob.bin"})

    assert out.startswith("[Error]")
    assert "binary" in out.lower()


@pytest.mark.asyncio
async def test_file_read_offset_past_eof_is_explicit(ws, tmp_path):
    _write(tmp_path, "s.py", "a\nb\n")

    out = await execute_file_read(ws, {"filepath": "s.py", "offset": 99})

    assert out.startswith("[Error]")
    assert "past end" in out
    assert "2 lines" in out


@pytest.mark.asyncio
async def test_file_read_pagination_arg_contract(ws, tmp_path):
    _write(tmp_path, "s.py", "a\nb\n")

    # 0 / 缺省 → 回落默认值（模型常用 0 表达「从头开始」）
    assert "     1\ta" in await execute_file_read(
        ws, {"filepath": "s.py", "offset": 0}
    )
    assert "     1\ta" in await execute_file_read(ws, {"filepath": "s.py", "limit": 0})

    # 负数是明确的调用错误
    assert (await execute_file_read(ws, {"filepath": "s.py", "offset": -3})).startswith(
        "[Error]"
    )
    assert (await execute_file_read(ws, {"filepath": "s.py", "limit": -1})).startswith(
        "[Error]"
    )
    assert (
        await execute_file_read(ws, {"filepath": "s.py", "offset": "abc"})
    ).startswith("[Error]")


@pytest.mark.asyncio
async def test_file_read_path_traversal_still_blocked(ws, tmp_path):
    out = await execute_file_read(ws, {"filepath": "../../etc/passwd"})
    assert "[Security Blocked]" in out or "[Error]" in out


@pytest.mark.asyncio
async def test_file_read_default_window_fits_a_module(ws, tmp_path):
    """Default omit-limit read must cover a typical module, not 80–200 line slices."""
    n = 800
    _write(tmp_path, "mod.py", "".join(f"def f{i}():\n    return {i}\n" for i in range(n)))
    # 800 funcs × 2 lines = 1600 lines, under default 2000
    out = await execute_file_read(ws, {"filepath": "mod.py"})
    assert "end of file" in out
    assert "offset=" not in out
    assert FILE_READ_DEFAULT_LIMIT >= 2000
    assert _file_read_char_budget() >= 80_000


@pytest.mark.asyncio
async def test_file_read_schema_default_limit_matches_executor():
    from backend.tools.builtins.core_tools import FileReadTool

    tool = FileReadTool()
    lim = tool.parameters["properties"]["limit"]["default"]
    assert lim == FILE_READ_DEFAULT_LIMIT
    desc = tool.description or ""
    assert str(FILE_READ_DEFAULT_LIMIT) in desc
    assert "offset" in desc.lower() or "续读" in desc


@pytest.mark.asyncio
async def test_file_read_identical_reread_uses_cache(ws, tmp_path, monkeypatch):
    """Same-run (path, offset, limit) hits — live 5–6× dispatcher/court re-reads."""
    from backend.services.tools import executors as ex

    _file_read_cache_clear()
    _write(tmp_path, "a.py", "".join(f"line{i}\n" for i in range(1, 51)))
    calls = {"n": 0}
    orig = ex._read_file_paginated

    def wrapped(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    monkeypatch.setattr(ex, "_read_file_paginated", wrapped)
    out1 = await execute_file_read(ws, {"filepath": "a.py"})
    out2 = await execute_file_read(ws, {"filepath": "a.py"})
    assert out1 == out2
    assert calls["n"] == 1
    _write(tmp_path, "a.py", "".join(f"changed{i}\n" for i in range(1, 51)))
    out3 = await execute_file_read(ws, {"filepath": "a.py"})
    assert calls["n"] == 2
    assert "changed1" in out3
    assert out3 != out1
    _file_read_cache_clear()


@pytest.mark.asyncio
async def test_file_read_same_offset_limit_cache_stops_slice_reread(
    ws, tmp_path, monkeypatch
):
    """Post-thinking re-reads of the same slice must not reopen the file."""
    from backend.services.tools import executors as ex

    _file_read_cache_clear()
    _write(tmp_path, "dispatcher.py", "".join(f"L{i}\n" for i in range(1, 401)))
    calls = {"n": 0}
    orig = ex._read_file_paginated

    def wrapped(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    monkeypatch.setattr(ex, "_read_file_paginated", wrapped)
    args = {"filepath": "dispatcher.py", "offset": 1, "limit": 80}
    out1 = await execute_file_read(ws, args)
    # same slice again (model already saw fail-open, still re-reads)
    out2 = await execute_file_read(ws, dict(args))
    out3 = await execute_file_read(ws, dict(args))
    assert out1 == out2 == out3
    assert calls["n"] == 1
    # different offset is a new page — not a cache hit
    await execute_file_read(ws, {"filepath": "dispatcher.py", "offset": 81, "limit": 80})
    assert calls["n"] == 2
    _file_read_cache_clear()


@pytest.mark.asyncio
async def test_file_read_explicit_small_limit_still_honoured(ws, tmp_path):
    _write(tmp_path, "big.py", "".join(f"line{i}\n" for i in range(1, 501)))
    out = await execute_file_read(ws, {"filepath": "big.py", "limit": 80})
    assert "    80\tline80" in out
    assert "line81" not in out.split("\n\n[")[0]
    assert "line limit" in out
    assert "offset=81" in out
