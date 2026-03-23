from alembic import context
from sqlalchemy import engine_from_config, pool

from mlpa.core.config import env

config = context.config
target_metadata = None


def get_effective_url() -> str:
    """Deploy passes -x sqlalchemy.url=...; otherwise URL is built from MLPA env (e.g. .env)."""
    x_args = context.get_x_argument(as_dictionary=True) or {}
    return x_args.get("sqlalchemy.url") or (
        f"{env.PG_DB_URL.rstrip('/')}/{env.LITELLM_DB_NAME}"
    )


def run_migrations_offline() -> None:
    url = get_effective_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = get_effective_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
