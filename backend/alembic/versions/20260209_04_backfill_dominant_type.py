"""Backfill dominant_type in legacy WPI data.

Revision ID: 20260209_04
Revises: 20260209_03
Create Date: 2026-02-09

레거시 WPI 데이터에 dominant_type이 없는 경우 scores에서 계산하여 추가.
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "20260209_04"
down_revision = "20260209_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # i_test.dominant_type 추가 (scores에서 계산)
    op.execute("""
        UPDATE scan_result
        SET data = jsonb_set(
            data,
            '{i_test,dominant_type}',
            to_jsonb((
                SELECT key
                FROM jsonb_each_text(data->'i_test'->'scores')
                ORDER BY value::numeric DESC
                LIMIT 1
            ))
        )
        WHERE scan_type = 'wpi'
          AND data->'i_test' IS NOT NULL
          AND data->'i_test'->'scores' IS NOT NULL
          AND data->'i_test'->>'dominant_type' IS NULL;
    """)

    # me_test.dominant_type 추가 (scores에서 계산)
    op.execute("""
        UPDATE scan_result
        SET data = jsonb_set(
            data,
            '{me_test,dominant_type}',
            to_jsonb((
                SELECT key
                FROM jsonb_each_text(data->'me_test'->'scores')
                ORDER BY value::numeric DESC
                LIMIT 1
            ))
        )
        WHERE scan_type = 'wpi'
          AND data->'me_test' IS NOT NULL
          AND data->'me_test'->'scores' IS NOT NULL
          AND data->'me_test'->>'dominant_type' IS NULL;
    """)


def downgrade() -> None:
    # dominant_type 필드 제거 (롤백용)
    op.execute("""
        UPDATE scan_result
        SET data = jsonb_set(
            jsonb_set(
                data,
                '{i_test}',
                (data->'i_test') - 'dominant_type'
            ),
            '{me_test}',
            (data->'me_test') - 'dominant_type'
        )
        WHERE scan_type = 'wpi'
          AND data->'i_test' IS NOT NULL
          AND data->'me_test' IS NOT NULL;
    """)
