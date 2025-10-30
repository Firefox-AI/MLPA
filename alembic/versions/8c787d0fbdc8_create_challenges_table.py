"""Create challenges table

Revision ID: 8c787d0fbdc8
Revises:
Create Date: 2025-09-11 09:57:13.253483

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8c787d0fbdc8"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
	op.execute("""
        CREATE TABLE challenges (
            key_id_b64 VARCHAR(255) PRIMARY KEY,
            challenge VARCHAR(255),
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)


def downgrade() -> None:
	op.drop_table("challenges")
