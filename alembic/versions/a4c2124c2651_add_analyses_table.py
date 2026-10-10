"""add analyses table

Revision ID: a4c2124c2651
Revises: 681b81b887e2
Create Date: 2026-10-10 03:39:06.839453

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a4c2124c2651"
down_revision: str | Sequence[str] | None = "681b81b887e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analyses",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("source_sha", sa.String(length=64), nullable=False),
        sa.Column("analyzer", sa.String(length=32), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("params_hash", sa.String(length=12), nullable=False),
        sa.Column("bundle_key", sa.String(length=1024), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_sha", "analyzer", "version", "params_hash", name="uq_analysis_bundle"
        ),
    )
    op.alter_column(
        "assets",
        "source_sha",
        existing_type=sa.VARCHAR(length=64),
        comment="Source bytes hash for derived assets",
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "assets",
        "source_sha",
        existing_type=sa.VARCHAR(length=64),
        comment=None,
        existing_comment="Source bytes hash for derived assets",
        existing_nullable=True,
    )
    op.drop_table("analyses")
