"""add counter tracking to public keys

Revision ID: 5b4ed32c7b2b
Revises: 482f016f00d7
Create Date: 2025-02-14 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5b4ed32c7b2b"
down_revision: Union[str, Sequence[str], None] = "482f016f00d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "public_keys",
        sa.Column(
            "counter",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "public_keys",
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.execute("UPDATE public_keys SET updated_at = NOW()")
    op.alter_column("public_keys", "counter", server_default=None)
    op.alter_column("public_keys", "updated_at", server_default=None)


def downgrade() -> None:
    op.drop_column("public_keys", "updated_at")
    op.drop_column("public_keys", "counter")
