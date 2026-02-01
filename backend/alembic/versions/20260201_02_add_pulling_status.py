"""add pulling status

Revision ID: 20260201_02
Revises: 20260201_01
Create Date: 2026-02-01

Add PULLING value to filestatus enum for external source download tracking.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260201_02"
down_revision: Union[str, None] = "20260201_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add PULLING to filestatus enum
    op.execute("ALTER TYPE filestatus ADD VALUE 'PULLING'")


def downgrade() -> None:
    # PostgreSQL doesn't support removing enum values directly
    pass
