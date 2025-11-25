import sys
from pathlib import Path
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

# backend 디렉토리를 Python 경로에 추가
backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.db.base import Base
from app.db import models  # noqa: F401
from app.core.config import get_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
# Alembic는 동기식 엔진을 사용하므로 asyncpg를 psycopg2로 변경
dsn = settings.postgres_dsn.replace("postgresql+asyncpg://", "postgresql://")
config.set_main_option("sqlalchemy.url", dsn)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
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
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
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


