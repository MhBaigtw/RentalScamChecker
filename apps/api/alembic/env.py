"""Alembic migration environment. The database URL comes from app settings (DATABASE_URL)."""

from logging.config import fileConfig

from sqlalchemy import create_engine, pool

from alembic import context
from rentalscamchecker.config import get_settings
from rentalscamchecker.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The models' metadata, so `alembic revision --autogenerate` can diff it against the database.
target_metadata = Base.metadata

# Callers running Alembic from code (the test suite) can pass the URL in config.attributes.
database_url: str = config.attributes.get("database_url") or get_settings().database_url


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it (`alembic upgrade head --sql`)."""
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(database_url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
