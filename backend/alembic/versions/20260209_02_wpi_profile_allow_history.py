"""Remove unique constraint from wpi_profile.user_id to allow history.

Revision ID: 20260209_02
Revises: 20260209_01
Create Date: 2026-02-09

검사 이력 누적을 위해 user_id의 unique 제약 제거.
기존: 사용자당 1개 프로필 (덮어쓰기)
변경: 사용자당 N개 프로필 (이력 누적)
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260209_02"
down_revision: Union[str, None] = "20260209_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # user_id unique 제약 제거 (이력 누적 허용)
    op.drop_constraint("wpi_profile_user_id_key", "wpi_profile", type_="unique")


def downgrade() -> None:
    # user_id unique 제약 복원
    op.create_unique_constraint("wpi_profile_user_id_key", "wpi_profile", ["user_id"])
