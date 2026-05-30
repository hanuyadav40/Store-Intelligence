import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# Load alembic config
config = context.config

# Interpret the config file for Python logging.
# disable_existing_loggers=False avoids KeyError when alembic.ini has no
# [formatters]/[handlers]/[loggers] sections (we use our own JSON logging).
if config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name, disable_existing_loggers=False)
    except KeyError:
        pass  # alembic.ini has no logging sections — that's fine

# Override the sqlalchemy.url with env var if present
sync_url = os.environ.get("SYNC_DATABASE_URL")
if sync_url:
    config.set_main_option("sqlalchemy.url", sync_url)

# Import metadata — must be after app models are importable
from app.models import Base  # noqa: E402
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode without a live DB connection."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode with a live DB connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
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
