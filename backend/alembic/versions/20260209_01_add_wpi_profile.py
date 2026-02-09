"""Add WPI profile table.

Revision ID: 20260209_01
Revises: 20260208_01
Create Date: 2026-02-09

WPI(Whang's Personality Inventory) 성격유형검사 프로필 테이블 추가.
- I-Test(자기평가 5유형): Realist, Romanticist, Humanist, Idealist, Agent
- Me-Test(타인평가 5유형): Relation, Trust, Manual, Self, Culture
- 대응 관계: Realist↔Relation, Romanticist↔Trust, Humanist↔Manual, Idealist↔Self, Agent↔Culture
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260209_01"
down_revision: Union[str, None] = "20260208_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "wpi_profile",
        # PK
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # User FK (1:1)
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            unique=True,
            nullable=False,
        ),
        # I-Test 결과 (JSONB) - 절대 점수
        # {"Realist": 60, "Romanticist": 45, "Humanist": 52, "Idealist": 38, "Agent": 55}
        sa.Column("i_test_scores", postgresql.JSONB, nullable=True),
        # Me-Test 결과 (JSONB) - 절대 점수
        # {"Relation": 58, "Trust": 42, "Manual": 35, "Self": 48, "Culture": 52}
        sa.Column("me_test_scores", postgresql.JSONB, nullable=True),
        # 갭 분석 (JSONB) - 5축 전체 분석
        sa.Column("gap_analysis", postgresql.JSONB, nullable=False, server_default="{}"),
        # 원시 응답 (JSONB)
        # {"i_test": {"rank_1": [...], "rank_2": [...], "rank_3": [...]}, "me_test": {...}}
        sa.Column("raw_responses", postgresql.JSONB, nullable=False, server_default="{}"),
        # 검사 완료 상태
        sa.Column("i_test_completed", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("me_test_completed", sa.Boolean, nullable=False, server_default="false"),
        # 타임스탬프
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    # user_id 인덱스
    op.create_index("ix_wpi_profile_user_id", "wpi_profile", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_wpi_profile_user_id", table_name="wpi_profile")
    op.drop_table("wpi_profile")
