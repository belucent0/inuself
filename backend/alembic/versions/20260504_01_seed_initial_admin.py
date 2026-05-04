"""seed initial admin: nature@timblo.io is_super=true

Revision ID: 20260504_01
Revises: 20260216_02
Create Date: 2026-05-04
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text


revision: str = "20260504_01"
down_revision: Union[str, None] = "20260216_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        text('UPDATE "user" SET is_super = TRUE WHERE email IN :emails'),
        {"emails": ("nature", "nature@timblo.io")},
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        text('UPDATE "user" SET is_super = FALSE WHERE email IN :emails'),
        {"emails": ("nature", "nature@timblo.io")},
    )
