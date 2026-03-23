"""create_mlpa_user_capacity_tables

Revision ID: f3c8a91b2e40
Revises:
Create Date: 2026-03-23

MLPA sign-in capacity tracking on the LiteLLM database. Runtime reconciles
counts and max_identities from config on startup.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "f3c8a91b2e40"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # IF NOT EXISTS: DBs that already had these tables from pre-Alembic MLPA still stamp this revision.
    op.execute("""
        CREATE TABLE IF NOT EXISTS mlpa_user_capacity (
            id SMALLINT PRIMARY KEY CHECK (id = 1),
            max_identities BIGINT NOT NULL CHECK (max_identities >= 0),
            current_identities BIGINT NOT NULL CHECK (current_identities >= 0),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS mlpa_user_capacity_identities (
            base_identity TEXT PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    # Default matches Env.MLPA_MAX_SIGNED_IN_USERS; startup overwrites max_identities from env.
    op.execute("""
        INSERT INTO mlpa_user_capacity (id, max_identities, current_identities)
        VALUES (1, 1000000, 0)
        ON CONFLICT (id) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mlpa_user_capacity_identities")
    op.execute("DROP TABLE IF EXISTS mlpa_user_capacity")
