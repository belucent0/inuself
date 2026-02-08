"""add download_failed status and source_url field

Revision ID: 20260208_01
Revises: 20260205_01
Create Date: 2026-02-08

Add DOWNLOAD_FAILED to filestatus enum and source_url column to file table.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "20260208_01"
down_revision: Union[str, None] = "20260205_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add DOWNLOAD_FAILED to filestatus enum
    op.execute("ALTER TYPE filestatus ADD VALUE 'DOWNLOAD_FAILED'")

    # Add source_url column to file table
    op.add_column("file", sa.Column("source_url", sa.String(2048), nullable=True))


def downgrade() -> None:
    op.drop_column("file", "source_url")
    # PostgreSQL doesn't support removing enum values directly
