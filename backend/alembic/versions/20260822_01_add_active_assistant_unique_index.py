"""Enforce one active assistant message per thread.

Revision ID: 20260822_01
Revises: 20260514_01
Create Date: 2026-08-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260822_01"
down_revision: Union[str, None] = "20260514_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = "uq_ai_message_active_assistant_per_thread"
ACTIVE_PREDICATE = (
    "role = 'assistant' AND status IN "
    "('queued', 'analyzing', 'searching', 'thinking', 'generating')"
)
FAILURE_CONTENT = "This response could not be completed. Please try again."


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE ai_message
        SET status = 'failed', content = '{FAILURE_CONTENT}', partial_content = NULL
        WHERE {ACTIVE_PREDICATE}
          AND created_at < now() - interval '2 hours'
        """
    )
    op.execute(
        f"""
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY thread_id
                       ORDER BY created_at DESC, id DESC
                   ) AS position
            FROM ai_message
            WHERE {ACTIVE_PREDICATE}
        )
        UPDATE ai_message
        SET status = 'failed', content = '{FAILURE_CONTENT}', partial_content = NULL
        WHERE id IN (SELECT id FROM ranked WHERE position > 1)
        """
    )
    op.create_index(
        INDEX_NAME,
        "ai_message",
        ["thread_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_PREDICATE),
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="ai_message")
