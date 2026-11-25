"""Guest claims: last_seen_at, bumped on guest token refresh

Revision ID: d7a3e91b5c20
Revises: c41d7e2a9f10
Create Date: 2026-11-23

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "d7a3e91b5c20"
down_revision = "c41d7e2a9f10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable without default, so this is metadata-only and existing claims
    # need no backfill. Infra cleanup uses COALESCE(last_seen_at, created_at).
    op.execute(
        """
        ALTER TABLE mlpa_user_capacity_identities
            ADD COLUMN last_seen_at TIMESTAMPTZ
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE mlpa_user_capacity_identities DROP COLUMN last_seen_at")
