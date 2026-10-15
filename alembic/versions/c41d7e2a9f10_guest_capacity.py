"""Guest capacity: second mlpa_user_capacity row and per-claim capacity_id

Revision ID: c41d7e2a9f10
Revises: b3ea148a6d0b
Create Date: 2026-10-14

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "c41d7e2a9f10"
down_revision = "b3ea148a6d0b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Row 1 = signed-in users, row 2 = guests (AIPLAT-1447).
    op.execute(
        """
        ALTER TABLE mlpa_user_capacity
            DROP CONSTRAINT mlpa_user_capacity_id_check,
            ADD CONSTRAINT mlpa_user_capacity_id_check CHECK (id IN (1, 2))
        """
    )
    # Constant default, so this is a metadata-only change on PG 11+.
    op.execute(
        """
        ALTER TABLE mlpa_user_capacity_identities
            ADD COLUMN capacity_id SMALLINT NOT NULL DEFAULT 1
        """
    )
    # One claim per (identity, capacity row), so a user claim and a guest claim
    # for the same base identity don't collide.
    op.execute(
        """
        ALTER TABLE mlpa_user_capacity_identities
            DROP CONSTRAINT mlpa_user_capacity_identities_pkey,
            ADD PRIMARY KEY (base_identity, capacity_id)
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM mlpa_user_capacity_identities WHERE capacity_id = 2")
    op.execute(
        """
        ALTER TABLE mlpa_user_capacity_identities
            DROP CONSTRAINT mlpa_user_capacity_identities_pkey,
            ADD PRIMARY KEY (base_identity)
        """
    )
    op.execute("ALTER TABLE mlpa_user_capacity_identities DROP COLUMN capacity_id")
    op.execute("DELETE FROM mlpa_user_capacity WHERE id = 2")
    op.execute(
        """
        ALTER TABLE mlpa_user_capacity
            DROP CONSTRAINT mlpa_user_capacity_id_check,
            ADD CONSTRAINT mlpa_user_capacity_id_check CHECK (id = 1)
        """
    )
