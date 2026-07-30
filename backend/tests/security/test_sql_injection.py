"""SQL 注入面回归测试（Phase 1.1）。

调研结论：仓库层全部使用 SQLAlchemy ORM，raw SQL 仅出现在迁移/常量场景，
且标识符经 _assert_sql_ident 白名单校验。本测试冻结该白名单守卫的行为，
防止未来有人放宽正则、把用户可控标识符拼进 DDL。

取自 backend/database.py: _assert_sql_ident / _SQL_IDENT
"""

from __future__ import annotations

import pytest

from backend.database import _assert_sql_ident


@pytest.mark.parametrize(
    "ident", ["users", "col_1", "_private", "SessionTable", "a1b2c3"]
)
def test_valid_identifiers_accepted(ident: str) -> None:
    assert _assert_sql_ident(ident) == ident


@pytest.mark.parametrize(
    "ident",
    [
        "users; DROP TABLE users",
        "col name",
        "1col",
        "col-name",
        "col`",
        "col'--",
        "*",
        "",
        "table)",
        "a b",
        "évil",
    ],
)
def test_malicious_identifiers_rejected(ident: str) -> None:
    with pytest.raises(ValueError):
        _assert_sql_ident(ident)


def test_error_message_includes_kind() -> None:
    with pytest.raises(ValueError) as ei:
        _assert_sql_ident("bad; drop", kind="column")
    assert "column" in str(ei.value)
