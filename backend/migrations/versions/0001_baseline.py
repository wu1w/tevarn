"""baseline —— 0.4.6-alpha 现有 schema 基线（空迁移）

Revision ID: 0001_baseline
Revises:
Create Date: 2026-07-29

背景：在此版本之前 schema 完全由 Base.metadata.create_all()（database.py）
隐式管理，没有版本化。本迁移是「锚点」：

- 已有数据库：`alembic -c backend/alembic.ini stamp 0001_baseline`
  （main.py 启动时若检测到无 alembic_version 表且业务表已存在，会自动 stamp）
- 全新数据库：create_all 建表后 stamp 到本基线
- 此后任何模型变更：必须走 `revision --autogenerate` 生成迁移，
  不再允许仅靠 create_all / 手写 ALTER 兜底
"""
from __future__ import annotations

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 基线锚点：schema 由历史 create_all 建立，本迁移不做任何变更
    pass


def downgrade() -> None:
    pass
