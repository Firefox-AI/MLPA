"""create_keys_table

Revision ID: 482f016f00d7
Revises: 8c787d0fbdc8
Create Date: 2025-09-11 11:58:58.872255

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "482f016f00d7"
down_revision: Union[str, Sequence[str], None] = "8c787d0fbdc8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
	op.execute("""
        CREATE TABLE public_keys (
            key_id_b64 VARCHAR(255) PRIMARY KEY,
            public_key_pem TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)


def downgrade() -> None:
	op.drop_table("public_keys")
