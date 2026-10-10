"""add assets.source_sha

Revision ID: 681b81b887e2
Revises: c5df8e428540
Create Date: 2026-10-10 03:08:49.576548

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "681b81b887e2"
down_revision: str | Sequence[str] | None = "c5df8e428540"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("assets", sa.Column("source_sha", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("assets", "source_sha")
