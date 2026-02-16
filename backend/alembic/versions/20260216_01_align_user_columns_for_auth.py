"""align user table columns for auth model

Revision ID: 20260216_01
Revises: 20260212_01
Create Date: 2026-02-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "20260216_01"
down_revision: Union[str, None] = "20260212_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names() -> set[str]:
    inspector = inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns("user")}


def upgrade() -> None:
    columns = _column_names()

    if "password_hash" in columns and "password" not in columns:
        op.alter_column("user", "password_hash", new_column_name="password")

    if "is_superuser" in columns and "is_super" not in columns:
        op.alter_column("user", "is_superuser", new_column_name="is_super")

    columns = _column_names()
    if "avatar_url" not in columns:
        op.add_column(
            "user", sa.Column("avatar_url", sa.String(length=512), nullable=True)
        )


def downgrade() -> None:
    columns = _column_names()

    if "avatar_url" in columns:
        op.drop_column("user", "avatar_url")

    columns = _column_names()
    if "is_super" in columns and "is_superuser" not in columns:
        op.alter_column("user", "is_super", new_column_name="is_superuser")

    if "password" in columns and "password_hash" not in columns:
        op.alter_column("user", "password", new_column_name="password_hash")
