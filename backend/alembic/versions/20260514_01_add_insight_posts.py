"""add insight post tables

Revision ID: 20260514_01
Revises: 20260504_01
Create Date: 2026-05-14
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260514_01"
down_revision: Union[str, None] = "20260504_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "insight_post",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_file_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("subtitle", sa.String(length=1024), nullable=True),
        sa.Column("body_md", sa.Text(), nullable=False),
        sa.Column("post_type", sa.String(length=64), nullable=False, server_default="insight"),
        sa.Column("tone", sa.String(length=64), nullable=False, server_default="analytical"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["source_file_id"], ["file.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_insight_post_source_file_id", "insight_post", ["source_file_id"])
    op.create_index("ix_insight_post_status", "insight_post", ["status"])
    op.create_index("ix_insight_post_user_id", "insight_post", ["user_id"])

    op.create_table(
        "insight_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("post_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=True),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("quote_text", sa.Text(), nullable=True),
        sa.Column("timestamp_seconds", sa.Float(), nullable=True),
        sa.Column("reliability_score", sa.Float(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["post_id"], ["insight_post.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_insight_evidence_post_id", "insight_evidence", ["post_id"])
    op.create_index("ix_insight_evidence_source_type", "insight_evidence", ["source_type"])

    op.create_table(
        "insight_annotation",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("post_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("anchor_text", sa.String(length=512), nullable=False),
        sa.Column("evidence_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["post_id"], ["insight_post.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_insight_annotation_post_id", "insight_annotation", ["post_id"])


def downgrade() -> None:
    op.drop_index("ix_insight_annotation_post_id", table_name="insight_annotation")
    op.drop_table("insight_annotation")
    op.drop_index("ix_insight_evidence_source_type", table_name="insight_evidence")
    op.drop_index("ix_insight_evidence_post_id", table_name="insight_evidence")
    op.drop_table("insight_evidence")
    op.drop_index("ix_insight_post_user_id", table_name="insight_post")
    op.drop_index("ix_insight_post_status", table_name="insight_post")
    op.drop_index("ix_insight_post_source_file_id", table_name="insight_post")
    op.drop_table("insight_post")
