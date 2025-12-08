"""add html_content to document

Revision ID: 20251208_01
Revises: 20251201_01
Create Date: 2025-12-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '20251208_01'
down_revision: Union[str, None] = '20251201_01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # document 테이블에 html_content 컬럼 추가
    op.add_column('document', sa.Column('html_content', sa.Text(), nullable=True))


def downgrade() -> None:
    # html_content 컬럼 제거
    op.drop_column('document', 'html_content')

