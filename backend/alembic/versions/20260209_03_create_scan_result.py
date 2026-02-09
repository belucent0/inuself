"""Create scan_result table and migrate wpi_profile data.

Revision ID: 20260209_03
Revises: 20260209_02
Create Date: 2026-02-09

범용 심리검사 결과 테이블 생성.
기존 wpi_profile 데이터를 scan_result로 마이그레이션 후 wpi_profile 삭제.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260209_03"
down_revision: Union[str, None] = "20260209_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. scan_result 테이블 생성
    op.create_table(
        "scan_result",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scan_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="in_progress"),
        sa.Column("data", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("ix_scan_result_user_id", "scan_result", ["user_id"])
    op.create_index("ix_scan_result_scan_type", "scan_result", ["scan_type"])
    op.create_index("ix_scan_result_user_type_created", "scan_result", ["user_id", "scan_type", "created_at"])

    # 2. wpi_profile 데이터를 scan_result로 마이그레이션
    op.execute("""
        INSERT INTO scan_result (id, user_id, scan_type, status, data, created_at, updated_at)
        SELECT
            id,
            user_id,
            'wpi',
            CASE
                WHEN i_test_completed AND me_test_completed THEN 'completed'
                ELSE 'in_progress'
            END,
            jsonb_build_object(
                'version', 1,
                'i_test', CASE
                    WHEN i_test_scores IS NOT NULL THEN jsonb_build_object(
                        'scores', i_test_scores,
                        'raw_responses', COALESCE(raw_responses->'i_test', '{}')
                    )
                    ELSE NULL
                END,
                'me_test', CASE
                    WHEN me_test_scores IS NOT NULL THEN jsonb_build_object(
                        'scores', me_test_scores,
                        'raw_responses', COALESCE(raw_responses->'me_test', '{}')
                    )
                    ELSE NULL
                END,
                'gap_analysis', CASE
                    WHEN gap_analysis != '{}' THEN gap_analysis
                    ELSE NULL
                END
            ),
            created_at,
            updated_at
        FROM wpi_profile;
    """)

    # 3. wpi_profile 테이블 삭제
    op.drop_index("ix_wpi_profile_user_id", table_name="wpi_profile")
    op.drop_table("wpi_profile")


def downgrade() -> None:
    # 1. wpi_profile 테이블 재생성
    op.create_table(
        "wpi_profile",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("i_test_scores", postgresql.JSONB, nullable=True),
        sa.Column("me_test_scores", postgresql.JSONB, nullable=True),
        sa.Column("gap_analysis", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("raw_responses", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("i_test_completed", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("me_test_completed", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("ix_wpi_profile_user_id", "wpi_profile", ["user_id"])

    # 2. scan_result에서 wpi 데이터 복원
    op.execute("""
        INSERT INTO wpi_profile (id, user_id, i_test_scores, me_test_scores, gap_analysis, raw_responses, i_test_completed, me_test_completed, created_at, updated_at)
        SELECT
            id,
            user_id,
            data->'i_test'->'scores',
            data->'me_test'->'scores',
            COALESCE(data->'gap_analysis', '{}'),
            jsonb_build_object(
                'i_test', COALESCE(data->'i_test'->'raw_responses', '{}'),
                'me_test', COALESCE(data->'me_test'->'raw_responses', '{}')
            ),
            data->'i_test' IS NOT NULL,
            data->'me_test' IS NOT NULL,
            created_at,
            updated_at
        FROM scan_result
        WHERE scan_type = 'wpi';
    """)

    # 3. scan_result 테이블 삭제
    op.drop_index("ix_scan_result_user_type_created", table_name="scan_result")
    op.drop_index("ix_scan_result_scan_type", table_name="scan_result")
    op.drop_index("ix_scan_result_user_id", table_name="scan_result")
    op.drop_table("scan_result")
