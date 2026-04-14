"""user capacity

Revision ID: 919c4d382c42
Revises: 5b4ed32c7b2b
Create Date: 2026-04-13 13:06:54.729440

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "919c4d382c42"
down_revision = "5b4ed32c7b2b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS mlpa_user_capacity (
            id SMALLINT PRIMARY KEY CHECK (id = 1),
            max_identities BIGINT NOT NULL CHECK (max_identities >= 0),
            current_identities BIGINT NOT NULL CHECK (current_identities >= 0),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS mlpa_user_capacity_identities (
            base_identity TEXT PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mlpa_user_capacity_identities")
    op.execute("DROP TABLE IF EXISTS mlpa_user_capacity")
