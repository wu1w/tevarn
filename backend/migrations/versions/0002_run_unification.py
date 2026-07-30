"""Phase 2.1 Run 统一：agent_runs 增加 origin/identity/parent/checkpoint/budget

Revision ID: 0002_run_unification
Revises: 0001_baseline
Create Date: 2026-07-30

策略：演进 agent_runs，不新建 runs 表。见 docs/design/RUN_UNIFICATION.md
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_run_unification"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "agent_runs" not in insp.get_table_names():
        # 全新库走 create_all，列已在模型中；此处仅老库补列
        return

    cols = {c["name"] for c in insp.get_columns("agent_runs")}

    def _add(name: str, col: sa.Column) -> None:
        if name not in cols:
            op.add_column("agent_runs", col)

    _add("origin", sa.Column("origin", sa.String(length=20), server_default="chat", nullable=False))
    _add("identity_id", sa.Column("identity_id", sa.Uuid(as_uuid=False), nullable=True))
    _add("parent_run_id", sa.Column("parent_run_id", sa.Uuid(as_uuid=False), nullable=True))
    _add("checkpoint", sa.Column("checkpoint", sa.JSON(), nullable=True))
    _add("token_limit", sa.Column("token_limit", sa.Integer(), server_default="0", nullable=False))
    _add("token_used", sa.Column("token_used", sa.Integer(), server_default="0", nullable=False))

    # 索引（幂等：名称固定，已存在则跳过）
    existing_ix = {ix["name"] for ix in insp.get_indexes("agent_runs")}
    if "ix_agent_runs_origin" not in existing_ix:
        op.create_index("ix_agent_runs_origin", "agent_runs", ["origin"])
    if "ix_agent_runs_identity_id" not in existing_ix:
        op.create_index("ix_agent_runs_identity_id", "agent_runs", ["identity_id"])
    if "ix_agent_runs_parent_run_id" not in existing_ix:
        op.create_index("ix_agent_runs_parent_run_id", "agent_runs", ["parent_run_id"])

    # backfill origin from mode / meta（best-effort；SQLite JSON 能力有限）
    try:
        op.execute(
            sa.text(
                """
                UPDATE agent_runs SET origin = CASE
                  WHEN lower(mode) = 'workforce' THEN 'inbox'
                  WHEN lower(mode) = 'subagent' THEN 'subagent'
                  WHEN lower(mode) IN ('headless', 'ci', 'script') THEN 'headless'
                  ELSE COALESCE(NULLIF(origin, ''), 'chat')
                END
                WHERE origin IS NULL OR origin = '' OR origin = 'chat'
                """
            )
        )
        # workforce 行即使 origin 已是 chat 也改写
        op.execute(
            sa.text(
                "UPDATE agent_runs SET origin = 'inbox' WHERE lower(mode) = 'workforce'"
            )
        )
        op.execute(
            sa.text(
                "UPDATE agent_runs SET origin = 'subagent' WHERE lower(mode) = 'subagent'"
            )
        )
    except Exception:
        pass


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "agent_runs" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("agent_runs")}
    existing_ix = {ix["name"] for ix in insp.get_indexes("agent_runs")}
    for ix in ("ix_agent_runs_parent_run_id", "ix_agent_runs_identity_id", "ix_agent_runs_origin"):
        if ix in existing_ix:
            op.drop_index(ix, table_name="agent_runs")
    for name in ("token_used", "token_limit", "checkpoint", "parent_run_id", "identity_id", "origin"):
        if name in cols:
            op.drop_column("agent_runs", name)
