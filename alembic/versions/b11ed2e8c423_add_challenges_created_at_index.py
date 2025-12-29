"""add challenges created_at index

Revision ID: b11ed2e8c423
Revises: 5b4ed32c7b2b
Create Date: 2025-01-15 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "b11ed2e8c423"
down_revision: Union[str, Sequence[str], None] = "5b4ed32c7b2b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX idx_challenges_created_at ON challenges(created_at);
    """)


def downgrade() -> None:
    op.execute("""
        DROP INDEX IF EXISTS idx_challenges_created_at;
    """)
