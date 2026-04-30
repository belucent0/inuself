"""add cover_image_key to content table for AI-generated cover images

Revision ID: 20260408_01
Revises: 20260216_02
Create Date: 2026-04-08
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260408_01"
down_revision: Union[str, None] = "20260216_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "content",
        sa.Column("cover_image_key", sa.String(1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("content", "cover_image_key")
