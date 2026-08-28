"""Add AI thread and message tables for conversation persistence.

Revision ID: 20260210_01
Revises: 20260209_04
Create Date: 2026-02-10
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "20260210_01"
down_revision: Union[str, None] = "20260209_04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names() -> set[str]:
    inspector = inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns("user")}


def upgrade() -> None:
    columns = _column_names()
    if "storage_key" not in columns:
        op.add_column(
            "user",
            sa.Column("storage_key", sa.String(length=16), nullable=True),
        )
        op.execute("""
            UPDATE "user"
            SET storage_key = substr(md5(id::text), 1, 12)
            WHERE storage_key IS NULL
        """)
        op.alter_column("user", "storage_key", nullable=False)
        op.create_index("ix_user_storage_key", "user", ["storage_key"], unique=True)

    # Temporary default user used by ai_chat_controller.get_current_user_id().
    op.execute("""
        INSERT INTO "user" (id, email, password, name, storage_key, is_active, is_super, created_at)
        VALUES (
            '01234567-89ab-cdef-0123-456789abcdef',
            'default@system.local',
            'not-used',
            'Default User',
            'default_sys',
            true,
            false,
            NOW()
        )
        ON CONFLICT (id) DO NOTHING
    """)

    # 1. ai_thread 테이블 생성
    op.create_table(
        "ai_thread",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "content_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(512), nullable=True),
        sa.Column("metadata_", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("is_archived", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("ix_ai_thread_user_id", "ai_thread", ["user_id"])
    op.create_index("ix_ai_thread_content_id", "ai_thread", ["content_id"])
    op.create_index(
        "ix_ai_thread_user_updated",
        "ai_thread",
        ["user_id", "updated_at"],
        postgresql_using="btree",
    )

    # 2. ai_message 테이블 생성
    op.create_table(
        "ai_message",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "thread_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_thread.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("metadata_", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_ai_message_thread_id", "ai_message", ["thread_id"])
    op.create_index(
        "ix_ai_message_thread_created",
        "ai_message",
        ["thread_id", "created_at"],
        postgresql_using="btree",
    )

    # 3. user_event 테이블 생성
    op.create_table(
        "user_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column(
            "content_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "thread_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_thread.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_user_event_user_id", "user_event", ["user_id"])
    op.create_index("ix_user_event_event_type", "user_event", ["event_type"])
    op.create_index(
        "ix_user_event_user_created",
        "user_event",
        ["user_id", "created_at"],
        postgresql_using="btree",
    )


def downgrade() -> None:
    # user_event 삭제
    op.drop_index("ix_user_event_user_created", table_name="user_event")
    op.drop_index("ix_user_event_event_type", table_name="user_event")
    op.drop_index("ix_user_event_user_id", table_name="user_event")
    op.drop_table("user_event")

    # ai_message 삭제
    op.drop_index("ix_ai_message_thread_created", table_name="ai_message")
    op.drop_index("ix_ai_message_thread_id", table_name="ai_message")
    op.drop_table("ai_message")

    # ai_thread 삭제
    op.drop_index("ix_ai_thread_user_updated", table_name="ai_thread")
    op.drop_index("ix_ai_thread_content_id", table_name="ai_thread")
    op.drop_index("ix_ai_thread_user_id", table_name="ai_thread")
    op.drop_table("ai_thread")

    op.execute("""
        DELETE FROM "user" WHERE id = '01234567-89ab-cdef-0123-456789abcdef'
    """)
