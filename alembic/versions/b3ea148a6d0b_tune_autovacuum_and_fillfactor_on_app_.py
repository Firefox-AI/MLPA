"""Postgres storage tuning for app_attest hot tables

Revision ID: b3ea148a6d0b
Revises: 919c4d382c42
Create Date: 2026-07-27

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "b3ea148a6d0b"
down_revision = "919c4d382c42"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE challenges SET (
            autovacuum_vacuum_scale_factor = 0.05,
            autovacuum_vacuum_cost_limit = 2000
        )
        """
    )
    op.execute(
        """
        ALTER TABLE public_keys SET (
            fillfactor = 85,
            autovacuum_vacuum_scale_factor = 0.05
        )
        """
    )
    op.execute(
        """
        ALTER TABLE mlpa_user_capacity SET (
            fillfactor = 70,
            autovacuum_vacuum_scale_factor = 0.0,
            autovacuum_vacuum_threshold = 1000
        )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE mlpa_user_capacity RESET (
            fillfactor,
            autovacuum_vacuum_scale_factor,
            autovacuum_vacuum_threshold
        )
        """
    )
    op.execute(
        """
        ALTER TABLE public_keys RESET (
            fillfactor,
            autovacuum_vacuum_scale_factor
        )
        """
    )
    op.execute(
        """
        ALTER TABLE challenges RESET (
            autovacuum_vacuum_scale_factor,
            autovacuum_vacuum_cost_limit
        )
        """
    )
