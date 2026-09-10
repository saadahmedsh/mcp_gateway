"""Alembic environment for asynchronous PostgreSQL migrations."""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from gateway.config import get_settings
from gateway.control_plane.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without creating a database connection."""

    settings = get_settings()
    context.configure(
        url=str(settings.control_plane_database_url),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Configure Alembic against a synchronous migration connection."""

    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations using SQLAlchemy's asynchronous engine adapter."""

    settings = get_settings()
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = str(settings.control_plane_database_url)
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={"timeout": settings.control_plane_connect_timeout_seconds},
    )
    timeout_seconds = settings.control_plane_connect_timeout_seconds + 1.0
    async with asyncio.timeout(timeout_seconds):
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations against the configured online database."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
